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
from .expansion import EDGE_WEIGHT
from .item import DEFAULT_TOP_K
from .episodic import lexical_score
from .lifecycle import UsageSignal, recency_score, usage_score

# The zero signal, shared: an item with no telemetry row. Hoisted to module scope so the ranking
# loop allocates nothing per item, and frozen so it cannot be mutated into a non-zero default.
_NO_USAGE = UsageSignal()

# Tier weights — semantic strongest, graph next, lexical the always-present floor.
SEMANTIC_WEIGHT = 1.0
GRAPH_WEIGHT = 0.5
LEXICAL_WEIGHT = 0.25

# DB.S5 — the QUALITY terms, weighted deliberately BELOW the lexical floor. The three weights
# above answer "does this item match the query"; these two answer "has this item proved useful",
# which is a tiebreak between things that already match, never a reason to surface something that
# does not. Set at or above LEXICAL_WEIGHT they would let a popular-but-irrelevant item outrank a
# precise lexical hit — a memory store that returns your favourites instead of your answer.
#
# Both terms are exactly 0.0 whenever the item has no usage signal (see `lifecycle.recency_score` /
# `usage_score`), so on a store with no v4 columns, a v3 shared schema, or a freshly-migrated store
# where nothing has been recalled yet, `fused` is arithmetically the pre-DB.S5 three-term sum and
# the ranking is byte-identical. That is a contract, not an emergent property — it is pinned by
# test.
RECENCY_WEIGHT = 0.15
USAGE_WEIGHT = 0.10

# DB.S7b (K1) — the EXPANSION term's weight lives in `expansion.py` beside the depth decay and the
# per-kind weights it multiplies with, because those four numbers are one tuning surface and
# splitting them across two modules is how half a knob gets turned. Imported, never re-declared.
#
# The same arithmetic contract the two DB.S5 terms above carry, for the same reason: an item that
# no edge reached scores exactly 0.0 on this tier, so `EDGE_WEIGHT * 0.0` leaves the DB.S5
# five-term sum bit for bit. On a store with no edge table, an un-migrated v4 team, or with
# `memory.edge_expansion` off, EVERY item is in that case — so the ranking is byte-identical to
# pre-DB.S7b, in the same order. Pinned by test, not assumed.

# A graph scorer is `(query, item) -> float` (0..1), wired only when a code graph is present.
GraphScorer = Callable[[str, Any], float]


@dataclass
class RetrievalHit:
    item: Any
    score: float           # fused
    lexical: float = 0.0
    semantic: float = 0.0
    graph: float = 0.0
    # DB.S5 — the quality terms. Defaulted so every existing constructor call (and every test
    # building a hit by hand) keeps working unchanged, and so a hit built without them carries the
    # zero signal rather than an absent attribute.
    recency: float = 0.0
    usage: float = 0.0
    # DB.S7b — the expansion tier's score, and the ACTUAL route that produced it. Defaulted for
    # the same reason the DB.S5 pair above is: every existing constructor call and every hand-built
    # test hit keeps working unchanged, and a hit built without them carries the zero signal rather
    # than an absent attribute. `path` is `None` for every directly-matched hit — which is most of
    # them — and is the ONLY source the "why" may use to speak about a traversal.
    edge: float = 0.0
    path: Optional[Any] = None               # an `expansion.ExpansionPath`, when a hop reached it

    def tiers(self) -> dict:
        return {"lexical": self.lexical, "graph": self.graph, "semantic": self.semantic,
                "recency": self.recency, "usage": self.usage, "edge": self.edge}

    def explain(self, query: str) -> str:
        """Stage 59 — a short, deterministic "why it surfaced" phrase for THIS hit, from its
        own per-tier scores + the query (the strongest tier + the matched token + kind).
        DB.S7b: when a hop reached this item, the phrase names the REAL walked path.
        Read-only; the hit carries its own explanation."""
        from .intelligence import why_surfaced
        return why_surfaced(query, self.item, tiers=self.tiers(), path=self.path)


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
                  degrade_out: Optional[Callable[[str], None]] = None,
                  now: Optional[str] = None,
                  expander: Optional[Callable] = None) -> List[RetrievalHit]:
    """Fuse lexical + graph + semantic + recency + usage into one ranked, top-k result (see the
    module docstring).

    `degrade_out` redirects a tier's degrade notice (default: stderr, once per subsystem).
    `now` fixes the clock the DB.S5 recency term decays against — injected so the ranking is a
    pure function of its inputs in a test; live callers omit it and get wall-clock UTC.

    DB.S7b (K1) — `expander` is the backend's `expand_from` seam, entering by exactly the same
    pluggable route `graph_scorer` already uses (`store.py:1115`) rather than a second one. When it
    is wired, the ranking becomes TWO passes, which is what doc 55's "seed hits → bounded
    expansion" literally says: rank directly, take the top `SEED_CAP` as SEEDS, walk ≤2 hops off
    them, then re-rank with the expansion term. `None` (no edge table, or the config off) skips the
    second pass entirely and the result is the one-pass DB.S5 ranking, unchanged."""
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

    # DB.S5 — the usage telemetry for exactly the candidates being ranked: ONE bounded read keyed
    # by ids we already hold, never a scan and never a per-item round trip. `usage_signals` returns
    # `{}` for any store that cannot answer (no v4 columns, a v3 team schema, a driver error), and
    # `{}` means every item gets the zero signal — which is the back-compat floor, not a failure.
    usage_signals: dict = {}
    reader = getattr(store, "usage_signals", None)
    if reader is not None:
        try:
            usage_signals = reader([it.id for it in items]) or {}
        except Exception:
            # `MemoryStore.usage_signals` already swallows its own failures, so this guard is for
            # the DUCK-TYPED stores this function accepts (a test double, a host-supplied object)
            # whose reader makes no such promise. The module contract at the top of this file is
            # that no tier can crash a recall, and a signal that arrived after that contract was
            # written does not get an exemption from it. Empty = the zero signal = the pre-DB.S5
            # ranking, which is the same floor every other tier degrades to.
            usage_signals = {}

    hits: List[RetrievalHit] = []
    match: dict = {}
    for it in items:
        l, s, g = lex.get(it.id, 0.0), sem.get(it.id, 0.0), grp.get(it.id, 0.0)
        signal = usage_signals.get(it.id) or _NO_USAGE
        r = recency_score(signal, it.created_at, now)
        u = usage_score(signal)
        # The three original terms are UNTOUCHED and lead the expression, so the additive change is
        # visible as exactly that: two terms that vanish to `+ 0.0 + 0.0` on a store with no
        # telemetry, leaving the pre-DB.S5 sum bit for bit.
        fused = (SEMANTIC_WEIGHT * s + GRAPH_WEIGHT * g + LEXICAL_WEIGHT * l
                 + RECENCY_WEIGHT * r + USAGE_WEIGHT * u)
        hits.append(RetrievalHit(item=it, score=fused, lexical=l, semantic=s, graph=g,
                                 recency=r, usage=u))
        # DB.S7b — the MATCH score (the three tiers that answer "does this item match the query"),
        # kept separately because it is what SEEDS the expansion. Not the fused score: the two
        # quality terms move with recall HISTORY, and seeding on them lets the reachable set creep
        # past the hop bound over repeated recalls. See `expansion.select_seeds`.
        match[it.id] = SEMANTIC_WEIGHT * s + GRAPH_WEIGHT * g + LEXICAL_WEIGHT * l

    # deterministic ordering: fused desc, then created_at asc, then id asc — UNCHANGED. The new
    # terms enter the score, never the tiebreak: a tiebreak that consulted usage would make the
    # order of two identically-scoring items depend on run-state that a recall itself mutates.
    hits.sort(key=lambda h: (-h.score, h.item.created_at, h.item.id))

    # DB.S7b — PASS TWO. Everything above is the pre-DB.S7b ranking, produced identically and in the
    # same order; what follows can only ADD to a score, never subtract, and adds exactly 0.0 to
    # every item when nothing was reached. `expander is None` skips it outright.
    expanded = _expansion_tier(hits, match, expander, degrade_out)
    if expanded:
        for hit in hits:
            path = expanded.paths.get(hit.item.id)
            if path is None:
                continue
            hit.edge = path.weight
            hit.path = path
            hit.score += EDGE_WEIGHT * path.weight
        # A second sort with the SAME key. When no item was reached this re-sorts an already-sorted
        # list under an unchanged key — Python's sort is stable, so the order is not merely
        # equivalent but identical, which is what makes the degrade byte-identical rather than
        # merely equal-scoring.
        hits.sort(key=lambda h: (-h.score, h.item.created_at, h.item.id))
    return hits[:top_k]


def _expansion_tier(hits: List[RetrievalHit], match: dict, expander: Optional[Callable],
                    degrade_out: Optional[Callable[[str], None]]) -> Any:
    """DB.S7b — seed from the MATCH ranking, walk ≤2 hops, report what was left out.

    Separated from the fusion so the two-pass structure reads as two passes. Returns an
    `ExpansionResult` (falsy when nothing was reached), or `None` when the tier is off.

    SCOPE: the visible set is `hits` — i.e. exactly what `scoped_active()` already decided this
    identity may read. It is not re-derived, and it is passed all the way INTO the walk so an
    unreadable item cannot bridge hop 1 to hop 2 (`expansion.walk_paths`, decision 4). Doc 55's
    acceptance is "no cross-scope leak through a hop", and a post-filter would satisfy the words
    while leaking the bridge's id inside the returned path.
    """
    if expander is None:
        return None
    from .expansion import EMPTY, MAX_HOPS, expand, select_seeds
    # Seeded on the MATCH score, and ordered by it too — so the seed set is a function of the
    # QUERY alone and does not drift as recall telemetry accumulates.
    ranked = sorted(((h.item.id, match.get(h.item.id, 0.0)) for h in hits),
                    key=lambda pair: (-pair[1], pair[0]))
    seeds, dropped = select_seeds(ranked)
    if not seeds:
        return EMPTY
    visible = {h.item.id for h in hits}
    try:
        result = expand(expander, seeds, visible=visible, max_hops=MAX_HOPS)
    except Exception as exc:
        # D5 — the EXPANSION tier just went away and the recall must say so ONCE, exactly as the
        # semantic and lexical tiers do above. Broad for the identical reason they are: this spans
        # a psycopg driver error (an OPTIONAL extra, not nameable at module scope), a sqlite3 error
        # on a store mid-migration, and any third-party adapter's own classes. The fallback is
        # right — the direct matches are still ranked correctly — but silence would make "the walk
        # failed" indistinguishable from "there are no edges", which is the exact ambiguity D5
        # exists to end.
        note_degraded("memory-expansion", failure_class_of(exc) or FAILURE_UNREACHABLE,
                      fallback="edge expansion is OFF — ranking is direct-match only",
                      fix="run `mokata doctor` to check the memory store",
                      detail=f"{type(exc).__name__}: {exc}", out=degrade_out)
        return EMPTY
    # NO SILENT CAPS. A bounded read that trims and says nothing reads as a complete one.
    if dropped or result.rows_truncated:
        note_degraded("memory-expansion-bounds", FAILURE_UNREACHABLE,
                      fallback=(f"edge expansion was bounded: {dropped} seed(s) beyond the cap, "
                                f"{result.rows_truncated} walked edge(s) dropped"),
                      fix="narrow the query, or raise the DB.S7b bounds if this recurs",
                      detail=f"seed cap {len(seeds)}, hop bound {MAX_HOPS}", out=degrade_out)
    return result
