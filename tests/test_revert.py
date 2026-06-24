"""I5 — reversibility: every committed durable write is revertible; `revert` restores
the prior state. Built on the state store + ledger (+ WriteGate for the gated path)."""

import os
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.govern import (
    AuditLedger,
    RevertError,
    ReversibleStateStore,
    WriteGate,
    WriteRequest,
    gated_reversible_write,
)
from mokata.state import StateStore


def rss(d):
    return ReversibleStateStore(StateStore(os.path.join(d, "state")),
                                ledger=AuditLedger(os.path.join(d, "l.jsonl")))


class TestReversibility(unittest.TestCase):
    def test_revert_restores_prior_state(self):
        with tempfile.TemporaryDirectory() as d:
            store = rss(d)
            store.write("db.engine", {"value": "postgres"})
            store.write("db.engine", {"value": "mysql"})
            self.assertEqual(store.read("db.engine"), {"value": "mysql"})
            store.revert("db.engine")
            self.assertEqual(store.read("db.engine"), {"value": "postgres"})

    def test_revert_first_write_removes_the_key(self):
        with tempfile.TemporaryDirectory() as d:
            store = rss(d)
            store.write("new.key", {"value": 1})
            store.revert("new.key")
            self.assertIsNone(store.read("new.key"))

    def test_revert_with_nothing_raises(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RevertError):
                rss(d).revert("missing")

    def test_undo_log_is_durable(self):
        with tempfile.TemporaryDirectory() as d:
            base = StateStore(os.path.join(d, "state"))
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            ReversibleStateStore(base, ledger=led).write("k", {"value": "a"})
            # a fresh wrapper over the same store sees the undo log + can revert
            reopened = ReversibleStateStore(base, ledger=led)
            reopened.write("k", {"value": "b"})
            reopened.revert("k")
            self.assertEqual(base.read("k"), {"value": "a"})

    def test_revert_is_audited(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            store = ReversibleStateStore(StateStore(os.path.join(d, "state")),
                                         ledger=led)
            store.write("k", {"value": 1})
            store.write("k", {"value": 2})
            store.revert("k")
            kinds = [e["kind"] for e in led.entries()]
            self.assertIn("reversible_write", kinds)
            self.assertIn("revert", kinds)


class TestGatedReversibleWrite(unittest.TestCase):
    def test_decline_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            store = rss(d)
            gate = WriteGate()
            out, rec = gated_reversible_write(
                gate, store, WriteRequest("config", "k", '{"value": 1}'),
                {"value": 1}, confirm=lambda _t: False)
            self.assertFalse(out.committed)
            self.assertIsNone(rec)
            self.assertIsNone(store.read("k"))

    def test_approve_writes_and_is_revertible(self):
        with tempfile.TemporaryDirectory() as d:
            store = rss(d)
            gate = WriteGate()
            out, rec = gated_reversible_write(
                gate, store, WriteRequest("config", "k", '{"value": 1}'),
                {"value": 1}, assume_yes=True)
            self.assertTrue(out.committed)
            self.assertEqual(store.read("k"), {"value": 1})
            store.revert("k")
            self.assertIsNone(store.read("k"))


if __name__ == "__main__":
    unittest.main()
