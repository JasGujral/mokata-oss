"""E2/E8 orchestration: fresh-subagent isolation, concurrent fan-out, estimate-vs-actual
under budget, ledger logging of every subagent decision, and degrade-to-sequential."""

import os
import tempfile
import threading
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.bootstrap import estimate_tokens
from mokata.execmode import (
    PARALLEL,
    SEQUENTIAL,
    ExecutionChoice,
    SubagentUnavailable,
    Task,
    TaskResult,
    run_tasks,
)
from mokata.govern import AuditLedger


class FakeRunner:
    def __init__(self, barrier=None, fail=False):
        self.seen = {}
        self.threads = set()
        self.barrier = barrier
        self.fail = fail

    def run(self, task):
        if self.fail:
            raise SubagentUnavailable("no subagent harness")
        if self.barrier is not None:
            self.barrier.wait(timeout=5)          # proves true concurrency or times out
        self.threads.add(threading.get_ident())
        self.seen[task.id] = task.context
        out = f"done:{task.id}"
        return TaskResult(task_id=task.id, ok=True, summary=f"ok {task.id}", output=out,
                          input_tokens=estimate_tokens(task.context + task.description),
                          output_tokens=estimate_tokens(out), seen_context=task.context)


def tasks3():
    return [Task("a", "build a", context="CONTEXT-A"),
            Task("b", "build b", context="CONTEXT-B"),
            Task("c", "build c", context="CONTEXT-C")]


class TestFreshSubagentIsolation(unittest.TestCase):
    def test_each_task_sees_only_its_own_context(self):
        runner = FakeRunner()
        choice = ExecutionChoice(PARALLEL, isolation=True, fanout=False)
        result = run_tasks(tasks3(), choice, runner=runner)
        self.assertEqual(runner.seen["b"], "CONTEXT-B")
        self.assertNotIn("CONTEXT-A", runner.seen["b"])   # no leakage across tasks
        # two-stage review ran for every isolated task
        self.assertTrue(all(r.review and r.review.passed for r in result.results))


class TestFanoutConcurrency(unittest.TestCase):
    def test_fanout_runs_tasks_concurrently(self):
        ts = tasks3()
        runner = FakeRunner(barrier=threading.Barrier(len(ts)))   # all must run at once
        choice = ExecutionChoice(PARALLEL, isolation=True, fanout=True)
        result = run_tasks(ts, choice, runner=runner)
        self.assertGreater(len(runner.threads), 1)               # ran on multiple threads
        self.assertEqual(len(result.results), 3)


class TestEstimateAndBudget(unittest.TestCase):
    def test_estimate_surfaced_and_actual_within_budget(self):
        runner = FakeRunner()
        choice = ExecutionChoice(PARALLEL, isolation=True)
        result = run_tasks(tasks3(), choice, runner=runner, budget=100000)
        self.assertGreater(result.estimate.est_input_tokens, 0)
        # the conservative estimate bounds the actual output, and we stay under budget
        self.assertLessEqual(result.actual_output, result.estimate.est_output_tokens)
        self.assertTrue(result.within_budget)


class TestLedger(unittest.TestCase):
    def test_every_subagent_decision_is_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            run_tasks(tasks3(), ExecutionChoice(PARALLEL, isolation=True),
                      runner=FakeRunner(), ledger=led)
            kinds = [e["kind"] for e in led.entries()]
            self.assertEqual(kinds.count("subagent"), 3)         # one per task
            self.assertIn("exec_estimate", kinds)


class TestDegrade(unittest.TestCase):
    def test_degrades_when_no_runner(self):
        result = run_tasks(tasks3(), ExecutionChoice(PARALLEL, isolation=True),
                           runner=None)
        self.assertTrue(result.degraded)
        self.assertEqual(len(result.results), 3)                 # still produced results
        self.assertFalse(result.results[0].isolated)             # sequential fallback

    def test_degrades_when_subagent_unavailable(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            result = run_tasks(tasks3(), ExecutionChoice(PARALLEL, isolation=True),
                               runner=FakeRunner(fail=True), ledger=led)
            self.assertTrue(result.degraded)
            self.assertIn("exec_degrade", [e["kind"] for e in led.entries()])

    def test_sequential_choice_needs_no_runner(self):
        result = run_tasks(tasks3(), ExecutionChoice(SEQUENTIAL))
        self.assertFalse(result.degraded)
        self.assertEqual(len(result.results), 3)


if __name__ == "__main__":
    unittest.main()
