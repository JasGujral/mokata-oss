"""DB.S7d — the group decision and the duplicate-both-active guard against a REAL Postgres.

The unit suite proves the semantics against `_FakeMemPg` and a transaction-capable double. Two of
this slice's claims are not provable there, and both fail SILENTLY rather than loudly:

  * **duplicate-both-active is a claim about the SHARED TABLE.** "Two active facts on one subject"
    is a statement about what other writers will read back out of Postgres — a Python dict standing
    in for the table can show the resolution being refused, but not that the refusal is what keeps
    the shared row singular. Only a real engine, with a second real writer that produced the
    conflict, makes that claim mean anything.

  * **the group decision has to survive the REAL flush.** A whole-approval verdict re-queues N
    writes at their current remote revisions and the next flush CASes all of them into one
    transaction. Whether they actually land together depends on psycopg3's BEGIN/COMMIT around the
    group and on Postgres's own `UPDATE … WHERE revision = %s` — the two things the unit doubles
    stand in for.

Gate is the same explicit contract as the other live-DB legs: MOKATA_LIVE_DB=1 + MOKATA_PG_DSN +
psycopg + a reachable DB, else these skip cleanly.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from test_db_s6_live_db import (_PG_LIVE, _PG_REASON, _LivePgCase, _flush,  # noqa: F401
                                _journal_update, _row, _status, _store, _writer)


@unittest.skipUnless(_PG_LIVE, _PG_REASON)
class TestDuplicateBothActiveOnRealPostgres(_LivePgCase):
    """(a) — the direction DB.S6 left open, proven where "two active facts" is observable.

    A rolled-back approval surfaces as two conflicts. Discard the RETIREMENT (so the teammate's
    row — still the old fact, still ACTIVE — stands) and approve the REPLACEMENT (which lands
    ACTIVE), and the shared table now carries both halves of a heal that was never decided as a
    whole. Postgres will do that faithfully; the guard is the only thing that does not."""

    def _rolled_back_group(self, a, b):
        """One approval, two writes — retire `old-fact`, install `new-fact` — with `new-fact`
        rigged to lose its CAS to writer B, so the whole approval rolls back on A."""
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

    def test_discarding_the_retirement_while_the_replacement_lands_is_refused(self):
        """MUTATION: return None from `duplicate_both_active_refusal` and this goes RED — Postgres
        commits the replacement, the old row stays ACTIVE beside it, and the shared table holds
        both halves of an approval nobody decided as a whole."""
        from mokata.memory.item import ACTIVE
        with tempfile.TemporaryDirectory() as da, tempfile.TemporaryDirectory() as db:
            a, b = _writer(da, self.dsn), _writer(db, self.dsn)
            self._rolled_back_group(a, b)

            # keep YOURS for the replacement — it is now landing.
            self.assertTrue(_store(a).apply_proposal(self._member(a, "new-fact"), "approve",
                                                     assume_yes=True).changed)
            res = _store(a).apply_proposal(self._member(a, "old-fact"), "discard", assume_yes=True)
            self.assertFalse(res.changed, "the retirement was dropped while its replacement landed")
            self.assertTrue(res.refused)
            self.assertIn("two active", res.message.lower())

            _flush(a)
            self.assertEqual(ACTIVE, _status(self.dsn, "old-fact"))
            self.assertEqual(1, len(_store(a).cross_writer_proposals()),
                             "the retirement is still open — nothing was silently settled")

    def test_the_other_order_is_refused_too(self):
        """Whichever member the human decides SECOND is the one that creates the duplicate, so the
        guard has to catch both orders and not just the one it was written for."""
        with tempfile.TemporaryDirectory() as da, tempfile.TemporaryDirectory() as db:
            a, b = _writer(da, self.dsn), _writer(db, self.dsn)
            self._rolled_back_group(a, b)

            self.assertTrue(_store(a).apply_proposal(self._member(a, "old-fact"), "discard",
                                                     assume_yes=True).changed)
            res = _store(a).apply_proposal(self._member(a, "new-fact"), "approve", assume_yes=True)
            self.assertFalse(res.changed)
            self.assertIn("two active", res.message.lower())
            self.assertEqual("B-took-it", _row(self.dsn, "new-fact")["value"],
                             "the replacement reached the shared row anyway")


@unittest.skipUnless(_PG_LIVE, _PG_REASON)
class TestTheGroupDecisionOnRealPostgres(_LivePgCase):
    """(b) — the one-prompt whole-approval verdict, landing through a REAL flush."""

    def _rolled_back_group(self, a, b):
        return TestDuplicateBothActiveOnRealPostgres._rolled_back_group(self, a, b)

    @staticmethod
    def _member(surface, rid):
        return next(p for p in _store(surface).cross_writer_proposals() if p.old.id == rid)

    def test_a_keep_local_group_verdict_lands_the_whole_approval(self):
        """The end state the surface exists to produce: the fact is retired AND replaced, in one
        decision, over two rows a second writer had already moved."""
        from mokata.memory.item import ACTIVE, SUPERSEDED
        with tempfile.TemporaryDirectory() as da, tempfile.TemporaryDirectory() as db:
            a, b = _writer(da, self.dsn), _writer(db, self.dsn)
            self._rolled_back_group(a, b)

            res = _store(a).apply_group_decision(self._member(a, "old-fact"), "approve",
                                                 assume_yes=True)
            self.assertTrue(res.changed)
            _flush(a)
            self.assertEqual(SUPERSEDED, _status(self.dsn, "old-fact"))
            self.assertEqual("the new value", _row(self.dsn, "new-fact")["value"])
            self.assertEqual(ACTIVE, _status(self.dsn, "new-fact"))
            self.assertEqual([], _store(a).cross_writer_proposals())

    def test_a_keep_remote_group_verdict_leaves_the_shared_table_exactly_as_b_left_it(self):
        """The other uniform verdict. Both local writes are dropped, so the old fact stays ACTIVE
        with nothing beside it — no loss, and no duplicate either."""
        from mokata.memory.item import ACTIVE
        with tempfile.TemporaryDirectory() as da, tempfile.TemporaryDirectory() as db:
            a, b = _writer(da, self.dsn), _writer(db, self.dsn)
            self._rolled_back_group(a, b)

            self.assertTrue(_store(a).apply_group_decision(self._member(a, "new-fact"), "discard",
                                                           assume_yes=True).changed)
            self.assertEqual(0, _flush(a).flushed, "a dropped write still reached Postgres")
            self.assertEqual(ACTIVE, _status(self.dsn, "old-fact"))
            self.assertEqual({"value": "the old value", "revision": 1},
                             _row(self.dsn, "old-fact"))
            self.assertEqual("B-took-it", _row(self.dsn, "new-fact")["value"])
            self.assertEqual([], _store(a).cross_writer_proposals())

    def test_a_group_verdict_cannot_land_a_retirement_whose_replacement_was_discarded(self):
        """P5 on the engine. MUTATION: skip `retire_without_replace_refusal` in the group path and
        this goes RED — Postgres commits the retirement, the shared row reads `superseded`, and the
        fact is gone in ONE prompt the human trusted because it claimed to cover everything."""
        from mokata.memory.item import ACTIVE
        with tempfile.TemporaryDirectory() as da, tempfile.TemporaryDirectory() as db:
            a, b = _writer(da, self.dsn), _writer(db, self.dsn)
            self._rolled_back_group(a, b)

            self.assertTrue(_store(a).apply_proposal(self._member(a, "new-fact"), "discard",
                                                     assume_yes=True).changed)
            res = _store(a).apply_group_decision(self._member(a, "old-fact"), "approve",
                                                 assume_yes=True)
            self.assertFalse(res.changed)
            self.assertTrue(res.refused)
            self.assertIn("resolve them together", res.message)

            _flush(a)
            self.assertEqual(ACTIVE, _status(self.dsn, "old-fact"),
                             "a GROUP verdict retired a fact whose replacement was discarded")

    def test_sync_settles_the_whole_approval_in_one_question_on_a_real_engine(self):
        """End-to-end through the surface a human actually uses, against a real DSN and a real
        second writer. One question, both rows landed, no second `mokata sync` pass."""
        from mokata import team_health, team_journal
        from mokata.memory.item import SUPERSEDED
        with tempfile.TemporaryDirectory() as da, tempfile.TemporaryDirectory() as db:
            a, b = _writer(da, self.dsn), _writer(db, self.dsn)
            self._rolled_back_group(a, b)

            asked = []
            res = team_journal.sync(
                a, health=team_health.HealthVerdict(team_health.HEALTHY, "reachable"),
                confirm=lambda t: (asked.append(t), True)[1])
            self.assertEqual(1, len(asked),
                             f"sync asked {len(asked)} questions to settle ONE approval")
            self.assertEqual(2, res.resolved_local)
            self.assertEqual(0, res.deferred)
            self.assertEqual(SUPERSEDED, _status(self.dsn, "old-fact"))
            self.assertEqual("the new value", _row(self.dsn, "new-fact")["value"])
            self.assertEqual([], _store(a).cross_writer_proposals())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
