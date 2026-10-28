"""SD.S1 (0.0.26 L7) — the spec-drift check runs in CI and fails on drift.

Exit criterion 5 of the 0.0.26 plan: "spec-drift check runs in CI". `mokata docsync` (no target)
is that check, and ci.yml's `spec-drift` job runs it:

  * the DOC leg (DK.S5 docsync, unchanged): every public doc is cross-checked against the code —
    commands, slash-command forms, skill counts, the install path; a Blocking finding exits 1;
  * the SPEC leg (new, SD.S1): the repo's SAVED spec is checked against the live tree — an AC
    anchor that no longer resolves (file gone / symbol gone) and, in a repo that tags tests with AC
    ids, an AC whose test is gone, are Blocking. No saved spec is reported as SKIPPED with the
    reason, never as a clean check.

Before this stage the doc leg had never run in CI, and the shipped tree had drifted: v0.0.25 carried
6 Blocking findings and checkpoint 1 carried 10. `test_SD_S1_the_tree_has_no_drift` holds the line.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)
from _plan import (call_planned, commit_planned, emit_planned,  # noqa: F401,E402  (R2)
                   planned, sealed, with_tasks)
from _workflow_pins import safe_load

from mokata import docsync
from mokata.config import Surface
from mokata.engine.spec import AcceptanceCriterion, CodeAnchor, Spec
from mokata.engine.spec_gate import SPEC_STATE_KEY

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")


def _mokata(*args, cwd=ROOT):
    env = dict(os.environ, PYTHONPATH=SRC + os.pathsep + os.environ.get("PYTHONPATH", ""))
    return subprocess.run([sys.executable, "-m", "mokata", *args], cwd=cwd, env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          stdin=subprocess.DEVNULL, timeout=300)


def _init(d):
    from mokata.init import init_repo
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _write(d, rel, text):
    p = os.path.join(d, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _save(surface, criteria):
    """Through emit's real durable write: this session's `emitted_spec` + the shared corpus (which
    a later process — the CLI, CI — reads; the emitted spec is session-scoped)."""
    from mokata.engine.emit import spec_commit
    commit_planned(surface.state, Spec(title="Payments", criteria=criteria))


class TestTheTree(unittest.TestCase):
    def test_SD_S1_the_tree_has_no_drift(self):
        # Exactly what ci.yml's `spec-drift` job runs, against the shipped tree.
        p = _mokata("docsync")
        self.assertEqual(p.returncode, 0, p.stdout[-4000:] + p.stderr[-2000:])
        self.assertNotIn("[Blocking]", p.stdout)
        # The spec leg reports itself even when it has nothing to check — never a silent pass.
        self.assertIn("docsync spec: skipped", p.stdout)


class TestDocDriftRedsTheJob(unittest.TestCase):
    def test_SD_S1_a_doc_naming_a_removed_command_exits_1(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "README.md", "# demo\n\nRun `mokata frobnicate` to start.\n")
            p = _mokata("docsync", "--path", d)
            self.assertEqual(p.returncode, 1, p.stdout)
            self.assertIn("mokata frobnicate", p.stdout)
            # control: the same doc naming a real command is clean
            _write(d, "README.md", "# demo\n\nRun `mokata status` to start.\n")
            self.assertEqual(_mokata("docsync", "--path", d).returncode, 0)


class TestSpecDrift(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = self._tmp.name
        self.surface = _init(self.d)
        _write(self.d, "pay.py", "def charge(cart):\n    return sum(cart)\n")

    def tearDown(self):
        self._tmp.cleanup()

    def test_SD_S1_uninitialised_repo_is_skipped_with_the_reason(self):
        with tempfile.TemporaryDirectory() as bare:
            sd = docsync.spec_drift(bare)
        self.assertEqual(sd.status, docsync.SPEC_SKIPPED)
        self.assertIn("skipped", docsync.render_spec_drift(sd))
        self.assertNotIn("OK", docsync.render_spec_drift(sd))

    def test_SD_S1_no_saved_spec_is_skipped(self):
        self.assertEqual(docsync.spec_drift(self.d).status, docsync.SPEC_SKIPPED)

    def test_SD_S1_spec_matching_the_code_is_clean(self):
        _save(self.surface, [AcceptanceCriterion(
            id="AC-1", text="charge sums the cart", anchors=[CodeAnchor("pay.py", "charge")])])
        sd = docsync.spec_drift(self.d)
        self.assertEqual(sd.status, docsync.SPEC_CHECKED)
        self.assertEqual(sd.findings, [])
        self.assertIn("OK", docsync.render_spec_drift(sd))

    def test_SD_S1_a_renamed_symbol_is_blocking_drift_and_reds_the_cli(self):
        _save(self.surface, [AcceptanceCriterion(
            id="AC-1", text="charge sums the cart", anchors=[CodeAnchor("pay.py", "charge")])])
        self.assertEqual(_mokata("docsync", "--path", self.d).returncode, 0)
        _write(self.d, "pay.py", "def bill(cart):\n    return sum(cart)\n")   # the drift
        sd = docsync.spec_drift(self.d)
        self.assertEqual([f.checker for f in sd.findings], ["spec-anchor"])
        self.assertEqual(sd.findings[0].severity, docsync.BLOCKING)
        self.assertIn("charge", sd.findings[0].message)
        p = _mokata("docsync", "--path", self.d)
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("spec-anchor", p.stdout)

    def test_SD_S1_a_deleted_file_is_blocking_drift(self):
        _save(self.surface, [AcceptanceCriterion(
            id="AC-1", text="refunds", anchors=[CodeAnchor("refund.py")])])
        self.assertEqual([f.checker for f in docsync.spec_drift(self.d).findings], ["spec-anchor"])

    def test_SD_S1_an_ac_whose_test_is_gone_is_blocking_drift(self):
        _save(self.surface, [AcceptanceCriterion(id="AC-1", text="charge sums the cart"),
                             AcceptanceCriterion(id="AC-2", text="charge rejects negatives")])
        _write(self.d, "tests/test_pay.py",
               "def test_sum():  # AC-1\n    assert True\n\n"
               "def test_negative():  # AC-2\n    assert True\n")
        self.assertEqual(docsync.spec_drift(self.d).findings, [])
        _write(self.d, "tests/test_pay.py", "def test_sum():  # AC-1\n    assert True\n")
        sd = docsync.spec_drift(self.d)
        self.assertEqual([(f.checker, f.section) for f in sd.findings], [("spec-ac-test", "AC-2")])

    def test_SD_S1_a_repo_without_ac_tagged_tests_is_not_judged_on_them(self):
        _save(self.surface, [AcceptanceCriterion(id="AC-1", text="charge sums the cart")])
        _write(self.d, "tests/test_pay.py", "def test_sum():\n    assert True\n")
        self.assertEqual(docsync.spec_drift(self.d).findings, [])

    def test_SD_S1_a_malformed_spec_is_blocking_not_skipped(self):
        self.surface.state.write(SPEC_STATE_KEY, {"title": "x", "criteria": "not-a-list"})
        sd = docsync.spec_drift(self.d)
        self.assertEqual(sd.status, docsync.SPEC_MALFORMED)
        self.assertTrue(docsync.has_blocking(sd.findings))


class TestTheCIJob(unittest.TestCase):
    def test_SD_S1_ci_runs_the_drift_check_on_every_push(self):
        with open(os.path.join(ROOT, ".github", "workflows", "ci.yml"), encoding="utf-8") as fh:
            wf = safe_load(fh.read())
        self.assertIn("pull_request", wf[True] if True in wf else wf["on"])
        job = wf["jobs"]["spec-drift"]
        runs = [s.get("run", "") for s in job["steps"]]
        self.assertIn("python -m mokata docsync", runs)
        self.assertLess(job["timeout-minutes"], 360)
        self.assertEqual(job["permissions"], {"contents": "read"})
        self.assertNotIn("--reconcile", json.dumps(job))     # CI audits; it never writes docs


if __name__ == "__main__":
    unittest.main()
