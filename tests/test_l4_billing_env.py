"""0.0.26 L4 — NO-API-KEY-GUARD, runtime half: a billing credential in the environment is made
VISIBLE where mokata launches Claude Code (IDE terminal, doctor) — named, never its value — and is
not stripped (the decision and its evidence: `src/mokata/billing_env.py`).
"""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import billing_env as B
from mokata.config import Surface
from mokata.govern import doctor as D
from mokata.init import init_repo

SENTINEL = "sk-ant-SENTINEL-must-never-surface-0123456789"


class TheWarningNamesTheVariableNeverTheValue(unittest.TestCase):

    def test_set_variables_are_named_in_claude_codes_precedence_order(self):
        env = {"ANTHROPIC_API_KEY": SENTINEL, "ANTHROPIC_AUTH_TOKEN": SENTINEL}
        self.assertEqual(["ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"], B.billing_overrides(env))
        self.assertEqual(["ANTHROPIC_API_KEY"], B.billing_overrides({"ANTHROPIC_API_KEY": SENTINEL}))

    def test_unset_or_blank_is_nothing(self):
        self.assertEqual([], B.billing_overrides({}))
        self.assertEqual([], B.billing_overrides({"ANTHROPIC_API_KEY": "  "}))
        self.assertEqual([], B.billing_overrides({"CLAUDE_CODE_OAUTH_TOKEN": SENTINEL}))  # the login

    def test_the_notice_never_carries_the_value(self):
        text = B.notice(B.billing_overrides({"ANTHROPIC_API_KEY": SENTINEL}))
        self.assertIn("ANTHROPIC_API_KEY", text)
        self.assertIn("unset", text)
        self.assertNotIn(SENTINEL, text)

    def test_the_terminal_preface_is_for_claude_only(self):
        env = {"ANTHROPIC_API_KEY": SENTINEL}
        pre = B.terminal_preface("claude", env)
        self.assertIn(b"ANTHROPIC_API_KEY", pre)
        self.assertNotIn(SENTINEL.encode(), pre)
        self.assertEqual(b"", B.terminal_preface("shell", env))
        self.assertEqual(b"", B.terminal_preface("claude", {}))


class TheDoctorWarns(unittest.TestCase):

    def test_a_key_in_the_env_is_a_named_warning(self):
        [f] = D.billing_env_findings({"ANTHROPIC_API_KEY": SENTINEL})
        self.assertEqual(("warning", "claude-billing-env"), (f.severity, f.code))
        self.assertNotIn(SENTINEL, f.detail)
        self.assertEqual([], D.billing_env_findings({}))

    def test_diagnose_carries_it(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
            with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": SENTINEL}):
                report = D.diagnose(Surface.load(d))
        hits = [f for f in report.findings if f.code == "claude-billing-env"]
        self.assertEqual(1, len(hits))
        self.assertNotIn(SENTINEL, repr(report.findings))


@unittest.skipIf(sys.platform == "win32", "the POSIX pty backend (WinTerminal shares the preface line)")
class TheEmbeddedTerminalShowsIt(unittest.TestCase):

    def _first_bytes(self, program):
        from mokata.ide import terminal as T
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": SENTINEL}):
            term = T.Terminal("t1", program, [sys.executable, "-c", "print('child-ran')"], d, 80, 24)
            try:
                deadline = time.time() + 10
                while term.alive and time.time() < deadline:
                    time.sleep(0.05)
                with term._cond:
                    return bytes(term._buf)
            finally:
                term.close()

    def test_the_claude_terminal_opens_with_the_notice_before_any_output(self):
        out = self._first_bytes("claude")
        self.assertTrue(out.startswith(B.terminal_preface("claude", {"ANTHROPIC_API_KEY": "x"})))
        self.assertIn(b"child-ran", out)
        self.assertNotIn(SENTINEL.encode(), out)

    def test_the_shell_terminal_is_untouched(self):
        self.assertFalse(self._first_bytes("shell").startswith(b"\x1b[33mmokata:"))

    def test_the_key_is_warned_about_not_stripped(self):
        # The decision (billing_env.py): Claude Code itself asks before an interactive session first
        # uses a key; stripping would override the user's recorded choice. The env passes through.
        from mokata.ide import terminal as T
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": SENTINEL}):
            self.assertEqual(SENTINEL, T.child_env().get("ANTHROPIC_API_KEY"))


class TheIdeLaunchesClaudeOnTheUsersLogin(unittest.TestCase):
    """The IDE's embedded `claude` (its live session preview) is the user's own interactive Claude
    Code: mokata adds no credential to its environment and no flag that bypasses the login
    (`-p` uses a key unasked; `--bare` reads only a key). With no key in the user's env, the
    session it starts can only be on the user's Claude login."""

    def test_mokata_injects_no_credential_and_no_login_bypassing_flag(self):
        from mokata.ide import terminal as T
        creds = B.BILLING_ENV + ("CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK",
                                 "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")
        clean = {k: v for k, v in os.environ.items() if k not in creds}
        with mock.patch.dict(os.environ, clean, clear=True):
            env = T.child_env()
        self.assertEqual([], [k for k in creds if k in env])
        build = T.default_programs()["claude"]
        with mock.patch.object(T.shutil, "which", return_value="/usr/bin/claude"), \
                mock.patch.object(T, "_windows", return_value=False):
            self.assertEqual(["/usr/bin/claude"], build(None))
            self.assertEqual(["/usr/bin/claude", "--resume", "abc"], build("abc"))


if __name__ == "__main__":
    unittest.main()
