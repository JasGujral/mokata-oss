"""DB.S3 — the lexical tier becomes a SQL full-text query (Postgres tsvector / SQLite FTS5).

Before this stage the lexical tier was a Jaccard token-overlap computed in PYTHON over EVERY
active item: `tiered_recall` fetched the whole candidate set and ran
`lexical_score(query, _text(it))` on each one. That is a full-store scan whose cost grows with the
store, AND it loses to real relevance ranking — Jaccard divides by the UNION of tokens, so a long
document containing every query term scores BELOW a short one sharing a single term.

DB.S3 replaces that scan with one ranked query in the database:
  * SQLite — an FTS5 virtual table (`memory_fts`) + `bm25()`, kept in sync by SQL TRIGGERS;
  * Postgres — `to_tsvector`/`to_tsquery`/`ts_rank` (core PG, no `CREATE EXTENSION`), optionally
    accelerated by a GIN expression index provisioned by `team init`.

This is a QUALITY change, not a byte-identity one: BM25/ts_rank rank differently from Jaccard, so
results WILL differ. The bar proven here is "as good or better, deterministic, degrade-clean":

  1. QUALITY — an exact multi-term match outranks a single shared token, and the FTS tier beats
     the Jaccard floor on the case Jaccard demonstrably gets wrong (long-document dilution);
  2. NO PYTHON SCAN — with FTS live, `lexical_score` is not called at all (deterministic spy);
  3. DEGRADE-CLEAN — with FTS5 absent the tier falls back to the Jaccard floor, recall still
     works, nothing crashes, and the degrade is REPORTED (`note_degraded`, the D5 pattern);
  4. INDEX SYNC — insert/update/delete on `memory` is reflected in the index (SQL triggers, so it
     cannot drift even when a DIFFERENT client writes the row);
  5. SCOPE ISOLATION — an FTS match in project B never reaches project A's recall;
  6. FUSION — FTS scores normalize into [0,1] so the existing tier weights still mean what they
     meant, and the deterministic order (fused DESC, created_at ASC, id ASC) is preserved;
  7. BACKFILL — an EXISTING populated table gains the index and its existing rows become findable
     (the upgrade path, not just fresh installs).

Backend legs: SQLite runs everything for real. Postgres runs its emitted SQL through the DB.S2a
`_PgShim` precedent (`%s` -> `?`) extended with a NARROW emulation of the four text-search
primitives — enough to prove the WHERE filters, the scope composes, and the ORDER BY ranks, but
NOT PG's own ranking quality (that is the live-DB leg, skipped unless MOKATA_TEST_DSN is set).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import math
import os
import re
import sqlite3
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import degrade
from mokata.memory import tiered
from mokata.memory.backends import (
    LEXICAL_MODE_FTS5,
    LEXICAL_MODE_JACCARD,
    LEXICAL_MODE_TSVECTOR,
    PostgresBackend,
    SQLiteBackend,
    lexical_tokens,
    normalize_lexical_scores,
)
from mokata.memory.episodic import lexical_score
from mokata.memory.item import ACTIVE, PERSISTENT, MemoryItem
from mokata.memory.tiered import LEXICAL_WEIGHT, SEMANTIC_WEIGHT, tiered_recall


# ============================================================== the Postgres execution shim
_TS_WORD = re.compile(r"[a-z0-9]+")


def _ts_tokens(text):
    return set(_TS_WORD.findall((text or "").lower()))


def _balanced_from(sql, start):
    """The end index (exclusive) of the balanced-paren expression beginning at `start`."""
    depth, i = 0, start
    while i < len(sql):
        if sql[i] == "(":
            depth += 1
        elif sql[i] == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(sql)


def _balanced_back(sql, end):
    """The start index of the balanced-paren expression ending at `end` (exclusive)."""
    depth, i = 0, end - 1
    while i >= 0:
        if sql[i] == ")":
            depth += 1
        elif sql[i] == "(":
            depth -= 1
            if depth == 0:
                # walk back over the function name
                j = i - 1
                while j >= 0 and (sql[j].isalnum() or sql[j] == "_"):
                    j -= 1
                return j + 1
        i -= 1
    return 0


def _rewrite_match_operator(sql):
    """Rewrite every `LEFT @@ RIGHT` into `pg_match(LEFT, RIGHT)` (SQLite has no `@@` operator
    and cannot define one). Both operands are balanced function calls in the SQL we emit."""
    while " @@ " in sql:
        at = sql.index(" @@ ")
        lo = _balanced_back(sql, at)
        hi = _balanced_from(sql, at + 4)
        sql = f"{sql[:lo]}pg_match({sql[lo:at]}, {sql[at + 4:hi]}){sql[hi:]}"
    return sql


class _PgShim:
    """Executes the Postgres backend's SQL for real, on SQLite (the DB.S2a precedent), with a
    NARROW emulation of Postgres's text-search primitives.

    What this proves: the emitted SQL is valid, the MATCH predicate actually FILTERS, the project
    scope composes with it, the ORDER BY ranks, and every value is bound. What it does NOT prove:
    Postgres's own BM25-family ranking quality — that is the live-DB leg."""

    def __init__(self):
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
        self._c.create_function("to_tsvector", 2, lambda cfg, t: " ".join(sorted(_ts_tokens(t))))
        self._c.create_function("to_tsquery", 2, lambda cfg, q: q or "")
        self._c.create_function("pg_match", 2, self._match)
        self._c.create_function("ts_rank", 2, self._rank)
        self._c.create_function("pg_doc_value", 1, self._doc_value)
        self.sql_log = []

    # -- the emulated primitives ------------------------------------------
    @staticmethod
    def _query_tokens(q):
        return {t.strip() for t in (q or "").split("|") if t.strip()}

    def _match(self, vec, q):
        return 1 if _ts_tokens(vec) & self._query_tokens(q) else 0

    def _rank(self, vec, q):
        vt = _ts_tokens(vec)
        hits = len(vt & self._query_tokens(q))
        return hits / (1.0 + math.log(1 + len(vt))) if hits else 0.0

    @staticmethod
    def _doc_value(doc):
        try:
            return json.loads(doc).get("value", "")
        except Exception:
            return ""

    # -- the connection contract ------------------------------------------
    def execute(self, sql, params=()):
        self.sql_log.append(sql)
        run = _rewrite_match_operator(sql.replace("%s", "?"))
        run = run.replace("(doc::jsonb->>'value')", "pg_doc_value(doc)")
        return self._c.execute(run, tuple(params or ()))

    def close(self):
        self._c.close()

    def last_select(self):
        return [s for s in self.sql_log if s.lstrip().upper().startswith("SELECT")][-1]


# ================================================================================ helpers
class _Store:
    """The minimal store-like object `tiered_recall` documents itself against: a backend plus a
    candidate source. `visible` lets a test hide rows the way scope/access filtering does."""

    def __init__(self, backend, items, visible=None):
        self.backend = backend
        self._items = list(items)
        self._visible = visible

    def all_active(self, mtype=None):
        return list(self._items)

    def scoped_active(self, mtype=None):
        if self._visible is None:
            return list(self._items)
        return [i for i in self._items if i.id in self._visible]


def _item(ident, subject, value, created_at="2026-01-01T00:00:00+00:00"):
    return MemoryItem.create(subject=subject, value=value, mtype=PERSISTENT,
                             id=ident, created_at=created_at)


# The quality corpus. `gate` matches every query term; `buffer` shares exactly one; `palette`
# shares none. `verbose` is the LONG document that Jaccard demonstrably mis-ranks.
_QUERY = "durable write gate"

_LONG_TAIL = (" ".join(f"filler{n}" for n in range(40)))


def _quality_corpus():
    return [
        _item("gate", "human write gate",
              "every durable write passes the human gate before it lands", "2026-01-01T00:00:00+00:00"),
        _item("buffer", "cache warmer",
              "the write buffer is flushed on a timer", "2026-01-02T00:00:00+00:00"),
        _item("palette", "brand palette",
              "green cursor wordmark colours", "2026-01-03T00:00:00+00:00"),
        _item("verbose", "release notes",
              f"a durable write is approved at the gate {_LONG_TAIL}", "2026-01-04T00:00:00+00:00"),
    ]


def _sqlite_backend(items, path=":memory:"):
    backend = SQLiteBackend(path)
    for it in items:
        backend.put(it)
    return backend


def _pg_backend(items, project="proj-a", shim=None):
    shim = shim or _PgShim()
    backend = PostgresBackend(project=project, conn=shim)
    for it in items:
        backend.put(it)
    return backend, shim


def _ids(hits):
    return [h.item.id if hasattr(h, "item") else h[0].id for h in hits]


# ============================================================ 1 · retrieval QUALITY (the bar)
class FtsRanksRelevantFirstTest(unittest.TestCase):
    """The tier returns the topically-right items in a sensible order — asserted as ITEMS and
    ORDER, never as Jaccard's numbers (BM25/ts_rank are a different scale by design)."""

    def test_db_s3_fts_ranks_relevant_first(self):
        backend = _sqlite_backend(_quality_corpus())
        self.addCleanup(backend.close)
        ranked = backend.lexical_search(_QUERY, top_k=10)
        ids = [it.id for it, _ in ranked]

        # the item matching every query term leads; the one-token match trails; the
        # zero-token item is not returned at all (FTS returns MATCHES, not the corpus).
        self.assertEqual(ids[0], "gate", f"expected the full match first, got {ids}")
        self.assertIn("buffer", ids)
        self.assertNotIn("palette", ids)
        self.assertLess(ids.index("gate"), ids.index("buffer"))

    def test_db_s3_fts_scores_are_normalized_into_the_tier_range(self):
        backend = _sqlite_backend(_quality_corpus())
        self.addCleanup(backend.close)
        for _, score in backend.lexical_search(_QUERY, top_k=10):
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 1.0)

    def test_db_s3_fts_beats_jaccard_on_long_documents(self):
        """THE retrieval-quality evidence. Jaccard divides by the token UNION, so `verbose` —
        which contains EVERY query term — scores BELOW `buffer`, which shares one. FTS ranks the
        document that actually answers the query above the incidental one."""
        corpus = _quality_corpus()
        by_id = {i.id: i for i in corpus}

        jac = {i.id: lexical_score(_QUERY, f"{i.subject} {i.value}") for i in corpus}
        self.assertGreater(jac["buffer"], jac["verbose"],
                           "premise broken: Jaccard is supposed to mis-rank the long document")

        backend = _sqlite_backend(corpus)
        self.addCleanup(backend.close)
        fts = dict((it.id, s) for it, s in backend.lexical_search(_QUERY, top_k=10))
        self.assertGreater(fts["verbose"], fts["buffer"],
                           f"FTS must rank the answering document first: {fts}")
        self.assertIn("verbose", by_id)

    def test_db_s3_pg_lexical_search_filters_and_ranks(self):
        backend, shim = _pg_backend(_quality_corpus())
        self.addCleanup(backend.close)
        ranked = backend.lexical_search(_QUERY, top_k=10)
        ids = [it.id for it, _ in ranked]
        self.assertNotIn("palette", ids, "the tsquery predicate must FILTER non-matching rows")
        self.assertIn("gate", ids)
        sql = shim.last_select()
        self.assertIn("ts_rank", sql)
        self.assertIn("to_tsvector", sql)
        self.assertNotIn("CREATE EXTENSION", " ".join(shim.sql_log).upper())

    def test_db_s3_lexical_mode_is_reported_per_backend(self):
        sq = _sqlite_backend([])
        self.addCleanup(sq.close)
        self.assertEqual(sq.lexical_mode, LEXICAL_MODE_FTS5)
        pg, _ = _pg_backend([])
        self.addCleanup(pg.close)
        self.assertEqual(pg.lexical_mode, LEXICAL_MODE_TSVECTOR)


# ==================================================================== 2 · no full Python scan
class _ScanSpy:
    """A deterministic stand-in for `lexical_score` that counts how many items got scored in
    Python. Not timing — a call count."""

    def __init__(self):
        self.calls = 0

    def __call__(self, query, text):
        self.calls += 1
        return lexical_score(query, text)


class NoPythonScanTest(unittest.TestCase):

    def test_db_s3_no_python_scan(self):
        corpus = _quality_corpus()
        backend = _sqlite_backend(corpus)
        self.addCleanup(backend.close)
        store = _Store(backend, corpus)

        spy = _ScanSpy()
        original = tiered.lexical_score
        tiered.lexical_score = spy
        try:
            hits = tiered_recall(store, _QUERY, top_k=5)
        finally:
            tiered.lexical_score = original

        self.assertEqual(spy.calls, 0,
                         "with FTS live the lexical tier must not score rows in Python")
        self.assertTrue(hits)
        self.assertEqual(hits[0].item.id, "gate")

    def test_db_s3_python_scan_returns_when_fts_is_unavailable(self):
        """The same spy proves the fallback is REAL — the floor genuinely scores in Python."""
        corpus = _quality_corpus()
        backend = _sqlite_backend(corpus)
        self.addCleanup(backend.close)
        backend._fts = False                     # the probe's verdict, forced off
        store = _Store(backend, corpus)

        spy = _ScanSpy()
        original = tiered.lexical_score
        tiered.lexical_score = spy
        try:
            tiered_recall(store, _QUERY, top_k=5)
        finally:
            tiered.lexical_score = original
        self.assertEqual(spy.calls, len(corpus))


# ========================================================================= 3 · degrade-clean
class DegradeCleanTest(unittest.TestCase):

    def setUp(self):
        degrade.reset_degrade_notices()
        self.addCleanup(degrade.reset_degrade_notices)

    def test_db_s3_no_fts5_falls_back(self):
        """FTS5 absent -> the tier is the Jaccard floor, recall still works, no crash, and the
        degrade is REPORTED (a user asking for FTS recall must be told they are not getting it)."""
        corpus = _quality_corpus()
        backend = _sqlite_backend(corpus)
        self.addCleanup(backend.close)
        backend._fts = False
        store = _Store(backend, corpus)

        notices = []
        hits = tiered_recall(store, _QUERY, top_k=5, degrade_out=notices.append)

        self.assertTrue(hits, "recall must still return on the floor")
        self.assertEqual(backend.lexical_mode, LEXICAL_MODE_JACCARD)
        self.assertTrue(notices, "the lexical degrade must be reported, not silent")
        self.assertTrue(any("lexical" in n for n in notices), notices)
        subsystems = [n.subsystem for n in degrade.emitted_notices()]
        self.assertIn("memory-lexical", subsystems)

    def test_db_s3_missing_fts5_never_crashes_the_backend(self):
        """A sqlite3 build without FTS5 must still construct, write, read and search."""
        original = SQLiteBackend.fts5_available
        SQLiteBackend.fts5_available = staticmethod(lambda conn: False)
        try:
            backend = SQLiteBackend(":memory:")
            self.addCleanup(backend.close)
            for it in _quality_corpus():
                backend.put(it)
            self.assertEqual(backend.lexical_mode, LEXICAL_MODE_JACCARD)
            self.assertEqual(backend.lexical_search(_QUERY, top_k=5), [])
            self.assertEqual(len(backend.all()), 4)
        finally:
            SQLiteBackend.fts5_available = original

    def test_db_s3_non_sql_backend_is_the_floor_without_a_degrade_notice(self):
        """Obsidian/native have no FTS to lose — the floor is their DESIGN, not a degrade, so
        they must not emit a notice (a notice that always fires is noise, not a signal)."""
        corpus = _quality_corpus()
        store = _Store(object(), corpus)         # a backend with no lexical_search at all
        notices = []
        hits = tiered_recall(store, _QUERY, top_k=5, degrade_out=notices.append)
        self.assertTrue(hits)
        self.assertEqual(notices, [])


# ========================================================================== 4 · index sync
class IndexStaysCurrentTest(unittest.TestCase):
    """The index is maintained by SQL TRIGGERS (SQLite) / computed at query time (Postgres), so
    it cannot drift from the rows — including when a different client does the writing."""

    def test_db_s3_index_stays_current_sqlite(self):
        backend = _sqlite_backend(_quality_corpus())
        self.addCleanup(backend.close)

        def found(term):
            return {it.id for it, _ in backend.lexical_search(term, top_k=20)}

        # INSERT — a new row is findable
        backend.put(_item("fresh", "telemetry sink", "the quarantine ledger records refusals"))
        self.assertIn("fresh", found("quarantine"))

        # UPDATE — the old text stops matching, the new text starts
        backend.put(_item("fresh", "telemetry sink", "the sampler emits histograms"))
        self.assertNotIn("fresh", found("quarantine"))
        self.assertIn("fresh", found("histograms"))

        # DELETE — the row leaves the index with the row
        self.assertTrue(backend.delete("fresh"))
        self.assertEqual(found("histograms"), set())

    def test_db_s3_index_survives_a_write_from_another_client(self):
        """The trigger lives in the DATABASE, so a row written by any other connection — an older
        mokata, psql, anything — is indexed too. That is why this is a trigger and not a
        Python-side index maintained by `put()`."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "memory.db")
            backend = _sqlite_backend(_quality_corpus(), path=path)
            self.addCleanup(backend.close)

            other = sqlite3.connect(path)
            doc = json.dumps(_item("outside", "sidecar", "written by another client").to_dict())
            other.execute(
                "INSERT INTO memory (id, mtype, subject, status, doc) VALUES (?,?,?,?,?)",
                ("outside", PERSISTENT, "sidecar", ACTIVE, doc))
            other.commit()
            other.close()

            ids = {it.id for it, _ in backend.lexical_search("sidecar", top_k=20)}
            self.assertIn("outside", ids)

    def test_db_s3_index_stays_current_postgres(self):
        backend, _ = _pg_backend(_quality_corpus())
        self.addCleanup(backend.close)

        def found(term):
            return {it.id for it, _ in backend.lexical_search(term, top_k=20)}

        backend.put(_item("fresh", "telemetry sink", "the quarantine ledger records refusals"))
        self.assertIn("fresh", found("quarantine"))
        backend.put(_item("fresh", "telemetry sink", "the sampler emits histograms"))
        self.assertNotIn("fresh", found("quarantine"))
        self.assertTrue(backend.delete("fresh"))
        self.assertEqual(found("histograms"), set())


# ======================================================================= 5 · scope isolation
class ScopeIsolationTest(unittest.TestCase):
    """Cross-tenant leakage is the scary failure. The FTS predicate composes WITH the scope
    filter — it never replaces it."""

    def test_db_s3_fts_scope_isolation(self):
        shim = _PgShim()
        a, _ = _pg_backend([_item("a1", "human write gate", "a durable write gate")],
                           project="proj-a", shim=shim)
        b, _ = _pg_backend([_item("b1", "human write gate", "a durable write gate")],
                           project="proj-b", shim=shim)
        self.addCleanup(shim.close)

        self.assertEqual([it.id for it, _ in a.lexical_search(_QUERY, top_k=10)], ["a1"])
        self.assertEqual([it.id for it, _ in b.lexical_search(_QUERY, top_k=10)], ["b1"])

    def test_db_s3_scope_clause_is_in_the_fts_query(self):
        backend, shim = _pg_backend(_quality_corpus(), project="proj-a")
        self.addCleanup(backend.close)
        backend.lexical_search(_QUERY, top_k=5)
        sql = shim.last_select()
        self.assertIn("project=%s", sql)
        self.assertIn("@@", sql, "the scope predicate must AND onto the MATCH, not replace it")

    def test_db_s3_unscoped_backend_spans_projects(self):
        shim = _PgShim()
        _pg_backend([_item("a1", "human write gate", "a durable write gate")],
                    project="proj-a", shim=shim)
        _pg_backend([_item("b1", "human write gate", "a durable write gate")],
                    project="proj-b", shim=shim)
        self.addCleanup(shim.close)
        spanning = PostgresBackend(project=None, conn=shim)
        self.assertEqual({it.id for it, _ in spanning.lexical_search(_QUERY, top_k=10)},
                         {"a1", "b1"})

    def test_db_s3_hidden_item_never_reaches_recall(self):
        """The store-level visibility filter (scope union / access policy) still wins: an FTS
        match the identity may not read is dropped even though the DB returned it."""
        corpus = _quality_corpus()
        backend = _sqlite_backend(corpus)
        self.addCleanup(backend.close)
        store = _Store(backend, corpus, visible={"buffer"})
        hits = tiered_recall(store, _QUERY, top_k=10)
        self.assertEqual(_ids(hits), ["buffer"])

    def test_db_s3_query_value_is_bound_never_formatted(self):
        backend, shim = _pg_backend(_quality_corpus())
        self.addCleanup(backend.close)
        hostile = "'; DROP TABLE mokata_memory; --"
        backend.lexical_search(hostile, top_k=5)
        self.assertNotIn("DROP TABLE", shim.last_select())

    def test_db_s3_hostile_query_does_not_crash_fts5(self):
        """FTS5's MATCH syntax has operators (`"`, `*`, `NEAR`, `:`). A user query is TEXT, not
        syntax — it is tokenized before it reaches MATCH, so no query can raise."""
        backend = _sqlite_backend(_quality_corpus())
        self.addCleanup(backend.close)
        for hostile in ['"unbalanced', "gate OR (", "NEAR/", "col:val", "*", "", "   "]:
            self.assertIsInstance(backend.lexical_search(hostile, top_k=5), list)


# =============================================================================== 6 · fusion
class FusionOrderTest(unittest.TestCase):

    def test_db_s3_fusion_order(self):
        """Deterministic order preserved: fused DESC, created_at ASC, id ASC."""
        corpus = _quality_corpus()
        backend = _sqlite_backend(corpus)
        self.addCleanup(backend.close)
        hits = tiered_recall(_Store(backend, corpus), _QUERY, top_k=10)
        keys = [(-h.score, h.item.created_at, h.item.id) for h in hits]
        self.assertEqual(keys, sorted(keys), f"order is not the deterministic key: {_ids(hits)}")

    def test_db_s3_semantic_still_outranks_a_merely_lexical_match(self):
        """The normalization must keep the tier WEIGHTS meaning what they meant: a semantic-near
        item outranks a top lexical match, because SEMANTIC_WEIGHT > LEXICAL_WEIGHT and the
        lexical contribution is capped at 1.0."""
        self.assertGreater(SEMANTIC_WEIGHT, LEXICAL_WEIGHT)
        corpus = _quality_corpus()
        backend = _sqlite_backend(corpus)
        self.addCleanup(backend.close)

        # `palette` shares NO token with the query but is the embedding-near item.
        def embedder(text):
            return [1.0, 0.0] if "colour" in text or "palette" in text.lower() else [0.0, 1.0]

        hits = tiered_recall(_Store(backend, corpus), "brand palette colours",
                             embedder=embedder, top_k=10)
        self.assertEqual(hits[0].item.id, "palette")
        self.assertGreater(hits[0].semantic, 0.0)

    def test_db_s3_lexical_contribution_never_exceeds_the_weight(self):
        corpus = _quality_corpus()
        backend = _sqlite_backend(corpus)
        self.addCleanup(backend.close)
        for hit in tiered_recall(_Store(backend, corpus), _QUERY, top_k=10):
            self.assertLessEqual(hit.lexical, 1.0)
            self.assertLessEqual(hit.score, LEXICAL_WEIGHT + 1e-9)

    def test_db_s3_normalization_is_deterministic_and_bounded(self):
        # bm25: LOWER (more negative) is better; ts_rank: HIGHER is better. Both land in [0,1].
        bm25 = normalize_lexical_scores([-3.0, -1.0, -0.5], higher_is_better=False)
        self.assertEqual(bm25, sorted(bm25, reverse=True))
        self.assertEqual(bm25[0], 1.0)
        self.assertTrue(all(0.0 <= s <= 1.0 for s in bm25))

        ts = normalize_lexical_scores([0.8, 0.4, 0.1], higher_is_better=True)
        self.assertEqual(ts[0], 1.0)
        self.assertTrue(all(0.0 <= s <= 1.0 for s in ts))

        self.assertEqual(normalize_lexical_scores([], higher_is_better=True), [])
        # a degenerate all-equal run must still be deterministic and bounded
        flat = normalize_lexical_scores([0.0, 0.0], higher_is_better=True)
        self.assertTrue(all(0.0 <= s <= 1.0 for s in flat))

    def test_db_s3_tokenizer_is_the_one_shared_source(self):
        self.assertEqual(lexical_tokens("Durable Write-Gate!"), ["durable", "write", "gate"])
        self.assertEqual(lexical_tokens("   "), [])


# =========================================================== 7 · migration / backfill
class BackfillTest(unittest.TestCase):
    """The UPGRADE path: a populated pre-DB.S3 table gains the index and its EXISTING rows become
    findable. Additive only — no schema-version bump, so an older client still reads the store."""

    def test_db_s3_backfill(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "memory.db")

            # a store written by a pre-DB.S3 build: no FTS table, no triggers
            original = SQLiteBackend.fts5_available
            SQLiteBackend.fts5_available = staticmethod(lambda conn: False)
            try:
                old = SQLiteBackend(path)
                for it in _quality_corpus():
                    old.put(it)
                old.close()
            finally:
                SQLiteBackend.fts5_available = original

            # `sqlite3.connect` as a context manager commits the transaction but does NOT close
            # the connection, and an open handle blocks the temp-dir unlink on Windows — so the
            # probe is closed explicitly, like every other backend in this class.
            probe = sqlite3.connect(path)
            try:
                tables = {r[0] for r in probe.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            finally:
                probe.close()
            self.assertNotIn("memory_fts", tables, "premise broken: the old store had an index")

            # today's build opens it — the index is created AND backfilled. Closed INSIDE the
            # `with` (not via addCleanup, which runs after the temp dir is already being removed).
            new = SQLiteBackend(path)
            try:
                self.assertEqual(new.lexical_mode, LEXICAL_MODE_FTS5)
                ids = {it.id for it, _ in new.lexical_search(_QUERY, top_k=10)}
                self.assertIn("gate", ids, "pre-existing rows must be findable after the upgrade")
                self.assertIn("verbose", ids)
            finally:
                new.close()

    def test_db_s3_backfill_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "memory.db")
            first = _sqlite_backend(_quality_corpus(), path=path)
            first.close()
            for _ in range(3):
                again = SQLiteBackend(path)
                hits = again.lexical_search(_QUERY, top_k=20)
                again.close()
            ids = [it.id for it, _ in hits]
            self.assertEqual(len(ids), len(set(ids)), f"reopening duplicated index rows: {ids}")

    def test_db_s3_does_not_break_older_clients(self):
        """A version bump is a real break for older clients (the DB.S2b lesson). DB.S3's Postgres
        side is an ADDITIVE GIN index — no column, no row change — so it costs older clients
        nothing.

        Re-expressed at DB.S5, which DID bump the version (3 → 4, adding the lifecycle columns).
        Pinning the literal `TEAM_SCHEMA_VERSION == 3` was only ever a proxy for the real claim,
        and it stopped being a true proxy the moment a LATER stage bumped it for its own reasons.
        The claim itself is about the FLOOR: `TEAM_SCHEMA_MIN_SUPPORTED` is what refuses an older
        client, and DB.S3 neither raised it nor gave anyone a reason to."""
        from mokata import teamdb
        self.assertEqual(teamdb.TEAM_SCHEMA_MIN_SUPPORTED, 3)
        index = [s for s in teamdb.provision_sql() if "USING GIN" in s.upper()]
        self.assertEqual(len(index), 1)
        self.assertIn("IF NOT EXISTS", index[0].upper())
        self.assertNotIn("ALTER TABLE", index[0].upper())

    def test_db_s3_postgres_index_is_provisioned_by_team_init(self):
        """No runtime DDL (D1/C4): the GIN index ships in `provision_sql`, which `team init`
        runs — a DML-only runtime role never issues it, and the query works without it."""
        from mokata import teamdb
        sql = " ".join(teamdb.provision_sql())
        self.assertIn("USING GIN", sql.upper())
        self.assertIn("to_tsvector", sql)
        self.assertIn("IF NOT EXISTS", sql)
        self.assertNotIn("CREATE EXTENSION", sql.upper())


# ================================================================== 8 · live Postgres leg
# GR.S2-FU — run by `.github/workflows/live-db-legs.yml` (opt-in: workflow_dispatch + weekly cron).
@unittest.skipUnless(os.environ.get("MOKATA_TEST_DSN"), "no live Postgres DSN (MOKATA_TEST_DSN)")
class LivePostgresTest(unittest.TestCase):
    """The only leg that proves PG's REAL ranking. Skipped unless a DSN is wired."""

    def test_db_s3_live_postgres_ranks_relevant_first(self):
        from mokata import teamdb
        dsn = os.environ["MOKATA_TEST_DSN"]
        teamdb.provision(dsn)
        backend = PostgresBackend(dsn, project="db-s3-live")
        self.addCleanup(backend.close)
        for it in _quality_corpus():
            backend.put(it)
        try:
            ids = [it.id for it, _ in backend.lexical_search(_QUERY, top_k=10)]
            self.assertEqual(ids[0], "gate", ids)
            self.assertNotIn("palette", ids)
        finally:
            for it in _quality_corpus():
                backend.delete(it.id)


# ======================================================================== 9 · secret-safety
class SecretSafetyTest(unittest.TestCase):
    """The FTS index holds memory TEXT (subject + value) — user memory, in the SAME governed
    store as the rows it indexes. It adds no new sink. Assert no DSN reaches a log or the SQL."""

    def test_db_s3_no_dsn_in_the_emitted_sql(self):
        backend, shim = _pg_backend(_quality_corpus())
        self.addCleanup(backend.close)
        backend.lexical_search(_QUERY, top_k=5)
        joined = " ".join(shim.sql_log)
        for marker in ("postgres://", "postgresql://", "password", "@localhost"):
            self.assertNotIn(marker, joined.lower())

    def test_db_s3_index_lives_in_the_same_store_as_the_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "memory.db")
            backend = _sqlite_backend(_quality_corpus(), path=path)
            self.addCleanup(backend.close)
            self.assertEqual(sorted(os.path.basename(p) for p in os.listdir(tmp)
                                    if p.endswith(".db")), ["memory.db"])


if __name__ == "__main__":
    unittest.main()
