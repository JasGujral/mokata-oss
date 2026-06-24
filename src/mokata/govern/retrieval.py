"""F2 — JIT graph-backed retrieval.

Retrieve by identifier through the knowledge layer (Part B) — the relevant reference
snippets — instead of dumping whole files into context. Reports the token reduction vs
the file-dump baseline, so the savings are visible (feeds the token governor, F1).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

from ..bootstrap import estimate_tokens

# The query kinds a JIT retrieval pulls for an identifier.
_KINDS = ("callers", "implementers", "imports")


@dataclass
class RetrievalResult:
    identifiers: List[str]
    snippets: List[str] = field(default_factory=list)
    files: List[str] = field(default_factory=list)
    backend: str = ""
    tokens_retrieved: int = 0
    tokens_if_dumped: int = 0

    @property
    def saved(self) -> int:
        return max(0, self.tokens_if_dumped - self.tokens_retrieved)

    @property
    def saved_pct(self) -> float:
        if self.tokens_if_dumped == 0:
            return 0.0
        return 100.0 * self.saved / self.tokens_if_dumped

    def report(self) -> str:
        return (f"JIT retrieval via {self.backend}: {self.tokens_retrieved} tokens "
                f"vs {self.tokens_if_dumped} if files were dumped "
                f"— saved {self.saved} ({self.saved_pct:.0f}%)")


def jit_retrieve(layer, identifiers: List[str]) -> RetrievalResult:
    """Gather compact reference snippets for `identifiers` and compare to dumping every
    file those references live in."""
    root = getattr(layer.primary, "root", ".")
    snippets: List[str] = []
    files: set = set()
    for ident in identifiers:
        for kind in _KINDS:
            result = layer._run(kind, ident)
            for ref in result.references:
                snippets.append(f"{ref.path}:{ref.line}: {ref.snippet}")
                files.add(ref.path)

    retrieved = estimate_tokens("\n".join(snippets))

    dumped_text = []
    for rel in sorted(files):
        path = os.path.join(root, rel)
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                dumped_text.append(fh.read())
        except OSError:
            continue
    dumped = estimate_tokens("\n".join(dumped_text))

    return RetrievalResult(
        identifiers=list(identifiers), snippets=snippets, files=sorted(files),
        backend=layer.backend_name, tokens_retrieved=retrieved,
        tokens_if_dumped=dumped,
    )
