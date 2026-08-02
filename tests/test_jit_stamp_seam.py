"""JIT-RECALL-STAMP-SEAM — `recall_relevant(stamp=False, kinds=…)` and the injection route.

The seam doc 84 filed at the H-1a build, built 2026-08-01. Three claims, and they are separable:

  P1  STAMP — `stamp=False` moves NO durable state. Both instrumentation writes on this path are
      suppressed (the DB.S5 `record_usage` stamp AND the read counter), because on a hook that
      fires every prompt either one alone turns a governance metric into a count of turns.
  P2  KINDS — `kinds=` restricts the result to those `effective_kind`s, and it is the SAME rule
      whether the backend can push it into SQL or not.
  P3  DEFAULTS — every pre-existing caller is byte-identical. `stamp` and `kinds` default to
      today's behaviour, so this is an addition, not a change.

THE SCALE PIN IS THE ONE THAT EARNED ITS PLACE. H-1a already pinned "the read counter does not
move across a session of turns", and that test PASSED against a build where the counter moved 21
times in 40 recalls — because its fixture is ~200 items with no edge bridging, so the SECOND
counted hydrate (`_bounded_walk`'s, reached only when an expansion walk touches an id the direct
tiers did not nominate) never fired. `test_the_counter_holds_when_the_expansion_bridges` is the
pin that fails on that build: it plants lineage so the bridge hydrate actually runs.
"""
import os
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401

import _scale_fixture as F

from mokata.memory import JIT_KINDS
from mokata.memory.backends import SQLiteBackend
from mokata.memory.item import ALWAYS_ON_KINDS
from mokata.memory.store import MemoryStore

#: Big enough that the fixture plants lineage (`_plant_lineage` needs >=20 filler items) and that
#: the expansion walk reaches ids outside the nominated union — which is the ONLY way the second
#: hydrate is exercised. Small enough to stay a unit test.
SEAM_N = 2_000

_SINK = staticmethod(lambda _msg: None)


class SeamLeg(unittest.TestCase):
    """One loaded store for the whole file — the fixture is deterministic and every test here
    either reads or is explicit about the counter it expects to move."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.corpus = F.generate(F.ScaleSpec(n_items=SEAM_N, probes=40))
        cls.backend = SQLiteBackend(os.path.join(cls._tmp.name, "seam.db"))
        loaded = F.load_sqlite(cls.backend, cls.corpus)
        assert loaded == SEAM_N == cls.corpus.declared_n, loaded

    @classmethod
    def tearDownClass(cls):
        cls.backend.close()
        cls._tmp.cleanup()

    def store(self):
        return MemoryStore(self.backend)

    def queries(self, n):
        return [p.query for p in self.corpus.probes][:n]


# ============================================================================== P1 · THE STAMP
class TestStampFalseMovesNoDurableState(SeamLeg):
    def test_the_usage_stamp_is_not_written(self):
        store = self.store()
        seen = []
        store.record_usage = lambda ids, now=None: seen.append(list(ids)) or True
        store.recall_relevant(self.queries(1)[0], top_k=5, stamp=False, degrade_out=_SINK)
        self.assertEqual([], seen, "`stamp=False` still called `record_usage` — the per-turn "
                                  "injection is writing DB.S5 telemetry on every prompt")

    def test_the_usage_stamp_IS_written_by_default(self):
        """The positive control. Without it, "never stamps" would pass on a build where the stamp
        was deleted outright rather than made optional."""
        store = self.store()
        seen = []
        store.record_usage = lambda ids, now=None: seen.append(list(ids)) or True
        store.recall_relevant(self.queries(1)[0], top_k=5, degrade_out=_SINK)
        self.assertEqual(1, len(seen))
        self.assertTrue(seen[0], "the default recall stamped an EMPTY id list")

    def test_the_read_counter_does_not_move(self):
        store = self.store()
        before = store.stats.reads
        for q in self.queries(10):
            store.recall_relevant(q, top_k=5, stamp=False, kinds=JIT_KINDS, degrade_out=_SINK)
        self.assertEqual(before, store.stats.reads,
                         "`stats.reads` — the read/write ratio `/mokata:govern` surfaces — moved "
                         "on a non-stamping recall")

    def test_the_read_counter_DOES_move_by_default(self):
        store = self.store()
        before = store.stats.reads
        store.recall_relevant(self.queries(1)[0], top_k=5, degrade_out=_SINK)
        self.assertGreater(store.stats.reads, before)

    def test_the_counter_holds_when_the_expansion_bridges(self):
        """THE regression pin for the leak the H-1a counter test could not see.

        `_bounded_walk` hydrates every id the ≤2-hop walk TOUCHED but the direct tiers did not
        nominate — a second counted read, reached only when the walk actually bridges. On a small
        fixture it never fires and the counter looks clean; at scale it fired on nearly every turn
        (measured: 21 bumps across 40 non-counting recalls at N=100k).

        This asserts the bridge is genuinely exercised BEFORE asserting the counter, so it cannot
        pass vacuously on a build where the expansion silently stopped running.
        """
        store = self.store()
        bridged = []
        real = MemoryStore.hydrate_candidates

        def spy(self_, ids, *, subjects=(), mtype=None, count_read=True):
            ids = list(ids)
            if not subjects:                    # the bridge hydrate passes ids only
                bridged.append((len(ids), count_read))
            return real(self_, ids, subjects=subjects, mtype=mtype, count_read=count_read)

        before = store.stats.reads
        with mock.patch.object(MemoryStore, "hydrate_candidates", spy):
            for q in self.queries(10):
                store.recall_relevant(q, top_k=5, stamp=False, kinds=JIT_KINDS,
                                      degrade_out=_SINK)
        self.assertTrue(bridged, "the expansion never bridged — this pin proved nothing. The "
                                 "fixture must plant lineage the walk can reach.")
        self.assertTrue(all(count_read is False for _n, count_read in bridged),
                        f"the bridge hydrate counted a read on a stamp=False recall: {bridged}")
        self.assertEqual(before, store.stats.reads)


# =============================================================================== P2 · THE KINDS
class TestKindsIsOneRule(SeamLeg):
    def test_only_the_asked_for_kinds_come_back(self):
        store = self.store()
        hits = store.recall_relevant(self.queries(1)[0], top_k=20, stamp=False,
                                     kinds=JIT_KINDS, degrade_out=_SINK)
        self.assertTrue(hits, "the kinds filter returned nothing at all — it is not a filter, it "
                              "is an off switch")
        for h in hits:
            self.assertIn(h.item.effective_kind, JIT_KINDS)

    def test_an_always_on_kind_is_excluded_by_asking_for_jit_kinds(self):
        """The complement, so "returns only JIT kinds" cannot pass by the store happening to hold
        nothing else. Asking for the always-on kinds returns items; asking for JIT kinds does not
        return those same items."""
        store = self.store()
        q = self.queries(1)[0]
        always = store.recall_relevant(q, top_k=20, stamp=False, kinds=ALWAYS_ON_KINDS,
                                       degrade_out=_SINK)
        jit = store.recall_relevant(q, top_k=20, stamp=False, kinds=JIT_KINDS, degrade_out=_SINK)
        # Guard against a VACUOUS pass: an empty `always` would satisfy the disjointness below
        # while proving nothing. The fixture carries 329 rules and 329 guardrails at this N.
        self.assertTrue(always, "no always-on item matched — the complement is untested")
        self.assertTrue(jit)
        for h in always:
            self.assertIn(h.item.effective_kind, ALWAYS_ON_KINDS)
        overlap = {h.item.id for h in always} & {h.item.id for h in jit}
        self.assertEqual(set(), overlap, "an item came back under two disjoint kind filters")

    def test_the_python_rule_holds_when_the_backend_cannot_push_it(self):
        """`kinds` is enforced in `tiered_recall`, in Python, over the resolved set — the SQL
        predicate is an OPTIMIZATION over the same rule. A backend that ignores `kinds` entirely
        must therefore still produce the right answer.

        Mutating the SQL half away is exactly the change that must NOT go unnoticed if someone
        later decides the Python filter is redundant."""
        store = self.store()
        real = type(self.backend).lexical_search

        def blind(self_, query, top_k=10, *, scope_path=None, statuses=None, kinds=None):
            return real(self_, query, top_k, scope_path=scope_path, statuses=statuses, kinds=None)

        with mock.patch.object(type(self.backend), "lexical_search", blind):
            hits = store.recall_relevant(self.queries(1)[0], top_k=20, stamp=False,
                                         kinds=JIT_KINDS, degrade_out=_SINK)
        for h in hits:
            self.assertIn(h.item.effective_kind, JIT_KINDS,
                          "the kind rule lives ONLY in the SQL predicate — a backend that cannot "
                          "push it returns the wrong kinds")

    def test_the_sql_predicate_is_what_stops_the_bounded_top_k_under_filling(self):
        """WHY the SQL predicate exists, pinned as behaviour rather than asserted as intent.

        The Python rule alone gives the right KINDS but not the right COUNT: nomination takes a
        bounded top-k across ALL kinds, so filtering to `JIT_KINDS` afterwards spends slots on
        rules and returns fewer items than the store can answer with. The predicate moves the
        filter in front of the LIMIT, so the k slots are k USABLE ones.

        MEASURED FIRST, and the first version of this pin FAILED — on the balanced DB.S8 corpus
        (roughly a sixth of items per kind) dropping the predicate cost exactly nothing, because
        `CANDIDATE_OVER_FETCH` already pulls enough rows that ~half of them are JIT kinds. So the
        predicate is NOT load-bearing at the shipped `top_k` on a balanced store, and this pin
        says so by using the corpus where it IS: a SKEWED one, which is the realistic shape of a
        store whose owner has captured mostly rules."""
        from mokata.memory.item import CONTEXT, PERSISTENT, RULE, MemoryItem
        with tempfile.TemporaryDirectory() as d:
            backend = SQLiteBackend(os.path.join(d, "skewed.db"))
            # 300 RULE items matching every term, and 10 CONTEXT items matching one. The rules
            # outrank the context items AND outnumber the over-fetch, so an unfiltered top-k is
            # entirely rules.
            for n in range(300):
                backend.put(MemoryItem(
                    id=f"rule-{n:04d}", mtype=PERSISTENT, kind=RULE,
                    subject=f"alpha bravo charlie {n}", value="alpha bravo charlie delta",
                    provenance={"created_at": "2026-01-04T03:03:00+00:00"}))
            for n in range(10):
                backend.put(MemoryItem(
                    id=f"ctx-{n:04d}", mtype=PERSISTENT, kind=CONTEXT,
                    subject=f"alpha topic {n}", value="alpha only",
                    provenance={"created_at": "2026-01-04T03:03:00+00:00"}))
            store = MemoryStore(backend)
            pushed = store.recall_relevant("alpha bravo charlie", top_k=5, stamp=False,
                                           kinds=(CONTEXT,), degrade_out=_SINK)
            real = type(backend).lexical_search

            def blind(self_, query, top_k=10, *, scope_path=None, statuses=None, kinds=None):
                return real(self_, query, top_k, scope_path=scope_path, statuses=statuses,
                            kinds=None)

            with mock.patch.object(type(backend), "lexical_search", blind):
                unpushed = store.recall_relevant("alpha bravo charlie", top_k=5, stamp=False,
                                                 kinds=(CONTEXT,), degrade_out=_SINK)
            backend.close()
        for h in pushed:
            self.assertEqual(CONTEXT, h.item.effective_kind)
        self.assertGreater(len(pushed), len(unpushed),
                           "dropping the SQL kind predicate cost nothing even on a skewed store — "
                           "the predicate is not reaching the ranked query")

    def test_an_empty_kind_string_falls_back_to_mtype_in_SQL_as_it_does_in_python(self):
        """`effective_kind` is `self.kind or self.mtype` (`item.py:485`) — an EMPTY kind falls back,
        not just a MISSING one. `coalesce` alone gets the missing case right and the empty case
        wrong, which is why the expression carries `nullif(…, '')` — the same idiom the scope
        backfill already uses, for the same reason.

        ASSERTED WHERE IT IS REACHABLE, which took grounding to get right. The fallback is only
        observable for kinds that are also `MEMORY_TYPES` — `(persistent, decision, episodic)` —
        because `mtype` is what it falls back TO, and `enabled_types` drops any row whose `mtype`
        is not one of those three. So no `JIT_KINDS` query can ever exercise it (`best-practice`,
        `context` and `reference` are kinds, never mtypes), and an earlier version of this pin that
        tried was not testing the fallback at all — it was constructing an item the store correctly
        refuses. `decision` is the honest case, and it is exactly the legacy shape the
        `effective_kind` docstring names."""
        from mokata.memory.item import DECISION, MemoryItem
        with tempfile.TemporaryDirectory() as d:
            backend = SQLiteBackend(os.path.join(d, "blank.db"))
            item = MemoryItem(id="blank-kind-1", mtype=DECISION, subject="zaphod beeblebrox",
                              value="the improbability drive tolerance is seven",
                              provenance={"created_at": "2026-01-04T03:03:00+00:00"})
            item.kind = ""                       # explicitly EMPTY, not absent
            backend.put(item)
            store = MemoryStore(backend)
            self.assertEqual(DECISION, item.effective_kind, "the Python fallback itself changed")
            hits = store.recall_relevant("improbability drive tolerance", top_k=5, stamp=False,
                                         kinds=(DECISION,), degrade_out=_SINK)
            backend.close()
        self.assertEqual(["blank-kind-1"], [h.item.id for h in hits],
                         "an item with an EMPTY `kind` was not matched under its `mtype` — the SQL "
                         "expression is `coalesce` without `nullif`, so it disagrees with "
                         "`effective_kind` on exactly this row shape")

    def test_the_sql_predicate_agrees_with_the_python_rule(self):
        """Same query, pushed vs not pushed: the SQL predicate must not change the ANSWER, only
        how many rows were read to reach it. This is what stops the two definitions drifting."""
        store = self.store()
        q = self.queries(1)[0]
        pushed = store.recall_relevant(q, top_k=10, stamp=False, kinds=JIT_KINDS,
                                       degrade_out=_SINK)
        real = type(self.backend).lexical_search

        def blind(self_, query, top_k=10, *, scope_path=None, statuses=None, kinds=None):
            return real(self_, query, top_k, scope_path=scope_path, statuses=statuses, kinds=None)

        with mock.patch.object(type(self.backend), "lexical_search", blind):
            unpushed = store.recall_relevant(q, top_k=10, stamp=False, kinds=JIT_KINDS,
                                             degrade_out=_SINK)
        self.assertEqual([h.item.id for h in pushed], [h.item.id for h in unpushed])

    def test_an_empty_kinds_tuple_is_not_the_same_as_none(self):
        """`None` means every kind; `()` means no kind qualifies. Collapsing the two is the classic
        falsy-default bug (`if not kinds` where `if kinds is None` was meant) and it silently
        disables the filter.

        CHECKED DOWN BOTH ROUTES, and the second one is the point. A first version of this pin
        asserted only the default path and SURVIVED the falsy mutation — because the SQL predicate
        emits `1=0` for an empty tuple, so the backend returned nothing and the broken Python rule
        was never consulted. The pin was passing on the strength of the optimization while the rule
        it names was inverted. Blinding the SQL half is what makes it test the rule."""
        store = self.store()
        q = self.queries(1)[0]
        self.assertTrue(store.recall_relevant(q, top_k=5, stamp=False, kinds=None,
                                              degrade_out=_SINK))
        self.assertEqual([], store.recall_relevant(q, top_k=5, stamp=False, kinds=(),
                                                   degrade_out=_SINK))
        real = type(self.backend).lexical_search

        def blind(self_, query, top_k=10, *, scope_path=None, statuses=None, kinds=None):
            return real(self_, query, top_k, scope_path=scope_path, statuses=statuses, kinds=None)

        with mock.patch.object(type(self.backend), "lexical_search", blind):
            self.assertEqual([], store.recall_relevant(q, top_k=5, stamp=False, kinds=(),
                                                       degrade_out=_SINK),
                             "with the SQL predicate blinded, an empty `kinds` returned items — "
                             "the Python rule is treating `()` as `None`")


# ============================================================================ P3 · THE DEFAULTS
class TestTheAdditionIsByteIdenticalForExistingCallers(SeamLeg):
    def test_kinds_none_ranks_exactly_as_before(self):
        """The default path must not consult the new machinery at all. Same query, with and
        without an explicit `kinds=None`, must produce the identical ranked ids AND scores."""
        store = self.store()
        q = self.queries(1)[0]
        a = store.recall_relevant(q, top_k=10, stamp=False, degrade_out=_SINK)
        b = store.recall_relevant(q, top_k=10, stamp=False, kinds=None, degrade_out=_SINK)
        self.assertEqual([(h.item.id, h.score) for h in a], [(h.item.id, h.score) for h in b])

    def test_stamp_does_not_change_the_ranking(self):
        """The stamp is INSTRUMENTATION. Suppressing it must change what is recorded, never what
        is returned — otherwise the injection path is ranking differently from a real recall and
        the quality measurement would not transfer."""
        store = self.store()
        q = self.queries(1)[0]
        unstamped = store.recall_relevant(q, top_k=10, stamp=False, degrade_out=_SINK)
        fresh = MemoryStore(self.backend)
        stamped = fresh.recall_relevant(q, top_k=10, degrade_out=_SINK)
        self.assertEqual([h.item.id for h in unstamped], [h.item.id for h in stamped])


if __name__ == "__main__":
    unittest.main()
