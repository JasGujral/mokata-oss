"""mokata execution-depth modes (E5/E6) — the bug, debug, and optimize engines that
replace the Stage-6 command scaffolds. Each enforces its skill's gate in code:
reproducer-before-fix, root-cause-before-fix (N-strikes), and measure-first.
"""

from .bug import (
    BUG_LABELS,
    FIXING,
    REPORTED,
    REPRODUCED,
    VERIFIED,
    Bug,
    BugError,
    BugFlow,
    ReproRequiredError,
)
from .debug import DebugError, DebugSession, Hypothesis, RootCauseRequiredError
from .optimize import (
    MeasureFirstError,
    OptimizeError,
    OptimizeResult,
    OptimizeSession,
)

__all__ = [
    # E5 bug
    "Bug", "BugFlow", "BugError", "ReproRequiredError",
    "REPORTED", "REPRODUCED", "FIXING", "VERIFIED", "BUG_LABELS",
    # E6 debug
    "DebugSession", "Hypothesis", "DebugError", "RootCauseRequiredError",
    # E6 optimize
    "OptimizeSession", "OptimizeResult", "OptimizeError", "MeasureFirstError",
]
