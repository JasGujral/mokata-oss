"""CM.S5 live leg — the shared event stream against a REAL Postgres, and a REAL notification.

Three claims in this stage cannot be graded without a server, and faking one and calling the
result measured is the failure this release keeps finding:

  1. **absence crosses the team boundary as NULL and not as 0** — the §7g distinction the whole
     typed stream exists for, at the one boundary more people read than the local store;
  2. **a notification is actually RECEIVED** by a separate connection. A publisher can send a
     `NOTIFY` into a void forever and look perfectly healthy from its own side;
  3. **the v6 retirement REFUSES a non-empty v5 table** — a `RAISE EXCEPTION` in plpgsql is not
     exercised by reading the SQL string.

⚠ Gated exactly like its siblings (`MOKATA_LIVE_DB=1` + a DSN + `psycopg`), which means it SKIPS
by default — and stage 11 measured what that is worth: `ci.yml`'s live-db job runs only
`-p "test_live_db.py"`, so a module added here runs in `live-db-legs.yml` (opt-in + weekly) and
nowhere else. That is recorded in the stage report rather than worked around here, because
changing which legs CI runs is a workflow decision and this is a test file.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import importlib.util
import json
import os
import threading
import time
import unittest

import _support  # noqa: F401  (puts src/ on the path when not pip-installed)

LIVE = os.environ.get("MOKATA_LIVE_DB") == "1"


def _have(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _pg_dsn():
    return os.environ.get("MOKATA_PG_DSN") or os.environ.get("MOKATA_TEST_PG_DSN")


_PG_LIVE = LIVE and _have("psycopg") and bool(_pg_dsn())
_PG_REASON = "live PG off (need MOKATA_LIVE_DB=1 + MOKATA_PG_DSN + psycopg + reachable DB)"

_PROJECT = "cm-s5-live"


def setUpModule():
    """Provision through the REAL admin path — the DDL `mokata team init` owns (D1/C4). Nothing
    here conjures a table at runtime, and a genuinely unprovisioned DB SHOULD be red."""
    if not _PG_LIVE:
        return
    from mokata import teamdb
    teamdb.provision(_pg_dsn())


def _team_surface(d, dsn):
    from mokata import MANIFEST_FILENAME, MOKATA_DIR
    from mokata.config import Surface
    from mokata.init import init_repo
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _m: None)
    path = os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    settings = data.setdefault("settings", {})
    settings["mode"] = "team"
    settings.setdefault("project", {})["id"] = _PROJECT        # pin the shared project key
    settings.setdefault("events", {})["shared"] = True         # opt in, explicitly
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.environ["MOKATA_PG_DSN"] = dsn
    return Surface.load(d)


def _wipe(dsn, ns):
    import psycopg
    from mokata.teamdb import EVENTS_TABLE
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(f"DELETE FROM {EVENTS_TABLE} WHERE namespace = %s", (ns,))  # nosec B608


@unittest.skipUnless(_PG_LIVE, _PG_REASON)
class TestAbsenceSurvivesTheTeamBoundary(unittest.TestCase):
    """⭐ The point of the v6 schema, measured rather than argued."""

    def setUp(self):
        import tempfile
        from mokata import team_events as TE
        self.dsn = _pg_dsn()
        self.d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, self.d, True)
        self.surface = _team_surface(self.d, self.dsn)
        self.ns = TE.namespace(self.d)
        _wipe(self.dsn, self.ns)
        self.addCleanup(_wipe, self.dsn, self.ns)

    def test_a_measured_zero_and_an_absence_do_NOT_become_the_same_row(self):
        from mokata import team_events as TE
        from mokata.events import GateDecision, ToolCall, emit
        emit(self.d, ToolCall("timed", "mcp", True), session_id="s1", duration_ms=0)
        emit(self.d, ToolCall("untimed", "mcp", True), session_id="s1")
        emit(self.d, GateDecision("write_gate", "approved", subject="src/a.py"),
             session_id="s1", ledger_seq=41)
        res = TE.publish(self.d, self.surface, assume_yes=True)
        self.assertTrue(res.ok, res.message)

        log = TE.make_shared_log(data=self.surface.manifest.data)
        try:
            rows = {r["data"].get("tool") or r["data"].get("gate"): r
                    for r in log.read(self.ns)}
        finally:
            log.close()
        self.assertEqual(rows["timed"]["duration_ms"], 0, "a MEASURED zero was lost")
        self.assertIsNone(rows["untimed"]["duration_ms"], "an absence became a number")
        self.assertEqual(rows["write_gate"]["ledger_seq"], 41)
        self.assertIsNone(rows["timed"]["ledger_seq"], "an unbacked event claimed the hash chain")

    def test_a_RE_PUBLISH_lands_nothing_and_duplicates_nothing(self):
        import psycopg
        from mokata import team_events as TE
        from mokata.events import ToolCall, emit
        from mokata.teamdb import EVENTS_TABLE
        emit(self.d, ToolCall("once", "mcp", True), session_id="s1")
        first = TE.publish(self.d, self.surface, assume_yes=True)
        self.assertTrue(first.ok)
        # Wipe the high-water mark the way a rebuilt local store would, so the UNIQUE INDEX is
        # the only thing left standing between a re-publish and duplicate rows.
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute(f"UPDATE {EVENTS_TABLE} SET local_seq = NULL "      # nosec B608
                         "WHERE namespace = %s", (self.ns,))
        again = TE.publish(self.d, self.surface, assume_yes=True)
        self.assertTrue(again.ok, again.message)
        self.assertGreater(again.already_there, 0, "the index did not deduplicate anything")
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            dupes = conn.execute(
                f"SELECT count(*) FROM (SELECT event_id FROM {EVENTS_TABLE} "   # nosec B608
                "WHERE namespace = %s GROUP BY event_id HAVING count(*) > 1) x",
                (self.ns,)).fetchone()[0]
        self.assertEqual(dupes, 0, "a re-publish duplicated rows")


@unittest.skipUnless(_PG_LIVE, _PG_REASON)
class TestTheDoorbellIsActuallyHeard(unittest.TestCase):
    """⛔ The claim a publisher cannot make about itself. A `NOTIFY` sent into a void looks exactly
    like one that was delivered, from the sending side — which is why this leg exists at all."""

    def test_a_SEPARATE_connection_receives_the_notification(self):
        import shutil
        import tempfile

        import psycopg
        from mokata import team_events as TE
        from mokata.events import ToolCall, emit
        dsn = _pg_dsn()
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        surface = _team_surface(d, dsn)
        ns = TE.namespace(d)
        _wipe(dsn, ns)
        self.addCleanup(_wipe, dsn, ns)

        heard = []

        def listen():
            with psycopg.connect(dsn, autocommit=True) as conn:
                conn.execute(f"LISTEN {TE.NOTIFY_CHANNEL}")
                for note in conn.notifies(timeout=20, stop_after=1):
                    heard.append((note.channel, note.payload))

        thread = threading.Thread(target=listen, daemon=True)
        thread.start()
        time.sleep(1.5)                      # let the LISTEN register before we ring

        emit(d, ToolCall("ring", "mcp", True), session_id="s1")
        res = TE.publish(d, surface, assume_yes=True)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(res.notify_state, TE.NOTIFY_SUPPORTED,
                         "this DSN was classified as unable to deliver — check the fixture")
        thread.join(timeout=22)

        self.assertEqual(len(heard), 1, "no notification arrived on a direct connection")
        channel, payload = heard[0]
        self.assertEqual(channel, TE.NOTIFY_CHANNEL)
        body = json.loads(payload)
        self.assertEqual(body["ns"], ns)
        self.assertGreaterEqual(body["n"], 1)
        self.assertNotIn("data", body, "the doorbell carried the delivery")


@unittest.skipUnless(_PG_LIVE, _PG_REASON)
class TestTheV6RetirementRefusesRatherThanMigrating(unittest.TestCase):
    """The `RAISE EXCEPTION` is plpgsql: reading the SQL string proves nothing about whether it
    fires. These two tests are the only place that question is answered."""

    def _legacy_table(self, conn, rows):
        conn.execute("DROP TABLE IF EXISTS cm_s5_legacy_probe")
        conn.execute("CREATE TABLE cm_s5_legacy_probe (id BIGSERIAL PRIMARY KEY, "
                     "namespace TEXT, project TEXT, kind TEXT, at TEXT, actor TEXT, payload TEXT)")
        for i in range(rows):
            conn.execute("INSERT INTO cm_s5_legacy_probe (namespace, kind, at, actor, payload) "
                         "VALUES (%s,%s,%s,%s,%s)", (f"ns{i}", "k", "t", "a", "{}"))

    def _run_events_ddl(self, conn):
        """Run the events statements this build ships, against the probe table — the SAME SQL,
        with the table name swapped, so the guard under test is the shipped one."""
        from mokata import teamdb
        for stmt in teamdb.provision_sql():
            if isinstance(stmt, str) and teamdb.EVENTS_TABLE in stmt:
                conn.execute(stmt.replace(teamdb.EVENTS_TABLE, "cm_s5_legacy_probe"))

    def test_an_EMPTY_legacy_table_migrates_and_the_v5_trio_is_gone(self):
        import psycopg
        dsn = _pg_dsn()
        with psycopg.connect(dsn, autocommit=True) as conn:
            self._legacy_table(conn, 0)
            self._run_events_ddl(conn)
            cols = {r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'cm_s5_legacy_probe'").fetchall()}
            conn.execute("DROP TABLE IF EXISTS cm_s5_legacy_probe")
        self.assertIn("duration_ms", cols)
        self.assertIn("ledger_seq", cols)
        for dead in ("kind", "at", "payload"):
            self.assertNotIn(dead, cols, f"the v5 column `{dead}` survived the migration")

    def test_a_NON_EMPTY_legacy_table_is_REFUSED_and_its_rows_are_UNTOUCHED(self):
        import psycopg
        dsn = _pg_dsn()
        with psycopg.connect(dsn, autocommit=True) as conn:
            self._legacy_table(conn, 3)
            with self.assertRaises(Exception) as caught:
                self._run_events_ddl(conn)
            self.assertIn("NOTHING was changed", str(caught.exception))
        # a fresh connection, because the refusal aborted the one above
        with psycopg.connect(dsn, autocommit=True) as conn:
            cols = {r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'cm_s5_legacy_probe'").fetchall()}
            rows = conn.execute("SELECT count(*) FROM cm_s5_legacy_probe").fetchone()[0]
            conn.execute("DROP TABLE IF EXISTS cm_s5_legacy_probe")
        self.assertEqual(rows, 3, "rows were destroyed by a refused migration")
        for kept in ("kind", "at", "payload"):
            self.assertIn(kept, cols, f"`{kept}` was dropped despite the refusal")


if __name__ == "__main__":
    unittest.main()
