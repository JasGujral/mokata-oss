"""The single human-gate reader (Stage 39 / M3 dedup).

One shared y/N prompt so the gated-write/deviation/reset/migrate/etc. surfaces don't each
re-implement the `input(...) in {"y","yes"}` + EOF handling. The default is always NO: an EOF
(non-interactive) or any non-yes answer declines — a durable action is never auto-approved.
Pure stdlib, no dependencies.
"""

from __future__ import annotations


def read_yes_no(prompt: str, question: str = "") -> bool:
    """Show `prompt` (plus an optional tailored `question`), read a y/N answer, and return True
    only on an explicit yes. EOF / anything else → False (never auto-approve)."""
    full = prompt + (f"\n{question} [y/N] " if question else "")
    try:
        return input(full).strip().lower() in ("y", "yes")
    except EOFError:
        return False
