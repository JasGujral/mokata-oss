"""Stage 54h — the "Running N agents" card-grid panel in the watch dashboard.

A PURE render-layer upgrade over the existing build_run_lanes data: the browser `watch`
HTML now shows a "Running N agents" header + a responsive card grid (one card per subagent,
each with a title, a status/activity pill, and a live running/idle dot), keeping the per-lane
ledger drill-down. No new run-state; deterministic; self-contained; read-only; degrade-clean.

No jsonschema is imported here (dashboard/progress are pure-stdlib), so the counts match in
both jsonschema states.
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
    LANE_ACTIVITY_MAX,
    build_feed,
    dashboard_path,
    lane_activity_label,
    render_dashboard_html,
    write_dashboard,
)
from mokata.govern import AuditLedger
from mokata.govern.resume import CHECKPOINT_PREFIX
from mokata.init import init_repo
from mokata.progress import build_run_lanes


def _silent(_):
    pass


def _repo(d, ux="dashboard"):
    init_repo(root=d, profile="full", assume_yes=True, out=_silent)
    if ux:
        cli.main(["config", "set", "settings.ux.progress", ux, "--yes", "--path", d])
    return Surface.load(d)


def _active_run(surface, passed=("brainstorm",), rid="r1"):
    surface.state.write(CHECKPOINT_PREFIX + rid, {"run_id": rid, "passed": list(passed)})
    return rid


def _ledger(surface):
    return AuditLedger.from_mokata_dir(surface.mokata_dir)


def _seed_parallel(surface):
    """3-lane batch: t1 done, t2 still running (estimated-but-not-reported), t3 blocked."""
    _active_run(surface)
    led = _ledger(surface)
    led.record("exec_estimate", mode="parallel", tasks=3)
    led.record("subagent", task="t1", ok=True, isolated=True, review_passed=True)
    led.record("subagent", task="t3", ok=True, isolated=True, review_passed=False)
    return led


def _render(surface, run_id="r1"):
    led = _ledger(surface)
    rl = build_run_lanes(surface.state, ledger=led, run_id=run_id)
    return rl, render_dashboard_html(rl, build_feed(led))


class TestAgentsPanel(unittest.TestCase):
    def test_running_n_agents_header_with_count(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_parallel(surface)             # 1 done + 1 blocked + 1 running(synth)
            rl, doc = _render(surface)
            running = sum(1 for ln in rl.lanes if ln.state == "running")
            self.assertEqual(running, 1)
            self.assertIn(f"Running {running} agents", doc)

    def test_one_card_per_subagent_with_title_pill_and_dot(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_parallel(surface)
            rl, doc = _render(surface)
            self.assertEqual(doc.count("class='card'"), len(rl.lanes))   # one card each
            self.assertEqual(len(rl.lanes), 3)
            for name in ("t1", "t3"):
                self.assertIn(name, doc)                                 # the title
            self.assertIn("statuspill", doc)                             # the status/activity pill
            self.assertIn("class='dot live'", doc)                       # the running lane's live dot
            self.assertIn("agentgrid", doc)                              # the responsive grid

    def test_activity_label_derives_from_latest_ledger_row(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            led = _seed_parallel(surface)
            rl = build_run_lanes(surface.state, ledger=led, run_id="r1")
            feed = build_feed(led)
            by_name = {ln.name: ln for ln in rl.lanes}
            # t1's latest row is a passed subagent → "review passed"; t3 failed review → "blocked"
            self.assertEqual(lane_activity_label(by_name["t1"], feed), "review passed")
            self.assertEqual(lane_activity_label(by_name["t3"], feed), "blocked")
            # the synthesized still-running lane has only the exec_estimate marker → "starting"
            running = next(ln for ln in rl.lanes if ln.state == "running")
            self.assertEqual(lane_activity_label(running, feed), "starting")

    def test_activity_label_is_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _active_run(surface)
            led = _ledger(surface)
            led.record("exec_estimate", mode="parallel", tasks=1)
            led.record("subagent", task="t1", ok=True, review_passed=True,
                       activity="x" * 500)         # an explicit, oversized activity field
            rl = build_run_lanes(surface.state, ledger=led, run_id="r1")
            label = lane_activity_label(rl.lanes[0], build_feed(led))
            self.assertLessEqual(len(label), LANE_ACTIVITY_MAX)

    def test_no_external_or_network_refs(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_parallel(surface)
            _, doc = _render(surface)
            for needle in ("http://", "https://", "//cdn", "<script", "<link ",
                           "src=\"http", "src='http", "@import"):
                self.assertNotIn(needle, doc)

    def test_deterministic_byte_identical(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            led = _seed_parallel(surface)
            rl = build_run_lanes(surface.state, ledger=led, run_id="r1")
            feed = build_feed(led)
            a = render_dashboard_html(rl, feed)
            b = render_dashboard_html(rl, feed)
            self.assertEqual(a, b)                  # pure function of state — no wall-clock

    def test_render_writes_nothing_to_ledger_or_state(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            led = _seed_parallel(surface)
            before_entries = len(led.entries())
            before_cp = surface.state.read(CHECKPOINT_PREFIX + "r1")
            write_dashboard(surface, run_id="r1")   # the real write path
            after = AuditLedger.from_mokata_dir(surface.mokata_dir)
            self.assertEqual(len(after.entries()), before_entries)      # nothing logged
            self.assertEqual(Surface.load(d).state.read(CHECKPOINT_PREFIX + "r1"),
                             before_cp)                                 # state unchanged

    def test_terminal_setting_writes_no_html(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, ux="terminal")       # the default tier
            _seed_parallel(surface)
            with redirect_stdout(StringIO()):
                rc = cli.main(["watch", "--once", "--path", d])
            self.assertEqual(rc, 0)
            self.assertFalse(os.path.exists(dashboard_path(surface.mokata_dir)))

    def test_single_sequential_lane_renders_one_card(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _active_run(surface)
            led = _ledger(surface)
            led.record("exec_estimate", mode="sequential", tasks=2)
            led.record("sequential", task="t1", ok=True)
            led.record("sequential", task="t2", ok=True)
            rl, doc = _render(surface)
            self.assertEqual(len(rl.lanes), 1)
            self.assertEqual(doc.count("class='card'"), 1)
            self.assertIn("Running", doc)            # header present, count sensible (0 — done)

    def test_degrade_clean_no_run(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)                       # no active run
            path = write_dashboard(surface)
            with open(path, encoding="utf-8") as fh:
                doc = fh.read()
            self.assertIn("no run in progress", doc.lower())
            self.assertNotIn("<div class='agentgrid'>", doc)   # no grid markup in the empty state
            self.assertNotIn("Running", doc)                   # no panel header either


if __name__ == "__main__":
    unittest.main()
