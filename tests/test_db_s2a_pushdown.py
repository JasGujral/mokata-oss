"""DB.S2a — SQL pushdown for the memory `all()` filter (cheap-first retrieval).

Before this stage every `all()` did `SELECT doc FROM memory ORDER BY seq` and then dropped rows in
a Python list comprehension — the WHOLE table over the wire, and through `from_dict`, on every
recall. DB.S2a pushes the `mtype`/`status` filter into a parameterized WHERE (+ an optional LIMIT
after the ORDER) on BOTH SQL backends, via one shared clause-builder.

Proven here:
  * RESULT-SET IDENTITY — for every filter combination, the pushed query returns the same items in
    the same order as the old fetch-all-then-filter path (run side by side on a seeded corpus);
  * both backends — SQLite always, and the Postgres SQL executed FOR REAL against a psycopg-shaped
    shim so its WHERE is exercised, not simulated;
  * project scoping UNCHANGED (Stage 71a) — project A never sees project B, including combined
    with the new mtype/status conditions;
  * LIMIT is ORDERED — `LIMIT n` returns the first n by `seq`, not an arbitrary n;
  * injection-safety — a hostile filter value is bound, never executed;
  * perf SHAPE (structural, not wall-clock) — with a filter given the SQL carries a WHERE on the
    column and the backend materializes only matching rows.

Scope note: the v3 scope/precedence columns are NOT touched here. No write path populates them
(every row carries the DDL default while the authoritative value lives in the doc), so filtering on
them would return wrong rows. Activating them needs a write-path backfill + schema-min bump —
that is DB.S2b, deferred.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import sqlite3
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.memory.backends import (
    PostgresBackend,
    SQLiteBackend,
    filter_clause_for,
)
from mokata.memory.item import ACTIVE, PERSISTENT, PROPOSED, STALE, MemoryItem

EPHEMERAL = "ephemeral"


# ------------------------------------------------------------------ the corpus + the ORACLE
def _corpus():
    """A seeded corpus spanning both filter axes, with duplicates on each so a filter has real
    work to do. Insertion order IS the expected `seq` order."""
    spec = [
        ("alpha", PERSISTENT, ACTIVE),
        ("bravo", EPHEMERAL, ACTIVE),
        ("charlie", PERSISTENT, PROPOSED),
        ("delta", PERSISTENT, ACTIVE),
        ("echo", EPHEMERAL, STALE),
        ("foxtrot", PERSISTENT, STALE),
        ("golf", EPHEMERAL, PROPOSED),
        ("hotel", PERSISTENT, ACTIVE),
    ]
    return [MemoryItem(subject=s, value=f"value of {s}", mtype=t, status=st, id=s)
            for s, t, st in spec]


# Every combination the callers actually use, plus the edge cases.
_COMBOS = [
    (None, None),
    (PERSISTENT, None),
    (EPHEMERAL, None),
    ("nonexistent-type", None),
    (None, (ACTIVE,)),
    (None, (PROPOSED,)),
    (None, (ACTIVE, STALE)),
    (None, (ACTIVE, PROPOSED, STALE)),
    (PERSISTENT, (ACTIVE,)),
    (EPHEMERAL, (ACTIVE, STALE)),
    (PERSISTENT, (PROPOSED,)),
    ("nonexistent-type", (ACTIVE,)),
    (None, ()),            # empty status tuple: matched nothing before, must match nothing now
]


def _python_filter_oracle(items, mtype=None, statuses=None):
    """The OLD path, verbatim: everything in seq order, then filtered in Python. This is the
    contract the pushdown must reproduce byte-for-byte."""
    out = list(items)
    if mtype is not None:
        out = [i for i in out if i.mtype == mtype]
    if statuses is not None:
        out = [i for i in out if i.status in statuses]
    return out


def _ids(items):
    return [i.id for i in items]


# ------------------------------------------------------------- a REAL SQL Postgres stand-in
class _PgShim:
    """Executes the Postgres backend's SQL for real, on SQLite (`%s` -> `?`).

    The existing project-scoping fake hand-parses SQL and so can only confirm the strings it was
    taught. A pushdown must be proven to FILTER, so this runs the emitted SQL through an actual SQL
    engine on a table shaped like the provisioned `mokata_memory`. Every statement is recorded for
    the structural assertions."""

    def __init__(self):
        self._c = sqlite3.connect(":memory:")
        self._c.execute(
            """CREATE TABLE mokata_memory (
                   seq INTEGER PRIMARY KEY AUTOINCREMENT,
                   id TEXT UNIQUE, mtype TEXT, subject TEXT, status TEXT, doc TEXT,
                   project TEXT, revision INTEGER NOT NULL DEFAULT 1,
                   scope_level TEXT NOT NULL DEFAULT 'personal', scope_id TEXT,
                   pin INTEGER NOT NULL DEFAULT 0, priority INTEGER NOT NULL DEFAULT 0
               )"""
        )
        self.sql_log = []

    def execute(self, sql, params=()):
        self.sql_log.append(sql)
        return self._c.execute(sql.replace("%s", "?"), tuple(params or ()))

    def close(self):
        self._c.close()

    # -- helpers ----------------------------------------------------------
    def last_select(self):
        return [s for s in self.sql_log if s.lstrip().upper().startswith("SELECT")][-1]

    def table_names(self):
        return {r[0] for r in self._c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}


def _seeded_sqlite(items):
    backend = SQLiteBackend(":memory:")
    for it in items:
        backend.put(it)
    return backend


def _seeded_pg(items, project="proj-a"):
    shim = _PgShim()
    backend = PostgresBackend(project=project, conn=shim)
    for it in items:
        backend.put(it)
    return backend, shim


# ================================================================== 1 · result-set identity
class PushdownMatchesPythonFilterTest(unittest.TestCase):
    """The correctness bar: same items, same order, every combination, both backends."""

    def test_db_s2a_pushdown_matches_python_filter(self):
        items = _corpus()
        sqlite_backend = _seeded_sqlite(items)
        pg_backend, _shim = _seeded_pg(items)
        self.addCleanup(sqlite_backend.close)
        self.addCleanup(pg_backend.close)

        for mtype, statuses in _COMBOS:
            expected = _python_filter_oracle(items, mtype, statuses)
            with self.subTest(backend="sqlite", mtype=mtype, statuses=statuses):
                self.assertEqual(
                    _ids(sqlite_backend.all(mtype=mtype, statuses=statuses)), _ids(expected))
            with self.subTest(backend="postgres", mtype=mtype, statuses=statuses):
                self.assertEqual(
                    _ids(pg_backend.all(mtype=mtype, statuses=statuses)), _ids(expected))

    def test_db_s2a_pushed_rows_are_whole_faithful_items(self):
        """Identity is about the ITEMS, not just their ids — the pushed row still round-trips
        through `from_dict` into the same object the Python path produced."""
        items = _corpus()
        backend = _seeded_sqlite(items)
        self.addCleanup(backend.close)

        got = backend.all(mtype=PERSISTENT, statuses=(ACTIVE,))
        expected = _python_filter_oracle(items, PERSISTENT, (ACTIVE,))
        self.assertEqual([i.to_dict() for i in got], [i.to_dict() for i in expected])

    def test_db_s2a_ordering_is_seq_not_insertion_of_matches(self):
        """`ORDER BY seq` survives the WHERE: a filter that skips rows must not reshuffle the
        survivors. `charlie` (seq 3) precedes `foxtrot` (seq 6) even though rows fall out between."""
        backend = _seeded_sqlite(_corpus())
        self.addCleanup(backend.close)
        self.assertEqual(
            _ids(backend.all(mtype=PERSISTENT, statuses=(PROPOSED, STALE))),
            ["charlie", "foxtrot"])

    def test_db_s2a_empty_status_tuple_matches_nothing(self):
        """`status IN ()` is a syntax error in both engines; the old Python path returned []. The
        builder must emit a constant-false condition, not raise."""
        backend = _seeded_sqlite(_corpus())
        self.addCleanup(backend.close)
        self.assertEqual(backend.all(statuses=()), [])

        pg_backend, _shim = _seeded_pg(_corpus())
        self.addCleanup(pg_backend.close)
        self.assertEqual(pg_backend.all(statuses=()), [])


# ============================================================ 2 · project scoping unchanged
class ProjectScopeIsolationTest(unittest.TestCase):
    """Stage 71a's project scoping is UNCHANGED by DB.S2a. A leak here would be cross-tenant, so
    it is re-proven against the new WHERE — including combined with the pushed conditions."""

    def _two_projects(self):
        shim = _PgShim()
        a = PostgresBackend(project="proj-a", conn=shim)
        b = PostgresBackend(project="proj-b", conn=shim)
        for it in _corpus():
            a.put(it)
        b.put(MemoryItem(subject="b-only", value="b", mtype=PERSISTENT,
                         status=ACTIVE, id="b-only"))
        return a, b, shim

    def test_db_s2a_scope_isolation(self):
        a, b, _shim = self._two_projects()
        self.assertEqual(_ids(b.all()), ["b-only"])
        self.assertNotIn("b-only", _ids(a.all()))
        for it_id in _ids(_corpus()):
            self.assertNotIn(it_id, _ids(b.all()))

    def test_db_s2a_scope_isolation_holds_under_every_filter(self):
        """The new conditions are ANDed ONTO the project clause — never instead of it."""
        a, b, _shim = self._two_projects()
        for mtype, statuses in _COMBOS:
            with self.subTest(mtype=mtype, statuses=statuses):
                for it in b.all(mtype=mtype, statuses=statuses):
                    self.assertEqual(it.id, "b-only")
                self.assertNotIn("b-only", _ids(a.all(mtype=mtype, statuses=statuses)))

    def test_db_s2a_project_clause_leads_the_params(self):
        """The project condition stays FIRST so its bound parameter keeps leading `params` — the
        ordering Stage 71a's callers and fakes rely on."""
        a, _b, shim = self._two_projects()
        a.all(mtype=PERSISTENT, statuses=(ACTIVE,))
        sql = " ".join(shim.last_select().split())
        self.assertIn("WHERE project=%s", sql)
        self.assertLess(sql.index("project=%s"), sql.index("mtype=%s"))

    def test_db_s2a_unscoped_backend_spans_projects(self):
        """`project=None` (review `--all`) still spans — the filter builder must open the WHERE
        itself when there is no project clause to hang off."""
        _a, _b, shim = self._two_projects()
        spanning = PostgresBackend(project=None, conn=shim)
        got = _ids(spanning.all(mtype=PERSISTENT, statuses=(ACTIVE,)))
        self.assertIn("b-only", got)
        self.assertIn("alpha", got)
        self.assertIn("WHERE mtype=%s", " ".join(shim.last_select().split()))


# ============================================================================ 3 · the LIMIT
class LimitIsOrderedTest(unittest.TestCase):
    """A LIMIT must apply AFTER the ORDER, so it is the right N."""

    def test_db_s2a_limit_is_ordered(self):
        items = _corpus()
        sqlite_backend = _seeded_sqlite(items)
        pg_backend, _shim = _seeded_pg(items)
        self.addCleanup(sqlite_backend.close)
        self.addCleanup(pg_backend.close)

        for backend in (sqlite_backend, pg_backend):
            with self.subTest(backend=backend.name):
                self.assertEqual(_ids(backend.all(limit=3)), ["alpha", "bravo", "charlie"])
                # ...and the seq-ordered N *of the filtered set*, not of the table.
                self.assertEqual(
                    _ids(backend.all(mtype=PERSISTENT, limit=2)), ["alpha", "charlie"])

    def test_db_s2a_limit_clause_follows_the_order_by(self):
        """Structural: were LIMIT emitted before ORDER BY, the engine would cap an unordered scan
        and hand back an arbitrary N."""
        _backend, shim = _seeded_pg(_corpus())
        _backend.all(limit=2)
        sql = " ".join(shim.last_select().split()).upper()
        self.assertLess(sql.index("ORDER BY SEQ"), sql.index("LIMIT"))

    def test_db_s2a_limit_beyond_corpus_returns_everything(self):
        backend = _seeded_sqlite(_corpus())
        self.addCleanup(backend.close)
        self.assertEqual(len(backend.all(limit=999)), len(_corpus()))

    def test_db_s2a_no_current_caller_passes_a_limit(self):
        """`limit` defaults to None, so DB.S2a cannot change any result TODAY. Every existing call
        site post-filters further in Python, where a pushed LIMIT would take the wrong N."""
        backend = _seeded_sqlite(_corpus())
        self.addCleanup(backend.close)
        self.assertEqual(_ids(backend.all()), _ids(_corpus()))


# ===================================================================== 4 · injection safety
class ParameterizationTest(unittest.TestCase):

    HOSTILE = "'; DROP TABLE memory;--"

    def test_db_s2a_hostile_filter_value_is_parameterized_not_executed(self):
        backend = _seeded_sqlite(_corpus())
        self.addCleanup(backend.close)

        # Compared as a VALUE: no row has this mtype, so the result is empty...
        self.assertEqual(backend.all(mtype=self.HOSTILE), [])
        # ...and, decisively, the table is still there with every row intact.
        self.assertEqual(len(backend.all()), len(_corpus()))

    def test_db_s2a_hostile_status_value_is_parameterized(self):
        backend = _seeded_sqlite(_corpus())
        self.addCleanup(backend.close)
        self.assertEqual(backend.all(statuses=(self.HOSTILE, ACTIVE)),
                         _python_filter_oracle(_corpus(), None, (self.HOSTILE, ACTIVE)))
        self.assertEqual(len(backend.all()), len(_corpus()))

    def test_db_s2a_hostile_value_cannot_drop_the_postgres_table(self):
        backend, shim = _seeded_pg(_corpus())
        self.addCleanup(backend.close)
        self.assertEqual(backend.all(mtype=self.HOSTILE), [])
        self.assertIn("mokata_memory", shim.table_names())

    def test_db_s2a_no_filter_value_reaches_the_sql_string(self):
        """The builder returns values as bound params; the SQL carries placeholders only."""
        clause, params = filter_clause_for(self.HOSTILE, (self.HOSTILE,), placeholder="?")
        self.assertNotIn("DROP", clause)
        self.assertNotIn(self.HOSTILE, clause)
        self.assertEqual(params, (self.HOSTILE, self.HOSTILE))
        self.assertEqual(clause.count("?"), 2)


# ======================================================================= 5 · the perf SHAPE
class PushdownShapeTest(unittest.TestCase):
    """Structural + deterministic — never wall-clock, which would flake."""

    def test_db_s2a_filtered_query_carries_a_where(self):
        _backend, shim = _seeded_pg(_corpus())
        for mtype, statuses, expect in (
            (PERSISTENT, None, "mtype=%s"),
            (None, (ACTIVE,), "status IN (%s)"),
            (None, (ACTIVE, STALE), "status IN (%s, %s)"),
        ):
            with self.subTest(mtype=mtype, statuses=statuses):
                _backend.all(mtype=mtype, statuses=statuses)
                self.assertIn(expect, " ".join(shim.last_select().split()))

    def test_db_s2a_backend_materializes_only_matching_rows(self):
        """The heart of the stage: with a filter given, the rows that come BACK from the engine are
        the matching ones — the whole table is no longer pulled into Python and thinned there."""
        items = _corpus()
        backend = _seeded_sqlite(items)
        self.addCleanup(backend.close)

        materialized = []
        real_conn = backend._mem_conn
        backend._mem_conn = _CountingConn(real_conn, materialized)
        self.addCleanup(lambda: setattr(backend, "_mem_conn", real_conn))

        expected = _python_filter_oracle(items, PERSISTENT, (ACTIVE,))
        got = backend.all(mtype=PERSISTENT, statuses=(ACTIVE,))

        self.assertEqual(_ids(got), _ids(expected))
        self.assertEqual(materialized, [len(expected)])
        self.assertLess(materialized[0], len(items))   # strictly fewer than the whole table

    def test_db_s2a_unfiltered_query_has_no_where(self):
        """No filter → no WHERE. The pushdown adds a clause only when there is one to add."""
        backend = _seeded_sqlite(_corpus())
        self.addCleanup(backend.close)
        clause, params = filter_clause_for(None, None, placeholder="?")
        self.assertEqual((clause, params), ("", ()))
        self.assertEqual(len(backend.all()), len(_corpus()))

    def test_db_s2a_builder_is_the_one_source_for_both_backends(self):
        """Backends differ ONLY in the placeholder token and the prefix — not in semantics."""
        sqlite_clause, sqlite_params = filter_clause_for(
            PERSISTENT, (ACTIVE, STALE), placeholder="?")
        pg_clause, pg_params = filter_clause_for(
            PERSISTENT, (ACTIVE, STALE), placeholder="%s")
        self.assertEqual(sqlite_params, pg_params)
        self.assertEqual(sqlite_clause.replace("?", "%s"), pg_clause)
        # ...and the prefix switches to AND when a project clause already opened the WHERE.
        anded, _ = filter_clause_for(PERSISTENT, None, placeholder="%s", prefix="AND")
        self.assertTrue(anded.startswith(" AND "))


class _Rows:
    """Minimal cursor stand-in so the spy can hand back rows it already drained."""

    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _CountingConn:
    """Wraps a live sqlite3 connection to record HOW MANY rows each `SELECT doc` actually returned.
    A proxy rather than a monkeypatched method because `sqlite3.Connection.execute` is a read-only
    C attribute. Everything else delegates, so the backend cannot tell."""

    def __init__(self, conn, sink):
        self._conn = conn
        self._sink = sink

    def execute(self, sql, params=()):
        cur = self._conn.execute(sql, params)
        if sql.lstrip().upper().startswith("SELECT DOC"):
            rows = cur.fetchall()
            self._sink.append(len(rows))
            return _Rows(rows)
        return cur

    def __getattr__(self, attr):
        return getattr(self._conn, attr)


# ================================================================ 6 · non-SQL backends hold
class NonSqlBackendsUnchangedTest(unittest.TestCase):
    """Obsidian and native-memory are not queryable stores — they keep the Python filter. The
    CONTRACT (including `limit`) stays uniform so no caller branches on backend."""

    def test_db_s2a_obsidian_filter_and_limit_still_correct(self):
        from mokata.memory.backends import ObsidianBackend
        with tempfile.TemporaryDirectory() as vault:
            backend = ObsidianBackend(vault)
            for it in _corpus():
                backend.put(it)
            got = backend.all(mtype=PERSISTENT, statuses=(ACTIVE,))
            self.assertEqual({i.id for i in got}, {"alpha", "delta", "hotel"})
            self.assertEqual(len(backend.all(limit=2)), 2)

    def test_db_s2a_native_filter_and_limit_still_correct(self):
        from mokata.memory.backends import NativeMemoryBackend

        class _Client:
            def __init__(self):
                self.docs = {}

            def put(self, doc):
                self.docs[doc["id"]] = doc

            def get(self, item_id):
                return self.docs.get(item_id)

            def all(self):
                return list(self.docs.values())

            def delete(self, item_id):
                return self.docs.pop(item_id, None) is not None

        backend = NativeMemoryBackend(_Client())
        for it in _corpus():
            backend.put(it)
        self.assertEqual({i.id for i in backend.all(mtype=PERSISTENT, statuses=(ACTIVE,))},
                         {"alpha", "delta", "hotel"})
        self.assertEqual(len(backend.all(limit=3)), 3)


# ==================================================== 7 · scope columns deliberately untouched
class ScopeColumnsNotPushedTest(unittest.TestCase):
    """DB.S2b's guard rail. No write path populates the v3 scope columns, so a row's `scope_level`
    column is the DDL default regardless of what its doc says. If a future edit pushes scope into
    the WHERE without first fixing the write path, this test fails LOUDLY rather than shipping a
    cross-tenant visibility bug."""

    def test_db_s2a_does_not_filter_on_scope_columns(self):
        backend, shim = _seeded_pg(_corpus())
        self.addCleanup(backend.close)
        backend.all(mtype=PERSISTENT, statuses=(ACTIVE,))
        sql = shim.last_select().lower()
        for column in ("scope_level", "scope_id", "pin", "priority"):
            self.assertNotIn(column, sql)

    def test_db_s2a_scope_column_is_stale_versus_the_doc(self):
        """The evidence for the deferral, pinned as a test: a team-scoped item's DOC says `team`
        while its stored COLUMN still says `personal`. Filtering on the column would lose the row.
        When DB.S2b fixes the write path, this test should be updated to assert they AGREE."""
        shim = _PgShim()
        backend = PostgresBackend(project="proj-a", conn=shim)
        item = MemoryItem(subject="team-rule", value="v", mtype=PERSISTENT,
                          status=ACTIVE, id="team-rule", scope_level="team", scope_id="t1")
        backend.put(item)

        row = shim._c.execute(
            "SELECT scope_level, doc FROM mokata_memory WHERE id=?", ("team-rule",)).fetchone()
        self.assertEqual(row[0], "personal")                    # the column: never written
        self.assertEqual(json.loads(row[1])["scope_level"], "team")   # the doc: authoritative
        # ...and the item still reads back correctly, because reads use the doc.
        self.assertEqual(backend.get("team-rule").scope_level, "team")


# ============================================================= 8 · the live Postgres leg (opt-in)
# GR.S2-FU — run by `.github/workflows/live-db-legs.yml` (opt-in: workflow_dispatch + weekly cron).
@unittest.skipUnless(os.environ.get("MOKATA_TEST_DSN"),
                     "no MOKATA_TEST_DSN — the live Postgres leg is opt-in (the `_PgShim` proves "
                     "the emitted SQL's shape on SQLite; only real psycopg proves psycopg)")
class LivePostgresPushdownTest(unittest.TestCase):
    """DB.S2a deviation (1), closed. The shim EXECUTES the real emitted SQL, but on SQLite with
    `%s` rewritten to `?` — so what it cannot prove is psycopg itself: that `%s` binding, the
    tuple-vs-list parameter handling for `status = ANY(...)`/`IN`, and PG's own `ORDER BY seq`
    reproduce the Python oracle exactly. That is what this leg proves, against a real server."""

    PROJECT = "db-s2a-live"

    def setUp(self):
        from mokata import teamdb
        dsn = os.environ["MOKATA_TEST_DSN"]
        teamdb.provision(dsn)
        self.backend = PostgresBackend(dsn, project=self.PROJECT)
        self.addCleanup(self.backend.close)
        self.items = _corpus()
        for it in self.items:
            self.backend.put(it)
        self.addCleanup(self._purge)

    def _purge(self):
        for it in self.items:
            self.backend.delete(it.id)

    def test_db_s2a_live_pushdown_matches_the_python_oracle(self):
        """Every filter combination the callers use, against REAL psycopg — same items, same
        order as the old fetch-all-then-filter path. A red here means the pushed WHERE diverges
        on the actual engine, not on the shim."""
        for mtype, statuses in _COMBOS:
            with self.subTest(mtype=mtype, statuses=statuses):
                got = _ids(self.backend.all(mtype=mtype, statuses=statuses))
                want = _ids(_python_filter_oracle(self.items, mtype=mtype, statuses=statuses))
                self.assertEqual(got, want,
                                 f"live Postgres diverged from the oracle for "
                                 f"mtype={mtype!r} statuses={statuses!r}")

    def test_db_s2a_live_limit_is_ordered_by_seq(self):
        """`LIMIT n` after `ORDER BY seq` must be the FIRST n by insertion order on the real
        engine — an unordered limit returns an arbitrary n and no test would notice."""
        self.assertEqual(_ids(self.backend.all(limit=3)), _ids(self.items)[:3])

    def test_db_s2a_live_hostile_filter_value_is_bound_not_executed(self):
        """Injection safety proven where it matters — through psycopg's own parameter binding."""
        hostile = "'; DROP TABLE mokata_memory; --"
        self.assertEqual(self.backend.all(mtype=hostile), [])
        # the table survived, i.e. the value was a VALUE
        self.assertEqual(_ids(self.backend.all()), _ids(self.items))


if __name__ == "__main__":
    unittest.main()
