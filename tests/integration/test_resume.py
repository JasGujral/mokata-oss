"""Stage 20 — resume from the last passed gate after an interruption.

Pipeline progress is persisted to the state store after each passed gate. A later session
(a fresh Surface over the same repo) resumes at the first phase AFTER the last one passed —
a crash never restarts from the top.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import tempfile
import unittest

from _support import write_sample_repo  # noqa: F401  (import = path-shim side effect)

from mokata.brainstorm import PIPELINE_PHASES
from mokata.config import Surface
from mokata.govern.resume import CHECKPOINT_PREFIX, PipelineCheckpoint
from mokata.init import init_repo


def _silent(_):
    pass


def _init(d, profile="standard"):
    init_repo(root=d, profile=profile, assume_yes=True, out=_silent)
    return Surface.load(d)


class TestResumeFromLastGate(unittest.TestCase):
    def test_resumes_after_last_passed_phase_in_a_new_session(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _init(d, "standard")

            # session 1 — pass the first two gates, then "crash"
            cp1 = PipelineCheckpoint(surface.state, "run-1")
            cp1.mark_passed("brainstorm")
            cp1.mark_passed("analysis")

            # the checkpoint is on disk under .mokata/state/
            self.assertTrue(os.path.exists(os.path.join(
                d, ".mokata", "state", CHECKPOINT_PREFIX + "run-1.json")))

            # session 2 — reload the surface, rebuild the checkpoint from disk
            cp2 = PipelineCheckpoint(Surface.load(d).state, "run-1")
            self.assertEqual(cp2.last_passed(), "analysis")
            self.assertEqual(cp2.resume_phase(), "strawman")
            self.assertFalse(cp2.is_complete())

            # finishing the remaining phases marks the run complete
            for phase in PIPELINE_PHASES[PIPELINE_PHASES.index("analysis") + 1:]:
                cp2.mark_passed(phase)
            self.assertIsNone(cp2.resume_phase())
            self.assertTrue(cp2.is_complete())


if __name__ == "__main__":
    unittest.main()
