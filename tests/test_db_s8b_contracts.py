"""DB.S8b — the doc-52 business contracts, at N=2,000, UNCONDITIONAL.

Doc 52 §4 states six contracts as the release's validation bar. Four of them are statements about
BEHAVIOUR rather than about an engine, so they are provable on the local store and run in every
CI leg — no DSN, no skip, no opt-in:

  S-2  a teammate's private items NEVER appear in another seat's recall or briefing
  S-3  every item answers: who wrote it, who approved it, which ledger entry
  S-4  two seats resolving the same contradiction → one wins, one is TOLD stale; nothing silent
  S-6  a global item is retrievable from EVERY project; a project item never leaks across projects

The other two (S-1 index-bound plans, S-5 latency budgets) are statements about a query PLANNER
and a real engine's timings. They cannot be made here without measuring a shim, and they live in
the live leg — `tests/integration/test_db_s8c_live_db.py`.

WHY THESE FOUR RUN AT S8b, BEFORE THE R-1 FIX. They are independent of it: none of them mentions
how candidates are selected. That independence is exactly what makes them the REGRESSION GATE for
it. R-1 moves `_visible_filter` — the scope predicate stops being applied to a materialized full
set and starts travelling with each tier's candidate query — and the failure mode of that change
is not a crash, it is a scope predicate that silently stops composing. S-2 and S-6 are the two
tests that would notice. They are landed first, green, so that "unchanged after R-1" is a
comparison against a recorded result rather than a claim.

N=2,000 is DECLARED. It is chosen to run in ~seconds in every leg while carrying every scope
level, 3 projects, 4 seats and ~200 typed edges. The 100k arm is S8c's, and it says 100k.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import tempfile
import unittest

import _support  # noqa: F401

import _scale_fixture as F

from mokata.memory import healing, scope as S
from mokata.memory.backends import SQLiteBackend
from mokata.memory.item import ACTIVE
from mokata.memory.store import MemoryStore

#: The DECLARED size of every corpus in this module. Named once, reported in every failure.
N_ITEMS = 2_000
SPEC = F.ScaleSpec(n_items=N_ITEMS, projects=3, seats=4, probes=40)


class _NoPushdownSQLite(SQLiteBackend):
    """The SAME store, advertising that it CANNOT push a scope predicate into SQL.

    Not an exotic configuration — it is what Obsidian's vault, the native client, any third-party
    adapter, and a Postgres store whose DB.S2b backfill has not yet run all look like. On those,
    `scope.union_read` is the ONLY thing doing the scope filtering.

    It exists here because scope isolation is enforced TWICE by design (`store._visible_filter`
    keeps `union_read` even when the backend already pushed the same predicate, so the pushdown is
    an optimization rather than a competing definition of visibility). That defence in depth is
    correct, and it is also why a test run only against the pushdown route cannot tell whether the
    doc-side layer still works: breaking one layer leaks nothing, because the other catches it.
    Running every scope contract down BOTH routes gates each layer on its own.
    """

    supports_scope_pushdown = False


#: The two routes a scope predicate can travel. Every S-2/S-6 contract runs down both.
_ROUTES = ("sql-pushdown", "doc-side")


class _ScaleCase(unittest.TestCase):
    """One generated, bulk-loaded store shared by the whole module.

    Built ONCE (`setUpClass`) because it is read-only for every contract here and regenerating it
    per test would turn a 1.6s module into a 20s one for no added coverage. Any test that needs to
    mutate builds its own.
    """

    corpus: F.Corpus
    backend: SQLiteBackend

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.corpus = F.generate(SPEC)
        path = os.path.join(cls._tmp.name, "scale.db")
        cls.backend = SQLiteBackend(path)
        loaded = F.load_sqlite(cls.backend, cls.corpus)
        # The declared N, asserted at the door. Every number this module reports is derived from a
        # corpus that has been checked to be the size it says it is.
        assert loaded == cls.corpus.declared_n == N_ITEMS, (
            f"loaded {loaded} rows for a corpus declaring {N_ITEMS}")
        # Same file, same rows — only the pushdown capability differs.
        cls._no_pushdown = _NoPushdownSQLite(path)

    @classmethod
    def tearDownClass(cls):
        cls._no_pushdown.close()
        cls.backend.close()
        cls._tmp.cleanup()

    def backend_for(self, route: str) -> SQLiteBackend:
        return self.backend if route == "sql-pushdown" else self._no_pushdown

    def store(self, context, route: str = "sql-pushdown") -> MemoryStore:
        return MemoryStore(self.backend_for(route), scope_context=context)


class S2NoCrossSeatLeaks(_ScaleCase):
    """S-2 — 'a teammate's private/junk items NEVER appear in another seat's recall or briefing'."""

    def _foreign_personal_ids(self, seat: int) -> set:
        """Every PERSONAL item belonging to some OTHER seat. Non-empty by construction (the
        fixture gives every personal item a real seat id — `_scale_fixture._assign_scope`), and
        asserted non-empty by the caller: a leak test over an empty leak set proves nothing."""
        mine = self.corpus.spec.seat_user(seat)
        return {i.id for i in self.corpus.items
                if i.scope_level == S.PERSONAL and i.scope_id != mine}

    def test_s2_no_other_seats_private_item_is_in_the_active_read(self):
        for route in _ROUTES:
            for seat in range(SPEC.seats):
                foreign = self._foreign_personal_ids(seat)
                self.assertTrue(foreign, "the fixture planted no foreign personal items to leak")
                store = self.store(self.corpus.seat_context(seat, project=0), route)
                leaked = {i.id for i in store.scoped_active()} & foreign
                self.assertEqual(leaked, set(),
                                 f"[{route}] seat {seat} can read {len(leaked)} of another "
                                 "seat's private items")

    def test_s2_no_other_seats_private_item_is_in_a_recall(self):
        """The recall path, not just the read — they are different code paths and R-1 changes one
        of them. A ranked top-k that reached outside the scope path would leak QUIETLY: the item
        arrives as a plausible answer with nothing marking it foreign."""
        for route in _ROUTES:
            for seat in range(SPEC.seats):
                foreign = self._foreign_personal_ids(seat)
                store = self.store(self.corpus.seat_context(seat, project=0), route)
                for probe in self.corpus.probes[:10]:
                    hits = store.recall_relevant(probe.query, top_k=25)
                    leaked = {h.item.id for h in hits} & foreign
                    self.assertEqual(leaked, set(),
                                     f"[{route}] seat {seat}, query {probe.query!r}: "
                                     f"leaked {sorted(leaked)}")

    def test_s2_no_other_seats_private_item_is_in_the_briefing(self):
        """'recall OR BRIEFING' — doc 52's wording. The always-on rules surface is a SECOND read
        path with its own budget and truncation, and a scope bug there leaks into every session
        start rather than only into a query someone typed."""
        from mokata.memory.brain import always_on_items, always_on_lines
        for route, seat in [(r, s) for r in _ROUTES for s in range(SPEC.seats)]:
            foreign = self._foreign_personal_ids(seat)
            store = self.store(self.corpus.seat_context(seat, project=0), route)
            # Asserted on the ITEM SET, not on the rendered text: the rendered line need not carry
            # an id, so a string search over it would pass whether or not the item was there —
            # a vacuous test wearing the same name as a real one.
            selected = always_on_items(store)
            self.assertTrue(selected, "the briefing surface selected nothing to reason about")
            leaked = {i.id for i in selected} & foreign
            self.assertEqual(leaked, set(), f"seat {seat}: briefing carries {sorted(leaked)}")
            # And the rendered, budget-capped output as well — the truncation path re-sorts and
            # re-selects, so it is its own opportunity to reach outside the scope.
            lines, _overflow = always_on_lines(store, max_lines=25)
            values = {i.id: i.value for i in self.corpus.items if i.id in foreign}
            text = "\n".join(lines)
            spilled = sorted(k for k, v in values.items() if v and v in text)
            self.assertEqual(spilled, [], f"seat {seat}: briefing renders {spilled}")

    def test_s2_the_isolation_is_real_and_not_an_empty_store(self):
        """THE CONTROL. Every assertion above is satisfied by a store that returns nothing at all.
        This one fails if the fixture, the scope path, or a future R-1 candidate query ever
        collapses the visible set — so 'no leak' cannot start meaning 'no rows'."""
        for route, seat in [(r, s) for r in _ROUTES for s in range(SPEC.seats)]:
            store = self.store(self.corpus.seat_context(seat, project=0), route)
            visible = store.scoped_active()
            mine = self.corpus.spec.seat_user(seat)
            own = [i for i in visible if i.scope_level == S.PERSONAL and i.scope_id == mine]
            self.assertTrue(own, f"[{route}] seat {seat} cannot see its OWN private items")
            self.assertGreater(len(visible), 100,
                               f"[{route}] seat {seat} sees only {len(visible)} of {N_ITEMS}")

    def test_s2_the_pushed_predicate_and_union_read_agree_row_for_row(self):
        """The DIFFERENTIAL — and it is the single most useful assertion here for R-1.

        The SQL predicate and `scope.union_read` are two implementations of ONE rule, and R-1 makes
        the SQL half travel with each tier's candidate query instead of one whole-set read. That is
        exactly the change that can make them disagree.

        It is taken at the BACKEND boundary, deliberately, and the first version of this test was
        wrong in a way worth recording: comparing `scoped_active()` down the two routes compares
        what comes out AFTER `union_read`, which runs on both — so a SQL predicate that had widened
        to ignore every scope id compared perfectly equal, because the doc-side layer quietly
        corrected it on both sides. Defence in depth makes the system safe and the naive
        differential blind at the same time. Comparing the SQL result against `union_read` over the
        UNFILTERED set is the comparison that can actually fail.

        ONE DIRECTION IT CANNOT SEE, named rather than left to be found: this corpus carries no
        LEGACY-personal items (F-9 gives every personal item a real seat id, because otherwise
        there would be no seats to leak between), so dropping the predicate's `OR scope_id=''`
        carve-out changes nothing here and this test stays GREEN under that mutation — verified,
        not assumed. That carve-out is covered by DB.S2b's own SQL≡union_read differential, which
        builds contexts for it.
        """
        from mokata.memory.scope import scope_path, union_read
        for seat in range(SPEC.seats):
            for project in range(SPEC.projects):
                context = self.corpus.seat_context(seat, project=project)
                pushed = [i.id for i in self.backend.all(
                    statuses=(ACTIVE,), scope_path=scope_path(context))]
                oracle = [i.id for i in union_read(
                    self.backend.all(statuses=(ACTIVE,)), context)]
                self.assertEqual(pushed, oracle,
                                 f"seat {seat} / project {project}: the pushed scope predicate "
                                 f"and scope.union_read disagree "
                                 f"({len(pushed)} rows vs {len(oracle)})")


class S3ProvenanceIsAnswerable(_ScaleCase):
    """S-3 — 'every shared item answers: who wrote it, who approved it, which ledger entry'.

    Stated here as a property of the READ PATH, which is the half DB.S8 can break and M-1/R9's own
    suite cannot see: R-1 rewrites how candidates are selected and hydrated, and a hydration that
    rebuilt items from indexed COLUMNS instead of from `doc` would return perfectly good-looking
    answers with the consent chain silently gone. Nothing else would fail.
    """

    def test_s3_every_item_a_recall_returns_answers_all_three_questions(self):
        answered = 0
        for probe in self.corpus.probes[:15]:
            store = self.store(self.corpus.context_for(probe))
            for hit in store.recall_relevant(probe.query, top_k=25):
                item = hit.item
                self.assertTrue(item.provenance.get("author"), f"{item.id}: no author")
                self.assertTrue(item.approved_by, f"{item.id}: no approver")
                self.assertIsNotNone(item.approval_ledger_id, f"{item.id}: no ledger entry")
                answered += 1
        self.assertGreater(answered, 100, "too few items examined to claim anything")

    def test_s3_the_author_and_the_approver_are_distinguishable(self):
        """R9's point: on a poisoned proposal the writer and the approver are two different people.
        A read path that answered 'who approved it' by handing back the author would satisfy the
        test above completely."""
        store = self.store(self.corpus.seat_context(0, project=0))
        differing = [i for i in store.scoped_active()
                     if i.approved_by and i.approved_by != i.provenance.get("author")]
        self.assertTrue(differing,
                        "no item distinguishes its approver from its author — S-3 would pass "
                        "against a store that conflates them")

    def test_s3_the_ledger_id_survives_the_storage_round_trip_as_an_int(self):
        """It is a JOIN KEY back into the hash-chained ledger, so its TYPE is load-bearing: an id
        that round-trips as the string "7" joins nothing."""
        for probe in self.corpus.probes[:5]:
            for item_id in probe.relevant:
                stored = self.backend.get(item_id)
                self.assertIsInstance(stored.approval_ledger_id, int)


class S4ConcurrentContradiction(unittest.TestCase):
    """S-4 — 'two seats resolving the same contradiction concurrently → one wins, one gets
    "stale — re-detect"; zero silent lost updates'.

    **WHAT IS PROVEN HERE, AND WHAT IS DELIBERATELY NOT.** Which of two writers actually wins is a
    property of Postgres's own `UPDATE … WHERE revision = %s`, and the in-Python double every unit
    suite uses IS that CAS — it decides in Python whether a revision matched, so it can only ever
    report what it was written to report. That half is proven on a real engine, with two real
    connections, in the live leg, and this docstring is the pointer rather than a claim.

    What IS provable locally is the half doc 52 actually words as a contract: the LOSER IS TOLD.
    'One gets "stale — re-detect"' and 'zero SILENT lost updates' are statements about whether the
    losing writer is handed a decision or is quietly dropped — and that is `detect_cross_writer`'s
    surface, on this machine, at N=2,000.
    """

    def setUp(self):
        self.corpus = F.generate(SPEC)
        self.by_id = self.corpus.by_id()

    def _conflict(self, item_id: str, remote_value: str, revision: int):
        local = self.by_id[item_id]
        remote = self.by_id[item_id].__class__(**{**local.__dict__, "value": remote_value})
        return healing.ConflictRecord(
            conflict_id=f"c-{item_id}", key=item_id,
            detail=f"remote revision {revision}",
            local=local, remote=remote, remote_revision=revision)

    def test_s4_the_losing_writer_is_handed_a_decision_not_a_silence(self):
        subject_item = self.corpus.probes[0].direct_id
        proposals = healing.detect_cross_writer(
            [self._conflict(subject_item, "the teammate's landed value", revision=9)])
        self.assertEqual(len(proposals), 1, "a surfaced conflict must produce exactly one decision")
        p = proposals[0]
        self.assertEqual(p.kind, healing.CROSS_WRITER)
        self.assertEqual(p.remote_revision, 9,
                         "the proposal must carry the revision to RE-DETECT against")
        self.assertIn("teammate changed this row", p.rationale)

    def test_s4_zero_silent_lost_updates_the_local_write_is_still_in_the_proposal(self):
        """'Zero silent lost updates' is the load-bearing clause. The losing write must still be
        REACHABLE from what the human is shown — a proposal that named only the winner would have
        lost the loser's content while looking like a correct conflict report."""
        target = self.corpus.probes[1].direct_id
        local_value = self.by_id[target].value
        p = healing.detect_cross_writer(
            [self._conflict(target, "a different landed value", revision=4)])[0]
        self.assertIsNotNone(p.old, "the local write is absent from the decision")
        self.assertEqual(p.old.value, local_value)
        self.assertIsNotNone(p.new, "the remote state is absent from the decision")
        self.assertNotEqual(p.old.value, p.new.value, "the conflict must actually differ")

    def test_s4_every_concurrent_conflict_on_a_scale_store_gets_its_own_decision(self):
        """At scale the failure mode is a batch that collapses N conflicts into one prompt and
        resolves the rest by implication. One conflict in, one decision out — N times."""
        targets = [p.direct_id for p in self.corpus.probes[:25]]
        conflicts = [self._conflict(t, f"remote-{i}", revision=i + 1)
                     for i, t in enumerate(targets)]
        proposals = healing.detect_cross_writer(conflicts)
        self.assertEqual(len(proposals), len(targets))
        self.assertEqual(sorted(p.conflict_id for p in proposals),
                         sorted(c.conflict_id for c in conflicts))
        self.assertEqual([p.remote_revision for p in proposals],
                         [c.remote_revision for c in conflicts])


class S6ScopeIsolation(_ScaleCase):
    """S-6 — 'an org-scoped item written once is retrievable from EVERY project on the DB; a
    project-scoped item NEVER leaks across projects'. Two directions, and both must hold: a filter
    that returns nothing satisfies the second one perfectly."""

    def test_s6_a_global_item_is_retrievable_from_every_project(self):
        global_ids = {i.id for i in self.corpus.items
                      if i.scope_level == S.GLOBAL and i.status == ACTIVE}
        self.assertTrue(global_ids)
        for route, project in [(r, p) for r in _ROUTES for p in range(SPEC.projects)]:
            store = self.store(self.corpus.seat_context(0, project=project), route)
            visible = {i.id for i in store.scoped_active()}
            missing = global_ids - visible
            self.assertEqual(missing, set(),
                             f"[{route}] project {project} cannot see {len(missing)} global items")

    def test_s6_a_project_item_never_leaks_across_projects(self):
        for route, project in [(r, p) for r in _ROUTES for p in range(SPEC.projects)]:
            key = self.corpus.spec.project_key(project)
            foreign = {i.id for i in self.corpus.items
                       if i.scope_level == S.PROJECT and i.scope_id != key}
            self.assertTrue(foreign, "the fixture planted no other project's items")
            store = self.store(self.corpus.seat_context(0, project=project), route)
            leaked = {i.id for i in store.scoped_active()} & foreign
            self.assertEqual(leaked, set(),
                             f"[{route}] project {key} can read {len(leaked)} items from another")

    def test_s6_the_isolation_holds_through_a_ranked_recall(self):
        """The same claim on the recall path. R-1 gives each tier its own candidate query, so this
        is where a scope predicate that stopped composing would first show."""
        for route, project in [(r, p) for r in _ROUTES for p in range(SPEC.projects)]:
            key = self.corpus.spec.project_key(project)
            foreign = {i.id for i in self.corpus.items
                       if i.scope_level == S.PROJECT and i.scope_id != key}
            store = self.store(self.corpus.seat_context(0, project=project), route)
            for probe in self.corpus.probes[:10]:
                hits = store.recall_relevant(probe.query, top_k=25)
                leaked = {h.item.id for h in hits} & foreign
                self.assertEqual(leaked, set(),
                                 f"[{route}] {key} / {probe.query!r}: leaked {sorted(leaked)}")

    def test_s6_a_probes_own_project_items_are_reachable_from_that_project(self):
        """THE CONTROL for the direction above."""
        for route in _ROUTES:
            for probe in self.corpus.probes[:10]:
                store = self.store(self.corpus.context_for(probe), route)
                visible = {i.id for i in store.scoped_active()}
                self.assertIn(probe.direct_id, visible, f"[{route}] {probe.query!r}")
                self.assertIn(probe.hop_id, visible, f"[{route}] {probe.query!r}")


if __name__ == "__main__":
    unittest.main()
