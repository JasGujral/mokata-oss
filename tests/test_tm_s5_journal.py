"""TM.S5 — ONE write path: journal always, flush when healthy (doc 48 E3 + C1 + C5).

Every durable team write lands in a crash-safe LOCAL journal FIRST, then flushes to Postgres in
batches. Healthy and offline use the SAME path — offline just means the flush waits (explicit +
journaled work-locally, never silent divergence, never lost). Each flush is compare-and-set
(C1); a concurrent-writer conflict SURFACES (never silent last-writer-wins). Each flushed write
carries the LEDGER ID of its original human approval (C5 / P2); a per-publish secret-scan still
applies.

The connection is a small fake Postgres (there is no live DB in CI).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import MANIFEST_FILENAME, MOKATA_DIR, run_mode, team_health, team_journal, teamdb
from mokata.config import Surface
from mokata.govern.ledger import AuditLedger
from mokata.init import init_repo


def _repo(d, mode="team"):
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    if mode is not None:
        path = os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME)
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        data.setdefault("settings", {})["mode"] = mode
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    return Surface.load(d)


class _Cursor:
    def __init__(self, rows=(), rowcount=1):
        self._rows = list(rows)
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeMemPg:
    """A tiny in-memory `mokata_memory` with revision CAS semantics — enough to exercise
    insert / update / lost-update-conflict without a live Postgres."""

    def __init__(self):
        self.rows = {}          # id -> {"doc":..., "revision":int, ...}

    def execute(self, sql, params=()):
        low = " ".join(sql.lower().split())
        if low.startswith("insert into mokata_memory"):
            rid = params[0]
            if rid in self.rows:
                return _Cursor(rowcount=0)      # ON CONFLICT DO NOTHING → 0 rows
            self.rows[rid] = {"id": rid, "mtype": params[1], "subject": params[2],
                              "status": params[3], "doc": params[4], "project": params[5],
                              "revision": 1}
            return _Cursor(rowcount=1)
        if low.startswith("update mokata_memory"):
            # params: (mtype, subject, status, doc, project, <DB.S2b scope cols…>, id, base_rev).
            # The CAS pair is read from the TAIL, not by fixed index: DB.S2b inserted four scope
            # columns into the SET list, and an index-based read would silently take `pin` as the
            # id and never match a row again.
            rid, base = params[-2], params[-1]
            row = self.rows.get(rid)
            if row is None or row["revision"] != base:
                return _Cursor(rowcount=0)      # lost update
            row.update({"mtype": params[0], "subject": params[1], "status": params[2],
                        "doc": params[3], "project": params[4], "revision": base + 1})
            return _Cursor(rowcount=1)
        if low.startswith("select") and "from mokata_memory" in low:
            rid = params[0]
            row = self.rows.get(rid)
            if row is None:
                return _Cursor([])
            return _Cursor([(row["doc"], row["revision"])])
        return _Cursor()

    def plant(self, rid, doc, revision):
        self.rows[rid] = {"id": rid, "doc": doc, "revision": revision, "project": None,
                          "mtype": "fact", "subject": rid, "status": "active"}


def _payload(rid, value):
    return {"id": rid, "mtype": "fact", "subject": rid, "status": "active",
            "doc": json.dumps({"id": rid, "value": value}), "project": "p1"}


class TestJournalAlways(unittest.TestCase):
    def test_record_appends_and_is_pending(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            j = team_journal.TeamJournal.for_surface(surface)
            e = team_journal.record_team_write(
                surface, op="memory_put", table=teamdb.MEMORY_TABLE, key="m1",
                payload=_payload("m1", "v1"), ledger_id=7, project="p1", actor="alice")
            self.assertEqual([p.key for p in j.pending()], ["m1"])
            self.assertEqual(e.ledger_id, 7)

    def test_journal_survives_a_fresh_process(self):
        # crash-safety: the journal is on disk, so a brand-new object sees the pending write.
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            team_journal.record_team_write(
                surface, op="memory_put", table=teamdb.MEMORY_TABLE, key="m1",
                payload=_payload("m1", "v1"), ledger_id=1, project="p1", actor="a")
            j2 = team_journal.TeamJournal.for_surface(surface)  # simulate a restart
            self.assertEqual(len(j2.pending()), 1)


class TestOfflineNeverBlocks(unittest.TestCase):
    def test_write_while_offline_journals_and_does_not_block_or_lose(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            team_journal.record_team_write(
                surface, op="memory_put", table=teamdb.MEMORY_TABLE, key="m1",
                payload=_payload("m1", "v1"), ledger_id=3, project="p1", actor="a")
            # OFFLINE health → flush is SKIPPED, writes NOTHING remote, and the entry stays
            # pending (not lost). The call returns immediately (no connection touched).
            offline = team_health.HealthVerdict(team_health.OFFLINE, "unreachable")
            touched = []
            res = team_journal.flush(surface, health=offline,
                                     connect=lambda *a, **k: touched.append(1))
            self.assertTrue(res.skipped)
            self.assertEqual(res.flushed, 0)
            self.assertEqual(res.pending, 1)
            self.assertEqual(touched, [], "offline flush must not open a connection")
            j = team_journal.TeamJournal.for_surface(surface)
            self.assertEqual(len(j.pending()), 1, "the offline write must NOT be lost")


class TestFlushCarriesApproval(unittest.TestCase):
    def test_healthy_flush_pushes_and_records_the_original_ledger_id(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            team_journal.record_team_write(
                surface, op="memory_put", table=teamdb.MEMORY_TABLE, key="m1",
                payload=_payload("m1", "v1"), ledger_id=42, project="p1", actor="alice")
            pg = _FakeMemPg()
            ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
            healthy = team_health.HealthVerdict(team_health.HEALTHY, "reachable")
            res = team_journal.flush(surface, health=healthy,
                                     connect=lambda *a, **k: pg, ledger=ledger)
            self.assertEqual(res.flushed, 1)
            self.assertEqual(res.conflicts, 0)
            self.assertEqual(res.pending, 0)
            self.assertIn("m1", pg.rows)                    # the row reached Postgres
            self.assertEqual(pg.rows["m1"]["revision"], 1)
            # the flush inherits the original approval — its ledger id is recorded (P2, C5).
            flush_entries = [e for e in ledger.entries() if e.get("kind") == "team_flush"]
            self.assertEqual(len(flush_entries), 1)
            self.assertEqual(flush_entries[0]["approval_ledger_id"], 42)

    def test_pending_is_empty_after_a_full_flush(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            team_journal.record_team_write(
                surface, op="memory_put", table=teamdb.MEMORY_TABLE, key="m1",
                payload=_payload("m1", "v1"), ledger_id=1, project="p1", actor="a")
            pg = _FakeMemPg()
            healthy = team_health.HealthVerdict(team_health.HEALTHY, "reachable")
            team_journal.flush(surface, health=healthy, connect=lambda *a, **k: pg)
            j = team_journal.TeamJournal.for_surface(surface)
            self.assertEqual(j.pending(), [])


class TestSecretScanPerPublish(unittest.TestCase):
    def test_a_secret_in_the_payload_blocks_that_flush(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            secret_doc = json.dumps({"id": "m1",
                                     "value": "aws key AKIAIOSFODNN7EXAMPLE secret"})
            payload = {"id": "m1", "mtype": "fact", "subject": "m1", "status": "active",
                       "doc": secret_doc, "project": "p1"}
            team_journal.record_team_write(
                surface, op="memory_put", table=teamdb.MEMORY_TABLE, key="m1",
                payload=payload, ledger_id=1, project="p1", actor="a")
            pg = _FakeMemPg()
            healthy = team_health.HealthVerdict(team_health.HEALTHY, "reachable")
            res = team_journal.flush(surface, health=healthy, connect=lambda *a, **k: pg)
            self.assertEqual(res.flushed, 0)
            self.assertNotIn("m1", pg.rows, "a secret payload must NOT be published")
            self.assertGreaterEqual(res.blocked, 1)


class TestCASConflictSurfaces(unittest.TestCase):
    def test_lost_update_surfaces_as_a_conflict_never_silent_lww(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            # our local write believed base revision 1, but a concurrent writer already
            # advanced the row to revision 2 (a different value).
            team_journal.record_team_write(
                surface, op="memory_put", table=teamdb.MEMORY_TABLE, key="m1",
                payload=_payload("m1", "mine"), ledger_id=5, project="p1", actor="alice",
                base_revision=1)
            pg = _FakeMemPg()
            pg.plant("m1", json.dumps({"id": "m1", "value": "theirs"}), revision=2)
            healthy = team_health.HealthVerdict(team_health.HEALTHY, "reachable")
            res = team_journal.flush(surface, health=healthy, connect=lambda *a, **k: pg)
            self.assertEqual(res.flushed, 0)
            self.assertEqual(res.conflicts, 1)
            # NOT silently overwritten — remote still holds the other writer's value.
            self.assertEqual(json.loads(pg.rows["m1"]["doc"])["value"], "theirs")
            j = team_journal.TeamJournal.for_surface(surface)
            conflicts = j.conflicts()
            self.assertEqual(len(conflicts), 1)
            # a conflicted entry is NOT pending (it waits for the human gate in `sync`).
            self.assertEqual(j.pending(), [])

    def test_concurrent_create_surfaces_as_a_conflict(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            team_journal.record_team_write(
                surface, op="memory_put", table=teamdb.MEMORY_TABLE, key="m1",
                payload=_payload("m1", "mine"), ledger_id=5, project="p1", actor="a",
                base_revision=None)               # believed to be a NEW row
            pg = _FakeMemPg()
            pg.plant("m1", json.dumps({"id": "m1", "value": "theirs"}), revision=1)
            healthy = team_health.HealthVerdict(team_health.HEALTHY, "reachable")
            res = team_journal.flush(surface, health=healthy, connect=lambda *a, **k: pg)
            self.assertEqual(res.conflicts, 1)
            self.assertEqual(res.flushed, 0)


class TestConflictResolution(unittest.TestCase):
    def test_keep_local_requeues_at_the_remote_revision(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            j = team_journal.TeamJournal.for_surface(surface)
            e = team_journal.record_team_write(
                surface, op="memory_put", table=teamdb.MEMORY_TABLE, key="m1",
                payload=_payload("m1", "mine"), ledger_id=5, project="p1", actor="a",
                base_revision=1)
            j.mark_conflict(e.id, detail="lost update",
                            remote={"revision": 2, "doc": json.dumps({"value": "theirs"})})
            self.assertEqual(len(j.conflicts()), 1)
            # keep-local: re-queue the local payload at the CURRENT remote revision (2) so a
            # re-flush CAS matches + bumps to 3 (overwrites remote — a human decision).
            j.resolve(e.id, "kept-local", remote_revision=2)
            pend = j.pending()
            self.assertEqual(len(pend), 1)
            self.assertEqual(pend[0].base_revision, 2)
            self.assertEqual(j.conflicts(), [])

    def test_keep_remote_drops_the_local_write(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            j = team_journal.TeamJournal.for_surface(surface)
            e = team_journal.record_team_write(
                surface, op="memory_put", table=teamdb.MEMORY_TABLE, key="m1",
                payload=_payload("m1", "mine"), ledger_id=5, project="p1", actor="a",
                base_revision=1)
            j.mark_conflict(e.id, detail="lost update", remote={"revision": 2})
            j.resolve(e.id, "kept-remote", remote_revision=2)
            self.assertEqual(j.pending(), [])
            self.assertEqual(j.conflicts(), [])


class TestFloorRecovery(unittest.TestCase):
    def test_stranded_floor_rows_are_enqueued_for_flush(self):
        # doc 48 finding 3: rows the OLD silent fallback stranded in the SQLite floor are
        # recovered by enqueueing them into the journal (so they flush through the same gate).
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            j = team_journal.TeamJournal.for_surface(surface)
            floor = [_payload("m1", "stranded"), _payload("m2", "also-stranded")]
            n = team_journal.recover_stranded_floor(
                surface, floor_rows=floor, remote_ids={"m2"}, project="p1", actor="a")
            self.assertEqual(n, 1, "only the row absent from remote (m1) is recovered")
            self.assertEqual([p.key for p in j.pending()], ["m1"])
            self.assertEqual(j.pending()[0].source, "recovery")

    def test_recovery_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            floor = [_payload("m1", "stranded")]
            team_journal.recover_stranded_floor(surface, floor_rows=floor, remote_ids=set(),
                                                project="p1", actor="a")
            team_journal.recover_stranded_floor(surface, floor_rows=floor, remote_ids=set(),
                                                project="p1", actor="a")
            j = team_journal.TeamJournal.for_surface(surface)
            self.assertEqual(len(j.pending()), 1, "re-running recovery must not double-enqueue")


class TestLocalUnaffected(unittest.TestCase):
    def test_local_mode_never_journals(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, mode=None)             # local (default)
            # in local mode there is no team write path; record_team_write is a no-op guard.
            e = team_journal.record_team_write(
                surface, op="memory_put", table=teamdb.MEMORY_TABLE, key="m1",
                payload=_payload("m1", "v1"), ledger_id=1, project="p1", actor="a")
            self.assertIsNone(e)
            j = team_journal.TeamJournal.for_surface(surface)
            self.assertEqual(j.pending(), [])


if __name__ == "__main__":
    unittest.main()
