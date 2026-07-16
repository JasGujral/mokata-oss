"""R-13F · B3-rider — a SIMULATED batch must never report estimate-of-placeholder as actual spend,
and the golden-path playbook must report a simulated exec as `simulated`, never a green review.

Prior stage B3 labeled every un-run task `simulated: true`. The rider closes the accounting hole it
left open: `_simulated_result` still fed `estimate_tokens(<placeholder ctx/output>)` into the
`TokenTracker`, and `RunResult.actual_*` reported that fiction as spend; and `run_playbook` ran the
default two-stage review over the simulated results (which passes on `ok and output`), reporting a
GREEN `review_passed` for work nothing executed.

These tests FAIL on pre-rider `src/`.
"""

import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.config import Surface
from mokata.execmode import SEQUENTIAL, ExecutionChoice, Task, TaskResult, run_tasks
from mokata.init import init_repo
from mokata.playbook import run_playbook


class _RealRunner:
    """A runner that actually 'runs' each task, reporting KNOWN real token spend."""

    def run(self, task):
        out = f"impl:{task.id}"
        return TaskResult(task.id, True, f"done {task.id}", output=out,
                          input_tokens=5, output_tokens=3, seen_context=task.context)


class TestSimulatedSpend(unittest.TestCase):
    def _tasks(self):
        return [Task("t1", "do t1", context="ctx-one"),
                Task("t2", "do t2", context="ctx-two")]

    def test_b3_rider_simulated_reports_zero_actual_spend(self):
        """No runner ⇒ every task simulated ⇒ ZERO actual spend (never estimate-of-placeholder)."""
        res = run_tasks(self._tasks(), ExecutionChoice(SEQUENTIAL), runner=None, budget=100_000)
        self.assertTrue(res.simulated, "a no-runner batch is simulated")
        self.assertEqual(res.actual_input, 0)
        self.assertEqual(res.actual_output, 0)
        self.assertEqual(res.actual_total, 0)
        self.assertEqual(res.actual_cost, 0.0)
        self.assertTrue(res.within_budget, "a simulated run spent nothing, so it is within any budget")

    def test_b3_rider_real_run_spend_is_byte_identical(self):
        """The negative: a REAL runner's spend accounting is unchanged by the rider."""
        res = run_tasks(self._tasks(), ExecutionChoice(SEQUENTIAL), runner=_RealRunner(),
                        budget=100_000)
        self.assertFalse(res.simulated)
        self.assertEqual(res.actual_input, 10)   # 5 tokens x 2 tasks
        self.assertEqual(res.actual_output, 6)   # 3 tokens x 2 tasks
        self.assertEqual(res.actual_total, 16)


class TestPlaybookReview(unittest.TestCase):
    def test_b3_rider_playbook_reports_simulated_not_green_review(self):
        """No harness runner ⇒ the exec step is simulated ⇒ review_passed is the honest marker
        'simulated', NEVER True — yet the pipeline smoke still passes (a simulated exec is honest,
        not a failure)."""
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=lambda *_a: None)
            result = run_playbook(Surface.load(d), ExecutionChoice(SEQUENTIAL))
            self.assertIsNot(result.checks["review_passed"], True,
                             "a simulated exec must not report a green review")
            self.assertEqual(result.checks["review_passed"], "simulated")
            self.assertTrue(result.ok, f"honest simulated run still passes: {result.checks}")


if __name__ == "__main__":
    unittest.main()
