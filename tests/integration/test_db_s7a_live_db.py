"""DB.S7a — the edge substrate against a REAL Postgres, including two REAL writers.

The unit suite proves the semantics against SQLite and a CAS-shaped double. Four of this slice's
claims are not provable there, and every one of them fails SILENTLY rather than loudly:

  * **the v5 MIGRATION is a statement about a POPULATED v4 store.** "Idempotent" and "skips a
    dangling ref" are properties of `INSERT … SELECT … WHERE NOT EXISTS` running against real rows,
    real `jsonb_array_elements_text`, and a real PARTIAL UNIQUE INDEX. A Python loop standing in
    for the migration can show mokata intending the right thing; only the engine can show that a
    second pass inserted nothing and that the index would have refused it if the predicate had not.

  * **"at most one OPEN edge" is a claim about an INDEX.** The partial unique index is the only
    thing that makes the projection safe under concurrency, and a dict cannot be violated.

  * **CAS-guarded edges need TWO REAL WRITERS.** The claim is that a writer which LOSES the item's
    compare-and-set writes no edge. In the unit suite the CAS verdict is a fake's `rowcount`; here
    it is Postgres's own `UPDATE … WHERE revision = %s` with a genuine competitor on the other end.

  * **the group ROLLBACK has to take the edges with it.** I1 gives a whole approval one BEGIN/
    COMMIT. Whether a rolled-back group leaves zero edge rows depends on psycopg3 emitting a real
    BEGIN/ROLLBACK and on Postgres undoing statements that had already succeeded — the two things
    the unit doubles stand in for.

Gate is the same explicit contract as the other live-DB legs: MOKATA_LIVE_DB=1 + MOKATA_PG_DSN +
psycopg + a reachable DB, else these skip cleanly.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from test_db_s6_live_db import (_PG_LIVE, _PG_REASON, _PROJECT, _LivePgCase,  # noqa: F401
                                _flush, _journal_update, _pg_dsn, _status, _writer)


def _conn(dsn):
    from mokata.memory import _pg
    return _pg.get_connection(dsn, RuntimeError)


def _edge_rows(dsn, src=None):
    from mokata import teamdb
    from mokata.memory import edges as E
    where, params = "", ()
    if src is not None:
        where, params = f" WHERE {E.SRC_COLUMN}=%s", (src,)
    return _conn(dsn).execute(
        f"SELECT {E.SRC_COLUMN}, {E.DST_COLUMN}, {E.KIND_COLUMN}, {E.VALID_FROM_COLUMN}, "
        f"{E.VALID_TO_COLUMN}, {E.APPROVAL_LEDGER_COLUMN} FROM {teamdb.EDGES_TABLE}{where} "
        f"ORDER BY {E.KIND_COLUMN}, {E.DST_COLUMN}", params).fetchall()


def _plain_item(rid, value, status=None):
    from mokata.memory.item import ACTIVE, PERSISTENT, MemoryItem
    return MemoryItem(subject=rid, value=value, id=rid, mtype=PERSISTENT,
                      status=status or ACTIVE,
                      provenance={"source": "test", "author": "t",
                                  "created_at": "2026-07-01T00:00:00+00:00"})


def _with_edges(rid, value, *, supersedes=(), depends_on=(), about_code=(), status=None):
    item = _plain_item(rid, value, status)
    item.supersedes = list(supersedes)
    item.depends_on = list(depends_on)
    item.about_code = list(about_code)
    return item


def _provision_v4(dsn):
    """A REAL v4 store: today's pass with every DB.S7a statement removed and the version row rolled
    back. Built this way rather than by provisioning v5 and pretending, because a migration test
    that starts from the post-state skips exactly the step that can go wrong."""
    from mokata import teamdb
    for table in (teamdb.EDGES_TABLE, teamdb.MEMORY_TABLE, teamdb.SCHEMA_VERSION_TABLE):
        _conn(dsn).execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    for stmt in teamdb.provision_sql():
        if teamdb.EDGES_TABLE in stmt:
            continue
        _conn(dsn).execute(stmt.replace(f"({teamdb.TEAM_SCHEMA_VERSION}, ", "(4, "))
    teamdb.reset_schema_cache()


class _EdgeLivePgCase(_LivePgCase):
    """`_LivePgCase` + a clean edge table. The base clears `mokata_memory` only, which was right
    when nothing else held per-item state; a leftover edge row would make every count below a
    statement about a different test's data."""

    def setUp(self):
        super().setUp()
        from mokata import teamdb
        _conn(self.dsn).execute(f"DELETE FROM {teamdb.EDGES_TABLE}")


# ======================================================================================
# E1 + E2 + E7 · THE MIGRATION, against a POPULATED v4 store
# ======================================================================================
@unittest.skipUnless(_PG_LIVE, _PG_REASON)
class TestTheV5MigrationOnAPopulatedStore(unittest.TestCase):
    """A store built by v4 SQL, then migrated by the ONE DDL path.

    (Naming note: this class was called `…OnAPopulatedV4Store` until mokata's own secret-guard
    blocked the file — a THIRD live sighting of doc 84's filed CamelCase entropy false positive.
    Renamed rather than ignore-listed: a version-controlled ignore entry for a test class name is a
    worse artifact than a shorter name, and the FP row is where the fix belongs.)"""

    def setUp(self):
        from mokata import teamdb
        self.dsn, self.teamdb = _pg_dsn(), teamdb
        _provision_v4(self.dsn)

    def tearDown(self):
        from mokata.memory import _pg
        _pg.reset_manager()

    def _seed(self, rid, **fields):
        doc = {"id": rid, "subject": rid, "value": "v", "mtype": "persistent", "status": "active",
               "schema_version": 1,
               "provenance": {"source": "test", "author": "alice",
                              "created_at": "2026-07-01T00:00:00+00:00"},
               "supersedes": [], "depends_on": [], "about_code": []}
        doc.update(fields)
        _conn(self.dsn).execute(
            f"INSERT INTO {self.teamdb.MEMORY_TABLE} (id, mtype, subject, status, doc, project) "
            f"VALUES (%s,%s,%s,%s,%s,%s)",
            (rid, "persistent", rid, "active", json.dumps(doc), "fixture"))

    def _populate(self):
        self._seed("old-fact")
        self._seed("mid-fact", supersedes=["old-fact"])
        self._seed("new-fact", supersedes=["mid-fact"], depends_on=["old-fact"],
                   about_code=["src/auth.py", "src/auth.py::login"])
        # THE PLANTED DANGLING REF — two relations at a target this store does not have.
        self._seed("dangling", supersedes=["GONE-1"], depends_on=["GONE-1", "GONE-2"])
        # a doc listing the same ref twice: the partial unique index refuses it un-collapsed
        self._seed("dupes", supersedes=["old-fact", "old-fact"])
        # a MALFORMED inline field (a string / an object / null where a list belongs)
        self._seed("malformed", supersedes="not-a-list", depends_on={"a": 1}, about_code=None)

    def test_a_v4_store_really_has_no_edge_table_before_the_migration(self):
        """The premise, asserted rather than assumed — a fixture that was secretly already v5 would
        make every assertion below pass for the wrong reason."""
        self.assertIsNone(_conn(self.dsn).execute(
            "SELECT to_regclass(%s)", (self.teamdb.EDGES_TABLE,)).fetchone()[0])
        self.assertEqual(4, _conn(self.dsn).execute(
            f"SELECT max(version) FROM {self.teamdb.SCHEMA_VERSION_TABLE}").fetchone()[0])

    def test_the_migration_moves_the_three_implicit_kinds_and_stamps_v5(self):
        self._populate()
        result = self.teamdb.provision(self.dsn, project_id="fixture")
        self.assertEqual(5, result.version)
        self.assertIn(self.teamdb.EDGES_TABLE, result.tables)
        self.assertEqual({
            ("mid-fact", "old-fact", "supersedes"),
            ("new-fact", "mid-fact", "supersedes"),
            ("new-fact", "old-fact", "depends_on"),
            ("new-fact", "src/auth.py", "about_code"),
            ("new-fact", "src/auth.py::login", "about_code"),
            ("dupes", "old-fact", "supersedes"),
        }, {(r[0], r[1], r[2]) for r in _edge_rows(self.dsn)})
        self.assertEqual(5, _conn(self.dsn).execute(
            f"SELECT max(version) FROM {self.teamdb.SCHEMA_VERSION_TABLE}").fetchone()[0])

    def test_the_floor_the_migration_stamps_is_still_3(self):
        """The whole point of an additive v5. MUTATION: raise `TEAM_SCHEMA_MIN_SUPPORTED` and every
        existing v3/v4 team fail-closes on upgrade for a table nothing yet requires."""
        self._populate()
        self.teamdb.provision(self.dsn, project_id="fixture")
        self.assertEqual((5, 3), _conn(self.dsn).execute(
            f"SELECT version, min_supported FROM {self.teamdb.SCHEMA_VERSION_TABLE} "
            f"ORDER BY version DESC LIMIT 1").fetchone())

    def test_the_migrated_edge_window_opens_where_its_ITEM_S_window_opened(self):
        """One rule for the live projection and the migration alike — so an edge rebuilt from a doc
        lands on the same instant as the edge that was written from it. And `valid_to` is NULL:
        migrating must not retire a single relation, exactly as DB.S5's backfill must not retire a
        single item."""
        self._populate()
        self.teamdb.provision(self.dsn, project_id="fixture")
        for row in _edge_rows(self.dsn):
            self.assertEqual("2026-07-01T00:00:00+00:00", row[3])
            self.assertIsNone(row[4], "the migration must never CLOSE a window")

    def test_the_migration_is_IDEMPOTENT_against_the_populated_store(self):
        """E1, where it means something. MUTATION: drop the `NOT EXISTS` clause from
        `_edge_backfill_sql` and this goes RED — Postgres's partial unique index raises on the
        second pass, which is the same defect surfacing one layer down."""
        self._populate()
        self.teamdb.provision(self.dsn, project_id="fixture")
        first = _edge_rows(self.dsn)
        for _ in range(3):
            self.teamdb.provision(self.dsn, project_id="fixture")
        self.assertEqual(first, _edge_rows(self.dsn), "a re-run must insert nothing")

    def test_the_partial_unique_index_would_have_REFUSED_a_duplicate(self):
        """Why idempotency is not resting on the predicate alone. The index is the backstop, and it
        is only real on a real engine."""
        import psycopg
        from mokata.memory import edges as E
        self._populate()
        self.teamdb.provision(self.dsn, project_id="fixture")
        with self.assertRaises(psycopg.errors.UniqueViolation):
            _conn(self.dsn).execute(
                f"INSERT INTO {self.teamdb.EDGES_TABLE} "
                f"({E.SRC_COLUMN}, {E.DST_COLUMN}, {E.KIND_COLUMN}, {E.VALID_TO_COLUMN}) "
                f"VALUES ('mid-fact', 'old-fact', 'supersedes', NULL)")

    def test_a_CLOSED_edge_does_not_block_a_re_opened_one(self):
        """The other half of PARTIAL: history may sit beside a live relation without colliding with
        it. MUTATION: make the index total and this goes RED — a relation that was true, withdrawn
        and is true again becomes unrepresentable."""
        from mokata.memory import edges as E
        self._populate()
        self.teamdb.provision(self.dsn, project_id="fixture")
        _conn(self.dsn).execute(
            f"UPDATE {self.teamdb.EDGES_TABLE} SET {E.VALID_TO_COLUMN}='2026-07-05T00:00:00+00:00' "
            f"WHERE {E.SRC_COLUMN}='mid-fact'")
        _conn(self.dsn).execute(
            f"INSERT INTO {self.teamdb.EDGES_TABLE} "
            f"({E.SRC_COLUMN}, {E.DST_COLUMN}, {E.KIND_COLUMN}, {E.VALID_TO_COLUMN}) "
            f"VALUES ('mid-fact', 'old-fact', 'supersedes', NULL)")
        rows = _edge_rows(self.dsn, "mid-fact")
        self.assertEqual(2, len(rows))
        self.assertEqual(1, sum(1 for r in rows if r[4] is None), "exactly one OPEN edge")

    def test_a_dangling_ref_is_SKIPPED_and_the_migration_COMPLETES(self):
        """E7's first half, decided by Postgres's own EXISTS rather than by a Python filter."""
        self._populate()
        self.teamdb.provision(self.dsn, project_id="fixture")
        self.assertEqual([], _edge_rows(self.dsn, "dangling"))
        orphans = _conn(self.dsn).execute(
            f"SELECT count(*) FROM {self.teamdb.EDGES_TABLE} e "
            f"WHERE e.kind IN ('supersedes','depends_on') AND NOT EXISTS "
            f"(SELECT 1 FROM {self.teamdb.MEMORY_TABLE} m WHERE m.id = e.dst_id)").fetchone()[0]
        self.assertEqual(0, orphans, "not one edge may point at an item that is not there")
        self.assertEqual(6, _conn(self.dsn).execute(
            f"SELECT count(*) FROM {self.teamdb.MEMORY_TABLE}").fetchone()[0],
            "…and the migration touched no item")

    def test_the_SKIP_COUNT_is_reported_and_STAYS_reported_on_a_re_run(self):
        """E7's second half, and the reason the count is its own statement rather than the INSERT's
        rowcount: a rowcount is ZERO on the (correctly idempotent) second pass, which would tell a
        re-running operator the dangling refs had gone away. They have not.

        MUTATION: derive `skipped_dangling_edges` from the backfill's rowcount and the re-run
        assertion below goes RED."""
        self._populate()
        first = self.teamdb.provision(self.dsn, project_id="fixture")
        self.assertEqual(3, first.skipped_dangling_edges,
                         "GONE-1 via supersedes, GONE-1 via depends_on, GONE-2 via depends_on")
        again = self.teamdb.provision(self.dsn, project_id="fixture")
        self.assertEqual(3, again.skipped_dangling_edges,
                         "the dangling refs are still dangling — a re-run must still say so")

    def test_the_skipped_refs_MIGRATE_once_their_target_lands(self):
        """Skip-and-report is not skip-forever. The operator's remedy (land the missing items, re-run
        `team init`) has to actually work, or the advice the notice gives is advice that does
        nothing."""
        self._populate()
        self.teamdb.provision(self.dsn, project_id="fixture")
        self._seed("GONE-1")
        self._seed("GONE-2")
        result = self.teamdb.provision(self.dsn, project_id="fixture")
        self.assertEqual(0, result.skipped_dangling_edges)
        self.assertEqual({("dangling", "GONE-1", "supersedes"),
                          ("dangling", "GONE-1", "depends_on"),
                          ("dangling", "GONE-2", "depends_on")},
                         {(r[0], r[1], r[2]) for r in _edge_rows(self.dsn, "dangling")})

    def test_an_about_code_anchor_is_never_counted_as_dangling(self):
        """Its dst is a repo path, not a row. `new-fact` carries two of them and the store reports
        only the three genuinely-missing ITEM refs."""
        self._populate()
        self.assertEqual(3, self.teamdb.provision(
            self.dsn, project_id="fixture").skipped_dangling_edges)

    def test_a_malformed_inline_field_does_not_fail_the_migration(self):
        """`supersedes` as a string, `depends_on` as an object, `about_code` as null — a hand-edited
        doc or a teammate's import. Degrade-clean: no edges for that item, and the pass completes."""
        self._populate()
        self.teamdb.provision(self.dsn, project_id="fixture")
        self.assertEqual([], _edge_rows(self.dsn, "malformed"))

    def test_team_init_PRINTS_the_skip_count_to_the_operator(self):
        """E7's surfacing half, end to end: `mokata team init` really runs, really migrates, and the
        operator really sees the number. The unit companion is an AST check; this is the sentence.

        MUTATION (confirmed RED): drop the `emit` from `team.init_team`'s skip guard, or neuter the
        guard to `if False:` — the migration still skips, the skips become invisible, and a silently
        skipped edge is indistinguishable from a migration that simply had nothing to do."""
        from mokata import team
        self._populate()
        lines = []
        with tempfile.TemporaryDirectory() as d:
            surface = _writer(d, self.dsn)   # a real repo, real manifest, team mode
            team.team_init(root=d, surface=surface, assume_yes=True, out=lines.append)
        surfaced = [ln for ln in lines if "SKIPPED" in ln]
        self.assertEqual(1, len(surfaced), f"expected one skip report, got: {lines}")
        self.assertIn("3 reference(s)", surfaced[0])
        self.assertIn("team init", surfaced[0], "…and it must name the remedy")

    def test_a_CLEAN_store_is_not_told_about_a_problem_it_does_not_have(self):
        """The other half of a good report: silence when there is nothing to say. A migration that
        announced '0 skipped' on every init would train the operator to ignore the line that
        matters."""
        from mokata import team
        self._seed("old-fact")
        self._seed("new-fact", supersedes=["old-fact"])
        lines = []
        with tempfile.TemporaryDirectory() as d:
            surface = _writer(d, self.dsn)
            team.team_init(root=d, surface=surface, assume_yes=True, out=lines.append)
        self.assertEqual([], [ln for ln in lines if "SKIPPED" in ln])

    def test_the_migration_asks_the_human_NOTHING(self):
        """E2 on the shared half. MUTATION: route the backfill through any confirm/gate and the
        prompt counter goes above zero. Counted over `builtins.input` AND `WriteGate.submit`, so a
        prompt raised by either route is caught."""
        import builtins
        from mokata.govern import gate as gate_mod
        self._populate()
        prompts = []
        real_input, real_submit = builtins.input, gate_mod.WriteGate.submit

        def _count_input(*a, **kw):
            prompts.append(("input", a))
            return "n"

        def _count_submit(self, *a, **kw):
            prompts.append(("gate", a))
            return real_submit(self, *a, **kw)

        builtins.input, gate_mod.WriteGate.submit = _count_input, _count_submit
        try:
            self.teamdb.provision(self.dsn, project_id="fixture")
        finally:
            builtins.input, gate_mod.WriteGate.submit = real_input, real_submit
        self.assertEqual([], prompts, "the migration moves ALREADY-approved relations — asking "
                                      "again is asking the same question twice")
        self.assertEqual(6, len(_edge_rows(self.dsn)), "…and it really did migrate them")


# ======================================================================================
# E3 · A v4 TEAM FLUSHES BYTE-IDENTICALLY
# ======================================================================================
@unittest.skipUnless(_PG_LIVE, _PG_REASON)
class TestV4TeamIsUntouched(unittest.TestCase):
    """The floor stays at 3 because a v4 store keeps working — proven against a REAL v4 store, the
    only place "the flush does not touch a table that is not there" can be observed."""

    def setUp(self):
        from mokata import teamdb
        self.dsn, self.teamdb = _pg_dsn(), teamdb
        _provision_v4(self.dsn)

    def tearDown(self):
        from mokata.memory import _pg
        _pg.reset_manager()

    def test_a_gated_write_flushes_onto_a_v4_store_without_raising(self):
        """MUTATION: make `_edges_present` return True unconditionally — i.e. make the edge write
        MANDATORY rather than probed — and this goes RED with an UndefinedTable that aborts the
        flush. That is the production failure a v4 team would suffer, and the reason
        `TEAM_SCHEMA_MIN_SUPPORTED` staying at 3 has to be EARNED rather than declared."""
        with tempfile.TemporaryDirectory() as d:
            surface = _writer(d, self.dsn)
            item = _with_edges("v4-item", "value", supersedes=["whatever"],
                               about_code=["src/x.py"])
            _journal_update(surface, item, ledger_id=1, base_revision=None)
            result = _flush(surface)
            self.assertEqual((1, 0), (result.flushed, result.conflicts))
            self.assertEqual("active", _status(self.dsn, "v4-item"))

    def test_the_UPDATE_path_flushes_onto_a_v4_store_too(self):
        """Both CAS branches, not just the INSERT — the projection hangs off each of them."""
        with tempfile.TemporaryDirectory() as d:
            surface = _writer(d, self.dsn)
            _journal_update(surface, _plain_item("v4-item", "v1"), ledger_id=1, base_revision=None)
            self.assertEqual(1, _flush(surface).flushed)
            _journal_update(surface, _with_edges("v4-item", "v2", about_code=["src/x.py"]),
                            ledger_id=2, base_revision=1)
            self.assertEqual(1, _flush(surface).flushed)


# ======================================================================================
# E4 · TWO REAL WRITERS — only the CAS winner projects
# ======================================================================================
@unittest.skipUnless(_PG_LIVE, _PG_REASON)
class TestEdgesAreCasGuarded(_EdgeLivePgCase):
    """The claim DB.S6's I3 makes for items, made for the relations derived from them. Two
    INDEPENDENT installs, one shared table, and Postgres deciding who wins."""

    def test_the_LOSER_of_the_race_writes_NO_edge(self):
        """MUTATION: move `_project_edges_for` above the `rowcount > 0` branch and this goes RED —
        the loser's relations land in the shared graph beside the winner's item, so the projection
        describes a state no approved item ever asserted."""
        with tempfile.TemporaryDirectory() as da, tempfile.TemporaryDirectory() as db:
            a, b = _writer(da, self.dsn), _writer(db, self.dsn)
            self._seed(a, "shared", "A1")

            # both writers read at revision 1; B lands first, A's CAS then misses.
            _journal_update(b, _with_edges("shared", "B-wins", about_code=["src/b.py"]),
                            ledger_id=10, base_revision=1)
            self.assertEqual(1, _flush(b).flushed)
            _journal_update(a, _with_edges("shared", "A-loses", about_code=["src/a.py"]),
                            ledger_id=20, base_revision=1)
            res = _flush(a)
            self.assertEqual((0, 1), (res.flushed, res.conflicts), "A must LOSE the CAS")

            self.assertEqual({"src/b.py"}, {r[1] for r in _edge_rows(self.dsn, "shared")},
                             "only the writer that won the row may have projected")

    def test_the_WINNER_S_projection_carries_the_WINNER_S_approval_id(self):
        """C5/P2 — the trail from a relation back to the human decision that created it is a column,
        not an inference, and under a race it must name the WINNER's decision."""
        with tempfile.TemporaryDirectory() as da, tempfile.TemporaryDirectory() as db:
            a, b = _writer(da, self.dsn), _writer(db, self.dsn)
            self._seed(a, "shared", "A1")
            _journal_update(b, _with_edges("shared", "B-wins", about_code=["src/b.py"]),
                            ledger_id=10, base_revision=1)
            _flush(b)
            _journal_update(a, _with_edges("shared", "A-loses", about_code=["src/a.py"]),
                            ledger_id=20, base_revision=1)
            _flush(a)
            rows = _edge_rows(self.dsn, "shared")
            self.assertEqual([("shared", "src/b.py", "about_code")],
                             [(r[0], r[1], r[2]) for r in rows])
            self.assertEqual(10, rows[0][5])

    def test_a_withdrawn_relation_CLOSES_and_the_row_SURVIVES_in_postgres(self):
        """R3's never-delete, on the shared table. MUTATION: make `close_withdrawn_sql` emit a
        DELETE and this goes RED — the history of what was related, and when, is destroyed."""
        with tempfile.TemporaryDirectory() as d:
            a = _writer(d, self.dsn)
            self._seed(a, "shared", "v1")
            _journal_update(a, _with_edges("shared", "v2", about_code=["src/x.py"]),
                            ledger_id=2, base_revision=1)
            self.assertEqual(1, _flush(a).flushed)
            self.assertEqual(1, len(_edge_rows(self.dsn, "shared")))

            _journal_update(a, _with_edges("shared", "v3"),      # about_code withdrawn
                            ledger_id=3, base_revision=2)
            self.assertEqual(1, _flush(a).flushed)

            rows = _edge_rows(self.dsn, "shared")
            self.assertEqual(1, len(rows), "the row must SURVIVE")
            self.assertIsNotNone(rows[0][4], "…closed, with a stamped `valid_to`")

    def test_a_gated_PRUNE_closes_the_pruned_item_s_edges(self):
        """The item row is gone; the relations it asserted were true for a while and the projection
        says so honestly instead of erasing them."""
        from mokata import team_journal, teamdb
        with tempfile.TemporaryDirectory() as d:
            a = _writer(d, self.dsn)
            self._seed(a, "doomed", "v1")
            _journal_update(a, _with_edges("doomed", "v2", about_code=["src/x.py"]),
                            ledger_id=2, base_revision=1)
            self.assertEqual(1, _flush(a).flushed)

            team_journal.record_team_write(
                a, op=team_journal.OP_DELETE, table=teamdb.MEMORY_TABLE, key="doomed",
                payload={"id": "doomed"}, ledger_id=9, project=_PROJECT, actor="tester",
                base_revision=2)
            self.assertEqual(1, _flush(a).flushed)

            self.assertIsNone(_status(self.dsn, "doomed"), "the item is pruned")
            rows = _edge_rows(self.dsn, "doomed")
            self.assertEqual(1, len(rows), "its edges are NOT deleted")
            self.assertIsNotNone(rows[0][4], "…they are closed")


# ======================================================================================
# E6 · THE DURABLE BOUNDARY — a rolled-back approval leaves no edge behind
# ======================================================================================
@unittest.skipUnless(_PG_LIVE, _PG_REASON)
class TestGroupRollbackTakesEdgesToo(_EdgeLivePgCase):
    """E6's team-mode half, where "one gate-approved commit" is a real BEGIN/ROLLBACK.

    I1's group transaction exists so an approval lands whole or not at all. The projection has to be
    INSIDE it, or a rolled-back approval leaves relations asserting a state its own items never
    reached — the durable half-apply the group transaction was built to make impossible."""

    def test_a_rolled_back_approval_leaves_ZERO_edge_rows(self):
        """MUTATION: give `_project_edges_for` its own connection (or commit inside it) and this
        goes RED — the edges survive the rollback that removed the items they describe."""
        with tempfile.TemporaryDirectory() as da, tempfile.TemporaryDirectory() as db:
            a, b = _writer(da, self.dsn), _writer(db, self.dsn)
            self._seed(a, "old-fact", "the old value")
            self._seed(a, "new-fact", "placeholder")

            # B takes `new-fact`, so A's second write loses its CAS and rolls the group back.
            _journal_update(b, _plain_item("new-fact", "B-took-it"), ledger_id=10, base_revision=1)
            self.assertEqual(1, _flush(b).flushed)

            # ONE approval, two writes — retire the old fact, install the new one, both with edges.
            _journal_update(a, _with_edges("old-fact", "the old value", about_code=["src/old.py"]),
                            ledger_id=99, base_revision=1)
            _journal_update(a, _with_edges("new-fact", "the new value", supersedes=["old-fact"],
                                           about_code=["src/new.py"]),
                            ledger_id=99, base_revision=1)
            res = _flush(a)
            self.assertEqual((0, 2), (res.flushed, res.conflicts), "I1's rollback is the premise")

            self.assertEqual([], _edge_rows(self.dsn),
                             "an approval that did not land as a whole must leave no half of it "
                             "behind — edges included")

    def test_the_same_approval_lands_its_items_AND_its_edges_together_when_it_wins(self):
        """The other side of the same boundary: uncontended, the whole approval commits — both
        items and every relation they assert, in one transaction, under one approval id."""
        with tempfile.TemporaryDirectory() as d:
            a = _writer(d, self.dsn)
            self._seed(a, "old-fact", "the old value")
            self._seed(a, "new-fact", "placeholder")
            _journal_update(a, _with_edges("old-fact", "the old value", about_code=["src/old.py"]),
                            ledger_id=99, base_revision=1)
            _journal_update(a, _with_edges("new-fact", "the new value", supersedes=["old-fact"]),
                            ledger_id=99, base_revision=1)
            res = _flush(a)
            self.assertEqual((2, 0), (res.flushed, res.conflicts))
            self.assertEqual({("new-fact", "old-fact", "supersedes"),
                              ("old-fact", "src/old.py", "about_code")},
                             {(r[0], r[1], r[2]) for r in _edge_rows(self.dsn)})
            for row in _edge_rows(self.dsn):
                self.assertEqual(99, row[5], "every edge carries the approval that authorised it")


if __name__ == "__main__":
    unittest.main()
