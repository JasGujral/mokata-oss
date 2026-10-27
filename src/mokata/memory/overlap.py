"""R5 (0.0.26) — WRITE-TIME OVERLAP: update the existing learning instead of adding a near-duplicate.

`healing.detect_issues` already finds duplicates AFTER they land (the canonical-subject group, the
embedding near-dup bar). That is a clean-up loop: two rows exist, someone resolves them later. This
is the same question asked BEFORE the write, scored on five independent dimensions:

    kind      — the same typed part (effective kind)
    subject   — the same canonical subject (`healing.canonical_subject`, the ONE grouping key)
    value     — token Jaccard of the values ≥ VALUE_JACCARD
    citations — at least one shared cited path/symbol (about_code ∪ the paths in the value)
    scope     — the same scope level and id

4–5 matches  → the write UPDATES the existing entry in place (same id; the gate prompt says so);
2–3 matches  → the write lands as a new entry and the prompt FLAGS the overlap for consolidation;
0–1          → unrelated.

`kind` and `scope` match for almost every pair on a single-user store, so they are CONTEXT
dimensions: an overlap only counts when at least one CONTENT dimension (subject, value, citations)
matches. Without that rule every write would be "flagged" against every same-kind entry — noise
that teaches the human to ignore the flag.

Every one of these still goes through the human gate — the score only decides what the human is
asked to approve. Nothing is merged silently. Pure: no I/O, no embedder call.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Set, Tuple

UPDATE_AT = 4           # ≥ this many matching dimensions → update the existing entry
FLAG_AT = 2             # ≥ this many (but < UPDATE_AT) → flag for consolidation
VALUE_JACCARD = 0.6     # token overlap at which two values "say the same thing"

CONTENT_DIMENSIONS = ("subject", "value", "citations")

_TOKEN = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class Overlap:
    existing: Any
    score: int
    dimensions: Tuple[str, ...]

    @property
    def updates(self) -> bool:
        return self.score >= UPDATE_AT

    def describe(self) -> str:
        return f"{self.score}/5 ({', '.join(self.dimensions)})"


def _tokens(text: Any) -> Set[str]:
    if not isinstance(text, str):
        return set()            # a structured value has no prose to compare
    return set(_TOKEN.findall(text.lower()))


def _cited(item: Any) -> Set[str]:
    from .grounding import extract_value_citations
    out = {str(a).strip() for a in (getattr(item, "about_code", None) or []) if str(a).strip()}
    value = getattr(item, "value", "")
    for c in extract_value_citations(value if isinstance(value, str) else ""):
        if c.kind == "path":
            out.add(c.target)
    return out


def overlap(incoming: Any, existing: Any) -> Overlap:
    from .healing import canonical_subject
    dims: List[str] = []
    if (incoming.kind or incoming.mtype) == (existing.kind or existing.mtype):
        dims.append("kind")
    if canonical_subject(str(incoming.subject)) == canonical_subject(str(existing.subject)):
        dims.append("subject")
    a, b = _tokens(incoming.value), _tokens(existing.value)
    if a and b and len(a & b) / len(a | b) >= VALUE_JACCARD:
        dims.append("value")
    ca, cb = _cited(incoming), _cited(existing)
    if ca and cb and ca & cb:
        dims.append("citations")
    if (getattr(incoming, "scope_level", ""), getattr(incoming, "scope_id", "")) == \
            (getattr(existing, "scope_level", ""), getattr(existing, "scope_id", "")):
        dims.append("scope")
    return Overlap(existing=existing, score=len(dims), dimensions=tuple(dims))


def best_overlap(incoming: Any, candidates: Iterable[Any]) -> Optional[Overlap]:
    """The strongest overlap ≥ FLAG_AT among same-`mtype` candidates (never the item itself), or
    None. Ties go to the most recently created entry — the one a human last approved."""
    best: Optional[Overlap] = None
    for c in candidates:
        if c.id == incoming.id or c.mtype != incoming.mtype:
            continue
        o = overlap(incoming, c)
        if o.score < FLAG_AT or not set(o.dimensions) & set(CONTENT_DIMENSIONS):
            continue
        if best is None or (o.score, c.created_at) > (best.score, best.existing.created_at):
            best = o
    return best


__all__ = ["FLAG_AT", "Overlap", "UPDATE_AT", "VALUE_JACCARD", "best_overlap", "overlap"]
