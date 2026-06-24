"""C1/C5 — the memory item: provenance, TTL/valid_for, supersedes/depends_on edges."""

import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.memory import DECISION, PERSISTENT, MemoryItem


class TestMemoryItem(unittest.TestCase):
    def test_create_sets_provenance_and_id(self):
        it = MemoryItem.create("db.engine", "postgres", author="jas")
        self.assertTrue(it.id)
        self.assertEqual(it.mtype, PERSISTENT)
        self.assertEqual(it.provenance["author"], "jas")
        self.assertIn("created_at", it.provenance)
        self.assertEqual(it.status, "active")

    def test_valid_for_computes_expiry(self):
        it = MemoryItem.create("x", "y", created_at="2026-01-01T00:00:00+00:00",
                               valid_for=3600)
        self.assertEqual(it.expires_at, "2026-01-01T01:00:00+00:00")

    def test_edges_default_empty_and_settable(self):
        it = MemoryItem.create("x", "y", supersedes=["a"], depends_on=["b", "c"])
        self.assertEqual(it.supersedes, ["a"])
        self.assertEqual(it.depends_on, ["b", "c"])

    def test_roundtrips_through_dict(self):
        it = MemoryItem.create("api.style", "REST", mtype=DECISION,
                               valid_for=10, depends_on=["z"])
        again = MemoryItem.from_dict(it.to_dict())
        self.assertEqual(again.to_dict(), it.to_dict())
        self.assertEqual(again.mtype, DECISION)


if __name__ == "__main__":
    unittest.main()
