"""Stage 37R — pre-finalize review remediation (H1–H4 + M1).

Both jsonschema states (no jsonschema imported here). Verifies:
- H1: a secret in an imported share file AND in a migrate source is HARD-BLOCKED (no write),
  and import/migrate now record a ledger entry per item.
- H4: a secret in a `remember` SUBJECT is blocked (not just value).
- H3: the MCP gate boolean is `approve` (with `confirm` kept as a back-compat alias);
  `record_finish_decision` uses the same spelling.
- M1: Neo4j degrade now flows through the typed `Neo4jUnavailable` path.
"""

import os
import sys
import tempfile
import types
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata.config import Surface
from mokata.govern import AuditLedger
from mokata.init import init_repo
from mokata.memory import (
    MemoryItem,
    MemoryStore,
    import_memory,
    migrate_memory,
)

# A credential the secret-scanner blocks (AWS access key id: AKIA + 16 alnum).
SECRET = "AKIAIOSFODNN7EXAMPLE"


def _silent(_):
    pass


def _repo(d, profile="full"):
    init_repo(root=d, profile=profile, assume_yes=True, out=_silent)
    return MemoryStore.from_surface(Surface.load(d))


def _share(items):
    # Build a share file containing UNTRUSTED items (incl. secrets) by planting them straight
    # into the backend — bypassing the gate — to simulate a teammate's external file. (Since
    # M2, store.remember itself blocks secrets, so we can't plant a secret through it.)
    from mokata.memory import SQLiteBackend, export_memory
    with tempfile.TemporaryDirectory() as t:
        s = MemoryStore(SQLiteBackend(os.path.join(t, "m.db")))
        for it in items:
            s.backend.put(it)
        data = export_memory(s)
        s.close()
        return data


# ----------------------------------------------------------------- H1: import secret-scan + ledger

class TestImportSecretScanAndLedger(unittest.TestCase):
    def test_secret_in_imported_item_is_hard_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            store = _repo(d)
            ledger = AuditLedger(os.path.join(d, "ledger.jsonl"))
            share = _share([
                MemoryItem.create("clean.fact", "nothing sensitive here"),
                MemoryItem.create("leaked.key", f"the prod key is {SECRET}"),
            ])
            res = import_memory(store, share, assume_yes=True, ledger=ledger)
            self.assertIn("clean.fact", res.added)
            self.assertIn("leaked.key", res.blocked)          # hard-blocked
            # the secret was NOT written
            values = [i.value for i in store.backend.all()]
            self.assertFalse(any(SECRET in v for v in values))
            # a ledger entry was recorded per item (approved + blocked)
            decisions = {(e.get("target"), e.get("decision"))
                         for e in ledger.entries() if e["kind"] == "write_gate"}
            self.assertIn(("memory:clean.fact", "approved"), decisions)
            self.assertIn(("memory:leaked.key", "blocked"), decisions)

    def test_secret_in_subject_is_blocked_too(self):
        with tempfile.TemporaryDirectory() as d:
            store = _repo(d)
            share = _share([MemoryItem.create(f"key-{SECRET}", "value")])
            res = import_memory(store, share, assume_yes=True)
            self.assertEqual(res.added, [])
            self.assertEqual(len(res.blocked), 1)

    def test_healing_surface_preserved_on_conflict(self):
        # the old->new diff is still shown to the human gate (behavior kept intact)
        with tempfile.TemporaryDirectory() as d:
            store = _repo(d)
            store.remember(MemoryItem.create("db", "postgres"), assume_yes=True)
            share = _share([MemoryItem.create("db", "mysql", source="bob")])
            seen = {}

            def _decline(text):
                seen["text"] = text
                return False
            res = import_memory(store, share, confirm=_decline)
            self.assertIn("db", res.declined)
            self.assertIn("postgres", seen["text"])
            self.assertIn("mysql", seen["text"])


# ----------------------------------------------------------------- H1: migrate secret-scan + ledger

class TestMigrateSecretScanAndLedger(unittest.TestCase):
    def test_secret_in_migrate_source_is_blocked(self):
        from mokata.memory import build_named_backend
        with tempfile.TemporaryDirectory() as d:
            store = _repo(d)               # sqlite source
            store.remember(MemoryItem.create("clean", "ok"), assume_yes=True)
            # plant the secret straight into the backend (bypassing the gate) to simulate an
            # external/pre-existing item — since M2, store.remember itself blocks secrets.
            store.backend.put(MemoryItem.create("leak", f"token {SECRET}"))
            store.close()
            surface = Surface.load(d)
            ledger = AuditLedger(os.path.join(d, "ledger.jsonl"))

            # migrate sqlite -> obsidian (distinct backends, no live external DB needed)
            res = migrate_memory(surface, to_backend="obsidian", from_backend="sqlite",
                                 assume_yes=True, ledger=ledger, out=_silent)
            self.assertEqual(res.migrated, 1)        # clean item only
            self.assertEqual(res.blocked, 1)         # secret hard-blocked

            # the secret never reached the destination
            dest = build_named_backend("obsidian", surface.mokata_dir, {})
            self.assertFalse(any(SECRET in i.value for i in dest.all()))

            # per-item ledger entries recorded (approved + blocked)
            decisions = {e.get("decision") for e in ledger.entries()
                         if e["kind"] == "write_gate"}
            self.assertIn("approved", decisions)
            self.assertIn("blocked", decisions)

    def test_drop_source_keeps_a_blocked_item(self):
        # a blocked item is NOT migrated, so --drop-source must not delete it from the source
        with tempfile.TemporaryDirectory() as d:
            store = _repo(d)
            store.remember(MemoryItem.create("clean", "ok"), assume_yes=True)
            # plant the secret straight into the backend (bypassing the gate) to simulate an
            # external/pre-existing item — since M2, store.remember itself blocks secrets.
            store.backend.put(MemoryItem.create("leak", f"token {SECRET}"))
            store.close()
            surface = Surface.load(d)
            res = migrate_memory(surface, to_backend="obsidian", from_backend="sqlite",
                                 assume_yes=True, drop_source=True, out=_silent)
            self.assertEqual(res.dropped, 1)          # only the migrated (clean) item dropped
            from mokata.memory import build_named_backend
            src = build_named_backend("sqlite", surface.mokata_dir, {})
            self.assertTrue(any(SECRET in i.value for i in src.all()))   # secret left intact


# ----------------------------------------------------------------- H4: remember scans subject

class TestRememberScansSubject(unittest.TestCase):
    def test_secret_in_subject_is_blocked(self):
        from mokata import mcp_server as M
        with tempfile.TemporaryDirectory() as d:
            _repo(d).close()
            res = M.remember(path=d, subject=f"creds-{SECRET}", value="ok", approve=True)
            self.assertEqual(res["status"], "blocked")
            self.assertEqual(MemoryStore.from_surface(Surface.load(d)).all_active(), [])

    def test_secret_in_value_still_blocked(self):
        from mokata import mcp_server as M
        with tempfile.TemporaryDirectory() as d:
            _repo(d).close()
            res = M.remember(path=d, subject="ok", value=f"key {SECRET}", approve=True)
            self.assertEqual(res["status"], "blocked")


# ----------------------------------------------------------------- H3: approve param + alias

class TestApproveParam(unittest.TestCase):
    def test_remember_gates_on_approve(self):
        from mokata import mcp_server as M
        with tempfile.TemporaryDirectory() as d:
            _repo(d).close()
            # no approve -> proposed, nothing written
            self.assertEqual(M.remember(path=d, subject="a", value="1")["status"],
                             "proposed")
            self.assertEqual(MemoryStore.from_surface(Surface.load(d)).all_active(), [])
            # approve=True -> committed
            self.assertEqual(
                M.remember(path=d, subject="a", value="1", approve=True)["status"],
                "committed")

    def test_confirm_alias_back_compat(self):
        from mokata import mcp_server as M
        with tempfile.TemporaryDirectory() as d:
            _repo(d).close()
            # the deprecated `confirm=True` alias still performs the gated write
            res = M.remember(path=d, subject="b", value="2", confirm=True)
            self.assertEqual(res["status"], "committed")

    def test_record_finish_decision_spelling(self):
        from mokata.engine import record_finish_decision
        dec = record_finish_decision(None, "keep", approve=True)
        self.assertTrue(dec.approved)
        # back-compat alias
        self.assertTrue(record_finish_decision(None, "keep", confirmed=True).approved)


# ----------------------------------------------------------------- M1: Neo4j typed degrade

class TestNeo4jTypedDegrade(unittest.TestCase):
    def test_unreachable_raises_typed_and_build_degrades(self):
        from mokata.knowledge import Neo4jGraphClient, Neo4jUnavailable, build_neo4j_client

        class _DownDriver:
            def verify_connectivity(self):
                raise RuntimeError("connection refused")

        # the client raises the TYPED signal when the DB is unreachable
        with self.assertRaises(Neo4jUnavailable):
            Neo4jGraphClient(_DownDriver())

        # build_neo4j_client catches the typed signal and degrades to None
        fake = types.ModuleType("neo4j")

        class _GDB:
            @staticmethod
            def driver(uri, auth=None):
                return _DownDriver()
        fake.GraphDatabase = _GDB
        with mock.patch.dict(os.environ, {"NEO4J_URI": "bolt://x"}), \
                mock.patch.dict(sys.modules, {"neo4j": fake}):
            self.assertIsNone(build_neo4j_client({}))

    def test_missing_driver_raises_typed_internally(self):
        from mokata.knowledge import build_neo4j_client
        with mock.patch.dict(os.environ, {"NEO4J_URI": "bolt://x"}), \
                mock.patch.dict(sys.modules, {"neo4j": None}):
            self.assertIsNone(build_neo4j_client({}))


if __name__ == "__main__":
    unittest.main()
