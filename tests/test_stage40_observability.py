"""Stage 40 — run-observability UX (parallel-aware terminal lanes).

The Stage-40 clickable HTML dashboard was folded into the IDE's Sessions screen in 0.0.27
WATCH-FOLD (tests/test_watchfold_watch_opens_the_ide.py); the terminal lanes stay, here.
Everything is read-only / local-first / frugal / degrade-clean.
"""

import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO

import _support  # noqa: F401  (puts src/ on the path)

from mokata import cli
from mokata.config import Surface
from mokata.govern import AuditLedger
from mokata.govern.resume import CHECKPOINT_PREFIX
from mokata.init import init_repo
from mokata.progress import (
    L_BLOCKED,
    L_DEGRADED,
    L_DONE,
    L_RUNNING,
    build_run_lanes,
    render_lanes,
)


def _silent(_):
    pass


def _repo(d, ux=None):
    init_repo(root=d, profile="full", assume_yes=True, out=_silent)
    if ux:
        cli.main(["config", "set", "settings.ux.progress", ux, "--yes", "--path", d])
    return Surface.load(d)


def _active_run(surface, passed=("brainstorm",), rid="r1"):
    # seed a persisted, incomplete pipeline checkpoint (so the run reads as active)
    surface.state.write(CHECKPOINT_PREFIX + rid, {"run_id": rid, "passed": list(passed)})
    return rid


def _ledger(surface):
    return AuditLedger.from_mokata_dir(surface.mokata_dir)


# ----------------------------------------------------------------- Tier 1: terminal lanes

class TestTerminalLanes(unittest.TestCase):
    def test_parallel_run_renders_n_lanes(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _active_run(surface)
            led = _ledger(surface)
            led.record("exec_estimate", mode="parallel", tasks=3)
            led.record("subagent", task="t1", ok=True, isolated=True, review_passed=True)
            led.record("subagent", task="t2", ok=True, isolated=True, review_passed=True)
            led.record("subagent", task="t3", ok=True, isolated=True, review_passed=False)

            rl = build_run_lanes(surface.state, ledger=led)
            self.assertEqual(rl.mode, "parallel")
            self.assertEqual(len(rl.lanes), 3)
            states = {ln.name: ln.state for ln in rl.lanes}
            self.assertEqual(states["t1"], L_DONE)
            self.assertEqual(states["t3"], L_BLOCKED)        # review failed
            out = render_lanes(rl)
            self.assertEqual(out.count("\n") >= 3, True)
            self.assertIn("t1", out)
            self.assertIn("3 concurrent", out)

    def test_sequential_run_renders_one_lane(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _active_run(surface)
            led = _ledger(surface)
            led.record("exec_estimate", mode="sequential", tasks=2)
            led.record("sequential", task="t1", ok=True)
            led.record("sequential", task="t2", ok=True)

            rl = build_run_lanes(surface.state, ledger=led)
            self.assertEqual(rl.mode, "sequential")
            self.assertEqual(len(rl.lanes), 1)
            self.assertEqual(rl.lanes[0].state, L_DONE)

    def test_parallel_with_pending_tasks_shows_running_lanes(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _active_run(surface)
            led = _ledger(surface)
            led.record("exec_estimate", mode="parallel", tasks=3)
            led.record("subagent", task="t1", ok=True, review_passed=True)
            rl = build_run_lanes(surface.state, ledger=led)
            self.assertEqual(len(rl.lanes), 3)               # 1 done + 2 still running
            self.assertEqual(sum(ln.state == L_RUNNING for ln in rl.lanes), 2)

    def test_degraded_parallel_renders_single_degraded_lane(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _active_run(surface)
            led = _ledger(surface)
            led.record("exec_estimate", mode="parallel", tasks=2)
            led.record("exec_degrade", reason="no subagent runner available")
            led.record("sequential", task="t1", ok=True)
            rl = build_run_lanes(surface.state, ledger=led)
            self.assertTrue(rl.degraded)
            self.assertEqual(len(rl.lanes), 1)
            self.assertEqual(rl.lanes[0].state, L_DEGRADED)

    def test_no_exec_batch_falls_back_to_single_lane(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _active_run(surface)
            rl = build_run_lanes(surface.state, ledger=_ledger(surface))
            self.assertEqual(rl.mode, "none")
            self.assertEqual(len(rl.lanes), 1)               # back-compat single line
            self.assertEqual(rl.lanes[0].state, L_RUNNING)

    def test_no_run_is_friendly_empty(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            rl = build_run_lanes(surface.state, ledger=_ledger(surface))
            self.assertFalse(rl.active)
            self.assertEqual(rl.lanes, [])
            self.assertIn("no run", render_lanes(rl).lower())

    def test_lanes_without_a_ledger_degrade_to_single_lane(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _active_run(surface)
            rl = build_run_lanes(surface.state, ledger=None)   # ledger absent
            self.assertTrue(rl.active)
            self.assertEqual(len(rl.lanes), 1)


# ----------------------------------------------------------------- config + CLI + read-only

class TestConfigAndCli(unittest.TestCase):
    def test_watch_on_the_terminal_tier_prints_the_parallel_lanes(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)               # terminal default
            _active_run(surface)
            led = _ledger(surface)
            led.record("exec_estimate", mode="parallel", tasks=2)
            led.record("subagent", task="t1", ok=True, review_passed=True)
            led.record("subagent", task="t2", ok=True, review_passed=True)
            buf = StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["watch", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("t1", buf.getvalue())
            self.assertIn("t2", buf.getvalue())

    def test_watch_and_progress_are_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _active_run(surface)
            led = _ledger(surface)
            led.record("exec_estimate", mode="parallel", tasks=1)
            led.record("subagent", task="t1", ok=True, review_passed=True)
            before_entries = len(led.entries())
            before_cp = surface.state.read(CHECKPOINT_PREFIX + "r1")

            with redirect_stdout(StringIO()):
                cli.main(["watch", "--path", d])
                cli.main(["progress", "--lanes", "--path", d])

            after = AuditLedger.from_mokata_dir(surface.mokata_dir)
            self.assertEqual(len(after.entries()), before_entries)   # nothing logged
            self.assertEqual(Surface.load(d).state.read(CHECKPOINT_PREFIX + "r1"),
                             before_cp)                              # state unchanged

    def test_progress_lanes_cli_renders(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _active_run(surface)
            led = _ledger(surface)
            led.record("exec_estimate", mode="parallel", tasks=2)
            led.record("subagent", task="t1", ok=True, review_passed=True)
            led.record("subagent", task="t2", ok=True, review_passed=True)
            buf = StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["progress", "--lanes", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("t1", buf.getvalue())
            self.assertIn("t2", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
