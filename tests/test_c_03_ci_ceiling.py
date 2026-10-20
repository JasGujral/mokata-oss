"""0.0.25 Lane C — INTERNAL-ONLY-TESTS-RUN-ON-NO-RUNNER, the CEILING half: a job ceiling is
re-derived from what the public mirror's runner measured, on every run, before a tag meets it.

What must be true (for the owner cutting a release, and so for every user waiting on it):

  * a suite that has grown to within 20% of a job ceiling REDS with the 1.5x value to set —
    while it is a one-line edit, not after the tag's test job is cancelled (v0.0.23 was lost so);
  * a ceiling that still clears says so; "could not measure" is never reported as "clears";
  * the PR gate's ubuntu legs measure release.yml's `test` ceiling, because they run a superset
    of that job on the same image — the tag gate's ceiling is re-derived on every PR;
  * the reader takes the JOB-level ceiling, never a step's and never a comment's.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import _support  # noqa: F401

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _workflow_pins import safe_load, workflow_files

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOWS = os.path.join(ROOT, ".github", "workflows")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import ci_ceiling as CC  # noqa: E402


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = CC.main(list(argv))
    return code, out.getvalue(), err.getvalue()


PLANTED = """\
name: planted
jobs:
  # timeout-minutes: 999  <- a comment, not a key
  build:
    runs-on: ubuntu-latest
    steps:
      - name: slow step
        timeout-minutes: 5
        run: echo hi
    timeout-minutes: 12   # derived somewhere
  nothing:
    runs-on: ubuntu-latest
    steps:
      - run: echo
other:
  build:
    timeout-minutes: 77
"""


class TheReaderTakesTheJobsOwnCeiling(unittest.TestCase):
    def test_job_level_not_step_level_not_comment(self):
        self.assertEqual(12, CC.job_ceiling(PLANTED, "build"))

    def test_a_job_without_a_ceiling_or_absent_is_none(self):
        self.assertIsNone(CC.job_ceiling(PLANTED, "nothing"))
        self.assertIsNone(CC.job_ceiling(PLANTED, "ghost"))

    def test_the_reader_agrees_with_a_real_yaml_parse_for_every_job_in_the_tree(self):
        checked = 0
        for name in workflow_files(WORKFLOWS):
            text = _read(os.path.join(WORKFLOWS, name))
            for job_id, job in (safe_load(text, "grade the ceiling reader")["jobs"]).items():
                with self.subTest(workflow=name, job=job_id):
                    self.assertEqual(job.get("timeout-minutes"), CC.job_ceiling(text, job_id))
                    checked += 1
        self.assertGreater(checked, 15, "the corpus is not the real one")


class TheVerdictIsDerivedNotGuessed(unittest.TestCase):
    def test_the_measured_v0_0_23_leg_clears_the_re_derived_32(self):
        """1240.6s is the slowest completed release `test` leg (run 38966278058)."""
        self.assertEqual(32, CC.derive_minutes(1240.6))
        self.assertEqual(CC.CLEARS, CC.verdict(1240.6, 32))

    def test_the_old_22_minute_ceiling_would_have_redded_before_the_cut(self):
        self.assertEqual(CC.TOO_TIGHT, CC.verdict(1240.6, 22))

    def test_alarm_sits_between_the_rule_and_the_cancellation(self):
        self.assertLess(1.0, CC.ALARM_FACTOR)
        self.assertLess(CC.ALARM_FACTOR, CC.DERIVE_FACTOR)
        self.assertEqual(1.5, CC.DERIVE_FACTOR)


class TheCommandReportsThreeFacts(unittest.TestCase):
    def setUp(self):
        fd, self.wf = tempfile.mkstemp(suffix=".yml")
        os.close(fd)
        self.addCleanup(os.unlink, self.wf)
        with open(self.wf, "w", encoding="utf-8") as fh:
            fh.write(PLANTED)

    def test_clears(self):
        code, out, _ = _run("--since", "1000", "--now", "1300", "--workflow", self.wf,
                            "--job", "build")
        self.assertEqual(CC.CLEARS, code)
        self.assertIn("CLEARS", out)

    def test_too_tight_names_the_value_to_set(self):
        code, out, _ = _run("--since", "0", "--now", "700", "--workflow", self.wf, "--job", "build")
        self.assertEqual(CC.TOO_TIGHT, code)
        self.assertIn("timeout-minutes: %d" % CC.derive_minutes(700), out)
        self.assertIn("::error", out)

    def test_cannot_answer_is_never_clears(self):
        for argv in (("--since", "", "--workflow", self.wf, "--job", "build"),
                     ("--since", "9999999999", "--now", "1", "--workflow", self.wf,
                      "--job", "build"),
                     ("--since", "0", "--now", "10", "--workflow", self.wf, "--job", "nothing"),
                     ("--since", "0", "--now", "10", "--workflow", self.wf + ".missing",
                      "--job", "build")):
            with self.subTest(argv=argv):
                code, out, err = _run(*argv)
                self.assertEqual(CC.CANNOT_ANSWER, code)
                self.assertNotIn("CLEARS", out)
                self.assertIn("cannot answer", err)


class TheGatesMeasureThemselves(unittest.TestCase):
    def setUp(self):
        self.ci = safe_load(_read(os.path.join(WORKFLOWS, "ci.yml")), "grade the CI-CEILING steps")
        self.steps = self.ci["jobs"]["test"]["steps"]

    def _runs(self, needle):
        return [s for s in self.steps if needle in str(s.get("run", ""))]

    def test_the_clock_starts_before_the_install_and_is_read_last(self):
        names = [str(s.get("run", "")) + str(s.get("uses", "")) for s in self.steps]
        clock = next(i for i, n in enumerate(names) if "MOKATA_CLOCK_T0=" in n)
        install = next(i for i, n in enumerate(names) if "pip install -e ." in n)
        checks = [i for i, n in enumerate(names) if "scripts/ci_ceiling.py" in n]
        self.assertLess(clock, install)
        self.assertEqual(len(names) - len(checks), min(checks), "the checks must be the last steps")

    def test_the_pr_gate_re_derives_the_tag_gates_ceiling_on_linux(self):
        tag = self._runs("--workflow .github/workflows/release.yml --job test")
        self.assertEqual(1, len(tag))
        self.assertEqual("runner.os == 'Linux'", tag[0].get("if"))
        release = safe_load(_read(os.path.join(WORKFLOWS, "release.yml")), "grade the tag gate")
        self.assertEqual("ubuntu-latest", release["jobs"]["test"]["runs-on"],
                         "the superset argument needs the SAME runner image")

    def test_every_leg_checks_its_own_ceiling(self):
        own = self._runs("--workflow .github/workflows/ci.yml --job test")
        self.assertEqual(1, len(own))
        self.assertNotIn("if", own[0])


if __name__ == "__main__":
    unittest.main()
