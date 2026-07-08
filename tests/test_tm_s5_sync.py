"""TM.S5 — `mokata sync` = manual flush + reconcile (doc 48 E3).

Drives the reconcile flow with a fake Postgres + injected confirm (no live DB):
  * a healthy sync flushes pending writes (carrying the original ledger id);
  * a CAS conflict is SURFACED and resolved through the human gate — keep-local re-flushes
    (overwrites remote at the current revision), keep-remote drops the local write, and a
    non-interactive sync DEFERS the conflict (never silent last-writer-wins);
  * an offline sync is skipped (work-locally, nothing lost);
  * local mode is a no-op.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import MANIFEST_FILENAME, MOKATA_DIR, team_health, team_journal, teamdb
from mokata.config import Surface
from mokata.govern.ledger import AuditLedger
from mokata.init import init_repo

from test_tm_s5_journal import _FakeMemPg, _payload   # reuse the fake PG + payload helper


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


_HEALTHY = team_health.HealthVerdict(team_health.HEALTHY, "reachable")
_OFFLINE = team_health.HealthVerdict(team_health.OFFLINE, "unreachable")


class TestSyncFlush(unittest.TestCase):
    def test_healthy_sync_flushes_pending_carrying_the_ledger_id(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            team_journal.record_team_write(
                surface, op="memory_put", table=teamdb.MEMORY_TABLE, key="m1",
                payload=_payload("m1", "v1"), ledger_id=99, project="p1", actor="a")
            pg = _FakeMemPg()
            ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
            res = team_journal.sync(surface, health=_HEALTHY, connect=lambda *a, **k: pg,
                                    ledger=ledger, assume_yes=True)
            self.assertEqual(res.flushed, 1)
            self.assertEqual(res.pending, 0)
            self.assertIn("m1", pg.rows)
            flush = [e for e in ledger.entries() if e.get("kind") == "team_flush"]
            self.assertEqual(flush[0]["approval_ledger_id"], 99)


class TestSyncOffline(unittest.TestCase):
    def test_offline_sync_is_skipped_and_nothing_lost(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            team_journal.record_team_write(
                surface, op="memory_put", table=teamdb.MEMORY_TABLE, key="m1",
                payload=_payload("m1", "v1"), ledger_id=1, project="p1", actor="a")
            res = team_journal.sync(surface, health=_OFFLINE, connect=lambda *a, **k: None)
            self.assertTrue(res.skipped)
            self.assertEqual(res.pending, 1)
            j = team_journal.TeamJournal.for_surface(surface)
            self.assertEqual(len(j.pending()), 1)


class TestSyncConflictGate(unittest.TestCase):
    def _planted_conflict(self, d):
        surface = _repo(d)
        team_journal.record_team_write(
            surface, op="memory_put", table=teamdb.MEMORY_TABLE, key="m1",
            payload=_payload("m1", "mine"), ledger_id=5, project="p1", actor="alice",
            base_revision=1)
        pg = _FakeMemPg()
        pg.plant("m1", json.dumps({"id": "m1", "value": "theirs"}), revision=2)
        return surface, pg

    def test_keep_local_reflushes_and_overwrites_remote(self):
        with tempfile.TemporaryDirectory() as d:
            surface, pg = self._planted_conflict(d)
            ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
            res = team_journal.sync(surface, health=_HEALTHY, connect=lambda *a, **k: pg,
                                    ledger=ledger, confirm=lambda _p: True)   # keep LOCAL
            self.assertEqual(res.conflicts_found, 1)
            self.assertEqual(res.resolved_local, 1)
            self.assertEqual(res.flushed, 1, "the kept-local write is re-flushed")
            self.assertEqual(json.loads(pg.rows["m1"]["doc"])["value"], "mine")
            self.assertEqual(pg.rows["m1"]["revision"], 3, "CAS bumped from remote rev 2")
            decisions = [e for e in ledger.entries() if e.get("kind") == "team_sync_conflict"]
            self.assertEqual(decisions[0]["decision"], "kept-local")
            self.assertEqual(team_journal.TeamJournal.for_surface(surface).pending(), [])

    def test_keep_remote_drops_the_local_write(self):
        with tempfile.TemporaryDirectory() as d:
            surface, pg = self._planted_conflict(d)
            res = team_journal.sync(surface, health=_HEALTHY, connect=lambda *a, **k: pg,
                                    confirm=lambda _p: False)                 # keep REMOTE
            self.assertEqual(res.resolved_remote, 1)
            self.assertEqual(res.flushed, 0)
            self.assertEqual(json.loads(pg.rows["m1"]["doc"])["value"], "theirs")
            j = team_journal.TeamJournal.for_surface(surface)
            self.assertEqual(j.pending(), [])
            self.assertEqual(j.conflicts(), [])

    def test_non_interactive_defers_never_silent_lww(self):
        with tempfile.TemporaryDirectory() as d:
            surface, pg = self._planted_conflict(d)
            # assume_yes with NO confirm callable → the conflict is DEFERRED (not overwritten).
            res = team_journal.sync(surface, health=_HEALTHY, connect=lambda *a, **k: pg,
                                    assume_yes=True)
            self.assertEqual(res.deferred, 1)
            self.assertEqual(res.resolved_local, 0)
            self.assertEqual(json.loads(pg.rows["m1"]["doc"])["value"], "theirs",
                             "a deferred conflict must NOT silently overwrite remote")
            # the conflict is still surfaced for a later interactive resolve.
            self.assertEqual(len(team_journal.TeamJournal.for_surface(surface).conflicts()), 1)


class TestSyncRecovery(unittest.TestCase):
    def test_sync_runs_the_recovery_step(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            pg = _FakeMemPg()
            calls = []
            res = team_journal.sync(surface, health=_HEALTHY, connect=lambda *a, **k: pg,
                                    recover=lambda: (calls.append(1) or 3))
            self.assertEqual(res.recovered, 3)
            self.assertEqual(calls, [1])


class TestSyncCLI(unittest.TestCase):
    def test_local_mode_is_a_noop(self):
        import io
        from contextlib import redirect_stdout
        from mokata.cli_commands.sync import cmd_sync
        import argparse
        with tempfile.TemporaryDirectory() as d:
            _repo(d, mode=None)                     # local
            buf = io.StringIO()
            args = argparse.Namespace(path=d, yes=False)
            with redirect_stdout(buf):
                rc = cmd_sync(args)
            self.assertEqual(rc, 0)
            self.assertIn("local mode", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
