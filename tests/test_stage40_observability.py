"""Stage 40 — run-observability UX (parallel-aware terminal lanes + clickable HTML dashboard).

Both jsonschema states (no jsonschema imported here — these exercise progress/dashboard, which
are pure-stdlib). Everything is read-only / local-first / frugal / degrade-clean.
"""

import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO

import _support  # noqa: F401  (puts src/ on the path)

from mokata import cli
from mokata.config import Surface
from mokata.dashboard import (
    LEDGER_FEED_TAIL,
    build_feed,
    dashboard_enabled,
    dashboard_path,
    render_dashboard_html,
    write_dashboard,
)
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


# ----------------------------------------------------------------- Tier 2: HTML dashboard

class TestDashboard(unittest.TestCase):
    def _seed_parallel(self, surface):
        _active_run(surface)
        led = _ledger(surface)
        led.record("exec_estimate", mode="parallel", tasks=2)
        led.record("subagent", task="alpha", ok=True, isolated=True, review_passed=True)
        led.record("subagent", task="beta", ok=True, isolated=True, review_passed=True)
        return led

    def test_self_contained_no_external_or_network_refs(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, ux="dashboard")
            self._seed_parallel(surface)
            path = write_dashboard(surface, run_id="r1")
            self.assertTrue(os.path.exists(path))
            self.assertIn("temp_local", path)            # gitignored location
            with open(path, encoding="utf-8") as fh:
                doc = fh.read()
            # no external assets / network / remote anything
            for needle in ("http://", "https://", "//cdn", "<script src", "<link ",
                           "src=\"http", "@import"):
                self.assertNotIn(needle, doc)
            # contains the lanes + phases + the ledger feed
            self.assertIn("alpha", doc)
            self.assertIn("beta", doc)
            self.assertIn("pipeline", doc)
            self.assertIn("feed", doc)
            self.assertIn("exec_estimate", doc)          # a ledger row is shown

    def test_render_is_deterministic_for_fixed_state(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, ux="dashboard")
            led = self._seed_parallel(surface)
            rl = build_run_lanes(surface.state, ledger=led, run_id="r1")
            feed = build_feed(led)
            a = render_dashboard_html(rl, feed)
            b = render_dashboard_html(rl, feed)
            self.assertEqual(a, b)                        # pure function, no wall-clock

    def test_once_has_no_meta_refresh_live_does(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, ux="dashboard")
            led = self._seed_parallel(surface)
            rl = build_run_lanes(surface.state, ledger=led, run_id="r1")
            feed = build_feed(led)
            self.assertNotIn("http-equiv=\"refresh\"", render_dashboard_html(rl, feed))
            self.assertIn("http-equiv=\"refresh\"",
                          render_dashboard_html(rl, feed, refresh_secs=2))

    def test_ledger_feed_is_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, ux="dashboard")
            _active_run(surface)
            led = _ledger(surface)
            led.record("exec_estimate", mode="parallel", tasks=1)
            for i in range(LEDGER_FEED_TAIL + 50):       # far more than the bound
                led.record("subagent", task=f"t{i}", ok=True, review_passed=True)
            feed = build_feed(led)
            self.assertEqual(len(feed), LEDGER_FEED_TAIL)   # the asserted bound
            self.assertLessEqual(len(led.entries()), LEDGER_FEED_TAIL + 100)  # full log is bigger
            self.assertGreater(len(led.entries()), LEDGER_FEED_TAIL)

    def test_no_run_friendly_empty_state(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, ux="dashboard")
            path = write_dashboard(surface)
            with open(path, encoding="utf-8") as fh:
                doc = fh.read()
            self.assertIn("no run in progress", doc.lower())
            self.assertNotIn("lanes (", doc)             # no lanes section in the empty state

    def test_ledger_absent_renders_lanes_only(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, ux="dashboard")
            _active_run(surface)
            rl = build_run_lanes(surface.state, ledger=None)
            doc = render_dashboard_html(rl, [])          # no feed
            self.assertIn("no audit ledger yet", doc)
            self.assertIn("lanes", doc)


# ----------------------------------------------------------------- config + CLI + read-only

class TestConfigAndCli(unittest.TestCase):
    def test_setting_gates_the_dashboard(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(dashboard_enabled(_repo(d)))                 # default terminal
            self.assertTrue(dashboard_enabled(_repo(d + "x" if False else d, ux="both")))

    def test_terminal_setting_writes_no_html(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)               # terminal default
            _active_run(surface)
            buf = StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["watch", "--once", "--path", d])
            self.assertEqual(rc, 0)
            self.assertFalse(os.path.exists(dashboard_path(surface.mokata_dir)))
            self.assertIn("dashboard is off", buf.getvalue())

    def test_dashboard_setting_writes_html(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, ux="dashboard")
            _active_run(surface)
            with redirect_stdout(StringIO()):
                rc = cli.main(["watch", "--once", "--path", d])
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.exists(dashboard_path(surface.mokata_dir)))

    def test_watch_and_progress_are_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, ux="dashboard")
            _active_run(surface)
            led = _ledger(surface)
            led.record("exec_estimate", mode="parallel", tasks=1)
            led.record("subagent", task="t1", ok=True, review_passed=True)
            before_entries = len(led.entries())
            before_cp = surface.state.read(CHECKPOINT_PREFIX + "r1")

            with redirect_stdout(StringIO()):
                cli.main(["watch", "--once", "--path", d])
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
