"""E6 — optimize mode (`/optimize`).

Measure-first: you cannot apply a change before a baseline measurement, and an
optimization is kept only when a before/after measurement shows it is faster (lower is
better) AND behaviour is preserved. The measure-first rule is a real gate, not prose.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


class OptimizeError(Exception):
    pass


class MeasureFirstError(OptimizeError):
    """Raised on an attempt to change code before a baseline is measured."""


@dataclass
class OptimizeResult:
    kept: bool
    improved: bool
    behavior_preserved: bool
    baseline: float
    after: float

    def render(self) -> str:
        verb = "kept" if self.kept else "reverted"
        return (f"optimize {verb}: {self.baseline} -> {self.after} "
                f"(improved={self.improved}, behavior_preserved={self.behavior_preserved})")


class OptimizeSession:
    def __init__(self, target: str, ledger: Any = None) -> None:
        self.target = target
        self.ledger = ledger
        self.baseline: Optional[float] = None
        self.after: Optional[float] = None
        self.change: Optional[str] = None
        self.behavior_preserved: bool = False

    def _log(self, step: str, **fields: Any) -> None:
        if self.ledger is not None:
            self.ledger.record("optimize", target=self.target, step=step, **fields)

    def measure_baseline(self, value: float) -> None:
        self.baseline = value
        self._log("baseline", value=value)

    def apply_change(self, description: str) -> None:
        # GATE: measure-first — no change before a baseline exists.
        if self.baseline is None:
            raise MeasureFirstError(
                "measure-first: record a baseline measurement before changing code")
        self.change = description
        self._log("apply_change", description=description)

    def measure_after(self, value: float, behavior_preserved: bool = True) -> None:
        if self.change is None:
            raise OptimizeError("apply a change before measuring its effect")
        self.after = value
        self.behavior_preserved = behavior_preserved
        self._log("after", value=value, behavior_preserved=behavior_preserved)

    def accept(self) -> OptimizeResult:
        if self.baseline is None or self.after is None:
            raise OptimizeError(
                "measure-first: need both a baseline and an after measurement")
        improved = self.after < self.baseline       # lower is faster
        kept = improved and self.behavior_preserved
        self._log("accept", kept=kept, improved=improved)
        return OptimizeResult(kept=kept, improved=improved,
                              behavior_preserved=self.behavior_preserved,
                              baseline=self.baseline, after=self.after)
