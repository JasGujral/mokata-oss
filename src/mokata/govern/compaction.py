"""F3 — sub-agent context isolation: cap the handback.

Heavy sub-agent work returns a compact SUMMARY, not raw context. Since 0.0.22 D4 (H-3) the summary
is the subagent's HandbackReport (`subagent_contract.parse_handback`) whenever it returned one;
`cap_summary` is the FALLBACK for output that carries none, and it keeps the HEAD and the TAIL
around a truncation marker — a subagent's conclusion is at the end of what it wrote, so a head-only
cut threw away the part the parent needed most. `tail=False` keeps the head only, for a caller whose
text is RANKED (the per-turn recall pack: its tail is its least relevant item). Reuses the F1 token
estimator; no new dependency.
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


def cap_summary(text: str, cap_tokens: int, tail: bool = True) -> Handback:
    """Return a handback whose estimated tokens are <= cap_tokens. Text already within the cap passes
    through unchanged. Over the cap: the head (two thirds of the room) + the marker + the tail (one
    third), or the head + the marker when `tail` is False."""
    original = estimate_tokens(text)
    if original <= cap_tokens:
        return Handback(summary=text, tokens=original, capped=False,
                        original_tokens=original)
    max_chars = max(1, cap_tokens * 4)        # ~4 chars/token (the F1 rule of thumb)
    if max_chars <= len(_MARK):
        summary = text[:max_chars]
    elif not tail:
        summary = text[:max_chars - len(_MARK)] + _MARK
    else:
        room = max_chars - len(_MARK)
        head = room - room // 3
        back = room - head
        summary = text[:head] + _MARK + (text[-back:] if back else "")
    return Handback(summary=summary, tokens=estimate_tokens(summary), capped=True,
                    original_tokens=original)
