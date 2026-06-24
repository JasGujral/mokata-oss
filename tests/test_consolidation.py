"""C7 — consolidation pass: PROPOSES merges/prunes/summaries and NEVER auto-applies.
Surfaces each as an old->new diff for approve/edit/reject (like C5); default no change;
proposals + decisions logged to the ledger."""

import os
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.govern import AuditLedger
from mokata.memory import MemoryItem, MemoryStore, SQLiteBackend


def store_with_dupes(d):
    store = MemoryStore(SQLiteBackend(os.path.join(d, "m.db")))
    # two identical active facts (a duplicate to merge)
    store.backend.put(MemoryItem.create("db.engine", "postgres",
                                        created_at="2026-01-01T00:00:00+00:00"))
    store.backend.put(MemoryItem.create("db.engine", "postgres",
                                        created_at="2026-02-01T00:00:00+00:00"))
    return store


class TestConsolidationProposalOnly(unittest.TestCase):
    def test_proposes_a_merge(self):
        with tempfile.TemporaryDirectory() as d:
            store = store_with_dupes(d)
            props = store.propose_consolidations()
            merges = [p for p in props if p.kind == "merge"]
            self.assertEqual(len(merges), 1)
            self.assertEqual(len(merges[0].olds), 2)
            self.assertIn("->", store.render_consolidation(merges[0]))

    def test_proposing_changes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            store = store_with_dupes(d)
            before = len(store.backend.all(statuses=("active",)))
            store.propose_consolidations()
            after = len(store.backend.all(statuses=("active",)))
            self.assertEqual(before, after)        # propose-only: no write

    def test_reject_changes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            store = store_with_dupes(d)
            p = store.propose_consolidations()[0]
            store.apply_consolidation(p, "reject")
            self.assertEqual(len(store.backend.all(statuses=("active",))), 2)

    def test_approve_applies_the_merge(self):
        with tempfile.TemporaryDirectory() as d:
            store = store_with_dupes(d)
            p = [x for x in store.propose_consolidations() if x.kind == "merge"][0]
            res = store.apply_consolidation(p, "approve", assume_yes=True)
            self.assertTrue(res.changed)
            active = store.backend.all(statuses=("active",))
            self.assertEqual(len(active), 1)       # duplicates merged into one

    def test_gate_blocks_apply_without_approval(self):
        with tempfile.TemporaryDirectory() as d:
            store = store_with_dupes(d)
            p = store.propose_consolidations()[0]
            res = store.apply_consolidation(p, "approve", confirm=lambda _t: False)
            self.assertFalse(res.changed)
            self.assertEqual(len(store.backend.all(statuses=("active",))), 2)

    def test_proposals_and_decisions_are_logged(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            store = store_with_dupes(d)
            p = store.propose_consolidations(ledger=led)[0]
            store.apply_consolidation(p, "approve", assume_yes=True, ledger=led)
            kinds = [e["kind"] for e in led.entries()]
            self.assertIn("consolidation_proposal", kinds)
            self.assertIn("consolidation_decision", kinds)


if __name__ == "__main__":
    unittest.main()
