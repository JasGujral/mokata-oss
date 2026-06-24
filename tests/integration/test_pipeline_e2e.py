"""Stage 20 — whole-pipeline end-to-end integration.

Drives ONE story through the REAL engine via the existing playbook (reused, not forked) and
asserts the modules are wired together: brainstorm -> approve -> completeness gate
(blocked-then-passing) -> RED tests -> RED-before-GREEN implement -> two-stage review ->
human-gated memory write -> audit ledger. The ledger is reloaded from disk to prove the
run was durably recorded.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import tempfile
import unittest

from _support import write_sample_repo  # noqa: F401  (import = path-shim side effect)

from mokata.config import Surface
from mokata.execmode import SEQUENTIAL, ExecutionChoice
from mokata.govern import AuditLedger
from mokata.init import init_repo
from mokata.playbook import run_playbook


def _silent(_):
    pass


def _init(d, profile="standard"):
    init_repo(root=d, profile=profile, assume_yes=True, out=_silent)
    return Surface.load(d)


class TestWholePipelineEndToEnd(unittest.TestCase):
    def test_every_stage_gate_fires_and_is_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _init(d, "standard")
            result = run_playbook(surface, ExecutionChoice(SEQUENTIAL))

            # the whole chain held together
            self.assertTrue(result.ok, result.checks)
            for key in ("brainstorm_approved", "gate_blocked_initially",
                        "gate_passed_after_tests", "red_before_green",
                        "review_passed", "knowledge_used"):
                self.assertTrue(result.checks.get(key), f"{key} not satisfied")

            # the gate truly blocked first, then passed — both observed in one run
            self.assertTrue(result.checks["gate_blocked_initially"])
            self.assertTrue(result.checks["gate_passed_after_tests"])

            # human-gated memory write committed on a memory-enabled profile
            self.assertTrue(result.checks["memory_enabled"])
            self.assertTrue(result.checks["memory_written"])

    def test_audit_ledger_recorded_the_pipeline_and_survives_reload(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _init(d, "standard")
            run_playbook(surface, ExecutionChoice(SEQUENTIAL))

            ledger_path = os.path.join(surface.mokata_dir, "audit", "ledger.jsonl")
            self.assertTrue(os.path.exists(ledger_path))

            # reload from disk (a fresh ledger object) — the steps persisted
            reloaded = AuditLedger.from_mokata_dir(surface.mokata_dir)
            steps = [e.get("step") for e in reloaded.entries()
                     if e.get("kind") == "playbook"]
            for expected in ("brainstorm", "gate_block", "gate_pass", "done"):
                self.assertIn(expected, steps)


if __name__ == "__main__":
    unittest.main()
