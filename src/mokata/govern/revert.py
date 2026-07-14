"""I5 — reversibility.

Every committed durable write records enough to undo it. `ReversibleStateStore` wraps the
state store: each write captures the prior value into a durable undo log, and `revert`
restores it (deleting the key if there was no prior value). Builds on the state store +
audit ledger; `gated_reversible_write` composes it with the WriteGate (I2) so a write is
both human-gated AND reversible.

MS.S6 (M-6) — the undo log is SHARED state, and it used to be read once into memory at
construction and blind-overwritten on every write: a read-modify-write whose gap spanned the whole
process lifetime. Two Claude Code windows therefore silently ATE each other's revert points — the
one thing reversibility exists to protect (P17). Two windows are closed here, both under ONE
sidecar lock (`oslock`, the shared MS.S1 primitive) held across a whole reversible operation:

  * the LOST UNDO ENTRY — the append now rides the locked `StateStore.update` RMW against the
    CURRENT persisted log, never a stale in-memory copy;
  * the LYING UNDO RECORD — capturing `before`, writing the target, and appending the record are
    now indivisible. Interleaved, they let two windows both record `before=X` while one had in fact
    already moved the key to X1, so reverting would restore X and silently destroy X1 — a revert
    that corrupts exactly what it exists to protect.

Lock order (deliberate, and why `ledger.record` sits OUTSIDE the critical section): the WriteGate
holds the LEDGER lock across its `commit()`, and `gated_reversible_write` commits through this
store — so the gated path is ledger → undo. Recording to the ledger while holding the undo lock
would create the reverse edge (undo → ledger) and deadlock the pair. The ledger write happens after
the lock is released; it is an audit record, not part of the atomic state transition.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, List, Optional

from ..atomicfile import lock_path_for
from ..oslock import file_lock
from ..errors import MokataError

UNDO_KEY = "undo_log"


class RevertError(MokataError):
    pass


@dataclass
class UndoRecord:
    target: str
    before: Any
    after: Any


class ReversibleStateStore:
    def __init__(self, store: Any, ledger: Any = None,
                 undo_key: str = UNDO_KEY) -> None:
        self.store = store
        self.ledger = ledger
        self.undo_key = undo_key
        data = store.read(undo_key)
        # A CACHE of the persisted log for readers, refreshed from the authoritative on-disk value
        # after every mutation. It is never the thing written back from (that was the M-6 bug).
        self._undo: List[dict] = list(data["records"]) if data else []

    def _op_lock(self):
        """The cross-process lock serialising one WHOLE reversible operation (capture `before` →
        write the target → append the undo record). It is a sidecar DISTINCT from the state store's
        own per-key lock, which `store.update`/`store.write` take INSIDE this critical section —
        nesting the same lock path would self-deadlock (an OS advisory lock is not reentrant across
        two open file descriptions). Ordering is always op → per-key, never the reverse."""
        return file_lock(lock_path_for(self.store.path(self.undo_key) + ".op"))

    @staticmethod
    def _records(cur: Any) -> List[dict]:
        return list((cur or {}).get("records") or []) if isinstance(cur, dict) else []

    def read(self, key: str) -> Any:
        return self.store.read(key)

    def write(self, key: str, value: Any) -> UndoRecord:
        with self._op_lock():
            before = self.store.read(key)
            self.store.write(key, value)
            record = {"target": key, "before": before, "after": value}
            merged = self.store.update(
                self.undo_key,
                lambda cur: {"records": self._records(cur) + [record]},
                default={"records": []})
            self._undo = self._records(merged)
        if self.ledger is not None:                  # outside the lock — see the module note
            self.ledger.record("reversible_write", target=key)
        return UndoRecord(key, before, value)

    def revert(self, key: Optional[str] = None) -> UndoRecord:
        """Revert the most recent write (optionally to a specific key).

        The pop is decided against the CURRENT persisted log under the lock, not a stale in-memory
        copy, so a sibling window's revert point can never be popped twice or lost. An empty log
        raises `RevertError` from inside the RMW, which writes nothing."""
        box: dict = {}

        def _mutator(cur: Any) -> dict:
            records = self._records(cur)
            idx = None
            for i in range(len(records) - 1, -1, -1):
                if key is None or records[i]["target"] == key:
                    idx = i
                    break
            if idx is None:
                raise RevertError(
                    f"nothing to revert{f' for {key}' if key else ''}")
            box["rec"] = records.pop(idx)
            return {"records": records}

        with self._op_lock():
            merged = self.store.update(self.undo_key, _mutator, default={"records": []})
            self._undo = self._records(merged)
            rec = box["rec"]
            if rec["before"] is None:
                self.store.delete(rec["target"])
            else:
                self.store.write(rec["target"], rec["before"])
        if self.ledger is not None:                  # outside the lock — see the module note
            self.ledger.record("revert", target=rec["target"])
        return UndoRecord(rec["target"], rec["before"], rec["after"])


def gated_reversible_write(gate: Any, store: ReversibleStateStore, request: Any,
                           value: Any, confirm: Optional[Callable[[str], bool]] = None,
                           assume_yes: bool = False):
    """Human-gate a write (WriteGate, I2) and, on approval, make it reversible (I5).
    Returns (WriteOutcome, UndoRecord|None)."""
    captured = {"rec": None}

    def commit() -> None:
        captured["rec"] = store.write(request.target, value)

    outcome = gate.submit(request, commit=commit, confirm=confirm,
                          assume_yes=assume_yes)
    return outcome, captured["rec"]
