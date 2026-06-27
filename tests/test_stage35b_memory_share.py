"""Stage 35b — `mokata memory export/import` (file share, provenance, gated healing import).

Both jsonschema states. Export writes a committable artifact (outside temp_local/) with
provenance and leaves the source unchanged; import is human-gated, dedups, and routes a
conflict through the self-healing old->new surface (no silent overwrite); the MCP variants
are propose-only without confirm; an A->B round-trip preserves items + provenance.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

import _support  # noqa: F401  (puts src/ on the path)

from mokata.cli import main
from mokata.config import Surface
from mokata.init import init_repo
from mokata.memory import (
    ACTIVE,
    MEMORY_SHARE_FILENAME,
    MemoryItem,
    MemoryStore,
    export_memory,
    import_memory,
    load_memory_share,
)


def _silent(_):
    pass


def _repo(d, profile="standard"):
    init_repo(root=d, profile=profile, assume_yes=True, out=_silent)
    return MemoryStore.from_surface(Surface.load(d))


# --------------------------------------------------------------------------- export

class TestExport(unittest.TestCase):
    def test_export_writes_committable_artifact_with_provenance(self):
        with tempfile.TemporaryDirectory() as d:
            store = _repo(d)
            store.remember(MemoryItem.create("api style", "REST", source="alice",
                                             author="alice"), assume_yes=True)
            dest = os.path.join(d, ".mokata", MEMORY_SHARE_FILENAME)
            data = export_memory(store, dest=dest)

            self.assertTrue(os.path.exists(dest))
            self.assertNotIn("temp_local", dest)            # committable, not gitignored
            self.assertEqual(data["kind"], "mokata-memory-share")
            self.assertEqual(len(data["items"]), 1)
            self.assertEqual(data["items"][0]["provenance"]["author"], "alice")

    def test_export_is_read_only_on_source(self):
        with tempfile.TemporaryDirectory() as d:
            store = _repo(d)
            store.remember(MemoryItem.create("x", "1"), assume_yes=True)
            before = [(i.id, i.value, i.status) for i in store.backend.all()]
            export_memory(store, dest=os.path.join(d, ".mokata", MEMORY_SHARE_FILENAME))
            after = [(i.id, i.value, i.status) for i in store.backend.all()]
            self.assertEqual(before, after)                 # source untouched

    def test_cli_export_default_path_under_mokata_root(self):
        with tempfile.TemporaryDirectory() as d:
            store = _repo(d)
            store.remember(MemoryItem.create("y", "2"), assume_yes=True)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["memory", "export", "--path", d])
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.exists(
                os.path.join(d, ".mokata", MEMORY_SHARE_FILENAME)))


# --------------------------------------------------------------------------- import

class TestImport(unittest.TestCase):
    def _share(self, items):
        return {"schema_version": 1, "kind": "mokata-memory-share",
                "items": [i.to_dict() for i in items]}

    def test_import_is_human_gated(self):
        with tempfile.TemporaryDirectory() as d:
            store = _repo(d)
            share = self._share([MemoryItem.create("decision", "ship", source="bob")])
            res = import_memory(store, share, confirm=lambda _t: False)  # declined
            self.assertEqual(res.added, [])
            self.assertIn("decision", res.declined)
            self.assertEqual(store.all_active(), [])         # nothing merged

    def test_import_adds_new_with_provenance(self):
        with tempfile.TemporaryDirectory() as d:
            store = _repo(d)
            share = self._share([MemoryItem.create("decision", "ship", source="bob",
                                                   author="bob")])
            res = import_memory(store, share, assume_yes=True)
            self.assertEqual(res.added, ["decision"])
            got = store.recall("decision")
            self.assertEqual(got[0].value, "ship")
            self.assertEqual(got[0].provenance.get("author"), "bob")  # provenance kept

    def test_import_dedups(self):
        with tempfile.TemporaryDirectory() as d:
            store = _repo(d)
            item = MemoryItem.create("conv", "tabs")
            share = self._share([item])
            import_memory(store, share, assume_yes=True)
            res = import_memory(store, share, assume_yes=True)   # same ids again
            self.assertEqual(res.added, [])
            self.assertEqual(res.skipped, ["conv"])             # dedup by id

    def test_conflict_routes_through_healing_no_silent_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            store = _repo(d)
            store.remember(MemoryItem.create("database", "postgres"), assume_yes=True)
            incoming = MemoryItem.create("database", "mysql", source="bob")
            share = self._share([incoming])

            # the gate is shown the old->new healing diff; declining changes nothing
            seen = {}

            def _decline(text):
                seen["text"] = text
                return False
            res = import_memory(store, share, confirm=_decline)
            self.assertIn("database", res.declined)
            self.assertIn("postgres", seen["text"])           # old->new surface shown
            self.assertIn("mysql", seen["text"])
            active = [i.value for i in store.backend.all(statuses=(ACTIVE,))]
            self.assertEqual(active, ["postgres"])            # NO silent overwrite

            # approving heals it: old superseded, new active
            res2 = import_memory(store, share, assume_yes=True)
            self.assertIn("database", res2.resolved)
            active2 = sorted(i.value for i in store.backend.all(statuses=(ACTIVE,)))
            self.assertEqual(active2, ["mysql"])

    def test_import_rejects_non_share_file(self):
        with tempfile.TemporaryDirectory() as d:
            store = _repo(d)
            res = import_memory(store, {"not": "a share"})
            self.assertTrue(res.aborted)
            self.assertTrue(res.errors)


# --------------------------------------------------------------- A -> B round-trip

class TestRoundTripAcrossRepos(unittest.TestCase):
    def test_export_A_import_B_preserves_items_and_provenance(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            store_a = _repo(a)
            store_a.remember(MemoryItem.create("auth", "jwt", source="alice",
                                               author="alice"), assume_yes=True)
            store_a.remember(MemoryItem.create("db", "postgres", source="alice"),
                             assume_yes=True)
            share = os.path.join(a, ".mokata", MEMORY_SHARE_FILENAME)
            export_memory(store_a, dest=share)

            store_b = _repo(b)
            res = import_memory(store_b, load_memory_share(share), assume_yes=True)
            self.assertEqual(sorted(res.added), ["auth", "db"])
            vals = {i.subject: i.value for i in store_b.all_active()}
            self.assertEqual(vals, {"auth": "jwt", "db": "postgres"})
            auth = store_b.recall("auth")[0]
            self.assertEqual(auth.provenance.get("author"), "alice")  # provenance crossed


# --------------------------------------------------------------- MCP propose-only

class TestMcpMemoryShare(unittest.TestCase):
    def setUp(self):
        from mokata import mcp_server
        self.M = mcp_server

    def test_registered_as_write_tools(self):
        self.assertIn("memory_export", self.M.write_tool_names())
        self.assertIn("memory_import", self.M.write_tool_names())

    def test_export_propose_only_without_confirm(self):
        with tempfile.TemporaryDirectory() as d:
            store = _repo(d)
            store.remember(MemoryItem.create("z", "1"), assume_yes=True)
            res = self.M.memory_export(path=d)               # no confirm
            self.assertEqual(res["status"], "proposed")
            self.assertFalse(os.path.exists(
                os.path.join(d, ".mokata", MEMORY_SHARE_FILENAME)))   # nothing written
            res2 = self.M.memory_export(path=d, confirm=True)
            self.assertTrue(res2["committed"])
            self.assertTrue(os.path.exists(
                os.path.join(d, ".mokata", MEMORY_SHARE_FILENAME)))

    def test_import_propose_only_without_confirm(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            sa = _repo(a)
            sa.remember(MemoryItem.create("w", "1", source="alice"), assume_yes=True)
            share = os.path.join(a, ".mokata", MEMORY_SHARE_FILENAME)
            export_memory(sa, dest=share)
            _repo(b)
            res = self.M.memory_import(path=b, file=share)   # no confirm
            self.assertEqual(res["status"], "proposed")
            self.assertEqual(res["incoming"], 1)
            self.assertEqual(Surface.load(b) and
                             MemoryStore.from_surface(Surface.load(b)).all_active(), [])
            res2 = self.M.memory_import(path=b, file=share, confirm=True)
            self.assertTrue(res2["committed"])
            self.assertIn("w", res2["added"])


if __name__ == "__main__":
    unittest.main()
