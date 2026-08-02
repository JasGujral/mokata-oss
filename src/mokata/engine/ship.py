"""Stage 34 Part A — the human-owned landing decision at `ship`.

The closing step. mokata does NOT auto-merge/PR/delete (irreversible, human-owned). It records
the human's finish decision (merge / open PR / keep branch / discard) in the audit ledger. The
git action itself is performed by the harness ONLY on explicit confirmation (P2); this module
records — it never lands anything on its own.

REVIEW-FIX.R3 — the readiness CHECK that used to live here (`check_ship_readiness` +
`ShipReadiness`) is DELETED, not moved. It was a second, divergent answer to "has review
passed?": a `review_passed` boolean the CALLER supplied, trusted on its say-so, with zero
production callers (only the engine re-export and its own tests). The ONE ship-gating truth is
the persisted `review_verdict` progress event — written by `progress_events.record_review_verdict`
and read by `progress_events.latest_review_verdict` / `ship_review_gate`, which key it to the run
(R1) inside a freshness bound (R2). A caller-supplied boolean can satisfy neither, so keeping a
look-alike an agent could reach for was the risk, not the value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

# The landing options the human chooses between. mokata never picks for them.
MERGE, OPEN_PR, KEEP_BRANCH, DISCARD = "merge", "pr", "keep", "discard"
LANDING_OPTIONS = (MERGE, OPEN_PR, KEEP_BRANCH, DISCARD)

# Stage 60 — how many of the run's "what + decision + why" lines the end-of-run recap shows.
# Bounded/frugal (P11): a compact tail of the audit timeline, never the whole ledger.
FINISH_SUMMARY_TAIL = 20


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
    except (OSError, ValueError, ImportError):
        # OSError = the ledger file could not be read; ValueError (json.JSONDecodeError) = a line is
        # unparseable; ImportError = a half-installed engine. The recap is presentation-only prose
        # over an already-recorded ledger — the caller shows a friendly note in its place, and the
        # ledger's own integrity is reported (loudly, as an error) by `mokata doctor`.
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
