"""Stage 35b — memory EXPORT/IMPORT as the single gated BACKUP surface.

After SIMP, exactly ONE memory file surface survives: `mokata memory export` = backup (store →
an explicit human-owned FILE, gated egress) and `mokata memory import` = restore (file → store,
via WriteGate, provenance-stamped). It is a BACKUP surface, not a sharing channel.

Covers the 35b test bar:
  * default dest is a timestamped `.mokata/backups/` path (NOT the removed memory-share path);
  * the legacy `memory-share.json` path still works and warns about NOTHING (0.0.18: the
    channel it named was removed; the path is a path — see `TestLegacyDestIsNowJustAPath`);
  * restore goes through the WriteGate with item-level provenance + an `import_batch` ledger anchor;
  * preview (keys-only) / approve / decline / idempotent re-run;
  * secret-scan on ingest (seeded secret → blocked, named, not imported);
  * round trip: content-identical + scopes + approval-status fidelity (unapproved stays unapproved);
  * secret-safety: no secret VALUE in the preview, ledger records, warns, or errors.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import glob
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

import _support  # noqa: F401  (puts src/ on the path)

from mokata import MOKATA_DIR
from mokata.cli import main
from mokata.config import Surface
from mokata.govern.ledger import AuditLedger
from mokata.init import init_repo
from mokata.memory import (
    ACTIVE,
    MemoryItem,
    MemoryStore,
    SHARE_KIND,
    SHARE_SCHEMA_VERSION,
    default_backup_path,
    export_memory,
    import_memory,
    load_memory_share,
    plan_memory_import,
)


def _fake_secret() -> str:
    """The canonical AWS-docs example key, assembled at RUNTIME (mokata's own secret-guard hook
    blocks writing a secret literal into a file — so a secret-block test can't spell one out)."""
    return "AKIA" + "IOSFODNN7" + "EXAMPLE"


def _silent(*_a):
    pass


def _repo(d, profile="standard"):
    init_repo(root=d, profile=profile, assume_yes=True, out=_silent)
    return MemoryStore.from_surface(Surface.load(d))


def _share(items):
    return {"schema_version": SHARE_SCHEMA_VERSION, "kind": SHARE_KIND,
            "items": [i.to_dict() for i in items]}


# ============================================================ deliverable 2 — backup re-frame

class TestDefaultDestIsBackupPath(unittest.TestCase):
    def test_35b_default_dest_is_backup_path(self):
        # the library default AND the CLI default land under `.mokata/backups/memory-<UTC>.json`,
        # never the (now removed) memory-share.json path.
        self.assertIn(os.path.join(MOKATA_DIR, "backups"), default_backup_path("/repo"))
        self.assertTrue(default_backup_path("/repo").endswith(".json"))
        self.assertNotIn("memory-share.json", default_backup_path("/repo"))

        with tempfile.TemporaryDirectory() as d:
            store = _repo(d)
            store.remember(MemoryItem.create("y", "2"), assume_yes=True)
            with redirect_stdout(io.StringIO()):
                rc = main(["memory", "export", "--path", d])
            self.assertEqual(rc, 0)
            backups = glob.glob(os.path.join(d, MOKATA_DIR, "backups", "memory-*.json"))
            self.assertEqual(len(backups), 1)                        # one timestamped backup file
            self.assertFalse(os.path.exists(
                os.path.join(d, MOKATA_DIR, "memory-share.json")))    # NOT the removed channel

    def test_35b_default_dest_never_clobbers_prior_backup(self):
        with tempfile.TemporaryDirectory() as d:
            store = _repo(d)
            store.remember(MemoryItem.create("y", "2"), assume_yes=True)
            with redirect_stdout(io.StringIO()):
                main(["memory", "export", "--path", d])
                main(["memory", "export", "--path", d])
            backups = glob.glob(os.path.join(d, MOKATA_DIR, "backups", "memory-*.json"))
            self.assertEqual(len(backups), 2)                        # distinct UTC-stamped files


class TestLegacyDestIsNowJustAPath(unittest.TestCase):
    """⚠ REVERSED AT 0.0.18 LANE D SLICE 2, IN PLACE, RATHER THAN DELETED.

    This class asserted that exporting to `memory-share.json` still worked and earned ONE
    deprecation warn naming the removal release. The channel is now REMOVED, so both halves of
    that claim are false and the honest move is to state what is true instead: the path lost its
    special meaning, not its usability. Deleting the class would have left the tree with no record
    that the filename was ever special and nothing asserting it stopped being so — and the second
    test (`warn fires once per repo`) is gone because its subject, the warn, no longer exists.

    P22 in one assertion: removing a deprecated destination did not remove the command."""

    def test_35b_legacy_dest_no_longer_warns_and_still_writes(self):
        with tempfile.TemporaryDirectory() as d:
            store = _repo(d)
            store.remember(MemoryItem.create("y", "2"), assume_yes=True)
            legacy = os.path.join(d, MOKATA_DIR, "memory-share.json")

            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                rc = main(["memory", "export", legacy, "--path", d])
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.exists(legacy))                  # writing it still WORKS

            # ⚠ THE ANNOUNCEMENT IS GRADED BY THE MARKER DIRECTORY, NOT BY A WORD.
            # This first read `assertNotIn("deprecated", stderr)` plus a `memory-share.marker`
            # path — both keyed on the vocabulary and the filename the DEPRECATION notice used.
            # A mutant that re-announced the same channel through `warn_removed` sailed straight
            # past: it says "removed", not "deprecated", and its marker is keyed
            # `<channel>@removed-<release>`. That is stage 10's own marker-key finding landing on
            # the test written after it. Both first-use notices — deprecation and removal — mint
            # under `temp_local/deprecations/`, so the directory being EMPTY is the property, and
            # it does not care what the notice calls itself.
            markers = os.path.join(d, MOKATA_DIR, "temp_local", "deprecations")
            self.assertEqual(os.listdir(markers) if os.path.isdir(markers) else [], [])
            for word in ("deprecated", "removed"):
                self.assertNotIn(word, err.getvalue().lower())


# ================================================ deliverable 3 — import via WriteGate + provenance

class TestImportGatedWithProvenance(unittest.TestCase):
    def test_35b_import_gated_with_provenance(self):
        with tempfile.TemporaryDirectory() as d:
            store = _repo(d)
            ledger = AuditLedger.from_mokata_dir(store.backend_dir if hasattr(store, "backend_dir")
                                                 else os.path.join(d, MOKATA_DIR))
            data = _share([MemoryItem.create("region", "eu-west", source="peer", author="alice"),
                           MemoryItem.create("tier", "gold", source="peer", author="alice")])
            res = import_memory(store, data, assume_yes=True, ledger=ledger,
                                source="backups/memory-x.json")
            self.assertEqual(sorted(res.added), ["region", "tier"])

            # item-level provenance stamp — original author kept, restore recorded
            region = store.recall("region")[0]
            self.assertEqual(region.provenance.get("author"), "alice")   # original preserved
            self.assertEqual(region.provenance.get("imported_from"), "backups/memory-x.json")
            self.assertTrue(region.provenance.get("import_batch"))        # the batch anchor id
            self.assertTrue(region.provenance.get("imported_at"))

            # the `import_batch` ledger record (mirrors SIMP.S2's migrate_batch): count + digest
            batch_rows = [e for e in ledger.entries() if e.get("kind") == "import_batch"]
            self.assertEqual(len(batch_rows), 1)
            self.assertEqual(batch_rows[0]["items"], 2)
            self.assertEqual(batch_rows[0]["batch_digest"], region.provenance["import_batch"])

    def test_35b_import_routes_every_item_through_the_writegate(self):
        # the restore's per-item commits appear on the ledger as write_gate rows — the ONE write
        # path (no bypass). Proves the restore is not a raw backend put.
        with tempfile.TemporaryDirectory() as d:
            store = _repo(d)
            ledger = AuditLedger.from_mokata_dir(os.path.join(d, MOKATA_DIR))
            data = _share([MemoryItem.create("a", "1"), MemoryItem.create("b", "2")])
            import_memory(store, data, assume_yes=True, ledger=ledger)
            gate_rows = [e for e in ledger.entries() if e.get("kind") == "write_gate"]
            self.assertGreaterEqual(len(gate_rows), 2)                    # one per imported item


class TestPreviewApproveDeclineIdempotent(unittest.TestCase):
    def test_35b_preview_is_read_only_keys_only(self):
        with tempfile.TemporaryDirectory() as d:
            store = _repo(d)
            data = _share([MemoryItem.create("region", "eu-west"),
                           MemoryItem.create("tier", "gold")])
            plan = plan_memory_import(store, data)
            self.assertEqual(plan.count, 2)
            self.assertEqual(plan.already, 0)
            self.assertIn("region", plan.sample)                         # KEY shown
            self.assertNotIn("eu-west", plan.render())                   # VALUE never shown
            self.assertEqual(store.all_active(), [])                     # READ-ONLY: no writes

    def test_35b_decline_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            store = _repo(d)
            data = _share([MemoryItem.create("region", "eu-west", source="peer")])
            res = import_memory(store, data, confirm=lambda _t: False)   # declined at the gate
            self.assertEqual(res.added, [])
            self.assertIn("region", res.declined)
            self.assertEqual(store.all_active(), [])                     # ZERO writes

    def test_35b_idempotent_re_run_reports_already_imported(self):
        with tempfile.TemporaryDirectory() as d:
            store = _repo(d)
            data = _share([MemoryItem.create("region", "eu-west"),
                           MemoryItem.create("tier", "gold")])
            import_memory(store, data, assume_yes=True)                  # first restore
            plan2 = plan_memory_import(store, data)
            self.assertEqual(plan2.already, 2)                           # preview sees the dups
            self.assertIn("no-op", plan2.render())
            res2 = import_memory(store, data, assume_yes=True)           # re-run
            self.assertEqual(res2.added, [])
            self.assertEqual(sorted(res2.skipped), ["region", "tier"])   # already restored


class TestSecretScanOnIngest(unittest.TestCase):
    def test_35b_secret_on_ingest_is_blocked_named_not_imported(self):
        with tempfile.TemporaryDirectory() as d:
            store = _repo(d)
            ledger = AuditLedger.from_mokata_dir(os.path.join(d, MOKATA_DIR))
            secret = _fake_secret()
            data = _share([MemoryItem.create("leaked.key", f"the prod key is {secret}"),
                           MemoryItem.create("clean.fact", "nothing sensitive")])
            res = import_memory(store, data, assume_yes=True, ledger=ledger)

            self.assertIn("leaked.key", res.blocked)                     # named by KEY
            self.assertIn("clean.fact", res.added)                       # the clean item lands
            self.assertEqual(store.recall("leaked.key"), [])             # secret NOT imported

            # secret-safety: the VALUE never appears in the result, the render, or the ledger
            self.assertNotIn(secret, res.render())
            self.assertNotIn(secret, json.dumps([e for e in ledger.entries()]))
            self.assertNotIn(secret, plan_memory_import(store, data).render())


# ================================================================= deliverable 4 — round trip

class TestRoundTrip(unittest.TestCase):
    def test_35b_round_trip(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            store_a = _repo(a)
            # a plain fact with an explicit scope; a HARD-GATE guardrail; an advisory rule that was
            # NOT promoted (its enforcement is unset → advisory = "unapproved" binding).
            store_a.remember(MemoryItem.create("db", "postgres", source="alice", author="alice",
                                               scope_level="project", scope_id="proj-x"),
                             assume_yes=True)
            store_a.remember(MemoryItem.create("no-secrets-in-logs", "enforced",
                                               kind="guardrail", enforcement="hard"),
                             assume_yes=True)
            store_a.remember(MemoryItem.create("prefer-tabs", "advisory-only", kind="rule"),
                             assume_yes=True)
            backup = os.path.join(a, MOKATA_DIR, "backups", "memory-rt.json")
            export_memory(store_a, dest=backup)
            src = {i.subject: i for i in store_a.all_active()}

            store_b = _repo(b)
            res = import_memory(store_b, load_memory_share(backup), assume_yes=True,
                                source=backup)
            self.assertEqual(sorted(res.added), ["db", "no-secrets-in-logs", "prefer-tabs"])

            dst = {i.subject: i for i in store_b.all_active()}
            # content-identical: subject / value / mtype / kind reproduced for every item
            for key in src:
                self.assertEqual(src[key].value, dst[key].value)
                self.assertEqual(src[key].mtype, dst[key].mtype)
                self.assertEqual(src[key].kind, dst[key].kind)
            # scopes preserved
            self.assertEqual(dst["db"].scope_level, "project")
            self.assertEqual(dst["db"].scope_id, "proj-x")
            # original provenance crossed + the round trip is recorded
            self.assertEqual(dst["db"].provenance.get("author"), "alice")
            self.assertTrue(dst["db"].provenance.get("import_batch"))

            # APPROVAL-STATUS fidelity — NOT laundered by the round trip:
            #   a HARD-GATE guardrail survives as HARD-GATE …
            self.assertEqual(dst["no-secrets-in-logs"].effective_enforcement, "hard")
            #   … and an unapproved (advisory, un-promoted) rule stays unapproved — it does NOT
            #   arrive promoted to hard just because it round-tripped.
            self.assertNotEqual(dst["prefer-tabs"].effective_enforcement, "hard")
            self.assertEqual(dst["prefer-tabs"].effective_enforcement,
                             src["prefer-tabs"].effective_enforcement)

    def test_35b_unapproved_source_does_not_arrive_approved(self):
        # the restore is GATED: a backup item is just file content; declining the gate means it is
        # NOT laundered into the store as an approved/live memory.
        with tempfile.TemporaryDirectory() as d:
            store = _repo(d)
            data = _share([MemoryItem.create("risky", "unreviewed")])
            import_memory(store, data, confirm=lambda _t: False)         # human declines
            self.assertEqual(store.all_active(), [])                     # nothing became live


if __name__ == "__main__":
    unittest.main()
