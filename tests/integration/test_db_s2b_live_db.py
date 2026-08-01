"""DB.S2b — the scope pushdown against a REAL Postgres.

The unit suite (tests/test_db_s2b_scope_pushdown.py) proves the semantics on a SQLite shim, but two
things in this stage are Postgres SQL that no shim can honestly stand in for:

  * the BACKFILL in `teamdb.provision_sql()` — `doc::jsonb->>'…'` with `::boolean`/`::int` casts and
    `IS DISTINCT FROM`. A shim that "translated" those into `json_extract` would be testing the
    translation, not the statement that actually ships;
  * the scope predicate running through psycopg's own `%s` binding against real column types
    (`BOOLEAN pin`, a genuinely NULL `scope_id`) rather than SQLite's permissive affinities.

Both are the safety-critical half of the stage, so they get a real engine. Gate is the same
explicit contract as the other live-DB legs: MOKATA_LIVE_DB=1 + MOKATA_PG_DSN + psycopg + a
reachable DB, else these skip cleanly.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import importlib.util
import json
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
    from mokata.memory.scope import CATEGORY, GLOBAL, PERSONAL, PROJECT, TEAM
    spec = [
        ("g1", GLOBAL, "", False, 0),
        ("t-acme", TEAM, "acme", True, 5),
        ("t-rival", TEAM, "rival", False, 0),      # the cross-tenant row
        ("p-mokata", PROJECT, "mokata", False, 2),
        ("p-other", PROJECT, "other", False, 0),
        ("c-backend", CATEGORY, "backend", False, 0),
        ("u-jas", PERSONAL, "jas", False, 0),
        ("u-sam", PERSONAL, "sam", False, 0),
        ("legacy", PERSONAL, "", False, 0),        # empty id: matches any personal reader
    ]
    return [MemoryItem(subject=s, value=f"value of {s}", id=s, mtype=PERSISTENT, status=ACTIVE,
                       scope_level=lvl, scope_id=sid, pin=pin, priority=pri)
            for s, lvl, sid, pin, pri in spec]


@unittest.skipUnless(_PG_LIVE, _PG_REASON)
class LiveScopePushdownTest(unittest.TestCase):
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

    def _cols(self, rid):
        from mokata import teamdb
        row = self.conn.execute(
            f"SELECT scope_level, scope_id, pin, priority FROM {teamdb.MEMORY_TABLE} WHERE id=%s",
            (rid,)).fetchone()
        return (row[0], row[1] or "", bool(row[2]), int(row[3]))

    def _doc_cols(self, rid):
        from mokata import teamdb
        doc = self.conn.execute(
            f"SELECT doc FROM {teamdb.MEMORY_TABLE} WHERE id=%s", (rid,)).fetchone()[0]
        d = json.loads(doc)
        return (d["scope_level"] or "personal", d["scope_id"] or "",
                bool(d["pin"]), int(d["priority"]))

    # ---------------------------------------------------------------- population
    def test_put_projects_every_scope_column_into_real_postgres_types(self):
        """The BOOLEAN `pin` column is the one a SQLite shim cannot vouch for — psycopg has to
        adapt what `scope_columns_from_doc` returns into a real boolean."""
        backend = self._backend()
        for it in _corpus():
            backend.put(it)
        for it in _corpus():
            with self.subTest(item=it.id):
                self.assertEqual(self._cols(it.id), self._doc_cols(it.id))

    # ---------------------------------------------------------------- the backfill
    def test_provisioning_backfill_corrects_stale_columns_and_is_idempotent(self):
        """The v2→v3 upgrade path, executed. Rows are pushed back to the DDL default (exactly the
        state a pre-DB.S2b `team init` left them in), then the shipped backfill statement runs."""
        from mokata import teamdb
        backend = self._backend()
        for it in _corpus():
            backend.put(it)
        self.conn.execute(
            f"UPDATE {teamdb.MEMORY_TABLE} "
            "SET scope_level='personal', scope_id=NULL, pin=FALSE, priority=0")

        backfill = [s for s in teamdb.provision_sql()
                    if isinstance(s, str) and s.lstrip().upper().startswith("UPDATE")
                    and "doc" in s]
        self.assertTrue(backfill, "no backfill UPDATE shipped in provision_sql()")

        cur = self.conn.execute(backfill[0])
        self.assertGreater(cur.rowcount, 0, "the backfill must have corrected the stale rows")
        for it in _corpus():
            with self.subTest(item=it.id):
                self.assertEqual(self._cols(it.id), self._doc_cols(it.id))

        # idempotent BY PREDICATE: a second run must match zero rows, not re-write them.
        again = self.conn.execute(backfill[0])
        self.assertEqual(again.rowcount, 0, "the backfill must be a no-op once converged")

    def test_provisioning_stamps_the_store_as_backfilled(self):
        from mokata import teamdb
        row = self.conn.execute(
            f"SELECT {teamdb.SCOPE_BACKFILLED_COLUMN} FROM {teamdb.SCHEMA_VERSION_TABLE} "
            "ORDER BY version DESC LIMIT 1").fetchone()
        self.assertTrue(row and row[0])
        self.assertTrue(self._backend().supports_scope_pushdown)

    # ---------------------------------------------------------------- the predicate
    def test_sql_scope_filter_matches_union_read_against_real_postgres(self):
        from mokata.memory.scope import ScopeContext, scope_path, union_read
        items = _corpus()
        backend = self._backend()
        for it in items:
            backend.put(it)

        contexts = [
            ScopeContext(user="jas"),
            ScopeContext(team="acme", user="jas"),
            ScopeContext(team="acme", project="mokata", user="jas"),
            ScopeContext(team="acme", project="mokata", category="backend", user="jas"),
            ScopeContext(team="rival", project="other", user="sam"),
            ScopeContext(project="mokata", user=None),
            ScopeContext(team="acme", user="jas", include_global=False),
        ]
        for ctx in contexts:
            expected = [i.id for i in union_read(items, ctx)]
            with self.subTest(ctx=ctx):
                got = [i.id for i in backend.all(scope_path=scope_path(ctx))]
                self.assertEqual(got, expected)

    def test_a_null_scope_id_still_reads_as_the_legacy_personal_item(self):
        """A row whose `scope_id` is genuinely NULL (not '') — the shape a pre-DB.S2b row has — must
        match a personal reader, exactly as `on_path` treats an empty id."""
        from mokata import teamdb
        from mokata.memory.item import ACTIVE, PERSISTENT, MemoryItem
        from mokata.memory.scope import ScopeContext, scope_path
        backend = self._backend()
        backend.put(MemoryItem(subject="s", value="v", id="nullish",
                               mtype=PERSISTENT, status=ACTIVE))
        self.conn.execute(
            f"UPDATE {teamdb.MEMORY_TABLE} SET scope_id=NULL WHERE id=%s", ("nullish",))
        got = [i.id for i in backend.all(
            scope_path=scope_path(ScopeContext(team="acme", user="jas")))]
        self.assertIn("nullish", got)


if __name__ == "__main__":
    unittest.main()
