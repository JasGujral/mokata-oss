"""DB.S6 — cross-writer healing against a REAL Postgres, with TWO REAL WRITERS.

The unit suite proves this stage's semantics against `_FakeMemPg` and a transaction-capable
double. Neither can prove the two things DB.S6 actually claims, and both of them are the kind that
fail silently rather than loudly:

  * **the revision race (I3).** `_FakeMemPg` IS the CAS — it decides in Python whether a revision
    matched. A shim cannot show that TWO independent mokata installs, journalling and flushing
    against one shared table, are serialized by Postgres's own `UPDATE … WHERE revision = %s`. That
    is the no-clobber claim, and only a real engine with two real writers can make it.

  * **the group transaction (I1).** The unit proof uses a fake whose `transaction()` snapshots a
    dict. What ships depends on psycopg3 emitting a genuine BEGIN/ROLLBACK around a group on an
    AUTOCOMMIT connection, and on that rollback undoing a statement that had already succeeded.
    If it did not, the shared store would be left with one fact retired and its replacement absent
    — a subject with no active value, and nothing anywhere saying so.

Gate is the same explicit contract as the other live-DB legs: MOKATA_LIVE_DB=1 + MOKATA_PG_DSN +
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

_PROJECT = "db-s6-live"


def _writer(d, dsn):
    """One INDEPENDENT writer: its own repo, its own `.mokata/`, its own journal and ledger —
    pinned to the same shared project so both see the same rows. This is what "two writers" has
    to mean for the claim to be worth anything; two stores over one journal would prove nothing
    about concurrency."""
    from mokata import MANIFEST_FILENAME, MOKATA_DIR
    from mokata.config import Surface
    from mokata.init import init_repo
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    path = os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("settings", {})["mode"] = "team"
    data["settings"].setdefault("project", {})["id"] = _PROJECT
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.environ["MOKATA_PG_DSN"] = dsn
    return Surface.load(d)


def _healthy():
    from mokata import team_health
    return team_health.HealthVerdict(team_health.HEALTHY, "reachable")


def _flush(surface):
    """A REAL flush: no injected connection — it resolves the DSN and opens psycopg itself."""
    from mokata import team_journal
    return team_journal.flush(surface, health=_healthy())


def _store(surface):
    from mokata.memory import MemoryStore
    return MemoryStore.from_surface(surface)


def _journal_update(surface, item, *, ledger_id, base_revision):
    """Journal a durable UPDATE exactly as the gated store path does — `to_doc`, the plain
    columns, an int approval id — without going through the gate, so the test controls the CAS
    base precisely."""
    from mokata import team_journal, teamdb
    payload = {"id": item.id, "mtype": item.mtype, "subject": item.subject,
               "status": item.status, "doc": json.dumps(item.to_doc()), "project": _PROJECT}
    team_journal.record_team_write(
        surface, op=team_journal.OP_UPDATE, table=teamdb.MEMORY_TABLE, key=item.id,
        payload=payload, ledger_id=ledger_id, project=_PROJECT, actor="tester",
        base_revision=base_revision)


def _row(dsn, rid):
    from mokata import teamdb
    from mokata.memory import _pg
    conn = _pg.get_connection(dsn, RuntimeError)
    got = conn.execute(
        f"SELECT doc, {teamdb.MEMORY_REVISION_COLUMN} FROM {teamdb.MEMORY_TABLE} WHERE id=%s",
        (rid,)).fetchone()
    if got is None:
        return None
    doc = got[0] if isinstance(got[0], (dict, list)) else json.loads(got[0])
    return {"value": doc.get("value"), "revision": got[1]}


def _status(dsn, rid):
    """The shared row's STATUS — the column that decides whether the fact is still in memory at
    all. `_row` deliberately reports value+revision only (the CAS story); retirement is a different
    question and gets its own reader rather than widening a dict five assertions compare exactly."""
    from mokata import teamdb
    from mokata.memory import _pg
    conn = _pg.get_connection(dsn, RuntimeError)
    got = conn.execute(f"SELECT status FROM {teamdb.MEMORY_TABLE} WHERE id=%s", (rid,)).fetchone()
    return None if got is None else got[0]


class _LivePgCase(unittest.TestCase):
    def setUp(self):
        from mokata import teamdb
        from mokata.memory import _pg
        self.dsn = _pg_dsn()
        self._saved = os.environ.get("MOKATA_PG_DSN")
        teamdb.provision(self.dsn)                        # idempotent DDL (the only DDL path)
        _pg.get_connection(self.dsn, RuntimeError).execute(f"DELETE FROM {teamdb.MEMORY_TABLE}")

    def tearDown(self):
        from mokata.memory import _pg
        _pg.reset_manager()
        if self._saved is None:
            os.environ.pop("MOKATA_PG_DSN", None)
        else:
            os.environ["MOKATA_PG_DSN"] = self._saved

    def _item(self, rid, value, status=None):
        from mokata.memory.item import ACTIVE, PERSISTENT, MemoryItem
        return MemoryItem(subject=rid, value=value, id=rid, mtype=PERSISTENT,
                          status=status or ACTIVE,
                          provenance={"source": "test", "author": "t",
                                      "created_at": "2026-07-01T00:00:00+00:00"})

    def _seed(self, surface, rid, value):
        """Put a row in the shared table at revision 1, through the real journal+flush path."""
        _journal_update(surface, self._item(rid, value), ledger_id=1, base_revision=None)
        self.assertEqual(1, _flush(surface).flushed)


@unittest.skipUnless(_PG_LIVE, _PG_REASON)
class TestI3TwoRealWritersNoClobber(_LivePgCase):
    """I3 — the no-clobber CAS regression, decided by Postgres rather than by a Python fake."""

    def test_a_stale_writer_never_overwrites_the_winner(self):
        with tempfile.TemporaryDirectory() as da, tempfile.TemporaryDirectory() as db:
            a, b = _writer(da, self.dsn), _writer(db, self.dsn)
            self._seed(a, "shared", "A1")

            # B updates the row it read at revision 1 → lands, revision 2.
            _journal_update(b, self._item("shared", "B2"), ledger_id=10, base_revision=1)
            self.assertEqual(1, _flush(b).flushed)
            self.assertEqual({"value": "B2", "revision": 2}, _row(self.dsn, "shared"))

            # A still believes it is at revision 1 and writes. Postgres's own CAS must refuse it.
            _journal_update(a, self._item("shared", "A3"), ledger_id=20, base_revision=1)
            res = _flush(a)
            self.assertEqual((0, 1), (res.flushed, res.conflicts))
            self.assertEqual({"value": "B2", "revision": 2}, _row(self.dsn, "shared"),
                             "the stale writer overwrote the winner — the CAS did not hold")

    def test_the_loser_sees_a_cross_writer_proposal_carrying_the_winners_value(self):
        from mokata.memory.healing import CROSS_WRITER
        with tempfile.TemporaryDirectory() as da, tempfile.TemporaryDirectory() as db:
            a, b = _writer(da, self.dsn), _writer(db, self.dsn)
            self._seed(a, "shared", "A1")
            _journal_update(b, self._item("shared", "B2"), ledger_id=10, base_revision=1)
            _flush(b)
            _journal_update(a, self._item("shared", "A3"), ledger_id=20, base_revision=1)
            _flush(a)

            props = [p for p in _store(a).detect_issues() if p.kind == CROSS_WRITER]
            self.assertEqual(1, len(props), "A must SEE the conflict without running sync")
            self.assertEqual("A3", props[0].old.value, "…as its own approved write")
            self.assertEqual("B2", props[0].new.value, "…against what the shared row really holds")
            self.assertEqual(2, props[0].remote_revision)

    def test_discarding_leaves_the_winners_row_untouched(self):
        from mokata.memory.healing import CROSS_WRITER
        with tempfile.TemporaryDirectory() as da, tempfile.TemporaryDirectory() as db:
            a, b = _writer(da, self.dsn), _writer(db, self.dsn)
            self._seed(a, "shared", "A1")
            _journal_update(b, self._item("shared", "B2"), ledger_id=10, base_revision=1)
            _flush(b)
            _journal_update(a, self._item("shared", "A3"), ledger_id=20, base_revision=1)
            _flush(a)

            store = _store(a)
            p = next(p for p in store.detect_issues() if p.kind == CROSS_WRITER)
            self.assertTrue(store.apply_proposal(p, "discard", assume_yes=True).changed)
            _flush(a)
            self.assertEqual({"value": "B2", "revision": 2}, _row(self.dsn, "shared"))
            self.assertEqual([], _store(a).cross_writer_proposals())

    def test_keeping_yours_rebases_and_lands_over_the_winner(self):
        """TM.S5c's CAS on the NEW branch: the re-queued write must carry the revision the human
        was SHOWN (2), so it lands at 3 — not a blind write at the base they never saw."""
        from mokata.memory.healing import CROSS_WRITER
        with tempfile.TemporaryDirectory() as da, tempfile.TemporaryDirectory() as db:
            a, b = _writer(da, self.dsn), _writer(db, self.dsn)
            self._seed(a, "shared", "A1")
            _journal_update(b, self._item("shared", "B2"), ledger_id=10, base_revision=1)
            _flush(b)
            _journal_update(a, self._item("shared", "A3"), ledger_id=20, base_revision=1)
            _flush(a)

            store = _store(a)
            p = next(p for p in store.detect_issues() if p.kind == CROSS_WRITER)
            self.assertTrue(store.apply_proposal(p, "approve", assume_yes=True).changed)
            _flush(a)
            self.assertEqual({"value": "A3", "revision": 3}, _row(self.dsn, "shared"))

    def test_a_second_race_during_the_resolution_still_does_not_clobber(self):
        """The nastiest ordering: B moves the row AGAIN between A being shown the conflict and A's
        rebased write reaching Postgres. A resolution is not a licence to overwrite a revision the
        human never saw — the rebased write must lose too, and come back as a fresh conflict.

        Resolving normally flushes IMMEDIATELY (`_best_effort_flush`), which would make A the
        first writer and B the loser — a correct outcome, but not the one under test. So A is
        taken OFFLINE across the resolution (the DSN is unset, which is exactly the work-locally
        path: the rebased write stays journaled), B lands, and only then does A reconnect."""
        from mokata.memory.healing import CROSS_WRITER
        with tempfile.TemporaryDirectory() as da, tempfile.TemporaryDirectory() as db:
            a, b = _writer(da, self.dsn), _writer(db, self.dsn)
            self._seed(a, "shared", "A1")
            _journal_update(b, self._item("shared", "B2"), ledger_id=10, base_revision=1)
            _flush(b)
            _journal_update(a, self._item("shared", "A3"), ledger_id=20, base_revision=1)
            _flush(a)

            store = _store(a)
            p = next(p for p in store.detect_issues() if p.kind == CROSS_WRITER)
            os.environ.pop("MOKATA_PG_DSN", None)                 # A goes offline
            try:
                self.assertTrue(store.apply_proposal(p, "approve", assume_yes=True).changed)
            finally:
                os.environ["MOKATA_PG_DSN"] = self.dsn
            _journal_update(b, self._item("shared", "B4"), ledger_id=11, base_revision=2)
            self.assertEqual(1, _flush(b).flushed)                # B moves it to revision 3
            self.assertEqual({"value": "B4", "revision": 3}, _row(self.dsn, "shared"))

            res = _flush(a)                                       # A reconnects and tries
            self.assertEqual((0, 1), (res.flushed, res.conflicts))
            self.assertEqual({"value": "B4", "revision": 3}, _row(self.dsn, "shared"),
                             "a rebased write must still lose to a newer concurrent change")
            self.assertEqual(1, len(_store(a).cross_writer_proposals()),
                             "and it comes back as a fresh conflict, not a silent loss")

    def test_resolving_while_connected_lands_immediately_and_the_other_writer_loses(self):
        """The same race with the ordering the product actually produces: resolving flushes at
        once, so A lands at revision 3 and B — writing against the revision 2 it read — is the one
        that conflicts. Pinned alongside the offline case so the pair covers BOTH directions: the
        loser is whoever reaches Postgres second, never whoever the code happens to favour."""
        from mokata.memory.healing import CROSS_WRITER
        with tempfile.TemporaryDirectory() as da, tempfile.TemporaryDirectory() as db:
            a, b = _writer(da, self.dsn), _writer(db, self.dsn)
            self._seed(a, "shared", "A1")
            _journal_update(b, self._item("shared", "B2"), ledger_id=10, base_revision=1)
            _flush(b)
            _journal_update(a, self._item("shared", "A3"), ledger_id=20, base_revision=1)
            _flush(a)

            store = _store(a)
            p = next(p for p in store.detect_issues() if p.kind == CROSS_WRITER)
            store.apply_proposal(p, "approve", assume_yes=True)   # rebases AND flushes
            self.assertEqual({"value": "A3", "revision": 3}, _row(self.dsn, "shared"))

            _journal_update(b, self._item("shared", "B4"), ledger_id=11, base_revision=2)
            res = _flush(b)
            self.assertEqual((0, 1), (res.flushed, res.conflicts))
            self.assertEqual({"value": "A3", "revision": 3}, _row(self.dsn, "shared"),
                             "B's stale write overwrote A's resolution")
            self.assertEqual(1, len(_store(b).cross_writer_proposals()),
                             "B is told, rather than silently losing its approved write")


@unittest.skipUnless(_PG_LIVE, _PG_REASON)
class TestI1GroupAtomicOnRealPostgres(_LivePgCase):
    """I1 — the prevention itself, on the engine that has to provide it."""

    def _one_approval_two_writes(self, a, b):
        """The exact shape `apply_proposal` on a contradiction produces: retire `old`, install
        `new`, both under ONE approval id. `new` is rigged to lose its CAS."""
        self._seed(a, "old-fact", "the old value")
        self._seed(a, "new-fact", "placeholder")
        _journal_update(b, self._item("new-fact", "B-took-it"), ledger_id=10, base_revision=1)
        self.assertEqual(1, _flush(b).flushed)
        _journal_update(a, self._item("old-fact", "RETIRED"), ledger_id=99, base_revision=1)
        _journal_update(a, self._item("new-fact", "the new value"), ledger_id=99, base_revision=1)

    def test_a_conflict_in_one_write_rolls_the_sibling_back_on_a_real_engine(self):
        with tempfile.TemporaryDirectory() as da, tempfile.TemporaryDirectory() as db:
            a, b = _writer(da, self.dsn), _writer(db, self.dsn)
            self._one_approval_two_writes(a, b)
            res = _flush(a)
            self.assertEqual((0, 2), (res.flushed, res.conflicts),
                             "the whole approval must conflict, not half of it")
            self.assertEqual({"value": "the old value", "revision": 1},
                             _row(self.dsn, "old-fact"),
                             "the sibling write was COMMITTED — the group did not roll back, so "
                             "the old fact is retired and its replacement never arrived")
            self.assertEqual({"value": "B-took-it", "revision": 2}, _row(self.dsn, "new-fact"))

    def test_the_subject_is_never_left_without_an_active_fact(self):
        """The consequence, stated as the thing a user would actually experience."""
        with tempfile.TemporaryDirectory() as da, tempfile.TemporaryDirectory() as db:
            a, b = _writer(da, self.dsn), _writer(db, self.dsn)
            self._one_approval_two_writes(a, b)
            _flush(a)
            for rid in ("old-fact", "new-fact"):
                self.assertIsNotNone(_row(self.dsn, rid))
                self.assertNotEqual("RETIRED", _row(self.dsn, rid)["value"],
                                    f"{rid} was retired by a heal whose other half never landed")

    def test_both_conflicts_name_the_approval_they_belong_to(self):
        with tempfile.TemporaryDirectory() as da, tempfile.TemporaryDirectory() as db:
            a, b = _writer(da, self.dsn), _writer(db, self.dsn)
            self._one_approval_two_writes(a, b)
            _flush(a)
            props = _store(a).cross_writer_proposals()
            self.assertEqual(2, len(props))
            for p in props:
                self.assertIn("1 of 2 approved together", p.rationale)

    def test_a_clean_approval_commits_every_member_on_a_real_engine(self):
        with tempfile.TemporaryDirectory() as da:
            a = _writer(da, self.dsn)
            self._seed(a, "old-fact", "the old value")
            self._seed(a, "new-fact", "placeholder")
            _journal_update(a, self._item("old-fact", "RETIRED"), ledger_id=99, base_revision=1)
            _journal_update(a, self._item("new-fact", "the new value"), ledger_id=99,
                            base_revision=1)
            self.assertEqual(2, _flush(a).flushed)
            self.assertEqual("RETIRED", _row(self.dsn, "old-fact")["value"])
            self.assertEqual("the new value", _row(self.dsn, "new-fact")["value"])

    def test_the_real_connection_offers_a_transaction_on_autocommit(self):
        """The premise the prevention rests on, asserted rather than assumed: mokata's connection
        posture is `autocommit=True`, and psycopg3 must still give a real BEGIN/ROLLBACK block on
        it. If a driver upgrade ever removed that, everything above would silently fall back to
        the detect-and-surface path — so the premise gets its own pin."""
        from mokata import team_journal
        from mokata.memory import _pg
        conn = _pg.get_connection(self.dsn, RuntimeError)
        self.assertTrue(conn.autocommit, "mokata connects with autocommit — that is the premise")
        self.assertIsNotNone(team_journal._group_transaction(conn),
                             "psycopg no longer offers a transaction block on an autocommit "
                             "connection — group-atomic apply has silently stopped preventing")


@unittest.skipUnless(_PG_LIVE, _PG_REASON)
class TestI1bResolvingARolledBackGroupOnRealPostgres(_LivePgCase):
    """I1b — the half I1's transaction does NOT cover, proven on the engine.

    The group rolls back atomically, then surfaces as TWO conflicts and the human settles them one
    prompt at a time. On a real engine the losing order is: discard the replacement (keep the
    teammate's row), approve your own retirement — and Postgres would faithfully commit a superseded
    old fact with nothing standing in for it. The fake cannot make that claim; only a real writer
    landing a real row can."""

    def _rolled_back_group(self, a, b):
        """One approval, two writes — retire `old-fact`, install `new-fact` — with `new-fact`
        rigged to lose its CAS to writer B, so the whole approval rolls back and BOTH members
        surface as conflicts on A."""
        from mokata.memory.item import SUPERSEDED
        self._seed(a, "old-fact", "the old value")
        self._seed(a, "new-fact", "placeholder")
        _journal_update(b, self._item("new-fact", "B-took-it"), ledger_id=10, base_revision=1)
        self.assertEqual(1, _flush(b).flushed)
        _journal_update(a, self._item("old-fact", "the old value", status=SUPERSEDED),
                        ledger_id=99, base_revision=1)
        _journal_update(a, self._item("new-fact", "the new value"), ledger_id=99, base_revision=1)
        res = _flush(a)
        self.assertEqual((0, 2), (res.flushed, res.conflicts), "I1's rollback is the premise here")

    @staticmethod
    def _member(surface, rid):
        return next(p for p in _store(surface).cross_writer_proposals() if p.old.id == rid)

    def test_approving_the_retirement_while_discarding_the_replacement_is_refused(self):
        """MUTATION: drop the guard and this goes RED — Postgres commits the retirement, the row
        reads `superseded`, and the fact is gone from the shared store with nothing replacing it."""
        from mokata.memory.item import ACTIVE
        with tempfile.TemporaryDirectory() as da, tempfile.TemporaryDirectory() as db:
            a, b = _writer(da, self.dsn), _writer(db, self.dsn)
            self._rolled_back_group(a, b)

            # keep THEIRS for the replacement — your new fact is dropped.
            self.assertTrue(_store(a).apply_proposal(self._member(a, "new-fact"), "discard",
                                                     assume_yes=True).changed)
            res = _store(a).apply_proposal(self._member(a, "old-fact"), "approve", assume_yes=True)
            self.assertFalse(res.changed, "the retirement was published on its own")
            self.assertTrue(res.refused)
            self.assertIn("resolve them together", res.message)

            _flush(a)
            self.assertEqual(ACTIVE, _status(self.dsn, "old-fact"),
                             "the shared row was retired while its replacement was discarded — "
                             "the subject has no active fact and nothing says so")
            self.assertEqual({"value": "the old value", "revision": 1},
                             _row(self.dsn, "old-fact"))
            self.assertEqual(1, len(_store(a).cross_writer_proposals()),
                             "the retirement is still open — nothing was silently settled")

    def test_deciding_the_replacement_first_still_lets_the_whole_heal_land(self):
        """The guard refuses an ORDER, not the heal. Keep yours on both and the approval lands
        exactly as it would have before I1b existed — otherwise this is a deadlock, not a guard."""
        from mokata.memory.item import SUPERSEDED
        with tempfile.TemporaryDirectory() as da, tempfile.TemporaryDirectory() as db:
            a, b = _writer(da, self.dsn), _writer(db, self.dsn)
            self._rolled_back_group(a, b)

            self.assertTrue(_store(a).apply_proposal(self._member(a, "new-fact"), "approve",
                                                     assume_yes=True).changed)
            self.assertTrue(_store(a).apply_proposal(self._member(a, "old-fact"), "approve",
                                                     assume_yes=True).changed)
            _flush(a)
            self.assertEqual(SUPERSEDED, _status(self.dsn, "old-fact"))
            self.assertEqual("the new value", _row(self.dsn, "new-fact")["value"])
            self.assertEqual([], _store(a).cross_writer_proposals())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
