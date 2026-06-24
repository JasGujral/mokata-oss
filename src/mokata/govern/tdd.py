"""E1 — RED before GREEN, enforced in the spine.

Implementation of a behaviour is allowed only after a test for it has been recorded as
FAILING (RED). `TddGuard.guard_implementation` raises until that RED is on record — this
is the executable form of the develop skill's `no-code-without-failing-test` gate.
"""

from __future__ import annotations

from typing import Any, Set

GATE_ID = "no-code-without-failing-test"


class RedBeforeGreenError(Exception):
    """Raised when implementation is attempted before its test has failed."""


class TddGuard:
    def __init__(self, ledger: Any = None) -> None:
        self._red: Set[str] = set()      # tests that have been seen FAILING
        self._green: Set[str] = set()    # tests that later passed
        self._ledger = ledger

    def record_red(self, test_id: str) -> None:
        self._red.add(test_id)
        if self._ledger is not None:
            self._ledger.record("tdd", event="red", test=test_id)

    def record_green(self, test_id: str) -> None:
        self._green.add(test_id)
        if self._ledger is not None:
            self._ledger.record("tdd", event="green", test=test_id)

    def allow_implementation(self, test_id: str) -> bool:
        return test_id in self._red

    def guard_implementation(self, test_id: str) -> None:
        if not self.allow_implementation(test_id):
            if self._ledger is not None:
                self._ledger.record("tdd", event="blocked", test=test_id, gate=GATE_ID)
            raise RedBeforeGreenError(
                f"RED before GREEN: '{test_id}' has no recorded failing run; write the "
                f"test and watch it fail before implementing."
            )
        if self._ledger is not None:
            self._ledger.record("tdd", event="allowed", test=test_id, gate=GATE_ID)
