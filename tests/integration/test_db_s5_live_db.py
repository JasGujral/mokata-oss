"""DB.S5 — the v4 lifecycle schema + usage telemetry against a REAL Postgres.

The unit suite proves the semantics on SQLite and on a SQLite-backed shim. Three things in this
stage are Postgres SQL that no shim can honestly stand in for, and all three are the parts that
would fail silently rather than loudly if they were wrong:

  * the v4 ADD-COLUMN block + the `valid_from` BACKFILL in `teamdb.provision_sql()` — real
    `doc::jsonb->'provenance'->>'created_at'` traversal and `IS DISTINCT FROM` idempotence, on a
    table that already has rows. A shim that translated those into `json_extract` would be testing
    the translation, not the statement that ships;
  * `record_usage` / `usage_stats` through psycopg's own binding, against a real `TIMESTAMPTZ`
    column — SQLite stores the stamp as TEXT and hands back exactly what went in, so only a live
    engine proves the round trip normalizes to something `lifecycle.parse_iso` can read;
  * `= ANY(%s)` array binding, which has no SQLite equivalent at all.

Gate is the same explicit contract as the other live-DB legs: MOKATA_LIVE_DB=1 + MOKATA_PG_DSN +
psycopg + a reachable DB, else these skip cleanly.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import importlib.util
import os
import unittest

import _support  # noqa: F401  (puts src/ on the path)

LIVE = os.environ.get("MOKATA_LIVE_DB") == "1"


def _have(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _pg_dsn():
    return os.environ.get("MOKATA_PG_DSN") or os.environ.get("MOKATA_TEST_PG_DSN")


_PG_LIVE = LIVE and _have("psycopg") and bool(_pg_dsn())
_PG_REASON = "live PG off (need MOKATA_LIVE_DB=1 + MOKATA_PG_DSN + psycopg + reachable DB)"


def _corpus():
    from mokata.memory.item import ACTIVE, PERSISTENT, MemoryItem
    return [MemoryItem(subject=f"s{i}", value=f"value of s{i}", id=f"s{i}",
                       mtype=PERSISTENT, status=ACTIVE,
                       provenance={"source": "test", "author": "t",
                                   "created_at": f"2026-07-{10 + i:02d}T00:00:00+00:00"})
            for i in range(5)]


@unittest.skipUnless(_PG_LIVE, _PG_REASON)
class LiveLifecycleSchemaTest(unittest.TestCase):
    def setUp(self):
        from mokata import teamdb
        from mokata.memory import _pg
        self.dsn = _pg_dsn()
        teamdb.provision(self.dsn)                          # idempotent DDL (the only DDL path)
        self.conn = _pg.get_connection(self.dsn, RuntimeError)
        self.conn.execute(f"DELETE FROM {teamdb.MEMORY_TABLE}")

    def tearDown(self):
        from mokata.memory import _pg
        _pg.reset_manager()

    def _backend(self):
        from mokata.memory.backends import PostgresBackend
        return PostgresBackend(project=None, conn=self.conn)

    def _row(self, rid):
        from mokata import teamdb
        return self.conn.execute(
            f"SELECT {teamdb.MEMORY_VALID_FROM_COLUMN}, {teamdb.MEMORY_VALID_TO_COLUMN}, "
            f"{teamdb.MEMORY_HIT_COUNT_COLUMN}, {teamdb.MEMORY_LAST_RECALLED_AT_COLUMN} "
            f"FROM {teamdb.MEMORY_TABLE} WHERE id=%s", (rid,)).fetchone()

    # ---------------------------------------------------------------- schema + provisioning
    def test_provisioning_creates_the_v4_columns_and_stamps_v4(self):
        from mokata import teamdb
        cols = {r[0] for r in self.conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name=%s",
            (teamdb.MEMORY_TABLE,)).fetchall()}
        for col in (teamdb.MEMORY_VALID_FROM_COLUMN, teamdb.MEMORY_VALID_TO_COLUMN,
                    teamdb.MEMORY_HIT_COUNT_COLUMN, teamdb.MEMORY_LAST_RECALLED_AT_COLUMN):
            self.assertIn(col, cols)
        version = self.conn.execute(
            f"SELECT version FROM {teamdb.SCHEMA_VERSION_TABLE} "
            "ORDER BY version DESC LIMIT 1").fetchone()[0]
        self.assertEqual(version, teamdb.TEAM_SCHEMA_VERSION)

    def test_put_projects_the_validity_window_into_real_columns(self):
        backend = self._backend()
        for it in _corpus():
            backend.put(it)
        for it in _corpus():
            with self.subTest(item=it.id):
                valid_from, valid_to, hits, last = self._row(it.id)
                self.assertEqual(valid_from, it.created_at)
                self.assertIsNone(valid_to, "a fresh item's window must be OPEN")
                # A put must NOT touch the usage columns — they are not a doc projection.
                self.assertEqual(hits, 0)
                self.assertIsNone(last)

    def test_the_backfill_opens_windows_and_is_idempotent(self):
        """The v3→v4 upgrade path, executed on a table that already has rows: push `valid_from`
        back to NULL (exactly the state a pre-DB.S5 `team init` left it in), then run the shipped
        statement."""
        from mokata import teamdb
        backend = self._backend()
        for it in _corpus():
            backend.put(it)
        self.conn.execute(
            f"UPDATE {teamdb.MEMORY_TABLE} SET {teamdb.MEMORY_VALID_FROM_COLUMN}=NULL")

        backfill = [s for s in teamdb.provision_sql()
                    if isinstance(s, str) and s.lstrip().upper().startswith("UPDATE")
                    and teamdb.MEMORY_VALID_FROM_COLUMN in s]
        self.assertEqual(len(backfill), 1, "expected exactly one valid_from backfill statement")

        cur = self.conn.execute(backfill[0])
        self.assertGreater(cur.rowcount, 0, "the backfill must have opened the stale rows")
        for it in _corpus():
            with self.subTest(item=it.id):
                self.assertEqual(self._row(it.id)[0], it.created_at)

        # idempotent BY PREDICATE: a second run matches zero rows, not re-writes them.
        again = self.conn.execute(backfill[0])
        self.assertEqual(again.rowcount, 0, "the backfill must be a no-op once converged")

    def test_the_backfill_never_closes_a_window(self):
        """The never-delete invariant, at the migration. Upgrading must not retire one item."""
        from mokata import teamdb
        backend = self._backend()
        for it in _corpus():
            backend.put(it)
        for stmt in teamdb.provision_sql():
            if isinstance(stmt, str) and stmt.lstrip().upper().startswith("UPDATE"):
                self.conn.execute(stmt)
        closed = self.conn.execute(
            f"SELECT count(*) FROM {teamdb.MEMORY_TABLE} "
            f"WHERE {teamdb.MEMORY_VALID_TO_COLUMN} IS NOT NULL").fetchone()[0]
        self.assertEqual(closed, 0)

    # ---------------------------------------------------------------- usage telemetry
    def test_record_usage_bumps_the_counter_through_real_types(self):
        """`= ANY(%s)` array binding + the TIMESTAMPTZ round trip — neither has a SQLite twin."""
        from mokata.memory.item import now_iso
        backend = self._backend()
        items = _corpus()
        for it in items:
            backend.put(it)
        ids = [it.id for it in items[:3]]
        self.assertEqual(backend.record_usage(ids, now_iso()), 3)
        backend.record_usage(ids[:1], now_iso())            # a second hit on the first

        stats = backend.usage_stats([it.id for it in items])
        self.assertEqual(stats[ids[0]][0], 2)
        self.assertEqual(stats[ids[1]][0], 1)
        self.assertEqual(stats[items[4].id][0], 0)
        # the stamp must come back as something `lifecycle.parse_iso` can actually read
        from mokata.memory import lifecycle
        self.assertIsNotNone(lifecycle.parse_iso(stats[ids[0]][1]))

    def test_usage_recording_leaves_the_approved_doc_untouched(self):
        """Transient run-state: telemetry must not rewrite a byte a human approved."""
        from mokata import teamdb
        from mokata.memory.item import now_iso
        backend = self._backend()
        item = _corpus()[0]
        backend.put(item)
        before = self.conn.execute(
            f"SELECT doc FROM {teamdb.MEMORY_TABLE} WHERE id=%s", (item.id,)).fetchone()[0]
        backend.record_usage([item.id], now_iso())
        after = self.conn.execute(
            f"SELECT doc FROM {teamdb.MEMORY_TABLE} WHERE id=%s", (item.id,)).fetchone()[0]
        self.assertEqual(before, after)

    def test_usage_is_project_scoped(self):
        """A usage counter is as tenant-scoped as the row it counts — one project must neither
        stamp nor read another's."""
        from mokata.memory.backends import PostgresBackend
        from mokata.memory.item import now_iso
        a = PostgresBackend(project="proj-a", conn=self.conn)
        b = PostgresBackend(project="proj-b", conn=self.conn)
        item = _corpus()[0]
        a.put(item)
        self.assertEqual(b.record_usage([item.id], now_iso()), 0,
                         "project B stamped project A's row")
        self.assertEqual(b.usage_stats([item.id]), {},
                         "project B read project A's usage")
        self.assertEqual(a.record_usage([item.id], now_iso()), 1)

    def test_a_re_put_never_resets_the_usage_counter(self):
        from mokata.memory.item import now_iso
        backend = self._backend()
        item = _corpus()[0]
        backend.put(item)
        backend.record_usage([item.id], now_iso())
        item.value = "edited after being recalled"
        backend.put(item)
        self.assertEqual(backend.usage_stats([item.id])[item.id][0], 1)


if __name__ == "__main__":
    unittest.main()
