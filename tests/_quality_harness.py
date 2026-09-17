"""DB.S8d — the retrieval-QUALITY harness: four arms, one corpus, measured against ground truth.

Every earlier DB.S8 leg measured COST (rows, milliseconds, plans). This one measures whether the
answers are right, which is the claim doc 86's exit criteria actually names: "recall/briefing
measurably better on the 100k fixture (before/after)". "Measurably" needs three things this module
supplies — a right answer, a metric, and a stored baseline to compare against.

## THE FOUR ARMS

Each arm is the SAME corpus read with one more tier switched on, so a difference between two
adjacent arms is attributable to the tier that changed and to nothing else:

  A  jaccard    — the zero-dependency floor: Python token overlap over the active set
  B  fts        — SQLite FTS5 `MATCH` + `bm25()`, ranked in the database
  C  vector     — B plus the semantic tier (embeddings stamped at generation)
  D  expansion  — C plus the bounded <=2-hop typed-edge walk

## THE METRICS

`recall@k` — of the ground truth this probe planted, how much came back in the top k.
`mrr@k`    — reciprocal rank of the FIRST relevant hit; it is what catches an arm that returns the
             right answers in a worse ORDER, which recall alone cannot see.

Ground truth per probe is two items (`_scale_fixture.Probe.relevant`): a DIRECT one carrying a
token unique in the whole corpus, and a HOP one drawn from a vocabulary no query ever names,
reachable only across a `depends_on` edge. So arms A-C can reach at most half the ground truth by
construction, and arm D's gain is a measurement of TRAVERSAL rather than of vocabulary.

## THE CANDIDATE PATH IS THE SECOND VARIABLE — and until 0.0.20 this note got it WRONG

Arms C and D wire an embedder against a backend with no vector index, and `tiered._can_nominate`
deliberately refuses candidate selection in exactly that configuration (R-1: a Python-side semantic
tier can only re-score rows something else already selected, so nominating lexically would redefine
semantic recall as "re-rank the lexical hits"). **That refusal is CORRECT and is not the defect.**

⚠ **What this note said until 2026-08-25 was: "Arm C therefore reads the full active set where arms
B and D do not." Both halves are false about D.** `run_arm` gives an embedder to `ARM_VECTOR` AND
`ARM_EXPANSION`, so **arm D reads the full active set too** — and calling it "a COST difference, not
a quality one" is the sentence that let `ARM-D-CONFOUND-BACK` be filed as a one-variable delta.

**Measured 2026-08-25 at N=100,000: arm D takes 1090.5s and `B+expansion` takes 3.9s — 280x.** That
is not a scoring tier's price; that is a different read. So a step of the cumulative ladder from B
to C flips TWO things at once — the semantic tier turns on AND the bounded nomination turns off —
and `D - (B+expansion)` is therefore *not* "the vector tier's contribution" at any N.

**THE FIX IS A THIRD CONTROL ARM, NOT A NEW READING OF THE OLD DELTA.** `B+expansion(full)` is
FTS + expansion with the semantic tier OFF and candidate selection forced off, so:

    D - B+expansion(full)          the semantic tier's contribution, candidate PATH held constant
    B+expansion(full) - B+expansion  the candidate PATH's contribution, TIER held constant
    (their sum is the old confounded delta, which is why the old delta could never be either)

⛔ **No production code is touched to get this.** `_can_nominate`'s third condition is an argued
product decision with a real premise (doc 85 §7h: a pin that encodes a true premise is not loosened
to make a measurement convenient). The arm forces the wide read the way the harness already forces
the Jaccard floor — by flipping a backend capability flag for the duration of one arm, and
restoring it in `finally`.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

import _support  # noqa: F401

import _scale_fixture as F

from mokata.memory import expansion as X
from mokata.memory import tiered
from mokata.memory.store import MemoryStore

ARM_JACCARD = "A:jaccard"
ARM_FTS = "B:fts"
ARM_VECTOR = "C:+vector"
ARM_EXPANSION = "D:+expansion"
#: THE ISOLATION ARM — B plus expansion, with the semantic tier OFF. Not one of the four the
#: ladder is built from; it exists because the cumulative ladder turned out to CONFOUND two
#: independent effects, and without it "expansion is worth +0.0pp" would have been the reported
#: conclusion — arm C gave back exactly what arm D added.
#:
#: DB.S8f bounded the semantic tier and arm D landed ON this arm at N=5,000 (0.833 both), which is
#: the identity `test_the_ladder_now_lands_on_the_isolation_arm` asserts. The arm is KEPT rather
#: than retired — it is the control that detects the confound coming back, and a control you delete
#: once it reads clean is not a control. ⭐ **It did its job: at N=100,000 the arms diverge
#: (D 0.7639 < iso 0.7778) and `ARM-D-CONFOUND-BACK` was filed off exactly this reading.**
#:
#: ⚠ **BUT THIS ARM ALONE CANNOT ATTRIBUTE THE DIVERGENCE, and 0.0.20 stage 01 is that correction.**
#: It differs from arm D in TWO ways, not one: no embedder AND (consequently) the bounded candidate
#: read. See `ARM_FTS_EXPANSION_FULL` and the module docstring.
ARM_FTS_EXPANSION = "B+expansion"
#: THE PATH CONTROL — B plus expansion, semantic tier OFF, candidate selection FORCED OFF so this
#: arm reads the full active set exactly as arms C and D do. Its only difference from arm D is the
#: semantic tier, which is what makes `D - B+expansion(full)` the tier's contribution rather than
#: the tier's contribution plus a different retrieval architecture.
#:
#: ⛔ It is NOT part of the ladder and must never be added to `ARMS`: the tier-monotonicity check
#: walks adjacent pairs of `ARMS`, and a control that deliberately holds a variable fixed is not a
#: rung. Added 0.0.20 stage 01 (`ARM-D-CONFOUND-BACK`).
ARM_FTS_EXPANSION_FULL = "B+expansion(full)"
#: Ordered weakest → strongest. The tier-monotonicity check walks adjacent pairs of THIS tuple, so
#: the order is the claim rather than an incidental listing.
ARMS: Tuple[str, ...] = (ARM_JACCARD, ARM_FTS, ARM_VECTOR, ARM_EXPANSION)
#: Everything the harness can measure, ladder plus isolation.
ALL_ARMS: Tuple[str, ...] = ARMS + (ARM_FTS_EXPANSION, ARM_FTS_EXPANSION_FULL)


@dataclass(frozen=True)
class Knobs:
    """The DB.S7b ranking knobs, all four marked PROVISIONAL at their definitions and tuned here.

    Carried as a value rather than read from the module so a sweep can evaluate a setting without
    leaving it installed — `apply()` is a context manager that always restores.
    """

    edge_weight: float = X.EDGE_WEIGHT
    depth_decay: float = X.DEPTH_DECAY
    seed_cap: int = X.SEED_CAP
    #: How many ranked rows the lexical tier over-fetches. Not a DB.S7b knob, but it is the OTHER
    #: half of the SEED_CAP finding (see `seed_starvation`) and cannot be swept without it.
    over_fetch: int = tiered.CANDIDATE_OVER_FETCH

    def label(self) -> str:
        return (f"edge={self.edge_weight} decay={self.depth_decay} "
                f"seeds={self.seed_cap} fetch={self.over_fetch}")


@dataclass(frozen=True)
class ArmResult:
    arm: str
    recall_at_k: float
    mrr_at_k: float
    probes: int
    k: int

    def row(self) -> str:
        return (f"{self.arm:<14} recall@{self.k}={self.recall_at_k:.3f}  "
                f"mrr@{self.k}={self.mrr_at_k:.3f}  ({self.probes} probes)")


class _install:
    """Install `Knobs` for the duration of a block, then restore — unconditionally.

    The knobs are module-level constants read at call time by `expansion` and `tiered`, so a sweep
    sets them, measures, and puts them back. `finally` rather than best-effort: a sweep that left a
    knob installed would silently change every later test in the process, and the failure would
    surface somewhere unrelated.
    """

    def __init__(self, knobs: Knobs):
        self.knobs = knobs

    def __enter__(self):
        self._saved = (X.EDGE_WEIGHT, X.DEPTH_DECAY, X.SEED_CAP, tiered.CANDIDATE_OVER_FETCH,
                       tiered.EDGE_WEIGHT)
        X.EDGE_WEIGHT = self.knobs.edge_weight
        X.DEPTH_DECAY = self.knobs.depth_decay
        X.SEED_CAP = self.knobs.seed_cap
        tiered.CANDIDATE_OVER_FETCH = self.knobs.over_fetch
        # `tiered` imports EDGE_WEIGHT by VALUE at module load (`from .expansion import
        # EDGE_WEIGHT`), so setting it on `expansion` alone leaves the fusion using the old number.
        # Both, or the sweep measures a knob it did not turn.
        tiered.EDGE_WEIGHT = self.knobs.edge_weight
        return self

    def __exit__(self, *exc):
        (X.EDGE_WEIGHT, X.DEPTH_DECAY, X.SEED_CAP, tiered.CANDIDATE_OVER_FETCH,
         tiered.EDGE_WEIGHT) = self._saved
        return False


def _embedder():
    from mokata.memory.embed import HashingEmbedder
    return HashingEmbedder()


def run_arm(corpus: F.Corpus, backend: Any, arm: str, *, k: int = 10,
            probes: Optional[Sequence[F.Probe]] = None,
            _force_embedder: bool = False) -> ArmResult:
    """Measure ONE arm over `probes`. The arm decides which tiers are live; nothing else differs.

    `_force_embedder` is a TEST SEAM and nothing else uses it: it wires an embedder onto an arm
    whose name says it has none, which is the only configuration that separates "wide because the
    semantic tier cannot nominate" from "wide for some other reason". Stage 01's mutants M4 and M5
    both survived until this seam existed.
    """
    probes = list(probes if probes is not None else corpus.probes)
    saved_fts = backend._fts
    backend._fts = (arm != ARM_JACCARD)          # arm A is the Jaccard floor
    embedder = _embedder() if (_force_embedder or arm in (ARM_VECTOR, ARM_EXPANSION)) else None
    # THE PATH CONTROL. `_can_nominate` refuses candidate selection when an embedder is wired
    # against a backend with no `semantic_search` — so arms C and D read the whole active set and
    # arms B / B+expansion read a bounded 50. That is production behaving correctly, and it is also
    # the ladder's SECOND variable. `B+expansion(full)` takes the wide read with the semantic tier
    # OFF, by shadowing the class attribute `_can_nominate` probes first. An INSTANCE attribute, so
    # `delattr` restores the class's answer exactly rather than re-asserting a value we typed.
    forced_wide = (arm == ARM_FTS_EXPANSION_FULL)
    if forced_wide:
        backend.supports_candidate_selection = False
    recalls: List[float] = []
    reciprocals: List[float] = []
    try:
        for probe in probes:
            store = MemoryStore(backend, scope_context=corpus.context_for(probe),
                                embedder=embedder)
            expander = (store._edge_expander()
                        if arm in (ARM_EXPANSION, ARM_FTS_EXPANSION, ARM_FTS_EXPANSION_FULL)
                        else None)
            hits = tiered.tiered_recall(store, probe.query, embedder=embedder, top_k=k,
                                        expander=expander, degrade_out=lambda _m: None)
            ranked = [h.item.id for h in hits]
            relevant = set(probe.relevant)
            found = [i for i in ranked if i in relevant]
            recalls.append(len(found) / len(relevant))
            first = next((n for n, i in enumerate(ranked, 1) if i in relevant), None)
            reciprocals.append(1.0 / first if first else 0.0)
    finally:
        backend._fts = saved_fts
        if forced_wide:
            # `delattr`, never `= True`: the instance attribute is REMOVED so the class's own
            # answer is restored. Re-assigning would hard-code the value this harness believes the
            # backend advertises, and a backend that stopped advertising it would go unnoticed.
            backend.__dict__.pop("supports_candidate_selection", None)
    n = len(probes) or 1
    return ArmResult(arm=arm, recall_at_k=sum(recalls) / n, mrr_at_k=sum(reciprocals) / n,
                     probes=len(probes), k=k)


#: THE DECLARED CANDIDATE PATH OF EVERY ARM — `True` = the bounded R-1 nomination, `False` = the
#: full active-set scan. The claim the ladder rests on, written down once so it can be GRADED
#: instead of assumed. ⭐ **This table is what `ARM-D-CONFOUND-BACK` needed and did not have:** the
#: confound survived four months because nothing anywhere stated which arm read what, so "one more
#: tier switched on" could stop being true without a single test changing colour.
DECLARED_CANDIDATE_PATH: Dict[str, bool] = {
    ARM_JACCARD: False,              # the Jaccard floor cannot nominate at all
    ARM_FTS: True,
    ARM_VECTOR: False,
    ARM_EXPANSION: False,            # THE CELL THE OLD DOCSTRING DENIED
    ARM_FTS_EXPANSION: True,         # bounded — which is exactly why it cannot isolate arm D
    ARM_FTS_EXPANSION_FULL: False,   # forced wide, so it differs from arm D by the TIER alone
}

#: WHICH arms are wide **because of `_can_nominate`'s third condition** — the semantic one. Kept
#: separate from the table above rather than folded into a tri-state, because "wide" and "wide for
#: the semantic reason" are different claims and doc 85 §7g is about exactly this: three refusal
#: reasons must not share one representation. Arm A is wide because the lexical tier is on the
#: floor; `B+expansion(full)` is wide because this harness forced it; **C and D are wide because
#: production decided so, and THAT is the fact the ladder's attribution depends on.**
DECLARED_SEMANTIC_REFUSAL: Dict[str, bool] = {
    ARM_JACCARD: False, ARM_FTS: False, ARM_VECTOR: True, ARM_EXPANSION: True,
    ARM_FTS_EXPANSION: False, ARM_FTS_EXPANSION_FULL: False,
}

#: WHICH arms wire an embedder at all. ⭐ **THIS TABLE EXISTS BECAUSE A MUTANT SURVIVED WITHOUT IT.**
#: Stage 01's mutant M2 gave `B+expansion(full)` an embedder and every path assertion still passed:
#: the arm forces `supports_candidate_selection` off, so `_can_nominate` refuses at condition 1
#: before the semantic condition is ever reached, and both the path table and the semantic-refusal
#: table read exactly as declared while the arm had quietly become a second arm D. **A control that
#: can acquire the variable it controls for, without reddening, is not a control** — so the wiring
#: is graded directly rather than inferred from the path it produces.
DECLARED_EMBEDDER_WIRED: Dict[str, bool] = {
    ARM_JACCARD: False, ARM_FTS: False, ARM_VECTOR: True, ARM_EXPANSION: True,
    ARM_FTS_EXPANSION: False, ARM_FTS_EXPANSION_FULL: False,
}


@dataclass(frozen=True)
class PathObservation:
    """What one arm actually did, derived from a real run. Nothing here is typed."""
    arm: str
    bounded: bool
    #: True when the refusal came from `_can_nominate`'s SEMANTIC condition — established
    #: DIFFERENTIALLY (re-ask the real predicate with the embedder removed; if it then says yes,
    #: the embedder was the refuser) so no condition of the production function is retyped here.
    refused_for_semantic: bool
    #: Whether this arm handed `tiered_recall` an embedder at all — read off the argument the
    #: production predicate actually received, not from the arm's name.
    embedder_wired: bool


def observe_candidate_paths(corpus: F.Corpus, backend: Any, *,
                            arms: Sequence[str] = ALL_ARMS,
                            probes: Optional[Sequence[F.Probe]] = None,
                            _force_embedder: bool = False,
                            ) -> Dict[str, PathObservation]:
    """DERIVE, per arm, whether the read was bounded — by watching `tiered._can_nominate` answer
    during a real run of that arm. The returned table is what the code DID, not what we believe."""
    out: Dict[str, PathObservation] = {}
    real_can = tiered._can_nominate
    one = list(probes if probes is not None else corpus.probes)[:1]

    for arm in arms:
        answers: List[bool] = []
        semantic_refusals: List[bool] = []
        embedders: List[bool] = []

        def spy(store, embedder, semantic, _real=real_can):
            ans = _real(store, embedder, semantic)
            answers.append(bool(ans))
            embedders.append(embedder is not None)
            semantic_refusals.append(
                bool(not ans and embedder is not None and _real(store, None, semantic)))
            return ans

        tiered._can_nominate = spy
        try:
            run_arm(corpus, backend, arm, probes=one, _force_embedder=_force_embedder)
        finally:
            tiered._can_nominate = real_can
        out[arm] = PathObservation(arm=arm,
                                   bounded=bool(answers) and any(answers),
                                   refused_for_semantic=any(semantic_refusals),
                                   embedder_wired=any(embedders))
    return out


def run_all_arms(corpus: F.Corpus, backend: Any, *, k: int = 10,
                 knobs: Optional[Knobs] = None,
                 arms: Sequence[str] = ALL_ARMS) -> Dict[str, ArmResult]:
    with _install(knobs or Knobs()):
        return {arm: run_arm(corpus, backend, arm, k=k) for arm in arms}


# ---------------------------------------------------------------- the K1 bound, RE-DERIVED
def k1_bound_violations(knobs: Optional[Knobs] = None) -> List[str]:
    """Every (kind, hops) whose expansion contribution can reach or exceed a full lexical match.

    THE BOUND IS THE CONTRACT, NOT THE CONSTANT — so this RE-DERIVES it from the live constants
    rather than asserting `EDGE_WEIGHT == 0.30`. A test that pinned the number would go red when
    someone tuned a knob (which is allowed) and stay green when someone tuned it past the bound
    (which is not). This goes red on exactly the second case.

    The inequality, from `expansion.path_weight` (`prod(kind weights) * DEPTH_DECAY ** depth`) and
    the fusion's `EDGE_WEIGHT * path.weight`:

        EDGE_WEIGHT * (kind weight ** hops) * (DEPTH_DECAY ** hops)  <  LEXICAL_WEIGHT

    at 1 and 2 hops, over EVERY declared kind — the three wired ones AND the five that share
    `UNWIRED_DEFAULT_WEIGHT`. The unwired five matter most: they have no producer today, so the day
    one lands its edges start contributing at a weight nobody re-checked, and this is the check.
    The worst case per kind is the SAME kind crossed twice (the strongest available product), which
    is what raising the kind weight to `hops` expresses.
    """
    knobs = knobs or Knobs()
    out: List[str] = []
    for kind in X.EDGE_KINDS:
        weight = X.kind_weight(kind)
        for hops in (1, 2):
            contribution = knobs.edge_weight * (weight ** hops) * (knobs.depth_decay ** hops)
            if contribution >= tiered.LEXICAL_WEIGHT:
                out.append(
                    f"{kind} at {hops} hop(s): {contribution:.4f} >= LEXICAL_WEIGHT "
                    f"{tiered.LEXICAL_WEIGHT} — a hop can DISPLACE a full direct match")
    return out


# ---------------------------------------------------------------- K2/K3/K4, THE SAME DISCIPLINE
# DB.S8f — K1 above was the only tier carrying a derived bound. These are the rest, in the same
# shape: an inequality RE-DERIVED from the live constants, so each goes red when someone tunes past
# it and stays green when they tune within it. The principle they all serve is stated once, in
# `tiered.py` — no non-matching signal, alone or in combination, may outrank a real match.

def max_edge_contribution(knobs: Optional[Knobs] = None) -> float:
    """The most the expansion tier can add to an item that matches NOTHING — K1's left-hand side,
    maximized over every declared kind and both hop counts, so K4 can sum it with the others.

    Derived from the same live constants K1 re-derives from; never the 0.15 it happens to equal at
    the shipped defaults."""
    knobs = knobs or Knobs()
    return max(knobs.edge_weight * (X.kind_weight(kind) ** hops) * (knobs.depth_decay ** hops)
               for kind in X.EDGE_KINDS for hops in (1, 2))


def k2_bound_violations(embedder: Any = None) -> List[str]:
    """K2 — SEMANTIC: a spurious cosine must not displace a lexical match.

        semantic_weight_for(e) * noise_floor_of(e)  <=  SEMANTIC_NOISE_BUDGET  <  LEXICAL_WEIGHT

    Checked against the LIVE embedder rather than against `SEMANTIC_WEIGHT`, because the number that
    matters is what an unrelated item actually collects, and that is the weight times the embedder's
    own floor. Asserting `SEMANTIC_WEIGHT == 1.0` would have been green throughout the entire period
    the tier was burying the answers.
    """
    from mokata.memory.embed import noise_floor_of
    if embedder is None:
        embedder = _embedder()
    floor = noise_floor_of(embedder)
    collected = tiered.semantic_weight_for(embedder) * floor
    out: List[str] = []
    if collected > tiered.SEMANTIC_NOISE_BUDGET + 1e-12:
        out.append(
            f"semantic: an item matching NOTHING collects {collected:.4f} "
            f"(weight {tiered.semantic_weight_for(embedder):.4f} x noise floor {floor:.4f}), past "
            f"its SEMANTIC_NOISE_BUDGET {tiered.SEMANTIC_NOISE_BUDGET}")
    if collected >= tiered.LEXICAL_WEIGHT:
        out.append(
            f"semantic: {collected:.4f} >= LEXICAL_WEIGHT {tiered.LEXICAL_WEIGHT} — spurious "
            "cosine can DISPLACE a full direct match")
    return out


def k3_bound_violations() -> List[str]:
    """K3 — RECENCY + USAGE: the bound is on the SUM, not on each term.

    Both terms saturate at 1.0 (`lifecycle.recency_score` clamps future timestamps, `usage_score` is
    `hits/(hits+k)`), so a non-matching, heavily-recalled, just-recalled item collects exactly
    `RECENCY_WEIGHT + USAGE_WEIGHT`. Bounding each below `LEXICAL_WEIGHT` and letting them add to it
    is the hole this exists to close — and it was not theoretical: the pair summed to 0.25 against a
    `LEXICAL_WEIGHT` of 0.25.
    """
    total = tiered.RECENCY_WEIGHT + tiered.USAGE_WEIGHT
    out: List[str] = []
    if total > tiered.QUALITY_BUDGET + 1e-12:
        out.append(f"quality terms: RECENCY {tiered.RECENCY_WEIGHT} + USAGE {tiered.USAGE_WEIGHT} "
                   f"= {total:.4f}, past QUALITY_BUDGET {tiered.QUALITY_BUDGET}")
    if total >= tiered.LEXICAL_WEIGHT:
        out.append(f"quality terms: {total:.4f} >= LEXICAL_WEIGHT {tiered.LEXICAL_WEIGHT} — a "
                   "non-matching, heavily-recalled item can rank at or above a full direct match")
    return out


def k4_bound_violations(knobs: Optional[Knobs] = None, embedder: Any = None) -> List[str]:
    """K4 — THE COMBINATION, which is the principle's actual claim: "alone OR IN COMBINATION".

        semantic noise + (recency + usage) + max edge contribution  <  LEXICAL_WEIGHT

    K1, K2 and K3 each bound one signal. An item can collect ALL of them at once and match nothing:
    reached across a `depends_on` hop, recalled often, and sitting on the embedder's cosine pedestal.
    Three individually-satisfied bounds do not make a satisfied sum, so the sum is its own check.

    The graph tier is absent from the left-hand side deliberately: it is a MATCH tier whose contract
    is 0.0 for an item the query does not reach (see `tiered.GraphScorer`), and it is off unless a
    host wires a scorer. Counting a signal that should be zero as if it were 1.0 would spend the
    whole budget on a tier that is not there.
    """
    from mokata.memory.embed import noise_floor_of
    if embedder is None:
        embedder = _embedder()
    semantic = tiered.semantic_weight_for(embedder) * noise_floor_of(embedder)
    quality = tiered.RECENCY_WEIGHT + tiered.USAGE_WEIGHT
    edge = max_edge_contribution(knobs)
    total = semantic + quality + edge
    if total >= tiered.NON_MATCH_BUDGET:
        return [f"COMBINATION: semantic {semantic:.4f} + quality {quality:.4f} + edge {edge:.4f} "
                f"= {total:.4f} >= NON_MATCH_BUDGET {tiered.NON_MATCH_BUDGET} — an item matching "
                "NOTHING can outrank a full direct match"]
    return []


def all_bound_violations(knobs: Optional[Knobs] = None, embedder: Any = None) -> List[str]:
    """Every bound, in one call. The four together ARE the principle."""
    return (k1_bound_violations(knobs) + k2_bound_violations(embedder)
            + k3_bound_violations() + k4_bound_violations(knobs, embedder))


# ---------------------------------------------------------------- the SEED_CAP finding
def seed_starvation(knobs: Optional[Knobs] = None) -> Tuple[int, int, float]:
    """`(seed_cap, over_fetch, fraction of nominated rows that never become seeds)`.

    THE FINDING, as a number rather than as a remark. The lexical tier nominates `over_fetch` (50)
    ranked rows; `expansion.select_seeds` then takes the top `SEED_CAP` (10) of the ones that
    scored. So 40 of 50 nominated rows — 80% — are ranked, paid for, and then never expanded from.
    `_expansion_tier` does report the drop (`seeds_dropped` → the `memory-expansion-bounds`
    notice), so it is bounded-and-said-so rather than silent; what it is NOT is TUNED, and the two
    numbers were chosen in different stages against different fixtures for unrelated reasons.

    This is a first-class candidate in the sweep below rather than a footnote.
    """
    knobs = knobs or Knobs()
    dropped = max(knobs.over_fetch - knobs.seed_cap, 0)
    return knobs.seed_cap, knobs.over_fetch, dropped / (knobs.over_fetch or 1)


# ---------------------------------------------------------------- the sweep
def sweep(corpus: F.Corpus, backend: Any, candidates: Sequence[Knobs], *,
          k: int = 10, probes: Optional[Sequence[F.Probe]] = None,
          arm: str = ARM_FTS_EXPANSION) -> List[Tuple[Knobs, ArmResult, List[str]]]:
    """Measure `arm` under each candidate setting. Returns `(knobs, result, bound_violations)`.

    IT SWEEPS THE ISOLATION ARM, NOT ARM D, and that default is a measurement rather than a
    preference. Swept on arm D the grid is FLAT — every setting scores 0.500, including settings
    that break the K1 bound — because arm C's semantic tier contributes ~0.75 to unrelated items
    and the whole expansion term (at most 0.15) moves nothing through it. A sweep that reported
    "no knob makes any difference" would have been the conclusion, and it would have been an
    artefact of the tier above the one being tuned. On `B+expansion` the knobs are observable.

    Violations are RETURNED, never filtered out: a setting that scores better by breaking a bound
    is exactly the setting a sweep would otherwise recommend, and the point of carrying the bound
    beside the score is that "it scored higher" cannot be the last word.

    DB.S8f — checks ALL FOUR bounds, not just K1. `EDGE_WEIGHT=0.45` is in the grid below and it
    RESPECTS K1 (0.45 x 1.0 x 0.5 = 0.225 < 0.25) while busting the combination bound K4 once the
    semantic and quality shares are summed with it. A sweep that only knew about K1 would have
    recommended it on a tie.
    """
    out = []
    for knobs in candidates:
        with _install(knobs):
            result = run_arm(corpus, backend, arm, k=k, probes=probes)
            out.append((knobs, result, all_bound_violations(knobs)))
    return out


def default_sweep_grid() -> List[Knobs]:
    """The grid. Deliberately small and deliberately including the SEED_CAP finding's own axis."""
    base = Knobs()
    grid = [base]
    for cap in (5, 25, 50):                                  # the SEED_CAP finding, swept
        grid.append(replace(base, seed_cap=cap))
    for fetch in (25, 100):                                  # …and its other half
        grid.append(replace(base, over_fetch=fetch))
    for weight in (0.15, 0.45, 0.60):                        # EDGE_WEIGHT — 0.60 breaks the bound
        grid.append(replace(base, edge_weight=weight))
    for decay in (0.25, 0.75):
        grid.append(replace(base, depth_decay=decay))
    return grid


def render(results: Dict[str, ArmResult]) -> str:
    return "\n".join(results[arm].row() for arm in ALL_ARMS if arm in results)
