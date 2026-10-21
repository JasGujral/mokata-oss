"""0.0.25 I-04 — PLUGIN-ROUTE-FIRST-SESSION-INSTALL: the plugin route reaches the CLI route's end
state by CHECK ON MISS, never by installing from a hook.

Claude Code has no plugin install hook and a plugin's `dependencies` are other plugins, so a user
who installs mokata from the marketplace and initializes `full` has none of full's optional
dependencies. What must be true for that user:

  * their first session's briefing says what full mode still lacks and names the ONE command that
    gets them to the same end state as `mokata setup claude --mode full`;
  * that command is real (the CLI parser accepts it) and, on a machine with no `mokata` CLI, is
    prefixed with the documented install;
  * nothing is installed and no process is spawned by the hook (P2 / DB.S4: consent stays human);
  * a complete repo, a seatbelt/standard repo, and a human who already said no hear nothing.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import _support  # noqa: F401

import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from mokata import adoption_modes as AM
from mokata import hook_cli
from mokata.init import init_repo

ROOT = Path(__file__).resolve().parents[1]

_ALL_PRESENT = {"embeddings": lambda: True, "code-graph": lambda: True}
_NONE_PRESENT = {"embeddings": lambda: False, "code-graph": lambda: False}


def _repo(test, profile):
    d = tempfile.mkdtemp(prefix="mk-i04-")
    test.addCleanup(shutil.rmtree, d, True)
    init_repo(root=d, profile=profile, assume_yes=True, out=lambda *_: None)
    return d


def _manifest(d):
    with open(os.path.join(d, ".mokata", "manifest.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _isolated_home(test):
    """Declines live in the USER's profile — never let a test read or write the real one."""
    home = tempfile.mkdtemp(prefix="mk-i04-home-")
    test.addCleanup(shutil.rmtree, home, True)
    patcher = mock.patch.dict(os.environ, {"HOME": home, "USERPROFILE": home})
    patcher.start()
    test.addCleanup(patcher.stop)
    return home


def _briefing(d, *, presence, mokata_on_path):
    """Run the REAL plugin SessionStart entry (`mokata-hook session-start --via-plugin`) on `d`
    and return the injected context. Only the environment probes are substituted."""
    real = AM.first_session_check

    def check(root, **kw):
        return real(root, presence=presence, mokata_on_path=mokata_on_path, **kw)

    buf = io.StringIO()
    with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": d}), \
            mock.patch.object(hook_cli, "_payload_from_stdin",
                              return_value={"cwd": d, "session_id": "S-i04",
                                            "source": "startup"}), \
            mock.patch.object(AM, "first_session_check", side_effect=check), \
            redirect_stdout(buf):
        rc = hook_cli.main(["session-start", "--plugin-root", d, "--via-plugin"])
    assert rc == 0, rc
    return json.loads(buf.getvalue())["hookSpecificOutput"]["additionalContext"]


class PluginOnlyFullRepoIsToldTheOneCommand(unittest.TestCase):
    def setUp(self):
        _isolated_home(self)
        self.d = _repo(self, "full")

    def test_the_first_briefing_names_what_is_missing_and_the_one_command(self):
        ctx = _briefing(self.d, presence=_NONE_PRESENT, mokata_on_path=False)
        self.assertIn("full mode is not complete", ctx)
        self.assertIn("semantic memory model", ctx)
        self.assertIn("code-review-graph", ctx)
        self.assertIn("`uv tool install mokata && mokata setup claude --mode full`", ctx)

    def test_with_the_cli_installed_the_command_is_just_setup(self):
        ctx = _briefing(self.d, presence=_NONE_PRESENT, mokata_on_path=True)
        self.assertIn("`mokata setup claude --mode full`", ctx)
        self.assertNotIn("uv tool install", ctx)

    def test_the_line_keeps_consent_with_the_human(self):
        line = AM.first_session_check(self.d, presence=_NONE_PRESENT,
                                      mokata_on_path=True).line
        self.assertIn("asks once before installing", line)
        self.assertIn("Do not run it for them", line)

    def test_the_line_fits_inside_the_one_briefing_budget(self):
        from mokata.bootstrap import estimate_tokens
        from mokata.context_budget import ContextBudget
        ctx = _briefing(self.d, presence=_NONE_PRESENT, mokata_on_path=False)
        self.assertLessEqual(estimate_tokens(ctx), ContextBudget().briefing_tokens)

    def test_the_hook_installs_nothing_and_spawns_nothing(self):
        def boom(*a, **k):
            raise AssertionError(f"SessionStart spawned a process: {a!r}")
        with mock.patch("mokata.extras_install.install_extras", side_effect=boom), \
                mock.patch.object(subprocess, "run", side_effect=boom), \
                mock.patch.object(subprocess, "Popen", side_effect=boom):
            line = AM.first_session_check(self.d, presence=_NONE_PRESENT).line
        self.assertIsNotNone(line)

    def test_the_plugin_route_is_told_the_command_moves_it_to_the_cli_route(self):
        """The plugin's MCP server runs as `uvx --from mokata==<v> mokata-mcp` — no extras — so
        the plugin route cannot carry the semantic model; saying so is the honest end state."""
        with open(ROOT / ".claude-plugin" / "plugin.json", encoding="utf-8") as fh:
            mcp = json.load(fh)["mcpServers"]["mokata"]
        self.assertEqual("uvx", mcp["command"])
        self.assertFalse(any("[" in a or a == "--with" for a in mcp["args"]), mcp["args"])
        ctx = _briefing(self.d, presence=_NONE_PRESENT, mokata_on_path=True)
        self.assertIn("moves this repo to the CLI route", ctx)
        self.assertIn(f"claude plugin disable {AM.PLUGIN_ID} --scope local", ctx)

    def test_the_cli_route_line_does_not_talk_about_the_plugin(self):
        line = AM.first_session_check(self.d, presence=_NONE_PRESENT, mokata_on_path=True,
                                      via_plugin=False).line
        self.assertIn("`mokata setup claude --mode full`", line)
        self.assertNotIn("plugin", line)

    def test_the_plugin_id_is_the_one_the_readme_installs(self):
        with open(ROOT / "README.md", encoding="utf-8") as fh:
            self.assertIn(f"/plugin install {AM.PLUGIN_ID}", fh.read())

    def test_the_named_command_is_a_real_cli_invocation(self):
        from mokata.cli import build_parser
        for on_path in (True, False):
            cmd = AM.setup_command_for("full", mokata_on_path=on_path)
            argv = cmd.split("&&")[-1].split()
            self.assertEqual("mokata", argv[0])
            ns = build_parser().parse_args(argv[1:])
            self.assertEqual(("claude", "full"), (ns.harness, ns.mode))


class NothingIsSaidWhenNothingIsOwed(unittest.TestCase):
    def setUp(self):
        _isolated_home(self)

    def test_a_complete_full_repo_hears_nothing(self):
        d = _repo(self, "full")
        data = _manifest(d)
        data.setdefault("settings", {}).setdefault("memory", {})["embedder"] = "auto"
        check = AM.first_session_check(d, data=data, presence=_ALL_PRESENT,
                                       mokata_on_path=True)
        self.assertEqual((), check.missing)
        self.assertIsNone(check.line)

    def test_a_standard_repo_never_hears_it_even_with_nothing_installed(self):
        d = _repo(self, "standard")
        ctx = _briefing(d, presence=_NONE_PRESENT, mokata_on_path=False)
        self.assertNotIn("mode is not complete", ctx)
        self.assertNotIn("setup claude --mode", ctx)

    def test_a_human_who_declined_is_not_nagged(self):
        from mokata.extras_install import record_extra_decline
        d = _repo(self, "full")
        record_extra_decline(d, AM.MODE_DEPS_KEY)
        check = AM.first_session_check(d, presence=_NONE_PRESENT, mokata_on_path=True)
        self.assertTrue(check.missing, "the gap is still MEASURED")
        self.assertTrue(check.silenced)
        self.assertIsNone(check.line)
        ctx = _briefing(d, presence=_NONE_PRESENT, mokata_on_path=True)
        self.assertNotIn("mode is not complete", ctx)


class TheModeIsDerivedNotRecorded(unittest.TestCase):
    def test_full_profile_is_full_mode(self):
        self.assertEqual("full", AM.mode_for_manifest({"profile": "full"}))

    def test_standard_with_a_semantic_embedder_is_memory_mode(self):
        data = {"profile": "standard", "settings": {"memory": {"embedder": "auto"}}}
        self.assertEqual("memory", AM.mode_for_manifest(data))
        check = AM.first_session_check("/nonexistent", data=data, presence=_NONE_PRESENT,
                                       mokata_on_path=True, user_home=tempfile.gettempdir())
        self.assertEqual(("semantic memory model",), check.missing)
        self.assertIn("`mokata setup claude --mode memory`", check.line)

    def test_plain_standard_hashing_and_minimal_imply_no_mode_dependencies(self):
        for data in ({"profile": "standard"},
                     {"profile": "standard", "settings": {"memory": {"embedder": "hashing"}}},
                     {"profile": "minimal"}, {}):
            self.assertIsNone(AM.mode_for_manifest(data), data)

    def test_the_plugin_manifest_records_no_mode(self):
        """`userConfig` was evaluated and not used (its picker needs Claude Code >= 2.1.271, far
        above mokata's floor, and the mode would become a second persisted axis)."""
        with open(ROOT / ".claude-plugin" / "plugin.json",
                  encoding="utf-8") as fh:
            self.assertNotIn("userConfig", json.load(fh))

    def test_the_plugin_session_start_hook_reaches_this_check(self):
        with open(ROOT / "src" / "mokata" / "hooks" / "hooks.json",
                  encoding="utf-8") as fh:
            hooks = json.load(fh)["hooks"]["SessionStart"]
        cmds = [h["command"] for b in hooks for h in b["hooks"]]
        self.assertTrue(any(" session-start " in c and "--via-plugin" in c for c in cmds), cmds)


if __name__ == "__main__":
    unittest.main()
