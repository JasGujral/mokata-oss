"""DB.S8c — the SQLITE scale leg, at an N that is DECLARED rather than implied.

Doc 52 wants a SQLite scale leg beside the Postgres one because SQLite is the local floor every
zero-config user actually runs, and `_PgShim` cannot stand in for either engine at scale (it would
measure the shim — doc 84's SHIM-FALSE-GREEN).

## THE DECLARED N, AND WHY IT IS NOT 100,000

`SQLITE_LEG_N = 25_000`. The Postgres leg runs 100,000 and says so; this one runs 25,000 and says
so. The difference is deliberate and it is stated rather than left to be inferred from a runtime:
this leg is UNCONDITIONAL — it runs in the ordinary unit suite on every push, with no DSN and no
opt-in — and a 100k generate-plus-load on every push is not a reasonable per-push cost. 25,000 is
what it runs and 25,000 is what it reports.

A leg that ran 2,000 rows under a name that says 100,000 is the SILENT-CAP failure: it reads as
coverage it does not have, and nothing in the output contradicts it. The rule this module holds to
is that the number in the name, the number in the corpus and the number in the report are the same
number, and `Corpus.declared_n` asserts it rather than trusting it.

WHAT THIS LEG PROVES THAT THE POSTGRES ONE CANNOT: that the SAME bounded-read shape holds on the
engine with no server, no statistics and a different query planner — and, specifically, that
SQLite's `SQLITE_MAX_VARIABLE_NUMBER` (999 on older builds) does not turn the candidate hydrate
into a raise on a large store. That is an engine fact about SQLite, and Postgres has no analogue.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import contextlib
import os
import statistics
import tempfile
import time
import unittest

import _support  # noqa: F401

import _scale_fixture as F

from mokata.memory import scope as S
from mokata.memory import tiered
from mokata.memory.backends import SQLiteBackend
from mokata.memory.store import MemoryStore

#: THE DECLARED N of this leg. Stated once, asserted at load, reported in every message.
SQLITE_LEG_N = 25_000


class SQLiteScaleLeg(unittest.TestCase):
    corpus: F.Corpus
    backend: SQLiteBackend

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.corpus = F.generate(F.ScaleSpec(n_items=SQLITE_LEG_N, probes=40))
        cls.backend = _CountingSQLite(os.path.join(cls._tmp.name, "scale.db"))
        started = time.perf_counter()
        loaded = F.load_sqlite(cls.backend, cls.corpus)
        cls.load_seconds = time.perf_counter() - started
        assert loaded == cls.corpus.declared_n == SQLITE_LEG_N, (
            f"loaded {loaded} rows for a corpus declaring {SQLITE_LEG_N}")

    @classmethod
    def tearDownClass(cls):
        cls.backend.close()
        cls._tmp.cleanup()

    def store(self, context):
        return MemoryStore(self.backend, scope_context=context)

    def test_the_declared_n_is_the_n_that_ran(self):
        """The anti-silent-cap pin. Three numbers — the constant, the corpus, and the table — and
        this asserts they are one number."""
        self.assertEqual(SQLITE_LEG_N, self.corpus.declared_n)
        self.assertEqual(SQLITE_LEG_N, len(self.backend.all()))
        self.assertIn(f"N={SQLITE_LEG_N}", self.corpus.describe())

    def test_recall_materializes_bounded_rows_at_scale(self):
        """S-1's row-count arm, on the local floor."""
        self.backend.materialized = 0
        counts = []
        for probe in self.corpus.probes[:20]:
            self.backend.materialized = 0
            self.store(self.corpus.context_for(probe)).recall_relevant(probe.query, top_k=10)
            counts.append(self.backend.materialized)
        worst = max(counts)
        self.assertLessEqual(
            worst, tiered.CANDIDATE_UNION_CAP * 2,
            f"[sqlite N={SQLITE_LEG_N:,}] a recall materialized {worst} rows — the read is not "
            f"bounded by the over-fetch")

    def test_recall_latency_is_flat_enough_to_be_index_bound(self):
        times = []
        for probe in self.corpus.probes[:20]:
            store = self.store(self.corpus.context_for(probe))
            started = time.perf_counter()
            store.recall_relevant(probe.query, top_k=10)
            times.append((time.perf_counter() - started) * 1000)
        median = statistics.median(times)
        # Loose, for the same reason the live leg's is: shared CI hardware makes a tight
        # millisecond threshold a flake generator, and the row count above is the real contract.
        # This catches only the regression that matters — a return to the full-set read, which at
        # this N is hundreds of milliseconds, not tens.
        self.assertLess(median, 1_000,
                        f"[sqlite N={SQLITE_LEG_N:,}] median recall {median:.0f}ms — R-1 has "
                        "regressed to a full-set read")

    def test_the_candidate_hydrate_chunks_rather_than_binding_one_statement(self):
        """THE engine fact only this leg can establish, pinned so it holds on EVERY build.

        SQLite binds one `?` per id and `SQLITE_MAX_VARIABLE_NUMBER` caps how many a single
        statement may carry — 999 on older builds, 32,766 on this one. `hydrate` chunks at
        `_ID_CHUNK` (500) for exactly that reason.

        The obvious test — ask for a few thousand ids and check it does not raise — is WORTHLESS
        HERE and was written that way first: verified by raising `_ID_CHUNK` to 100,000, which
        defeats the chunk loop entirely and left that test GREEN, because 4,000 parameters is
        comfortably under this build's 32,766. It would only have failed on the old builds the
        chunking exists to protect, i.e. never in this CI.

        The SECOND attempt was self-referential and is recorded too: asserting
        `statements == ceil(N / _ID_CHUNK)` derives the expectation FROM the constant under test,
        so raising `_ID_CHUNK` moved both sides and it stayed green as well.

        What is pinned is the PROPERTY, against a fixed number the code does not supply: NO single
        emitted statement may bind more than `_OLDEST_SQLITE_PARAM_LIMIT` (999) parameters. That is
        the limit the chunking exists to respect, it is a constant of the oldest supported engine
        rather than of this configuration, and it goes RED for any `_ID_CHUNK` that stops
        protecting it.
        """
        ids = [i.id for i in self.corpus.items[:4_000]]
        widest = _widest_hydrate_statement(self.backend, ids)
        self.assertLessEqual(
            widest, _OLDEST_SQLITE_PARAM_LIMIT,
            f"[sqlite N={SQLITE_LEG_N:,}] a single hydrate statement binds {widest} parameters, "
            f"over the {_OLDEST_SQLITE_PARAM_LIMIT} an older sqlite3 allows — on such a build this "
            "read RAISES, and the seams above it swallow the failure")
        # …and it is genuinely chunking rather than passing by returning nothing.
        self.assertGreater(widest, 0, "no parameterized hydrate statement was emitted at all")
        # …and it still returns the right rows, so the chunking is not correct-by-being-broken.
        rows = self.backend.hydrate(ids, statuses=("active",))
        self.assertTrue(rows)
        self.assertLessEqual(len(rows), len(ids))
        self.assertEqual(len({r.id for r in rows}), len(rows), "the chunk loop returned duplicates")
        # The SUBJECT half is a separate chunk loop and therefore a separate chance to exceed it.
        subjects = sorted({i.subject for i in self.corpus.items[:4_000]})
        self.assertTrue(self.backend.hydrate((), subjects=subjects, statuses=("active",)))

    def test_scope_isolation_still_holds_at_this_n(self):
        """S-2/S-6 run at N=2,000 in `test_db_s8b_contracts`. This re-runs the claim an order of
        magnitude up, because a scope predicate that composes on 2,000 rows and stops composing on
        25,000 would be a predicate that silently depends on the planner's choices."""
        for project in range(self.corpus.spec.projects):
            key = self.corpus.spec.project_key(project)
            foreign = {i.id for i in self.corpus.items
                       if i.scope_level == S.PROJECT and i.scope_id != key}
            store = self.store(self.corpus.seat_context(0, project=project))
            leaked = {i.id for i in store.scoped_active()} & foreign
            self.assertEqual(leaked, set(),
                             f"[sqlite N={SQLITE_LEG_N:,}] project {key} leaked {len(leaked)}")

    def test_the_ground_truth_is_still_answerable_at_this_n(self):
        """The control on everything above: a bounded read that returned nothing would satisfy
        every latency and row-count assertion in this file."""
        for probe in self.corpus.probes[:10]:
            store = self.store(self.corpus.context_for(probe))
            ids = {h.item.id for h in store.recall_relevant(probe.query, top_k=25)}
            self.assertIn(probe.direct_id, ids, f"[sqlite N={SQLITE_LEG_N:,}] {probe.query!r}")
            self.assertIn(probe.hop_id, ids, f"[sqlite N={SQLITE_LEG_N:,}] {probe.query!r}")


#: The parameter ceiling of the OLDEST sqlite3 mokata can be built against. Deliberately a
#: constant of that engine and NOT derived from `SQLiteBackend._ID_CHUNK` — an expectation computed
#: from the value under test moves with it, which is exactly how the second version of the pin
#: below managed to stay green while the chunk loop was disabled.
_OLDEST_SQLITE_PARAM_LIMIT = 999


def _widest_hydrate_statement(backend, ids):
    """The most parameters any SINGLE statement `hydrate` emits binds, for `ids`.

    Measured on the PARAMS TUPLE, not on the SQL text. `sqlite3.set_trace_callback` hands back the
    EXPANDED statement with every parameter already substituted, so counting `?` in it yields 0 for
    every statement — verified, and it is why the previous version of this helper reported "no
    parameterized statement was emitted" against a store that had just emitted eight.

    `_connect` is a CONTEXT MANAGER, so the wrapper is one too, and it yields a proxy that records
    each call's parameter count before delegating.
    """
    widest = [0]
    original = backend._connect

    class _Recording:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, params=()):
            try:
                widest[0] = max(widest[0], len(params))
            except TypeError:                 # a mapping / an unsized params object
                pass
            return self._conn.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self._conn, name)

    @contextlib.contextmanager
    def recording():
        with original() as conn:
            yield _Recording(conn)

    backend._connect = recording
    try:
        backend.hydrate(list(ids), statuses=("active",))
    finally:
        backend._connect = original
    return widest[0]


class _CountingSQLite(SQLiteBackend):
    """A real backend that tallies the rows its reads materialize."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.materialized = 0

    def all(self, *a, **kw):
        rows = super().all(*a, **kw)
        self.materialized += len(rows)
        return rows

    def hydrate(self, *a, **kw):
        rows = super().hydrate(*a, **kw)
        self.materialized += len(rows)
        return rows


if __name__ == "__main__":
    unittest.main()
