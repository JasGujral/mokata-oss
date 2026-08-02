"""DB.S7a — the memory EDGE SUBSTRATE, pinned contract by contract.

Seven claims, E1–E7. Each one is stated as the MUTATION that turns it red, because a green
assertion that cannot fail is not a contract — it is decoration. Every mutation named in a
docstring below was applied to the SOURCE, the test confirmed RED, and the source restored.

What is deliberately NOT here, and lives in `tests/integration/test_db_s7a_live_db.py` instead:
the claims a Python double cannot make. "The v5 migration is idempotent against a POPULATED v4
store", "two real writers cannot both project a conflicting edge set", and "a dangling ref is
skipped by Postgres's own EXISTS" are statements about an engine, and a dict standing in for the
table can only show mokata asking for the right thing, never that the right thing happened.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import sqlite3
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import teamdb
from mokata.memory import edges as E
from mokata.memory.backends import SQLiteBackend
from mokata.memory.item import MemoryItem


def _item(subject="s", value="v", **kw):
    return MemoryItem.create(subject, value, **kw)


def _tree_snapshot(root):
    """Every byte under `root` — the store, its WAL sidecars, the ledger, the manifest.

    Whole-tree rather than store-only, and borrowed deliberately from DB.S7d's P10 pin: the write
    that breaks a "nothing is written" charter is by definition the one nobody anticipated, so a
    snapshot scoped to the file the pin's author thought of would miss it. Bytes, not mtimes."""
    snap = {}
    for base, _dirs, files in os.walk(root):
        for name in sorted(files):
            p = os.path.join(base, name)
            with open(p, "rb") as fh:
                snap[os.path.relpath(p, root)] = fh.read()
    return snap


def _rows(path, sql="SELECT src_id, dst_id, kind, valid_to FROM memory_edges ORDER BY seq"):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


# ======================================================================================
# E5 · THE CLOSED SET — 8 declared, 3 wired, everything else refused
# ======================================================================================
class TestE5TheClosedTypedSet(unittest.TestCase):
    """The SET is the contract. A kind admitted later is a reviewed schema change, not a string a
    caller invents at a write site."""

    def test_all_eight_kinds_are_declared(self):
        """MUTATION: drop any kind from `EDGE_KINDS` and this goes RED. The list is asserted by
        NAME rather than by length so a rename cannot slip past a count."""
        self.assertEqual(
            ("supersedes", "depends_on", "derives_from", "contradicts",
             "about_code", "decided_in", "used_by", "promoted_from"), E.EDGE_KINDS)
        self.assertEqual(8, len(set(E.EDGE_KINDS)), "the closed set must have no duplicates")

    def test_exactly_the_kinds_with_a_producer_are_wired(self):
        """The WIRED kinds are exactly the persisted doc-JSON fields on `MemoryItem` — which is what
        makes them MIGRATABLE rather than inventable. MUTATION: wire `contradicts` (add it to
        `_ITEM_FIELD`) and this goes RED, as it should: contradiction is detected at READ time and
        never persisted, so there is no producer to migrate and wiring it would mean INVENTING
        edges.

        WAS "exactly three" until 2026-08-01. `derives_from` joined the wired set when its producer
        was built — an approved SUMMARIZE lands a new item and is the only place that knows which
        items it was distilled out of. The count moved because the CODE moved; the RULE ("wired iff
        a persisted producer field exists") is what this pin actually guards and it is unchanged,
        which is why the assertion below is the rule and not the number."""
        self.assertEqual(("supersedes", "depends_on", "derives_from", "about_code"),
                         E.WIRED_KINDS)
        for kind in E.WIRED_KINDS:
            self.assertTrue(hasattr(MemoryItem("s", "v"), E._ITEM_FIELD[kind]),
                            f"'{kind}' claims a producer field the item model does not have")
        self.assertEqual(4, len(set(E.EDGE_KINDS) - set(E.WIRED_KINDS)),
                         "four kinds are DECLARED-ONLY: contradicts (detected at read time, never "
                         "persisted), used_by (K5 not built), decided_in and promoted_from (no "
                         "producer at all)")

    def test_an_out_of_set_kind_is_REFUSED_not_stored(self):
        """MUTATION: make `validate_kind` return `kind` unconditionally and this goes RED. A hard
        refusal, never a coerce-to-default: an unrecognised kind in the table would be a relation no
        traversal knows how to interpret, and silently storing it is how a CLOSED set stops being
        one."""
        for bogus in ("relates_to", "SUPERSEDES", "", "supersedes ", "causes"):
            with self.assertRaises(E.EdgeKindError, msg=f"'{bogus}' was accepted"):
                E.validate_kind(bogus)

    def test_the_refusal_names_the_whole_set_so_the_reader_can_act(self):
        with self.assertRaises(E.EdgeKindError) as ctx:
            E.validate_kind("relates_to")
        message = str(ctx.exception)
        for kind in E.EDGE_KINDS:
            self.assertIn(kind, message)
        self.assertIn("CLOSED", message)

    def test_constructing_an_edge_validates_its_kind(self):
        """The refusal is on the TYPE, not only on the helper — so no write path can reach the DB
        with an out-of-set kind by skipping `validate_kind` and building a `MemoryEdge` directly.
        MUTATION: delete `MemoryEdge.__post_init__` and this goes RED."""
        with self.assertRaises(E.EdgeKindError):
            E.MemoryEdge(src_id="a", dst_id="b", kind="relates_to")

    def test_only_item_target_kinds_can_dangle(self):
        """`about_code` points at a code path in the repo, not a row in the memory table, so "the
        target is not an item" is what a CORRECT about_code edge looks like. MUTATION: add
        `about_code` to `ITEM_TARGET_KINDS` and E7's count reports a dangling ref for every properly
        formed code anchor."""
        self.assertNotIn(E.ABOUT_CODE, E.ITEM_TARGET_KINDS)
        self.assertTrue(set(E.ITEM_TARGET_KINDS) <= set(E.EDGE_KINDS))


# ======================================================================================
# E4 (the bi-temporal half) · ONE VALIDITY AXIS, item vocabulary, no `expired`
# ======================================================================================
class TestE4BiTemporalOneAxis(unittest.TestCase):
    """Doc 02 decision #1, as a contract rather than a paragraph: an edge is never more temporally
    sophisticated than the item it connects."""

    def test_the_edge_wears_the_ITEM_S_OWN_two_column_names(self):
        """MUTATION: rename the edge columns to `valid_at`/`invalid_at` (the `84:173` names this
        decision amended down) and this goes RED. Same concept, same words, or the store speaks two
        vocabularies for one axis."""
        from mokata.memory import lifecycle
        self.assertEqual(lifecycle.VALID_FROM_COLUMN, E.VALID_FROM_COLUMN)
        self.assertEqual(lifecycle.VALID_TO_COLUMN, E.VALID_TO_COLUMN)

    def test_there_is_NO_transaction_time_pair_on_an_edge(self):
        """The load-bearing half of the decision. `created_at` is PROVENANCE (doc 55's own column,
        `55:27-28`); `expired` does not exist. MUTATION: add an `expired` field to `MemoryEdge` and
        this goes RED — items express retirement structurally and deliberately have no such stamp,
        so an edge with one would be the ONLY place in mokata where the valid/transaction
        distinction is representable at all."""
        self.assertIn(E.CREATED_AT_COLUMN, E.EDGE_COLUMNS)
        for banned in ("expired", "invalid_at", "valid_at", "t_valid", "t_invalid"):
            self.assertNotIn(banned, E.EDGE_COLUMNS)
        self.assertFalse(hasattr(E.MemoryEdge(src_id="a", dst_id="b", kind="supersedes"),
                                 "expired"))

    def test_the_item_did_NOT_grow_a_transaction_time_pair_either(self):
        """The branch that was NOT taken, pinned so it cannot be half-taken later. The two-axis
        resolution would have obliged items to gain the pair IN THE SAME CHANGE; the one-axis
        resolution obliges them to gain nothing."""
        item = MemoryItem("s", "v")
        for banned in ("expired", "invalid_at", "valid_at"):
            self.assertFalse(hasattr(item, banned),
                             f"an item grew '{banned}' — the two halves must move together")

    def test_an_edge_window_opens_where_its_ITEM_S_window_opens(self):
        """ONE rule for both the live projection and the migration, so an edge rebuilt from a doc
        lands on the same instant as the edge written from it. MUTATION: make `open_window_of`
        return `now` and this goes RED."""
        from mokata.memory import lifecycle
        item = _item(created_at="2026-01-02T03:04:05+00:00")
        self.assertEqual(lifecycle.open_window(item), E.open_window_of(item.to_doc()))
        item.valid_from = "2025-12-01T00:00:00+00:00"
        self.assertEqual("2025-12-01T00:00:00+00:00", E.open_window_of(item.to_doc()))

    def test_absence_reads_as_OPEN_on_an_edge_exactly_as_on_an_item(self):
        edge = E.MemoryEdge(src_id="a", dst_id="b", kind="supersedes")
        self.assertTrue(edge.is_open)
        self.assertFalse(E.MemoryEdge(src_id="a", dst_id="b", kind="supersedes",
                                      valid_to="2026-01-01T00:00:00+00:00").is_open)

    def test_a_withdrawn_relation_CLOSES_and_the_row_survives(self):
        """R3's "contradiction = invalidation, never deletion", on the projection. MUTATION: make
        `close_withdrawn_sql` emit `DELETE FROM` instead of `UPDATE … SET valid_to` and this goes
        RED — the row count drops and the only record that the relation ever held is destroyed."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "m.db")
            back = SQLiteBackend(path)
            target = _item("target", "T")
            src = _item("src", "S", supersedes=[target.id])
            back.put(target)
            back.put(src)
            self.assertEqual(1, len(back.open_edges(src.id)))

            src.supersedes = []
            back.put(src)
            self.assertEqual([], back.open_edges(src.id), "the relation is no longer asserted")
            rows = _rows(path)
            self.assertEqual(1, len(rows), "the row must SURVIVE — closed, not deleted")
            self.assertIsNotNone(rows[0][3], "`valid_to` must be stamped, not left open")

    def test_re_asserting_a_withdrawn_relation_opens_a_NEW_window_beside_the_old(self):
        """Why the unique index is PARTIAL rather than a plain primary key. MUTATION: make the index
        total (drop `WHERE valid_to IS NULL`) and the re-assertion is REFUSED by the engine — a
        relation that was true, withdrawn and is true again becomes unrepresentable."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "m.db")
            back = SQLiteBackend(path)
            target = _item("target", "T")
            src = _item("src", "S", supersedes=[target.id])
            back.put(target)
            back.put(src)
            src.supersedes = []
            back.put(src)
            src.supersedes = [target.id]
            back.put(src)

            rows = _rows(path)
            self.assertEqual(2, len(rows), "history plus the re-opened window")
            self.assertEqual(1, sum(1 for r in rows if r[3] is None), "exactly one OPEN edge")
            self.assertEqual(1, len(back.open_edges(src.id)))


# ======================================================================================
# E1 · THE MIGRATION IS IDEMPOTENT
# ======================================================================================
class TestE1MigrationIdempotency(unittest.TestCase):
    """Re-running the migration must match ZERO rows, not duplicate the corpus."""

    def test_re_opening_the_store_does_not_duplicate_edges(self):
        """The LOCAL half. MUTATION: drop the `WHERE NOT EXISTS` clause from `insert_open_sql` and
        this goes RED (or the partial unique index raises, which is the same defect surfacing one
        layer down)."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "m.db")
            back = SQLiteBackend(path)
            target = _item("target", "T")
            src = _item("src", "S", supersedes=[target.id], depends_on=[target.id],
                        about_code=["src/x.py"])
            back.put(target)
            back.put(src)
            first = _rows(path)
            self.assertEqual(3, len(first))

            for _ in range(3):
                SQLiteBackend(path)          # re-open: the constructor re-runs the migration
                back.put(src)                # and re-put: the live projection re-runs too
            self.assertEqual(first, _rows(path), "a second pass must change nothing")

    def test_the_backfill_is_idempotent_even_with_its_stamp_CLEARED(self):
        """The stamp keeps the scan off every per-operation open; the PREDICATE is what makes the
        migration safe. Both, not either — MUTATION: remove the `WHERE NOT EXISTS` and rely on the
        stamp alone, and this goes RED the moment a store's stamp is reset (a restore, a repair, a
        future generation bump)."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "m.db")
            back = SQLiteBackend(path)
            target = _item("target", "T")
            back.put(target)
            back.put(_item("src", "S", supersedes=[target.id]))
            before = _rows(path)

            conn = sqlite3.connect(path)
            conn.execute("PRAGMA user_version=0")
            conn.commit()
            conn.close()
            SQLiteBackend(path)              # every generation re-runs from scratch
            self.assertEqual(before, _rows(path))

    def test_a_doc_listing_the_same_ref_twice_yields_ONE_edge(self):
        """`supersedes: [a, a]` asserts one relation. MUTATION: drop the dedupe in `edges_from_doc`
        and the partial unique index refuses the second insert mid-migration."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "m.db")
            back = SQLiteBackend(path)
            target = _item("target", "T")
            back.put(target)
            back.put(_item("src", "S", supersedes=[target.id, target.id]))
            self.assertEqual(1, len(_rows(path)))

    def test_the_shared_backfill_carries_the_not_exists_predicate(self):
        """The Postgres half of the same claim, at the SQL level — the behaviour against a real
        engine is proven in the live-DB leg, which is where an idempotency claim actually means
        something."""
        sql = " || ".join(teamdb.provision_sql())
        self.assertIn(f"CREATE TABLE IF NOT EXISTS {teamdb.EDGES_TABLE}", sql)
        for stmt in teamdb._edge_backfill_sql():
            self.assertIn("NOT EXISTS", stmt)
            self.assertIn("DISTINCT", stmt)


# ======================================================================================
# E2 · THE MIGRATION ASKS NOBODY ANYTHING
# ======================================================================================
class TestE2MigrationNeedsNoHumanReApproval(unittest.TestCase):
    """It moves relations a human ALREADY approved. Re-prompting would be asking the same question
    twice, which is how a gate stops meaning anything."""

    def test_the_local_migration_prompts_ZERO_times(self):
        """MUTATION: route `_backfill_edges` through `WriteGate.submit` (or any confirm callable)
        and the prompt count goes above zero — RED. The counter is installed over the module's own
        confirm seam AND over `builtins.input`, so a prompt raised by any route is caught."""
        import builtins
        from mokata.govern import gate as gate_mod

        prompts = []
        real_input, real_submit = builtins.input, gate_mod.WriteGate.submit

        def _counting_input(*a, **kw):
            prompts.append(("input", a))
            return "n"

        def _counting_submit(self, *a, **kw):
            prompts.append(("gate", a))
            return real_submit(self, *a, **kw)

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "m.db")
            seed = SQLiteBackend(path)
            target = _item("target", "T")
            seed.put(target)
            seed.put(_item("src", "S", supersedes=[target.id], about_code=["src/x.py"]))
            # rewind the stamp so the NEXT open genuinely runs the migration
            conn = sqlite3.connect(path)
            conn.execute("PRAGMA user_version=2")
            conn.execute("DROP TABLE memory_edges")
            conn.commit()
            conn.close()

            builtins.input, gate_mod.WriteGate.submit = _counting_input, _counting_submit
            try:
                migrated = SQLiteBackend(path)
            finally:
                builtins.input, gate_mod.WriteGate.submit = real_input, real_submit

            self.assertEqual([], prompts, "the migration must ask NOTHING — it moves already-"
                                          "approved relations between representations")
            self.assertEqual(2, len(_rows(path)), "…and it must actually have migrated them")
            self.assertEqual(0, migrated.edge_backfill_skipped)

    def test_the_shared_migration_is_pure_SQL_with_no_gate_on_the_path(self):
        """The Postgres half, structurally: `provision_sql` returns STATEMENTS, and `provision` runs
        them. No callable, no confirm, nothing that can ask. MUTATION: add a confirm parameter to
        `teamdb.provision` and this goes RED."""
        import inspect
        for fn in (teamdb.provision, teamdb.provision_sql, teamdb._edge_backfill_sql):
            params = set(inspect.signature(fn).parameters)
            self.assertEqual(set(), params & {"confirm", "assume_yes", "gate", "ledger"},
                             f"{fn.__name__} grew a human-interaction parameter")
        self.assertTrue(all(isinstance(s, str) for s in teamdb._edge_backfill_sql()))


# ======================================================================================
# E3 · A v4 TEAM DEGRADES BYTE-IDENTICALLY
# ======================================================================================
class _NoEdgeTableConn:
    """A v4 shared store: `mokata_memory` is there, `mokata_memory_edges` is NOT. `to_regclass`
    answers NULL for it, exactly as Postgres does — the honest shape of an un-migrated team."""

    def __init__(self):
        self.executed = []
        self.rowcount = 1

    def execute(self, sql, params=()):
        self.executed.append(sql)
        if "to_regclass" in sql:
            return _Cur([(None,)])
        if teamdb.EDGES_TABLE in sql:
            raise AssertionError("a v4 store has no edge table — this statement must never run")
        return _Cur([])


class _Cur:
    def __init__(self, rows):
        self._rows, self.rowcount = rows, 1

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class TestE3AV4TeamIsUntouched(unittest.TestCase):
    """`TEAM_SCHEMA_MIN_SUPPORTED` stays 3 — and this is what buys that."""

    def test_the_floor_did_not_move_and_the_version_did(self):
        """MUTATION: raise the floor to 4 or 5 and this goes RED. It would fail-close every existing
        team on upgrade for a table nothing yet requires."""
        self.assertEqual(5, teamdb.TEAM_SCHEMA_VERSION)
        self.assertEqual(3, teamdb.TEAM_SCHEMA_MIN_SUPPORTED)

    def test_the_floor_comment_states_the_tripwire_that_would_move_it(self):
        """The floor is only honest while the condition that would raise it is WRITTEN DOWN. Pinned
        because the next stage reads this constant, not this test."""
        import inspect
        source = inspect.getsource(teamdb)
        head = source.split("TEAM_SCHEMA_MIN_SUPPORTED = 3")[0]
        self.assertIn("MANDATORY", head.upper())
        self.assertIn("FLOOR MOVES IN THE SAME CHANGE", head.upper())

    def test_a_v4_store_flushes_WITHOUT_TOUCHING_the_edge_table(self):
        """THE degrade pin. MUTATION: make `_edges_present` return True unconditionally — i.e. make
        the edge read MANDATORY rather than probed — and this goes RED with "a v4 store has no edge
        table", which is precisely the failure a v4 team would suffer in production."""
        from mokata import team_journal
        conn = _NoEdgeTableConn()
        entry = team_journal.JournalEntry(
            id="e1", op=team_journal.OP_UPDATE, table=teamdb.MEMORY_TABLE, key="x",
            payload={"id": "x", "mtype": "persistent", "subject": "s", "status": "active",
                     "doc": json.dumps({"id": "x", "supersedes": ["y"]}), "project": "p"},
            ledger_id=1, base_revision=1, actor="t")
        outcome = team_journal.apply_memory_write(conn, entry)
        self.assertEqual("ok", outcome.status)
        self.assertFalse([s for s in conn.executed if teamdb.EDGES_TABLE in s])

    def test_the_probe_FAILS_CLOSED_when_it_cannot_tell(self):
        """Unknown is not permission — the `min_supported` posture, applied to a capability. A
        connection that raises on the probe must be treated as v4, never as v5: guessing v5 would
        abort the caller's transaction on the very next statement."""
        from mokata import team_journal

        class _Blind:
            def execute(self, *a, **kw):
                raise RuntimeError("no catalog here")

        self.assertFalse(team_journal._edges_present(_Blind()))

    def test_a_v4_backend_reports_no_edges_instead_of_raising(self):
        from mokata.memory.backends import PostgresBackend
        back = PostgresBackend(conn=_NoEdgeTableConn(), project="p")
        self.assertFalse(back.supports_edges)
        self.assertEqual([], back.open_edges("anything"))


# ======================================================================================
# E4 · CAS-GUARDED — only the writer that WON the row projects
# ======================================================================================
class _CasConn:
    """A shared store that answers a CAS the way Postgres does: the UPDATE matches or it doesn't."""

    def __init__(self, *, cas_wins, edges=True):
        self.cas_wins, self._edges = cas_wins, edges
        self.edge_statements = []

    def execute(self, sql, params=()):
        if "to_regclass" in sql:
            return _Cur([("t",)] if self._edges else [(None,)])
        if teamdb.EDGES_TABLE in sql:
            self.edge_statements.append(sql)
            return _Cur([])
        if sql.strip().upper().startswith(("UPDATE", "INSERT", "DELETE")):
            cur = _Cur([])
            cur.rowcount = 1 if self.cas_wins else 0
            return cur
        return _Cur([])


class TestE4EdgesAreCasGuarded(unittest.TestCase):
    """No second CAS, and none needed: the projection sits behind the ITEM's compare-and-set, so a
    losing writer never reaches it."""

    @staticmethod
    def _entry(op="memory_update"):
        from mokata import team_journal
        return team_journal.JournalEntry(
            id="e", op=op, table=teamdb.MEMORY_TABLE, key="x",
            payload={"id": "x", "mtype": "persistent", "subject": "s", "status": "active",
                     "doc": json.dumps({"id": "x", "supersedes": ["y"]}), "project": "p"},
            ledger_id=7, base_revision=1, actor="t")

    def test_the_CAS_WINNER_projects(self):
        from mokata import team_journal
        conn = _CasConn(cas_wins=True)
        self.assertEqual("ok", team_journal.apply_memory_write(conn, self._entry()).status)
        self.assertTrue(conn.edge_statements, "the winner must maintain the projection")

    def test_the_CAS_LOSER_projects_NOTHING(self):
        """MUTATION: move `_project_edges_for` above the `rowcount > 0` branch and this goes RED —
        two writers racing one item would then both write edges, and the shared graph would carry a
        relation set no writer's approved item ever asserted."""
        from mokata import team_journal
        conn = _CasConn(cas_wins=False)
        self.assertEqual("conflict", team_journal.apply_memory_write(conn, self._entry()).status)
        self.assertEqual([], conn.edge_statements,
                         "a writer that LOST the row must not have written an edge")

    def test_a_gated_PRUNE_closes_the_edges_and_deletes_none(self):
        """Never-delete reaches the projection too. MUTATION: make `_close_edges_for` issue a
        `DELETE FROM` and this goes RED."""
        from mokata import team_journal
        conn = _CasConn(cas_wins=True)
        self.assertEqual("ok", team_journal.apply_memory_write(
            conn, self._entry(op=team_journal.OP_DELETE)).status)
        self.assertTrue(conn.edge_statements)
        for sql in conn.edge_statements:
            self.assertNotIn("DELETE", sql.upper())
            self.assertIn("valid_to", sql)

    def test_the_edge_inherits_the_ITEM_S_approval_id(self):
        """C5/P2 — the trail from a relation back to the human decision that created it is a COLUMN,
        not an inference. MUTATION: pass `approval_ledger_id=None` in `_project_edges_for` and this
        goes RED."""
        from mokata import team_journal
        captured = []

        class _Capture(_CasConn):
            def execute(self, sql, params=()):
                if teamdb.EDGES_TABLE in sql and sql.strip().upper().startswith("INSERT"):
                    captured.append(params)
                return super().execute(sql, params)

        team_journal.apply_memory_write(_Capture(cas_wins=True), self._entry())
        self.assertTrue(captured)
        self.assertEqual(7, captured[0][7], "the edge row must carry the approval ledger id")


# ======================================================================================
# E6 · AUTO-ATTACH RIDES THE GATE — pinned BEHAVIOURALLY, at the durable boundary
# ======================================================================================
class TestE6AutoAttachRidesTheGate(unittest.TestCase):
    """The claim is not "the code looks gated". It is: an APPROVED write lands the item and its
    edges in ONE commit, a DECLINED one leaves ZERO edge rows, and the refusal path changes not one
    byte on disk.

    Pinned behaviourally on purpose, and the reason is the systemic SI.6 finding this stage files:
    SI.6's zero-bypass sweep is a STATIC scan over a fixed vocabulary of write NAMES, so a durable
    write reached through a helper it does not know the name of is invisible to it. A structural
    "the projection is called from `put`" assertion would inherit exactly that blindness. Bytes
    cannot be fooled by a delegation."""

    def _store(self, root):
        from mokata.memory import MemoryStore
        back = SQLiteBackend(os.path.join(root, "memory.db"))
        return MemoryStore(back), back

    def test_an_APPROVED_write_lands_the_item_and_its_edges_together(self):
        with tempfile.TemporaryDirectory() as d:
            store, back = self._store(d)
            target = store.remember(_item("target", "T"), confirm=lambda _t: True)
            self.assertTrue(target.committed)
            src = _item("src", "S", supersedes=[target.item.id], about_code=["src/x.py"])
            self.assertTrue(store.remember(src, confirm=lambda _t: True).committed)

            kinds = sorted(e.kind for e in back.open_edges(src.id))
            self.assertEqual(["about_code", "supersedes"], kinds)

    def test_DECLINING_the_item_s_gate_leaves_ZERO_edge_rows(self):
        """THE pin. MUTATION: move `project_edges` out of `put` and call it from `remember` BEFORE
        `_gated_commit` (the shape a well-meaning refactor produces when it wants the edges "ready"
        for the gate's preview) and this goes RED: the declined write leaves edges behind for an
        item that was never stored."""
        with tempfile.TemporaryDirectory() as d:
            store, back = self._store(d)
            target = store.remember(_item("target", "T"), confirm=lambda _t: True)
            path = os.path.join(d, "memory.db")
            before = _rows(path)

            src = _item("src", "S", supersedes=[target.item.id], depends_on=[target.item.id],
                        about_code=["src/x.py"])
            result = store.remember(src, confirm=lambda _t: False)

            self.assertFalse(result.committed)
            self.assertEqual([], back.open_edges(src.id))
            self.assertEqual(before, _rows(path), "a declined write must add no edge row")

    def test_the_REFUSAL_path_is_byte_identical_on_disk(self):
        """Whole-tree bytes, not just the edge table — a declined write must not leave a trace
        anywhere, including in a sidecar nobody thought to check."""
        with tempfile.TemporaryDirectory() as d:
            store, _back = self._store(d)
            store.remember(_item("target", "T"), confirm=lambda _t: True)
            before = _tree_snapshot(d)

            store.remember(_item("src", "S", supersedes=["target"], about_code=["src/x.py"]),
                           confirm=lambda _t: False)
            self.assertEqual(before, _tree_snapshot(d))

    def test_the_edge_and_the_item_land_in_ONE_commit_not_two(self):
        """The durable BOUNDARY, observed rather than argued: a `put` whose commit never happens
        must leave NEITHER the item nor its edges on disk.

        The wrapper swallows `commit()` instead of raising, and that shape is deliberate — it is
        what makes the test discriminating. A raising commit proves nothing, because a projection
        that opened its OWN connection would be wrapped too and would raise identically. A
        SWALLOWED commit means: whatever the projection wrote on the caller's connection is
        discarded, and anything it wrote anywhere ELSE survives.

        MUTATIONS, both confirmed RED: (a) commit inside `project_edges`, (b) project on a fresh
        `sqlite3.connect(self.path)`. Either one leaves edge rows describing an item that was never
        stored — the half-applied state the one-commit boundary exists to make impossible."""
        from contextlib import contextmanager

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "memory.db")
            back = SQLiteBackend(path)
            target = _item("target", "T")
            back.put(target)
            before = _rows(path)

            real_connect = back._connect

            class _NeverCommits:
                """Executes faithfully; drops the commit on the floor."""

                def __init__(self, conn):
                    self._conn = conn

                def execute(self, *a, **kw):
                    return self._conn.execute(*a, **kw)

                def commit(self):
                    self._conn.rollback()

            @contextmanager
            def _wrapped():
                with real_connect() as conn:
                    yield _NeverCommits(conn)

            back._connect = _wrapped
            try:
                back.put(_item("src", "S", supersedes=[target.id], about_code=["src/x.py"]))
            finally:
                back._connect = real_connect

            self.assertEqual(before, _rows(path),
                             "an edge row survived a commit the item write did not get — the two "
                             "are not landing on one connection in one transaction")
            self.assertEqual(1, len(_rows(path, "SELECT id FROM memory")),
                             "…and the item did not survive either, which is the other half")

    def test_the_projection_has_no_writer_of_its_own(self):
        """The structural companion to the behavioural pins above — kept because it says something
        they cannot: `memory/edges.py` opens NOTHING. It builds SQL and executes it on a connection
        SOMEBODY ELSE owns and will commit, which is exactly why the projection cannot escape its
        caller's transaction (and therefore cannot escape the gate).

        Walked as an AST over real CALLS rather than grepped over source text — a substring scan
        would match `insert_open_sql` for `open(` and this module's own prose for `neo4j`, i.e. it
        would fail on its own documentation while a real `sqlite3.connect` hidden behind an alias
        walked past. (Learned here: the naive form did both.)"""
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(E))
        called = {ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
        for forbidden in ("open", "connect", "sqlite3.connect", "conn.commit", "commit"):
            self.assertNotIn(forbidden, called,
                             f"the edge module called `{forbidden}` — it is a projection over a "
                             f"connection its CALLER owns, never a store of its own")
        imported = {a.name.split(".")[0] for n in ast.walk(tree)
                    if isinstance(n, ast.Import) for a in n.names}
        imported |= {(n.module or "").split(".")[0] for n in ast.walk(tree)
                     if isinstance(n, ast.ImportFrom)}
        self.assertEqual(set(), imported & {"sqlite3", "psycopg", "os", "pathlib"},
                         "the edge module imports no storage or filesystem machinery")


# ======================================================================================
# E7 · A DANGLING REF IS SKIPPED **AND THE SKIP IS SURFACED**
# ======================================================================================
class TestE7DanglingRefsAreSkippedAndReported(unittest.TestCase):
    """Never orphan an edge, never fail the migration — and never skip in silence, which is the
    half that makes the other two safe to have."""

    def _store_with_a_dangling_ref(self, d):
        path = os.path.join(d, "m.db")
        back = SQLiteBackend(path)
        present = _item("present", "P")
        back.put(present)
        src = _item("src", "S", supersedes=["GONE-1"], depends_on=["GONE-1", present.id],
                    about_code=["src/x.py"])
        back.put(src)
        conn = sqlite3.connect(path)
        conn.execute("DROP TABLE memory_edges")
        conn.execute("PRAGMA user_version=2")
        conn.commit()
        conn.close()
        return path, present, src

    def test_the_dangling_ref_is_SKIPPED_and_the_migration_completes(self):
        """MUTATION: drop the `dst_id not in known` guard and this goes RED — the table gains an
        edge pointing at an item that does not exist, which is exactly the orphan the skip
        prevents."""
        with tempfile.TemporaryDirectory() as d:
            path, present, src = self._store_with_a_dangling_ref(d)
            back = SQLiteBackend(path)

            dsts = {(r[1], r[2]) for r in _rows(path)}
            self.assertEqual({(present.id, "depends_on"), ("src/x.py", "about_code")}, dsts)
            self.assertNotIn("GONE-1", {dst for dst, _k in dsts})
            self.assertIsNotNone(back.get(src.id), "the migration completed — nothing failed")
            self.assertIsNotNone(back.get(present.id), "…and no item was harmed by the skip")

    def test_the_SKIP_COUNT_is_surfaced_not_swallowed(self):
        """THE second half of E7, and the one a "skip dangling refs" implementation usually forgets.
        MUTATION: return 0 from `_backfill_edges` (or stop assigning `edge_backfill_skipped`) and
        this goes RED — the skips still happen, and become indistinguishable from a migration that
        simply had nothing to do."""
        with tempfile.TemporaryDirectory() as d:
            path, _present, _src = self._store_with_a_dangling_ref(d)
            self.assertEqual(2, SQLiteBackend(path).edge_backfill_skipped,
                             "GONE-1 dangles from BOTH `supersedes` and `depends_on` — two "
                             "distinct (src, dst, kind) skips")

    def test_one_missing_target_in_two_fields_is_counted_per_relation_not_per_ref(self):
        """A store is not reported as having ten problems because one item names one missing target
        ten times in one field."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "m.db")
            back = SQLiteBackend(path)
            back.put(_item("src", "S", supersedes=["GONE", "GONE", "GONE"]))
            conn = sqlite3.connect(path)
            conn.execute("DROP TABLE memory_edges")
            conn.execute("PRAGMA user_version=2")
            conn.commit()
            conn.close()
            self.assertEqual(1, SQLiteBackend(path).edge_backfill_skipped)

    def test_an_about_code_anchor_is_NEVER_counted_as_dangling(self):
        """Its dst is a repo path, not a row. MUTATION: add `about_code` to `ITEM_TARGET_KINDS` and
        this goes RED — every correctly-formed code anchor would be reported as a broken reference."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "m.db")
            back = SQLiteBackend(path)
            back.put(_item("src", "S", about_code=["src/a.py", "src/b.py::fn"]))
            conn = sqlite3.connect(path)
            conn.execute("DROP TABLE memory_edges")
            conn.execute("PRAGMA user_version=2")
            conn.commit()
            conn.close()
            reopened = SQLiteBackend(path)
            self.assertEqual(0, reopened.edge_backfill_skipped)
            self.assertEqual(2, len(_rows(path)))

    def test_a_malformed_inline_field_degrades_clean_instead_of_failing(self):
        """`supersedes` as a string, `depends_on` as a dict, `about_code` as null — a hand-edited
        doc or a teammate's import. The DB.S5 `normalize_applicability` posture: coerce at the
        boundary, never raise on a read path."""
        self.assertEqual([], E.edges_from_doc({"id": "x", "supersedes": "not-a-list",
                                               "depends_on": {"a": 1}, "about_code": None}))
        self.assertEqual([], E.edges_from_doc({"supersedes": ["y"]}), "no id → nothing to project")

    def test_the_shared_report_counts_the_DATA_not_the_rowcount(self):
        """Why the count is its own statement rather than the INSERT's rowcount: the rowcount is
        ZERO on the second (correctly idempotent) pass, which would tell a re-running operator the
        dangling refs had gone away. MUTATION: derive the count from the backfill's rowcount and the
        live-DB leg's re-run assertion goes RED."""
        sql = teamdb.dangling_edge_refs_sql()
        self.assertIn("count(*)", sql)
        self.assertIn("NOT EXISTS", sql)
        self.assertIn("DISTINCT", sql)
        self.assertNotIn(teamdb.EDGES_TABLE, sql,
                         "the count is a property of the ITEM data, not of what was inserted")

    def test_team_init_SURFACES_the_count_to_the_operator(self):
        """A number nobody sees is not a report — so the emit must be REACHED, not merely present.

        Walked as an AST: find the `if` whose test reads `prov.skipped_dangling_edges` and assert
        its body actually calls `emit`. The obvious version of this test (`assertIn(
        'skipped_dangling_edges', source)`) was written first and is USELESS — verified, not
        assumed: neutering the guard to `if False:` leaves the attribute name sitting in the source
        and the substring check passes while nothing is ever printed. The behavioural end-to-end
        version, where `team init` really runs against a store with a planted dangling ref, is in
        `tests/integration/test_db_s7a_live_db.py` — this is its cheap structural companion.

        MUTATION (confirmed RED): change the guard to `if False:`, or drop the `emit` from its
        body."""
        import ast
        import inspect
        from mokata import team

        guards = [n for n in ast.walk(ast.parse(inspect.getsource(team)))
                  if isinstance(n, ast.If)
                  and "skipped_dangling_edges" in ast.unparse(n.test)]
        self.assertEqual(1, len(guards), "exactly one guard should read the skip count")
        called = {ast.unparse(c.func) for c in ast.walk(guards[0]) if isinstance(c, ast.Call)}
        self.assertIn("emit", called, "the guard must SAY something, not just test the number")
        printed = " ".join(ast.unparse(n) for n in guards[0].body)
        self.assertIn("SKIPPED", printed)
        self.assertIn("skipped_dangling_edges", printed,
                      "…and the message must carry the actual COUNT, not a vague 'some'")


# ======================================================================================
# THE GUARDRAIL · recursive CTEs over rows, never a second graph database
# ======================================================================================
class TestNoSecondGraphDatabase(unittest.TestCase):
    def test_the_substrate_adds_no_engine_and_no_dependency(self):
        """Doc 04's standing constraint, and doc 84's for this row specifically. MUTATION: import a
        graph library in `memory/edges.py` and this goes RED.

        Over IMPORTS, not over source text: the module's own docstring names `neo4j_backend.py` as
        the deprecated counter-example, and a guard that cannot tell a citation from a dependency
        would forbid explaining the rule it enforces."""
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(E))
        imported = {a.name.split(".")[0] for n in ast.walk(tree)
                    if isinstance(n, ast.Import) for a in n.names}
        imported |= {(n.module or "").split(".")[0] for n in ast.walk(tree)
                     if isinstance(n, ast.ImportFrom)}
        self.assertEqual(set(), imported & {"neo4j", "networkx", "igraph", "kuzu", "duckdb",
                                            "rdflib", "rustworkx", "graphviz"},
                         "the substrate is rows in the store that already exists — recursive CTEs, "
                         "never a second graph database")

    def test_the_edges_live_in_the_store_that_already_exists(self):
        """One table in the SAME database as the items, on both engines — not a sidecar file, not a
        second DSN, not a second connection. It rides the ONE provision pass, which is what "no
        second graph DB" means operationally: there is nothing extra to install, reach or back up."""
        self.assertIn(teamdb.EDGES_TABLE, " || ".join(teamdb.provision_sql()))
        self.assertTrue(teamdb.EDGES_TABLE.startswith("mokata_"),
                        "the shared table is mokata-NAMESPACED like every other shared table")
        # and the LOCAL half lives in the SAME `memory.db` file the items do.
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "m.db")
            SQLiteBackend(path)
            self.assertEqual([path], [p for p in
                                      (os.path.join(d, f) for f in os.listdir(d))
                                      if p.endswith(".db")])
            conn = sqlite3.connect(path)
            try:
                names = {r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
            finally:
                conn.close()
            self.assertLessEqual({"memory", E.LOCAL_EDGES_TABLE}, names)


if __name__ == "__main__":
    unittest.main()
