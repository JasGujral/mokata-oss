"""The REAL-EMBEDDER leg — everything about model2vec that the unit suite can only mock.

WHY THIS EXISTS. `mokata[embeddings]` is the one blessed extra, and until now no test in this
repo had ever loaded it: `test_db_s4_pgvector.py` patches `_load_model2vec` with a fake, and every
other embedder test uses `HashingEmbedder`. Two consequences, and the second is the one that
matters:

  1. DB.S4's DETECTION seam (`make_embedder("auto")` -> the extra when usable) was proven against
     a stand-in that always loads. Whether the real package loads, produces the dim it claims, and
     survives a cold cache was never executed.

  2. **K2's PROBED branch had never met a real embedder.** `noise_floor_of` is DECLARED -> PROBED
     -> UNCHARACTERIZED, and `HashingEmbedder` DECLARES its floor (0.65, measured at DB.S8f), so
     every K2 test in the unit suite takes the declared path. `Model2VecEmbedder` declares
     nothing — so the real extra is the ONLY thing that exercises the probe, and the probe is what
     decides the semantic tier's live weight. DB.S8f shipped that bound having measured only the
     noisy half of it.

WHAT THE LEG MEASURED (recorded here so a reader does not have to run it to know the shape):

    embedder                 noise floor      derived semantic weight
    HashingEmbedder          0.65 DECLARED    0.077
    model2vec potion-base-8M 0.3067 PROBED    0.163

So the real extra is roughly twice as quiet and earns roughly twice the weight — and is STILL
capped to ~16% of the declared `SEMANTIC_WEIGHT` ceiling by the K2 bound. That is the honest
headline: even the blessed embedder is a tiebreak, not a driver, under the non-match budget.
Asserted below as a RANGE, never as the literal, because pinning 0.3067 would make this leg fail
on a model2vec release that changes a weight by a thousandth — which is noise, not a regression.

THE GATE. Skips unless the extra is genuinely importable AND the model genuinely loads. That
makes a green skip possible, which is the LIVE-LEG-ORPHANS failure mode — so `_M2V_LIVE` and
`_M2V_REASON` are exported for the CI preflight to import, exactly as `test_db_s6_live_db`'s are.
The preflight asserts the gate is ON, so a job that would skip everything fails instead.
"""
import os
import tempfile
import unittest

import _support  # noqa: F401

from mokata.memory import embed
from mokata.memory.backends import SQLiteBackend
from mokata.memory.embed import (HASHING_ID, MODEL2VEC_MODEL, HashingEmbedder,
                                 Model2VecEmbedder, ModelUnavailable, make_embedder,
                                 measure_noise_floor, noise_floor_of)
from mokata.memory.item import CONTEXT, PERSISTENT, MemoryItem
from mokata.memory.store import MemoryStore
from mokata.memory.tiered import (SEMANTIC_NOISE_BUDGET, SEMANTIC_WEIGHT, semantic_weight_for)


def _probe_live():
    """Is a REAL model2vec usable here? Returns (live, reason). ONE definition — the CI preflight
    imports this rather than re-deriving an equivalent check, because two implementations of "is
    it live" is how a preflight passes while the tests it guards skip anyway."""
    if os.environ.get("MOKATA_SKIP_EMBEDDINGS_LEG"):
        return False, "MOKATA_SKIP_EMBEDDINGS_LEG is set"
    try:
        import model2vec  # noqa: F401
    except ImportError as exc:
        return False, f"model2vec is not installed ({exc}) — `pip install -e '.[embeddings]'`"
    try:
        Model2VecEmbedder()
    except ModelUnavailable as exc:
        return False, f"the model could not be loaded ({exc}) — cold cache + offline?"
    return True, ""


_M2V_LIVE, _M2V_REASON = _probe_live()

#: The probed floor observed for potion-base-8M (0.3067 on 2026-08-01). Asserted as a BAND: wide
#: enough that a model2vec point release cannot red the leg, narrow enough that "quieter than
#: hashing, but not silent" remains a claim rather than a tautology.
FLOOR_BAND = (0.10, 0.55)


@unittest.skipUnless(_M2V_LIVE, _M2V_REASON)
class TheDetectionSeamAgainstTheRealExtra(unittest.TestCase):
    """DB.S4's seam, executed rather than mocked."""

    def test_auto_resolves_to_the_real_extra_when_it_is_installed(self):
        e = make_embedder("auto")
        self.assertIsInstance(e, Model2VecEmbedder)
        self.assertEqual(f"model2vec:{MODEL2VEC_MODEL}", e.embedder_id)
        self.assertNotEqual(HASHING_ID, e.embedder_id,
                            "`auto` fell back to hashing with the extra INSTALLED — the whole "
                            "detection seam is inert")

    def test_the_dim_is_the_models_own_not_a_mokata_constant(self):
        """Probed from a real encode at construction. A wrong dim is how a re-embed migration
        silently no-ops, so it is read from the model, never assumed."""
        e = Model2VecEmbedder()
        self.assertEqual(len(e("dimension probe")), e.dim)
        self.assertGreater(e.dim, 0)

    def test_vectors_are_normalized(self):
        e = Model2VecEmbedder()
        norm = sum(v * v for v in e("the improbability drive")) ** 0.5
        self.assertAlmostEqual(1.0, norm, places=5)

    def test_an_explicit_hashing_ask_is_still_honoured_with_the_extra_present(self):
        """`hashing`/`local` must not be quietly upgraded because a better embedder is available —
        an explicit ask is a decision, not a preference."""
        self.assertIsInstance(make_embedder("hashing"), HashingEmbedder)
        self.assertIsInstance(make_embedder("local"), HashingEmbedder)


@unittest.skipUnless(_M2V_LIVE, _M2V_REASON)
class K2sProbedBranchAgainstARealEmbedder(unittest.TestCase):
    """THE point of this leg. Everything here is unreachable without the real extra installed."""

    def test_the_real_embedder_declares_no_floor_so_the_PROBE_is_what_runs(self):
        """The precondition for everything below. If model2vec ever starts declaring a floor, this
        leg stops testing the probe and must be re-pointed rather than silently passing."""
        self.assertIsNone(getattr(Model2VecEmbedder(), "noise_floor", None),
                          "model2vec now DECLARES a noise floor — this leg no longer exercises "
                          "K2's probed branch and needs re-anchoring")

    def test_the_probed_floor_is_a_real_measurement_in_a_plausible_band(self):
        e = Model2VecEmbedder()
        floor = measure_noise_floor(e)
        lo, hi = FLOOR_BAND
        self.assertTrue(lo <= floor <= hi,
                        f"probed noise floor {floor:.4f} is outside the plausible band "
                        f"{FLOOR_BAND} — either the probe corpus or the model changed materially")
        self.assertNotEqual(embed.UNCHARACTERIZED_NOISE_FLOOR, floor,
                            "the probe failed and fell closed — it did not measure anything")

    def test_the_probe_is_deterministic(self):
        """64 encodes on FIXED text. A ranking weight derived from a number that moves between
        runs would make recall non-reproducible, which is the whole reason the probe corpus is
        fixed rather than sampled."""
        self.assertEqual(measure_noise_floor(Model2VecEmbedder()),
                         measure_noise_floor(Model2VecEmbedder()))

    def test_the_floor_is_cached_on_the_instance_not_re_probed_per_recall(self):
        e = Model2VecEmbedder()
        calls = []
        real = e._encode_one
        e._encode_one = lambda t: calls.append(t) or real(t)
        noise_floor_of(e)
        first = len(calls)
        noise_floor_of(e)
        self.assertGreater(first, 0, "the probe never encoded anything")
        self.assertEqual(first, len(calls),
                         "the noise floor was re-probed — 64 encodes per recall, not per embedder")

    def test_the_K2_BOUND_holds_for_the_real_embedder(self):
        """The inequality DB.S8f shipped, checked against the embedder it had never seen:

            semantic_weight_for(e) * noise_floor_of(e)  <=  SEMANTIC_NOISE_BUDGET
        """
        e = Model2VecEmbedder()
        self.assertLessEqual(semantic_weight_for(e) * noise_floor_of(e),
                             SEMANTIC_NOISE_BUDGET + 1e-9)

    def test_the_real_embedder_is_measurably_QUIETER_than_hashing(self):
        """DB.S8f's claim was that HASHING is noisy (floor 0.65, weight 0.077), and it weighted it
        down accordingly. That claim is only meaningful if a real embedder scores better — which
        nothing in the repo could check. This is that check."""
        real, hashing = Model2VecEmbedder(), HashingEmbedder()
        self.assertLess(noise_floor_of(real), noise_floor_of(hashing),
                        "the real embedder is no quieter than token-hashing — either the probe "
                        "corpus is not discriminating or the model is not doing what it claims")
        self.assertGreater(semantic_weight_for(real), semantic_weight_for(hashing),
                           "a quieter embedder earned no more weight — the derivation is inert")

    def test_even_the_real_embedder_stays_under_the_declared_ceiling(self):
        """Recorded because it is the honest headline: the blessed extra is a TIEBREAK, not a
        driver. It earns roughly twice hashing's weight and is still capped near a sixth of the
        declared ceiling."""
        self.assertLess(semantic_weight_for(Model2VecEmbedder()), SEMANTIC_WEIGHT)


@unittest.skipUnless(_M2V_LIVE, _M2V_REASON)
class TheSemanticTierEarnsItsPlaceOnARealEmbedder(unittest.TestCase):
    """End-to-end: the thing token-hashing structurally cannot do — rank a LEXICALLY DISJOINT but
    semantically near item above an unrelated one."""

    def _store(self, tmp, embedder):
        backend = SQLiteBackend(os.path.join(tmp, "sem.db"))
        rows = [
            ("near", "canine companion care",
             "looking after a puppy: feeding, walks and vet visits"),
            ("far", "quarterly tax filing",
             "submit the VAT return before the end of the accounting period"),
        ]
        for ident, subject, value in rows:
            backend.put(MemoryItem(id=ident, mtype=PERSISTENT, kind=CONTEXT, subject=subject,
                                   value=value,
                                   provenance={"created_at": "2026-01-04T03:03:00+00:00"}))
        return backend, MemoryStore(backend, embedder=embedder)

    def test_a_lexically_disjoint_but_semantically_near_item_outranks_an_unrelated_one(self):
        query = "how do I take care of my dog"          # shares NO content word with either row
        with tempfile.TemporaryDirectory() as d:
            backend, store = self._store(d, Model2VecEmbedder())
            hits = store.recall_relevant(query, top_k=2, stamp=False,
                                         degrade_out=lambda _m: None)
            backend.close()
        self.assertTrue(hits, "the semantic tier returned nothing on a real embedder")
        self.assertEqual("near", hits[0].item.id,
                         "the semantically near item did not rank first — the real embedder is "
                         "wired but is not ranking")
        self.assertGreater(hits[0].semantic, 0.0, "the semantic term contributed nothing")


if __name__ == "__main__":
    unittest.main()
