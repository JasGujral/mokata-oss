"""Stage 35e — tiered, degrade-safe memory retrieval.

`recall_relevant(query)` fuses up to three tiers into one ranked result:
  - **lexical** (the floor, zero deps) — keyword overlap (`lexical_score`);
  - **graph-proximity** (optional middle tier) — a code-graph-keyed boost, supplied as a
    pluggable `graph_scorer` (off unless a graph is wired);
  - **semantic** (top tier) — embedding cosine, via the vector backend's index
    (`semantic_search`, no full-store scan) or, for any other backend, the embeddings stamped
    on each item at WRITE time (frugal — computed once, on the gated write).

Deterministic ordering: fused score DESC, then `created_at` ASC, then `id` ASC. The weights
make semantic dominate, then graph, with lexical as the always-present floor — so an
embedding-near item outranks a merely lexical match, yet lexical still returns when semantic
is off. Frugal (P11): retrieval returns only the top-k (no corpus dump) and embeds just the
query at read time (item vectors are precomputed on write).

Degrade-clean: no embedder ⇒ semantic tier silently absent; no graph_scorer ⇒ graph tier
absent; lexical always works. Nothing crashes when a tier is unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, List, Optional

from ..degrade import FAILURE_UNREACHABLE, note_degraded
from ..errors import failure_class_of
from .embed import Embedder, cosine
from .item import DEFAULT_TOP_K
from .episodic import lexical_score

# Tier weights — semantic strongest, graph next, lexical the always-present floor.
SEMANTIC_WEIGHT = 1.0
GRAPH_WEIGHT = 0.5
LEXICAL_WEIGHT = 0.25

# A graph scorer is `(query, item) -> float` (0..1), wired only when a code graph is present.
GraphScorer = Callable[[str, Any], float]


@dataclass
class RetrievalHit:
    item: Any
    score: float           # fused
    lexical: float = 0.0
    semantic: float = 0.0
    graph: float = 0.0

    def tiers(self) -> dict:
        return {"lexical": self.lexical, "graph": self.graph, "semantic": self.semantic}

    def explain(self, query: str) -> str:
        """Stage 59 — a short, deterministic "why it surfaced" phrase for THIS hit, from its
        own per-tier scores + the query (the strongest tier + the matched token + kind).
        Read-only; the hit carries its own explanation."""
        from .intelligence import why_surfaced
        return why_surfaced(query, self.item, tiers=self.tiers())


def _text(item: Any) -> str:
    return f"{item.subject} {item.value}"


# DB.S3 — how many ranked rows to pull from the FTS index. Wider than `top_k` because the DB ranks
# over ALL rows while the caller only sees the SCOPE-VISIBLE ones: a hit the identity may not read
# is dropped after the query, so a bare `top_k` could come back short. Still bounded — this is a
# top-N, not the full-store scan it replaces.
LEXICAL_CANDIDATE_FLOOR = 50


def lexical_tier(store: Any, query: str, items: List[Any], top_k: int,
                 degrade_out: Optional[Callable[[str], None]] = None) -> tuple:
    """DB.S3 — the lexical tier: ONE ranked SQL query when the backend can search, the Jaccard
    scan when it can't. Returns `({item_id: score}, mode)`.

    Before DB.S3 this WAS `{it.id: lexical_score(query, _text(it)) for it in items}` — a full-store
    Python scan whose cost grows with the store, and which ranks by Jaccard (token overlap over the
    UNION), so a long document containing every query term scores BELOW a short one sharing a
    single term. Now a backend that can rank in the database (`lexical_search`) does, and only
    matching rows are ever scored.

    Three outcomes, and the difference between the last two is the honesty:
      * FTS live       — the DB's normalized scores, intersected with what the caller may SEE;
      * floor by DESIGN — a backend with no `lexical_search` at all (Obsidian's files, the native
        client). Jaccard is that backend's lexical tier, not a loss. NO notice: one that always
        fires is noise, and D5's whole point is that a notice MEANS something;
      * floor by DEGRADE — a SQL backend that SHOULD have FTS and doesn't (FTS5 not compiled), or
        whose search raised. The user asked for FTS recall and is getting keyword overlap, so say
        so ONCE (`note_degraded`) — the exact silence D5 was written to end.
    """
    from .backends import LEXICAL_MODE_JACCARD
    backend = getattr(store, "backend", None)
    search = getattr(backend, "lexical_search", None)
    mode = getattr(backend, "lexical_mode", LEXICAL_MODE_JACCARD)

    if search is not None and mode != LEXICAL_MODE_JACCARD:
        try:
            ranked = search(query, top_k=max(top_k * 4, LEXICAL_CANDIDATE_FLOOR))
            # The scope/access filter still WINS: `items` is what this identity may read, and a
            # hit outside it is dropped. The FTS predicate composes with visibility (the backend
            # already applied its project scope); it never overrides it.
            visible = {it.id for it in items}
            return {it.id: float(s) for it, s in ranked if it.id in visible}, mode
        except Exception as exc:
            # Broad for the same reason the semantic tier's handler is: `lexical_search` spans a
            # psycopg driver error (an OPTIONAL extra, not nameable at module scope), a sqlite3
            # error, and the decode of each stored doc. Narrowing to what we CAN name would let a
            # driver error crash every recall — a swallow turned into an outage.
            note_degraded("memory-lexical", failure_class_of(exc) or FAILURE_UNREACHABLE,
                          fallback="lexical recall fell back to keyword overlap (jaccard)",
                          fix="run `mokata doctor` to check the memory store",
                          detail=f"{type(exc).__name__}: {exc}", out=degrade_out)
    elif search is not None:
        # A SQL backend reporting the floor = the FTS5 capability probe came back False.
        note_degraded("memory-lexical", FAILURE_UNREACHABLE,
                      fallback="lexical recall is keyword overlap (jaccard) — FTS is unavailable",
                      fix="use a sqlite3 built with FTS5, or run `mokata doctor`",
                      detail="this sqlite3 has no FTS5 compiled in", out=degrade_out)

    return {it.id: lexical_score(query, _text(it)) for it in items}, LEXICAL_MODE_JACCARD


def tiered_recall(store: Any, query: str, *, embedder: Optional[Embedder] = None,
                  graph_scorer: Optional[GraphScorer] = None, top_k: int = DEFAULT_TOP_K,
                  semantic: bool = True,
                  degrade_out: Optional[Callable[[str], None]] = None) -> List[RetrievalHit]:
    """Fuse lexical + graph + semantic into one ranked, top-k result (see module docstring).

    `degrade_out` redirects a tier's degrade notice (default: stderr, once per subsystem)."""
    # TM.S6 — candidates are the scope-path UNION (byte-identical to all_active when the store
    # has no scope context). Falls back to all_active for any store-like object lacking the method.
    candidates = getattr(store, "scoped_active", None) or store.all_active
    items = candidates()                             # honoring toggles + scope path
    if not items:
        return []

    # DB.S3 — the lexical tier is a SQL FTS query where the backend can run one, and the Jaccard
    # scan (this line's former body) only where it can't.
    lex, _lexical_mode = lexical_tier(store, query, items, top_k, degrade_out)

    sem: dict = {}
    if semantic and embedder is not None:
        backend = store.backend
        # DB.S4 — REACHABLE. The D5-rider(3) not-yet-reachable marker that stood here has been
        # retired because it fired: `selection._select_raw_backend` now has a `pgvector` branch, so
        # a team that opts into the semantic store reaches this line on a real config. The marker
        # did its job — it kept the branch (rather than deleting a shape DB.S4 would have had to
        # re-add) and kept it honestly labelled while it waited. `test_r_13f_d5_rider_3` still pins
        # both sub-paths; DB.S4's own suite pins the live selection that reaches them.
        if hasattr(backend, "semantic_search"):
            # index-backed top-k (e.g. pgvector) — no full-store scan
            try:
                for it, score in backend.semantic_search(query, top_k=max(top_k, len(items))):
                    sem[it.id] = score
            except Exception as exc:
                # D5 — the semantic TIER just went away, and recall carried on ranking as if it had
                # never been configured. The fallback is right (lexical is the designed floor and a
                # recall must not break); the SILENCE was the bug — the user asked for semantic
                # recall, is getting keyword overlap, and nothing anywhere said so.
                #
                # Deliberately still broad, and this is the honest reason: `semantic_search` is an
                # index-backed pgvector call, so its raisables span `psycopg.Error` (an OPTIONAL
                # extra — not nameable at module scope), the embedder's own errors, and the decode of
                # each stored doc. Narrowing to the subset we CAN name would let a driver error crash
                # every recall — turning a swallow into a crash, which is worse than the bug.
                sem = {}                              # degrade to the lexical floor
                note_degraded("memory-semantic", failure_class_of(exc) or FAILURE_UNREACHABLE,
                              fallback="semantic recall is OFF — ranking is lexical-only",
                              fix="check the vector backend / run `mokata doctor`",
                              detail=f"{type(exc).__name__}: {exc}")
        else:
            qv = embedder(query)
            for it in items:
                ev = it.provenance.get("_embedding") or embedder(_text(it))
                sem[it.id] = cosine(qv, ev)

    grp: dict = {}
    if graph_scorer is not None:
        for it in items:
            try:
                grp[it.id] = float(graph_scorer(query, it))
            except Exception:
                # LEGITIMATE SUPPRESS: a PER-ITEM hiccup in a pluggable, OPTIONAL ranking signal.
                # It contributes 0.0 — the item still ranks on lexical (+ semantic), so nothing is
                # hidden and no result is lost; only a boost is. A notice per item would be noise,
                # and the tier is off by default anyway (`graph_scorer is None`).
                grp[it.id] = 0.0

    hits: List[RetrievalHit] = []
    for it in items:
        l, s, g = lex.get(it.id, 0.0), sem.get(it.id, 0.0), grp.get(it.id, 0.0)
        fused = SEMANTIC_WEIGHT * s + GRAPH_WEIGHT * g + LEXICAL_WEIGHT * l
        hits.append(RetrievalHit(item=it, score=fused, lexical=l, semantic=s, graph=g))

    # deterministic ordering: fused desc, then created_at asc, then id asc
    hits.sort(key=lambda h: (-h.score, h.item.created_at, h.item.id))
    return hits[:top_k]
