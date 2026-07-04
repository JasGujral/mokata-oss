"""The single human-gate reader (Stage 39 / M3 dedup).

One shared y/N prompt so the gated-write/deviation/reset/migrate/etc. surfaces don't each
re-implement the `input(...) in {"y","yes"}` + EOF handling. The default is always NO: an EOF
(non-interactive) or any non-yes answer declines — a durable action is never auto-approved.
Pure stdlib, no dependencies.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Callable, Optional


def read_yes_no(prompt: str, question: str = "") -> bool:
    """Show `prompt` (plus an optional tailored `question`), read a y/N answer, and return True
    only on an explicit yes. Non-interactive / EOF / unreadable stdin / anything else → False
    (never auto-approve).

    Fail-closed (P2): this is mokata's primary durable-write gate, and its primary runtime is an
    agent harness — which often leaves stdin CONNECTED but silent (never writes, never closes).
    A bare `input()` there would BLOCK FOREVER. So, mirroring `_cli_ask` (Stage 1b): when stdin
    is not a TTY, take the safe default (No) and log why WITHOUT calling `input()` — non-interactive
    automation approves via the explicit `assume_yes`/`--yes` path, not by piping an answer. The
    Stage-1a `(EOFError, OSError) → No` catch is kept as defense-in-depth (a lying isatty, a
    captured/redirected stream). Either way we never hang and never fail open to Yes."""
    if not _stdin_is_tty():
        print("mokata: stdin is not a TTY — defaulting to No without prompting "
              "(approve non-interactively with --yes / assume_yes).", file=sys.stderr)
        return False
    full = prompt + (f"\n{question} [y/N] " if question else "")
    try:
        return input(full).strip().lower() in ("y", "yes")
    except (EOFError, OSError) as exc:
        # Don't swallow it silently: a durable action was declined because stdin was unreadable.
        print(f"mokata: unreadable stdin ({exc.__class__.__name__}) — defaulting to No",
              file=sys.stderr)
        return False


def _stdin_is_tty() -> bool:
    """True only when stdin is a real interactive terminal. Fail-closed and never raises: a
    missing stdin (None) or an isatty() that itself errors counts as non-interactive."""
    stdin = getattr(sys, "stdin", None)
    if stdin is None:
        return False
    try:
        return bool(stdin.isatty())
    except (OSError, ValueError):
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


def _reject_on_unreadable(exc: Exception) -> "GateResponse":
    """Fail-closed helper (P2): an unreadable stdin (EOF, closed/non-interactive, or an
    OSError from a captured/redirected stream) declines to the SAFE DEFAULT — no change —
    and logs why rather than crashing or silently swallowing it."""
    print(f"mokata: unreadable stdin ({exc.__class__.__name__}) — defaulting to reject "
          f"(no change)", file=sys.stderr)
    return GateResponse("reject")


def read_approve_edit_reject(prompt: str, proposed: Optional[str], *,
                             reader: Optional[Callable[[str], str]] = None) -> GateResponse:
    """Show `prompt`, then read a one-key choice over `proposed`:
        a / approve → apply `proposed`        (GateResponse("approve", proposed))
        e / edit    → read + apply a new value (GateResponse("edit", <typed>))
        r / reject / blank / EOF → no change   (GateResponse("reject"))   ← SAFE DEFAULT
    `reader` is injectable for testing; it resolves to the live `input` at call time when
    omitted (so it honours patching). Fail-closed (P2): EOF *or* an OSError from an
    unreadable/captured stdin defaults to reject (no change), logged — never raises."""
    reader = reader or input
    full = prompt + "\n  [a]pprove · [e]dit · [r]eject (default: reject — no change): "
    try:
        ans = reader(full).strip().lower()
    except (EOFError, OSError) as exc:
        return _reject_on_unreadable(exc)
    if ans in ("a", "approve"):
        return GateResponse("approve", proposed)
    if ans in ("e", "edit"):
        try:
            new_value = reader("  new value: ")
        except (EOFError, OSError) as exc:
            return _reject_on_unreadable(exc)
        return GateResponse("edit", new_value)
    return GateResponse("reject")             # r / blank / anything else → safe default
