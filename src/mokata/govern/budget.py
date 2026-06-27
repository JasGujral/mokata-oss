"""F5 — savings statusline + budget.

Turns the trackers into measurable savings: each governance win (JIT retrieval vs a
file-dump baseline, compression vs raw, a capped handback vs raw context) is recorded as
a baseline-vs-actual event and logged to the audit ledger. `mokata budget` aggregates the
ledger into a report; `budget_statusline` is the live one-liner. Built on F1/F2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List


@dataclass
class SavingsEvent:
    label: str
    baseline_tokens: int
    actual_tokens: int

    @property
    def saved(self) -> int:
        return max(0, self.baseline_tokens - self.actual_tokens)


@dataclass
class BudgetReport:
    events: List[SavingsEvent] = field(default_factory=list)

    @property
    def baseline(self) -> int:
        return sum(e.baseline_tokens for e in self.events)

    @property
    def actual(self) -> int:
        return sum(e.actual_tokens for e in self.events)

    @property
    def saved(self) -> int:
        return self.baseline - self.actual

    @property
    def pct(self) -> float:
        return 100.0 * self.saved / self.baseline if self.baseline else 0.0

    def render(self) -> str:
        lines = ["mokata budget — token savings:"]
        for e in self.events:
            lines.append(f"  {e.label}: {e.baseline_tokens} -> {e.actual_tokens} "
                         f"(saved {e.saved})")
        lines.append(f"  TOTAL: saved {self.saved} of {self.baseline} "
                     f"({self.pct:.0f}%)")
        return "\n".join(lines)

    @classmethod
    def from_ledger(cls, ledger: Any) -> "BudgetReport":
        events = [
            SavingsEvent(e.get("label", "?"), int(e.get("baseline", 0)),
                         int(e.get("actual", 0)))
            for e in ledger.entries() if e.get("kind") == "savings"
        ]
        return cls(events=events)


def budget_statusline(report: BudgetReport) -> str:
    return f"mokata · saved {report.saved} tok ({report.pct:.0f}%)"


class SavingsTracker:
    def __init__(self, ledger: Any = None) -> None:
        self.events: List[SavingsEvent] = []
        self._ledger = ledger

    def record(self, label: str, baseline_tokens: int,
               actual_tokens: int) -> SavingsEvent:
        event = SavingsEvent(label, baseline_tokens, actual_tokens)
        self.events.append(event)
        if self._ledger is not None:
            self._ledger.record("savings", label=label, baseline=baseline_tokens,
                                actual=actual_tokens, saved=event.saved)
        return event

    def record_retrieval(self, result: Any) -> SavingsEvent:
        """Record a JIT-retrieval win (F2): file-dump baseline vs retrieved tokens."""
        label = "retrieval:" + ",".join(result.identifiers)
        return self.record(label, result.tokens_if_dumped, result.tokens_retrieved)

    def report(self) -> BudgetReport:
        return BudgetReport(events=list(self.events))
