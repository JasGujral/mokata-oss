"""E6 — debug mode (`/debug`).

Root-cause-before-fix with N-strikes escalation: form hypotheses, rule them out; after N
ruled-out hypotheses without a root cause, escalate (bumping the model via the E4 router
when one is supplied). A fix is gated behind an identified root cause — not prose.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional


class DebugError(Exception):
    pass


class RootCauseRequiredError(DebugError):
    """Raised on an attempt to propose a fix before a root cause is identified."""


@dataclass
class Hypothesis:
    text: str
    ruled_out: bool = False


class DebugSession:
    def __init__(self, problem: str, max_strikes: int = 3, router: Any = None,
                 ledger: Any = None) -> None:
        self.problem = problem
        self.max_strikes = max_strikes
        self.router = router
        self.ledger = ledger
        self.hypotheses: List[Hypothesis] = []
        self.strikes = 0
        self.root_cause: Optional[str] = None
        self.escalated = False
        self.model = router.cheapest() if router is not None else None

    def _log(self, step: str, **fields: Any) -> None:
        if self.ledger is not None:
            self.ledger.record("debug", problem=self.problem, step=step, **fields)

    def hypothesize(self, text: str) -> Hypothesis:
        h = Hypothesis(text=text)
        self.hypotheses.append(h)
        self._log("hypothesize", text=text)
        return h

    def rule_out(self, hypothesis: Hypothesis) -> None:
        hypothesis.ruled_out = True
        self.strikes += 1
        self._log("rule_out", text=hypothesis.text, strikes=self.strikes)
        if self.strikes >= self.max_strikes and self.root_cause is None:
            self._escalate()

    def _escalate(self) -> None:
        self.escalated = True
        if self.router is not None and self.model is not None:
            nxt = self.router.escalate(self.model)
            if nxt is not None:
                self.model = nxt
        self._log("escalate", model=(self.model.name if self.model else None))

    def set_root_cause(self, cause: str) -> None:
        self.root_cause = cause
        self._log("root_cause", cause=cause)

    def propose_fix(self) -> str:
        # GATE: no fix until the root cause is identified.
        if self.root_cause is None:
            raise RootCauseRequiredError(
                "root-cause-before-fix: identify the root cause before proposing a fix")
        self._log("propose_fix", cause=self.root_cause)
        return f"fix addressing root cause: {self.root_cause}"
