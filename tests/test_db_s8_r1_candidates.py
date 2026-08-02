"""R-1 (DB.S8) — candidate selection: the contracts, and the ONE declared behaviour change.

WHAT R-1 IS. `tiered_recall` used to begin `items = store.scoped_active()` — every active,
visible row, materialized and `from_dict`-decoded, on EVERY recall. Measured on the DB.S8 fixture
at N=100,000: 51,606 rows and 4,533ms per recall. H-1a runs `jit_recall` through that same read on
every turn, so this was not only a scale-test artefact, it was a live per-turn UX defect.

Option A: each tier nominates its own ranked top-k(+over-fetch) in SQL, the union (~<=150 ids,
`CANDIDATE_UNION_CAP`) is hydrated bounded, and the scope predicate travels WITH the candidate
query. Same fixture, after: 26 rows median and 20.1ms — 225x on latency, ~2,000x on rows.

  R1-1  rows materialized are BOUNDED — they do not grow with the store
  R1-2  the scope predicate TRAVELS with the candidate query (and the top-N is a top-N of
        rows this identity may read, not of the whole store)
  R1-3  an item reachable ONLY by a hop is still returned — the walk ADMITS, it no longer merely
        re-ranks what was already materialized
  R1-4  precedence groups are hydrated whole — a loser is never returned as a winner
  R1-5  DB.S5's byte-identical-ranking guarantee, RE-PINNED in the new shape
  R1-6  a Jaccard-floor backend keeps the full scan, and is not broken by any of this
  R1-7  a failed nomination degrades to the full-set read, LOUDLY
  R1-8  a store whose semantic tier cannot be nominated does NOT use candidate selection
  R1-9  the declared union cap is asserted, not trusted

## THE DECLARED BEHAVIOUR CHANGE (read this before changing anything here)

`recall_relevant` now returns only items the query MATCHED on some tier. It used to rank and
return every active item, most of them scoring 0.0 on every match tier.

That is a deliberate narrowing, and it FIXES a live defect this module pins as `QualityTermSumTest`
below. `tiered.py`'s own DB.S5 comment states the intent — the recency and usage terms are "a
tiebreak between things that already match, never a reason to surface something that does not" —
and reasons about each weight being below `LEXICAL_WEIGHT`. But they ADD: `RECENCY_WEIGHT +
USAGE_WEIGHT` was 0.15 + 0.10 = 0.25, exactly `LEXICAL_WEIGHT`. So a heavily-recalled item matching
the query at ZERO tied a perfect lexical match and beat every imperfect one. Measured on the
Jaccard floor, not theorised: a frequently-recalled irrelevant item ranked FIRST, above the item
that answered the query. Under candidate selection it is never nominated, so it cannot.

**DB.S8f closed the rest of it.** R-1's fix reached every store that can nominate and explicitly
NOT the Jaccard floor, which still full-scans; that boundary was recorded here as a passing test
whose message named the real fix ("the weights ... a ranking change with its own gate"). The gate
ran: the two weights are now bounded as a SUM (`tiered.QUALITY_BUDGET`), so the floor is fixed by
arithmetic and `QualityTermSumTest` is the regression guard for a CLOSED defect rather than the
record of a live one. The derivation lives in `test_db_s8f_ranking_bounds.py` (K3).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import tempfile
import unittest

import _support  # noqa: F401

import _scale_fixture as F

from mokata.memory import scope as S
from mokata.memory import tiered
from mokata.memory.backends import SQLiteBackend
from mokata.memory.item import ACTIVE, MemoryItem
from mokata.memory.store import MemoryStore


def _loaded(tmp, n_items, **kw):
    corpus = F.generate(F.ScaleSpec(n_items=n_items, probes=kw.pop("probes", 20), **kw))
    backend = SQLiteBackend(os.path.join(tmp, f"m{n_items}.db"))
    F.load_sqlite(backend, corpus)
    return corpus, backend


class _CountingBackend(SQLiteBackend):
    """Counts the rows every read materializes. Not a double — a real backend that tallies."""

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


class BoundedReadTest(unittest.TestCase):
    def test_r1_1_rows_materialized_do_not_grow_with_the_store(self):
        """R1-1 — THE claim. Not "fewer rows" (a constant-factor win would satisfy that while
        leaving the read O(store)); the claim is that the read is bounded by the OVER-FETCH, so a
        store four times the size costs the same recall."""
        counts = {}
        with tempfile.TemporaryDirectory() as tmp:
            for n in (2_000, 8_000):
                corpus, backend = _CountingBackend, None
                corpus = F.generate(F.ScaleSpec(n_items=n, probes=20))
                backend = _CountingBackend(os.path.join(tmp, f"c{n}.db"))
                F.load_sqlite(backend, corpus)
                probe = corpus.probes[0]
                store = MemoryStore(backend, scope_context=corpus.context_for(probe))
                backend.materialized = 0
                store.recall_relevant(probe.query, top_k=10)
                counts[n] = backend.materialized
        self.assertLessEqual(counts[8_000], tiered.CANDIDATE_UNION_CAP * 2,
                             f"a recall on an 8k store materialized {counts[8_000]} rows")
        # 4x the store must not be ~4x the rows. Generous factor: the point is the SHAPE.
        self.assertLess(counts[8_000], counts[2_000] * 2,
                        f"rows grew with the store ({counts[2_000]} -> {counts[8_000]}) — the "
                        "read is still proportional to N")

    def test_r1_9_the_declared_union_cap_is_asserted_not_trusted(self):
        """R1-9 — the assertion inside `_nominate` must FIRE, or it is decoration. A third
        nominating tier could otherwise widen the bounded read back into a scan silently."""
        with tempfile.TemporaryDirectory() as tmp:
            corpus, backend = _loaded(tmp, 2_000)
            store = MemoryStore(backend, scope_context=corpus.context_for(corpus.probes[0]))
            original = tiered.CANDIDATE_UNION_CAP
            tiered.CANDIDATE_UNION_CAP = 2       # pretend the union outgrew its declared bound
            try:
                with self.assertRaises(AssertionError):
                    store.recall_relevant(corpus.probes[0].query, top_k=10)
            finally:
                tiered.CANDIDATE_UNION_CAP = original


class ScopeTravelsWithTheQueryTest(unittest.TestCase):
    """R1-2 — the change with teeth, and the reason S-2/S-6 were landed as a gate first."""

    def test_r1_2_the_ranked_query_carries_the_scope_predicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            corpus, backend = _loaded(tmp, 2_000)
            context = corpus.seat_context(0, project=0)
            path = S.scope_path(context)
            wide = backend.lexical_search("auth session token", top_k=50, statuses=(ACTIVE,))
            narrow = backend.lexical_search("auth session token", top_k=50,
                                            scope_path=path, statuses=(ACTIVE,))
            self.assertTrue(wide and narrow, "the fixture produced no lexical hits to compare")
            # Every scoped hit is on the path — checked against `on_path`, the SPEC, rather than
            # against the predicate's own idea of itself.
            for item, _score in narrow:
                self.assertTrue(S.on_path(item, path),
                                f"{item.id} ({item.scope_level}/{item.scope_id}) is off the path "
                                "but came back from a SCOPED candidate query")
            # …and the predicate is doing work: the unscoped query returns rows the scoped one must
            # not. Without this the test above passes on a store where everything is visible.
            self.assertTrue({i.id for i, _ in wide} - {i.id for i, _ in narrow},
                            "the scoped and unscoped candidate queries returned the same rows — "
                            "the fixture gave the predicate nothing to exclude")

    def test_r1_2b_the_top_n_is_taken_over_rows_this_identity_may_read(self):
        """The failure this prevents, stated as its own test. Before R-1 the LIMIT was taken over
        the WHOLE store and the caller intersected afterwards — so a reader whose visible rows all
        rank below the cut got NOTHING, while their own matching rows sat unread underneath."""
        with tempfile.TemporaryDirectory() as tmp:
            backend = SQLiteBackend(os.path.join(tmp, "m.db"))
            # 60 rows another seat owns, all matching; then ONE this seat owns, also matching.
            for i in range(60):
                backend.put(MemoryItem(id=f"theirs-{i:03d}", subject=f"rotation {i}",
                                       value="the rotation policy detail",
                                       scope_level=S.PERSONAL, scope_id="seat-other"))
            backend.put(MemoryItem(id="mine", subject="rotation mine",
                                   value="the rotation policy detail",
                                   scope_level=S.PERSONAL, scope_id="seat-me"))
            store = MemoryStore(backend, scope_context=S.ScopeContext(user="seat-me",
                                                                      include_global=False))
            hits = store.recall_relevant("rotation policy", top_k=10)
            self.assertEqual(["mine"], [h.item.id for h in hits],
                             "the reader's own matching row did not survive the candidate LIMIT")


class ExpansionStillAdmitsTest(unittest.TestCase):
    """R1-3 — under the full-set read the walk only ever RE-RANKED rows already materialized.
    Bounded, it must ADMIT: a hop-reached item is by definition one no direct tier nominated."""

    def test_r1_3_an_item_reachable_only_by_a_hop_is_still_returned(self):
        with tempfile.TemporaryDirectory() as tmp:
            corpus, backend = _loaded(tmp, 2_000)
            found = 0
            for probe in corpus.probes[:10]:
                store = MemoryStore(backend, scope_context=corpus.context_for(probe))
                hits = {h.item.id: h for h in store.recall_relevant(probe.query, top_k=25)}
                self.assertIn(probe.direct_id, hits, f"{probe.query!r}: the DIRECT answer is gone")
                self.assertIn(probe.hop_id, hits,
                              f"{probe.query!r}: the hop-only answer was walked to and then "
                              "dropped — expansion re-ranked instead of admitting")
                self.assertGreater(hits[probe.hop_id].edge, 0.0)
                self.assertEqual(0.0, hits[probe.hop_id].lexical,
                                 "the hop item scored lexically — the ground truth is not disjoint")
                found += 1
            self.assertEqual(10, found)

    def test_r1_3b_a_hop_cannot_bridge_through_an_item_the_reader_may_not_see(self):
        """The bridge prune, on the bounded path. `_bounded_walk` establishes visibility from the
        DATABASE (a scope-predicated hydrate) rather than from the candidate list — a post-filter
        would satisfy "no cross-scope leak" while leaking the bridge's id inside the path."""
        with tempfile.TemporaryDirectory() as tmp:
            backend = SQLiteBackend(os.path.join(tmp, "m.db"))
            # a (mine, matches) -> b (ANOTHER SEAT'S, must not bridge) -> c (mine)
            backend.put(MemoryItem(id="a", subject="rotation anchor", value="rotation policy",
                                   scope_level=S.PERSONAL, scope_id="me", depends_on=["b"]))
            backend.put(MemoryItem(id="b", subject="bridge", value="quokka vermilion",
                                   scope_level=S.PERSONAL, scope_id="other", depends_on=["c"]))
            backend.put(MemoryItem(id="c", subject="target", value="zephyr trellis",
                                   scope_level=S.PERSONAL, scope_id="me"))
            store = MemoryStore(backend, scope_context=S.ScopeContext(user="me",
                                                                      include_global=False))
            hits = {h.item.id: h for h in store.recall_relevant("rotation policy", top_k=10)}
            self.assertIn("a", hits)
            self.assertNotIn("b", hits, "another seat's item came back through a hop")
            self.assertNotIn("c", hits, "a hop BRIDGED through an item the reader may not see")


class PrecedenceGroupTest(unittest.TestCase):
    """R1-4 — the subtlest correctness requirement in Option A, and the one with no obvious symptom.

    `precedence.resolve_items` collapses a scope union to ONE winner per `item.subject`. Hydrating
    only the nominated ids hands it a partial group, so a narrow item that LOSES to a broader
    PINNED one is returned as a winner — a visible ranking change, produced by an optimization,
    with nothing failing.
    """

    def _store(self, tmp):
        backend = SQLiteBackend(os.path.join(tmp, "m.db"))
        # Same subject, two scopes. The GLOBAL one is PINNED — an un-overridable floor (doc 62 §3)
        # — so it wins regardless of the narrower one. Only the narrow one matches the query.
        #
        # The winner's VALUE shares no token with the query, and that is the whole test. The first
        # version of this fixture gave it "keep records for 7 years", which shares `keep`/`records`
        # with the query — so the lexical tier nominated it too, the group arrived complete BY
        # ACCIDENT, and the test passed with the group read deleted. Verified: the mutation that
        # drops `subjects=` from `_nominate` stayed GREEN against that fixture and goes RED
        # against this one. A pinned winner that the query does not mention is exactly the case
        # only the group read can reach.
        backend.put(MemoryItem(id="broad", subject="retention",
                               value="zephyr trellis vermilion halcyon",
                               scope_level=S.GLOBAL, scope_id="", pin=True))
        backend.put(MemoryItem(id="narrow", subject="retention",
                               value="keep audit records for 30 days",
                               scope_level=S.PROJECT, scope_id="P"))
        return MemoryStore(backend, scope_context=S.ScopeContext(project="P", user="U")), backend

    def test_r1_4_the_pinned_winner_is_not_displaced_by_a_partial_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = self._store(tmp)
            hits = store.recall_relevant("audit records 30 days", top_k=10)
            ids = [h.item.id for h in hits]
            self.assertNotIn("narrow", ids,
                             "a precedence LOSER was returned as a winner — its pinned sibling "
                             "was never hydrated, so `resolve_items` saw a partial group")

    def test_r1_4b_the_group_read_agrees_with_the_full_set_read(self):
        """The DIFFERENTIAL, which is what makes R1-4 a claim about equivalence rather than about
        one hand-built case: the bounded path and the full-set path must resolve identically."""
        with tempfile.TemporaryDirectory() as tmp:
            store, backend = self._store(tmp)
            bounded = [h.item.id for h in store.recall_relevant("audit records 30 days", top_k=10)]
            full = [i.id for i in store.scoped_active()
                    if i.id in {"broad", "narrow"}]
            self.assertNotIn("narrow", full, "the FULL-set read also drops the loser (control)")
            self.assertNotIn("narrow", bounded)


class ByteIdenticalRankingTest(unittest.TestCase):
    """R1-5 — DB.S5's guarantee, RE-PINNED in the new shape.

    The guarantee was never about WHICH rows are candidates; it is arithmetic — an item with no
    usage signal scores exactly the pre-DB.S5 three-term sum, so a store with no v4 columns, a v3
    team schema, or a freshly-migrated store ranks byte-identically. Candidate selection changes
    the candidate set and must leave that arithmetic untouched.
    """

    def test_r1_5_an_item_with_no_telemetry_fuses_to_the_pre_db_s5_sum(self):
        with tempfile.TemporaryDirectory() as tmp:
            corpus, backend = _loaded(tmp, 2_000)
            probe = corpus.probes[0]
            store = MemoryStore(backend, scope_context=corpus.context_for(probe))
            checked = 0
            for hit in store.recall_relevant(probe.query, top_k=25):
                if hit.recency or hit.usage:
                    continue                       # this item HAS telemetry — not the claim
                legacy = (tiered.SEMANTIC_WEIGHT * hit.semantic
                          + tiered.GRAPH_WEIGHT * hit.graph
                          + tiered.LEXICAL_WEIGHT * hit.lexical
                          + tiered.EDGE_WEIGHT * hit.edge)
                self.assertEqual(repr(legacy), repr(hit.score),
                                 f"{hit.item.id}: the fused score is not the pre-DB.S5 sum")
                checked += 1
            self.assertGreater(checked, 5, "too few telemetry-free items to claim anything")

    def test_r1_5b_the_deterministic_tiebreak_is_unchanged(self):
        """fused DESC, then created_at ASC, then id ASC — still, and on the bounded path."""
        with tempfile.TemporaryDirectory() as tmp:
            corpus, backend = _loaded(tmp, 2_000)
            probe = corpus.probes[0]
            store = MemoryStore(backend, scope_context=corpus.context_for(probe))
            hits = store.recall_relevant(probe.query, top_k=25)
            keys = [(-h.score, h.item.created_at, h.item.id) for h in hits]
            self.assertEqual(keys, sorted(keys))


class JaccardFloorKeepsTheFullScanTest(unittest.TestCase):
    """R1-6 — "Jaccard-floor backends keep a full scan, but must stop being the shape every backend
    pays for." Both halves: they still work, and they are the ones paying."""

    def test_r1_6_a_backend_without_fts_still_recalls(self):
        with tempfile.TemporaryDirectory() as tmp:
            corpus, backend = _loaded(tmp, 1_000)
            backend._fts = False                   # the Jaccard floor, as an FTS5-less build sees it
            probe = corpus.probes[0]
            store = MemoryStore(backend, scope_context=corpus.context_for(probe))
            self.assertFalse(tiered._can_nominate(store, None, True),
                             "a Jaccard-floor store must not try to nominate — `lexical_search` "
                             "returns [] there, so the candidate set would be empty and recall "
                             "would silently return nothing")
            hits = store.recall_relevant(probe.query, top_k=10)
            self.assertIn(probe.direct_id, [h.item.id for h in hits])

    def test_r1_6b_a_backend_with_fts_does_nominate(self):
        """The control. Without it R1-6 passes on a build where NOBODY nominates."""
        with tempfile.TemporaryDirectory() as tmp:
            corpus, backend = _loaded(tmp, 1_000)
            store = MemoryStore(backend, scope_context=corpus.context_for(corpus.probes[0]))
            self.assertTrue(tiered._can_nominate(store, None, True))


class DegradeTest(unittest.TestCase):
    def test_r1_7_a_failed_nomination_falls_back_to_the_full_scan_loudly(self):
        """R1-7 — and the notice is the point. A failed nomination and a query that matched nothing
        both produce a short result; without the notice they are indistinguishable."""
        with tempfile.TemporaryDirectory() as tmp:
            corpus, backend = _loaded(tmp, 1_000)
            probe = corpus.probes[0]

            def boom(*a, **kw):
                raise RuntimeError("the index is on fire")

            backend.lexical_search = boom
            store = MemoryStore(backend, scope_context=corpus.context_for(probe))
            said = []
            hits = tiered.tiered_recall(store, probe.query, top_k=10,
                                        expander=store._edge_expander(), degrade_out=said.append)
            self.assertTrue(said, "candidate selection failed SILENTLY")
            self.assertIn("candidate selection is OFF", " ".join(said))
            # …and it genuinely fell back rather than returning nothing. The Jaccard floor still
            # ranks the direct answer, because the full-set read is a correct answer.
            self.assertIn(probe.direct_id, [h.item.id for h in hits])

    def test_r1_8_a_store_whose_semantic_tier_cannot_be_nominated_keeps_the_full_scan(self):
        """R1-8 — the quality guard. An embedder with no vector index ranks by embedding each
        CANDIDATE, so it can only score rows something else already selected. Nominating lexically
        and running it over the survivors would redefine semantic recall as "re-rank the lexical
        hits", and an item that is semantically near but lexically ZERO — exactly what the tier
        exists to find — would stop being findable. That store pays for the full scan honestly."""
        with tempfile.TemporaryDirectory() as tmp:
            corpus, backend = _loaded(tmp, 500)
            store = MemoryStore(backend, scope_context=corpus.context_for(corpus.probes[0]))
            self.assertFalse(hasattr(backend, "semantic_search"))
            embedder = (lambda text: [0.0] * 8)
            self.assertFalse(tiered._can_nominate(store, embedder, True))
            # …but with the semantic tier OFF, the same store nominates again.
            self.assertTrue(tiered._can_nominate(store, embedder, False))
            self.assertTrue(tiered._can_nominate(store, None, True))


class QualityTermSumTest(unittest.TestCase):
    """QUALITY-TERM-SUM — CLOSED at DB.S8f, on the floor as well as under candidate selection.

    `tiered.py` said the DB.S5 quality terms are "a tiebreak between things that already match,
    never a reason to surface something that does not", and argued it by each weight sitting below
    `LEXICAL_WEIGHT`. They ADDED: 0.15 + 0.10 = 0.25 = `LEXICAL_WEIGHT`. So a heavily-recalled item
    matching the query at ZERO tied a perfect lexical match and beat every imperfect one.

    R-1 fixed it WHERE CANDIDATE SELECTION REACHES — a non-matching item is never nominated, so it
    cannot be ranked — and this module recorded the honest boundary: the Jaccard floor still
    full-scans, so it still ranked `popular` first. That test's own message named the real fix ("the
    weights ... a ranking change with its own gate"). DB.S8f is that gate: `RECENCY_WEIGHT +
    USAGE_WEIGHT` is now bounded as a SUM by `QUALITY_BUDGET` (0.04), well below `LEXICAL_WEIGHT`,
    so the defect is closed by ARITHMETIC and the floor is fixed too.

    Kept rather than deleted, and inverted: these are now the regression guard for the closed
    defect. `test_db_s8f_ranking_bounds.K3TheQualityTermSum` derives the bound; this shows what it
    buys on a real store, on the read path that had no other protection.
    """

    def _store(self, tmp, fts):
        backend = SQLiteBackend(os.path.join(tmp, "m.db"))
        backend.put(MemoryItem(id="real-answer", subject="alpha rotation",
                               value="alpha rotation policy detail beta gamma"))
        backend.put(MemoryItem(id="popular", subject="zeta", value="zeta quokka vermilion"))
        backend._fts = fts
        store = MemoryStore(backend)
        for _ in range(80):
            store.record_usage(["popular"])
        return store

    def test_the_arithmetic_that_made_it_possible_is_gone(self):
        """Pinned as arithmetic so the constants cannot drift BACK into it. `>=` rather than `>`:
        a sum that merely EQUALS the lexical weight is the original defect — it ties a perfect
        match and then wins on the `created_at` tiebreak."""
        total = tiered.RECENCY_WEIGHT + tiered.USAGE_WEIGHT
        self.assertLess(total, tiered.LEXICAL_WEIGHT,
                        f"the quality terms sum to {total} against a LEXICAL_WEIGHT of "
                        f"{tiered.LEXICAL_WEIGHT} — QUALITY-TERM-SUM is BACK, and a non-matching "
                        "item can rank at or above a perfect lexical match")

    def test_a_non_matching_item_no_longer_outranks_the_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp, fts=True)
            ids = [h.item.id for h in store.recall_relevant("alpha rotation", top_k=5)]
            self.assertEqual(["real-answer"], ids,
                             "an item the query matches at ZERO was returned")

    def test_the_floor_that_still_full_scans_is_fixed_too(self):
        """THE BOUNDARY R-1 COULD NOT REACH, now closed. A Jaccard-floor backend (Obsidian's files,
        the native client, any adapter with no `lexical_search`) still reads the full active set, so
        `popular` is still RANKED — candidate selection cannot save it. What saves it is the weight:
        `popular` matches at zero and collects at most 0.04, while `real-answer` collects a real
        lexical score. The answer ranks first because of arithmetic, not because of a read path."""
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp, fts=False)
            hits = store.recall_relevant("alpha rotation", top_k=5)
            ids = [h.item.id for h in hits]
            self.assertEqual("real-answer", ids[0],
                             f"a non-matching but heavily-recalled item outranked the answer on "
                             f"the Jaccard floor: {ids}")
            # …and the reason, so a future reader does not have to re-derive it: the popular item
            # IS still ranked (the floor scans), it simply cannot collect enough to win.
            popular = next((h for h in hits if h.item.id == "popular"), None)
            if popular is not None:
                self.assertEqual(0.0, popular.lexical)
                self.assertLessEqual(popular.score, tiered.QUALITY_BUDGET + 1e-9)


if __name__ == "__main__":
    unittest.main()
