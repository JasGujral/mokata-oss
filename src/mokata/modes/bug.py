"""E5 — bug-fix mode (`/bug`).

The full flow: capture a reproducer FIRST, then fix, with label progression
reported -> reproduced -> fixing -> verified. Reproducer-before-fix is a real gate
(reusing the TddGuard / E1 — the reproducer is a failing test recorded RED), not prose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

from ..govern import TddGuard

REPORTED = "reported"
REPRODUCED = "reproduced"
FIXING = "fixing"
VERIFIED = "verified"
BUG_LABELS = (REPORTED, REPRODUCED, FIXING, VERIFIED)


class BugError(Exception):
    pass


class ReproRequiredError(BugError):
    """Raised on an attempt to start a fix before a reproducer exists."""


@dataclass
class Bug:
    id: str
    title: str
    label: str = REPORTED
    reproducer: Optional[str] = None
    events: List[str] = field(default_factory=list)


class BugFlow:
    def __init__(self, bug: Bug, guard: Optional[TddGuard] = None,
                 ledger: Any = None) -> None:
        self.bug = bug
        self.ledger = ledger
        self.guard = guard or TddGuard(ledger=ledger)

    @property
    def label(self) -> str:
        return self.bug.label

    def _key(self) -> str:
        return f"bug:{self.bug.id}"

    def _to(self, label: str, step: str) -> None:
        self.bug.label = label
        self.bug.events.append(step)
        if self.ledger is not None:
            self.ledger.record("bug", id=self.bug.id, step=step, label=label)

    def reproduce(self, reproducer: str) -> None:
        """Capture the reproducer (a failing test). Records RED via the TddGuard."""
        self.bug.reproducer = reproducer
        self.guard.record_red(self._key())
        self._to(REPRODUCED, f"reproduced via {reproducer}")

    def start_fix(self) -> None:
        # GATE: no fix until a reproducer is on record (reuses the RED-before-GREEN gate).
        if self.bug.reproducer is None or not self.guard.allow_implementation(self._key()):
            raise ReproRequiredError(
                "reproducer-before-fix: capture a failing reproducer before fixing")
        self._to(FIXING, "fixing")

    def verify(self) -> None:
        if self.bug.label != FIXING:
            raise BugError("verify is only valid after a fix has started")
        self.guard.record_green(self._key())
        self._to(VERIFIED, "verified — reproducer now passes")
