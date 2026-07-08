"""TM.S5b — live-DB legs: the MemoryStore→journal rewire against a REAL Postgres (doc 48 E3/C5).

Turns the fake-Postgres unit proof (tests/test_tm_s5b_store_journal.py) into real round-trips:

  - a team-mode `mokata remember` journals-first, then flushes to Postgres when healthy — the
    row lands at revision 1 and the flush ledger entry carries the gate's real approval id (C5);
  - disconnect → `remember` journals (work-locally, not lost) → reconnect → `mokata sync` flushes;
  - local mode writes straight to the backend and NEVER touches the team journal (zero-config).

Gate (explicit, never accidental — same contract as test_live_db.py): runs ONLY when
MOKATA_LIVE_DB=1 AND MOKATA_PG_DSN is set AND psycopg is installed AND the DB is reachable. On a
dev box with no hosted DB these skip cleanly; in the CI `live-db` job they run and are required.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import importlib.util
import json
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

LIVE = os.environ.get("MOKATA_LIVE_DB") == "1"


def _have(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _pg_dsn():
    return os.environ.get("MOKATA_PG_DSN") or os.environ.get("MOKATA_TEST_PG_DSN")


_PG_LIVE = LIVE and _have("psycopg") and bool(_pg_dsn())
_PG_REASON = "live PG off (need MOKATA_LIVE_DB=1 + MOKATA_PG_DSN + psycopg + reachable DB)"

_PROJECT = "tm-s5b-live"


def _team_surface(d, dsn):
    from mokata import MANIFEST_FILENAME, MOKATA_DIR
    from mokata.config import Surface
    from mokata.init import init_repo
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    path = os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("settings", {})["mode"] = "team"
    data["settings"].setdefault("project", {})["id"] = _PROJECT   # pin the shared project key
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.environ["MOKATA_PG_DSN"] = dsn
    return Surface.load(d)


def _approval_seq(surface, subject):
    from mokata.govern.ledger import AuditLedger
    entries = AuditLedger.from_mokata_dir(surface.mokata_dir).entries()
    hits = [e for e in entries if e.get("kind") == "write_gate"
            and e.get("decision") == "approved" and e.get("target") == f"memory:{subject}"]
    return hits[-1]["seq"]


def _remote_row(dsn, rid):
    from mokata import teamdb
    from mokata.memory import _pg
    conn = _pg.get_connection(dsn, RuntimeError)
    row = conn.execute(
        f"SELECT doc, {teamdb.MEMORY_REVISION_COLUMN} FROM {teamdb.MEMORY_TABLE} WHERE id=%s",
        (rid,)).fetchone()
    return None if row is None else {"doc": row[0], "revision": row[1]}


@unittest.skipUnless(_PG_LIVE, _PG_REASON)
class TestLiveTeamRememberFlushesCarryingApproval(unittest.TestCase):
    def setUp(self):
        from mokata import teamdb
        from mokata.memory import _pg
        self.dsn = _pg_dsn()
        self._saved = os.environ.get("MOKATA_PG_DSN")
        teamdb.provision(self.dsn)                       # idempotent DDL (the only DDL path)
        conn = _pg.get_connection(self.dsn, RuntimeError)  # isolate: clear the owned table
        conn.execute(f"DELETE FROM {teamdb.MEMORY_TABLE}")

    def tearDown(self):
        from mokata.memory import _pg
        _pg.reset_manager()
        if self._saved is None:
            os.environ.pop("MOKATA_PG_DSN", None)
        else:
            os.environ["MOKATA_PG_DSN"] = self._saved

    def test_healthy_team_remember_lands_at_revision_1_with_the_real_approval_id(self):
        from mokata import team_journal
        from mokata.govern.ledger import AuditLedger
        from mokata.memory import MemoryItem, MemoryStore
        with tempfile.TemporaryDirectory() as d:
            surface = _team_surface(d, self.dsn)
            store = MemoryStore.from_surface(surface)
            item = MemoryItem.create("db.engine", "postgres")
            res = store.remember(item, confirm=lambda _t: True)
            self.assertTrue(res.committed)

            # healthy → the best-effort flush pushed it live: row at revision 1, journal drained.
            remote = _remote_row(self.dsn, item.id)
            self.assertIsNotNone(remote, "the healthy team write must reach Postgres")
            self.assertEqual(remote["revision"], 1)
            self.assertEqual(team_journal.TeamJournal.for_surface(surface).pending(), [])

            # the flush inherited the gate's approval id (C5 — deferred durability, not consent).
            approval = _approval_seq(surface, "db.engine")
            flushes = [e for e in AuditLedger.from_mokata_dir(surface.mokata_dir).entries()
                       if e.get("kind") == "team_flush"]
            self.assertEqual(len(flushes), 1)
            self.assertEqual(flushes[0]["approval_ledger_id"], approval)

    def test_disconnect_journals_then_reconnect_sync_flushes(self):
        import os as _os
        from mokata import team_journal
        from mokata.govern.ledger import AuditLedger
        from mokata.memory import MemoryItem, MemoryStore, _pg
        with tempfile.TemporaryDirectory() as d:
            surface = _team_surface(d, self.dsn)
            store = MemoryStore.from_surface(surface)

            # DISCONNECT: point at an unreachable DSN so health is OFFLINE (work-locally).
            _pg.reset_manager()
            _os.environ["MOKATA_PG_DSN"] = "postgresql://127.0.0.1:1/nope?connect_timeout=1"
            from mokata import team_health
            team_health.check(surface, force=True)       # cache the OFFLINE verdict
            item = MemoryItem.create("api.retries", "3")
            self.assertTrue(store.remember(item, confirm=lambda _t: True).committed)
            pend = team_journal.TeamJournal.for_surface(surface).pending()
            self.assertEqual([p.key for p in pend], [item.id], "offline write must be journaled")
            self.assertIsNone(_remote_row(self.dsn, item.id), "nothing pushed while offline")

            # RECONNECT + sync: the journaled write now flushes to Postgres.
            _pg.reset_manager()
            _os.environ["MOKATA_PG_DSN"] = self.dsn
            team_health.check(surface, force=True)       # observe the reconnect (health cache TTL)
            led = AuditLedger.from_mokata_dir(surface.mokata_dir)
            res = team_journal.sync(surface, environ=_os.environ, assume_yes=True, ledger=led,
                                    out=lambda _m: None)
            self.assertFalse(res.skipped)
            self.assertGreaterEqual(res.flushed, 1)
            self.assertIsNotNone(_remote_row(self.dsn, item.id), "sync must flush after reconnect")


@unittest.skipUnless(_PG_LIVE, _PG_REASON)
class TestLiveLocalModeUntouched(unittest.TestCase):
    def test_local_remember_writes_to_the_backend_and_never_journals(self):
        from mokata import MANIFEST_FILENAME, MOKATA_DIR, team_journal
        from mokata.config import Surface
        from mokata.init import init_repo
        from mokata.memory import MemoryItem, MemoryStore
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)  # local
            surface = Surface.load(d)
            store = MemoryStore.from_surface(surface)
            self.assertTrue(store.remember(MemoryItem.create("k", "v"),
                                           confirm=lambda _t: True).committed)
            self.assertEqual([i.value for i in store.all_active()], ["v"])
            self.assertFalse(os.path.exists(team_journal.TeamJournal.for_surface(surface).path),
                             "local mode must never create the team journal")


if __name__ == "__main__":
    unittest.main()
