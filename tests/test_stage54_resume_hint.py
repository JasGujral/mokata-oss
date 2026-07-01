"""Stage 54 — proactive resume surfacing in the SessionStart briefing.

On SessionStart, if there's a resumable run (a session with a passed gate) and/or an
in-progress brainstorm, the briefing says so in ONE line (max two) — so reopening a repo
TELLS you there's something to pick up. Composes the existing primitives
(progress.list_sessions + brainstorm.restore_brainstorm_progress) read-only; degrade-clean;
within the 2k briefing budget; absent (no noise) when there's nothing to resume.
"""

import os
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.bootstrap import (
    BOOTSTRAP_TOKEN_BUDGET,
    build_bootstrap,
    build_resume_hint,
)
from mokata.brainstorm import (
    Approach,
    BrainstormSession,
    save_brainstorm_progress,
)
from mokata.config import Surface
from mokata.govern import PipelineCheckpoint
from mokata.memory import MemoryStore


def _repo(d, profile="standard"):
    from mokata.init import init_repo
    init_repo(root=d, profile=profile, assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _in_progress_session(topic="slugify"):
    s = BrainstormSession(topic)
    s.ask("unicode or ascii-only?")
    s.answer("ascii-only")
    s.propose_approaches([
        Approach("regex", "strip via regex", pros=["tiny"], cons=["edge cases"]),
        Approach("library", "use a slug lib", pros=["robust"], cons=["a dependency"]),
    ])
    return s  # deliberately NOT approved — mid-stream


class TestResumeHint(unittest.TestCase):
    # ----------------------------------------------------------- resumable run
    def test_resumable_run_surfaces_in_briefing(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            cp = PipelineCheckpoint(surface.state, "run-a")
            cp.mark_passed("brainstorm")
            cp.mark_passed("analysis")

            hint = build_resume_hint(surface)
            self.assertIsNotNone(hint)
            self.assertIn("Resume:", hint)
            self.assertIn("strawman", hint)          # the phase resume continues at
            self.assertIn("analysis", hint)          # the last passed gate
            self.assertIn("mokata resume", hint)

            text = build_bootstrap(surface).text
            self.assertIn("Resume:", text)
            self.assertIn("strawman", text)
            self.assertIn("mokata resume", text)

    # ----------------------------------------------------- in-progress brainstorm
    def test_in_progress_brainstorm_surfaces_in_briefing(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            save_brainstorm_progress(_in_progress_session("auth-refactor"), surface.state)

            hint = build_resume_hint(surface)
            self.assertIsNotNone(hint)
            self.assertIn("brainstorm", hint.lower())
            self.assertIn("/brainstorm", hint)
            self.assertIn("auth-refactor", hint)     # the topic, for actionability

            text = build_bootstrap(surface).text
            self.assertIn("/brainstorm", text)
            self.assertIn("auth-refactor", text)

    def test_both_present_surfaces_both_compactly(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            PipelineCheckpoint(surface.state, "run-a").mark_passed("brainstorm")
            save_brainstorm_progress(_in_progress_session(), surface.state)

            hint = build_resume_hint(surface)
            self.assertIsNotNone(hint)
            lines = hint.splitlines()
            self.assertEqual(len(lines), 2)          # both, still ≤ 2 short lines
            self.assertTrue(any("mokata resume" in ln for ln in lines))
            self.assertTrue(any("/brainstorm" in ln for ln in lines))

    # ------------------------------------------------------------- no noise
    def test_nothing_to_resume_no_hint(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            self.assertIsNone(build_resume_hint(surface))
            self.assertNotIn("Resume:", build_bootstrap(surface).text)

    def test_complete_run_is_not_resumable(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            cp = PipelineCheckpoint(surface.state, "run-c")
            from mokata.brainstorm import PIPELINE_PHASES
            for phase in PIPELINE_PHASES:
                cp.mark_passed(phase)                # fully complete -> nothing to resume
            self.assertIsNone(build_resume_hint(surface))

    def test_fresh_run_with_no_passed_gate_is_not_resumable(self):
        # A persisted checkpoint with nothing passed yet — no progress to pick up, no noise.
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            surface.state.write("pipeline_run__fresh", {"run_id": "fresh", "passed": []})
            self.assertIsNone(build_resume_hint(surface))

    # ------------------------------------------------------- read-only + deterministic
    def test_building_hint_is_read_only_and_deterministic(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, profile="full")
            cp = PipelineCheckpoint(surface.state, "run-z")
            cp.mark_passed("brainstorm")
            save_brainstorm_progress(_in_progress_session(), surface.state)

            before_reads = MemoryStore.from_surface(Surface.load(d)).stats.reads
            before_cp = surface.state.read("pipeline_run__run-z")
            before_bp = surface.state.read("brainstorm_progress")

            first = build_resume_hint(surface)
            second = build_resume_hint(surface)

            self.assertEqual(first, second)          # deterministic
            after_reads = MemoryStore.from_surface(Surface.load(d)).stats.reads
            self.assertEqual(after_reads, before_reads)                      # no read bumped
            self.assertEqual(surface.state.read("pipeline_run__run-z"), before_cp)  # unchanged
            self.assertEqual(surface.state.read("brainstorm_progress"), before_bp)  # unchanged

    def test_corrupt_brainstorm_checkpoint_degrades_clean(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            # Write a garbage brainstorm-progress file: must not raise, must not surface.
            path = surface.state.path("brainstorm_progress")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{ not valid json")
            self.assertIsNone(build_resume_hint(surface))      # no error, no hint
            # and the briefing still builds cleanly
            self.assertTrue(build_bootstrap(surface).within_budget)

    # --------------------------------------------------------------- frugal
    def test_briefing_with_hint_stays_within_budget(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, profile="full")
            cp = PipelineCheckpoint(surface.state, "run-a")
            cp.mark_passed("brainstorm")
            cp.mark_passed("analysis")
            save_brainstorm_progress(_in_progress_session(), surface.state)
            result = build_bootstrap(surface)
            self.assertTrue(result.within_budget)
            self.assertLessEqual(result.token_estimate, BOOTSTRAP_TOKEN_BUDGET)
            self.assertIn("Resume:", result.text)              # hint present AND fits


if __name__ == "__main__":
    unittest.main()
