"""Stage 54d — parallel-agent observability, surfaced INSIDE Claude Code.

The parallel-lane / watch / govern engines already exist (Stage 40 / 48) but were CLI-only.
This stage exposes them as MCP read tools + slash commands, and adds a compact agents summary
to the 54b badge during a fan-out — all REUSING the existing read-only engines.

  * `lanes` MCP tool → the per-subagent parallel lanes (running/done/blocked) via
    build_run_lanes/render_lanes; `watch` → the self-contained dashboard (honoring
    settings.ux.progress); `govern` → the governance view. All declared "read".
  * /mokata:progress, /mokata:watch, /mokata:govern slash templates exist + are namespaced.
  * build_stage_badge gains a compact agents summary during a parallel batch, omitted when
    sequential / no parallel run.
  * Everything degrades clean with no run / no ledger.
"""

import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import mcp_server as M
from mokata import progress
from mokata.config import Surface
from mokata.govern import AuditLedger
from mokata.govern.resume import PipelineCheckpoint

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMMANDS_DIR = os.path.join(ROOT, "templates", "commands")


def _repo(d, profile="standard"):
    from mokata.init import init_repo
    init_repo(root=d, profile=profile, assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _active_run(surface, rid="run-a", passed=("brainstorm",)):
    cp = PipelineCheckpoint(surface.state, rid)
    for p in passed:
        cp.mark_passed(p)
    return rid


def _parallel_batch(surface, tasks=3):
    """A parallel exec batch: 1 done + 1 blocked + (tasks-2) still running."""
    led = AuditLedger.from_mokata_dir(surface.mokata_dir)
    led.record("exec_estimate", mode="parallel", tasks=tasks)
    led.record("subagent", task="t1", ok=True, review_passed=True)     # done
    led.record("subagent", task="t2", ok=False)                        # blocked
    return led


def _sequential_batch(surface):
    led = AuditLedger.from_mokata_dir(surface.mokata_dir)
    led.record("exec_estimate", mode="sequential", tasks=2)
    led.record("sequential", task="t1", ok=True)
    return led


def _state_snapshot(surface):
    """Sorted run-state dir contents, or [] when it doesn't exist yet (created lazily)."""
    root = surface.state.root
    return sorted(os.listdir(root)) if os.path.isdir(root) else []


def _set_progress_tier(d, tier):
    import json
    from mokata import MOKATA_DIR
    from pathlib import Path
    p = Path(d) / MOKATA_DIR / "manifest.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    data.setdefault("settings", {}).setdefault("ux", {})["progress"] = tier
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# ===================================================================== MCP: lanes
class TestLanesTool(unittest.TestCase):
    def test_registered_read_only(self):
        self.assertIn("lanes", M.read_tool_names())
        self.assertNotIn("lanes", M.write_tool_names())

    def test_renders_parallel_lanes(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _active_run(surface)
            _parallel_batch(surface)
            res = M.lanes(path=d)
            self.assertTrue(res["active"])
            self.assertEqual(res["mode"], "parallel")
            self.assertEqual(len(res["lanes"]), 3)               # 1 done + 1 blocked + 1 running
            self.assertIn("lanes", res["block"])                 # the rendered multi-lane block

    def test_no_run_degrades_clean(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            res = M.lanes(path=d)
            self.assertFalse(res["active"])
            self.assertTrue(res["block"])                        # a friendly message, not an error

    def test_is_read_only_on_run_state(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _active_run(surface)
            _parallel_batch(surface)
            before = _state_snapshot(surface)
            M.lanes(path=d)
            self.assertEqual(before, _state_snapshot(surface))


# ===================================================================== MCP: watch
class TestWatchTool(unittest.TestCase):
    def test_registered_read_only(self):
        self.assertIn("watch", M.read_tool_names())
        self.assertNotIn("watch", M.write_tool_names())

    def test_writes_dashboard_when_enabled(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _set_progress_tier(d, "dashboard")
            _active_run(surface)
            _parallel_batch(surface)
            res = M.watch(path=d)
            self.assertTrue(res["enabled"])
            self.assertTrue(os.path.exists(res["path"]))         # the self-contained HTML
            with open(res["path"], encoding="utf-8") as fh:
                self.assertIn("<html", fh.read().lower())

    def test_off_under_terminal_tier(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)                                             # default tier = terminal
            res = M.watch(path=d)
            self.assertFalse(res["enabled"])
            self.assertIn("settings.ux.progress", res["note"])

    def test_does_not_mutate_run_state(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _set_progress_tier(d, "dashboard")
            _active_run(surface)
            before = _state_snapshot(surface)
            M.watch(path=d)
            self.assertEqual(before, _state_snapshot(surface))


# ===================================================================== MCP: govern
class TestGovernTool(unittest.TestCase):
    def test_registered_read_only(self):
        self.assertIn("govern", M.read_tool_names())
        self.assertNotIn("govern", M.write_tool_names())

    def test_returns_governance_view(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, profile="full")
            res = M.govern(path=d)
            self.assertTrue(os.path.exists(res["path"]))         # the self-contained HTML
            self.assertEqual(res["version"], surface.manifest.mokata_version)
            self.assertIn("profile", res)
            self.assertIn("proposals", res)

    def test_does_not_mutate_run_state(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, profile="full")
            before = _state_snapshot(surface)
            M.govern(path=d)
            self.assertEqual(before, _state_snapshot(surface))


# ===================================================================== slash templates
class TestSlashTemplates(unittest.TestCase):
    MARKER = "mokata ·"

    def _read(self, name):
        with open(os.path.join(COMMANDS_DIR, f"{name}.md"), encoding="utf-8") as fh:
            return fh.read()

    def test_three_templates_exist(self):
        for name in ("progress", "watch", "govern"):
            self.assertTrue(
                os.path.exists(os.path.join(COMMANDS_DIR, f"{name}.md")),
                f"{name}.md missing")

    def test_namespaced_and_marker_prefixed(self):
        for name in ("progress", "watch", "govern"):
            md = self._read(name)
            self.assertIn(f"name: {name}", md)
            self.assertIn(f"description: {self.MARKER}", md)

    def test_instruct_calling_the_mcp_tool(self):
        # the body points Claude at the matching read tool
        self.assertIn("lanes", self._read("progress").lower())
        self.assertIn("watch", self._read("watch").lower())
        self.assertIn("govern", self._read("govern").lower())


# ===================================================================== badge agents summary
class TestBadgeAgentsSummary(unittest.TestCase):
    def test_agents_summary_pure_helper(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _active_run(surface)
            led = _parallel_batch(surface)
            rl = progress.build_run_lanes(surface.state, ledger=led)
            summ = progress.agents_summary(rl)
            self.assertIn("running", summ)
            self.assertIn("blocked", summ)

    def test_summary_empty_for_sequential(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _active_run(surface)
            led = _sequential_batch(surface)
            rl = progress.build_run_lanes(surface.state, ledger=led)
            self.assertEqual(progress.agents_summary(rl), "")

    def test_summary_empty_with_no_run(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            rl = progress.build_run_lanes(surface.state, ledger=None)
            self.assertEqual(progress.agents_summary(rl), "")

    def test_badge_shows_summary_during_parallel_batch(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _active_run(surface)
            _parallel_batch(surface)
            badge = progress.build_stage_badge(surface)
            self.assertIn("running", badge)
            self.assertIn("blocked", badge)

    def test_badge_omits_summary_when_sequential(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _active_run(surface)
            _sequential_batch(surface)
            badge = progress.build_stage_badge(surface)
            self.assertNotIn("running", badge)
            self.assertNotIn("blocked", badge)

    def test_badge_omits_summary_with_no_parallel_run(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _active_run(surface)                                 # active run, but no exec batch
            badge = progress.build_stage_badge(surface)
            self.assertNotIn("running", badge)


if __name__ == "__main__":
    unittest.main()
