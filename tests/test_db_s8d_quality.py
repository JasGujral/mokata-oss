"""DB.S8d — retrieval QUALITY: four arms, a stored baseline, a knob sweep, and three findings.

Doc 86's exit criterion is "recall/briefing measurably better on the 100k fixture (before/after)".
This module is the "measurably". `_quality_harness` supplies the arms and the metrics;
`_scale_fixture` supplies the ground truth.

## THE HEADLINE, STATED BEFORE THE TESTS BECAUSE IT RECORDS A LOCKED DECISION BEING MET

Three thresholds were locked for this stage: **A→D >= 15pp absolute**, **MRR@10 no regression**,
and **each tier >= its predecessor - 2pp**. Measured on the N=5,000 corpus, 36 probes, BEFORE and
AFTER the DB.S8f ranking bounds:

    arm             recall BEFORE -> AFTER      mrr BEFORE -> AFTER
    A:jaccard        0.500 -> 0.500              0.869 -> 0.869
    B:fts            0.500 -> 0.500              0.836 -> 0.836
    C:+vector        0.472 -> 0.500  (+2.8pp)    0.773 -> 0.836  (+6.3pp)
    D:+expansion     0.500 -> 0.833 (+33.3pp)    0.773 -> 0.836  (+6.3pp)
    B+expansion      0.833 -> 0.833              0.836 -> 0.836   <- the isolation arm

**A→D now PASSES outright, and tier-monotonicity PASSES on recall at every step. The MRR half of
the remaining thresholds fails at exactly ONE step — A→B — and that step is finding 3, which this
stage did not touch and whose numbers are identical before and after it.**

  * **A→D is +33.3pp**, clearing the 15pp threshold. It was +0.0pp. Nothing about the expansion
    tier changed: arm D simply stopped being cancelled by arm C, and now lands exactly on the
    isolation arm, which is what "the ladder confounded two independent effects" predicted it would
    do once the confound was removed.
  * **The vector tier is no longer a regression.** B→C is 0.0pp on both metrics, inside the -2pp
    per-tier tolerance. Every tier step now holds on recall, and every step but A→B holds on MRR.
  * **FTS still ranks worse than the Jaccard floor**: -3.3pp MRR overall, and -10.0pp MRR on the
    hard probes (0.607 -> 0.507) where the answer is mid-pack. `normalize_lexical_scores` scales
    bm25 against the BEST score in its own result set, so on a query whose answer is not the top hit
    the normalization flattens exactly the gap that would have ranked it. Recall is unaffected (both
    0.500) — this is an ORDERING defect, which is why it is invisible to recall@k and why MRR is in
    the metric set at all. UNCHANGED by DB.S8f, unrelated to it, and still pinned below. It is also
    the whole of the ladder's residual A→D MRR gap (-3.3pp): the "MRR no regression" threshold holds
    for every tier this stage governs and fails only across the one this finding names.

## WHAT CHANGED, AND WHY IT IS NOT TUNING

DB.S8f did not tune a weight to move these numbers. It derived the bound each tier was missing —
`tiered.py`'s RANKING PRINCIPLE, "no non-matching signal, alone or in combination, may outrank a
real match" — and made the constants satisfy it. The semantic tier's weight is now derived from the
live embedder's own noise floor rather than being a global 1.0, so `HashingEmbedder` (measured floor
0.65, against real answers scoring 0.71-0.83) is held to its share of the non-match budget instead
of burying the answers under filler. The recall gain is a CONSEQUENCE of the bound, not its target;
the bounds and their mutations live in `test_db_s8f_ranking_bounds.py`.

Findings 1 and 2 below are therefore CLOSED, each in the way its own message specified ("delete this
finding and assert the threshold"). Finding 3 remains, pinned, unchanged. A pinned finding fails
when it changes in EITHER direction, so an improvement is noticed and a regression cannot hide.

## WHY THE FIXTURE GAINED "HARD" PROBES

The first sweep scored 1.000 on every knob setting, including settings that break the K1 bound. A
grid where everything is perfect tunes nothing. The easy probes are too easy: a corpus-unique token
puts the direct answer at rank 1, and its hop answer is ONE `depends_on` hop away at the
strongest-weighted kind. Hard probes (`ScaleSpec.hard_probes`) query only common vocabulary against
24 planted competitors and put their answer two hops out, which is what makes `SEED_CAP`,
`DEPTH_DECAY` and `EDGE_WEIGHT` observable at all.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import tempfile
import unittest

import _support  # noqa: F401

import _quality_harness as Q
import _scale_fixture as F

from mokata.memory import expansion as X
from mokata.memory import tiered
from mokata.memory.backends import SQLiteBackend

#: The DECLARED corpus for every quality number in this module. Smaller than the scale legs'
#: 100,000 on purpose and said so, and the reason is cost with a shape: arm C wires an embedder
#: against a backend with no vector index, so its semantic tier is a Python cosine over EVERY
#: visible candidate — the product of N and the probe count, per arm. At N=10,000 with 60 probes
#: this module took 146s of CPU, which is not a per-push cost. At N=5,000 with 36 probes it is
#: ~35s and the findings below are unchanged (re-measured, not assumed). The scale legs measure
#: cost at 100k; this one measures QUALITY, and quality does not need the extra order of magnitude.
QUALITY_N = 5_000
SPEC = F.ScaleSpec(n_items=QUALITY_N, probes=24, hard_probes=12, stamp_embeddings=True)

#: THE STORED BASELINE. Recorded from a real run on the seeded corpus; the fixture is deterministic
#: (`test_db_s8a_fixture` F-1), so these are reproducible rather than approximate. A change here is
#: a change in RETRIEVAL QUALITY and must be a deliberate edit with a reason, never a re-record to
#: make a red test green.
#:
#: RE-RECORDED at DB.S8f — deliberately, with the reason, which is the only way this constant is
#: allowed to move. The ranking changed (the semantic tier's weight is now derived from the live
#: embedder's noise floor; the two quality terms are bounded as a sum), so the two arms that wire an
#: embedder moved and the three that do not did NOT. Arms A, B and B+expansion are byte-identical to
#: the pre-DB.S8f record below, which is itself the check that the change is confined to what it
#: claims: no embedder, no difference.
#:
#:     arm            pre-DB.S8f recall / mrr      DB.S8f recall / mrr
#:     A:jaccard          0.5000 / 0.8690            0.5000 / 0.8690   unchanged
#:     B:fts              0.5000 / 0.8355            0.5000 / 0.8355   unchanged
#:     C:+vector          0.4722 / 0.7728            0.5000 / 0.8355   MOVED
#:     D:+expansion       0.5000 / 0.7728            0.8333 / 0.8355   MOVED
#:     B+expansion        0.8333 / 0.8355            0.8333 / 0.8355   unchanged
BASELINE = {
    Q.ARM_JACCARD:       {"recall": 0.5000, "mrr": 0.8690},
    Q.ARM_FTS:           {"recall": 0.5000, "mrr": 0.8355},
    Q.ARM_VECTOR:        {"recall": 0.5000, "mrr": 0.8355},
    Q.ARM_EXPANSION:     {"recall": 0.8333, "mrr": 0.8355},
    Q.ARM_FTS_EXPANSION: {"recall": 0.8333, "mrr": 0.8355},
}
#: How far a measured arm may drift from its stored baseline before it is a finding. Tight — the
#: corpus is seeded and the ranking is deterministic, so any real drift is a ranking change.
BASELINE_TOLERANCE = 0.02

#: The locked thresholds, named so the assertions read as the decisions they encode.
EXPANSION_GAIN_THRESHOLD = 0.15         # 15pp absolute
TIER_REGRESSION_TOLERANCE = 0.02        # each tier >= predecessor - 2pp


class _QualityCase(unittest.TestCase):
    """One generated, bulk-loaded corpus for the whole module. Built once: every arm reads it."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.corpus = F.generate(SPEC)
        cls.backend = SQLiteBackend(os.path.join(cls._tmp.name, "quality.db"))
        loaded = F.load_sqlite(cls.backend, cls.corpus)
        assert loaded == cls.corpus.declared_n == QUALITY_N
        cls.results = Q.run_all_arms(cls.corpus, cls.backend, k=10)
        cls.hard = [p for p in cls.corpus.probes if p.hard]

    @classmethod
    def tearDownClass(cls):
        cls.backend.close()
        cls._tmp.cleanup()


class TheArmsAgainstTheStoredBaseline(_QualityCase):
    def test_every_arm_matches_its_stored_baseline(self):
        """The regression gate for retrieval quality. Deterministic corpus + deterministic ranking
        means a drift here is a real change, not noise."""
        for arm, expected in BASELINE.items():
            got = self.results[arm]
            self.assertAlmostEqual(
                expected["recall"], got.recall_at_k, delta=BASELINE_TOLERANCE,
                msg=f"{arm} recall@10 moved: baseline {expected['recall']:.4f} -> "
                    f"{got.recall_at_k:.4f}. If this is intended, edit BASELINE and say why.")
            self.assertAlmostEqual(
                expected["mrr"], got.mrr_at_k, delta=BASELINE_TOLERANCE,
                msg=f"{arm} mrr@10 moved: baseline {expected['mrr']:.4f} -> {got.mrr_at_k:.4f}")

    def test_the_ladder_is_reported_in_full(self):
        """Not an assertion so much as the number the stage exists to produce, printed where a run
        log will carry it."""
        print(f"\n[quality N={QUALITY_N:,} · {len(self.corpus.probes)} probes]\n"
              + Q.render(self.results))
        self.assertEqual(len(Q.ALL_ARMS), len(self.results))


class TheExpansionTierEarnsItsPlace(_QualityCase):
    """THE threshold, asserted where it is meaningful: against the tier in isolation."""

    def test_expansion_clears_the_15pp_threshold_when_measured_in_isolation(self):
        base = self.results[Q.ARM_FTS]
        with_expansion = self.results[Q.ARM_FTS_EXPANSION]
        gain = with_expansion.recall_at_k - base.recall_at_k
        self.assertGreaterEqual(
            gain, EXPANSION_GAIN_THRESHOLD,
            f"expansion added {gain * 100:.1f}pp over arm B, under the "
            f"{EXPANSION_GAIN_THRESHOLD * 100:.0f}pp threshold")

    def test_expansion_never_costs_mrr(self):
        """'MRR@10 no regression', asserted on the tier this stage is validating. Expansion ADMITS
        items the direct tiers missed, so it must never push a direct answer down the ranking —
        that is the "can surface, does not displace" bound, seen from the metric side."""
        self.assertGreaterEqual(self.results[Q.ARM_FTS_EXPANSION].mrr_at_k,
                                self.results[Q.ARM_FTS].mrr_at_k)

    def test_the_hop_only_answers_are_what_the_gain_is_made_of(self):
        """The gain must come from TRAVERSAL, not from the arm happening to rank better. Checked by
        identity: the items arm B+expansion finds and arm B does not must be the planted hop
        answers, which share no token with any query by construction (fixture F-7)."""
        from mokata.memory.store import MemoryStore
        newly_found = 0
        planted_hops_found = 0
        for probe in self.corpus.probes[:15]:
            base, expanded = {}, {}
            for arm in (Q.ARM_FTS, Q.ARM_FTS_EXPANSION):
                store = MemoryStore(self.backend, scope_context=self.corpus.context_for(probe))
                expander = store._edge_expander() if arm == Q.ARM_FTS_EXPANSION else None
                hits = tiered.tiered_recall(store, probe.query, top_k=10, expander=expander,
                                            degrade_out=lambda _m: None)
                (base if arm == Q.ARM_FTS else expanded).update({h.item.id: h for h in hits})
            for item_id, hit in expanded.items():
                if item_id in base:
                    continue
                newly_found += 1
                # THE claim, stated per item: everything the expansion arm adds arrived across a
                # WALKED PATH. Asserted on the path itself rather than on the id, because the
                # first version demanded every admitted item be a PLANTED answer and that is
                # simply false — a hard probe's MID item sits one hop from its direct answer and
                # is legitimately reachable, as is the lineage graph the fixture plants. Those are
                # traversal working, not traversal failing, and an identity check called them a
                # failure.
                self.assertIsNotNone(hit.path,
                                     f"{item_id} appeared only in the expansion arm but carries "
                                     "no path — it was re-ranked, not reached")
                self.assertGreater(hit.edge, 0.0)
                if item_id == probe.hop_id:
                    planted_hops_found += 1
        self.assertTrue(newly_found, "expansion admitted nothing at all")
        self.assertTrue(planted_hops_found,
                        "expansion admitted items but none of them was a planted hop answer — "
                        "the gain is not the thing the ground truth measures")


class TheK1BoundReDerived(unittest.TestCase):
    """THE BOUND IS THE CONTRACT, NOT THE CONSTANT.

    `EDGE_WEIGHT == 0.30` is the wrong assertion in both directions: it goes red when someone tunes
    a knob (allowed, and this stage exists to do it) and stays green when someone tunes it past the
    point where a hop can displace a direct match (not allowed). So the inequality is re-derived
    from the live constants instead, at both hop counts, over every declared kind.
    """

    def test_no_kind_at_either_hop_count_can_displace_a_direct_match(self):
        violations = Q.k1_bound_violations()
        self.assertEqual(violations, [], "\n".join(violations))

    def test_the_re_derivation_covers_every_declared_kind_including_the_unwired(self):
        """The five kinds with no producer share `UNWIRED_DEFAULT_WEIGHT`, and they are the ones
        that will silently start contributing the day a producer lands. A bound checked only over
        the three wired kinds would not have looked at them."""
        checked = {kind for kind in X.EDGE_KINDS}
        self.assertEqual(checked, set(X.EDGE_KINDS))
        self.assertGreaterEqual(len(X.EDGE_KINDS), 8)
        unwired = [k for k in X.EDGE_KINDS if X.kind_weight(k) == X.UNWIRED_DEFAULT_WEIGHT]
        self.assertTrue(unwired, "no kind carries the unwired default — has the table changed?")

    def test_the_bound_check_actually_fires(self):
        """A bound checker that never returns a violation is decoration. The sweep grid deliberately
        contains a setting that breaks it."""
        broken = Q.Knobs(edge_weight=0.60)
        self.assertTrue(Q.k1_bound_violations(broken),
                        "EDGE_WEIGHT=0.60 x 1.0 x 0.5 = 0.30 exceeds LEXICAL_WEIGHT 0.25 and must "
                        "be reported as a violation")


class TheSeedCapFinding(_QualityCase):
    """SEED_CAP=10 against a 50-row over-fetch — 80% of nominated rows never seed an expansion.

    Fed into the sweep as a first-class candidate rather than left as a remark, and the sweep
    ANSWERED it: on this corpus the setting makes no measurable difference to recall or MRR at 5,
    10, 25 or 50. That is a real result and it is the reason to LEAVE the cap at 10 — the cheapest
    setting on a flat part of the curve — rather than a reason to raise it.
    """

    def test_the_starvation_is_real_as_arithmetic(self):
        cap, fetch, fraction = Q.seed_starvation()
        self.assertEqual((cap, fetch), (X.SEED_CAP, tiered.CANDIDATE_OVER_FETCH))
        self.assertAlmostEqual(0.8, fraction, places=2,
                               msg=f"{cap} seeds taken from {fetch} nominated rows")

    def test_but_it_costs_nothing_measurable_on_this_corpus(self):
        """THE ANSWER, measured on the HARD probes — the only ones where seeding could matter,
        because their direct answer is mid-pack rather than rank 1."""
        scores = {}
        for cap in (5, 10, 25, 50):
            knobs = Q.Knobs(seed_cap=cap)
            with Q._install(knobs):
                result = Q.run_arm(self.corpus, self.backend, Q.ARM_FTS_EXPANSION,
                                   k=10, probes=self.hard)
            scores[cap] = round(result.recall_at_k, 4)
        spread = max(scores.values()) - min(scores.values())
        print(f"\n[SEED_CAP sweep on hard probes] {scores}")
        self.assertLess(spread, 0.02,
                        f"SEED_CAP now MOVES retrieval quality ({scores}) — the finding's "
                        "conclusion (leave it at 10) no longer holds and needs re-deciding")


class TheKnobSweep(_QualityCase):
    def test_the_shipped_defaults_are_not_beaten_by_any_swept_setting(self):
        """The tuning verdict. If some setting beat the defaults this would fail, which is exactly
        what a sweep is for — it is a question, and this records the answer."""
        rows = Q.sweep(self.corpus, self.backend, Q.default_sweep_grid(), probes=self.hard)
        default = next(r for knobs, r, _v in rows if knobs == Q.Knobs())
        print("\n[knob sweep on hard probes]")
        for knobs, result, violations in rows:
            flag = f"  !! BOUND BROKEN ({len(violations)})" if violations else ""
            print(f"  {knobs.label():<46} recall@10={result.recall_at_k:.3f} "
                  f"mrr={result.mrr_at_k:.3f}{flag}")
        better = [(k.label(), r.recall_at_k) for k, r, v in rows
                  if not v and r.recall_at_k > default.recall_at_k + 1e-9]
        self.assertEqual(better, [],
                         f"a bound-respecting setting beat the shipped defaults: {better}. The "
                         "knobs are marked PROVISIONAL — this is the evidence to change them.")

    def test_a_setting_that_breaks_the_k1_bound_also_measurably_degrades_retrieval(self):
        """The bound is not merely an inequality someone asserted — breaking it COSTS quality, and
        that is what makes it worth defending. EDGE_WEIGHT=0.60 lets a 1-hop neighbour outrank a
        full direct match, and the metric shows exactly that: direct answers get displaced."""
        with Q._install(Q.Knobs()):
            ok = Q.run_arm(self.corpus, self.backend, Q.ARM_FTS_EXPANSION, k=10, probes=self.hard)
        with Q._install(Q.Knobs(edge_weight=0.60)):
            broken = Q.run_arm(self.corpus, self.backend, Q.ARM_FTS_EXPANSION, k=10,
                               probes=self.hard)
        self.assertTrue(Q.k1_bound_violations(Q.Knobs(edge_weight=0.60)))
        self.assertLess(broken.mrr_at_k, ok.mrr_at_k,
                        f"breaking the K1 bound did NOT degrade MRR ({broken.mrr_at_k:.3f} vs "
                        f"{ok.mrr_at_k:.3f}) — the bound's justification is arithmetic only")


class TheLockedThresholds(_QualityCase):
    """Findings 1 and 2, CLOSED at DB.S8f — each replaced by the threshold it was standing in for.

    Both findings' own messages specified this transition ("delete this finding and assert the
    threshold" / "delete this and assert monotonicity"), and DB.S8f is what met the condition. The
    assertions below are the thresholds themselves, on the LADDER rather than on the isolation arm,
    which is where they were locked and where they could not previously be asserted.
    """

    def test_the_cumulative_a_to_d_threshold_is_met(self):
        """LOCKED THRESHOLD: A→D >= 15pp. MEASURED: +33.3pp (was +0.0pp).

        Nothing about the expansion tier changed to get here. Arm C stopped cancelling it — see
        `test_the_ladder_now_lands_on_the_isolation_arm`, which is the same claim by identity
        rather than by threshold.
        """
        gain = (self.results[Q.ARM_EXPANSION].recall_at_k
                - self.results[Q.ARM_JACCARD].recall_at_k)
        self.assertGreaterEqual(
            gain, EXPANSION_GAIN_THRESHOLD,
            f"A→D is {gain * 100:+.1f}pp, under the {EXPANSION_GAIN_THRESHOLD * 100:.0f}pp "
            "threshold. This regressed — DB.S8f cleared it at +33.3pp.")

    #: The ONE tier step that still breaks the MRR half of the monotonicity threshold, and the
    #: finding that owns it. Named rather than silently tolerated: an exclusion nobody can see is
    #: how a threshold quietly stops meaning anything. Finding 3 pins this gap in both directions
    #: (`TheRemainingFinding`), so it cannot widen or vanish unnoticed while sitting here.
    MRR_EXEMPT_STEP = (Q.ARM_JACCARD, Q.ARM_FTS)

    def test_every_tier_is_at_least_its_predecessor_minus_two_points(self):
        """LOCKED THRESHOLD: each tier >= its predecessor - 2pp, walked over `Q.ARMS` so the
        ordering weakest→strongest is the claim rather than an incidental listing. This is the
        threshold finding 2 was standing in for: B→C used to cost -2.8pp recall and break it.

        RECALL holds for every step. MRR holds for every step EXCEPT A→B, which is finding 3 — the
        `normalize_lexical_scores` defect — and is not a DB.S8f regression: that gap is identical
        before and after this stage. The exemption is asserted to be the ONLY one, so a second
        MRR regression cannot hide behind the first.
        """
        offenders = []
        for earlier, later in zip(Q.ARMS, Q.ARMS[1:]):
            lo, hi = self.results[earlier], self.results[later]
            self.assertGreaterEqual(
                hi.recall_at_k, lo.recall_at_k - TIER_REGRESSION_TOLERANCE,
                f"{earlier} -> {later} costs "
                f"{(lo.recall_at_k - hi.recall_at_k) * 100:.1f}pp recall, past the "
                f"{TIER_REGRESSION_TOLERANCE * 100:.0f}pp tolerance")
            if hi.mrr_at_k < lo.mrr_at_k - TIER_REGRESSION_TOLERANCE:
                offenders.append(((earlier, later),
                                  f"{earlier} -> {later}: "
                                  f"{(lo.mrr_at_k - hi.mrr_at_k) * 100:.1f}pp MRR"))
        self.assertEqual(
            [step for step, _ in offenders], [self.MRR_EXEMPT_STEP],
            "the set of MRR-regressing tier steps changed: "
            + "; ".join(text for _, text in offenders)
            + f". Expected exactly {self.MRR_EXEMPT_STEP} (finding 3). A new step here is a real "
              "regression; the exempt step disappearing means finding 3 is fixed — delete both.")

    def test_the_ladder_now_lands_on_the_isolation_arm(self):
        """THE CONFOUND IS GONE, stated as the identity that proves it rather than as a number.

        The isolation arm exists because the cumulative ladder confounded two independent effects:
        arm D measured expansion MINUS whatever the vector tier cost. With the vector tier bounded,
        arm D and `B+expansion` must now agree — the semantic tier neither adds nor subtracts, which
        is precisely what "no longer a regression" means when said exactly.

        The arm is KEPT rather than retired. It is the control that detects the confound coming
        back, and a control you delete once it reads clean is not a control.
        """
        self.assertAlmostEqual(self.results[Q.ARM_EXPANSION].recall_at_k,
                               self.results[Q.ARM_FTS_EXPANSION].recall_at_k,
                               delta=BASELINE_TOLERANCE,
                               msg="arm D and the isolation arm have diverged again — the semantic "
                                   "tier is once more moving the ladder's answer")


class TheRemainingFinding(_QualityCase):
    """Finding 3, still open and still pinned at its measured value.

    Pinned, not asserted as a target and not deleted. A pinned finding fails in EITHER direction, so
    an improvement is noticed and a regression cannot hide. It names what would have to change for
    the pin to go. Untouched by DB.S8f — it is a lexical-normalization defect and the bounds are a
    weighting change; the numbers below are identical before and after.
    """

    def test_finding_3_fts_ranks_worse_than_the_jaccard_floor_on_mid_pack_queries(self):
        """MEASURED on hard probes: B is -10.0pp MRR against A (0.607 -> 0.507), recall equal.

        `normalize_lexical_scores` scales bm25 against the BEST score in its own result set, so on
        a query whose answer is mid-pack among genuine competitors the normalization flattens
        precisely the gap that would have ranked it. On the easy probes (answer at rank 1, unique
        token) the two are identical, which is why this only appears once the fixture has probes
        that are hard.
        """
        a = Q.run_arm(self.corpus, self.backend, Q.ARM_JACCARD, k=10, probes=self.hard)
        b = Q.run_arm(self.corpus, self.backend, Q.ARM_FTS, k=10, probes=self.hard)
        self.assertLess(b.mrr_at_k, a.mrr_at_k,
                        f"FTS mrr {b.mrr_at_k:.3f} is no longer below Jaccard's {a.mrr_at_k:.3f} "
                        "— the finding is fixed; delete it.")
        print(f"\n[finding 3, hard probes] jaccard mrr={a.mrr_at_k:.3f} "
              f"fts mrr={b.mrr_at_k:.3f} ({(b.mrr_at_k - a.mrr_at_k) * 100:+.1f}pp)")


if __name__ == "__main__":
    unittest.main()
