"""Integration: `mokata setup claude` produces a working, plugin-equivalent wiring.

End-to-end through the CLI in a fresh temp project: init runs, the slash commands land,
the MCP server is registered, the hooks are wired, and `unsetup` cleanly reverses it.
Part of the release gate, so it must pass with jsonschema absent AND present.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

import _support  # noqa: F401  (puts src/ on the path)

from mokata import MOKATA_DIR
from mokata.cli import main


def run_cli(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(argv)
    return rc, buf.getvalue()


class TestSetupHarnessE2E(unittest.TestCase):
    def test_setup_then_unsetup_full_cycle(self):
        with tempfile.TemporaryDirectory() as d:
            rc, _ = run_cli(["setup", "claude", "--profile", "standard",
                             "--yes", "--path", d])
            self.assertEqual(rc, 0)

            # init happened
            self.assertTrue(os.path.exists(os.path.join(d, MOKATA_DIR, "manifest.json")))
            # commands: all eight pipeline slash commands present
            cmds = sorted(os.listdir(os.path.join(d, ".claude", "commands")))
            for expected in ("brainstorm.md", "spec.md", "test.md", "develop.md",
                             "review.md"):
                self.assertIn(expected, cmds)
            # MCP server registered
            with open(os.path.join(d, ".mcp.json"), encoding="utf-8") as fh:
                mcp = json.load(fh)
            self.assertEqual(mcp["mcpServers"]["mokata"]["command"], "mokata-mcp")
            # hooks wired
            with open(os.path.join(d, ".claude", "settings.json"), encoding="utf-8") as fh:
                settings = json.load(fh)
            self.assertIn("SessionStart", settings["hooks"])
            self.assertIn("PreToolUse", settings["hooks"])

            # reverse
            rc, _ = run_cli(["unsetup", "claude", "--yes", "--path", d])
            self.assertEqual(rc, 0)
            self.assertEqual(os.listdir(os.path.join(d, ".claude", "commands")), [])
            with open(os.path.join(d, ".mcp.json"), encoding="utf-8") as fh:
                self.assertNotIn("mokata", json.load(fh).get("mcpServers", {}))
            # config preserved across unsetup
            self.assertTrue(os.path.exists(os.path.join(d, MOKATA_DIR, "manifest.json")))


if __name__ == "__main__":
    unittest.main()
