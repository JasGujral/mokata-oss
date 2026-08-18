"""THE CI FLOOR GAP — the PR gate must exercise the interpreter version the package DECLARES.

The finding this file guards, stated plainly: `pyproject.toml` promises `requires-python =
">=3.10"`, but until 0.0.16 the only job that ever ran 3.10 was `release.yml`. The declared floor
was therefore first exercised at TAG time — after review, after the work was called done, and at
the one moment when a red is most expensive. That is not a testing gap in the abstract; it is the
mechanism by which the ISO-dialect split shipped green. `datetime.fromisoformat` learned the Zulu
designator in 3.11, so the fault existed ONLY below the CI floor, and a 3.12-only gate cannot see
any bug of that shape by construction.

Fixing one such bug does not close the hole. Pinning the floor into the PR gate does, which is
what this file is for.

WHAT IS AND IS NOT CLAIMED — REWRITTEN AT 0.0.18 STAGE 17, AND THE OLD SENTENCE WAS THE POINT.
Until this stage the paragraph here read "one leg is not full matrix coverage of the floor, and
this file does not pretend otherwise". Honest, and it stayed true for two releases, which is how a
declared shortfall becomes furniture. Stage 17 closes the breadth `CI-MATRIX-FLOOR-GAP` names: the
floor now runs on the PR gate across BOTH operating systems and BOTH jsonschema dispositions.

⚠ AND ONE OF THE THREE FLOOR LEGS HAS NEVER RUN ANYWHERE. `windows-latest × floor` is configured
in no workflow before this stage — not in `ci.yml`, not in `release.yml` — so unlike every other
leg here it is a promise with no evidence behind it, and its first execution will be at the cut.
That is the same shape the row exists to end, so it is not left implied: `LEG_PROVENANCE` below
records the state of every leg in three values, never two (doc 85 §7g), and the two states that
are DERIVABLE are derived rather than declared.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import itertools
import os
import re
import unittest

import _support  # noqa: F401 - puts src/ on the path
from _workflow_pins import safe_load

_ROOT = os.path.join(os.path.dirname(__file__), "..")
PYPROJECT = os.path.join(_ROOT, "pyproject.toml")
CI_YML = os.path.join(_ROOT, ".github", "workflows", "ci.yml")
RELEASE_YML = os.path.join(_ROOT, ".github", "workflows", "release.yml")

# The floor's own version is never written down here — `FLOOR` is a placeholder substituted from
# `pyproject.toml` at read time, so raising the floor cannot leave a declaration pinning a version
# nobody supports. Hard-coding "3.10" into a table like this is the defect `test_pg_floor_drift.py`
# closed one file over, and it would be a strange thing to reintroduce in the file about floors.
FLOOR = "<floor>"

# ---- THE THREE STATES (§7g). "Has not run on the PR gate" is not one fact, it is three. --------
RUN_ON_THE_PR_GATE = "run on the PR gate"      # every PR has exercised it
RUN_AT_TAG_ONLY = "run at tag time only"       # it HAS run — first at a tag, which is the defect
NEVER_RUN = "never run anywhere"               # configured in no workflow before this stage

#: Every leg of `ci.yml`'s `test` job, keyed (os, python, jsonschema), with what is actually known
#: about it. Graded for EXACTNESS against the workflow in both directions below — a leg missing
#: here and a leg here that the workflow does not configure are different failures and both red.
LEG_PROVENANCE = {
    ("ubuntu-latest", "3.12", "absent"): RUN_ON_THE_PR_GATE,
    ("ubuntu-latest", "3.12", "present"): RUN_ON_THE_PR_GATE,
    ("windows-latest", "3.12", "absent"): RUN_ON_THE_PR_GATE,
    ("windows-latest", "3.12", "present"): RUN_ON_THE_PR_GATE,
    ("ubuntu-latest", FLOOR, "present"): RUN_ON_THE_PR_GATE,      # added 0.0.16, cf2d265
    ("ubuntu-latest", FLOOR, "absent"): RUN_AT_TAG_ONLY,          # release.yml has always had it
    ("windows-latest", FLOOR, "present"): NEVER_RUN,              # ★ added here, exercised nowhere
}

#: Legs a human has run on a developer machine, via `scripts/floor-python.sh`. Kept SEPARATE from
#: the states above rather than folded into them: a local run is evidence about the interpreter and
#: the dependency set, and none at all about the runner OS. The one rule that binds the two is
#: below — nothing may be claimed both NEVER_RUN and locally exercised.
EXERCISED_LOCALLY = frozenset({
    ("ubuntu-latest", FLOOR, "present"),
    ("ubuntu-latest", FLOOR, "absent"),
})

#: ★ THE FAILURE POLICY for the leg that has never run, decided at stage 17 and pinned by
#: `TheNeverRunLegBlocksLikeEveryOtherLeg` rather than left in prose.
#:
#: BLOCKING — no `continue-on-error`, from its first run. Two reasons, in order:
#:   1. `ci.yml` gates nothing in the publish path. `release.yml`'s `pypi` and `github-release`
#:      jobs need `build`, `build` needs `test` and `validate`, and all four are declared INSIDE
#:      `release.yml`. So a red Windows floor leg at the cut costs a red check on the mirror; it
#:      does not block the tag. That claim is derived below, not asserted.
#:   2. `continue-on-error` makes a red leg and a green leg produce the same workflow conclusion,
#:      which is exactly the two-states-for-three-facts collapse this release keeps finding. A leg
#:      that cannot fail is indistinguishable from a leg that never ran, wearing a green tick.
POLICY_BLOCKING = "blocking — no continue-on-error, from the leg's first run"


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _declared_floor() -> str:
    """The minimum Python `pyproject.toml` promises, e.g. '3.10'.

    Read from the manifest rather than hard-coded, so RAISING the floor cannot leave this guard
    silently pinning a version nobody supports any more.
    """
    match = re.search(r'requires-python\s*=\s*["\']>=\s*([0-9]+\.[0-9]+)', _read(PYPROJECT))
    assert match, "pyproject.toml declares no `requires-python = \">=X.Y\"` floor"
    return match.group(1)


def _python_legs(doc) -> list:
    """Every Python version the `test` job actually runs — the axis AND the `include` legs.

    Reading only `matrix.python` would miss the floor leg entirely (it is an `include`), which is
    exactly the kind of half-look that lets a guard pass while the thing it guards is absent.
    """
    matrix = doc["jobs"]["test"]["strategy"]["matrix"]
    legs = [str(v) for v in matrix.get("python", [])]
    for extra in matrix.get("include", []) or []:
        if "python" in extra:
            legs.append(str(extra["python"]))
    return legs


def _expand(job, keys=("os", "python", "jsonschema")) -> set:
    """Every leg a job runs, as (os, python, jsonschema) tuples, expanded the way GitHub expands.

    The axes are crossed and then each `include` entry is applied: an entry that would OVERWRITE
    an axis value on a combination becomes a NEW leg, and one that only adds keys merges into the
    combinations it matches. Both halves matter here — the floor legs are includes that overwrite
    `python`, and reading `include` as "extra legs" or as "extra keys" alone gets a different
    answer to how wide the gate is.
    """
    matrix = (job.get("strategy") or {}).get("matrix") or {}
    axes = {k: [str(v) for v in vals]
            for k, vals in matrix.items() if k not in ("include", "exclude")}
    combos = []
    if axes:
        names = sorted(axes)
        for values in itertools.product(*[axes[n] for n in names]):
            combos.append(dict(zip(names, values)))
    for entry in matrix.get("include") or []:
        entry = {k: str(v) for k, v in entry.items()}
        merged = False
        for combo in combos:
            if all(combo.get(k) == v for k, v in entry.items() if k in combo):
                combo.update(entry)
                merged = True
        if not merged:
            combos.append(dict(entry))
    # A single non-matrix runner still runs one leg; `runs-on` is its os.
    runner = str(job.get("runs-on", ""))
    return {tuple(c.get(k) or (runner if k == "os" and "${{" not in runner else None)
                  for k in keys)
            for c in (combos or [{}])}


def _resolved_provenance(floor):
    """`LEG_PROVENANCE` with the `FLOOR` placeholder replaced by the version pyproject declares."""
    return {tuple(floor if part == FLOOR else part for part in leg): state
            for leg, state in LEG_PROVENANCE.items()}


class TheDeclaredFloorRunsOnThePRGate(unittest.TestCase):
    def setUp(self) -> None:
        # A text `assertIn("3.10", ci_yml)` would match the EXPLANATORY COMMENT in that file and
        # pass with no such leg configured. A guard that can be satisfied by prose is worse than
        # no guard — and so is a SKIP, which reports OK for a floor nobody checked. `safe_load`
        # raises `MissingParser`: the parser is a test dependency, and its absence is a broken
        # environment, not a supported mode (PYYAML-SKIP-CLUSTER, 0.0.18 stage 2).
        self.doc = safe_load(_read(CI_YML), "assert the PR gate runs the declared Python floor")
        self.floor = _declared_floor()

    def test_a_the_floor_is_a_real_version_and_is_not_the_ci_default(self):
        # Non-degeneracy for every test below: if the floor stopped parsing, or the project
        # raised its floor to the version CI already ran, these assertions would pass for the
        # wrong reason. Fail loudly on that day instead.
        self.assertRegex(self.floor, r"^3\.\d+$")
        self.assertEqual(self.floor, "3.10",
                         "the declared floor moved — re-read this file's premise before editing it")

    def test_b_the_pr_gate_runs_the_declared_floor(self):
        legs = _python_legs(self.doc)
        self.assertIn(self.floor, legs,
                      f"ci.yml never runs py{self.floor}, the version pyproject.toml promises — "
                      f"the floor would first be exercised at tag time. Legs found: {legs}")

    def test_c_every_floor_leg_is_fully_specified(self):
        # An `include` leg that omits `jsonschema` leaves every `if: matrix.jsonschema == …` step
        # unmatched, so the leg would run a HOLLOW subset of the gate and still report green.
        matrix = self.doc["jobs"]["test"]["strategy"]["matrix"]
        floor_legs = [e for e in (matrix.get("include") or [])
                      if str(e.get("python")) == self.floor]
        self.assertTrue(floor_legs, "no floor leg at all")
        for leg in floor_legs:
            for key in ("os", "jsonschema"):
                self.assertIn(key, leg,
                              f"a floor leg pins no `{key}` — its `if:` steps would skip: {leg}")

    def test_c2_the_floor_covers_BOTH_operating_systems_and_BOTH_jsonschema_states(self):
        """★ THE BREADTH, and it is stated as a SET rather than as a count.

        `CI-MATRIX-FLOOR-GAP`'s two open axes were the floor on Windows and the floor with
        jsonschema absent. Asserting "three floor legs" would be satisfied by three copies of the
        cheap one; asserting the set is satisfied only by the coverage the row asked for.
        """
        floor_legs = {(o, j) for o, p, j in _expand(self.doc["jobs"]["test"])
                      if p == self.floor}
        self.assertEqual(
            {("ubuntu-latest", "present"),
             ("ubuntu-latest", "absent"),
             ("windows-latest", "present")},
            floor_legs,
            "the floor's coverage on the PR gate changed. `CI-MATRIX-FLOOR-GAP` is closed by "
            "these three legs and by no other set; dropping one reopens the axis it names.")

    def test_d_the_floor_did_not_REPLACE_the_primary_version(self):
        # The cheap wrong fix: swap 3.12 for 3.10 and call the floor covered. Both must run.
        legs = _python_legs(self.doc)
        self.assertIn("3.12", legs, "the primary Python leg was dropped")

    def test_e_the_floor_legs_are_INCLUDES_not_a_cross_product(self):
        # The gate is already slow. Adding the floor to the `python:` axis would cross-product
        # with os × jsonschema to 8 legs and roughly double it; three targeted `include` entries
        # buy the coverage for three. This pins the cheap shape against being "fixed" into the
        # expensive one — the constraint is the SHAPE, so it is asserted as the axis staying
        # single-valued rather than as a leg count that grows every time the gate widens.
        matrix = self.doc["jobs"]["test"]["strategy"]["matrix"]
        self.assertEqual([str(v) for v in matrix["python"]], ["3.12"],
                         "the floor belongs in `include`, not on the `python` axis")
        self.assertNotIn(self.floor, [str(v) for v in matrix["python"]])

    def test_f_windows_still_runs_the_primary_version_too(self):
        """The floor leg on Windows ADDS to the 3.12 Windows legs; it must not have replaced them.

        Same wrong fix as `test_d`, one axis over — and the one this stage could plausibly have
        made, since "Windows now runs the floor" reads like coverage either way.
        """
        legs = _expand(self.doc["jobs"]["test"])
        self.assertEqual({"absent", "present"},
                         {j for o, p, j in legs if o == "windows-latest" and p == "3.12"},
                         "Windows stopped running both jsonschema legs at 3.12")


class EveryLegSaysWhetherItHasEverRun(unittest.TestCase):
    """★ THE STAGE-17 DELIVERABLE THAT IS NOT A LEG: the provenance of each leg, in three states.

    Adding a leg nobody can run means its first execution is at the cut — which is the defect
    `CI-MATRIX-FLOOR-GAP` was filed to end, so a stage that adds one and does not SAY so has
    reproduced the row inside its own fix. `LEG_PROVENANCE` is the record. It is a declaration,
    because "has this ever executed" is a fact about history and no tree contains it — but the
    two states that CAN be checked against the tree are checked, in both directions, so the
    declaration cannot quietly go stale the way a comment would.
    """

    def setUp(self) -> None:
        self.ci = safe_load(_read(CI_YML), "grade the per-leg provenance record")
        self.release = safe_load(_read(RELEASE_YML), "grade the per-leg provenance record")
        self.floor = _declared_floor()
        self.declared = _resolved_provenance(self.floor)

    def test_a_the_record_names_exactly_the_legs_the_workflow_runs(self):
        """Exactness both ways, the treatment `_mutant_driver_contract.DECLARED_NONCONFORMING`
        gets. A leg the workflow runs and the record omits is an unaudited leg; a leg the record
        names and the workflow does not run is a claim about nothing. Different failures."""
        configured = _expand(self.ci["jobs"]["test"])
        self.assertEqual(set(self.declared), configured,
                         "the provenance record and ci.yml disagree about which legs exist.\n"
                         "  only in the record: %s\n  only in ci.yml   : %s"
                         % (sorted(set(self.declared) - configured),
                            sorted(configured - set(self.declared))))

    def test_b_the_three_states_are_three_distinct_values(self):
        """§7g's whole content. Two of these collapsing into one is the failure, not a typo."""
        self.assertEqual(3, len({RUN_ON_THE_PR_GATE, RUN_AT_TAG_ONLY, NEVER_RUN}))
        self.assertEqual({RUN_ON_THE_PR_GATE, RUN_AT_TAG_ONLY, NEVER_RUN},
                         set(self.declared.values()),
                         "a state went unused — either a leg is mis-declared, or the record has "
                         "quietly become a two-state one")

    def test_c_a_leg_declared_NEVER_RUN_is_not_covered_by_the_release_matrix(self):
        """★ THE EXPIRY, and it is derived. `release.yml` is the other place a leg could have run,
        so a `NEVER_RUN` claim is FALSIFIABLE against it: the day someone adds Windows to the
        release matrix, this leg has run, and the record here reds instead of ageing."""
        covered = _expand(self.release["jobs"]["test"])
        wrong = [leg for leg, state in self.declared.items()
                 if state == NEVER_RUN and leg in covered]
        self.assertEqual([], wrong,
                         "declared never-run, but release.yml runs it: %s" % wrong)

    def test_d_a_leg_declared_RUN_AT_TAG_ONLY_is_covered_by_the_release_matrix(self):
        """The other direction, and the one that catches the lazier error. `RUN_AT_TAG_ONLY` says
        "it has executed, at the worst possible moment" — if release.yml stops running it that
        sentence becomes false and the leg is really `NEVER_RUN`, a strictly worse fact."""
        covered = _expand(self.release["jobs"]["test"])
        wrong = [leg for leg, state in self.declared.items()
                 if state == RUN_AT_TAG_ONLY and leg not in covered]
        self.assertEqual([], wrong,
                         "declared run-at-tag, but release.yml does not run it: %s" % wrong)

    def test_e_nothing_is_both_never_run_and_exercised_locally(self):
        """The §7g companion. `scripts/floor-python.sh` makes "I ran the floor here" a claim
        anyone can re-run, which makes it tempting to let a local run stand in for a runner. It
        cannot: a local run says nothing whatever about the runner OS, and the one leg that has
        never executed is the one where that distinction is the entire finding."""
        local = {tuple(self.floor if part == FLOOR else part for part in leg)
                 for leg in EXERCISED_LOCALLY}
        self.assertEqual(set(), {leg for leg in local if self.declared.get(leg) == NEVER_RUN},
                         "a leg is claimed both never-run and locally exercised")
        self.assertEqual(set(), local - set(self.declared),
                         "a locally-exercised leg is not a leg ci.yml runs: %s"
                         % sorted(local - set(self.declared)))

    def test_f_the_never_run_leg_is_the_windows_floor_leg(self):
        """Named, so that a record which drifted into declaring everything safe reds. This is the
        one assertion here that would have to be EDITED the day the leg first goes green — which
        is the point: the flip is a decision a human makes and records, not a state that decays."""
        self.assertEqual([("windows-latest", self.floor, "present")],
                         [leg for leg, state in self.declared.items() if state == NEVER_RUN])


class TheNeverRunLegBlocksLikeEveryOtherLeg(unittest.TestCase):
    """★ THE FAILURE POLICY, pinned rather than documented. See `POLICY_BLOCKING` above.

    The alternative on the table was `continue-on-error: true` until the leg is once-green. It was
    refused: it buys nothing (`ci.yml` does not gate the tag — asserted below) and it costs the
    distinction between a leg that passed and a leg that failed, which is the distinction the
    whole record above exists to keep.
    """

    def setUp(self) -> None:
        self.ci = safe_load(_read(CI_YML), "assert the floor legs block")
        self.release = safe_load(_read(RELEASE_YML), "assert ci.yml does not gate the tag")

    def test_a_no_leg_of_the_test_job_may_swallow_its_own_failure(self):
        job = self.ci["jobs"]["test"]
        self.assertNotIn("continue-on-error", job,
                         "the whole test job was made non-blocking — %s" % POLICY_BLOCKING)
        for entry in (job["strategy"]["matrix"].get("include") or []):
            self.assertNotIn("continue-on-error", entry,
                             "a matrix leg was made non-blocking: %s. %s" % (entry, POLICY_BLOCKING))

    def test_b_no_step_of_the_test_job_may_swallow_its_own_failure(self):
        """The same escape one level down, and the easier one to add by accident: a single
        `continue-on-error: true` on the unit-suite step turns every leg into a green tick."""
        offenders = [s.get("name", s.get("run", "?")) for s in self.ci["jobs"]["test"]["steps"]
                     if s.get("continue-on-error")]
        self.assertEqual([], offenders, "these steps swallow their failure: %s" % offenders)

    def test_c_the_publish_path_does_not_depend_on_ci_yml(self):
        """★ THE DERIVATION THE POLICY RESTS ON. "A red Windows floor leg does not block the tag"
        is only true while every job in the publish chain is declared in `release.yml` itself. If
        `ci.yml` is ever wired into it — a `workflow_run` trigger, a `needs` naming it — the cost
        of a blocking never-run leg changes completely and this policy has to be re-decided.
        """
        jobs = self.release["jobs"]
        chain, seen = ["pypi", "github-release"], set()
        while chain:
            name = chain.pop()
            if name in seen:
                continue
            seen.add(name)
            self.assertIn(name, jobs,
                          "the publish chain reaches `%s`, which release.yml does not declare — "
                          "the tag now depends on something outside this workflow" % name)
            chain.extend(jobs[name].get("needs") or [])
        self.assertIn("test", seen, "release.yml's publish chain no longer runs its own matrix")

        on = self.release.get("on", self.release.get(True))
        self.assertNotIn("workflow_run", on if isinstance(on, dict) else {on: None},
                         "release.yml now triggers off another workflow's run")


class TheReleaseGateStillCoversTheFloor(unittest.TestCase):
    """The floor leg on the PR gate ADDS to release.yml; it must not have moved coverage."""

    def test_a_release_still_runs_the_floor(self):
        doc = safe_load(_read(RELEASE_YML), "assert release.yml still covers the declared floor")
        floor = _declared_floor()
        versions = [str(v) for v in doc["jobs"]["test"]["strategy"]["matrix"]["python"]]
        self.assertIn(floor, versions,
                      "release.yml stopped running the declared floor")
        self.assertGreater(len(versions), 1, "the release matrix collapsed to one version")


if __name__ == "__main__":
    unittest.main()
