"""Stage 34 Part A — `ship` (finish) readiness + the human-owned landing decision.

The closing step. mokata does NOT auto-merge/PR/delete (irreversible, human-owned). It:
  - verifies it's actually done — evidence over claims (P10): a persisted spec with met ACs,
    the test suite green, and review passed; otherwise it BLOCKS with what's missing;
  - records the human's finish decision (merge / open PR / keep branch / discard) in the audit
    ledger. The git action itself is performed by the harness ONLY on explicit confirmation
    (P2); this module verifies and records — it never lands anything on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

from .spec_gate import load_emitted_spec

# The landing options the human chooses between. mokata never picks for them.
MERGE, OPEN_PR, KEEP_BRANCH, DISCARD = "merge", "pr", "keep", "discard"
LANDING_OPTIONS = (MERGE, OPEN_PR, KEEP_BRANCH, DISCARD)

# Stage 60 — how many of the run's "what + decision + why" lines the end-of-run recap shows.
# Bounded/frugal (P11): a compact tail of the audit timeline, never the whole ledger.
FINISH_SUMMARY_TAIL = 20


@dataclass
class ShipReadiness:
    ready: bool
    blockers: List[str] = field(default_factory=list)
    spec_acs: int = 0

    def render(self) -> str:
        if self.ready:
            return (f"[READY] ship — spec with {self.spec_acs} acceptance "
                    f"criterion{'' if self.spec_acs == 1 else 'a'} met, tests green, review "
                    "passed. Choose how to land it.")
        lines = ["[BLOCKED] ship — not done yet:"]
        for b in self.blockers:
            lines.append(f"  - {b}")
        return "\n".join(lines)


def check_ship_readiness(store: Any, tests_green: bool, review_passed: bool,
                         ledger: Any = None) -> ShipReadiness:
    """Decide whether the work is ACTUALLY done (evidence over claims). `tests_green` and
    `review_passed` are the verified signals the caller supplies; AC-completeness is read
    from the persisted spec (Stage 32). Any unmet signal blocks with what's missing. The
    decision is logged to the audit ledger ("review every decision")."""
    blockers: List[str] = []

    spec = load_emitted_spec(store)
    acs = len(spec.criteria) if spec is not None else 0
    if spec is None or acs < 1:
        blockers.append(
            "no emitted spec with acceptance criteria — draft + emit it (/mokata:spec)")
    if not tests_green:
        blockers.append("test suite is not green — fix the failing tests before shipping")
    if not review_passed:
        blockers.append("review has not passed — run /mokata:review and resolve findings")

    ready = not blockers
    if ledger is not None:
        ledger.record("ship", decision="ready" if ready else "blocked",
                      blockers=list(blockers), spec_acs=acs)
    return ShipReadiness(ready=ready, blockers=blockers, spec_acs=acs)


def build_finish_summary(ledger: Any, tail: int = FINISH_SUMMARY_TAIL) -> List[str]:
    """Stage 60 — the end-of-run "what I changed and WHY" recap: a bounded `audit --why` over
    THIS run's ledger entries (reuses the Stage 49 `why_timeline`). Read-only — derives from the
    ledger entries and returns strings; writes nothing. Degrade-clean: no ledger / no entries
    yields an empty list (the caller shows a friendly note)."""
    if ledger is None:
        return []
    try:
        from ..govern.ledger import why_timeline
        return why_timeline(ledger.entries(), tail=tail)
    except Exception:
        return []


@dataclass
class FinishDecision:
    choice: str
    approved: bool
    note: str = ""
    summary: List[str] = field(default_factory=list)   # Stage 60 — what-changed-and-why recap

    def render(self) -> str:
        """The end-of-run recap: the recorded landing decision + the read-only "what I changed
        and WHY" timeline for this run. Human-owned: it states the decision and whether the
        human approved the landing; it never implies mokata merged anything itself."""
        verb = {MERGE: "merge", OPEN_PR: "open a PR", KEEP_BRANCH: "keep the branch",
                DISCARD: "discard"}.get(self.choice, self.choice)
        state = "approved" if self.approved else "NOT approved (nothing landed by mokata)"
        lines = [f"mokata · ship — landing decision: {verb} ({state})."]
        if self.note:
            lines.append(f"  note: {self.note}")
        lines.append("")
        lines.append("what I changed and why (this run):")
        if self.summary:
            lines.extend(f"  {ln}" for ln in self.summary)
        else:
            lines.append("  (no audited changes recorded for this run)")
        lines.append("")
        lines.append("mokata records the decision; the git action is yours to run — it never "
                     "merges/PRs/deletes on its own.")
        return "\n".join(lines)


def record_finish_decision(ledger: Any, choice: str, approve: Optional[bool] = None,
                           note: str = "", confirmed: Optional[bool] = None) -> FinishDecision:
    """Record the human's landing choice (one of LANDING_OPTIONS) in the audit ledger.
    mokata performs the git action ONLY when `approve` is the human's explicit yes; it
    never merges/PRs/deletes on its own. This records the decision; it does not land it.

    Stage 60: the returned decision also carries the read-only "what I changed and WHY" recap
    (a bounded `audit --why` over this run's ledger) so finishing a run shows what changed.

    Stage 37R (H3): the boolean is `approve`, consistent with the MCP write tools; `confirmed`
    is a DEPRECATED alias kept for backward-compat."""
    if choice not in LANDING_OPTIONS:
        raise ValueError(
            f"unknown landing choice '{choice}'; one of {LANDING_OPTIONS}")
    approved = bool(approve) or bool(confirmed)
    if ledger is not None:
        ledger.record("finish", choice=choice, approved=approved, note=note)
    # Build the recap AFTER recording, so the finish decision itself appears in the timeline.
    summary = build_finish_summary(ledger)
    return FinishDecision(choice=choice, approved=approved, note=note, summary=summary)
