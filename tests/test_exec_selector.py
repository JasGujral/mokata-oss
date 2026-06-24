"""E8 — execution-mode selector: ask on every run; default to sequential when no choice
is made; honor parallel + isolation/fan-out sub-choices."""

import os
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.execmode import PARALLEL, SEQUENTIAL, ExecutionChoice, select_execution_mode
from mokata.govern import AuditLedger


class TestSelector(unittest.TestCase):
    def test_default_is_sequential_when_no_choice(self):
        choice = select_execution_mode()          # non-interactive -> default
        self.assertEqual(choice.mode, SEQUENTIAL)
        self.assertFalse(choice.is_parallel)

    def test_blank_answer_defaults_to_sequential(self):
        choice = select_execution_mode(ask=lambda q, d: "")
        self.assertEqual(choice.mode, SEQUENTIAL)

    def test_parallel_with_isolation_only(self):
        answers = iter(["parallel", "y", "n"])
        choice = select_execution_mode(ask=lambda q, d: next(answers))
        self.assertEqual(choice.mode, PARALLEL)
        self.assertTrue(choice.isolation)
        self.assertFalse(choice.fanout)

    def test_parallel_with_fanout_only(self):
        answers = iter(["parallel", "n", "y"])
        choice = select_execution_mode(ask=lambda q, d: next(answers))
        self.assertTrue(choice.is_parallel)
        self.assertFalse(choice.isolation)
        self.assertTrue(choice.fanout)

    def test_parallel_with_neither_defaults_to_isolation(self):
        answers = iter(["parallel", "n", "n"])
        choice = select_execution_mode(ask=lambda q, d: next(answers))
        self.assertTrue(choice.isolation)         # parallel implies at least isolation

    def test_selector_prompts_on_every_run(self):
        calls = []
        select_execution_mode(ask=lambda q, d: calls.append(q) or "")
        self.assertGreaterEqual(len(calls), 1)    # it asked

    def test_choice_logged_to_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            select_execution_mode(ask=lambda q, dft: "sequential", ledger=led)
            kinds = [e["kind"] for e in led.entries()]
            self.assertIn("exec_mode", kinds)


if __name__ == "__main__":
    unittest.main()
