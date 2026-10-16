"""0.0.23 B3 — the plugin stands alone: no pip step, each hook once, no shadowed commands, true stage labels.

Four rows, one theme — what a stranger sees after `/plugin marketplace add` + `/plugin install`:

* `INSTALL-SELF-CONTAINED-PLUGIN` — the MCP server launched as bare `mokata-mcp`, which exists only
  after `pip install mokata`. Now `uvx --from mokata==<plugin version> mokata-mcp`, pinned and
  guarded as a version field (pinned in `test_hook_resolve` / `test_stage61b_release_process`).
* `HOOKS-FIRE-TWICE-WITH-PLUGIN-AND-PROJECT-WIRING` — plugin + `mokata init` ran every hook twice.
* `COMMAND-NAMES-COLLIDE-WITH-CLAUDE-CODE-BUILTINS` — project-scope `/review`, `/init`, `/bug`,
  `/upgrade`, `/resume` were shadowed by Claude Code's built-ins.
* `STAGE-LABEL-DEVELOP-ON-EVERY-EVENT` — `gate_block` / `subagent_finished` said `develop` always.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import hook_cli, packaging
from mokata.harness_setup import CLAUDE_CODE_BUILTIN_COMMANDS, _command_target_name

ROOT = Path(__file__).resolve().parents[1]
HOOKS_JSON = ROOT / "src" / "mokata" / "hooks" / "hooks.json"
TEMPLATES = ROOT / "src" / "mokata" / "templates" / "commands"


class TheMcpServerNeedsNoPip(unittest.TestCase):

    def test_the_pin_is_a_guarded_version_field_and_matches(self):
        fields = packaging.read_version_fields(str(ROOT))
        self.assertIn("plugin.json:mcp-pin", fields)
        self.assertEqual(fields["plugin.json:mcp-pin"], fields["plugin.json:version"])

    def test_doctor_names_uv_when_uvx_does_not_resolve(self):
        from mokata.govern import doctor
        from mokata.hook_wiring import HookWiringReport, WiredServer
        rep = HookWiringReport(hooks=[], servers=[WiredServer(
            where="plugin.json", name="mokata", command="uvx", resolves=False)])
        with mock.patch("mokata.hook_wiring.hook_wiring_report", return_value=rep):
            found = [f for f in doctor.hook_resolution_findings(_Surface(tempfile.gettempdir()))
                     if f.code == "plugin-mcp-unresolvable"]
        self.assertEqual(len(found), 1)
        self.assertIn("install uv", found[0].detail)


class _Surface:
    def __init__(self, root):
        self.root = root


class EachHookFiresOnce(unittest.TestCase):

    def test_every_plugin_hook_carries_the_route_flag(self):
        data = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
        cmds = [h["command"] for blocks in data["hooks"].values() for b in blocks for h in b["hooks"]]
        self.assertTrue(cmds)
        for c in cmds:
            self.assertTrue(c.rstrip().endswith(hook_cli.VIA_PLUGIN_FLAG), c)

    def test_the_plugin_route_stands_down_when_settings_wire_the_same_hook(self):
        calls = []
        with mock.patch.object(hook_cli, "_settings_route_owns", return_value=True), \
                mock.patch.dict(hook_cli._SUBCOMMANDS, {"stop": lambda a: calls.append(a) or 0}):
            self.assertEqual(hook_cli.main(["stop", "--via-plugin"]), 0)
        self.assertEqual(calls, [], "the plugin invocation must not run the handler")

    def test_the_plugin_route_runs_when_nothing_else_is_wired(self):
        calls = []
        with mock.patch.object(hook_cli, "_settings_route_owns", return_value=False), \
                mock.patch.dict(hook_cli._SUBCOMMANDS, {"stop": lambda a: calls.append(a) or 0}):
            hook_cli.main(["stop", "--via-plugin", "--x"])
        self.assertEqual(calls, [["--x"]], "the flag is stripped before the handler sees it")

    def test_the_settings_route_never_stands_down(self):
        calls = []
        with mock.patch.object(hook_cli, "_settings_route_owns", return_value=True), \
                mock.patch.dict(hook_cli._SUBCOMMANDS, {"stop": lambda a: calls.append(a) or 0}):
            hook_cli.main(["stop"])
        self.assertEqual(calls, [[]])

    def test_a_DEAD_settings_hook_does_not_silence_the_plugin(self):
        """A gate that silently does not run is worse than a duplicate."""
        from mokata.hook_wiring import WiredHook
        dead = WiredHook(surface="settings.json (project scope)", where="x", event="PreToolUse",
                         command="/nowhere/mokata-hook", program="/nowhere/mokata-hook",
                         resolves=False, args=["gate-guard"])
        with mock.patch("mokata.hook_wiring._settings_hooks", return_value=[dead]):
            self.assertFalse(hook_cli._settings_route_owns("gate-guard"))
        full = "Write|Edit|MultiEdit|NotebookEdit|Bash|Skill|Agent|Task"

        def live(matcher, event="PreToolUse"):
            return WiredHook(surface="settings.json (project scope)", where="x", event=event,
                             command=sys.executable, program=sys.executable, resolves=True,
                             args=["gate-guard"], matcher=matcher)

        with mock.patch("mokata.hook_wiring._settings_hooks", return_value=[live(full)]):
            self.assertTrue(hook_cli._settings_route_owns("gate-guard"))
            self.assertFalse(hook_cli._settings_route_owns("secret-guard"))

    def test_an_OLDER_narrower_matcher_does_not_silence_the_plugin(self):
        """0.0.23 verification B1: a 0.0.21 wiring's gate-guard sees no Skill|Agent|Task. Standing
        down for it would switch the gate off for exactly those tools."""
        from mokata.hook_wiring import WiredHook
        old = WiredHook(surface="settings.json (project scope)", where="x", event="PreToolUse",
                        command=sys.executable, program=sys.executable, resolves=True,
                        args=["gate-guard"], matcher="Write|Edit|MultiEdit|NotebookEdit|Bash")
        with mock.patch("mokata.hook_wiring._settings_hooks", return_value=[old]):
            self.assertFalse(hook_cli._settings_route_owns("gate-guard"))

    def test_the_same_subcommand_on_ANOTHER_event_does_not_count(self):
        from mokata.hook_wiring import WiredHook
        wrong = WiredHook(surface="settings.json (project scope)", where="x", event="PostToolUse",
                          command=sys.executable, program=sys.executable, resolves=True,
                          args=["secret-guard"], matcher="Write|Edit|MultiEdit|Bash")
        with mock.patch("mokata.hook_wiring._settings_hooks", return_value=[wrong]):
            self.assertFalse(hook_cli._settings_route_owns("secret-guard"))


class NoCommandIsShadowed(unittest.TestCase):

    def test_every_colliding_template_is_written_under_a_mokata_prefix(self):
        stems = sorted(p.stem for p in TEMPLATES.glob("*.md"))
        for stem in stems:
            out = _command_target_name("claude", stem + ".md")
            self.assertNotIn(out[:-3], CLAUDE_CODE_BUILTIN_COMMANDS, out)
        self.assertEqual(_command_target_name("claude", "review.md"), "mokata-review.md")
        self.assertEqual(_command_target_name("claude", "develop.md"), "develop.md",
                         "a non-colliding name is unchanged")

    def test_other_harnesses_keep_their_names(self):
        self.assertEqual(_command_target_name("cursor", "review.md"), "review.md")


class EventsCarryTheRunsStage(unittest.TestCase):

    def test_gate_block_is_stamped_with_the_runs_current_stage(self):
        from mokata import progress_events as pe
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        log = pe.ProgressLog.from_state_dir(d)
        log.append_event(pe.STAGE_ENTER, "brainstorm", run_id="r1")
        ev = pe.record_gate_block(d, "r1", "spec-gate", "a.py")
        self.assertEqual(ev["stage"], "brainstorm")
        log.append_event(pe.STAGE_ENTER, "review", run_id="r1")
        self.assertEqual(pe.record_gate_block(d, "r1", "g", "b.py")["stage"], "review")

    def test_a_run_with_no_stage_enter_keeps_the_old_label(self):
        from mokata import progress_events as pe
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        self.assertEqual(pe.record_gate_block(d, "r9", "g", "c.py")["stage"], "develop")


if __name__ == "__main__":
    unittest.main()
