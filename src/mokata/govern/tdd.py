"""E1 — RED before GREEN, enforced in the spine.

Implementation of a behaviour is allowed only after a test for it has been recorded as
FAILING (RED). `TddGuard.guard_implementation` raises until that RED is on record — this
is the executable form of the develop skill's `no-code-without-failing-test` gate.

SI.2 — the RED/GREEN record SURVIVES the process
-------------------------------------------------
That record used to live ONLY in the two sets below, on the guard instance: exit the CLI, crash the
window, or construct a second guard, and every owed test evaporated — the gate that blocks un-tested
code was itself cheatable by a restart. Wire a `store` (and the run it belongs to) and the two sets
become a CACHE of `tdd_state`'s persisted, per-`run_id` truth:

  * every transition writer (`record_red`, `record_green`) persists AT THE MOMENT of transition,
    through the MS.S1 atomic + cross-process-locked `StateStore`;
  * every reader (`allow_implementation`, and so `guard_implementation`) consults that persisted
    state, so a transition made by another process on the same run is seen immediately;
  * construction REHYDRATES: a fresh process on the same run_id resumes exactly what it owed
    (RED stays RED after a `kill -9`), while a NEW run starts unset as before.

Enforcement is UNCHANGED — what blocks and what passes is exactly what it was (SI.1 is the stage
that hook-enforces this state; this one only makes it survive). With no `store` the guard is the
pure in-memory object it always was, byte for byte.
"""

from __future__ import annotations

from typing import Any, Optional, Set

from ..tdd_state import PHASE_GREEN, PHASE_RED, PHASE_UNSET, load, phase_of, record
from ..errors import MokataError

GATE_ID = "no-code-without-failing-test"


class RedBeforeGreenError(MokataError):
    """Raised when implementation is attempted before its test has failed."""


class TddGuard:
    def __init__(self, ledger: Any = None, store: Any = None,
                 run_id: Optional[str] = None) -> None:
        self._red: Set[str] = set()      # tests that have been seen FAILING
        self._green: Set[str] = set()    # tests that later passed
        self._ledger = ledger
        # SI.2 — the durable half. `store` is a StateStore (typically `surface.state`); `run_id`
        # defaults to this session's run (run_id == session_id, see session.py). Without a store the
        # sets above ARE the state, exactly as before.
        self._store = store
        self._run_id: Optional[str] = None
        if store is not None:
            if run_id is None:
                from ..session import current_run_id
                run_id = current_run_id()
            self._run_id = run_id
            self._refresh()              # resume the persisted phase (restart honesty)

    # ---------------------------------------------------------------- persistence (SI.2)
    def _refresh(self) -> None:
        """Re-seed the in-memory cache from the persisted truth. Degrade-clean: an unreadable state
        dir leaves the cache as-is rather than raising into the gate (`StateStore.read` already
        degrades a corrupt file to absent)."""
        if self._store is None or self._run_id is None:
            return
        try:
            self._red, self._green = load(self._store, self._run_id)
        except OSError:
            pass

    def _persist(self, *, red: Set[str] = frozenset(), green: Set[str] = frozenset()) -> None:
        """Persist a transition the instant it happens (locked read-modify-write; atomic replace).
        Degrade-clean: if the state dir cannot be written the guard still enforces from memory for
        the life of THIS process — it degrades to the pre-SI.2 behaviour, never to no gate."""
        if self._store is None or self._run_id is None:
            return
        try:
            self._red, self._green = record(self._store, self._run_id, red=red, green=green)
        except OSError:
            pass

    @property
    def run_id(self) -> Optional[str]:
        """The run this guard's phase is persisted under (None when it is memory-only)."""
        return self._run_id

    def phase(self) -> str:
        """This run's TDD phase — `unset` / `red` / `green` (see `tdd_state`). Read-only: it says
        what the run owes, it gates nothing."""
        self._refresh()
        return phase_of(self._red, self._green)

    def owed(self) -> list:
        """The RED tests not yet seen passing — what this run still owes GREEN."""
        self._refresh()
        return sorted(self._red - self._green)

    # ---------------------------------------------------------------- transitions + the gate
    def record_red(self, test_id: str) -> None:
        self._red.add(test_id)
        self._persist(red={test_id})
        if self._ledger is not None:
            self._ledger.record("tdd", event="red", test=test_id)

    def record_green(self, test_id: str) -> None:
        self._green.add(test_id)
        self._persist(green={test_id})
        if self._ledger is not None:
            self._ledger.record("tdd", event="green", test=test_id)

    def allow_implementation(self, test_id: str) -> bool:
        self._refresh()                  # the persisted state is the truth; memory is its cache
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


__all__ = ["GATE_ID", "RedBeforeGreenError", "TddGuard",
           "PHASE_UNSET", "PHASE_RED", "PHASE_GREEN"]
