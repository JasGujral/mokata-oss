"""H-1a S1 — the UserPromptSubmit SEAM: the hook exists, is wired everywhere, and cannot block.

S1 lands the wiring alone, before anything depends on it. What it has to get right:

  * `_emit` is no longer hard-coded to SessionStart. Claude Code keys an `additionalContext`
    payload on `hookEventName`; a payload stamped with the wrong event is dropped SILENTLY, so a
    hard-coded name would have produced a hook that runs, exits 0, and injects nothing;
  * BOTH wiring sites carry the new event — `harness_setup._EXPECTED_HOOK_WIRING` (what
    `mokata setup claude` writes into settings.json) AND the packaged plugin `hooks.json`. A user
    is on exactly one of those two routes, and a hook wired on only one is dead for the other;
  * the plugin block declares `"shell": "bash"`. Without it a Windows box lacking Git Bash falls
    through to PowerShell, where a quoted path followed by an argument is a parse ERROR — the hook
    silently never runs (HOOK-SHELL-AGNOSTIC's whole finding);
  * `wiring_drift` DETECTS a hand-removed UserPromptSubmit entry. Wiring written by an older
    mokata keeps working and stays silently incomplete forever unless the drift check knows the
    event exists;
  * FAIL-OPEN, and `BLOCK_EXIT` unreachable. On PreToolUse exit 2 blocks a tool call; on
    UserPromptSubmit it EATS THE HUMAN'S TURN. There is no failure in a context-injection hook
    worth that price.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import contextlib
import inspect
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

import _support  # noqa: F401  (puts src/ on the path)

from mokata import harness_setup, hook_cli, hook_wiring

ROOT = Path(__file__).resolve().parents[1]
HOOKS_JSON = ROOT / "src" / "mokata" / "hooks" / "hooks.json"
EVENT = "UserPromptSubmit"
SUB = "user-prompt-submit"


def _plugin_hooks():
    data = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    return data["hooks"]


def _run(argv, stdin_text=None):
    """Run a hook subcommand, returning (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    saved = None
    if stdin_text is not None:
        import sys
        saved, sys.stdin = sys.stdin, io.StringIO(stdin_text)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = hook_cli.main(argv)
    finally:
        if saved is not None:
            import sys
            sys.stdin = saved
    return code, out.getvalue(), err.getvalue()


# ==================================================================== _emit is parameterised
class TestEmitCarriesItsEvent(unittest.TestCase):
    def test_emit_defaults_to_session_start(self):
        """The default keeps every existing SessionStart call site byte-identical."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            hook_cli._emit("hello")
        self.assertEqual("SessionStart",
                         json.loads(out.getvalue())["hookSpecificOutput"]["hookEventName"])

    def test_emit_stamps_the_event_it_is_given(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            hook_cli._emit("hello", event=hook_cli.USER_PROMPT_SUBMIT_EVENT)
        payload = json.loads(out.getvalue())["hookSpecificOutput"]
        self.assertEqual(EVENT, payload["hookEventName"])
        self.assertEqual("hello", payload["additionalContext"])

    def test_the_event_names_are_declared_not_scattered(self):
        self.assertEqual("SessionStart", hook_cli.SESSION_START_EVENT)
        self.assertEqual(EVENT, hook_cli.USER_PROMPT_SUBMIT_EVENT)


# ==================================================================== the dispatcher knows it
class TestDispatcher(unittest.TestCase):
    def test_the_subcommand_is_registered(self):
        self.assertIn(SUB, hook_cli._SUBCOMMANDS)
        self.assertIs(hook_cli._SUBCOMMANDS[SUB], hook_cli.user_prompt_submit_main)

    def test_both_misconfiguration_messages_name_it(self):
        """A mis-wired hook must be diagnosable from the message alone — and the two messages are
        derived from `_SUBCOMMANDS`, so a future hook cannot be added without appearing in them."""
        code_missing, _o, err_missing = _run([])
        code_unknown, _o, err_unknown = _run(["not-a-hook"])
        self.assertEqual(1, code_missing)     # misconfiguration: exit 1, never 0 and never 2
        self.assertEqual(1, code_unknown)
        for err in (err_missing, err_unknown):
            self.assertIn(SUB, err)
            for existing in ("session-start", "secret-guard", "gate-guard", "dirty-track"):
                self.assertIn(existing, err)


# ==================================================================== BOTH wiring sites
class TestBothWiringSitesCarryTheEvent(unittest.TestCase):
    def test_expected_hook_wiring_declares_it_with_no_matcher(self):
        wiring = harness_setup.expected_hook_wiring()
        self.assertIn(EVENT, wiring)
        self.assertEqual([{"subcommand": SUB, "matcher": None}], wiring[EVENT])

    def test_setup_plans_the_hook(self):
        plan = harness_setup.plan_setup("claude", root=str(ROOT), home=None)
        entries = plan.hook_commands.get(EVENT)
        self.assertTrue(entries, "mokata setup claude does not wire the UserPromptSubmit hook")
        self.assertEqual([SUB], entries[0]["args"])
        self.assertNotIn("matcher", entries[0])      # the event carries no tool to match

    def test_the_plugin_hooks_json_wires_it_through_the_shim(self):
        blocks = _plugin_hooks()
        self.assertIn(EVENT, blocks, "the PLUGIN route has no UserPromptSubmit hook — a plugin "
                                     "user gets no per-turn recall at all")
        hook = blocks[EVENT][0]["hooks"][0]
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/src/mokata/hooks/mokata-hook-launch", hook["command"])
        self.assertTrue(hook["command"].rstrip().endswith(SUB))
        self.assertNotIn("matcher", blocks[EVENT][0])

    def test_the_plugin_block_names_its_shell(self):
        """WITHOUT `shell: bash` a Windows box lacking Git Bash falls through to PowerShell, where
        a leading quote opens EXPRESSION mode and the command is a parse error — so the hook
        silently never runs. This is the one property the block cannot be missing."""
        hook = _plugin_hooks()[EVENT][0]["hooks"][0]
        self.assertEqual("bash", hook.get("shell"))

    def test_the_standalone_shim_exists_and_re_exports_the_runtime(self):
        from mokata.hooks import user_prompt_submit as shim
        self.assertIs(shim.main, hook_cli.user_prompt_submit_main)


# ==================================================================== drift detection
class TestWiringDriftSeesAHandRemovedEntry(unittest.TestCase):
    """THE pin doc 84 asks for: an older/edited settings.json missing this event is DRIFT."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.home = os.path.join(self.root, "home")
        os.makedirs(self.home, exist_ok=True)
        harness_setup.setup_harness("claude", root=self.root, home=self.home,
                                    assume_yes=True, out=lambda _: None)
        self.settings = Path(self.root) / ".claude" / "settings.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _drift(self):
        return hook_wiring.wiring_drift(self.root, self.home)

    def _mutate(self, fn):
        data = json.loads(self.settings.read_text(encoding="utf-8"))
        fn(data)
        self.settings.write_text(json.dumps(data), encoding="utf-8")

    def test_freshly_wired_is_not_drifted(self):
        drift = self._drift()
        self.assertFalse(drift.drifted, drift.reasons)

    def test_a_hand_removed_user_prompt_submit_entry_is_drift(self):
        self._mutate(lambda d: d["hooks"].pop(EVENT))
        drift = self._drift()
        self.assertTrue(drift.drifted,
                        "a settings.json with no UserPromptSubmit entry reads as CURRENT — an "
                        "older wiring would stay silently incomplete forever")
        self.assertIn(hook_wiring.DRIFT_MISSING, drift.codes)
        reasons = " ".join(drift.reasons)
        self.assertIn(EVENT, reasons)
        self.assertIn(SUB, reasons)

    def test_the_rendered_line_names_the_remedy(self):
        self._mutate(lambda d: d["hooks"].pop(EVENT))
        line = hook_wiring.wiring_drift_line(self._drift())
        self.assertIn(EVENT, line)
        self.assertIn("mokata setup claude", line)


# ==================================================================== fail-open / never blocks
class TestTheHookCanNeverBlockATurn(unittest.TestCase):
    def test_block_exit_is_unreachable_from_this_subcommand(self):
        """Structural, and cheap because the function is self-contained: nothing in the
        subcommand's own source can return the security-block code."""
        src = inspect.getsource(hook_cli.user_prompt_submit_main)
        self.assertNotIn("BLOCK_EXIT", src)
        self.assertNotIn("return 2", src)

    def test_an_unparseable_envelope_exits_zero_silently(self):
        code, out, err = _run([SUB], stdin_text="{not json at all")
        self.assertEqual(0, code)
        self.assertEqual("", out)
        self.assertEqual("", err)

    def test_an_empty_stdin_exits_zero_silently(self):
        code, out, err = _run([SUB], stdin_text="")
        self.assertEqual((0, "", ""), (code, out, err))

    def test_a_non_mapping_envelope_exits_zero_silently(self):
        code, out, err = _run([SUB], stdin_text=json.dumps(["not", "a", "mapping"]))
        self.assertEqual((0, "", ""), (code, out, err))

    def test_an_empty_prompt_says_nothing(self):
        code, out, err = _run([SUB], stdin_text=json.dumps({"prompt": "", "cwd": os.getcwd()}))
        self.assertEqual((0, "", ""), (code, out, err))

    def test_a_real_prompt_outside_a_mokata_repo_exits_zero(self):
        with tempfile.TemporaryDirectory() as d:
            code, out, err = _run(
                [SUB], stdin_text=json.dumps({"prompt": "how do I deploy?", "cwd": d}))
        self.assertEqual(0, code)
        self.assertEqual("", err)

    def test_an_exploding_internals_still_exits_zero(self):
        """The fail-open FLOOR itself, exercised rather than asserted about."""
        from unittest import mock
        with mock.patch.object(hook_cli, "_prompt_envelope", side_effect=RuntimeError("boom")):
            code, out, err = _run([SUB], stdin_text=json.dumps({"prompt": "hi"}))
        self.assertEqual((0, "", ""), (code, out, err))


if __name__ == "__main__":                              # pragma: no cover
    unittest.main()
