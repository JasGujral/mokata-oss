"""Stage 6d — everything-on badge default + verbosity config + the enriched /progress view.

The always-on badge is EVERYTHING-ON by default (the full arc + spec/develop counters + the
fan-out agents summary); `settings.ux.badge_verbosity` lets a user dial DOWN to a compact form.
`/progress` (render_progress + a surface) gains MAX detail: the existing 7-phase tracker PLUS the
5 user-stage arc, the 6c develop sub-counter, and what's pending this session. This proves:

  * `badge_verbosity` is opt-DOWN (default `full`), read like `statusline_enabled`, degrade-clean;
  * `full` (default) renders the complete badge; `minimal` collapses to the active cell alone;
  * render_progress WITH a surface appends the user-stage arc + develop counter + pending stages;
  * render_progress WITHOUT a surface is byte-identical to before (no regression, single model);
  * everything derives from `_badge_state` / `build_progress` (no second progress model);
  * degrade-clean on a broken/absent config and a no-run surface.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

import _support  # noqa: F401  (puts src/ on the path)

from mokata import MOKATA_DIR
from mokata import progress as P
from mokata.config import Surface
from mokata.govern import AuditLedger
from mokata.govern.resume import PipelineCheckpoint


def _repo(d):
    from mokata.init import init_repo
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _at_develop(surface, rid="run-a"):
    cp = PipelineCheckpoint(surface.state, rid)
    for ph in P.PIPELINE_PHASES:            # spec emitted -> badge sits at develop
        cp.mark_passed(ph)
    return rid


def _parallel_batch(surface, *, tasks=3, done=2):
    led = AuditLedger.from_mokata_dir(surface.mokata_dir)
    led.record("exec_estimate", mode="parallel", tasks=tasks)
    for i in range(done):
        led.record("subagent", task=f"t{i + 1}", ok=True, review_passed=True)
    return led


def _set_ux(d, key, value):
    p = Path(d) / MOKATA_DIR / "manifest.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    data.setdefault("settings", {}).setdefault("ux", {})[key] = value
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# ===================================================================== badge_verbosity config
class TestBadgeVerbosityConfig(unittest.TestCase):
    def test_default_is_full_everything_on(self):
        with tempfile.TemporaryDirectory() as d:
            s = _repo(d)
            self.assertEqual(P.badge_verbosity(s), "full")   # everything-on by default

    def test_minimal_is_read_from_settings(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _set_ux(d, "badge_verbosity", "minimal")
            self.assertEqual(P.badge_verbosity(Surface.load(d)), "minimal")

    def test_bad_or_unknown_value_degrades_to_full(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _set_ux(d, "badge_verbosity", "loud")            # unrecognised -> full
            self.assertEqual(P.badge_verbosity(Surface.load(d)), "full")

    def test_broken_surface_reads_as_full(self):
        class Broken:
            @property
            def manifest(self):
                raise RuntimeError("no manifest")
        self.assertEqual(P.badge_verbosity(Broken()), "full")   # never silently lose the badge


# ===================================================================== full vs minimal badge
class TestFullVsMinimalBadge(unittest.TestCase):
    def test_full_default_shows_the_complete_arc_and_counters(self):
        with tempfile.TemporaryDirectory() as d:
            s = _repo(d)
            _at_develop(s)
            _parallel_batch(s, tasks=3, done=2)
            badge = P.build_stage_badge(s)                   # default = full
            for stage in P.STAGE_BADGE_STAGES:               # every cell present
                self.assertIn(stage, badge)
            self.assertIn("›develop‹", badge)                # active-cell emphasis
            self.assertIn(" · 2/3", badge)                   # 6c develop sub-counter
            self.assertIn("done", badge)                     # 54d agents summary

    def test_minimal_collapses_to_the_active_cell_only(self):
        with tempfile.TemporaryDirectory() as d:
            s = _repo(d)
            _at_develop(s)
            _parallel_batch(s, tasks=3, done=2)
            _set_ux(d, "badge_verbosity", "minimal")
            badge = P.build_stage_badge(Surface.load(d))
            self.assertIn("›develop‹", badge)                # the active cell, still emphasised
            self.assertNotIn("brainstorm", badge)            # no other cells
            self.assertNotIn("review", badge)
            self.assertNotIn("2/3", badge)                   # no counter
            self.assertNotIn("running", badge)               # no agents summary
            self.assertNotIn("✓", badge)                     # no per-stage glyphs
            self.assertEqual(badge, "mokata ▸ …›develop‹…")

    def test_minimal_ascii_variant(self):
        with tempfile.TemporaryDirectory() as d:
            s = _repo(d)
            _at_develop(s)
            _set_ux(d, "badge_verbosity", "minimal")
            badge = P.build_stage_badge(Surface.load(d), ascii_only=True)
            self.assertEqual(badge, "mokata > ...>develop<...")

    def test_minimal_keeps_session_name(self):
        with tempfile.TemporaryDirectory() as d:
            s = _repo(d)
            _at_develop(s)
            _set_ux(d, "badge_verbosity", "minimal")
            badge = P.build_stage_badge(Surface.load(d), session_name="feat/x")
            self.assertTrue(badge.startswith("mokata ▸ feat/x · "))
            self.assertIn("›develop‹", badge)


# ===================================================================== enriched /progress render
class TestProgressMaxDetail(unittest.TestCase):
    def test_render_with_surface_shows_user_stage_arc_and_pending(self):
        with tempfile.TemporaryDirectory() as d:
            s = _repo(d)
            _at_develop(s)
            _parallel_batch(s, tasks=3, done=2)
            block = P.render_progress(P.build_progress(s.state), surface=s)
            # keeps the existing 7-phase detail ...
            self.assertIn("emit", block)
            self.assertIn("mokata · run  [7/7 done]", block)
            # ... and adds the 5 user-stage arc + develop sub-counter + pending-this-session.
            self.assertIn("user stages:", block)
            for stage in P.STAGE_BADGE_STAGES:
                self.assertIn(stage, block)
            self.assertIn("develop [2/3]", block)            # the 6c sub-counter, in the arc
            self.assertIn("pending this session: review, ship", block)

    def test_render_without_surface_is_unchanged(self):
        # Back-compat: every existing caller that passes no surface gets today's exact block.
        with tempfile.TemporaryDirectory() as d:
            s = _repo(d)
            _at_develop(s)
            prog = P.build_progress(s.state)
            self.assertNotIn("user stages:", P.render_progress(prog))
            self.assertNotIn("pending this session", P.render_progress(prog))

    def test_arc_derives_from_badge_state_single_source(self):
        # The arc is the SAME derivation the badge uses — no independent model. Prove it by
        # matching the arc's develop counter to _badge_state's counter for the same run.
        with tempfile.TemporaryDirectory() as d:
            s = _repo(d)
            _at_develop(s)
            _parallel_batch(s, tasks=4, done=1)
            _, counter = P._badge_state(s)
            self.assertEqual(counter, "1/4")
            block = P.render_progress(P.build_progress(s.state), surface=s)
            self.assertIn(f"develop [{counter}]", block)     # arc reflects the same source

    def test_pending_is_empty_dash_at_the_last_stage(self):
        # A run logged into `ship` (the final user stage) has nothing pending this session.
        # B-LIFE (amendment #2 corollary): a ship-logged run is FINISHED, so the no-run_id progress
        # surface retires it (`find_active_run` excludes it). This test's mechanic — empty pending at
        # the last stage — is rendered via the EXPLICIT run_id surface, which by P17 always shows the
        # named run in full (retirement applies only to "which run is active" resolution).
        with tempfile.TemporaryDirectory() as d:
            s = _repo(d)
            rid = _at_develop(s)
            from mokata.progress_events import ProgressLog, STAGE_ENTER
            ProgressLog.from_surface(s).append_event(STAGE_ENTER, "ship", run_id=rid)
            block = P.render_progress(P.build_progress(s.state, run_id=rid), surface=s)
            self.assertIn("pending this session: —", block)

    def test_no_run_surface_degrades_clean(self):
        with tempfile.TemporaryDirectory() as d:
            s = _repo(d)                                     # no run at all
            prog = P.build_progress(s.state)
            block = P.render_progress(prog, surface=s)
            self.assertEqual(block, prog.message)            # friendly no-run message, unchanged
            self.assertNotIn("user stages:", block)

    def test_arc_helper_degrades_clean_on_none_and_broken(self):
        self.assertEqual(P._user_stage_arc_lines(None), [])
        class Broken:
            @property
            def state(self):
                raise RuntimeError("unreadable")
        self.assertEqual(P._user_stage_arc_lines(Broken()), [])


if __name__ == "__main__":
    unittest.main()
