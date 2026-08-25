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

from .notify import announce_prompt


# A3 (0.0.19) — WHY the reader answered as it did. Doc 85 §7g at mokata's PRIMARY durable-write
# gate: `False` used to mean BOTH "a human was asked and said no" AND "no human was ever asked,
# because there was no TTY", and every caller got the same bool. The two facts have opposite
# remedies — one is settled and must not be re-asked, the other is a wait that a human can still
# redeem — so they get separate representations, not one shared one.
#
# THE MODEL IS `RunResolution` (§7g names it): an answer that carries its own provenance cannot be
# mistaken for a different answer.
ASKED = "asked"                          # a human was asked and answered (yes OR no)
NO_TTY = "no-tty"                        # never asked — stdin is not a terminal
UNREADABLE_STDIN = "unreadable-stdin"    # asked, but stdin died before a human answered


@dataclass(frozen=True)
class ConsentDecision:
    """THE answer of a y/N human gate, with the basis it was reached on (doc 85 §3: a `*Decision`
    is a recorded human/router choice).

    ⭐ IT IS TRUTHY EXACTLY WHEN APPROVED, and that is what makes a PARTIAL conversion possible
    without the compatibility shim §7d forbids. There is ONE reader, ONE return type and ONE code
    path: a call site that only ever asked "may I write?" keeps asking it in a boolean context and
    is byte-identical in behaviour; a call site that must tell the two nos apart reads `basis`.
    Nothing branches on a version, nothing is deprecated, and there is no second entry point kept
    alive beside this one.

    FAIL-CLOSED is unchanged (P2): `approved` is True only on an explicit yes."""

    approved: bool
    basis: str

    def __bool__(self) -> bool:
        return self.approved

    @property
    def answered_by_human(self) -> bool:
        """True only when a human was actually asked and gave this answer. An unreadable stdin is
        NOT a human answer — the read was attempted, but nobody answered it — so it groups with
        `NO_TTY` here even though it keeps its own basis for anyone who needs to say WHICH."""
        return self.basis == ASKED


def read_yes_no(prompt: str, question: str = "") -> ConsentDecision:
    """Show `prompt` (plus an optional tailored `question`), read a y/N answer, and approve only on
    an explicit yes. Non-interactive / EOF / unreadable stdin / anything else → declined
    (never auto-approve), with the `basis` saying WHICH of those it was.

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
        return ConsentDecision(False, NO_TTY)
    full = prompt + (f"\n{question} [y/N] " if question else "")
    # Lane F — mokata's own attention channel, raised HERE and not at the twenty-four call sites
    # that reach this reader, for the same reason `awaiting_block` builds the loud head once: a
    # signal each caller emits its own way is a signal that drifts. It sits AFTER the TTY guard on
    # purpose — "never fire off a TTY" is then structural rather than a second check that can part
    # company with the first. Raises nothing (`notify.notify` is the seam), so the gate below is
    # exactly as reliable as it was before this line existed.
    announce_prompt()
    try:
        return ConsentDecision(input(full).strip().lower() in ("y", "yes"), ASKED)
    except (EOFError, OSError) as exc:
        # Don't swallow it silently: a durable action was declined because stdin was unreadable.
        print(f"mokata: unreadable stdin ({exc.__class__.__name__}) — defaulting to No",
              file=sys.stderr)
        return ConsentDecision(False, UNREADABLE_STDIN)


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
    # Lane F — this reader blocks a human exactly as `read_yes_no` does; it is only a different
    # SHAPE of answer. A notifier wired to the y/N reader alone would have left the memory-edit and
    # config-wizard gates silent, which is the corpus axis §7j is about.
    announce_prompt()
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
