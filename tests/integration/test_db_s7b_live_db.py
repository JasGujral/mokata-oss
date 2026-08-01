"""DB.S7b — the ≤2-hop traversal against a REAL Postgres, beside a REAL SQLite.

The unit suite proves the walk's semantics, its bounds and its prunes against SQLite. Exactly ONE
of this slice's claims is not provable there, and it is the load-bearing one:

  * **SQLite and Postgres return the SAME walk.** K1's traversal is a recursive CTE, and "both
    dialects agree" is a statement about two ENGINES. No double can make it — and the usual double
    is worse than useless here: the `_PgShim` the pushdown/FTS suites use executes the Postgres
    backend's SQL on real SQLite (`%s` → `?`), so it runs a recursive CTE perfectly well and would
    report GREEN while comparing SQLite against itself. That is the SI.6-DELEGATED-BLINDNESS shape:
    a pass that reads as coverage it does not have. The unit suite carries a guard forbidding the
    shim in any DB.S7b traversal test; THIS file is the thing that guard points at.

Two further claims ride the same live connection because they are equally engine-facts:

  * **the anchor's types are DECLARED, not inferred.** A recursive CTE anchored on `SELECT ?, 0`
    passes on dynamically-typed SQLite and fails on Postgres. The shape shipped here takes every
    text column from a real column and CASTs the one literal — which is only *proven* by Postgres
    accepting it.

  * **the plan is INDEX-BOUND (doc 55:83, "no seq scans").** An `EXPLAIN` is the only place that
    claim exists.

Gate is the same explicit contract as the other live-DB legs: MOKATA_LIVE_DB=1 + MOKATA_PG_DSN +
psycopg + a reachable DB, else these skip cleanly.

**CI DOES NOT RUN THIS.** The live-DB lane is billing-blocked, so every claim in this file is
on-device evidence only — and a skipped live leg reads GREEN, which is precisely why that is
written here rather than assumed.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import sqlite3
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from test_db_s6_live_db import _PG_LIVE, _PG_REASON, _pg_dsn   # noqa: F401

from mokata.memory import edges as E
from mokata.memory import expansion as X

_PROJECT = "db-s7b-live"

# The fixture graph, written to BOTH engines byte-identically. It carries, deliberately:
#   * a 3-hop chain  (a → b → c → d)         — so the ≤2-hop bound has something to refuse;
#   * a CLOSED edge  (a ⇥ x)                 — so the open-window filter has something to skip;
#   * a cycle        (b → a)                 — so `UNION ALL`'s non-dedup has something to return;
#   * a second kind  (b → e, decided_in)     — so the walk is not proven on one kind only;
#   * a fan-out      (a → f, a → g)          — so ordering has something to order.
_OPEN = None
_CLOSED = "2026-01-01T00:00:00+00:00"
_EDGES = (
    ("a", "b", E.DEPENDS_ON, _OPEN),
    ("b", "c", E.DEPENDS_ON, _OPEN),
    ("c", "d", E.DEPENDS_ON, _OPEN),
    ("b", "a", E.DEPENDS_ON, _OPEN),
    ("b", "e", E.DECIDED_IN, _OPEN),
    ("a", "f", E.SUPERSEDES, _OPEN),
    ("a", "g", E.ABOUT_CODE, _OPEN),
    ("a", "x", E.DEPENDS_ON, _CLOSED),
    ("c", "y", E.DEPENDS_ON, _CLOSED),
)
_SEEDS = ["a"]


def _pg_conn(dsn):
    from mokata.memory import _pg
    return _pg.get_connection(dsn, RuntimeError)


def _sqlite_walk(seeds, max_hops=X.MAX_HOPS):
    """The fixture on a REAL SQLite, walked by the real builder."""
    conn = sqlite3.connect(":memory:")
    cols = ", ".join(E.EDGE_COLUMNS)
    conn.execute(f"CREATE TABLE {E.LOCAL_EDGES_TABLE} ("
                 f"{E.SRC_COLUMN} TEXT, {E.DST_COLUMN} TEXT, {E.KIND_COLUMN} TEXT, "
                 f"{E.VALID_FROM_COLUMN} TEXT, {E.VALID_TO_COLUMN} TEXT, "
                 f"{E.CREATED_AT_COLUMN} TEXT, {E.CREATED_BY_COLUMN} TEXT, "
                 f"{E.APPROVAL_LEDGER_COLUMN} INTEGER)")
    conn.execute(f"CREATE UNIQUE INDEX {E.LOCAL_EDGES_TABLE}_open ON {E.LOCAL_EDGES_TABLE} "
                 f"({E.SRC_COLUMN}, {E.DST_COLUMN}, {E.KIND_COLUMN}) "
                 f"WHERE {E.VALID_TO_COLUMN} IS NULL")
    for src, dst, kind, closed in _EDGES:
        conn.execute(f"INSERT INTO {E.LOCAL_EDGES_TABLE} ({cols}) VALUES (?,?,?,?,?,?,?,?)",
                     (src, dst, kind, "2025-01-01T00:00:00+00:00", closed,
                      "2025-01-01T00:00:00+00:00", "t", None))
    conn.commit()
    sql = X.expansion_sql(E.LOCAL_EDGES_TABLE, len(seeds))
    rows = [tuple(r) for r in conn.execute(sql, X.expansion_params(seeds, max_hops)).fetchall()]
    plan = [tuple(r) for r in conn.execute(
        f"EXPLAIN QUERY PLAN {sql}", X.expansion_params(seeds, max_hops)).fetchall()]
    conn.close()
    return rows, plan


@unittest.skipUnless(_PG_LIVE, _PG_REASON)
class _LiveEdgeCase(unittest.TestCase):
    """The fixture on a REAL Postgres, in the table `team init` really provisions."""

    def setUp(self):
        from mokata import teamdb
        self.dsn = _pg_dsn()
        self._saved = os.environ.get("MOKATA_PG_DSN")
        teamdb.provision(self.dsn)                      # idempotent DDL (the only DDL path)
        conn = _pg_conn(self.dsn)
        conn.execute(f"DELETE FROM {teamdb.EDGES_TABLE}")
        cols = ", ".join(E.EDGE_COLUMNS)
        for src, dst, kind, closed in _EDGES:
            conn.execute(
                f"INSERT INTO {teamdb.EDGES_TABLE} ({cols}) "
                f"VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (src, dst, kind, "2025-01-01T00:00:00+00:00", closed,
                 "2025-01-01T00:00:00+00:00", "t", None))

    def tearDown(self):
        from mokata.memory import _pg
        _pg.reset_manager()
        if self._saved is None:
            os.environ.pop("MOKATA_PG_DSN", None)
        else:
            os.environ["MOKATA_PG_DSN"] = self._saved

    def _pg_walk(self, seeds, max_hops=X.MAX_HOPS):
        from mokata import teamdb
        sql = X.expansion_sql(teamdb.EDGES_TABLE, len(seeds), placeholder="%s")
        rows = _pg_conn(self.dsn).execute(sql, X.expansion_params(seeds, max_hops)).fetchall()
        return [tuple(r) for r in rows]


# ======================================================================================
# P2 · THE TWO ENGINES AGREE — the claim no double can make
# ======================================================================================
class TwoEngineAgreementTest(_LiveEdgeCase):
    """SQLite and Postgres return the SAME walked edges on the SAME graph.

    Compared as a SORTED sequence, and that is a deliberate strengthening rather than a dodge:
    SQLite sorts text by BINARY collation while Postgres sorts by the database's (typically
    `en_US.UTF-8`, which weights punctuation differently at the first level), so two engines
    holding identical rows may legitimately emit them in different orders. The ordering the caller
    SEES is imposed by `expansion.walk_paths` in Python — so what must agree here is the DATA, and
    the ordering agreement is pinned separately on mokata's own sort, where it is a real claim
    rather than a statement about two collation tables.
    """

    def test_identical_walked_edges(self):
        sqlite_rows, _ = _sqlite_walk(_SEEDS)
        pg_rows = self._pg_walk(_SEEDS)
        self.assertEqual(sorted(sqlite_rows), sorted(pg_rows),
                         "the two engines walked DIFFERENT graphs from identical rows")
        self.assertTrue(sqlite_rows, "the fixture produced no walk at all — nothing was proven")

    def test_identical_after_mokata_own_sort(self):
        """The order the caller sees comes from `walk_paths`, so THAT must agree byte for byte."""
        sqlite_rows, _ = _sqlite_walk(_SEEDS)
        pg_rows = self._pg_walk(_SEEDS)
        visible = {"a", "b", "c", "d", "e", "f", "x", "y"}    # `g` is a code path, not an item
        left = X.walk_paths(sqlite_rows, seeds=_SEEDS, visible=visible)
        right = X.walk_paths(pg_rows, seeds=_SEEDS, visible=visible)
        self.assertEqual({k: (v.render(), v.depth, repr(v.weight)) for k, v in left.paths.items()},
                         {k: (v.render(), v.depth, repr(v.weight)) for k, v in right.paths.items()})

    def test_identical_at_every_hop_bound(self):
        """One hop and two, so agreement is not an accident of the default."""
        for hops in (1, 2):
            sqlite_rows, _ = _sqlite_walk(_SEEDS, hops)
            self.assertEqual(sorted(sqlite_rows), sorted(self._pg_walk(_SEEDS, hops)),
                             f"the engines diverged at max_hops={hops}")

    def test_identical_on_a_multi_seed_walk(self):
        seeds = ["a", "b", "c"]
        sqlite_rows, _ = _sqlite_walk(seeds)
        self.assertEqual(sorted(sqlite_rows), sorted(self._pg_walk(seeds)))


# ======================================================================================
# P1/P3 ON THE REAL ENGINE — the bound and the window, decided by Postgres
# ======================================================================================
class LiveBoundAndWindowTest(_LiveEdgeCase):
    """The bound and the open-window filter, enforced by the engine rather than by Python."""

    def test_the_third_hop_is_absent_on_postgres(self):
        reached = {row[2] for row in self._pg_walk(_SEEDS)}
        self.assertIn("b", reached)                          # 1 hop
        self.assertIn("c", reached)                          # 2 hops
        self.assertNotIn("d", reached, "Postgres walked a THIRD hop — the bound is broken")

    def test_closed_edges_are_absent_on_postgres(self):
        rows = self._pg_walk(_SEEDS)
        self.assertNotIn("x", {r[2] for r in rows}, "a CLOSED edge was walked from the anchor")
        self.assertNotIn("y", {r[2] for r in rows}, "a CLOSED edge was walked in the recursive term")

    def test_the_closed_rows_are_still_on_disk(self):
        """R3 — closing is never deleting. The history the walk skips must still be there."""
        from mokata import teamdb
        got = _pg_conn(self.dsn).execute(
            f"SELECT count(*) FROM {teamdb.EDGES_TABLE} "
            f"WHERE {E.VALID_TO_COLUMN} IS NOT NULL").fetchone()
        self.assertEqual(2, got[0])

    def test_the_cycle_really_comes_back_from_postgres(self):
        """`UNION ALL` does not dedupe on Postgres either — the Python guard is not defending
        against a hypothetical."""
        self.assertIn(("a", "b", "a", E.DEPENDS_ON, 2), self._pg_walk(_SEEDS))

    def test_postgres_accepts_the_anchor_without_inferring_a_type(self):
        """The shape that fails when the anchor is `SELECT ?, 0`. Postgres accepting it IS the
        proof — SQLite would accept either."""
        self.assertTrue(self._pg_walk(_SEEDS), "the anchor did not execute on Postgres")


# ======================================================================================
# INDEX-BOUND — doc 55:83, "no seq scans"
# ======================================================================================
class IndexBoundPlanTest(_LiveEdgeCase):
    """The `EXPLAIN` is the only place "index-bound" exists as evidence rather than intent.

    **A caveat stated rather than buried:** on a table of nine rows Postgres will prefer a seq scan
    whatever indexes exist, because scanning nine rows is cheaper than descending a btree. So this
    seeds enough rows for the planner to have a real choice, and asserts on THAT plan — a
    ten-row assertion would be measuring the planner's small-table shortcut, not mokata's SQL.
    """

    ROWS = 5000

    def _bulk(self):
        from mokata import teamdb
        conn = _pg_conn(self.dsn)
        cols = ", ".join(E.EDGE_COLUMNS)
        for n in range(self.ROWS):
            conn.execute(f"INSERT INTO {teamdb.EDGES_TABLE} ({cols}) "
                         f"VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                         (f"bulk{n}", f"bulk{n + 1}", E.DEPENDS_ON,
                          "2025-01-01T00:00:00+00:00", None,
                          "2025-01-01T00:00:00+00:00", "t", None))
        conn.execute(f"ANALYZE {teamdb.EDGES_TABLE}")

    def test_postgres_plan_uses_the_index(self):
        from mokata import teamdb
        self._bulk()
        sql = X.expansion_sql(teamdb.EDGES_TABLE, 1, placeholder="%s")
        plan = "\n".join(r[0] for r in _pg_conn(self.dsn).execute(
            f"EXPLAIN {sql}", X.expansion_params(["bulk0"], X.MAX_HOPS)).fetchall())
        self.assertIn("Index", plan, f"the traversal is not index-bound:\n{plan}")
        self.assertNotIn("Seq Scan", plan, f"the traversal seq-scans the edge table:\n{plan}")

    def test_sqlite_plan_uses_the_index(self):
        _, plan = _sqlite_walk(_SEEDS)
        text = "\n".join(str(r[-1]) for r in plan)
        self.assertIn("USING", text, f"SQLite is not using an index:\n{text}")
        self.assertNotIn("SCAN memory_edges\n", text + "\n", f"SQLite seq-scans:\n{text}")


# ======================================================================================
# THE FULL READ PATH, on the shared store
# ======================================================================================
class LiveRecallTest(_LiveEdgeCase):
    """`PostgresBackend.expand_from` against the real table — the seam a recall actually calls."""

    def test_the_backend_seam_walks_on_postgres(self):
        from mokata.memory.backends import PostgresBackend
        backend = PostgresBackend(project=_PROJECT, conn=_pg_conn(self.dsn))
        self.assertTrue(backend.supports_edges)
        rows = [tuple(r) for r in backend.expand_from(["a"], X.MAX_HOPS)]
        self.assertEqual(sorted(rows), sorted(self._pg_walk(_SEEDS)))

    def test_a_v4_store_degrades_to_no_expansion(self):
        """An un-migrated team must get `[]`, not a raise over a table it never provisioned."""
        from mokata.memory.backends import PostgresBackend
        backend = PostgresBackend(project=_PROJECT, conn=_pg_conn(self.dsn))
        backend._edges_ready = False                     # what a v4 probe answers
        self.assertEqual([], backend.expand_from(["a"], X.MAX_HOPS))


if __name__ == "__main__":                                    # pragma: no cover
    unittest.main()
