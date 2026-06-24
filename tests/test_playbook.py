"""Stage 9 — the full v1 integration playbook: a real story driven end-to-end through
the actual pipeline (brainstorm -> completeness gate -> tests -> RED-before-GREEN ->
review), across profiles and both execution modes, reusing every prior stage."""

import io
import tempfile
import unittest
from contextlib import redirect_stdout

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.bootstrap import estimate_tokens
from mokata.cli import main
from mokata.config import Surface
from mokata.execmode import PARALLEL, SEQUENTIAL, ExecutionChoice, TaskResult
from mokata.init import init_repo
from mokata.playbook import run_playbook


def silent(_):
    pass


class InlineRunner:
    """Stand-in subagent runner (the harness fulfils this in real use)."""

    def run(self, task):
        out = f"impl:{task.id}"
        return TaskResult(task.id, True, f"done {task.id}", output=out,
                          input_tokens=estimate_tokens(task.context + task.description),
                          output_tokens=estimate_tokens(out), seen_context=task.context)


def init(d, profile):
    init_repo(root=d, profile=profile, assume_yes=True, out=silent)
    return Surface.load(d)


class TestPlaybookAcrossProfiles(unittest.TestCase):
    def test_passes_on_each_profile_sequential(self):
        for profile in ("minimal", "standard", "full"):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as d:
                result = run_playbook(init(d, profile), ExecutionChoice(SEQUENTIAL))
                self.assertTrue(result.ok, f"{profile}: {result.checks}")
                # the engine invariants hold on every profile
                self.assertTrue(result.checks["gate_blocked_initially"])
                self.assertTrue(result.checks["gate_passed_after_tests"])
                self.assertTrue(result.checks["red_before_green"])
                self.assertTrue(result.checks["review_passed"])

    def test_memory_active_on_standard_off_on_minimal(self):
        with tempfile.TemporaryDirectory() as d:
            r = run_playbook(init(d, "standard"), ExecutionChoice(SEQUENTIAL))
            self.assertTrue(r.checks["memory_enabled"])
            self.assertTrue(r.checks["memory_written"])
        with tempfile.TemporaryDirectory() as d:
            r = run_playbook(init(d, "minimal"), ExecutionChoice(SEQUENTIAL))
            self.assertFalse(r.checks["memory_enabled"])
            self.assertTrue(r.ok)             # still passes — memory not required on minimal


class TestPlaybookBothModes(unittest.TestCase):
    def test_passes_sequential_and_parallel(self):
        for choice in (ExecutionChoice(SEQUENTIAL),
                       ExecutionChoice(PARALLEL, isolation=True, fanout=True)):
            with self.subTest(mode=choice.mode), tempfile.TemporaryDirectory() as d:
                result = run_playbook(init(d, "full"), choice, runner=InlineRunner())
                self.assertTrue(result.ok)
                self.assertEqual(result.exec_mode, choice.mode)
                self.assertFalse(result.degraded)

    def test_parallel_without_runner_degrades_but_still_passes(self):
        with tempfile.TemporaryDirectory() as d:
            result = run_playbook(init(d, "full"),
                                  ExecutionChoice(PARALLEL, isolation=True))
            self.assertTrue(result.degraded)   # no harness -> sequential fallback
            self.assertTrue(result.ok)


class TestCompletenessGateInPlaybook(unittest.TestCase):
    def test_emit_is_blocked_until_acs_map_to_tests(self):
        with tempfile.TemporaryDirectory() as d:
            r = run_playbook(init(d, "standard"), ExecutionChoice(SEQUENTIAL))
            # the gate blocked with no tests, then passed once ACs mapped
            self.assertTrue(r.checks["gate_blocked_initially"])
            self.assertTrue(r.checks["gate_passed_after_tests"])
            self.assertTrue(r.checks["approach_in_gate"])   # read the brainstorm handoff


class TestPlaybookCLI(unittest.TestCase):
    def test_clean_install_then_playbook_runs(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=silent)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["playbook", "--path", d])
            out = buf.getvalue()
            self.assertEqual(rc, 0)
            self.assertIn("RESULT: PASS", out)


if __name__ == "__main__":
    unittest.main()
