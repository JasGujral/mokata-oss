"""`mokata setup` / `mokata unsetup` — wire mokata into a harness without the plugin."""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

import _support  # noqa: F401  (puts src/ on the path)

from mokata import MOKATA_DIR
from mokata.cli import main
from mokata.harness_setup import (
    MCP_SERVER_NAME,
    apply_setup,
    apply_unsetup,
    plan_setup,
    plan_unsetup,
    resolve_targets,
    setup_harness,
    unsetup_harness,
)


def run_cli(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(argv)
    return rc, buf.getvalue()


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


class TestSetupProject(unittest.TestCase):
    def test_full_setup_writes_all_three_pieces(self):
        with tempfile.TemporaryDirectory() as d:
            res = setup_harness("claude", root=d, scope="project",
                                profile="standard", assume_yes=True, out=lambda _: None)
            self.assertFalse(res.aborted)
            # init
            self.assertTrue(os.path.exists(os.path.join(d, MOKATA_DIR, "manifest.json")))
            # commands
            cmds = os.path.join(d, ".claude", "commands")
            self.assertTrue(os.path.isdir(cmds))
            self.assertIn("brainstorm.md", os.listdir(cmds))
            # mcp
            mcp = _read(os.path.join(d, ".mcp.json"))
            self.assertIn(MCP_SERVER_NAME, mcp["mcpServers"])
            self.assertEqual(mcp["mcpServers"][MCP_SERVER_NAME]["command"], "mokata-mcp")
            # hooks
            settings = _read(os.path.join(d, ".claude", "settings.json"))
            self.assertIn("SessionStart", settings["hooks"])
            self.assertIn("PreToolUse", settings["hooks"])

    def test_no_hooks_flag_skips_settings(self):
        with tempfile.TemporaryDirectory() as d:
            setup_harness("claude", root=d, with_hooks=False,
                          assume_yes=True, out=lambda _: None)
            self.assertFalse(os.path.exists(os.path.join(d, ".claude", "settings.json")))
            self.assertTrue(os.path.exists(os.path.join(d, ".mcp.json")))

    def test_profile_passthrough(self):
        with tempfile.TemporaryDirectory() as d:
            setup_harness("claude", root=d, profile="minimal",
                          assume_yes=True, out=lambda _: None)
            manifest = _read(os.path.join(d, MOKATA_DIR, "manifest.json"))
            self.assertEqual(manifest["profile"], "minimal")

    def test_aborts_without_confirmation(self):
        with tempfile.TemporaryDirectory() as d:
            res = setup_harness("claude", root=d, assume_yes=False,
                                confirm=lambda _: False, out=lambda _: None)
            self.assertTrue(res.aborted)
            self.assertFalse(os.path.exists(os.path.join(d, ".mcp.json")))


class TestIdempotencyAndMerge(unittest.TestCase):
    def test_rerun_does_not_duplicate_hooks(self):
        with tempfile.TemporaryDirectory() as d:
            for _ in range(2):
                setup_harness("claude", root=d, assume_yes=True, out=lambda _: None)
            settings = _read(os.path.join(d, ".claude", "settings.json"))
            self.assertEqual(len(settings["hooks"]["PreToolUse"]), 1)
            self.assertEqual(len(settings["hooks"]["SessionStart"]), 1)

    def test_merge_preserves_existing_entries(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".claude"))
            with open(os.path.join(d, ".mcp.json"), "w", encoding="utf-8") as fh:
                json.dump({"mcpServers": {"other": {"command": "other-mcp"}}}, fh)
            with open(os.path.join(d, ".claude", "settings.json"), "w", encoding="utf-8") as fh:
                json.dump({
                    "permissions": {"allow": ["Read"]},
                    "hooks": {"PreToolUse": [
                        {"matcher": "Bash",
                         "hooks": [{"type": "command", "command": "echo keepme"}]}]},
                }, fh)

            setup_harness("claude", root=d, assume_yes=True, out=lambda _: None)

            mcp = _read(os.path.join(d, ".mcp.json"))
            self.assertIn("other", mcp["mcpServers"])
            self.assertIn(MCP_SERVER_NAME, mcp["mcpServers"])
            settings = _read(os.path.join(d, ".claude", "settings.json"))
            self.assertIn("permissions", settings)
            self.assertEqual(len(settings["hooks"]["PreToolUse"]), 2)  # keepme + mokata


class TestUnsetup(unittest.TestCase):
    def test_unsetup_removes_only_mokata(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".claude"))
            with open(os.path.join(d, ".mcp.json"), "w", encoding="utf-8") as fh:
                json.dump({"mcpServers": {"other": {"command": "other-mcp"}}}, fh)
            with open(os.path.join(d, ".claude", "settings.json"), "w", encoding="utf-8") as fh:
                json.dump({
                    "permissions": {"allow": ["Read"]},
                    "hooks": {"PreToolUse": [
                        {"matcher": "Bash",
                         "hooks": [{"type": "command", "command": "echo keepme"}]}]},
                }, fh)
            setup_harness("claude", root=d, assume_yes=True, out=lambda _: None)

            res = unsetup_harness("claude", root=d, assume_yes=True, out=lambda _: None)
            self.assertFalse(res.aborted)

            mcp = _read(os.path.join(d, ".mcp.json"))
            self.assertEqual(list(mcp["mcpServers"]), ["other"])  # mokata gone, other kept
            settings = _read(os.path.join(d, ".claude", "settings.json"))
            self.assertIn("permissions", settings)
            self.assertEqual(len(settings["hooks"]["PreToolUse"]), 1)  # only keepme
            # copied commands removed; .mokata config left intact
            self.assertEqual(os.listdir(os.path.join(d, ".claude", "commands")), [])
            self.assertTrue(os.path.exists(os.path.join(d, MOKATA_DIR, "manifest.json")))

    def test_unsetup_when_nothing_wired_is_safe(self):
        with tempfile.TemporaryDirectory() as d:
            res = unsetup_harness("claude", root=d, assume_yes=True, out=lambda _: None)
            self.assertFalse(res.aborted)
            self.assertEqual(res.removed, [])


class TestUserScope(unittest.TestCase):
    def test_user_scope_targets_home_and_dotclaude_json(self):
        with tempfile.TemporaryDirectory() as home:
            t = resolve_targets("user", root=".", home=home)
            self.assertEqual(t.commands_dir, __import__("pathlib").Path(home)
                             .resolve() / ".claude" / "commands")
            self.assertEqual(t.mcp_path, __import__("pathlib").Path(home)
                             .resolve() / ".claude.json")

    def test_user_scope_apply_writes_under_home(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            plan = plan_setup("claude", root=d, scope="user", home=home)
            apply_setup(plan, assume_yes=True, out=lambda _: None)
            self.assertTrue(os.path.isdir(os.path.join(home, ".claude", "commands")))
            self.assertTrue(os.path.exists(os.path.join(home, ".claude.json")))
            # project root still gets .mokata (init is project-local)
            self.assertTrue(os.path.exists(os.path.join(d, MOKATA_DIR, "manifest.json")))


class TestSetupCLI(unittest.TestCase):
    def test_cli_setup_and_unsetup_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            rc, _ = run_cli(["setup", "claude", "--yes", "--path", d])
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.exists(os.path.join(d, ".mcp.json")))
            rc, _ = run_cli(["unsetup", "claude", "--yes", "--path", d])
            self.assertEqual(rc, 0)

    def test_cli_rejects_unknown_harness(self):
        # `gemini` became a real harness in Stage 63; use a name that will never be one.
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit):
                run_cli(["setup", "notanagent", "--yes", "--path", d])


if __name__ == "__main__":
    unittest.main()
