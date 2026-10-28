"""0.0.26 Lane R — R6: an explicit model tier per role, floors for reviewers, recorded per run.

Business-level: the REAL gate-guard / SubagentStop hook mains, driven with the payloads Claude Code
sends for an Agent spawn and a subagent stop, against an initialised mokata repo with a bound run.
"""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import hook_cli
from mokata import model_tiers as MT
from mokata import run_intent as RI
from mokata.cli import main as cli_main
from mokata.config import Surface
from mokata.govern.ledger import AuditLedger
from mokata.govern.resume import PipelineCheckpoint
from mokata.init import init_repo


def _silent(_):
    pass


def _hook(fn, payload):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(hook_cli, "_read_stdin_bounded", return_value=json.dumps(payload)), \
            redirect_stdout(out), redirect_stderr(err):
        code = fn([])
    return code, out.getvalue(), err.getvalue()


def _cli(*argv):
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(io.StringIO()):
        code = cli_main(list(argv))
    return code, out.getvalue()


class _Run(unittest.TestCase):

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.d = os.path.realpath(self._t.name)
        init_repo(root=self.d, profile="standard", assume_yes=True, out=_silent)
        PipelineCheckpoint(Surface.load(self.d).state, "r1").ensure_registered()
        RI.bind_registered(self.d, "S1", "r1")

    def tearDown(self):
        self._t.cleanup()

    def spawn(self, role=None, model=None, tool="Agent"):
        tool_input = {"description": (f"[mokata:{role}] review the diff" if role else "look around"),
                      "prompt": "brief", "subagent_type": "general-purpose"}
        if model is not None:
            tool_input["model"] = model
        return _hook(hook_cli.gate_guard_main,
                     {"cwd": self.d, "session_id": "S1", "hook_event_name": "PreToolUse",
                      "tool_name": tool, "tool_input": tool_input})


class R6EveryRoleNamesATier(_Run):

    def test_R6_each_role_definition_names_a_tier_and_the_floors_hold(self):
        self.assertEqual(set(MT.ROLES), set(MT.DEFAULTS))
        for role, tier in MT.DEFAULTS.items():
            self.assertIn(tier, MT.TIERS, role)
            self.assertEqual("", MT.tier_problem(role, tier), role)
        self.assertEqual("sonnet", MT.FLOORS["reviewer"])        # mid-tier floor for reviewers
        self.assertEqual("opus", MT.DEFAULTS["final-reviewer"])  # top tier for the final review
        self.assertEqual(MT.TIERS[-1], MT.FLOORS["final-reviewer"])

    def test_R6_every_dispatching_skill_names_its_roles_tier(self):
        from mokata.skills import get_skill, render_skill
        for skill, roles in (("develop", ("implementer", "explorer")),
                             ("review", ("reviewer", "final-reviewer")),
                             ("debug", ("debugger",)), ("brainstorm", ("explorer",))):
            text = render_skill(get_skill(skill))
            for role in roles:
                with self.subTest(skill=skill, role=role):
                    self.assertIn(f'`model: "{MT.DEFAULTS[role]}"`', text)
                    self.assertIn(f"[mokata:{role}]", text)

    def test_R6_a_role_spawn_must_name_its_tier_and_respect_the_floor(self):
        for role, model, why in (("reviewer", None, "must name its model tier"),
                                 ("reviewer", "inherit", "must name its model tier"),
                                 ("reviewer", "haiku", "floor is sonnet"),
                                 ("final-reviewer", "sonnet", "floor is opus")):
            with self.subTest(role=role, model=model):
                code, _, err = self.spawn(role, model)
                self.assertEqual(2, code, err)
                self.assertIn("model-tier", err)
                self.assertIn(why, err)
        self.assertEqual(0, self.spawn("reviewer", "opus")[0])
        self.assertEqual(0, self.spawn(None, None)[0], "an untagged spawn is not policed")

    def test_R6_the_run_record_carries_the_tier_and_the_ledger_a_row(self):
        self.assertEqual(0, self.spawn("implementer", "sonnet")[0])
        self.assertEqual(0, self.spawn("final-reviewer", "opus", tool="Task")[0])
        rec = MT.read_record(self.d, "r1")
        self.assertEqual([("implementer", "sonnet"), ("final-reviewer", "opus")],
                         [(r["role"], r["model"]) for r in rec["spawns"]])
        rows = [e for e in AuditLedger.from_mokata_dir(os.path.join(self.d, ".mokata")).entries()
                if e.get("kind") == MT.LEDGER_KIND]
        self.assertEqual(["implementer", "final-reviewer"], [r.get("role") for r in rows])
        code, out = _cli("progress", "models", "--path", self.d, "--run", "r1")
        self.assertEqual(0, code)
        self.assertIn("spawned final-reviewer on opus", out)

    def test_R6_a_subagents_measured_usage_joins_the_run_record(self):
        transcript = os.path.join(self.d, "agent.jsonl")
        with open(transcript, "w", encoding="utf-8", newline="\n") as fh:
            for i in range(2):
                fh.write(json.dumps({"type": "assistant", "message": {
                    "role": "assistant", "model": "claude-sonnet-x",
                    "usage": {"input_tokens": 100 + i, "output_tokens": 10}}}) + "\n")
            fh.write("not json\n")
        _hook(hook_cli.subagent_start_main, {"cwd": self.d, "session_id": "S1", "agent_id": "A1",
                                             "agent_type": "general-purpose",
                                             "hook_event_name": "SubagentStart"})
        _hook(hook_cli.subagent_stop_main, {
            "cwd": self.d, "session_id": "S1", "agent_id": "A1", "stop_hook_active": False,
            "agent_transcript_path": transcript, "hook_event_name": "SubagentStop",
            "last_assistant_message": "HANDBACK\nstatus: done\nsummary: read it\nfiles: none\n"
                                      "tests: not run\nopen: none\n"})
        [usage] = MT.read_record(self.d, "r1")["usage"]
        self.assertEqual(({"claude-sonnet-x": 2}, 201, 20),
                         (usage["models"], usage["input_tokens"], usage["output_tokens"]))
        self.assertIsNone(MT.usage_from_transcript(os.path.join(self.d, "absent.jsonl")),
                          "a usage nobody measured is absent, never zero")

    def test_R6_the_tier_is_configurable_and_governed(self):
        code, out = _cli("config", "set", "settings.models.reviewer", "haiku", "--yes",
                         "--path", self.d)
        self.assertNotEqual(0, code)
        self.assertIn("floor is sonnet", out)
        code, out = _cli("config", "set", "settings.models.reviewer", "opus", "--yes",
                         "--path", self.d)
        self.assertEqual(0, code, out)
        self.assertEqual("opus", MT.configured(self.d)["reviewer"])
        _, _, err = self.spawn("reviewer", None)
        self.assertIn('model: "opus"', err, "the refusal names THIS repo's tier")

    def test_R6_no_api_key_and_no_model_id(self):
        """Tiers are Claude Code aliases resolved inside the user's login — never an API model id,
        never an API key read."""
        import inspect
        src = inspect.getsource(MT)
        self.assertNotIn("ANTHROPIC_API_KEY", src)
        self.assertNotIn("claude-3", src)
        self.assertEqual(("haiku", "sonnet", "opus"), MT.TIERS)


if __name__ == "__main__":
    unittest.main()
