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


def _text(item: Any) -> str:
    return f"{item.subject} {item.value}"


def tiered_recall(store: Any, query: str, *, embedder: Optional[Embedder] = None,
                  graph_scorer: Optional[GraphScorer] = None, top_k: int = DEFAULT_TOP_K,
                  semantic: bool = True) -> List[RetrievalHit]:
    """Fuse lexical + graph + semantic into one ranked, top-k result (see module docstring)."""
    items = store.all_active()                       # candidate set, honoring toggles
    if not items:
        return []

    lex = {it.id: lexical_score(query, _text(it)) for it in items}

    sem: dict = {}
    if semantic and embedder is not None:
        backend = store.backend
        if hasattr(backend, "semantic_search"):
            # index-backed top-k (e.g. pgvector) — no full-store scan
            try:
                for it, score in backend.semantic_search(query, top_k=max(top_k, len(items))):
                    sem[it.id] = score
            except Exception:
                sem = {}                              # any backend hiccup -> degrade to lexical
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
                grp[it.id] = 0.0

    hits: List[RetrievalHit] = []
    for it in items:
        l, s, g = lex.get(it.id, 0.0), sem.get(it.id, 0.0), grp.get(it.id, 0.0)
        fused = SEMANTIC_WEIGHT * s + GRAPH_WEIGHT * g + LEXICAL_WEIGHT * l
        hits.append(RetrievalHit(item=it, score=fused, lexical=l, semantic=s, graph=g))

    # deterministic ordering: fused desc, then created_at asc, then id asc
    hits.sort(key=lambda h: (-h.score, h.item.created_at, h.item.id))
    return hits[:top_k]
