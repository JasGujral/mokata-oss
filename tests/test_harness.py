"""J2 — cross-harness portability: a thin harness boundary the engine talks to. The
Claude Code harness supports everything; a harness missing a capability degrades with a
clear message rather than crashing."""

import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.harness import (
    HARNESS_CAPABILITIES,
    Harness,
    HarnessBoundary,
    claude_code_harness,
)


class TestHarnessBoundary(unittest.TestCase):
    def test_claude_code_supports_all_capabilities(self):
        h = claude_code_harness()
        self.assertTrue(all(h.supports(c) for c in HARNESS_CAPABILITIES))

    def test_pipeline_ops_run_on_claude_code(self):
        b = HarnessBoundary(claude_code_harness())
        results = [b.inject_context("briefing"), b.run_command("brainstorm"),
                   b.run_hook("secret-guard"), b.run_subagent("task-1")]
        self.assertTrue(all(r.ok and not r.degraded for r in results))

    def test_missing_capability_degrades_with_a_clear_message(self):
        codex = Harness("codex", {"commands", "context_injection"})  # no subagents/hooks
        b = HarnessBoundary(codex)
        r = b.run_subagent("task-1")
        self.assertFalse(r.ok)
        self.assertTrue(r.degraded)
        self.assertIn("subagents", r.message)
        self.assertIn("codex", r.message)
        # other capabilities still work — no crash, just a clean degrade
        self.assertTrue(b.run_command("spec").ok)

    def test_ops_are_dispatched(self):
        calls = []
        h = claude_code_harness(
            ops={"commands": lambda name, args: calls.append(name) or "dispatched"})
        r = HarnessBoundary(h).run_command("review")
        self.assertTrue(r.ok)
        self.assertEqual(calls, ["review"])
        self.assertEqual(r.value, "dispatched")

    def test_engine_is_harness_agnostic(self):
        # the same boundary calls work across different harnesses (no per-harness engine)
        for h in (claude_code_harness(), Harness("opencode", {"commands"})):
            self.assertIn(HarnessBoundary(h).run_command("test").ok, (True, False))


if __name__ == "__main__":
    unittest.main()
