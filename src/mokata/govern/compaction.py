"""F3 — sub-agent context isolation: cap the handback.

Heavy sub-agent work returns a compact SUMMARY, not raw context. `cap_summary` bounds a
handback to a token cap (keeping a head slice + a truncation marker) so a parent context
never absorbs a sub-agent's full working set. Wired into the execmode handback path.
Reuses the F1 token estimator; no new dependency.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..bootstrap import estimate_tokens

_MARK = " …[handback capped]"


@dataclass
class Handback:
    summary: str
    tokens: int
    capped: bool
    original_tokens: int


def cap_summary(text: str, cap_tokens: int) -> Handback:
    """Return a handback whose estimated tokens are <= cap_tokens. Text already within
    the cap passes through unchanged."""
    original = estimate_tokens(text)
    if original <= cap_tokens:
        return Handback(summary=text, tokens=original, capped=False,
                        original_tokens=original)
    max_chars = max(1, cap_tokens * 4)        # ~4 chars/token (the F1 rule of thumb)
    if max_chars <= len(_MARK):
        summary = text[:max_chars]
    else:
        summary = text[:max_chars - len(_MARK)] + _MARK
    return Handback(summary=summary, tokens=estimate_tokens(summary), capped=True,
                    original_tokens=original)
