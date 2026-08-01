"""DB.S7c2 — STALE-REF's `index_epoch` against a REAL Postgres with TWO REAL WRITERS.

What a shim cannot prove, and why each of these had to run on a real engine:

  * THE EPOCH MOVES UNDER A GATED WRITE. The unit leg drives `PostgresBackend.put`, which is NOT
    the path a durable team write takes. A gated write lands through `team_journal`'s CAS
    (`_INSERT_SQL`/`_UPDATE_SQL`), and the epoch's claim — that `sum(revision)` moves on every
    update because the CAS bumps `revision` by exactly 1 — is a claim about THAT SQL, on an engine
    that actually enforces the compare-and-set.

  * THE TWO-WRITER STORY IS THE WHOLE FEATURE. Writer A recalls, mints a citation, and persists it
    into brainstorm run state. Writer B lands an approved write. A's citation is now a handle to a
    version of memory that no longer exists — and it must REFUSE at approve time rather than being
    acted on. One process cannot stage that honestly; the entire point is a store that moved under
    someone else.

  * S2 LIVE — a window-closed edge is HISTORY, not staleness. On a real store a withdrawn relation
    CLOSES (R3, pinned at DB.S7c1). The epoch must not move for it, and here the edge table is real
    rather than a table this test made up.

  * PROJECT SCOPING. The epoch carries the same project predicate every other read here carries, so
    another tenant's writes must not age this tenant's citations. Only a shared store shows this.

Gate is the same explicit contract as every other live-DB leg: MOKATA_LIVE_DB=1 + MOKATA_PG_DSN +
psycopg + a reachable DB, else these skip cleanly.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import importlib.util
import json
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

LIVE = os.environ.get("MOKATA_LIVE_DB") == "1"


def _have(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _pg_dsn():
    return os.environ.get("MOKATA_PG_DSN") or os.environ.get("MOKATA_TEST_PG_DSN")


_PG_LIVE = LIVE and _have("psycopg") and bool(_pg_dsn())
_PG_REASON = "live PG off (need MOKATA_LIVE_DB=1 + MOKATA_PG_DSN + psycopg + reachable DB)"

_PROJECT = "db-s7c2-live"
_OTHER_PROJECT = "db-s7c2-other"


def _writer(d, dsn, project=_PROJECT):
    """One INDEPENDENT writer — own repo, journal and ledger, pinned to `project`, with
    `memory_store` WIRED TO POSTGRES (the same wiring DB.S7c1's live leg spells out: the epoch is
    read off the store's own backend, so on the default SQLite wiring it would read the local floor
    and answer OFF, proving nothing)."""
    from mokata import MANIFEST_FILENAME, MOKATA_DIR
    from mokata.config import Surface
    from mokata.init import init_repo
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    path = os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("settings", {})["mode"] = "team"
    data["settings"].setdefault("project", {})["id"] = project
    data.setdefault("capabilities", {}).setdefault("memory_store", {})["fallback"] = [
        "postgres", "sqlite"]
    data.setdefault("tools", {})["postgres"] = {
        "provides": "memory_store", "kind": "external", "version": None,
        "detect": {"type": "python_module", "name": "psycopg"}, "enabled": True,
        "config": {"dsn_env": "MOKATA_PG_DSN"}}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.environ["MOKATA_PG_DSN"] = dsn
    return Surface.load(d)


def _flush(surface):
    from mokata import team_health, team_journal
    return team_journal.flush(
        surface, health=team_health.HealthVerdict(team_health.HEALTHY, "reachable"))


def _store(surface):
    from mokata.memory import MemoryStore
    return MemoryStore.from_surface(surface)


def _epoch(surface):
    """The epoch as the PRODUCT reads it — through `read_index_epoch` off the store's backend."""
    from mokata.memory.staleness import read_index_epoch
    return read_index_epoch(_store(surface).backend)


def _journal_update(surface, item, *, ledger_id, base_revision, project=_PROJECT):
    from mokata import team_journal, teamdb
    payload = {"id": item.id, "mtype": item.mtype, "subject": item.subject,
               "status": item.status, "doc": json.dumps(item.to_doc()), "project": project}
    team_journal.record_team_write(
        surface, op=team_journal.OP_UPDATE, table=teamdb.MEMORY_TABLE, key=item.id,
        payload=payload, ledger_id=ledger_id, project=project, actor="tester",
        base_revision=base_revision)


@unittest.skipUnless(_PG_LIVE, _PG_REASON)
class _LivePgCase(unittest.TestCase):
    def setUp(self):
        from mokata import teamdb
        from mokata.memory import _pg
        self.dsn = _pg_dsn()
        self._saved = os.environ.get("MOKATA_PG_DSN")
        teamdb.provision(self.dsn)
        conn = _pg.get_connection(self.dsn, RuntimeError)
        conn.execute(f"DELETE FROM {teamdb.EDGES_TABLE}")
        conn.execute(f"DELETE FROM {teamdb.MEMORY_TABLE}")

    def tearDown(self):
        from mokata.memory import _pg
        _pg.reset_manager()
        if self._saved is None:
            os.environ.pop("MOKATA_PG_DSN", None)
        else:
            os.environ["MOKATA_PG_DSN"] = self._saved

    def _item(self, rid, value, *, depends_on=(), status=None):
        from mokata.memory.item import ACTIVE, PERSISTENT, MemoryItem
        return MemoryItem(subject=rid, value=value, id=rid, mtype=PERSISTENT,
                          status=status or ACTIVE, depends_on=list(depends_on),
                          provenance={"source": "test", "author": "t",
                                      "created_at": "2026-07-01T00:00:00+00:00"})

    def _seed(self, surface, rid, value, depends_on=(), project=_PROJECT):
        _journal_update(surface, self._item(rid, value, depends_on=depends_on),
                        ledger_id=1, base_revision=None, project=project)
        self.assertEqual(1, _flush(surface).flushed)


# ================================================================ the epoch under a gated write
class TestEpochMovesOnGatedWrite(_LivePgCase):

    def test_insert_and_update_each_move_the_epoch(self):
        with tempfile.TemporaryDirectory() as da:
            a = _writer(da, self.dsn)
            empty = _epoch(a)
            self.assertTrue(empty, "a Postgres-wired store must report an epoch, not OFF")
            self._seed(a, "retry", "use utils.retry")
            after_insert = _epoch(a)
            self.assertNotEqual(empty, after_insert)
            # the CAS UPDATE path — `revision`+1, which is what `sum(revision)` is counting on.
            _journal_update(a, self._item("retry", "use utils.retry v2"),
                            ledger_id=2, base_revision=1)
            self.assertEqual(1, _flush(a).flushed)
            self.assertNotEqual(after_insert, _epoch(a))

    def test_a_read_never_moves_the_epoch(self):
        """P1 — merely LOOKING must not age anybody's citations (the DB.S7c1 `scoped_active`
        defect, in this stage's own terms)."""
        with tempfile.TemporaryDirectory() as da:
            a = _writer(da, self.dsn)
            self._seed(a, "retry", "use utils.retry")
            before = _epoch(a)
            _store(a).recall_relevant("retry", top_k=5)
            self.assertEqual(before, _epoch(a))

    def test_a_losing_cas_does_not_move_the_epoch(self):
        """A write that did NOT land must not age citations. The epoch tracks the INDEX, not
        attempts on it."""
        with tempfile.TemporaryDirectory() as da, tempfile.TemporaryDirectory() as db:
            a, b = _writer(da, self.dsn), _writer(db, self.dsn)
            self._seed(a, "shared", "A1")
            settled = _epoch(a)
            _journal_update(b, self._item("shared", "B2"), ledger_id=3, base_revision=99)
            self.assertEqual(0, _flush(b).flushed, "B must lose the CAS")
            self.assertEqual(settled, _epoch(a))


# ================================================================ the two-writer story
class TestTwoWriterCitationGoesStale(_LivePgCase):
    """THE claim, end to end: A's persisted citation is refused after B moves the store."""

    def test_b_s_landed_write_makes_a_s_persisted_citation_refuse(self):
        from mokata.brainstorm import Approach, BrainstormGateError, BrainstormSession
        from mokata.brainstorm_impact import DesignFitVerdict
        from mokata.govern.stale_ref_gate import brainstorm_stale_ref_gate
        with tempfile.TemporaryDirectory() as da, tempfile.TemporaryDirectory() as db:
            a, b = _writer(da, self.dsn), _writer(db, self.dsn)
            self._seed(a, "retry", "use utils.retry")

            # --- A brainstorms against the shared store and PERSISTS the citation it was shown.
            s = BrainstormSession("add a retry helper")
            s.propose_approaches([
                Approach("a", "new retry loop", pros=["simple"], cons=["dup"], targets=["retry"]),
                Approach("b", "extend utils.retry", pros=["reuse"], cons=["x"], targets=["retry"]),
            ])
            s.assess_impacts(layer=None)
            s.record_design_fit("a", DesignFitVerdict("a", "fits"))
            s.assess_prior_art(memory_store=_store(a))
            minted = s.prior_art["a"].decisions
            self.assertTrue(minted, "the shared store must have yielded a citation to stamp")
            self.assertTrue(minted[0].index_epoch, "the citation went out UN-STAMPED")

            # the citation crosses the store boundary — run state, exactly as brainstorm persists it
            restored = BrainstormSession.from_dict(json.loads(json.dumps(s.to_dict())))

            # while it sat there, B landed an APPROVED write against the same shared row.
            _journal_update(b, self._item("retry", "use tenacity instead"),
                            ledger_id=2, base_revision=1)
            self.assertEqual(1, _flush(b).flushed)

            # --- A comes back and tries to approve on what it was shown.
            gate = brainstorm_stale_ref_gate(restored, "a", current_epoch=_epoch(a))
            self.assertTrue(gate.refused, "A approved on a citation the store had moved past")
            self.assertIn("retry", gate.stale_ids)
            with self.assertRaises(BrainstormGateError) as ctx:
                restored.approve("jas", "a", stale_ref_gate=gate)
            self.assertIn("REFUSED", str(ctx.exception))
            self.assertFalse(restored.approved)
            # LOUD, never silent-correct: the stamp is NOT quietly refreshed to the current epoch.
            self.assertEqual(minted[0].index_epoch,
                             restored.prior_art["a"].decisions[0].index_epoch)

    def test_an_untouched_store_lets_the_same_approval_through(self):
        """The control. Without it, a gate that refuses unconditionally would pass the test above."""
        from mokata.brainstorm import Approach, BrainstormSession
        from mokata.brainstorm_impact import DesignFitVerdict
        from mokata.govern.stale_ref_gate import brainstorm_stale_ref_gate
        with tempfile.TemporaryDirectory() as da:
            a = _writer(da, self.dsn)
            self._seed(a, "retry", "use utils.retry")
            s = BrainstormSession("add a retry helper")
            s.propose_approaches([
                Approach("a", "new retry loop", pros=["simple"], cons=["dup"], targets=["retry"]),
                Approach("b", "extend utils.retry", pros=["reuse"], cons=["x"], targets=["retry"]),
            ])
            s.assess_impacts(layer=None)
            s.record_design_fit("a", DesignFitVerdict("a", "fits"))
            s.assess_prior_art(memory_store=_store(a))
            restored = BrainstormSession.from_dict(json.loads(json.dumps(s.to_dict())))
            gate = brainstorm_stale_ref_gate(restored, "a", current_epoch=_epoch(a))
            self.assertFalse(gate.refused)
            restored.approve("jas", "a", stale_ref_gate=gate)
            self.assertTrue(restored.approved)


# ================================================================ S2 live · closed edge windows
class TestClosedEdgeWindowNeverAgesACitation(_LivePgCase):

    def test_a_withdrawn_relation_closes_without_moving_the_epoch(self):
        """DB.S7c1 pinned that a withdrawn relation CLOSES rather than deletes. That close is R3
        HISTORY, and history must not age a citation."""
        from mokata import teamdb
        from mokata.memory import _pg
        from mokata.memory import edges as _edges
        with tempfile.TemporaryDirectory() as da:
            a = _writer(da, self.dsn)
            self._seed(a, "shared", "A1", depends_on=["dep-1", "dep-2"])
            before = _epoch(a)

            # close ONE edge window directly, exactly as the projection does on a withdrawal —
            # without touching the item row, so the epoch sees an edge-only change or nothing.
            conn = _pg.get_connection(self.dsn, RuntimeError)
            conn.execute(
                f"UPDATE {teamdb.EDGES_TABLE} SET {_edges.VALID_TO_COLUMN}=%s "
                f"WHERE {_edges.SRC_COLUMN}=%s AND {_edges.DST_COLUMN}=%s",
                ("2026-07-31T00:00:00+00:00", "shared", "dep-2"))
            closed = conn.execute(
                f"SELECT COUNT(*) FROM {teamdb.EDGES_TABLE} "
                f"WHERE {_edges.SRC_COLUMN}=%s AND {_edges.VALID_TO_COLUMN} IS NOT NULL",
                ("shared",)).fetchone()[0]
            self.assertEqual(1, closed, "the fixture did not actually close a window")

            self.assertEqual(before, _epoch(a), "an edge close aged a citation — that is history")

    def test_a_new_edge_row_does_not_move_the_epoch_either(self):
        """The other direction, and the one that catches an epoch which merely COUNTS edge rows:
        asserting a brand-new relation is not index staleness any more than withdrawing one is."""
        from mokata import teamdb
        from mokata.memory import _pg
        from mokata.memory import edges as _edges
        with tempfile.TemporaryDirectory() as da:
            a = _writer(da, self.dsn)
            self._seed(a, "shared", "A1", depends_on=["dep-1"])
            before = _epoch(a)
            conn = _pg.get_connection(self.dsn, RuntimeError)
            conn.execute(
                f"INSERT INTO {teamdb.EDGES_TABLE} ({_edges.SRC_COLUMN}, {_edges.DST_COLUMN}, "
                f"{_edges.KIND_COLUMN}, {_edges.VALID_FROM_COLUMN}) VALUES (%s, %s, %s, %s)",
                ("shared", "dep-brand-new", "depends_on", "2026-07-31T00:00:00+00:00"))
            self.assertEqual(before, _epoch(a), "a new edge aged a citation")


# ================================================================ the epoch's alias-freedom
class TestEpochAliasFreedom(_LivePgCase):
    """`PostgresBackend.index_epoch` claims that an unchanged triple means an unchanged index. That
    claim rests on all THREE numbers, and each of these constructs the exact index change that
    ONLY its number can see. Written after a mutation pass found the claim asserted but unpinned:
    dropping `count(*)` or `max(seq)` left every other test green.

    The states are built with raw SQL on purpose. They are statements about the epoch's ARITHMETIC,
    and the effects the gated write path has on a row (`revision`+1 per CAS update, a pruned row
    removed) are reproduced exactly — with no dependence on being able to drive a real writer into
    a deliberately contrived shape."""

    def _raw(self):
        from mokata.memory import _pg
        return _pg.get_connection(self.dsn, RuntimeError)

    def test_a_prune_plus_offsetting_updates_still_moves_the_epoch(self):
        """COUNT is load-bearing. Prune one row at revision 3, then let three updates elsewhere add
        the same 3 back to `sum(revision)`: without `count(*)` the epoch would read as unchanged
        while a whole fact has left the index."""
        from mokata import teamdb
        with tempfile.TemporaryDirectory() as da:
            a = _writer(da, self.dsn)
            self._seed(a, "victim", "about to be pruned")
            self._seed(a, "survivor", "stays")
            conn = self._raw()
            conn.execute(f"UPDATE {teamdb.MEMORY_TABLE} SET revision=3 WHERE id=%s", ("victim",))
            before = _epoch(a)
            conn.execute(f"DELETE FROM {teamdb.MEMORY_TABLE} WHERE id=%s", ("victim",))   # -1, -3
            conn.execute(f"UPDATE {teamdb.MEMORY_TABLE} SET revision=revision+3 "
                         f"WHERE id=%s", ("survivor",))                                   # +3
            self.assertNotEqual(before, _epoch(a),
                                "a pruned fact left the index unnoticed — count(*) is not doing "
                                "the job the epoch's alias-freedom argument gives it")

    def test_a_row_replaced_by_another_moves_the_epoch(self):
        """MAX(SEQ) is load-bearing. Prune a revision-1 row and insert a different one: `count(*)`
        and `sum(revision)` both return to exactly where they were, and only the sequence — which
        never reissues a value — can tell that the index now holds a different fact."""
        from mokata import teamdb
        with tempfile.TemporaryDirectory() as da:
            a = _writer(da, self.dsn)
            self._seed(a, "old-fact", "the thing you were shown")
            before = _epoch(a)
            conn = self._raw()
            conn.execute(f"DELETE FROM {teamdb.MEMORY_TABLE} WHERE id=%s", ("old-fact",))
            self._seed(a, "new-fact", "something else entirely")
            self.assertNotEqual(before, _epoch(a),
                                "the index swapped one fact for another and the epoch could not "
                                "tell — max(seq) is not doing its job")

    def test_a_true_round_trip_reads_as_unchanged(self):
        """The honest converse, and NOT a miss: insert a row then prune it, and the index really is
        back where it was. A citation is not stale because something appeared and left."""
        from mokata import teamdb
        with tempfile.TemporaryDirectory() as da:
            a = _writer(da, self.dsn)
            self._seed(a, "retry", "use utils.retry")
            before = _epoch(a)
            self._seed(a, "transient", "here and gone")
            self.assertNotEqual(before, _epoch(a))
            self._raw().execute(f"DELETE FROM {teamdb.MEMORY_TABLE} WHERE id=%s", ("transient",))
            self.assertEqual(before, _epoch(a))


# ================================================================ project scoping
class TestAnotherProjectNeverAgesThisOne(_LivePgCase):

    def test_another_tenant_s_write_leaves_this_epoch_alone(self):
        with tempfile.TemporaryDirectory() as da, tempfile.TemporaryDirectory() as db:
            a = _writer(da, self.dsn)
            other = _writer(db, self.dsn, project=_OTHER_PROJECT)
            self._seed(a, "retry", "use utils.retry")
            mine = _epoch(a)
            self._seed(other, "their-row", "their business", project=_OTHER_PROJECT)
            self.assertEqual(mine, _epoch(a),
                             "another project's write aged this project's citations")
            self.assertNotEqual(mine, _epoch(other))


if __name__ == "__main__":
    unittest.main()
