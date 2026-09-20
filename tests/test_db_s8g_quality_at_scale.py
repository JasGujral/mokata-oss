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

from mokata.memory import MemoryItem
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
#: 🔴 RE-RECORDED AT 0.0.20 STAGE 04 — `FTS-NORMALIZE-FLATTENS` IS REPAIRED (FTS5 SELECTS, Jaccard
#: SCORES). Stage 03 measured it on two independently seeded 100k corpora; `03-ranking-bm25.
#: findings.md` is the derivation. **EVERY arm moved and none moved DOWN:**
#:
#:     arm                 before recall / mrr      stage 04 recall / mrr    Δ
#:     A:jaccard           0.5000 / 0.8334          0.5000 / 0.8334    unchanged — THE CONTROL
#:     B:fts               0.4444 / 0.7258          0.5000 / 0.8528    +5.56pp / +12.70pp
#:     C:+vector           0.4306 / 0.7235          0.5000 / 0.8253    +6.94pp / +10.18pp
#:     D:+expansion        0.7639 / 0.7235          0.8333 / 0.8253    +6.94pp / +10.18pp
#:     B+expansion         0.7778 / 0.7258          0.8333 / 0.8528    +5.55pp / +12.70pp
#:     B+expansion(full)   0.7778 / 0.7258          0.8333 / 0.8667    +5.55pp / +14.09pp
#:
#: ⭐ **ARM A IS THE CONTROL AND IT DID NOT MOVE** — the floor never touches FTS, so a change
#: confined to what it claims must leave it byte-identical, and it does at both scales.
#: ⭐ **THE DISCLOSED REGRESSION INVERTS:** arm B was −5.56pp / −10.76pp against the floor; it is
#: now **+0.00pp / +1.94pp**. Every arm gains on both metrics; none loses on either.
#:
#: 🔴 A FIRST PASS SCORED WITH RAW JACCARD AND READ BETTER STILL — both expansion arms reached
#: recall 1.0000. **That was the K1 BOUND FAILING.** `normalize_lexical_scores` maps the tier onto
#: the `[0,1]` range the fusion weights assume, and K1 is stated against a full match worth exactly
#: `LEXICAL_WEIGHT`; raw Jaccard never reaches 1.0, so the lexical term shrank and 1-hop neighbours
#: climbed over direct matches, inflating expansion recall. `test_db_s7b`'s *"can surface, does not
#: displace"* caught it. ⛔ **Those numbers are void and are recorded here so nobody re-derives
#: them from the git history and believes them.**
BASELINE_100K = {
    Q.ARM_JACCARD:       {"recall": 0.5000, "mrr": 0.8334},
    Q.ARM_FTS:           {"recall": 0.5000, "mrr": 0.8528},
    Q.ARM_VECTOR:        {"recall": 0.5000, "mrr": 0.8253},
    Q.ARM_EXPANSION:     {"recall": 0.8333, "mrr": 0.8253},
    Q.ARM_FTS_EXPANSION: {"recall": 0.8333, "mrr": 0.8528},
    #: 0.0.20 stage 01 — THE PATH CONTROL, recorded from a real 100k run on 2026-08-25.
    #: ⭐ **BYTE-IDENTICAL TO `B+expansion` ABOVE, and that identity is the stage's whole result.**
    #: This arm reads the full active set (1074.0s) where `B+expansion` reads a bounded 50 (2.1s) —
    #: a 511x cost difference that moves recall and MRR by 0.0000 in both directions.
    Q.ARM_FTS_EXPANSION_FULL: {"recall": 0.8333, "mrr": 0.8667},
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


def engine_rows(backend) -> int:
    """`SELECT count(*)` off the STORE ITSELF — the only counter in this file that no Python-side
    bookkeeping can flatter. Kept beside the pin that needs it rather than reaching into the
    fixture, and it knows nothing about how the rows got there."""
    with backend._connect() as conn:
        return conn.execute("SELECT count(*) FROM memory").fetchone()[0]


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
        # DB.S8c's shape, completed (audit finding B). Everything above this line is a
        # PYTHON-side counter: `load_sqlite` returns calls made, `declared_n` and
        # `len(corpus.items)` describe the in-memory list. `SQLiteBackend.put` is
        # `INSERT … ON CONFLICT(id) DO UPDATE`, so colliding ids land FEWER rows while every
        # one of those numbers still reads 100,000. The engine is the only witness that
        # cannot be talked round — DB.S8c has always counted it (test_db_s8c_live_db.py:93-94)
        # and this pin's docstring claimed the same shape without having it.
        cls.engine_rows = engine_rows(cls.backend)
        assert cls.engine_rows == QUALITY_SCALE_N, (
            f"the table holds {cls.engine_rows} rows, not the declared {QUALITY_SCALE_N}")
        cls.results = {arm: Q.run_arm(cls.corpus, cls.backend, arm) for arm in Q.ALL_ARMS}

    @classmethod
    def tearDownClass(cls):
        cls.backend.close()
        cls._tmp.cleanup()

    def test_the_declared_n_is_the_n_that_ran(self):
        """The anti-silent-cap pin, in the shape DB.S8c established: FOUR numbers — the constant,
        the corpus, the write path's own count and THE TABLE — asserted to be ONE number. A leg
        that quietly measured 5,000 while reporting 100,000 is the exact failure this whole module
        exists to close, and until the table joined the list this pin could not have caught the
        upsert case (see `TheEngineIsTheOnlyHonestCounter` below, which runs per-push)."""
        self.assertEqual(QUALITY_SCALE_N, self.engine_rows)
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

    def test_fts_normalize_flattens_is_REPAIRED_at_scale(self):
        """🟢 **INVERTED AT STAGE 04, WHICH IS WHAT THE OLD PIN ASKED FOR.**

        It read: *"arm B no longer loses RECALL to the Jaccard floor at 100k — if this is a fix,
        invert this pin and update FTS-NORMALIZE-FLATTENS"*. It is a fix. The row was filed as *"an
        ORDERING defect, invisible to recall@k entirely"*, widened at 100k when arm B turned out to
        lose recall too — and stage 03's repair closes both halves at the scale that showed them.

        ⛔ THE ASSERTION IS "NO LONGER WORSE", NOT "BETTER". Arm B measures `0.5000/0.8528` against
        the floor's `0.5000/0.8334`, so it IS ahead — but pinning a margin would pin one corpus's
        luck, and the disclosure only ever claimed a loss."""
        a, b = self.results[Q.ARM_JACCARD], self.results[Q.ARM_FTS]
        self.assertGreaterEqual(b.recall_at_k, a.recall_at_k - TOL,
                                "arm B lost RECALL to the Jaccard floor again — "
                                "`FTS-NORMALIZE-FLATTENS` has returned at scale")
        self.assertGreaterEqual(b.mrr_at_k, a.mrr_at_k - TOL,
                                "arm B fell back below the Jaccard floor on MRR at scale")

    def test_arm_D_lands_on_the_isolation_arm_ON_RECALL_and_diverges_ON_MRR(self):
        """🟢 **RE-DERIVED AT STAGE 04, AND THE OLD PIN ASKED FOR IT IN THOSE WORDS.**

        It asserted arm D loses RECALL to the isolation arm at 100k — DB.S8f's closing evidence
        having been that they land exactly together — and its message said: *"arm D has caught up
        with the isolation arm at 100k — the DB.S8f identity would then hold at scale and this
        finding should be retired"*. **Stage 03's lexical repair is what made it catch up: both
        measure 0.8333, from 0.7639 against 0.7778.**

        ⛔ THE FINDING IS NOT RETIRED WHOLESALE, because the divergence moved metric rather than
        vanishing: arm D still costs **−2.75pp MRR** against the isolation arm. ⭐ That is the SAME
        fact as `the_vector_tiers_cost_at_scale_is_MRR_NOW`, seen from the ladder instead of from
        the rung — B→C and D→(B+expansion) are both the semantic tier — and the two tests agreeing
        is what says the attribution below is reading one effect rather than two."""
        d = self.results[Q.ARM_EXPANSION]
        iso = self.results[Q.ARM_FTS_EXPANSION]
        self.assertAlmostEqual(d.recall_at_k, iso.recall_at_k, delta=TOL,
                               msg="arm D and the isolation arm diverged on RECALL again at 100k — "
                                   "`ARM-D-CONFOUND-BACK`'s original claim is back, and it was "
                                   "closed on stage 03's repair")
        self.assertLess(d.mrr_at_k, iso.mrr_at_k,
                        "the semantic tier stopped costing MRR at 100k as well — that is the whole "
                        "of the finding gone; retire it and re-measure VECTOR-TIER-NOISE with it")

    def test_the_divergence_is_ATTRIBUTED_and_the_candidate_path_owns_none_of_it(self):
        """0.0.20 stage 01 — `ARM-D-CONFOUND-BACK`, and the row is REFUTED by the measurement it
        asked for.

        The row said the ladder "can no longer attribute its own gain", because the B->C rung
        flips two variables at once: the semantic tier turns ON and the bounded candidate read
        turns OFF. It proposed watching `D - (B+expansion)` as the tier's standing drag. **That
        delta spans both variables**, so it could not be the tier's drag on the argument's own
        terms — and at 100k arm D costs 1187.5s against `B+expansion`'s 2.1s, which is a different
        read, not a scoring tier's price.

        ⭐ **MEASURED WITH THE PATH CONTROL, 2026-08-25: the candidate path owns 0.0000pp of it.**
        `B+expansion(full)` reads the full active set with the semantic tier off and lands
        BYTE-IDENTICALLY on `B+expansion` — 0.7778 / 0.7258, both metrics, at 511x the cost. So
        the whole -1.389pp is the semantic tier, and `D - (B+expansion)` was the right number all
        along.

        ⛔ **That is not a vindication of the row and this docstring will not read as one.** The
        delta was right by coincidence — one of its two terms happens to be zero — and nobody could
        know which until the control existed. A number that is correct for a reason you cannot
        state is not a measurement. **This test asserts the decomposition, not the coincidence:**
        if the path term ever stops being zero, the old delta silently stops meaning what four
        months of notes say it means, and this reds instead.
        """
        d = self.results[Q.ARM_EXPANSION]
        iso = self.results[Q.ARM_FTS_EXPANSION]
        full = self.results[Q.ARM_FTS_EXPANSION_FULL]
        # ⚠ **AMENDED AT STAGE 04: RECALL IS STILL AN IDENTITY; MRR IS NOW A BOUND.** With bm25
        # ordering the nominees, the bounded and wide paths were byte-identical on both metrics and
        # stage 01 recorded `0.0000pp`. With Jaccard scoring them, the two paths' slightly different
        # candidate sets become visible — **0.0000pp recall, 1.39pp MRR at N=100,000** (1.53pp at
        # N=5,000). ⭐ The old pin did exactly what it was written to do: it red, and its message
        # said *"quote `D - B+expansion(full)` instead"*. That is what the tier attribution below
        # now rests on, so the weaker claim costs the argument nothing.
        self.assertAlmostEqual(
            0.0, full.recall_at_k - iso.recall_at_k, delta=TOL,
            msg=("the CANDIDATE PATH started owning RECALL at 100k — a bounded read that finds "
                 "different ANSWERS is not an optimization, and `D - B+expansion(full)` stops "
                 "isolating the semantic tier"))
        path_mrr = abs(full.mrr_at_k - iso.mrr_at_k)
        self.assertLess(
            path_mrr, 0.02,
            f"the CANDIDATE PATH now owns {path_mrr*100:+.3f}pp of MRR at 100k, past the 2.00pp "
            "bound stage 04 derived from 1.39pp measured. Re-check every ranking claim in the "
            "notes that rests on `D - (B+expansion)`.")
        tier = d.recall_at_k - full.recall_at_k
        self.assertAlmostEqual(d.recall_at_k - iso.recall_at_k, tier + (full.recall_at_k - iso.recall_at_k),
                               delta=1e-9)

    def test_finding_the_full_scan_costs_511x_and_buys_nothing_MEASURED(self):
        """⭐ THE STAGE'S OTHER RESULT, and it is about production rather than about the harness.

        `tiered._can_nominate`'s third condition sends any store with an embedder and no vector
        index to the full active-set scan. Its docstring argues this at length and the argument is
        sound in principle: nominating lexically and then re-scoring would redefine semantic recall
        as "re-rank the lexical hits", so an item that is semantically near and lexically zero
        would stop being findable.

        **On this fixture, at N=100,000, that trade buys 0.0000pp of recall and 0.0000pp of MRR,
        for 1074.0s against 2.1s.** It is the first number anyone has attached to the decision.

        ⚠ **SCOPE, stated because it is what keeps this honest and it is NOT a small caveat.** This
        measures `HashingEmbedder`, whose noise floor (0.65) very nearly overlaps its signal band
        (0.71-0.83) — so on this corpus there may simply BE no semantically-near / lexically-zero
        item for the wide read to find, in which case the trade is worth nothing HERE and could be
        worth a great deal against a real embedder. `memory.embedder` is unset on a default
        install, so the shipped default arm is `B+expansion` and pays none of this.
        ⛔ **This is a measurement, not a proposal to change `_can_nominate`.** The embeddings CI
        leg (probed floor 0.3067) is what could turn it into one.
        """
        iso = self.results[Q.ARM_FTS_EXPANSION]
        full = self.results[Q.ARM_FTS_EXPANSION_FULL]
        # ⚠ AMENDED AT STAGE 04. "Buys nothing" was measured while bm25 ordered the nominees, which
        # made the two paths byte-identical. With Jaccard scoring them the full scan buys
        # **0.0000pp of recall and 1.39pp of MRR** for the same 511x. The conclusion is unchanged
        # and the number is no longer zero, so the number is what is written down.
        self.assertAlmostEqual(iso.recall_at_k, full.recall_at_k, delta=TOL)
        self.assertLess(abs(iso.mrr_at_k - full.mrr_at_k), 0.02,
                        "the full scan started buying real MRR — at that point it is a trade "
                        "worth re-arguing, not a 511x tax on nothing")

    def test_the_vector_tiers_cost_at_scale_is_MRR_NOW_AND_NO_LONGER_RECALL(self):
        """⚠ RE-DERIVED AT STAGE 04, AND HALF OF `VECTOR-TIER-NOISE` IS CLOSED BY MEASUREMENT.

        This asserted arm C loses RECALL to arm B at 100k — the row's sharpest claim, and its own
        message said what to do if it stopped being true: *"VECTOR-TIER-NOISE can stay closed and
        this finding retired"*. Stage 03's lexical repair made it stop being true: **arm C recovers
        to recall parity with arm B (0.5000 both, from 0.4306 against 0.4444).**

        ⛔ THE ROW IS NOT RETIRED, because the other half survives and is now the whole of it: arm C
        still costs **−2.75pp MRR** against a repaired arm B. It is a RANKING cost, not a recall
        one, and stating it as recall would now be false.

        ⚠ Scope unchanged, and it is what keeps this honest: `memory.embedder` is UNSET on a default
        install, so this measures an OPT-IN `HashingEmbedder` whose noise floor (0.65) very nearly
        overlaps its signal band (0.71–0.83). The embeddings CI leg is what re-measures it against a
        real embedder."""
        b, c = self.results[Q.ARM_FTS], self.results[Q.ARM_VECTOR]
        self.assertAlmostEqual(c.recall_at_k, b.recall_at_k, delta=TOL,
                               msg="arm C's RECALL parity with arm B broke again at 100k — that is "
                                   "`VECTOR-TIER-NOISE`'s original claim returning, and it was "
                                   "closed on stage 03's repair")
        self.assertLess(c.mrr_at_k, b.mrr_at_k,
                        "arm C stopped costing MRR against arm B at 100k — the surviving half of "
                        "VECTOR-TIER-NOISE is gone too; retire the row")

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


class TheEngineIsTheOnlyHonestCounter(unittest.TestCase):
    """Why the anti-silent-cap pin above counts the TABLE (audit finding B, 2026-08-02).

    Deliberately NOT behind `MOKATA_QUALITY_SCALE`: it costs milliseconds, and a pin that only
    runs on a nine-minute opt-in leg is a pin nobody sees fail. It also makes the 100k
    assertion legible — that leg cannot demonstrate this without a corrupt corpus, so the
    demonstration lives here at N=3 and the leg carries the assertion.

    The hole was never a live defect (the real corpus's ids are `fill-%07d` and unique — the
    audit counted the table by hand and got exactly 100,000). It was that the pin did not prove
    what its own docstring said: "three numbers … asserted to be ONE number" described the
    CORPUS three times over, while `SQLiteBackend.put` is an UPSERT."""

    def _item(self, ident: str) -> MemoryItem:
        return MemoryItem(id=ident, subject=f"subject {ident}", value=f"value {ident}")

    def test_a_colliding_id_is_invisible_to_every_python_side_counter(self):
        # A corpus that DECLARES three items, HOLDS three items, and whose write path MAKES
        # three put calls — landing two rows. Every number a Python-side counter can offer
        # says 3. Only the engine says 2.
        corpus = F.Corpus(
            spec=F.ScaleSpec(n_items=3, probes=0, hard_probes=0),
            items=[self._item("dup"), self._item("other"), self._item("dup")],
            probes=[],
        )
        with tempfile.TemporaryDirectory() as tmp:
            backend = SQLiteBackend(os.path.join(tmp, "collision.db"))
            try:
                loaded = F.load_sqlite(backend, corpus)
                self.assertEqual(3, loaded, "the write path counts CALLS, not rows")
                self.assertEqual(3, corpus.declared_n, "the corpus declares three")
                self.assertEqual(3, len(corpus.items), "and holds three")
                self.assertEqual(
                    2, engine_rows(backend),
                    "the upsert is supposed to collapse the collision — if this is 3 the "
                    "backend stopped being an upsert and this whole pin needs rethinking",
                )
            finally:
                backend.close()

    def test_the_engine_count_agrees_when_nothing_collides(self):
        # The other direction, so a counter that simply always under-reports would be caught.
        corpus = F.Corpus(
            spec=F.ScaleSpec(n_items=3, probes=0, hard_probes=0),
            items=[self._item("a"), self._item("b"), self._item("c")],
            probes=[],
        )
        with tempfile.TemporaryDirectory() as tmp:
            backend = SQLiteBackend(os.path.join(tmp, "clean.db"))
            try:
                self.assertEqual(3, F.load_sqlite(backend, corpus))
                self.assertEqual(3, engine_rows(backend))
            finally:
                backend.close()

    def test_the_scale_pin_actually_asserts_the_engine_count(self):
        """Source-level, because the assertion it guards only executes on the opt-in leg — and
        the finding being closed here is precisely "the pin does not assert what it claims"."""
        import inspect
        setup = inspect.getsource(QualityAtTheDeclaredScale.setUpClass)
        pin = inspect.getsource(QualityAtTheDeclaredScale.test_the_declared_n_is_the_n_that_ran)
        self.assertIn("engine_rows(cls.backend)", setup,
                      "setUpClass must count the ENGINE, as DB.S8c does")
        self.assertIn("self.engine_rows", pin,
                      "the anti-silent-cap pin must assert the engine count, not only the corpus")


if __name__ == "__main__":
    unittest.main()
