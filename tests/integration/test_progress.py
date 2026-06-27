"""Stage 27 — run-progress threaded through a real run, surfaced via the MCP `progress`
read tool (registry-level; the SDK isn't required to exercise the tool function).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path-shim side effect)

from mokata import mcp_server as M
from mokata.brainstorm import PIPELINE_PHASES
from mokata.config import Surface
from mokata.execmode import SEQUENTIAL, ExecutionChoice
from mokata.govern.resume import PipelineCheckpoint
from mokata.init import init_repo
from mokata.playbook import run_playbook


def _silent(_):
    pass


class TestProgressMcpTool(unittest.TestCase):
    def test_progress_tool_registered_and_read_only(self):
        self.assertIn("progress", M.read_tool_names())
        self.assertNotIn("progress", M.write_tool_names())

    def test_progress_tool_no_run(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            res = M.progress(path=d)
            self.assertFalse(res["active"])
            self.assertIn("no run in progress", res["block"])

    def test_progress_threads_through_a_run(self):
        with tempfile.TemporaryDirectory() as d:
            surface = init_repo(root=d, profile="standard", assume_yes=True,
                                out=_silent) and Surface.load(d)
            # a real engine run proves the pipeline works…
            r = run_playbook(surface, ExecutionChoice(SEQUENTIAL))
            self.assertTrue(r.ok)
            # …and an actual pipeline checkpoint is the run-state the tracker reads.
            cp = PipelineCheckpoint(surface.state, "story-1")
            for phase in PIPELINE_PHASES[:4]:
                cp.mark_passed(phase)

            res = M.progress(path=d)               # active/most-recent run
            self.assertTrue(res["active"])
            self.assertEqual(res["run_id"], "story-1")
            self.assertEqual(res["done"], 4)
            self.assertEqual(res["total"], len(PIPELINE_PHASES))
            self.assertEqual(res["current"], PIPELINE_PHASES[4])
            self.assertIn("[4/7 done]", res["block"])

            # a fresh session (reloaded Surface) reads the same run-state from disk
            res2 = M.progress(path=d, run="story-1")
            self.assertEqual(res2["done"], 4)


if __name__ == "__main__":
    unittest.main()
