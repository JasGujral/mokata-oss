"""C4 — pluggable storage backends. SQLite (default) and an Obsidian markdown adapter
must satisfy the SAME storage contract (storage only; logic lives in the store)."""

import os
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.memory import MemoryItem, ObsidianBackend, SQLiteBackend


class BackendContractMixin:
    """Run the identical contract against whichever backend `make_backend` returns."""

    def make_backend(self, root):  # pragma: no cover - overridden
        raise NotImplementedError

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.backend = self.make_backend(self.tmp.name)

    def tearDown(self):
        self.backend.close()
        self.tmp.cleanup()

    def test_put_get(self):
        it = MemoryItem.create("db.engine", "postgres")
        self.backend.put(it)
        got = self.backend.get(it.id)
        self.assertIsNotNone(got)
        self.assertEqual(got.value, "postgres")
        self.assertEqual(got.subject, "db.engine")

    def test_all_filters_by_type_and_status(self):
        self.backend.put(MemoryItem.create("a", "1", mtype="persistent"))
        self.backend.put(MemoryItem.create("b", "2", mtype="decision"))
        sup = MemoryItem.create("c", "3", mtype="persistent")
        sup.status = "superseded"
        self.backend.put(sup)
        self.assertEqual(len(self.backend.all()), 3)
        self.assertEqual(len(self.backend.all(mtype="persistent")), 2)
        self.assertEqual(len(self.backend.all(statuses=("active",))), 2)

    def test_update_is_upsert(self):
        it = MemoryItem.create("k", "v")
        self.backend.put(it)
        it.status = "stale"
        self.backend.update(it)
        self.assertEqual(self.backend.get(it.id).status, "stale")
        self.assertEqual(len(self.backend.all()), 1)

    def test_delete(self):
        it = MemoryItem.create("k", "v")
        self.backend.put(it)
        self.assertTrue(self.backend.delete(it.id))
        self.assertIsNone(self.backend.get(it.id))

    def test_edges_survive_storage(self):
        it = MemoryItem.create("k", "v", supersedes=["x"], depends_on=["y", "z"])
        self.backend.put(it)
        got = self.backend.get(it.id)
        self.assertEqual(got.supersedes, ["x"])
        self.assertEqual(got.depends_on, ["y", "z"])


class TestSQLiteBackend(BackendContractMixin, unittest.TestCase):
    def make_backend(self, root):
        return SQLiteBackend(os.path.join(root, "memory.db"))

    def test_persists_across_reopen(self):
        # a new backend over the same file = a new session
        it = MemoryItem.create("db.engine", "postgres")
        self.backend.put(it)
        self.backend.close()
        reopened = SQLiteBackend(os.path.join(self.tmp.name, "memory.db"))
        self.assertEqual(reopened.get(it.id).value, "postgres")
        reopened.close()


class TestObsidianBackend(BackendContractMixin, unittest.TestCase):
    def make_backend(self, root):
        return ObsidianBackend(os.path.join(root, "vault"))

    def test_writes_markdown_notes(self):
        it = MemoryItem.create("db.engine", "postgres")
        self.backend.put(it)
        files = os.listdir(os.path.join(self.tmp.name, "vault"))
        self.assertTrue(any(f.endswith(".md") for f in files))


if __name__ == "__main__":
    unittest.main()
