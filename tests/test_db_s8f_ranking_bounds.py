"""DB.S8f — the ranking bounds: K1's discipline applied to EVERY tier, not just the edge tier.

## THE PRINCIPLE, WHICH LIVES IN ONE PLACE

`tiered.py` states it once and every weight in that file answers to it:

    NO NON-MATCHING SIGNAL, ALONE OR IN COMBINATION, MAY OUTRANK A REAL MATCH.

DB.S7b derived that for the expansion tier and pinned it as an INEQUALITY over live constants
(K1) rather than as an assertion about a constant's value, so it goes red when a knob is tuned PAST
the bound and stays green when it is tuned within. That was the right shape and it was the ONLY
tier that had it. This module derives the analogous bound for each remaining tier and pins it the
same way.

## THE FOUR BOUNDS

    K1  EDGE       EDGE_WEIGHT x kind^hops x DEPTH_DECAY^hops  <  LEXICAL_WEIGHT   (DB.S7b)
    K2  SEMANTIC   semantic_weight_for(e) x noise_floor_of(e)  <= SEMANTIC_NOISE_BUDGET
    K3  QUALITY    RECENCY_WEIGHT + USAGE_WEIGHT               <= QUALITY_BUDGET
    K4  COMBINED   semantic + quality + edge                    <  NON_MATCH_BUDGET

K4 is the one the principle's "or in combination" actually names, and it is not implied by the
other three: an item can collect ALL of them at once while matching nothing — reached across a
`depends_on` hop, recalled often, and sitting on the embedder's cosine pedestal.

## WHAT WAS ACTUALLY WRONG (both measured, neither theoretical)

  * **K2 did not exist.** `SEMANTIC_WEIGHT` is 4x `LEXICAL_WEIGHT` and cosine between two UNRELATED
    items is a POSITIVE number — 0.65 for `HashingEmbedder`, measured at p99 on the DB.S8 fixture.
    So an item matching nothing collected 0.65 where a full lexical match collects 0.25. DB.S8d
    measured the consequence: the tier cost -2.8pp recall and -6.3pp MRR.
  * **K3 was bounded on the wrong thing.** Each quality term was below `LEXICAL_WEIGHT`; their SUM
    was EXACTLY `LEXICAL_WEIGHT` (0.15 + 0.10 = 0.25). A non-matching, heavily-recalled item tied a
    full lexical match and took the `created_at` tiebreak. Filed as QUALITY-TERM-SUM.

## MUTATION, NOT DECORATION

A bound checker that can never return a violation is decoration. Every bound here has a test that
BREAKS the inequality — by mutating the live constants the derivation reads, never by editing the
assertion — and shows the checker goes red. That is the same demand `TheK1BoundReDerived`'s
`test_the_bound_check_actually_fires` makes of K1, applied to all four.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import unittest
from unittest import mock

import _support  # noqa: F401

import _quality_harness as Q

from mokata.memory import expansion as X
from mokata.memory import tiered
from mokata.memory.embed import (HashingEmbedder, UNCHARACTERIZED_NOISE_FLOOR,
                                 measure_noise_floor, noise_floor_of)


class ThePrincipleIsStatedOnce(unittest.TestCase):
    """The budgets are the principle as arithmetic, and they live in ONE module."""

    def test_the_budget_is_one_full_lexical_match(self):
        """`NON_MATCH_BUDGET` is not an independent number to be tuned — it IS `LEXICAL_WEIGHT`,
        because "a real match" is what the ceiling is defined against."""
        self.assertEqual(tiered.NON_MATCH_BUDGET, tiered.LEXICAL_WEIGHT)

    def test_every_declared_share_fits_inside_the_budget(self):
        """The shares are declared separately and must sum STRICTLY below the budget — a sum that
        merely equals it is exactly the K3 defect this stage found (0.15 + 0.10 = 0.25)."""
        shares = tiered.SEMANTIC_NOISE_BUDGET + tiered.QUALITY_BUDGET + Q.max_edge_contribution()
        self.assertLess(shares, tiered.NON_MATCH_BUDGET,
                        f"the declared shares sum to {shares:.4f}, at or past the "
                        f"{tiered.NON_MATCH_BUDGET} budget")


class K2TheSemanticBound(unittest.TestCase):
    """A spurious cosine must not displace a lexical match."""

    def test_the_bound_holds_for_the_shipped_embedder(self):
        violations = Q.k2_bound_violations(HashingEmbedder())
        self.assertEqual(violations, [], "\n".join(violations))

    def test_the_weight_is_derived_from_the_live_embedder_not_from_the_constant(self):
        """THE fix, stated as the thing it changed. `SEMANTIC_WEIGHT` is one global constant; the
        noise floor is a property of the EMBEDDER, so no single value can be right for both a
        token-hash bag-of-words and a real static embedding. The weight is derived per embedder."""
        noisy = HashingEmbedder()
        self.assertGreater(noise_floor_of(noisy), tiered.SEMANTIC_NOISE_BUDGET)
        self.assertLess(tiered.semantic_weight_for(noisy), tiered.SEMANTIC_WEIGHT,
                        "a noisy embedder was handed the full declared weight")

    def test_a_quiet_embedder_earns_the_full_declared_weight(self):
        """The cap must not punish an embedder that does not need it — otherwise it is a nerf
        wearing a bound's clothes. An embedder whose unrelated-pair cosine is at or below the
        budget gets `SEMANTIC_WEIGHT` exactly."""
        class Quiet:
            noise_floor = tiered.SEMANTIC_NOISE_BUDGET

            def __call__(self, text):                     # pragma: no cover - never encoded here
                return [1.0]

        self.assertEqual(tiered.semantic_weight_for(Quiet()), tiered.SEMANTIC_WEIGHT)

    def test_no_embedder_leaves_the_arithmetic_untouched(self):
        """The default install has `memory.embedder` unset, so the semantic tier is OFF. The fusion
        must be term-for-term what it was before this stage on that path."""
        self.assertEqual(noise_floor_of(None), 0.0)
        self.assertEqual(tiered.semantic_weight_for(None), tiered.SEMANTIC_WEIGHT)

    # ------------------------------------------------------------------ mutation
    def test_understating_the_noise_floor_breaks_the_bound(self):
        """THE MUTATION. An embedder that claims to be silent when it is not — the exact shape of
        the defect, since `HashingEmbedder` carried no declaration at all until this stage. The
        checker re-measures instead of trusting, so the claim fails.

        Broken by mutating the CONSTANT the derivation reads, never by editing the assertion.
        """
        liar = HashingEmbedder()
        liar.noise_floor = 0.0                              # "I am orthogonal on unrelated text"
        # The fusion now hands it the full weight...
        self.assertEqual(tiered.semantic_weight_for(liar), tiered.SEMANTIC_WEIGHT)
        # ...and the bound, re-derived against what it ACTUALLY returns, catches it.
        measured = measure_noise_floor(liar)
        collected = tiered.semantic_weight_for(liar) * measured
        self.assertGreater(measured, 0.0, "the probe found no noise — re-measure the finding")
        self.assertGreaterEqual(
            collected, tiered.SEMANTIC_NOISE_BUDGET,
            f"an embedder declaring a 0.0 floor collected only {collected:.4f} — the mutation did "
            "not break the inequality and the bound is not being tested")

    def test_raising_the_declared_weight_past_the_budget_breaks_the_bound(self):
        """THE SECOND MUTATION, on the other constant in the inequality: with the cap removed,
        `SEMANTIC_WEIGHT` alone decides, and the shipped 1.0 against a 0.65 floor collects 0.65 —
        past `LEXICAL_WEIGHT`. This is the pre-DB.S8f fusion, reconstructed and shown red."""
        e = HashingEmbedder()
        legacy_collected = tiered.SEMANTIC_WEIGHT * noise_floor_of(e)
        self.assertGreater(legacy_collected, tiered.LEXICAL_WEIGHT,
                           "the pre-DB.S8f semantic tier did NOT out-collect a full lexical match "
                           "— finding 2's mechanism is not what was recorded")
        with mock.patch.object(tiered, "semantic_weight_for", lambda _e: tiered.SEMANTIC_WEIGHT):
            self.assertTrue(Q.k2_bound_violations(e),
                            "the uncapped weight must be reported as a K2 violation")


class K3TheQualityTermSum(unittest.TestCase):
    """RECENCY + USAGE — the bound is on the SUM, and that is the whole finding."""

    def test_the_bound_holds(self):
        violations = Q.k3_bound_violations()
        self.assertEqual(violations, [], "\n".join(violations))

    def test_the_sum_is_what_is_bounded_not_each_term(self):
        """Bounding each term individually was ALREADY true before this stage and was not enough:
        0.15 < 0.25 and 0.10 < 0.25, yet 0.15 + 0.10 = 0.25. The assertion is on the sum."""
        self.assertLess(tiered.RECENCY_WEIGHT + tiered.USAGE_WEIGHT, tiered.LEXICAL_WEIGHT)

    def test_both_terms_saturate_at_one_so_the_sum_is_reachable(self):
        """The bound is only meaningful if an item can actually COLLECT the whole sum. Both terms
        are documented in [0, 1] and both reach it: a just-recalled item scores recency 1.0, and
        usage saturates upward toward 1.0 with hits."""
        from mokata.memory.lifecycle import UsageSignal, recency_score, usage_score
        hot = UsageSignal(hits=10_000, last_recalled_at="2026-01-01T00:00:00Z")
        self.assertEqual(1.0, recency_score(hot, "", now="2026-01-01T00:00:00Z"))
        self.assertGreater(usage_score(hot), 0.99)

    # ------------------------------------------------------------------ mutation
    def test_restoring_the_old_weights_breaks_the_bound(self):
        """THE MUTATION, and it is literally the shipped-until-now pair: 0.15 + 0.10 = 0.25, equal
        to `LEXICAL_WEIGHT`. A non-matching item that has been recalled enough TIES a full direct
        match and wins on the `created_at` tiebreak, which is why the bound is `>=` and not `>`."""
        with mock.patch.object(tiered, "RECENCY_WEIGHT", 0.15), \
                mock.patch.object(tiered, "USAGE_WEIGHT", 0.10):
            violations = Q.k3_bound_violations()
            self.assertTrue(violations, "0.15 + 0.10 = 0.25 = LEXICAL_WEIGHT must be a violation")
            self.assertTrue(any("LEXICAL_WEIGHT" in v for v in violations), violations)


class K4TheCombination(unittest.TestCase):
    """"Alone OR IN COMBINATION" — the bound the other three do not imply."""

    def test_the_bound_holds(self):
        violations = Q.k4_bound_violations()
        self.assertEqual(violations, [], "\n".join(violations))

    def test_the_combination_is_not_implied_by_the_individual_bounds(self):
        """The point of K4, demonstrated rather than asserted: a setting where K1, K2 and K3 EACH
        pass and their sum still reaches the budget. Three satisfied bounds are not a satisfied
        sum, which is exactly why this check exists separately."""
        with mock.patch.object(tiered, "RECENCY_WEIGHT", 0.05), \
                mock.patch.object(tiered, "USAGE_WEIGHT", 0.04), \
                mock.patch.object(tiered, "QUALITY_BUDGET", 0.09), \
                mock.patch.object(tiered, "SEMANTIC_NOISE_BUDGET", 0.06):
            self.assertEqual(Q.k1_bound_violations(), [])
            self.assertEqual(Q.k3_bound_violations(), [])
            # semantic re-derives against the raised budget, so K2 passes too
            self.assertEqual(Q.k2_bound_violations(), [])
            # 0.06 + 0.09 + 0.15 = 0.30 >= 0.25
            self.assertTrue(Q.k4_bound_violations(),
                            "each bound passes individually but their sum exceeds the budget — K4 "
                            "must report it, or it is adding nothing over K1-K3")

    # ------------------------------------------------------------------ mutation
    def test_a_knob_within_k1_can_still_break_the_combination(self):
        """THE MUTATION, and it is the case with teeth: `EDGE_WEIGHT=0.45` respects K1 (0.45 x 1.0
        x 0.5 = 0.225 < 0.25) and is a setting the DB.S8d sweep grid actually evaluates. Summed
        with the semantic and quality shares it reaches 0.315, past the budget. K1 alone would have
        waved it through."""
        knobs = Q.Knobs(edge_weight=0.45)
        self.assertEqual(Q.k1_bound_violations(knobs), [],
                         "EDGE_WEIGHT=0.45 was expected to RESPECT K1 — re-derive the mutation")
        self.assertTrue(Q.k4_bound_violations(knobs),
                        "a K1-respecting knob that busts the combination must be reported")


class TheNoiseFloorIsProbedNotAssumed(unittest.TestCase):
    """The floor is DECLARED, else PROBED, else fails closed — the `_can_nominate` posture applied
    to a number instead of to a method."""

    def test_a_declaration_wins_over_the_probe(self):
        self.assertEqual(noise_floor_of(HashingEmbedder()), HashingEmbedder.noise_floor)

    def test_an_undeclared_embedder_is_probed_and_the_probe_finds_real_noise(self):
        """A bare callable is the seam's whole point (any `text -> list[float]` is legal), so it
        must be characterizable without an author doing anything. The probe uses UNRELATED text
        drawn from SHARED vocabulary — a disjoint-token probe measures 0.0 against a bag-of-words
        embedder and would certify the noisiest embedder in the tree as silent."""
        inner = HashingEmbedder()
        floor = noise_floor_of(lambda text: inner(text))
        self.assertGreater(floor, 0.0,
                           "the probe found no noise in a token-hash embedder — check that "
                           "NOISE_PROBE_DOCS still share ordinary vocabulary with the queries")
        self.assertLessEqual(floor, 1.0)

    def test_the_probe_is_deterministic(self):
        """The ranking is a pure function of its inputs, and a probed weight is part of the
        ranking. Fixed probe text, deterministic embedder, same answer every time."""
        self.assertEqual(measure_noise_floor(HashingEmbedder()),
                         measure_noise_floor(HashingEmbedder()))

    def test_an_unprobeable_embedder_fails_closed(self):
        """FAIL-CLOSED, the same posture `embedder_identity` takes when its dim probe raises. An
        embedder we could not characterize might return 1.0 between unrelated items, and the
        principle has no exemption for signals we failed to measure."""
        def broken(_text):
            raise RuntimeError("no")

        self.assertEqual(measure_noise_floor(broken), UNCHARACTERIZED_NOISE_FLOOR)
        self.assertEqual(noise_floor_of(broken), UNCHARACTERIZED_NOISE_FLOOR)
        # ...and that collapses the tier's weight to its budget share, never above it.
        self.assertAlmostEqual(tiered.semantic_weight_for(broken), tiered.SEMANTIC_NOISE_BUDGET)

    def test_a_non_numeric_declaration_fails_closed_too(self):
        """A broken declaration is not a licence to ignore the bound."""
        class Bogus:
            noise_floor = "quiet, trust me"

            def __call__(self, text):                     # pragma: no cover - never encoded here
                return [1.0]

        self.assertEqual(noise_floor_of(Bogus()), UNCHARACTERIZED_NOISE_FLOOR)

    def test_the_probe_is_cached_on_the_instance(self):
        """64 encodes ONCE, never per recall — a bound that costs a network round trip per query
        would be paid for by the thing it protects."""
        calls = []

        class Counting:
            def __call__(self, text):
                calls.append(text)
                return HashingEmbedder()(text)

        e = Counting()
        first = noise_floor_of(e)
        seen = len(calls)
        self.assertEqual(noise_floor_of(e), first)
        self.assertEqual(len(calls), seen, "the noise floor was re-probed on the second call")


if __name__ == "__main__":
    unittest.main()
