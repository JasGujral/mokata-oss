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

WHAT IS AND IS NOT CLAIMED. One leg (ubuntu × floor × jsonschema=present) is not full matrix
coverage of the floor, and this file does not pretend otherwise — it pins that the floor is
exercised BEFORE tag, not that every axis is crossed with it. The broader treatment is tracked as
CI-MATRIX-FLOOR-GAP in doc 84.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import re
import unittest

import _support  # noqa: F401 - puts src/ on the path

_ROOT = os.path.join(os.path.dirname(__file__), "..")
PYPROJECT = os.path.join(_ROOT, "pyproject.toml")
CI_YML = os.path.join(_ROOT, ".github", "workflows", "ci.yml")
RELEASE_YML = os.path.join(_ROOT, ".github", "workflows", "release.yml")


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


def _yaml():
    try:
        import yaml
    except ImportError:                                  # pragma: no cover - env-dependent
        return None
    return yaml


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


class TheDeclaredFloorRunsOnThePRGate(unittest.TestCase):
    def setUp(self) -> None:
        self.yaml = _yaml()
        if self.yaml is None:
            # A text `assertIn("3.10", ci_yml)` would match the EXPLANATORY COMMENT in that file
            # and pass with no such leg configured. A guard that can be satisfied by prose is
            # worse than no guard, so this skips honestly instead.
            self.skipTest("PyYAML not installed — structural matrix assertions need a real parse")
        self.doc = self.yaml.safe_load(_read(CI_YML))
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

    def test_c_the_floor_leg_is_fully_specified(self):
        # An `include` leg that omits `jsonschema` leaves every `if: matrix.jsonschema == …` step
        # unmatched, so the leg would run a HOLLOW subset of the gate and still report green.
        matrix = self.doc["jobs"]["test"]["strategy"]["matrix"]
        floor_legs = [e for e in (matrix.get("include") or [])
                      if str(e.get("python")) == self.floor]
        self.assertEqual(len(floor_legs), 1, "expected exactly one floor leg")
        leg = floor_legs[0]
        for key in ("os", "jsonschema"):
            self.assertIn(key, leg, f"the floor leg pins no `{key}` — its `if:` steps would skip")
        self.assertEqual(leg["jsonschema"], "present")
        self.assertEqual(leg["os"], "ubuntu-latest", "the floor leg belongs on the cheap runner")

    def test_d_the_floor_did_not_REPLACE_the_primary_version(self):
        # The cheap wrong fix: swap 3.12 for 3.10 and call the floor covered. Both must run.
        legs = _python_legs(self.doc)
        self.assertIn("3.12", legs, "the primary Python leg was dropped")

    def test_e_the_floor_was_added_as_ONE_leg_not_a_cross_product(self):
        # The gate is already ~45 min. Adding "3.10" to the `python:` axis would cross-product
        # with os × jsonschema to 8 legs and roughly double it. This pins the cheap
        # shape so a later edit cannot quietly double the gate.
        matrix = self.doc["jobs"]["test"]["strategy"]["matrix"]
        self.assertEqual([str(v) for v in matrix["python"]], ["3.12"],
                         "the floor belongs in `include`, not on the `python` axis")
        self.assertEqual(len(matrix.get("include") or []), 1)


class TheReleaseGateStillCoversTheFloor(unittest.TestCase):
    """The floor leg on the PR gate ADDS to release.yml; it must not have moved coverage."""

    def test_a_release_still_runs_the_floor(self):
        yaml = _yaml()
        if yaml is None:
            self.skipTest("PyYAML not installed")
        doc = yaml.safe_load(_read(RELEASE_YML))
        floor = _declared_floor()
        versions = [str(v) for v in doc["jobs"]["test"]["strategy"]["matrix"]["python"]]
        self.assertIn(floor, versions,
                      "release.yml stopped running the declared floor")
        self.assertGreater(len(versions), 1, "the release matrix collapsed to one version")


if __name__ == "__main__":
    unittest.main()
