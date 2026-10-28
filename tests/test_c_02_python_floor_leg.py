"""0.0.25 Lane C — the declared Python floor is exercised: a WEEKLY leg, not a PR gate (U2). Since
0.0.26 L12 the floor is 3.12 (PY-FLOOR-3.12), the gate Python too; this leg still reads it from pyproject.

What must be true for a user installing mokata on the oldest Python it claims to support:

  * some workflow runs the FULL unit suite on exactly the floor `pyproject.toml` declares (read
    from the manifest, never typed) — so `requires-python` is a tested promise again;
  * it runs weekly and on demand, never per PR (the owner's 0.0.24 ruling removed the floor from
    the PR gate for its Windows wall-clock; U2 keeps it off the gate);
  * the leg cannot read GREEN while testing nothing: test tooling is hash-installed, the MCP gates
    must run, and the job is bounded and re-derives its own ceiling;
  * the G4 harness-cert server half runs there too (it had only ever been run by hand).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import _support  # noqa: F401

import os
import re
import unittest

from _workflow_pins import safe_load

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLOOR_YML = os.path.join(ROOT, ".github", "workflows", "python-floor.yml")
PYPROJECT = os.path.join(ROOT, "pyproject.toml")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _declared_floor():
    m = re.search(r'requires-python\s*=\s*["\']>=\s*([0-9]+\.[0-9]+)', _read(PYPROJECT))
    assert m, "pyproject.toml declares no requires-python floor"
    return m.group(1)


class TheFloorIsTestedWeekly(unittest.TestCase):
    def setUp(self):
        self.doc = safe_load(_read(FLOOR_YML), "grade the weekly Python-floor leg")
        self.job = self.doc["jobs"]["floor"]
        self.steps = self.job["steps"]
        self.runs = "\n".join(str(s.get("run", "")) for s in self.steps)

    def test_the_floor_is_still_the_one_declared(self):
        self.assertEqual("3.12", _declared_floor(),
                         "the floor moved — re-read U2 / L12 (PY-FLOOR-3.12) before editing")

    def test_the_leg_runs_on_exactly_the_declared_floor(self):
        pythons = [str(s["with"]["python-version"]) for s in self.steps
                   if "setup-python" in str(s.get("uses", ""))]
        self.assertEqual([_declared_floor()], pythons)
        self.assertIn("requires-python", self.runs,
                      "the preflight must READ the floor from pyproject.toml, not trust the YAML")

    def test_it_is_weekly_and_manual_never_a_pr_gate(self):
        on = self.doc.get("on", self.doc.get(True))
        self.assertEqual({"schedule", "workflow_dispatch"}, set(on))
        self.assertTrue(on["schedule"][0]["cron"].strip())

    def test_it_runs_the_full_unit_suite(self):
        # the whole suite: not a sub-directory, not a `-p` pattern, not a `-k` filter
        self.assertRegex(self.runs,
                         r"python -m unittest discover -s tests -t tests(?![\w/])(?!\s+-[pk])")

    def test_it_cannot_read_green_while_testing_nothing(self):
        self.assertIn("pip install --require-hashes -r requirements/ci.txt", self.runs)
        self.assertIn("pip install --require-hashes -r requirements/jsonschema.txt", self.runs)
        suite = next(s for s in self.steps if "unittest discover" in str(s.get("run", "")))
        self.assertEqual("1", str(suite.get("env", {}).get("MOKATA_REQUIRE_MCP_SDK")))
        self.assertFalse(any(s.get("continue-on-error") for s in self.steps))
        self.assertNotIn("continue-on-error", self.job)

    def test_the_tooling_it_installs_was_compiled_for_the_floor(self):
        for req in ("ci.txt", "jsonschema.txt"):
            head = _read(os.path.join(ROOT, "requirements", req))[:400]
            self.assertIn("--python-version %s" % _declared_floor(), head, req)

    def test_it_is_bounded_and_re_derives_its_own_ceiling(self):
        self.assertIsInstance(self.job.get("timeout-minutes"), int)
        self.assertLess(self.job["timeout-minutes"], 360)
        self.assertIn("scripts/ci_ceiling.py", self.runs)
        self.assertIn("--workflow .github/workflows/python-floor.yml --job floor", self.runs)

    def test_the_g4_server_half_runs_on_the_floor(self):
        self.assertIn("python scripts/harness_cert.py contract all", self.runs)
        self.assertNotIn("harness_cert.py record", self.runs,
                         "CI must never write the owner's real-client matrix")


if __name__ == "__main__":
    unittest.main()
