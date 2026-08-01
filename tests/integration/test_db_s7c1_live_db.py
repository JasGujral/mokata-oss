"""DB.S7c1 — K2's second third, "CAS COVERS EDGES", against a REAL Postgres with TWO REAL WRITERS.

This claim cannot be proven by a shim, and the reason is the same one DB.S6's live leg gives for
the revision race: `_FakeMemPg` IS the compare-and-set — it decides in Python whether a revision
matched, and it does not run `_project_edges_for` inside the same transaction that the CAS either
committed or rolled back. What ships depends on two things only a real engine shows:

  * the LOSER of a CAS projects NO edge. `team_journal.py:854` projects edges only after
    `rowcount > 0`, so a losing writer's relations must never reach the shared table. If they did,
    the edge table would assert relations for a version of the row that does not exist — and every
    subsequent K1 walk and K2 subgraph would be reading a graph no item backs.

  * the WINNER's edges land WITH it, in the same transaction. A crash between the item's UPDATE
    and its edge projection would leave the two disagreeing, and since the projection is derived
    (DB.S7a decision #2) nothing would ever notice.

And one thing K2 adds on top: the conflict SURFACE (`detect_issues`) must show the LOSER the
relations that a resolution would re-project — read from the shared table, on a real engine.

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

_PROJECT = "db-s7c1-live"


def _writer(d, dsn):
    """One INDEPENDENT writer — its own repo, journal and ledger, pinned to the shared project,
    and with `memory_store` WIRED TO POSTGRES.

    That last part is load-bearing and the first cut of this file got it wrong, so it is spelled
    out. DB.S6's live fixture leaves `memory_store` on the default SQLite floor, which is fine for
    what DB.S6 proves: its conflicts come from the JOURNAL, and the shared row is read by the flush.
    K2's subgraph is different — it reads the store's own backend, so on the default wiring it
    would read the LOCAL floor and describe relations that have nothing to do with the shared row
    the human is about to overwrite. It came back empty and the test failed, which is the correct
    outcome: a team that has actually opted into a shared Postgres memory store (the only
    deployment where a shared subgraph means anything) wires it here, exactly as
    `test_stage35a_shared_memory.py:145-155` does."""
    from mokata import MANIFEST_FILENAME, MOKATA_DIR
    from mokata.config import Surface
    from mokata.init import init_repo
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    path = os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("settings", {})["mode"] = "team"
    data["settings"].setdefault("project", {})["id"] = _PROJECT
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


def _journal_update(surface, item, *, ledger_id, base_revision):
    from mokata import team_journal, teamdb
    payload = {"id": item.id, "mtype": item.mtype, "subject": item.subject,
               "status": item.status, "doc": json.dumps(item.to_doc()), "project": _PROJECT}
    team_journal.record_team_write(
        surface, op=team_journal.OP_UPDATE, table=teamdb.MEMORY_TABLE, key=item.id,
        payload=payload, ledger_id=ledger_id, project=_PROJECT, actor="tester",
        base_revision=base_revision)


def _shared_edges(dsn, src):
    """The OPEN edges the SHARED table holds for one src — read with raw SQL, deliberately, so the
    assertion does not go through the same code it is checking."""
    from mokata import teamdb
    from mokata.memory import _pg
    from mokata.memory import edges as _edges
    conn = _pg.get_connection(dsn, RuntimeError)
    rows = conn.execute(
        f"SELECT {_edges.DST_COLUMN}, {_edges.KIND_COLUMN} FROM {teamdb.EDGES_TABLE} "
        f"WHERE {_edges.SRC_COLUMN}=%s AND {_edges.VALID_TO_COLUMN} IS NULL "
        f"ORDER BY {_edges.KIND_COLUMN}, {_edges.DST_COLUMN}", (src,)).fetchall()
    return [(r[0], r[1]) for r in rows]


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

    def _seed(self, surface, rid, value, depends_on=()):
        _journal_update(surface, self._item(rid, value, depends_on=depends_on),
                        ledger_id=1, base_revision=None)
        self.assertEqual(1, _flush(surface).flushed)


class TestCasCoversEdges(_LivePgCase):
    """The losing writer must leave NO trace in the edge table."""

    def test_the_winner_s_edges_land_with_the_winner(self):
        with tempfile.TemporaryDirectory() as da:
            a = _writer(da, self.dsn)
            self._seed(a, "shared", "A1", depends_on=["dep-1", "dep-2"])
            self.assertEqual([("dep-1", "depends_on"), ("dep-2", "depends_on")],
                             _shared_edges(self.dsn, "shared"))

    def test_the_loser_of_a_cas_projects_no_edge(self):
        """THE claim. Writer B loses the compare-and-set; its relations must not be in the table.
        An edge from a row version that never landed is a relation nothing backs."""
        with tempfile.TemporaryDirectory() as da, tempfile.TemporaryDirectory() as db:
            a, b = _writer(da, self.dsn), _writer(db, self.dsn)
            self._seed(a, "shared", "A1", depends_on=["dep-a"])
            # Both now journal an UPDATE from base revision 1; A lands, B loses.
            _journal_update(a, self._item("shared", "A2", depends_on=["dep-a", "dep-a2"]),
                            ledger_id=2, base_revision=1)
            _journal_update(b, self._item("shared", "B2", depends_on=["dep-b-LOSER"]),
                            ledger_id=3, base_revision=1)
            self.assertEqual(1, _flush(a).flushed)
            self.assertEqual(0, _flush(b).flushed, "B must lose the CAS")
            dsts = [d for d, _k in _shared_edges(self.dsn, "shared")]
            self.assertIn("dep-a2", dsts, "the winner's new relation must have landed")
            self.assertNotIn("dep-b-LOSER", dsts,
                             "the CAS LOSER projected an edge — the edge table now asserts a "
                             "relation for a row version that does not exist")

    def test_the_withdrawn_relation_is_CLOSED_not_deleted(self):
        """R3 — invalidation, never deletion. The winner dropped `dep-2`; its window closes and the
        historical row stays on disk."""
        from mokata import teamdb
        from mokata.memory import _pg
        from mokata.memory import edges as _edges
        with tempfile.TemporaryDirectory() as da:
            a = _writer(da, self.dsn)
            self._seed(a, "shared", "A1", depends_on=["dep-1", "dep-2"])
            _journal_update(a, self._item("shared", "A2", depends_on=["dep-1"]),
                            ledger_id=2, base_revision=1)
            self.assertEqual(1, _flush(a).flushed)
            self.assertEqual([("dep-1", "depends_on")], _shared_edges(self.dsn, "shared"))
            conn = _pg.get_connection(self.dsn, RuntimeError)
            closed = conn.execute(
                f"SELECT COUNT(*) FROM {teamdb.EDGES_TABLE} "
                f"WHERE {_edges.SRC_COLUMN}=%s AND {_edges.DST_COLUMN}=%s "
                f"AND {_edges.VALID_TO_COLUMN} IS NOT NULL", ("shared", "dep-2")).fetchone()[0]
            self.assertEqual(1, closed, "the withdrawn relation was DELETED, not closed")


class TestTheLoserSeesTheSubgraph(_LivePgCase):
    """K2's first third, end to end on a real engine: the writer who lost is shown the relations a
    resolution would re-project, read from the SHARED table."""

    def _conflict_over_real_deps(self, a, b, deps=("dep-a", "dep-a2")):
        """Seed the dependency TARGETS as real shared items, then produce the conflict.

        The targets have to be real, and the first cut of this test proved why by failing: with
        `depends_on` pointing at ids no item backs, every relation was pruned by the visibility
        filter and the subgraph came back EMPTY — while the unit-level assertion (`assertIsNotNone`)
        still passed, because an empty subgraph is not None. That is the SHIM-FALSE-GREEN family
        (doc 84) in miniature: a green that reads as coverage it does not have. The live engine
        caught it; the assertions below now check CONTENTS, never mere presence."""
        for i, dep in enumerate(deps):
            self._seed(a, dep, f"dependency {i}")
        self._seed(a, "shared", "A1", depends_on=[deps[0]])
        _journal_update(a, self._item("shared", "A2", depends_on=list(deps)),
                        ledger_id=2, base_revision=1)
        _journal_update(b, self._item("shared", "B2"), ledger_id=3, base_revision=1)
        self.assertEqual(1, _flush(a).flushed)
        self.assertEqual(0, _flush(b).flushed, "B must lose the CAS")

    def test_the_cross_writer_proposal_carries_the_shared_tables_relations(self):
        with tempfile.TemporaryDirectory() as da, tempfile.TemporaryDirectory() as db:
            a, b = _writer(da, self.dsn), _writer(db, self.dsn)
            self._conflict_over_real_deps(a, b)

            from mokata.memory.healing import CROSS_WRITER
            props = [p for p in _store(b).detect_issues() if p.kind == CROSS_WRITER]
            self.assertEqual(1, len(props))
            sub = props[0].subgraph
            self.assertIsNotNone(sub, "the loser was shown no subgraph on a live engine")
            self.assertEqual({"dep-a", "dep-a2"}, {e.dst_id for e in sub.edges})
            self.assertEqual(0, sub.truncated)

    def test_the_rendered_conflict_names_them(self):
        with tempfile.TemporaryDirectory() as da, tempfile.TemporaryDirectory() as db:
            a, b = _writer(da, self.dsn), _writer(db, self.dsn)
            self._conflict_over_real_deps(a, b)

            from mokata.memory.healing import CROSS_WRITER, render_proposal
            p = next(x for x in _store(b).detect_issues() if x.kind == CROSS_WRITER)
            text = render_proposal(p)
            self.assertIn("depends_on → dep-a", text)
            self.assertIn("depends_on → dep-a2", text)
            self.assertIn("2 open relations re-projected", text)

    def test_a_relation_to_an_unreadable_target_is_not_disclosed(self):
        """Scope, on the engine. A dst the reader's visible set does not contain is dropped — and
        a dangling ref (a `depends_on` pointing at an id no item backs) lands in the same bucket,
        because the two are INDISTINGUISHABLE from the reader's side: "pruned from my scope" and
        "never existed" look identical. Fail-closed is the only safe reading, since the other
        direction discloses the id of an item the reader may not see."""
        with tempfile.TemporaryDirectory() as da, tempfile.TemporaryDirectory() as db:
            a, b = _writer(da, self.dsn), _writer(db, self.dsn)
            self._seed(a, "dep-real", "a real dependency")
            self._seed(a, "shared", "A1", depends_on=["dep-real"])
            _journal_update(a, self._item("shared", "A2",
                                          depends_on=["dep-real", "dep-GHOST"]),
                            ledger_id=2, base_revision=1)
            _journal_update(b, self._item("shared", "B2"), ledger_id=3, base_revision=1)
            self.assertEqual(1, _flush(a).flushed)
            self.assertEqual(0, _flush(b).flushed)

            # The shared TABLE holds both — nothing was hidden at the write.
            self.assertEqual({"dep-real", "dep-GHOST"},
                             {d for d, _k in _shared_edges(self.dsn, "shared")})
            from mokata.memory.healing import CROSS_WRITER, render_proposal
            p = next(x for x in _store(b).detect_issues() if x.kind == CROSS_WRITER)
            self.assertEqual({"dep-real"}, {e.dst_id for e in p.subgraph.edges})
            self.assertNotIn("dep-GHOST", render_proposal(p))

    def test_detection_on_a_live_store_still_writes_nothing_to_the_shared_tables(self):
        """P1 on the engine that matters: the subgraph read must not create an edge or bump a
        revision. A read that mutates the shared store would be a cross-writer bug of its own."""
        from mokata import teamdb
        from mokata.memory import _pg
        with tempfile.TemporaryDirectory() as da, tempfile.TemporaryDirectory() as db:
            a, b = _writer(da, self.dsn), _writer(db, self.dsn)
            self._seed(a, "shared", "A1", depends_on=["dep-a"])
            _journal_update(a, self._item("shared", "A2", depends_on=["dep-a"]),
                            ledger_id=2, base_revision=1)
            _journal_update(b, self._item("shared", "B2"), ledger_id=3, base_revision=1)
            _flush(a)
            _flush(b)
            conn = _pg.get_connection(self.dsn, RuntimeError)

            def _snapshot():
                edges = conn.execute(f"SELECT COUNT(*) FROM {teamdb.EDGES_TABLE}").fetchone()[0]
                rev = conn.execute(
                    f"SELECT {teamdb.MEMORY_REVISION_COLUMN} FROM {teamdb.MEMORY_TABLE} "
                    f"WHERE id=%s", ("shared",)).fetchone()[0]
                return edges, rev

            store = _store(b)
            store.detect_issues()                      # warm
            before = _snapshot()
            for _ in range(3):
                store.detect_issues()
            self.assertEqual(before, _snapshot(),
                             "detection mutated the shared store on a live engine")


if __name__ == "__main__":                              # pragma: no cover
    unittest.main()
