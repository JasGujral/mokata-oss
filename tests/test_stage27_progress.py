"""Stage 27 — run-progress UX (read-only over the persisted run-state).

Both jsonschema states. The model marks done/current/pending and counts correctly across a
fresh / mid-run (reloaded Surface) / complete run; the renderer is compact and degrades
cleanly with no run; `mokata progress` renders and degrades; the active-skill banner reads
right; the pipeline skills carry the progress instruction.
"""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

import _support  # noqa: F401  (puts src/ on the path)

from mokata.brainstorm import PIPELINE_PHASES
from mokata.cli import main
from mokata.config import Surface
from mokata.govern.resume import PipelineCheckpoint
from mokata.init import init_repo
from mokata.progress import (
    NO_RUN_MESSAGE,
    active_banner,
    build_progress,
    find_active_run,
    render_progress,
)
from mokata.skills import get_skill
from mokata.state import StateStore


def _silent(_):
    pass


def _store(d):
    return StateStore(os.path.join(d, "state"))


def _checkpoint(store, run_id, passed_phases):
    cp = PipelineCheckpoint(store, run_id)
    if not passed_phases:
        # a started run with nothing passed yet — persist the (empty) checkpoint so the
        # run exists on disk (mark_passed is what normally writes it)
        from mokata.govern.resume import CHECKPOINT_PREFIX
        store.write(CHECKPOINT_PREFIX + run_id, {"run_id": run_id, "passed": []})
    for p in passed_phases:
        cp.mark_passed(p)
    return cp


# ------------------------------------------------------------------- the model

class TestProgressModel(unittest.TestCase):
    def test_no_run_is_inactive_and_friendly(self):
        with tempfile.TemporaryDirectory() as d:
            p = build_progress(_store(d))
            self.assertFalse(p.active)
            self.assertEqual(p.done, 0)
            self.assertEqual(p.total, len(PIPELINE_PHASES))
            self.assertEqual(p.pending, len(PIPELINE_PHASES))
            self.assertIn("no run in progress", p.message)

    def test_fresh_run_marks_first_phase_current(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            _checkpoint(store, "r1", [])           # a started-but-nothing-passed run
            p = build_progress(store, run_id="r1")
            self.assertTrue(p.active)
            self.assertEqual(p.done, 0)
            self.assertEqual(p.current, PIPELINE_PHASES[0])
            self.assertEqual(p.steps[0].status, "current")
            self.assertTrue(all(s.status == "pending" for s in p.steps[1:]))

    def test_mid_run_done_current_pending_and_counts(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            passed = list(PIPELINE_PHASES[:2])     # brainstorm, analysis passed
            _checkpoint(store, "r1", passed)
            # reload through a fresh Surface-style store (reads from disk)
            p = build_progress(_store(d), run_id="r1")
            self.assertEqual(p.done, 2)
            self.assertEqual(p.pending, len(PIPELINE_PHASES) - 2)
            self.assertEqual(p.current, PIPELINE_PHASES[2])
            self.assertEqual(p.next_phase, PIPELINE_PHASES[3])
            statuses = {s.phase: s.status for s in p.steps}
            self.assertEqual(statuses[PIPELINE_PHASES[0]], "done")
            self.assertEqual(statuses[PIPELINE_PHASES[2]], "current")
            self.assertEqual(statuses[PIPELINE_PHASES[-1]], "pending")
            # counts add up: done + current(1) + pending-after-current == total
            self.assertEqual(p.done + 1 + (p.total - p.done - 1), p.total)

    def test_complete_run_has_no_current(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            _checkpoint(store, "r1", list(PIPELINE_PHASES))   # every phase passed
            p = build_progress(store, run_id="r1")
            self.assertTrue(p.complete)
            self.assertIsNone(p.current)
            self.assertEqual(p.done, len(PIPELINE_PHASES))
            self.assertEqual(p.pending, 0)
            self.assertTrue(all(s.status == "done" for s in p.steps))

    def test_unknown_explicit_run_degrades_clean(self):
        with tempfile.TemporaryDirectory() as d:
            p = build_progress(_store(d), run_id="nope")
            self.assertFalse(p.active)
            self.assertIn("no run", p.message.lower())

    def test_find_active_prefers_incomplete(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            _checkpoint(store, "done-run", list(PIPELINE_PHASES))
            _checkpoint(store, "live-run", list(PIPELINE_PHASES[:1]))
            self.assertEqual(find_active_run(store), "live-run")


# ------------------------------------------------------------------- renderer

class TestRenderer(unittest.TestCase):
    def test_render_block_has_counts_and_glyphs(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            _checkpoint(store, "r1", list(PIPELINE_PHASES[:2]))
            block = render_progress(build_progress(store, "r1"))
            self.assertIn("[2/7 done]", block)
            self.assertIn("← you are here", block)
            self.assertIn("✓", block)
            self.assertIn("next:", block)

    def test_render_ascii_mode(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            _checkpoint(store, "r1", list(PIPELINE_PHASES[:1]))
            block = render_progress(build_progress(store, "r1"), ascii_only=True)
            self.assertIn("[x]", block)
            self.assertIn("[>]", block)
            self.assertNotIn("✓", block)

    def test_render_no_run_is_the_message(self):
        with tempfile.TemporaryDirectory() as d:
            block = render_progress(build_progress(_store(d)))
            self.assertEqual(block, NO_RUN_MESSAGE)

    def test_active_banner(self):
        self.assertEqual(active_banner("brainstorm"), "mokata · brainstorm (running)")
        self.assertEqual(active_banner("develop", running=False, sub_done=2, sub_total=3),
                         "mokata · develop [2/3] (done)")


# ------------------------------------------------------------------- CLI

class TestProgressCLI(unittest.TestCase):
    def _run(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(argv)
        return rc, buf.getvalue()

    def test_progress_degrades_cleanly_with_no_run(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            rc, out = self._run(["progress", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("no run in progress", out)

    def test_progress_renders_an_active_run(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            surface = Surface.load(d)
            _checkpoint(surface.state, "r1", list(PIPELINE_PHASES[:3]))
            rc, out = self._run(["progress", "--path", d, "--run", "r1"])
            self.assertEqual(rc, 0)
            self.assertIn("[3/7 done]", out)
            self.assertIn("next:", out)


# ------------------------------------------------------------------- surfacing

class TestSkillsSurfaceProgress(unittest.TestCase):
    def test_pipeline_skills_carry_progress_instruction(self):
        for name in ("brainstorm", "refine", "spec", "test", "develop", "review"):
            self.assertTrue(get_skill(name).show_progress, f"{name} should show progress")

    def test_non_pipeline_skills_do_not(self):
        for name in ("debug", "optimize", "bug"):
            self.assertFalse(get_skill(name).show_progress)


if __name__ == "__main__":
    unittest.main()
