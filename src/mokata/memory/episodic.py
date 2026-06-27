"""C3 — episodic conversation memory.

A searchable local store of past conversation turns, wired as a third memory type
(`episodic`) on top of the existing pluggable backends and the per-type toggle. Recording
a turn goes through the same human-gated write path; search honors the toggle (a disabled
episodic type surfaces nothing).

Embeddings are OPTIONAL: pass an `embedder(text) -> vector` for semantic ranking; with
none, search degrades to a dependency-free lexical (keyword-overlap) ranking. No new
required runtime dependencies.
"""

from __future__ import annotations

import math
import re
from typing import Any, Callable, List, Optional, Tuple

from .item import DEFAULT_TOP_K, EPISODIC, MemoryItem

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set:
    return set(_WORD.findall(text.lower()))


def lexical_score(query: str, text: str) -> float:
    """Jaccard overlap of word tokens — the no-dependency fallback ranking."""
    q, t = _tokens(query), _tokens(text)
    if not q or not t:
        return 0.0
    return len(q & t) / len(q | t)


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class EpisodicMemory:
    """Episodic turns over a MemoryStore (reusing its backend, toggle, and gate)."""

    def __init__(self, store: Any) -> None:
        self.store = store

    def record(self, session: str, text: str, role: str = "user",
               confirm: Optional[Callable[[str], bool]] = None,
               assume_yes: bool = False) -> Any:
        """Record one conversation turn (human-gated like any memory write)."""
        item = MemoryItem.create(subject=session, value=text, mtype=EPISODIC,
                                 source=role)
        return self.store.remember(item, confirm=confirm, assume_yes=assume_yes)

    def search(self, query: str, top_k: int = DEFAULT_TOP_K,
               embedder: Optional[Callable[[str], List[float]]] = None,
               ) -> List[Tuple[MemoryItem, float]]:
        """Return up to `top_k` (turn, score) pairs, best first. Uses `embedder` for
        semantic ranking when supplied; otherwise lexical overlap. Honors the toggle —
        returns [] when episodic memory is disabled."""
        turns = self.store.all_active(mtype=EPISODIC)
        if not turns:
            return []
        if embedder is not None:
            qv = embedder(query)
            scored = [(t, _cosine(qv, embedder(t.value))) for t in turns]
        else:
            scored = [(t, lexical_score(query, t.value)) for t in turns]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:top_k]
