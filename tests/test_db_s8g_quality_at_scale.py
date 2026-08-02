"""DB.S8g — THE QUALITY ARMS AT THE DECLARED 100,000, which is what the exit criterion asked for.

WHY THIS MODULE EXISTS. Doc 86's 0.0.16 exit criterion reads "recall/briefing measurably better on
the **100k fixture** (before/after)". `test_db_s8d_quality` measures at a DECLARED 5,000, for a
costed and honest reason (arms A, C and D wire an embedder against a backend with no vector index,
so `_can_nominate` refuses candidate selection and each becomes a Python cosine over every visible
candidate — O(N x probes) per arm). The doc said 100k; the test said 5k; nothing reconciled them.

This module is the reconciliation, and it was built by RUNNING the arms at 100k rather than by
editing the criterion down — because running them turned out to matter. **Four DB.S8f conclusions
were measured at 5,000 and three of them DO NOT HOLD at 100,000:**

    arm            5k recall/mrr     100k recall/mrr
    A:jaccard      0.5000 / 0.8690   0.5000 / 0.8334
    B:fts          0.5000 / 0.8355   0.4444 / 0.7258
    C:+vector      0.5000 / 0.8361   0.4306 / 0.7235
    D:+expansion   0.8333 / 0.8361   0.7639 / 0.7235
    B+expansion    0.8333 / 0.8355   0.7778 / 0.7258

  1. **THE HEADLINE SURVIVES.** A->D recall is +26.4pp at 100k (0.5000 -> 0.7639) against a locked
     15pp threshold. Smaller than the +33.3pp DB.S8f recorded, and still comfortably clear. The
     release's central retrieval claim holds at the scale the criterion names.

  2. **FTS-NORMALIZE-FLATTENS IS ~3x WORSE AT SCALE, and it costs RECALL too, not only ordering.**
     The A->B MRR gap goes -3.3pp -> -10.8pp, and arm B now loses RECALL as well (0.5000 ->
     0.4444). DB.S8d filed this as "an ORDERING defect, invisible to recall@k entirely". At 5,000
     that was true. At 100,000 it is not: bm25 normalization against the result set's own max
     drops genuine answers out of the top k once there are enough competitors.

  3. **THE ARM-D CONFOUND IS BACK.** DB.S8f's closing evidence was that arm D lands exactly ON the
     isolation arm (B+expansion), so the ladder's confound was gone and the identity could be
     asserted rather than the number re-recorded. At 100k they diverge: D 0.7639 < B+expansion
     0.7778. The embedder arm is 1.4pp WORSE than the same ladder without it.

  4. **THE VECTOR TIER IS NET-NEGATIVE ON RECALL at 100k.** C 0.4306 < B 0.4444. DB.S8f CLOSED
     VECTOR-TIER-NOISE on the strength of arm C recovering to parity with B at 5,000. It does not
     recover at 100,000 — with `HashingEmbedder`, whose noise floor (0.65) very nearly overlaps
     its signal band (0.71-0.83), more corpus means more near-signal noise.

WHAT THIS MODULE DOES NOT DO, stated so nobody mistakes its scope: it does not RE-TUNE the knobs at
100k. Re-deriving the four bounds against this corpus and re-fitting `EDGE_WEIGHT`/`DEPTH_DECAY`/
the quality weights is a stage, not a test file, and doing it inside a measurement module is how a
baseline gets fitted to the thing it is supposed to be judging. The findings above are FILED
(doc 84) and the numbers are pinned here so they cannot drift silently.

COST, and why this is opt-in. 554s of arm time at 100k (A 176.6s, C 185.2s, D 190.0s; B and
B+expansion ~1s each, because those two take R-1's bounded candidate path and the other three do
not) plus ~6s of fixture. That is not a per-push cost, so `test_db_s8d_quality` stays the per-push
regression gate at its declared 5,000 and this runs on dispatch. Gated behind an explicit env var
rather than a size heuristic, and `_SCALE_LIVE`/`_SCALE_REASON` are exported for the CI preflight
to import — a skipped leg reads GREEN (LIVE-LEG-ORPHANS), so the job asserts the gate is ON.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
import os
import tempfile
import unittest

import _support  # noqa: F401

import _quality_harness as Q
import _scale_fixture as F

from mokata.memory.backends import SQLiteBackend

#: THE DECLARED N of this leg — the number doc 86's exit criterion names. Stated once, asserted
#: against the corpus at load, and reported in the message, so the three cannot disagree.
QUALITY_SCALE_N = 100_000
SPEC = F.ScaleSpec(n_items=QUALITY_SCALE_N, probes=24, hard_probes=12, stamp_embeddings=True)

#: The locked threshold from DB.S8d, unchanged: the full ladder must beat the Jaccard floor on
#: recall@10 by at least this much. It is checked HERE at 100k, which is where the criterion asks.
MIN_LADDER_RECALL_GAIN = 0.15

#: THE STORED BASELINE AT 100k. Recorded from a real run on the seeded corpus (2026-08-01); the
#: fixture is deterministic (`test_db_s8a_fixture` F-1) so these reproduce rather than approximate.
#: A change here is a change in retrieval quality at scale and must be a deliberate edit with a
#: reason — never a re-record to make a red test green.
BASELINE_100K = {
    Q.ARM_JACCARD:       {"recall": 0.5000, "mrr": 0.8334},
    Q.ARM_FTS:           {"recall": 0.4444, "mrr": 0.7258},
    Q.ARM_VECTOR:        {"recall": 0.4306, "mrr": 0.7235},
    Q.ARM_EXPANSION:     {"recall": 0.7639, "mrr": 0.7235},
    Q.ARM_FTS_EXPANSION: {"recall": 0.7778, "mrr": 0.7258},
}

TOL = 0.001


def _probe_live():
    """Is the 100k quality leg switched on? ONE definition — the CI preflight imports THIS rather
    than re-deriving an equivalent check, so a preflight cannot pass while the tests skip."""
    if os.environ.get("MOKATA_QUALITY_SCALE") not in ("1", "true", "yes"):
        return False, ("MOKATA_QUALITY_SCALE is not set — this leg costs ~9 minutes of arm time "
                       "at N=100,000 and is opt-in by design")
    return True, ""


_SCALE_LIVE, _SCALE_REASON = _probe_live()


@unittest.skipUnless(_SCALE_LIVE, _SCALE_REASON)
class QualityAtTheDeclaredScale(unittest.TestCase):
    """One corpus, one load, every arm — the ladder measured where the exit criterion names."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.corpus = F.generate(SPEC)
        cls.backend = SQLiteBackend(os.path.join(cls._tmp.name, "quality_scale.db"))
        loaded = F.load_sqlite(cls.backend, cls.corpus)
        assert loaded == cls.corpus.declared_n == QUALITY_SCALE_N, (
            f"loaded {loaded} rows for a corpus declaring {QUALITY_SCALE_N}")
        cls.results = {arm: Q.run_arm(cls.corpus, cls.backend, arm) for arm in Q.ALL_ARMS}

    @classmethod
    def tearDownClass(cls):
        cls.backend.close()
        cls._tmp.cleanup()

    def test_the_declared_n_is_the_n_that_ran(self):
        """The anti-silent-cap pin, in the shape DB.S8c established: three numbers — the constant,
        the corpus and the loaded table — asserted to be ONE number. A leg that quietly measured
        5,000 while reporting 100,000 is the exact failure this whole module exists to close."""
        self.assertEqual(QUALITY_SCALE_N, self.corpus.declared_n)
        self.assertEqual(QUALITY_SCALE_N, SPEC.n_items)
        self.assertEqual(QUALITY_SCALE_N, len(self.corpus.items))

    def test_the_measured_arms_match_the_stored_baseline(self):
        report = "\n".join(self.results[a].row() for a in Q.ALL_ARMS)
        for arm, expected in BASELINE_100K.items():
            got = self.results[arm]
            self.assertAlmostEqual(expected["recall"], got.recall_at_k, delta=TOL,
                                   msg=f"{arm} recall moved at N={QUALITY_SCALE_N:,}\n{report}")
            self.assertAlmostEqual(expected["mrr"], got.mrr_at_k, delta=TOL,
                                   msg=f"{arm} mrr moved at N={QUALITY_SCALE_N:,}\n{report}")

    def test_THE_EXIT_CRITERION_the_full_ladder_beats_the_floor_at_100k(self):
        """Doc 86's 0.0.16 exit criterion, checked at the N it names.

        This is the claim the release ships on, and it SURVIVES the move from 5k to 100k — at a
        reduced magnitude (+26.4pp here against +33.3pp at 5,000), which is recorded rather than
        smoothed over."""
        floor = self.results[Q.ARM_JACCARD].recall_at_k
        ladder = self.results[Q.ARM_EXPANSION].recall_at_k
        self.assertGreaterEqual(
            ladder - floor, MIN_LADDER_RECALL_GAIN,
            f"the full ladder gains only {ladder - floor:+.4f} recall@10 over the Jaccard floor at "
            f"N={QUALITY_SCALE_N:,}, under the locked {MIN_LADDER_RECALL_GAIN:.2f} threshold. The "
            f"release's central retrieval claim does not hold at the scale doc 86 names.")

    # ---------------------------------------------------------------- the three DIVERGENCES
    # Each of these RECORDS a DB.S8f conclusion that does not survive the move to 100k. They are
    # asserted so the finding cannot drift silently — NOT because the behaviour is desired. Each
    # names the doc-84 row that owns the fix. When a row is fixed, the pin here INVERTS.

    def test_finding_fts_normalize_flattens_costs_RECALL_at_scale_not_only_ordering(self):
        """FTS-NORMALIZE-FLATTENS (doc 84) was filed as "an ORDERING defect, invisible to recall@k
        entirely". True at 5,000, FALSE at 100,000: arm B loses recall against the Jaccard floor,
        not merely MRR. The row's scope is widened by this measurement."""
        a, b = self.results[Q.ARM_JACCARD], self.results[Q.ARM_FTS]
        self.assertLess(b.recall_at_k, a.recall_at_k,
                        "arm B no longer loses RECALL to the Jaccard floor at 100k — if this is a "
                        "fix, invert this pin and update FTS-NORMALIZE-FLATTENS")
        self.assertLess(b.mrr_at_k, a.mrr_at_k)

    def test_finding_arm_D_no_longer_lands_on_the_isolation_arm(self):
        """DB.S8f's closing evidence was that arm D lands EXACTLY on the isolation arm, so the
        ladder's confound was gone. At 100k they diverge and the embedder arm is WORSE."""
        d = self.results[Q.ARM_EXPANSION].recall_at_k
        iso = self.results[Q.ARM_FTS_EXPANSION].recall_at_k
        self.assertLess(d, iso,
                        "arm D has caught up with the isolation arm at 100k — the DB.S8f identity "
                        "would then hold at scale and this finding should be retired")

    def test_finding_the_vector_tier_is_net_negative_on_recall_at_scale(self):
        """VECTOR-TIER-NOISE was CLOSED at DB.S8f because arm C recovered to parity with arm B at
        5,000. It does not recover at 100,000 — with `HashingEmbedder`, whose noise floor (0.65)
        very nearly overlaps its signal band (0.71-0.83), more corpus means more near-signal noise.

        NOTE the scope, because it is what keeps this honest: `memory.embedder` is UNSET on a
        default install, so the shipped default arm is B+expansion and this measures an OPT-IN
        embedder — the same distinction DB.S8f drew. The embeddings CI leg is what will let this
        be re-measured against a REAL embedder (probed floor 0.3067) rather than the hashing one."""
        b, c = self.results[Q.ARM_FTS], self.results[Q.ARM_VECTOR]
        self.assertLess(c.recall_at_k, b.recall_at_k,
                        "arm C no longer costs recall against arm B at 100k — VECTOR-TIER-NOISE "
                        "can stay closed and this finding retired")

    def test_the_bounds_still_hold_at_scale(self):
        """The four DB.S8f bounds are arithmetic over live constants, so scale cannot break them —
        asserted anyway, because "cannot" is what a regression guard is for."""
        self.assertEqual([], Q.all_bound_violations())

    def test_report(self):
        print(f"\n[DB.S8g quality at the DECLARED N={QUALITY_SCALE_N:,} · "
              f"{len(self.corpus.probes)} probes]")
        for arm in Q.ALL_ARMS:
            print("  " + self.results[arm].row())
        floor = self.results[Q.ARM_JACCARD].recall_at_k
        ladder = self.results[Q.ARM_EXPANSION].recall_at_k
        print(f"  A->D recall gain {ladder - floor:+.4f} "
              f"(locked threshold {MIN_LADDER_RECALL_GAIN:.2f})")


if __name__ == "__main__":
    unittest.main()
