"""THE CI PYTHON AXIS — every workflow leg runs Python 3.12, and 3.12 IS the declared floor.

0.0.24 (owner ruling 2026-10-17): the PR gate (`ci.yml`) and the tag-time gate (`release.yml`)
run Python 3.12 ONLY; the 3.10 "floor" legs were removed for the Windows wall-clock, which left
`requires-python = ">=3.10"` a promise no gate tested (the ISO-dialect split and the uv receipt
both shipped as 3.10-only bugs). 0.0.26 L12 (PY-FLOOR-3.12, Jas 2026-10-22) closed that the other
way: the floor was RAISED to 3.12, so the gate Python and the floor are the same version and every
PR tests the floor. This file pins that they stay equal — raising one without the other reopens
the gap. The weekly `.github/workflows/python-floor.yml` (graded by
`tests/test_c_02_python_floor_leg.py`) still reads the floor out of pyproject and runs it.

Kept from the earlier file: per-leg provenance, graded against the workflow in both directions,
and the failure policy — every leg BLOCKS; nothing swallows its own failure.

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
#: about it. Graded for EXACTNESS against the workflow in both directions below.
LEG_PROVENANCE = {
    ("ubuntu-latest", "3.12", "absent"): RUN_ON_THE_PR_GATE,
    ("ubuntu-latest", "3.12", "present"): RUN_ON_THE_PR_GATE,
    ("windows-latest", "3.12", "absent"): RUN_ON_THE_PR_GATE,
    ("windows-latest", "3.12", "present"): RUN_ON_THE_PR_GATE,
    # 0.0.22 B1 added it NEVER_RUN; flipped at 0.0.24 with the evidence — the mirror's 0.0.23
    # release PR #81 ran it green (CI run 38898749296).
    ("macos-latest", "3.12", "present"): RUN_ON_THE_PR_GATE,
}

#: The one Python every workflow leg runs.
GATE_PYTHON = "3.12"

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


class EveryLegRunsOnePython(unittest.TestCase):
    def setUp(self) -> None:
        # Parsed, never text-matched: a comment naming "3.10" must not satisfy or break this.
        self.ci = safe_load(_read(CI_YML), "assert the PR gate runs one Python")
        self.release = safe_load(_read(RELEASE_YML), "assert the tag gate runs one Python")
        self.floor = _declared_floor()

    def test_a_the_pr_gate_runs_only_the_gate_python(self):
        self.assertEqual({GATE_PYTHON}, set(_python_legs(self.ci)),
                         "ci.yml runs a Python other than %s" % GATE_PYTHON)
        self.assertEqual({GATE_PYTHON}, {p for _o, p, _j in _expand(self.ci["jobs"]["test"])})

    def test_b_the_tag_gate_runs_only_the_gate_python(self):
        versions = [str(v) for v in self.release["jobs"]["test"]["strategy"]["matrix"]["python"]]
        self.assertEqual([GATE_PYTHON], versions)

    def test_c_the_declared_floor_IS_the_gate_python_and_every_gate_runs_it(self):
        """★ PY-FLOOR-3.12. A gate on a Python above the floor would leave the floor untested again;
        a floor above the gate would promise users a Python no gate runs."""
        self.assertEqual(GATE_PYTHON, self.floor,
                         "requires-python and the gate Python parted — the floor is untested again")
        everywhere = ({p for _o, p, _j in _expand(self.ci["jobs"]["test"])}
                      | {str(v) for v in self.release["jobs"]["test"]["strategy"]["matrix"]["python"]})
        self.assertEqual({self.floor}, everywhere)

    def test_d_both_operating_systems_still_run_both_jsonschema_states(self):
        legs = _expand(self.ci["jobs"]["test"])
        for os_name in ("ubuntu-latest", "windows-latest"):
            self.assertEqual({"absent", "present"},
                             {j for o, p, j in legs if o == os_name and p == GATE_PYTHON},
                             "%s stopped running both jsonschema legs" % os_name)


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
        """§7g's content: the three facts stay three values, even when one is currently unused."""
        self.assertEqual(3, len({RUN_ON_THE_PR_GATE, RUN_AT_TAG_ONLY, NEVER_RUN}))
        self.assertTrue(set(self.declared.values()) <= {RUN_ON_THE_PR_GATE, RUN_AT_TAG_ONLY,
                                                        NEVER_RUN})

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

    def test_f_no_leg_is_currently_never_run(self):
        """Named, so a NEW leg added without run evidence has to be declared NEVER_RUN on purpose.
        (The macOS leg was the last one; PR #81 ran it green at 0.0.23.)"""
        self.assertEqual([], [leg for leg, state in self.declared.items() if state == NEVER_RUN])


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


if __name__ == "__main__":
    unittest.main()
