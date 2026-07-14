"""B3 (0.0.13) — NO fabricated success rows in the audit ledger.

The sequential floor is mokata's DEFAULT execution mode, and it never touched a runner: for
every task it invented `output="processed:<id>"` with `ok=True` and appended
`{"kind": "sequential", "ok": true}` to the ledger — work nothing ran, recorded as real.
MS.S3 then hash-chains that row (the fabrication became durably ATTESTED) and SI.3 made
approvals human-minted, so a fabricated entry was the last way left to lie to the ledger from
INSIDE (doc 74, B3). P16: counters built on the ledger must not count fiction.

This pins:
  * REAL RUNNER — a sequential run WITH a runner now actually invokes it (shared context, not
    isolated), and its ledger rows carry the runner's REAL `ok` + `simulated: false`; a task the
    runner fails records `ok: false`, never a fabricated success;
  * HONEST LABEL — with NO runner (the floor, and every degrade path) nothing executed, so every
    row carries `simulated: true`. The old shape — a bare `ok: true` with no `simulated` field —
    is dead;
  * DEGRADE-CLEAN — a runner that goes unavailable on the sequential floor never raises; the rest
    of the batch is labeled simulated;
  * CONSUMER HONESTY — the lanes view labels a simulated batch, the stage-badge develop
    sub-counter never counts a simulated lane as done, the dashboard activity label and the
    why-timeline both say "simulated";
  * ADDITIVE ONLY — new field on new entries; the MS.S3 hash chain still verifies.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the import path)

from mokata import dashboard as D
from mokata import progress as P
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
from mokata.govern.ledger import why_timeline


class FakeRunner:
    """A runner that really runs: it records the context it was handed and returns a result."""

    def __init__(self, fail=False, unavailable=False):
        self.seen = {}
        self.fail = fail
        self.unavailable = unavailable

    def run(self, task):
        if self.unavailable:
            raise SubagentUnavailable("no subagent harness")
        self.seen[task.id] = task.context
        out = f"done:{task.id}"
        return TaskResult(task_id=task.id, ok=not self.fail, summary=f"ran {task.id}",
                          output=out,
                          input_tokens=estimate_tokens(task.context + task.description),
                          output_tokens=estimate_tokens(out), seen_context=task.context)


def tasks3():
    return [Task("a", "build a", context="CONTEXT-A"),
            Task("b", "build b", context="CONTEXT-B"),
            Task("c", "build c", context="CONTEXT-C")]


def _ledger(d):
    return AuditLedger(os.path.join(d, "l.jsonl"))


def _seq_rows(led):
    return [e for e in led.entries() if e.get("kind") == "sequential"]


# =========================================================== the real runner is actually passed
class TestSequentialPassesTheRealRunner(unittest.TestCase):
    def test_sequential_with_a_runner_actually_runs_it(self):
        with tempfile.TemporaryDirectory() as d:
            led = _ledger(d)
            runner = FakeRunner()
            result = run_tasks(tasks3(), ExecutionChoice(SEQUENTIAL), runner=runner, ledger=led)
            # the runner SAW every task — the floor no longer invents the work
            self.assertEqual(sorted(runner.seen), ["a", "b", "c"])
            self.assertEqual([r.output for r in result.results],
                             ["done:a", "done:b", "done:c"])
            self.assertTrue(all(not r.simulated for r in result.results))
            rows = _seq_rows(led)
            self.assertEqual(len(rows), 3)
            self.assertTrue(all(e["ok"] is True for e in rows))
            self.assertTrue(all(e["simulated"] is False for e in rows))   # real-run-backed

    def test_the_runner_receives_the_shared_sequential_context(self):
        with tempfile.TemporaryDirectory() as d:
            runner = FakeRunner()
            run_tasks(tasks3(), ExecutionChoice(SEQUENTIAL), runner=runner, ledger=_ledger(d))
            # sequential shares context (it is NOT the isolated path): b sees a's real output
            self.assertIn("CONTEXT-B", runner.seen["b"])
            self.assertIn("done:a", runner.seen["b"])
            self.assertFalse(runner.seen["a"].startswith("done:"))        # a saw no prior output

    def test_a_failed_task_is_recorded_as_failed_not_fabricated_ok(self):
        with tempfile.TemporaryDirectory() as d:
            led = _ledger(d)
            result = run_tasks(tasks3(), ExecutionChoice(SEQUENTIAL),
                               runner=FakeRunner(fail=True), ledger=led)
            self.assertTrue(all(not r.ok for r in result.results))
            rows = _seq_rows(led)
            self.assertTrue(all(e["ok"] is False for e in rows))          # the REAL verdict
            self.assertTrue(all(e["simulated"] is False for e in rows))


# ================================================== no runner ⇒ labeled simulated, never faked
class TestNoRunnerIsLabeledSimulated(unittest.TestCase):
    def _assert_all_simulated(self, led, result, n=3):
        rows = _seq_rows(led)
        self.assertEqual(len(rows), n)
        self.assertTrue(all(e.get("simulated") is True for e in rows))
        self.assertTrue(all(r.simulated for r in result.results))
        self.assertTrue(result.simulated)

    def test_the_floor_with_no_runner_labels_every_row_simulated(self):
        with tempfile.TemporaryDirectory() as d:
            led = _ledger(d)
            result = run_tasks(tasks3(), ExecutionChoice(SEQUENTIAL), ledger=led)
            self._assert_all_simulated(led, result)

    def test_the_old_fabricated_shape_is_dead(self):
        """The regression pin: an entry is either real-run-backed or carries `simulated: true`.
        A bare fabricated `ok: true` with no `simulated` field can never be written again."""
        with tempfile.TemporaryDirectory() as d:
            led = _ledger(d)
            run_tasks(tasks3(), ExecutionChoice(SEQUENTIAL), ledger=led)
            for e in _seq_rows(led):
                self.assertIn("simulated", e)                  # never a BARE ok:true row
                self.assertNotEqual((e.get("ok"), e.get("simulated")), (True, False))

    def test_parallel_without_a_runner_degrades_to_simulated_rows(self):
        with tempfile.TemporaryDirectory() as d:
            led = _ledger(d)
            result = run_tasks(tasks3(), ExecutionChoice(PARALLEL, isolation=True),
                               runner=None, ledger=led)
            self.assertTrue(result.degraded)
            self._assert_all_simulated(led, result)

    def test_subagent_unavailable_degrades_to_simulated_rows(self):
        with tempfile.TemporaryDirectory() as d:
            led = _ledger(d)
            result = run_tasks(tasks3(), ExecutionChoice(PARALLEL, isolation=True),
                               runner=FakeRunner(unavailable=True), ledger=led)
            self.assertTrue(result.degraded)
            self.assertIn("exec_degrade", [e["kind"] for e in led.entries()])
            self._assert_all_simulated(led, result)

    def test_a_runner_that_goes_unavailable_on_the_floor_degrades_clean(self):
        """SEQUENTIAL + a runner that raises: never hard-fail (the floor's contract) — the batch
        is completed as SIMULATED and says so, rather than fabricating success."""
        with tempfile.TemporaryDirectory() as d:
            led = _ledger(d)
            result = run_tasks(tasks3(), ExecutionChoice(SEQUENTIAL),
                               runner=FakeRunner(unavailable=True), ledger=led)
            self.assertEqual(len(result.results), 3)
            self._assert_all_simulated(led, result)


# ============================================================================ consumer honesty
class TestConsumerHonesty(unittest.TestCase):
    """Every consumer of the `sequential` rows reads the label rather than believing the row."""

    def _lanes(self, d, *, simulated, tasks=2):
        from mokata.config import Surface
        from mokata.govern.resume import PipelineCheckpoint
        from mokata.init import init_repo
        init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
        surface = Surface.load(d)
        cp = PipelineCheckpoint(surface.state, "run-b3")
        for ph in P.PIPELINE_PHASES:
            cp.mark_passed(ph)                                  # badge sits at `develop`
        led = AuditLedger.from_mokata_dir(surface.mokata_dir)
        led.record("exec_estimate", mode="sequential", tasks=tasks)
        for i in range(tasks):
            led.record("sequential", task=f"t{i + 1}", ok=True, simulated=simulated)
        return surface, P.build_run_lanes(surface.state, ledger=led)

    def test_lanes_label_a_simulated_batch(self):
        with tempfile.TemporaryDirectory() as d:
            _, rl = self._lanes(d, simulated=True)
            self.assertTrue(rl.simulated)
            self.assertTrue(rl.lanes[0].simulated)
            self.assertIn("simulated", rl.lanes[0].note)
            self.assertIn("simulated", P.render_lanes(rl))
            self.assertTrue(rl.to_dict()["simulated"])

    def test_lanes_do_not_label_a_real_run(self):
        with tempfile.TemporaryDirectory() as d:
            _, rl = self._lanes(d, simulated=False)
            self.assertFalse(rl.simulated)
            self.assertNotIn("simulated", rl.lanes[0].note)

    def test_the_develop_counter_never_counts_a_simulated_batch(self):
        """The 0.0.9 stage-6c develop sub-counter: a simulated batch yields NO counter — it is
        not counted as real, and it is not counted at all (exactly like review/ship show none)."""
        with tempfile.TemporaryDirectory() as d:
            surface, rl = self._lanes(d, simulated=True)
            self.assertEqual(P.develop_counter(rl), "")
            stage, counter = P._badge_state(surface)
            self.assertEqual(stage, "develop")
            self.assertEqual(counter, "")                       # never an invented n/m
            self.assertNotIn("/", P.build_stage_badge(surface).split("develop")[-1][:6])

    def test_the_develop_counter_skips_simulated_lanes(self):
        """The lane-level guard: even inside a fan-out view, a lane whose work was SIMULATED is
        never counted as `done` — a simulated lane can never inflate a real counter."""
        real = P.Lane(name="t1", state=P.L_DONE)
        fake = P.Lane(name="t2", state=P.L_DONE, simulated=True)
        rl = P.RunLanes(active=True, mode="parallel", lanes=[real, fake], tasks=2)
        self.assertEqual(P.develop_counter(rl), "1/2")          # not 2/2

    def test_the_dashboard_activity_label_says_simulated(self):
        lane = P.Lane(name="t1", state=P.L_DONE, simulated=True)
        feed = [{"kind": "sequential", "task": "t1", "ok": True, "simulated": True}]
        self.assertIn("simulated", D.lane_activity_label(lane, feed))
        real = [{"kind": "sequential", "task": "t1", "ok": True, "simulated": False}]
        self.assertEqual(D.lane_activity_label(P.Lane(name="t1", state=P.L_DONE), real), "done")

    def test_the_why_timeline_labels_simulated_rows(self):
        lines = why_timeline([
            {"seq": 1, "kind": "sequential", "task": "t1", "ok": True, "simulated": True},
            {"seq": 2, "kind": "sequential", "task": "t2", "ok": True, "simulated": False},
        ])
        self.assertIn("simulated", lines[0])
        self.assertNotIn("simulated", lines[1])


# ================================================================ additive: MS.S3 chain intact
class TestLedgerIntegrityUnchanged(unittest.TestCase):
    def test_the_simulated_field_is_additive_and_the_chain_verifies(self):
        with tempfile.TemporaryDirectory() as d:
            led = _ledger(d)
            run_tasks(tasks3(), ExecutionChoice(SEQUENTIAL), ledger=led)
            self.assertTrue(led.verify().intact)                # MS.S3 hash-chain untouched
            row = _seq_rows(led)[0]
            for key in ("seq", "kind", "at", "prev_hash", "entry_hash"):
                self.assertIn(key, row)                         # no schema change


if __name__ == "__main__":
    unittest.main()
