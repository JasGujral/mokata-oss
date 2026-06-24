"""C5 — self-healing, SURFACING form only: detect contradictions/staleness, present an
old->new diff, and let the user approve / edit / reject. NEVER auto-rewrite; default to
no change. (Autonomous consolidation C7 is out of scope.)"""

import os
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.memory import MemoryItem, MemoryStore, SQLiteBackend


def store_with_two_facts(d):
    store = MemoryStore(SQLiteBackend(os.path.join(d, "m.db")))
    store.remember(MemoryItem.create("db.engine", "postgres",
                                     created_at="2026-01-01T00:00:00+00:00"),
                   assume_yes=True)
    store.remember(MemoryItem.create("db.engine", "mysql",
                                     created_at="2026-02-01T00:00:00+00:00"),
                   assume_yes=True)
    return store


class TestContradictionDetection(unittest.TestCase):
    def test_contradiction_is_detected_and_presented_as_old_new_diff(self):
        with tempfile.TemporaryDirectory() as d:
            store = store_with_two_facts(d)
            issues = store.detect_issues()
            contradictions = [p for p in issues if p.kind == "contradiction"]
            self.assertEqual(len(contradictions), 1)
            p = contradictions[0]
            self.assertEqual(p.old.value, "postgres")   # older
            self.assertEqual(p.new.value, "mysql")       # newer
            text = store.render_proposal(p)
            self.assertIn("postgres", text)
            self.assertIn("mysql", text)
            self.assertIn("->", text)                    # an old -> new diff

    def test_detection_writes_nothing_and_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            store = store_with_two_facts(d)
            store.detect_issues()
            # both contradictory facts remain active — nothing was silently rewritten
            self.assertEqual(
                sorted(i.value for i in store.all_active("persistent")),
                ["mysql", "postgres"],
            )


class TestSurfacingResolution(unittest.TestCase):
    def test_reject_changes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            store = store_with_two_facts(d)
            p = [x for x in store.detect_issues() if x.kind == "contradiction"][0]
            res = store.apply_proposal(p, "reject")
            self.assertFalse(res.changed)
            self.assertEqual(len(store.all_active("persistent")), 2)

    def test_default_no_action_changes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            store = store_with_two_facts(d)
            p = [x for x in store.detect_issues() if x.kind == "contradiction"][0]
            res = store.apply_proposal(p, "defer")
            self.assertFalse(res.changed)
            self.assertEqual(len(store.all_active("persistent")), 2)

    def test_gate_blocks_apply_without_approval(self):
        with tempfile.TemporaryDirectory() as d:
            store = store_with_two_facts(d)
            p = [x for x in store.detect_issues() if x.kind == "contradiction"][0]
            res = store.apply_proposal(p, "approve", confirm=lambda _t: False)
            self.assertFalse(res.changed)
            self.assertEqual(len(store.all_active("persistent")), 2)

    def test_approve_supersedes_old_keeps_new(self):
        with tempfile.TemporaryDirectory() as d:
            store = store_with_two_facts(d)
            p = [x for x in store.detect_issues() if x.kind == "contradiction"][0]
            res = store.apply_proposal(p, "approve", assume_yes=True)
            self.assertTrue(res.changed)
            active = store.all_active("persistent")
            self.assertEqual([i.value for i in active], ["mysql"])
            # the supersedes edge is recorded
            self.assertIn(p.old.id, store.get(p.new.id).supersedes)

    def test_edit_supersedes_old_with_edited_value(self):
        with tempfile.TemporaryDirectory() as d:
            store = store_with_two_facts(d)
            p = [x for x in store.detect_issues() if x.kind == "contradiction"][0]
            edited = MemoryItem.create("db.engine", "postgres 16")
            res = store.apply_proposal(p, "edit", edited=edited, assume_yes=True)
            self.assertTrue(res.changed)
            self.assertEqual([i.value for i in store.all_active("persistent")],
                             ["postgres 16"])


class TestStalenessDetection(unittest.TestCase):
    def test_expired_fact_is_surfaced_not_removed(self):
        with tempfile.TemporaryDirectory() as d:
            store = MemoryStore(SQLiteBackend(os.path.join(d, "m.db")))
            store.remember(
                MemoryItem.create("token", "abc",
                                  created_at="2020-01-01T00:00:00+00:00",
                                  expires_at="2020-01-02T00:00:00+00:00"),
                assume_yes=True)
            issues = store.detect_issues(now="2026-01-01T00:00:00+00:00")
            stale = [p for p in issues if p.kind == "stale"]
            self.assertEqual(len(stale), 1)
            # still active until the user acts
            self.assertEqual(len(store.all_active()), 1)
            store.apply_proposal(stale[0], "approve", assume_yes=True)
            self.assertEqual(len(store.all_active()), 0)   # now marked stale


if __name__ == "__main__":
    unittest.main()
