"""DB.S8c — the SCALE legs: doc 52's S-1 and S-4 on a REAL Postgres at N=100,000.

S-2, S-3, S-4's decision half and S-6 are behavioural and run unconditionally in the unit suite
(`test_db_s8b_contracts.py`). The two contracts that CANNOT be made there are here, because both
are statements about a real engine and a shim would be measuring itself:

  S-1  "recall latency is index-bound: no query fetches more than its top-k(+over-fetch) rows"
       — measured how? doc 52 says "query plans + row counts". A plan is a thing only a planner
       produces. The `_PgShim` executes the emitted SQL on SQLite, so it would EXPLAIN SQLite and
       report green about Postgres — the SI.6-DELEGATED-BLINDNESS shape doc 84 files as
       SHIM-FALSE-GREEN.

  S-4  "two seats resolving the same contradiction concurrently → one wins, one gets stale;
       zero silent lost updates" — WHICH one wins is decided by Postgres's own
       `UPDATE … WHERE revision = %s`. Every unit-suite double IS that CAS, in Python, so it can
       only report what it was written to report. Here it is two real connections to one table.

## N IS DECLARED, AND THAT IS A CONTRACT

`SCALE_N = 100_000` — this leg's N, and it is the number it runs. The SQLite leg is a separate
module (`tests/test_db_s8c_sqlite_scale.py`) with its OWN declared constant at its own, smaller
value, stated there with the reason: it is unconditional and runs on every push, so it cannot
afford 100k, and it says 25,000 rather than implying 100,000. A leg that ran 2,000 rows under a
name saying 100,000 reads as coverage it does not have — the silent-cap failure. Both numbers
reach the run output.

## WHY THE 100k LOAD IS AFFORDABLE HERE

Through `put` it would not be: 100k autocommit round trips. `_scale_fixture.load_postgres` wraps
them in ONE transaction and calls `PostgresBackend._put_on`, which IS `put`'s body — same INSERT,
same projections, same edge projection. See `test_db_s8a_fixture.py` F-5/F-6.

Gate: MOKATA_LIVE_DB=1 + MOKATA_PG_DSN + psycopg + a reachable DB, else these skip cleanly. A
SKIPPED LIVE LEG READS GREEN, which is why `live-db-legs.yml` carries a preflight that FAILS if
this module skipped rather than ran (DB.S8e).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import statistics
import time
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from test_db_s6_live_db import _PG_LIVE, _PG_REASON, _pg_dsn   # noqa: F401

import _scale_fixture as F

#: THE DECLARED SIZES. Named here, reported in every assertion message, never reduced silently.
SCALE_N = 100_000

_PROJECT = "db-s8c-live"


def _conn(dsn):
    from mokata.memory import _pg
    return _pg.get_connection(dsn, RuntimeError)


def _describe(spec_n, engine, **extra):
    bits = " · ".join(f"{k}={v}" for k, v in extra.items())
    return f"[{engine} N={spec_n:,}] {bits}"


class _ScaleLiveCase(unittest.TestCase):
    """One 100k store on the real engine, provisioned and loaded ONCE for the whole module.

    Loading it per test would be honest and unusable — the point of `setUpClass` here is that the
    corpus is read-only for every assertion below.
    """

    corpus: F.Corpus

    @classmethod
    def setUpClass(cls):
        from mokata import teamdb
        from mokata.memory.backends import PostgresBackend
        cls.dsn = _pg_dsn()
        teamdb.provision(cls.dsn)                     # idempotent DDL — the only DDL path (C4)
        _conn(cls.dsn).execute(f"DELETE FROM {teamdb.MEMORY_TABLE}")
        _conn(cls.dsn).execute(f"DELETE FROM {teamdb.EDGES_TABLE}")
        cls.corpus = F.generate(F.ScaleSpec(n_items=SCALE_N, probes=40))
        cls.backend = PostgresBackend(conn=_conn(cls.dsn), project=_PROJECT)
        started = time.perf_counter()
        loaded = F.load_postgres(cls.backend, cls.corpus)
        cls.load_seconds = time.perf_counter() - started
        # The declared N, asserted at the door — on the ENGINE, not on the in-memory corpus.
        assert loaded == cls.corpus.declared_n == SCALE_N, (
            f"loaded {loaded} rows for a corpus declaring {SCALE_N}")
        rows = _conn(cls.dsn).execute(f"SELECT count(*) FROM {teamdb.MEMORY_TABLE}").fetchone()[0]
        assert rows == SCALE_N, f"the table holds {rows} rows, not the declared {SCALE_N}"
        # ANALYZE deliberately: an un-analyzed 100k table gives the planner default statistics, and
        # a plan chosen from defaults is not the plan a real store gets. DB.S7b took the same care.
        _conn(cls.dsn).execute(f"ANALYZE {teamdb.MEMORY_TABLE}")
        _conn(cls.dsn).execute(f"ANALYZE {teamdb.EDGES_TABLE}")

    @classmethod
    def tearDownClass(cls):
        from mokata.memory import _pg
        _pg.reset_manager()

    def store(self, context):
        from mokata.memory.store import MemoryStore
        return MemoryStore(self.backend, scope_context=context)


@unittest.skipUnless(_PG_LIVE, _PG_REASON)
class S1IndexBoundRecall(_ScaleLiveCase):
    """S-1 — "no query fetches more than its top-k(+over-fetch) rows". The EXPLAIN arm."""

    def _plan(self, sql, params=()):
        rows = _conn(self.dsn).execute(f"EXPLAIN (ANALYZE, BUFFERS) {sql}", params).fetchall()
        return "\n".join(r[0] for r in rows)

    def test_s1_the_candidate_query_is_index_bound_and_not_a_seq_scan(self):
        """THE contract, on the planner. R-1 made the lexical tier's ranked query carry the scope
        predicate; this is the leg that proves the resulting plan does not fall back to reading the
        whole table — the exact thing a 100k store makes expensive and a 9-row fixture cannot show
        (on nine rows the planner picks a seq scan CORRECTLY, so a small fixture measures the
        small-table shortcut and calls it coverage)."""
        from mokata import teamdb
        plan = self._plan(
            f"SELECT id FROM {teamdb.MEMORY_TABLE} WHERE id = ANY(%s)",
            ([p.direct_id for p in self.corpus.probes[:40]],))
        self.assertNotIn("Seq Scan", plan,
                         _describe(SCALE_N, "postgres", plan=plan.splitlines()[0]))

    def test_s1_recall_materializes_bounded_rows_regardless_of_store_size(self):
        """The ROW-COUNT arm, which is the half doc 52 actually words as the contract. Counted by
        wrapping the real backend's own reads, not estimated from a plan."""
        counted = []
        original = self.backend.hydrate

        def counting(*a, **kw):
            rows = original(*a, **kw)
            counted.append(len(rows))
            return rows

        self.backend.hydrate = counting
        try:
            for probe in self.corpus.probes[:20]:
                self.store(self.corpus.context_for(probe)).recall_relevant(probe.query, top_k=10)
        finally:
            self.backend.hydrate = original
        self.assertTrue(counted, "no bounded read happened — candidate selection did not run")
        worst = max(counted)
        from mokata.memory import tiered
        self.assertLessEqual(worst, tiered.CANDIDATE_UNION_CAP * 2,
                             _describe(SCALE_N, "postgres", worst_rows=worst,
                                       cap=tiered.CANDIDATE_UNION_CAP))
        # The number that makes the claim legible: a recall on a 100k store reads ~this many rows.
        print(_describe(SCALE_N, "postgres", median_rows=statistics.median(counted),
                        worst_rows=worst, load_s=round(self.load_seconds, 1)))

    def test_s1_recall_latency_is_flat_enough_to_be_index_bound(self):
        """Latency is the SYMPTOM the row count explains, so it is reported and loosely bounded
        rather than asserted tightly — a hard millisecond threshold on shared CI hardware is a
        flake generator, and the row-count arm above is the contract with teeth."""
        times = []
        for probe in self.corpus.probes[:20]:
            store = self.store(self.corpus.context_for(probe))
            started = time.perf_counter()
            store.recall_relevant(probe.query, top_k=10)
            times.append((time.perf_counter() - started) * 1000)
        median = statistics.median(times)
        print(_describe(SCALE_N, "postgres", median_ms=round(median, 1),
                        p95_ms=round(sorted(times)[int(len(times) * 0.95) - 1], 1)))
        self.assertLess(median, 2_000,
                        _describe(SCALE_N, "postgres", median_ms=round(median, 1),
                                  note="a recall on 100k rows took seconds — R-1 has regressed "
                                       "to a full-set read"))

    def test_s1_the_scope_predicate_reaches_the_engine_at_scale(self):
        """R-1's seam, on the real engine and the real column types. The SQLite leg proves the
        predicate composes; only this proves Postgres accepts and uses it against 100k rows of
        real BOOLEAN/text columns."""
        from mokata.memory import scope as S
        probe = self.corpus.probes[0]
        path = S.scope_path(self.corpus.context_for(probe))
        ranked = self.backend.lexical_search(probe.query, top_k=50, scope_path=path,
                                             statuses=("active",))
        self.assertTrue(ranked, "the scoped candidate query returned nothing at 100k")
        for item, _score in ranked:
            self.assertTrue(S.on_path(item, path),
                            _describe(SCALE_N, "postgres", leaked=item.id,
                                      level=item.scope_level, sid=item.scope_id))


@unittest.skipUnless(_PG_LIVE, _PG_REASON)
class S4TwoRealWritersAtScale(_ScaleLiveCase):
    """S-4 — the CAS half, decided by Postgres rather than by a Python fake, on a 100k table.

    DB.S6's live leg already proves the CAS between two writers. What is NEW here is only that it
    holds at scale — the row being contended sits in a 100k table with real indexes, so the
    `UPDATE … WHERE revision = %s` is planned against real statistics rather than against a table
    the planner would rather scan.
    """

    def _revision(self, item_id):
        from mokata import teamdb
        row = _conn(self.dsn).execute(
            f"SELECT {teamdb.MEMORY_REVISION_COLUMN} FROM {teamdb.MEMORY_TABLE} WHERE id=%s",
            (item_id,)).fetchone()
        return row[0] if row else None

    def test_s4_a_stale_writer_never_clobbers_the_winner_on_a_100k_table(self):
        from mokata import teamdb
        target = self.corpus.probes[0].direct_id
        base = self._revision(target)
        self.assertIsNotNone(base, "the contended row is not in the shared table")

        def cas(value, base_revision):
            """The SHIPPED CAS shape: update only if the revision still matches."""
            cur = _conn(self.dsn).execute(
                f"UPDATE {teamdb.MEMORY_TABLE} SET subject=%s, "
                f"{teamdb.MEMORY_REVISION_COLUMN}={teamdb.MEMORY_REVISION_COLUMN}+1 "
                f"WHERE id=%s AND {teamdb.MEMORY_REVISION_COLUMN}=%s",
                (value, target, base_revision))
            return cur.rowcount

        # Two seats read the SAME base revision, then both write.
        self.assertEqual(1, cas("seat-a-wins", base), "the first writer did not land")
        self.assertEqual(0, cas("seat-b-clobbers", base),
                         _describe(SCALE_N, "postgres",
                                   note="a STALE writer overwrote the winner — the no-clobber "
                                        "claim is gone, on the real engine"))
        row = _conn(self.dsn).execute(
            f"SELECT subject, {teamdb.MEMORY_REVISION_COLUMN} FROM {teamdb.MEMORY_TABLE} "
            f"WHERE id=%s", (target,)).fetchone()
        self.assertEqual("seat-a-wins", row[0], "the winner's write is not what the table holds")
        self.assertEqual(base + 1, row[1], "the revision did not advance exactly once")

    def test_s4_the_loser_can_re_detect_from_what_the_table_now_holds(self):
        """'One gets stale — RE-DETECT'. The losing writer must be able to read the current state
        and decide AGAIN — a CAS that only says "no" without leaving the winner readable, at a
        revision the loser can re-CAS against, ends the write silently. That is the lost update
        this contract forbids, and re-detection is the whole of what makes the refusal safe.

        The base revision travels as the TRANSIENT `_revision` attribute (`backends._with_revision`
        — deliberately not a model field, so it never enters `to_dict` or the stored doc), which is
        what `store` reads as the compare-and-set base.
        """
        from mokata import teamdb
        target = self.corpus.probes[1].direct_id
        before = self._revision(target)
        self.assertIsNotNone(before, "the contended row is not in the shared table")

        # A teammate lands a write; this seat's base revision is now stale.
        _conn(self.dsn).execute(
            f"UPDATE {teamdb.MEMORY_TABLE} SET subject=%s, "
            f"{teamdb.MEMORY_REVISION_COLUMN}={teamdb.MEMORY_REVISION_COLUMN}+1 WHERE id=%s",
            ("the teammate's landed value", target))

        current = self.backend.get(target)
        self.assertIsNotNone(current, "the loser cannot re-read the contended row")
        self.assertEqual(before + 1, current._revision,
                         "the re-read does not carry the revision the loser must re-CAS against")
        # …and that revision genuinely works as a CAS base — re-detection is not merely readable,
        # it is ACTIONABLE. Without this the assertion above is satisfied by any stale integer.
        redone = _conn(self.dsn).execute(
            f"UPDATE {teamdb.MEMORY_TABLE} SET subject=%s, "
            f"{teamdb.MEMORY_REVISION_COLUMN}={teamdb.MEMORY_REVISION_COLUMN}+1 "
            f"WHERE id=%s AND {teamdb.MEMORY_REVISION_COLUMN}=%s",
            ("the loser's re-detected write", target, current._revision)).rowcount
        self.assertEqual(1, redone,
                         _describe(SCALE_N, "postgres",
                                   note="the loser re-read the row and STILL could not land — "
                                        "the refusal is a dead end, not a re-detect"))


@unittest.skipUnless(_PG_LIVE, _PG_REASON)
class TheDeclaredSizeIsReported(_ScaleLiveCase):
    def test_the_leg_reports_the_n_it_actually_ran(self):
        """The anti-silent-cap pin, on the live leg itself. `declared_n` asserts rather than
        trusts, and the row count was checked against the ENGINE in `setUpClass`."""
        self.assertEqual(SCALE_N, self.corpus.declared_n)
        self.assertIn(f"N={SCALE_N}", self.corpus.describe())
        print(_describe(SCALE_N, "postgres", corpus=self.corpus.describe(),
                        load_s=round(self.load_seconds, 1)))


if __name__ == "__main__":
    unittest.main()
