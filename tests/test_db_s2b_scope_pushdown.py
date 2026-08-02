"""DB.S2b — scope pushdown: the v3 scope/precedence columns become LOAD-BEARING SQL.

DB.S2a pushed `mtype`/`status` only, and said why the v3 columns could not follow: no write path
populated them, so every row carried the DDL default (`personal`/NULL/0/0) while the authoritative
value lived in the `doc` JSON. A scope predicate against those columns would have returned wrong
rows — a cross-tenant visibility bug.

DB.S2b closes the gap in the ONLY safe order: populate at the write layer → backfill the rows that
predate it → only then let a query read the columns. Proven here:

  * PROJECTION — `put()` writes the four columns in the SAME statement as `doc`, so a column can
    never disagree with `MemoryItem.from_dict(doc)` (the property `mtype`/`status` already had);
  * BACKFILL — rows carrying the DDL default are corrected from their own doc, idempotently, on
    both backends;
  * THE ORDERING GUARD (the safety-critical one) — a store whose backfill has NOT run never has a
    scope predicate pushed at it. This is the cross-tenant-leak guard: stale columns are never
    filtered on, they are bypassed;
  * EQUIVALENCE — for every scope context, the SQL union returns exactly what `scope.union_read`
    returns in Python. `union_read` is the spec; the SQL must not invent its own semantics;
  * FLOOR — `TEAM_SCHEMA_MIN_SUPPORTED` is 3 now that v3 columns are load-bearing, and a v2 store
    is refused with `schema-too-old` (migrate) rather than silently mis-filtered.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import sqlite3
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import teamdb
from mokata.memory.backends import (
    PostgresBackend,
    SQLiteBackend,
    filter_clause_for,
)
from mokata.memory.item import ACTIVE, PERSISTENT, MemoryItem
from mokata.memory.scope import (
    CATEGORY,
    GLOBAL,
    PERSONAL,
    PROJECT,
    TEAM,
    ScopeContext,
    scope_path,
    union_read,
)


# --------------------------------------------------------------------------- the scoped corpus
def _scoped_corpus():
    """Items spread across EVERY level of the broad→narrow chain, including the cases that make
    scope filtering dangerous: two different ids at the same level (the cross-tenant pair), and a
    legacy item with an empty scope_id."""
    spec = [
        ("g1", GLOBAL, ""),
        ("t-acme", TEAM, "acme"),
        ("t-rival", TEAM, "rival"),          # the cross-tenant row: must NEVER leak to acme
        ("p-mokata", PROJECT, "mokata"),
        ("p-other", PROJECT, "other"),
        ("c-backend", CATEGORY, "backend"),
        ("c-frontend", CATEGORY, "frontend"),
        ("u-jas", PERSONAL, "jas"),
        ("u-sam", PERSONAL, "sam"),
        ("legacy", PERSONAL, ""),            # pre-TM.S6 item: empty id, matches any personal reader
    ]
    return [MemoryItem(subject=s, value=f"value of {s}", id=s,
                       mtype=PERSISTENT, status=ACTIVE, scope_level=lvl, scope_id=sid)
            for s, lvl, sid in spec]


_CONTEXTS = [
    ScopeContext(user="jas"),                                          # personal-only (LOCAL)
    ScopeContext(team="acme", user="jas"),
    ScopeContext(team="acme", project="mokata", user="jas"),
    ScopeContext(team="acme", project="mokata", category="backend", user="jas"),
    ScopeContext(team="rival", project="other", user="sam"),           # the other tenant
    ScopeContext(project="mokata", user=None),                         # any-user personal ref
    ScopeContext(team="acme", user="jas", include_global=False),       # global element excluded
]


def _ids(items):
    return [i.id for i in items]


def _scope_cols(conn, table, item_id):
    row = conn.execute(
        f"SELECT scope_level, scope_id, pin, priority FROM {table} WHERE id=?", (item_id,)
    ).fetchone()
    return None if row is None else (row[0], row[1], int(row[2]), int(row[3]))


def _doc_scope(conn, table, item_id):
    doc = json.loads(conn.execute(
        f"SELECT doc FROM {table} WHERE id=?", (item_id,)).fetchone()[0])
    return (doc["scope_level"], doc["scope_id"], int(bool(doc["pin"])), int(doc["priority"]))


# ------------------------------------------------------------- a REAL SQL Postgres stand-in
class _PgShim:
    """Runs the Postgres backend's SQL for real on SQLite (`%s` -> `?`), on a table shaped like a
    provisioned v3 `mokata_memory`. Borrowed from the DB.S2a suite so the emitted WHERE is
    EXERCISED, not string-matched."""

    def __init__(self, backfilled=True):
        self._c = sqlite3.connect(":memory:")
        self._c.execute(
            """CREATE TABLE mokata_memory (
                   seq INTEGER PRIMARY KEY AUTOINCREMENT,
                   id TEXT UNIQUE, mtype TEXT, subject TEXT, status TEXT, doc TEXT,
                   project TEXT, revision INTEGER NOT NULL DEFAULT 1,
                   scope_level TEXT NOT NULL DEFAULT 'personal', scope_id TEXT,
                   pin INTEGER NOT NULL DEFAULT 0, priority INTEGER NOT NULL DEFAULT 0,
                   -- DB.S5 (v4): the shim mirrors the shared DDL, so it must carry the
                   -- lifecycle columns `teamdb.provision_sql` provisions or `put()` fails here
                   -- for a reason that has nothing to do with what this file tests.
                   valid_from TEXT, valid_to TEXT,
                   hit_count INTEGER NOT NULL DEFAULT 0, last_recalled_at TEXT
               )"""
        )
        self._c.execute(
            f"CREATE TABLE {teamdb.SCHEMA_VERSION_TABLE} "
            f"(version INT PRIMARY KEY, {teamdb.MIN_SUPPORTED_COLUMN} INT, "
            f"{teamdb.SCOPE_BACKFILLED_COLUMN} INT)"
        )
        self._c.execute(
            f"INSERT INTO {teamdb.SCHEMA_VERSION_TABLE} VALUES (?, ?, ?)",
            (teamdb.TEAM_SCHEMA_VERSION, teamdb.TEAM_SCHEMA_MIN_SUPPORTED,
             1 if backfilled else 0),
        )
        self.sql_log = []

    def execute(self, sql, params=()):
        self.sql_log.append(sql)
        return self._c.execute(sql.replace("%s", "?"), tuple(params or ()))

    def close(self):
        self._c.close()

    def last_select(self):
        return [s for s in self.sql_log if s.lstrip().upper().startswith("SELECT")][-1]


def _seeded_sqlite(items):
    backend = SQLiteBackend(":memory:")
    for it in items:
        backend.put(it)
    return backend


def _seeded_pg(items, project=None, backfilled=True):
    shim = _PgShim(backfilled=backfilled)
    backend = PostgresBackend(project=project, conn=shim)
    for it in items:
        backend.put(it)
    return backend, shim


# ============================================================ 1 · write-path population
class WritePathPopulatesScopeColumnsTest(unittest.TestCase):
    """DELIVERABLE 1 — the columns become a faithful projection of the doc, written by the SAME
    statement, so there is no window in which they can disagree."""

    def test_sqlite_put_populates_scope_columns_from_the_item(self):
        backend = _seeded_sqlite([MemoryItem(
            subject="s", value="v", id="x1",
            scope_level=TEAM, scope_id="acme", pin=True, priority=7)])
        self.addCleanup(backend.close)
        with backend._connect() as conn:
            self.assertEqual(_scope_cols(conn, "memory", "x1"), (TEAM, "acme", 1, 7))
            self.assertEqual(_doc_scope(conn, "memory", "x1"), (TEAM, "acme", 1, 7))

    def test_postgres_put_populates_scope_columns_from_the_item(self):
        backend, shim = _seeded_pg([MemoryItem(
            subject="s", value="v", id="x1",
            scope_level=PROJECT, scope_id="mokata", pin=True, priority=3)])
        self.addCleanup(backend.close)
        self.assertEqual(_scope_cols(shim._c, "mokata_memory", "x1"), (PROJECT, "mokata", 1, 3))
        self.assertEqual(_doc_scope(shim._c, "mokata_memory", "x1"), (PROJECT, "mokata", 1, 3))

    def test_column_and_doc_agree_for_every_item_in_the_corpus(self):
        """The projection property stated as a property, not a spot check."""
        items = _scoped_corpus()
        backend = _seeded_sqlite(items)
        self.addCleanup(backend.close)
        with backend._connect() as conn:
            for it in items:
                with self.subTest(item=it.id):
                    self.assertEqual(_scope_cols(conn, "memory", it.id),
                                     _doc_scope(conn, "memory", it.id))

    def test_upsert_moves_the_columns_with_the_doc(self):
        """A re-put at a NEW scope must move the columns too — a stale column after an update is
        the same cross-tenant bug as a stale column after an insert."""
        backend = SQLiteBackend(":memory:")
        self.addCleanup(backend.close)
        backend.put(MemoryItem(subject="s", value="v", id="x1",
                               scope_level=TEAM, scope_id="acme", pin=True, priority=7))
        backend.put(MemoryItem(subject="s", value="v2", id="x1",
                               scope_level=PERSONAL, scope_id="jas", pin=False, priority=0))
        with backend._connect() as conn:
            self.assertEqual(_scope_cols(conn, "memory", "x1"), (PERSONAL, "jas", 0, 0))


# ============================================================ 2 · backfill
class BackfillTest(unittest.TestCase):
    """DELIVERABLE 2 — rows written before DB.S2b carry the DDL default; the backfill reads each
    row's own doc and corrects the four columns."""

    @staticmethod
    def _stale_store(tmpdir, items):
        """A store in the PRE-DB.S2b state: docs are right, columns carry the DDL default."""
        path = f"{tmpdir}/memory.db"
        backend = SQLiteBackend(path)
        for it in items:
            backend.put(it)
        backend.close()
        conn = sqlite3.connect(path)
        conn.execute("UPDATE memory SET scope_level='personal', scope_id=NULL, pin=0, priority=0")
        conn.execute("PRAGMA user_version=0")     # un-stamp: the backfill has not run
        conn.commit()
        conn.close()
        return path

    def test_sqlite_backfill_corrects_stale_columns_from_the_doc(self):
        import tempfile
        items = _scoped_corpus()
        with tempfile.TemporaryDirectory() as tmp:
            path = self._stale_store(tmp, items)
            backend = SQLiteBackend(path)          # opening runs the backfill
            self.addCleanup(backend.close)
            with backend._connect() as conn:
                for it in items:
                    with self.subTest(item=it.id):
                        self.assertEqual(_scope_cols(conn, "memory", it.id),
                                         _doc_scope(conn, "memory", it.id))

    def test_sqlite_backfill_is_idempotent(self):
        import tempfile
        items = _scoped_corpus()
        with tempfile.TemporaryDirectory() as tmp:
            path = self._stale_store(tmp, items)
            SQLiteBackend(path).close()
            backend = SQLiteBackend(path)          # a second open must change nothing
            self.addCleanup(backend.close)
            with backend._connect() as conn:
                for it in items:
                    self.assertEqual(_scope_cols(conn, "memory", it.id),
                                     _doc_scope(conn, "memory", it.id))

    def test_postgres_backfill_sql_is_in_the_provisioning_ddl(self):
        """The Postgres backfill belongs to `team init` (C4 — runtime runs no migration), so it
        must ride the provisioning statements, AFTER the ADD COLUMNs that create the columns."""
        sql = teamdb.provision_sql()
        joined = "\n".join(sql)
        self.assertIn(teamdb.MEMORY_SCOPE_LEVEL_COLUMN, joined)
        backfill = [i for i, s in enumerate(sql)
                    if s.lstrip().upper().startswith("UPDATE") and "doc" in s]
        self.assertTrue(backfill, "no scope backfill UPDATE in provision_sql()")
        add_col = max(i for i, s in enumerate(sql)
                      if "ADD COLUMN IF NOT EXISTS " + teamdb.MEMORY_PRIORITY_COLUMN in s)
        self.assertLess(add_col, backfill[0],
                        "the backfill must run AFTER the columns it populates exist")

    def test_postgres_backfill_is_stamped_only_after_it_runs(self):
        """The stamp the runtime trusts must be set by the LAST statement, after the backfill —
        stamping first would advertise a store as backfilled during the window before it is."""
        sql = teamdb.provision_sql()
        backfill = min(i for i, s in enumerate(sql)
                       if s.lstrip().upper().startswith("UPDATE") and "doc" in s)
        stamp = max(i for i, s in enumerate(sql)
                    if teamdb.SCOPE_BACKFILLED_COLUMN in s and "INSERT" in s.upper())
        self.assertLess(backfill, stamp)
        self.assertEqual(stamp, len(sql) - 1, "the version/stamp row must be the LAST statement")

    def test_postgres_backfill_reads_the_doc_for_all_four_columns(self):
        """It must project all FOUR columns from the doc — a partially-backfilled row is exactly
        the stale-column state the stamp then wrongly declares clean.

        (The statement is EXECUTED against a real Postgres in
        tests/integration/test_db_s2b_live_db.py — its `::jsonb` casts are Postgres syntax that no
        SQLite shim can honestly stand in for.)"""
        sql = teamdb.provision_sql()
        backfill = next(s for s in sql
                        if s.lstrip().upper().startswith("UPDATE") and "doc" in s)
        for col in (teamdb.MEMORY_SCOPE_LEVEL_COLUMN, teamdb.MEMORY_SCOPE_ID_COLUMN,
                    teamdb.MEMORY_PIN_COLUMN, teamdb.MEMORY_PRIORITY_COLUMN):
            with self.subTest(column=col):
                self.assertIn(f"{col} = ", backfill)              # assigned
                self.assertIn(f"doc::jsonb->>'{col}'", backfill)  # ...from its own doc key


# ============================================================ 3 · THE ORDERING GUARD
class BackfillBeforeFilterGuardTest(unittest.TestCase):
    """THE safety-critical pin (the cross-tenant-leak guard).

    A scope predicate is only ever emitted against columns proven to be a faithful projection. A
    store that has NOT been backfilled must be BYPASSED (no scope predicate; the caller's Python
    `union_read` still filters correctly from the doc) — never filtered on stale columns."""

    def test_a_non_backfilled_postgres_store_reports_no_scope_pushdown(self):
        _backend, shim = _seeded_pg([], backfilled=False)
        backend = PostgresBackend(project=None, conn=shim)
        self.addCleanup(backend.close)
        self.assertFalse(backend.supports_scope_pushdown)

    def test_a_backfilled_postgres_store_reports_scope_pushdown(self):
        _b, shim = _seeded_pg([], backfilled=True)
        backend = PostgresBackend(project=None, conn=shim)
        self.addCleanup(backend.close)
        self.assertTrue(backend.supports_scope_pushdown)

    def test_a_non_backfilled_store_emits_no_scope_predicate_and_loses_no_rows(self):
        """The leak scenario, end to end. Rows whose columns are stale would be DROPPED by a scope
        predicate. The guard must return them all instead, so the doc-based Python filter can do
        the work."""
        items = _scoped_corpus()
        shim = _PgShim(backfilled=False)
        self.addCleanup(shim.close)
        backend = PostgresBackend(project=None, conn=shim)
        for it in items:
            backend.put(it)
        shim._c.execute(
            "UPDATE mokata_memory SET scope_level='personal', scope_id=NULL, pin=0, priority=0")

        ctx = ScopeContext(team="acme", project="mokata", user="jas")
        got = backend.all(scope_path=scope_path(ctx))
        self.assertNotIn("scope_level", shim.last_select())
        self.assertEqual(_ids(got), _ids(items))   # nothing dropped on stale columns

    def test_sqlite_is_backfilled_by_construction_before_any_query_can_run(self):
        """SQLite needs no runtime flag: opening the store runs ADD COLUMN then the backfill
        before `__init__` returns, so no query can precede it. Ordering as an invariant."""
        backend = SQLiteBackend(":memory:")
        self.addCleanup(backend.close)
        self.assertTrue(backend.supports_scope_pushdown)


# ============================================================ 4 · SQL == union_read
class SqlScopeFilterMatchesUnionReadTest(unittest.TestCase):
    """DELIVERABLE 3 — `scope.union_read` is the SPEC. The SQL must reproduce it exactly for every
    context, on both backends: the rows visible at a scope and NO others."""

    def test_sql_scope_filter_matches_union_read_on_both_backends(self):
        items = _scoped_corpus()
        sq = _seeded_sqlite(items)
        pg, _shim = _seeded_pg(items)
        self.addCleanup(sq.close)
        self.addCleanup(pg.close)

        for ctx in _CONTEXTS:
            expected = _ids(union_read(items, ctx))
            path = scope_path(ctx)
            with self.subTest(backend="sqlite", ctx=ctx):
                self.assertEqual(_ids(sq.all(scope_path=path)), expected)
            with self.subTest(backend="postgres", ctx=ctx):
                self.assertEqual(_ids(pg.all(scope_path=path)), expected)

    def test_the_other_tenants_rows_are_never_returned(self):
        """The bug this stage exists to make impossible, named explicitly."""
        items = _scoped_corpus()
        sq = _seeded_sqlite(items)
        self.addCleanup(sq.close)
        acme = scope_path(ScopeContext(team="acme", project="mokata", user="jas"))
        got = _ids(sq.all(scope_path=acme))
        self.assertIn("t-acme", got)
        self.assertNotIn("t-rival", got)
        self.assertNotIn("p-other", got)
        self.assertNotIn("u-sam", got)

    def test_scope_filter_composes_with_the_existing_mtype_status_projection(self):
        """DB.S2a's pushdown must survive intact — the two filters AND together."""
        items = _scoped_corpus() + [MemoryItem(
            subject="off", value="v", id="off", mtype=PERSISTENT, status="stale",
            scope_level=TEAM, scope_id="acme")]
        sq = _seeded_sqlite(items)
        self.addCleanup(sq.close)
        path = scope_path(ScopeContext(team="acme", user="jas"))
        got = _ids(sq.all(mtype=PERSISTENT, statuses=(ACTIVE,), scope_path=path))
        self.assertIn("t-acme", got)
        self.assertNotIn("off", got)        # right scope, wrong status
        self.assertNotIn("t-rival", got)    # right status, wrong scope

    def test_scope_values_are_bound_never_formatted_into_the_sql(self):
        """Injection safety, same bar as DB.S2a: a hostile scope id is compared, never executed."""
        hostile = "x'; DROP TABLE memory; --"
        clause, params = filter_clause_for(
            scope_path=scope_path(ScopeContext(team=hostile, user=hostile)),
            placeholder="?")
        self.assertNotIn(hostile, clause)
        self.assertIn(hostile, params)

    def test_an_empty_path_matches_nothing_like_union_read(self):
        """`union_read` with an empty path returns nothing; the SQL must agree rather than
        degrading to 'no filter' (which would return everything — the fail-OPEN bug)."""
        clause, params = filter_clause_for(scope_path=[], placeholder="?")
        self.assertIn("1=0", clause)
        self.assertEqual(params, ())

    def test_no_scope_path_is_byte_identical_to_db_s2a(self):
        """Zero-config/local recall must not change: no path given => no scope predicate."""
        with_none, params_none = filter_clause_for(PERSISTENT, (ACTIVE,), placeholder="?")
        self.assertNotIn("scope_level", with_none)
        self.assertEqual(params_none, (PERSISTENT, ACTIVE))


# ============================================================ 5 · the schema floor
class SchemaFloorTest(unittest.TestCase):
    """DELIVERABLE 4 — v3 columns are load-bearing in the runtime SQL now, so the floor rises."""

    def test_min_supported_is_three(self):
        self.assertEqual(teamdb.TEAM_SCHEMA_MIN_SUPPORTED, 3)

    def test_a_v2_store_is_refused_as_too_old_not_silently_mis_filtered(self):
        verdict = teamdb.compatibility(2, 2)
        self.assertFalse(verdict.compatible)
        self.assertEqual(verdict.reason, teamdb.REASON_SCHEMA_TOO_OLD)

    def test_a_v3_store_is_in_range(self):
        self.assertTrue(teamdb.compatibility(3, 3).compatible)


if __name__ == "__main__":
    unittest.main()
