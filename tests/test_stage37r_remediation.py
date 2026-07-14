"""Stage 37R — pre-finalize review remediation (H1–H4 + M1).

Both jsonschema states (no jsonschema imported here). Verifies:
- H1: a secret in an imported share file AND in a migrate source is HARD-BLOCKED (no write),
  and import/migrate now record a ledger entry per item.
- H4: a secret in a `remember` SUBJECT is blocked (not just value).
- H3: the `approve`/`confirm` params. SI.3 (0.0.13) DEMOTED both on the MCP write tools — they are
  still ACCEPTED (schema stability) but they no longer commit anything, because they are parameters
  the MODEL types and a gate the gated party can open is not a gate. The consent now has to be
  MINTED BY A HUMAN out-of-band (`mokata approve <id>`, see `mokata/approval.py`). H3's original
  claim survives where it is still true: `record_finish_decision` (an in-process engine call, not a
  model-facing tool) keeps the same spelling + alias.
- M1: Neo4j degrade now flows through the typed `Neo4jUnavailable` path.
"""

import os
import sys
import tempfile
import types
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)
from _support import mcp_commit   # SI.3: propose -> a HUMAN approves out-of-band -> redeem by id

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
    # Build a share file containing UNTRUSTED items (incl. secrets) to simulate a teammate's
    # external file. (Since M2, store.remember itself blocks secrets, so we can't plant a secret
    # through it.)
    #
    # SI.6 (74 C2): this used to construct the file by planting the items into a backend and calling
    # `export_memory` — which worked only because export was a SCANNING HOLE. Export now hard-blocks
    # a secret-bearing item, so mokata can no longer PRODUCE such a file (that is the fix, and
    # `test_si_6_c2_*` pins it). The untrusted file is therefore built directly here, which is what
    # it always really was: a file from OUTSIDE this mokata — hand-rolled, hostile, or written by a
    # pre-SI.6 version. The import-side hard-block these tests assert is unchanged and still the
    # thing under test.
    from mokata.memory import SHARE_KIND, SHARE_SCHEMA_VERSION
    return {"schema_version": SHARE_SCHEMA_VERSION, "kind": SHARE_KIND,
            "items": [i.to_dict() for i in items]}


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
    # These drive the FULL human round-trip (mcp_commit) on purpose: the secret must be hard-blocked
    # AFTER a real human approval. A security block is not a methodology gate — no approval, however
    # legitimately minted, can license writing a credential. See SI.3 / src/mokata/approval.py.
    def test_secret_in_subject_is_blocked(self):
        from mokata import mcp_server as M
        with tempfile.TemporaryDirectory() as d:
            _repo(d).close()
            res = mcp_commit(M.remember, path=d, subject=f"creds-{SECRET}", value="ok")
            self.assertEqual(res["status"], "blocked")
            self.assertEqual(MemoryStore.from_surface(Surface.load(d)).all_active(), [])

    def test_secret_in_value_still_blocked(self):
        from mokata import mcp_server as M
        with tempfile.TemporaryDirectory() as d:
            _repo(d).close()
            res = mcp_commit(M.remember, path=d, subject="ok", value=f"key {SECRET}")
            self.assertEqual(res["status"], "blocked")


# ----------------------------------------------------------------- H3: approve param + alias
#
# SI.3 (0.0.13) rewrote what these two tests pin. H3 originally established `approve` as THE MCP gate
# boolean (with `confirm` as its alias) — and that was the bug: `approve` is a parameter the MODEL
# types, so "every durable write is human-gated" (P2) reduced to *the model said it was approved*.
# Both flags are now DEMOTED: still ACCEPTED (schema stability — an older caller must not blow up),
# but they commit NOTHING. The consent moved out-of-process, to an approval a HUMAN mints with
# `mokata approve <id>` and the model may only REFERENCE by id. See src/mokata/approval.py.

class TestApproveParam(unittest.TestCase):
    def test_approve_param_no_longer_commits(self):
        """`approve=True` alone must NOT commit (SI.3) — only the human round-trip does."""
        from mokata import mcp_server as M
        with tempfile.TemporaryDirectory() as d:
            _repo(d).close()
            # no consent param at all -> proposed, nothing written
            res = M.remember(path=d, subject="a", value="1")
            self.assertEqual(res["status"], "proposed")
            self.assertEqual(MemoryStore.from_surface(Surface.load(d)).all_active(), [])

            # approve=True -> STILL only a proposal. It is accepted, it is not consent: it hands
            # back a proposal_id, sets committed=False, and writes nothing.
            res = M.remember(path=d, subject="a", value="1", approve=True)
            self.assertEqual(res["status"], "proposed")
            self.assertFalse(res["committed"])
            self.assertTrue(res["proposal_id"])
            self.assertEqual(MemoryStore.from_surface(Surface.load(d)).all_active(), [])

            # the FULL round-trip — propose, a HUMAN mints the approval out-of-band, the model
            # redeems it by id — is the only thing that commits.
            self.assertEqual(
                mcp_commit(M.remember, path=d, subject="a", value="1")["status"], "committed")
            self.assertEqual([i.value for i in
                              MemoryStore.from_surface(Surface.load(d)).recall("a")], ["1"])

    def test_confirm_alias_is_accepted_but_does_not_commit(self):
        """The deprecated `confirm` alias stays ACCEPTED (no TypeError — older callers keep working)
        but, like `approve`, it commits nothing (SI.3 / src/mokata/approval.py)."""
        from mokata import mcp_server as M
        with tempfile.TemporaryDirectory() as d:
            _repo(d).close()
            res = M.remember(path=d, subject="b", value="2", confirm=True)   # accepted: no TypeError
            self.assertEqual(res["status"], "proposed")                      # but NOT a commit
            self.assertFalse(res["committed"])
            self.assertEqual(MemoryStore.from_surface(Surface.load(d)).all_active(), [])

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
