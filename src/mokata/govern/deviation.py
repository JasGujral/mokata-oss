"""Stage 31 — plan-adherence deviation gate (never silently deviate).

During implementation mokata sticks to the APPROVED plan: the approved approach (brainstorm)
or refinement set (refine), the emitted spec, and its acceptance criteria. A deviation — an AC
is wrong/infeasible, the approved approach doesn't work, a materially better design appears, or
an unforeseen constraint blocks it — is a **durable plan change**, so it is human-gated (P2)
and never silent: STOP, surface it (what · why · options), get EXPLICIT approval, and re-enter
the existing approval surface (re-approve the approach/refinements, or amend the spec so every
AC still maps to a test). The request AND the decision are logged to the audit ledger — a plan
change is a gate decision ("review every decision"). This is the FORWARD guardrail; the
two-pass `review` is the backstop that catches an unapproved divergence after the fact.
"""

from __future__ import annotations

from ..prompt import read_yes_no

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

DEVIATION_KIND = "deviation"

# Which part of the approved plan the deviation touches.
SCOPE = "scope"
APPROACH = "approach"
ACCEPTANCE_CRITERIA = "acceptance_criteria"
DESIGN = "design"
DEVIATION_TARGETS = (SCOPE, APPROACH, ACCEPTANCE_CRITERIA, DESIGN)

# Ledger decision values.
PROPOSED = "proposed"
APPROVED = "approved"
DECLINED = "declined"


@dataclass
class DeviationRequest:
    """A proposed change to the approved plan, surfaced for approval."""
    what: str                                   # what would change
    why: str                                    # why it's necessary
    options: List[str] = field(default_factory=list)   # the options offered
    target: str = DESIGN                        # which part of the plan (DEVIATION_TARGETS)
    phase: str = "develop"                      # where it surfaced


@dataclass
class DeviationOutcome:
    approved: bool
    aborted: bool
    reason: str


def render_deviation(req: DeviationRequest) -> str:
    """The human-gate surface: what · why · options. Clean-room wording."""
    lines = [
        f"mokata · DEVIATION from the approved plan ({req.target}) at phase '{req.phase}':",
        f"  what changes: {req.what}",
        f"  why: {req.why}",
    ]
    if req.options:
        lines.append("  options:")
        for opt in req.options:
            lines.append(f"    - {opt}")
    lines.append(
        "Approve this plan change? It re-enters the approval surface (re-approve the "
        "approach/refinements, or amend the spec so every AC still maps to a test). "
        "Nothing proceeds without your explicit yes; the request and decision are audited."
    )
    return "\n".join(lines)


def _default_confirm(text: str) -> bool:
    return read_yes_no(text, "Approve this deviation?")


class DeviationGate:
    """Surface a deviation, human-gate it, and log BOTH the request and the decision to the
    audit ledger. A plan change is never auto-approved silently."""

    def __init__(self, ledger: Any = None) -> None:
        self.ledger = ledger

    def _log(self, req: DeviationRequest, decision: str, **extra: Any) -> Optional[dict]:
        if self.ledger is None:
            return None
        return self.ledger.record(
            DEVIATION_KIND, phase=req.phase, target=req.target, decision=decision,
            what=req.what, why=req.why, options=list(req.options), **extra)

    def request(self, req: DeviationRequest) -> Optional[dict]:
        """Record that a deviation was surfaced (decision: proposed)."""
        return self._log(req, PROPOSED)

    def resolve(self, req: DeviationRequest, approved: bool,
                approver: str = "user") -> DeviationOutcome:
        """Record the human decision (approved/declined) and return the outcome."""
        self._log(req, APPROVED if approved else DECLINED, approver=approver)
        if approved:
            return DeviationOutcome(
                True, False,
                "approved — re-enter the approval surface (re-approve the "
                "approach/refinements, or amend the spec) before proceeding")
        return DeviationOutcome(
            False, True, "declined — implement strictly to the approved plan")

    def submit(self, req: DeviationRequest,
               confirm: Optional[Callable[[str], bool]] = None,
               assume_yes: bool = False) -> DeviationOutcome:
        """Surface (what · why · options) → human-gate → log the request and the decision.
        A plan change is never silent: `assume_yes`/`confirm` IS the explicit approval."""
        self.request(req)
        if assume_yes:
            approved = True
        else:
            gate = confirm or _default_confirm
            approved = gate(render_deviation(req))
        return self.resolve(req, approved)
