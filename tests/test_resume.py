"""I6 — resume / recovery: pipeline progress is persisted after each passed gate so an
interrupted run resumes from the last passed gate, never from the start."""

import os
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.brainstorm import PIPELINE_PHASES
from mokata.govern import PipelineCheckpoint
from mokata.state import StateStore


class TestResume(unittest.TestCase):
    def test_resumes_from_last_passed_gate(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(os.path.join(d, "state"))
            cp = PipelineCheckpoint(store, "run-1")
            for phase in ("brainstorm", "analysis", "strawman", "pre_mortem",
                          "probes", "completeness_gate"):
                cp.mark_passed(phase)
            # --- crash here ---
            resumed = PipelineCheckpoint(store, "run-1")     # fresh "session"
            self.assertEqual(resumed.last_passed(), "completeness_gate")
            self.assertEqual(resumed.resume_phase(), "emit")  # not "brainstorm"
            self.assertFalse(resumed.is_complete())

    def test_crash_safe_progress_is_durable(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(os.path.join(d, "state"))
            PipelineCheckpoint(store, "r").mark_passed("brainstorm")
            # a brand-new instance over the same store still has the progress
            self.assertEqual(PipelineCheckpoint(store, "r").passed, ["brainstorm"])

    def test_fresh_run_starts_at_first_phase(self):
        with tempfile.TemporaryDirectory() as d:
            cp = PipelineCheckpoint(StateStore(os.path.join(d, "state")), "new")
            self.assertIsNone(cp.last_passed())
            self.assertEqual(cp.resume_phase(), PIPELINE_PHASES[0])

    def test_complete_run_has_no_resume_phase(self):
        with tempfile.TemporaryDirectory() as d:
            cp = PipelineCheckpoint(StateStore(os.path.join(d, "state")), "done")
            for phase in PIPELINE_PHASES:
                cp.mark_passed(phase)
            self.assertTrue(cp.is_complete())
            self.assertIsNone(cp.resume_phase())


if __name__ == "__main__":
    unittest.main()
