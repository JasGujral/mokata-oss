"""Stage 35e — pluggable embedder seam + a zero-dependency local embedder.

Semantic memory turns text into a vector. The embedder is a SEAM: any callable
``text -> list[float]``. The default :class:`HashingEmbedder` is deterministic, local, and
dependency-free (a hashing bag-of-words), so semantic memory works with **zero deps** and
never forces a network or a model download. Real providers (a local model, or the store's own
embedding) are wired by config. With **no embedder configured the semantic tier is simply
OFF** — lexical (and graph, when wired) still work (local-first, P8; degrade-never-break).
"""

from __future__ import annotations

import math
import re
import zlib
from typing import Callable, List, Optional

EMBED_DIM = 64
_WORD = re.compile(r"[a-z0-9]+")

# An embedder is any callable text -> vector. (Kept as a plain alias so the core stays
# dependency-free and any provider — local model, hosted, the store's own — can be wired.)
Embedder = Callable[[str], List[float]]


def cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class HashingEmbedder:
    """A deterministic, local, dependency-free embedder: hashes word tokens into a fixed-dim
    bag-of-words vector (L2-normalized). Reproducible across processes (a stable hash, not the
    salted built-in ``hash``), so rankings are deterministic — good enough for tiered ranking
    and the default test double, with zero deps and no network."""

    def __init__(self, dim: int = EMBED_DIM) -> None:
        self.dim = dim

    def __call__(self, text: str) -> List[float]:
        vec = [0.0] * self.dim
        for tok in _WORD.findall((text or "").lower()):
            vec[zlib.adler32(tok.encode("utf-8")) % self.dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec] if norm else vec


# Registry so config can name an embedder; unknown / None -> semantic stays OFF.
def make_embedder(name: Optional[str]) -> Optional[Embedder]:
    """Resolve an embedder by name (from config). `"hashing"` -> the local default; anything
    else (incl. None) -> None, so the semantic tier is OFF unless explicitly wired."""
    if name in ("hashing", "local"):
        return HashingEmbedder()
    return None
