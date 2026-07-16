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


def tiered_recall(store: Any, query: str, *, embedder: Optional[Embedder] = None,
                  graph_scorer: Optional[GraphScorer] = None, top_k: int = DEFAULT_TOP_K,
                  semantic: bool = True) -> List[RetrievalHit]:
    """Fuse lexical + graph + semantic into one ranked, top-k result (see module docstring)."""
    # TM.S6 — candidates are the scope-path UNION (byte-identical to all_active when the store
    # has no scope context). Falls back to all_active for any store-like object lacking the method.
    candidates = getattr(store, "scoped_active", None) or store.all_active
    items = candidates()                             # honoring toggles + scope path
    if not items:
        return []

    lex = {it.id: lexical_score(query, _text(it)) for it in items}

    sem: dict = {}
    if semantic and embedder is not None:
        backend = store.backend
        # D5-rider(3) — NOT-YET-REACHABLE on any shipped config: the only backend exposing
        # `semantic_search` is PgVectorBackend, which no shipped store selects (export-only until
        # DB.S4 wires pgvector for real in 0.0.15). KEPT rather than deleted — this is the exact
        # shape DB.S4 will consume, so removing it would only force DB.S4 to re-add it. Today it is
        # exercised solely by test_r_13f_d5_rider_3 (an injected `semantic_search` backend), so the
        # branch is covered-and-marked, never dead-and-silent.
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
