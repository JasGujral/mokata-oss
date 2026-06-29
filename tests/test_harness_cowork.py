"""Stage 52b — the Cowork install path, modeled HONESTLY through the harness boundary.

Cowork supports plugins, so /mokata:* commands + the SessionStart briefing + subagents work;
but its PreToolUse hook enforcement is NOT guaranteed, so the cowork harness declares
`hooks` = False and that gate degrades CLEARLY (durable-write protection there relies on the
gated CLI/MCP WriteGate, not the hook). The plugin bundle (marketplace + commands) is
harness-agnostic, so it's usable in any plugin host including Cowork.
"""

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.cli import main
from mokata.harness import (
    HarnessBoundary,
    available_harnesses,
    capability_matrix,
    cowork_harness,
)

ROOT = os.path.join(os.path.dirname(__file__), "..")


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


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


class TestCoworkHarness(unittest.TestCase):
    def test_declares_only_real_capabilities(self):
        h = cowork_harness()
        self.assertEqual(h.name, "cowork")
        self.assertTrue(h.supports("commands"))
        self.assertTrue(h.supports("context_injection"))
        self.assertTrue(h.supports("subagents"))
        self.assertFalse(h.supports("hooks"))          # NOT assumed — honest

    def test_missing_hook_capability_degrades_clearly(self):
        b = HarnessBoundary(cowork_harness())
        self.assertTrue(b.run_command("brainstorm").ok)
        self.assertTrue(b.inject_context("briefing").ok)
        self.assertTrue(b.run_subagent("t1").ok)
        hook = b.run_hook("secret-guard")
        self.assertFalse(hook.ok)                       # the PreToolUse hook degrades
        self.assertTrue(hook.degraded)
        self.assertIn("hooks", hook.message)            # names the missing capability
        self.assertIn("degraded", hook.message)

    def test_in_registry_and_matrix(self):
        self.assertIn("cowork", available_harnesses())
        m = capability_matrix()
        self.assertTrue(m["cowork"]["commands"])
        self.assertTrue(m["cowork"]["subagents"])
        self.assertFalse(m["cowork"]["hooks"])
        # claude/codex profiles untouched
        self.assertTrue(all(m["claude"].values()))
        self.assertFalse(m["codex"]["subagents"])


class TestCoworkHarnessCLI(unittest.TestCase):
    def test_harness_lists_cowork_with_hooks_no(self):
        rc, out = run_cli(["harness"])
        self.assertEqual(rc, 0)
        self.assertIn("cowork", out)
        block = out[out.index("'cowork'"):]
        self.assertIn("[no ] hooks", block)            # degrade shown plainly
        self.assertIn("[yes] commands", block)

    def test_harness_show_only_cowork(self):
        rc, out = run_cli(["harness", "cowork"])
        self.assertEqual(rc, 0)
        self.assertIn("cowork", out)
        self.assertNotIn("'claude'", out)


class TestPluginBundleUsableInCowork(unittest.TestCase):
    """The plugin bundle is harness-agnostic — a plugin host (Claude Code OR Cowork) loads the
    same marketplace + commands. Validate the manifest references real command templates."""

    def test_marketplace_lists_mokata(self):
        data = json.loads(_read(".claude-plugin/marketplace.json"))
        names = [p.get("name") for p in data.get("plugins", [])]
        self.assertIn("mokata", names)

    def test_plugin_commands_dir_exists_with_templates(self):
        plugin = json.loads(_read(".claude-plugin/plugin.json"))
        cmds_rel = plugin["commands"].lstrip("./")
        cmds_dir = os.path.join(ROOT, cmds_rel)
        self.assertTrue(os.path.isdir(cmds_dir))
        mds = [f for f in os.listdir(cmds_dir) if f.endswith(".md")]
        self.assertTrue(mds)                            # /mokata:* commands ship in the bundle

    def test_how_to_doc_exists_and_is_honest(self):
        doc = _read("docs/how-to/use-mokata-in-cowork.md")
        self.assertIn("/plugin install mokata@mostack", doc)   # the install step
        self.assertIn("PreToolUse", doc)                       # names the degrading hook
        self.assertIn("WriteGate", doc)                        # the fallback it degrades to


if __name__ == "__main__":
    unittest.main()
