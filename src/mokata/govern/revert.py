"""I5 — reversibility.

Every committed durable write records enough to undo it. `ReversibleStateStore` wraps the
state store: each write captures the prior value into a durable undo log, and `revert`
restores it (deleting the key if there was no prior value). Builds on the state store +
audit ledger; `gated_reversible_write` composes it with the WriteGate (I2) so a write is
both human-gated AND reversible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, List, Optional

UNDO_KEY = "undo_log"


class RevertError(Exception):
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
        self._undo: List[dict] = data["records"] if data else []

    def _save_undo(self) -> None:
        self.store.write(self.undo_key, {"records": self._undo})

    def read(self, key: str) -> Any:
        return self.store.read(key)

    def write(self, key: str, value: Any) -> UndoRecord:
        before = self.store.read(key)
        self.store.write(key, value)
        self._undo.append({"target": key, "before": before, "after": value})
        self._save_undo()
        if self.ledger is not None:
            self.ledger.record("reversible_write", target=key)
        return UndoRecord(key, before, value)

    def revert(self, key: Optional[str] = None) -> UndoRecord:
        """Revert the most recent write (optionally to a specific key)."""
        idx = None
        for i in range(len(self._undo) - 1, -1, -1):
            if key is None or self._undo[i]["target"] == key:
                idx = i
                break
        if idx is None:
            raise RevertError(
                f"nothing to revert{f' for {key}' if key else ''}")
        rec = self._undo.pop(idx)
        self._save_undo()
        if rec["before"] is None:
            self.store.delete(rec["target"])
        else:
            self.store.write(rec["target"], rec["before"])
        if self.ledger is not None:
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
