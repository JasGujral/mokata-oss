"""C4 — pluggable storage backends: every one satisfies the SAME storage contract.

⚠ 0.0.18 stage 10 (lane D slice 1) removed `TestObsidianBackend`, and the CONTRACT MIXIN is the
reason that is a deletion rather than a reversal: it exists to be run against two or more
backends, and its five cases were the ONLY thing an `ObsidianBackend` test could assert once the
class was gone. What the removal must not do is quietly leave the mixin with a single
implementation and no one noticing — `test_the_contract_mixin_still_has_more_than_one_
implementation` below is the guard against that, because a shared contract exercised by exactly
one class has stopped being a contract."""

import os
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.memory import MemoryItem, PostgresBackend, SQLiteBackend


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


class TestInMemorySQLiteBackend(BackendContractMixin, unittest.TestCase):
    """The second implementation of the contract. `:memory:` is a genuinely different storage
    regime from the file-backed one — it holds a live connection where the file-backed backend
    opens per operation — so the mixin still discriminates rather than running twice over one
    code path."""

    def make_backend(self, root):
        return SQLiteBackend(":memory:")


class TestTheContractIsStillShared(unittest.TestCase):
    def test_the_contract_mixin_still_has_more_than_one_implementation(self):
        # ⚠ A contract test with ONE implementation is not a contract test; it is a unit test
        # wearing a mixin. The Obsidian adapter's removal took one of the two, so this asserts
        # what the mixin is FOR rather than trusting the file to keep looking right.
        implementations = [cls for cls in globals().values()
                           if isinstance(cls, type) and issubclass(cls, BackendContractMixin)
                           and cls is not BackendContractMixin]
        self.assertGreaterEqual(len(implementations), 2, implementations)

    def test_the_removed_adapter_is_not_importable_from_the_memory_package(self):
        with self.assertRaises(ImportError):
            from mokata.memory import ObsidianBackend  # noqa: F401
        self.assertIsNotNone(PostgresBackend)          # …and the survivors still are


if __name__ == "__main__":
    unittest.main()
