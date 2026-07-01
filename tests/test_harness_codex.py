"""Stage 52a — a real SECOND harness adapter (codex) behind the existing boundary.

The codex harness declares ONLY the capabilities it actually supports (commands,
context_injection); a request for a capability it lacks (hooks, subagents) degrades CLEARLY
via the boundary — never pretends, never silently no-ops a gate. `mokata harness` lists the
matrix; `mokata setup codex` generalizes setup (capability-aware) with the claude path
unchanged.
"""

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.cli import main
from mokata.harness import (
    HARNESS_CAPABILITIES,
    HarnessBoundary,
    available_harnesses,
    capability_matrix,
    codex_harness,
    get_harness,
)
from mokata.harness_setup import plan_setup, render_setup_plan, resolve_targets, setup_harness


def run_cli(argv):
    buf = io.StringIO()
    old = sys.stdin
    sys.stdin = io.StringIO("")
    try:
        with redirect_stdout(buf):
            rc = main(argv)
    finally:
        sys.stdin = old
    return rc, buf.getvalue()


def silent(_):
    pass


class TestCodexHarness(unittest.TestCase):
    def test_declares_only_real_capabilities(self):
        h = codex_harness()
        self.assertEqual(h.name, "codex")
        self.assertTrue(h.supports("commands"))
        self.assertTrue(h.supports("context_injection"))
        self.assertFalse(h.supports("hooks"))
        self.assertFalse(h.supports("subagents"))

    def test_missing_capability_degrades_clearly(self):
        b = HarnessBoundary(codex_harness())
        ok = b.run_command("brainstorm")
        self.assertTrue(ok.ok and not ok.degraded)             # supported → runs
        hook = b.run_hook("secret-guard")
        self.assertFalse(hook.ok)                              # unsupported → degrades
        self.assertTrue(hook.degraded)
        self.assertIn("hooks", hook.message)                  # names the missing capability
        self.assertIn("degraded", hook.message)
        sub = b.run_subagent("t1")
        self.assertFalse(sub.ok)
        self.assertIn("subagents", sub.message)

    def test_registry_and_matrix(self):
        # Stage 63 extended the registry with cursor/copilot/windsurf/gemini/aider; the
        # reference (claude) + the Stage-52 adapters (codex/cowork) stay first + unchanged.
        self.assertEqual(available_harnesses(),
                         ["claude", "codex", "cowork", "cursor", "copilot", "windsurf",
                          "gemini", "aider"])
        with self.assertRaises(ValueError):
            get_harness("nope")
        matrix = capability_matrix()
        self.assertTrue(all(matrix["claude"][c] for c in HARNESS_CAPABILITIES))  # reference
        self.assertFalse(matrix["codex"]["hooks"])
        self.assertTrue(matrix["codex"]["commands"])


class TestHarnessCLI(unittest.TestCase):
    def test_lists_all_harnesses_and_matrix(self):
        rc, out = run_cli(["harness"])
        self.assertEqual(rc, 0)
        self.assertIn("claude-code", out)       # reference (back-compat with the J2 test)
        self.assertIn("codex", out)
        self.assertIn("subagents", out)
        # codex's lacking capabilities are shown as 'no'
        codex_block = out[out.index("'codex'"):]
        self.assertIn("[no ] hooks", codex_block)
        self.assertIn("[no ] subagents", codex_block)

    def test_show_one_harness(self):
        rc, out = run_cli(["harness", "codex"])
        self.assertEqual(rc, 0)
        self.assertIn("codex", out)
        self.assertNotIn("'claude'", out)        # only the requested one

    def test_unknown_harness_errors(self):
        rc, _ = run_cli(["harness", "borg"])
        self.assertEqual(rc, 1)


class TestCodexSetup(unittest.TestCase):
    def test_resolve_targets_codex_uses_prompts_dir_no_mcp(self):
        t = resolve_targets("project", root="/r", harness="codex")
        self.assertTrue(str(t.commands_dir).endswith(os.path.join(".codex", "prompts")))
        self.assertIsNone(t.mcp_path)            # codex MCP auto-wire not supported

    def test_plan_states_the_degrade_clearly(self):
        with tempfile.TemporaryDirectory() as d:
            plan = plan_setup("codex", root=d)
            self.assertFalse(plan.with_hooks)            # codex lacks hooks
            self.assertFalse(plan.mcp_auto)
            self.assertIn("hooks", plan.unsupported)
            self.assertIn("subagents", plan.unsupported)
            text = render_setup_plan(plan)
            self.assertIn("lacks", text)
            self.assertIn("codex", text)

    def test_setup_codex_wires_commands_only_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            res = setup_harness("codex", root=d, assume_yes=True, out=silent)
            self.assertFalse(res.aborted)
            prompts = os.path.join(d, ".codex", "prompts")
            self.assertTrue(os.path.isdir(prompts))
            self.assertTrue(any(f.endswith(".md") for f in os.listdir(prompts)))
            self.assertFalse(os.path.exists(os.path.join(d, ".mcp.json")))      # no MCP
            self.assertFalse(os.path.exists(os.path.join(d, ".codex", "settings.json")))  # no hooks
            n_before = len(os.listdir(prompts))
            setup_harness("codex", root=d, assume_yes=True, out=silent)          # idempotent
            self.assertEqual(len(os.listdir(prompts)), n_before)

    def test_setup_codex_is_gated(self):
        with tempfile.TemporaryDirectory() as d:
            res = setup_harness("codex", root=d, assume_yes=False,
                                confirm=lambda _t: False, out=silent)
            self.assertTrue(res.aborted)
            self.assertEqual(res.touched, [])

    def test_claude_path_unchanged_still_writes_all_three(self):
        with tempfile.TemporaryDirectory() as d:
            setup_harness("claude", root=d, assume_yes=True, out=silent)
            self.assertTrue(os.path.isdir(os.path.join(d, ".claude", "commands")))
            self.assertTrue(os.path.exists(os.path.join(d, ".mcp.json")))           # MCP wired
            self.assertTrue(os.path.exists(os.path.join(d, ".claude", "settings.json")))  # hooks


if __name__ == "__main__":
    unittest.main()
