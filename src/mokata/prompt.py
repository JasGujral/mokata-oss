"""The single human-gate reader (Stage 39 / M3 dedup).

One shared y/N prompt so the gated-write/deviation/reset/migrate/etc. surfaces don't each
re-implement the `input(...) in {"y","yes"}` + EOF handling. The default is always NO: an EOF
(non-interactive) or any non-yes answer declines — a durable action is never auto-approved.
Pure stdlib, no dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


def read_yes_no(prompt: str, question: str = "") -> bool:
    """Show `prompt` (plus an optional tailored `question`), read a y/N answer, and return True
    only on an explicit yes. EOF / anything else → False (never auto-approve)."""
    full = prompt + (f"\n{question} [y/N] " if question else "")
    try:
        return input(full).strip().lower() in ("y", "yes")
    except EOFError:
        return False


# Stage 54c — one-key human-gate response over an EDITABLE value (e.g. a memory edit or a
# self-healing old→new diff): approve the proposed value, edit it, or reject it. The SAFE
# DEFAULT is reject = no change — a durable change is never auto-applied. This only READS the
# decision; it does NOT bypass any gate. Security blocks (the WriteGate secret hard-block) fire
# regardless of the answer — `approve` can never override a security block.

@dataclass
class GateResponse:
    action: str                       # "approve" | "edit" | "reject"
    value: Optional[str] = None       # the value to apply (proposed for approve, typed for edit)

    @property
    def is_change(self) -> bool:
        """True when something should be applied (approve or edit); reject = no change."""
        return self.action in ("approve", "edit")


def read_approve_edit_reject(prompt: str, proposed: Optional[str], *,
                             reader: Optional[Callable[[str], str]] = None) -> GateResponse:
    """Show `prompt`, then read a one-key choice over `proposed`:
        a / approve → apply `proposed`        (GateResponse("approve", proposed))
        e / edit    → read + apply a new value (GateResponse("edit", <typed>))
        r / reject / blank / EOF → no change   (GateResponse("reject"))   ← SAFE DEFAULT
    `reader` is injectable for testing; it resolves to the live `input` at call time when
    omitted (so it honours patching). Never raises on EOF (defaults to reject)."""
    reader = reader or input
    full = prompt + "\n  [a]pprove · [e]dit · [r]eject (default: reject — no change): "
    try:
        ans = reader(full).strip().lower()
    except EOFError:
        return GateResponse("reject")
    if ans in ("a", "approve"):
        return GateResponse("approve", proposed)
    if ans in ("e", "edit"):
        try:
            new_value = reader("  new value: ")
        except EOFError:
            return GateResponse("reject")
        return GateResponse("edit", new_value)
    return GateResponse("reject")             # r / blank / anything else → safe default
