"""Stage 53b — hook invocation hardening.

The fragile `sh launch.sh -> python3/python/py` hook chain is replaced by a `mokata-hook`
console entry point — the same PATH-resolved mechanism the bundled `mokata-mcp` server
already uses. These tests assert:

  * `mokata.hook_cli` dispatches `secret-guard` (exit 2 on a secret, 0 on clean) and
    `session-start` (always exit 0, emits a briefing/offer), and degrades clean on an
    unknown/missing subcommand;
  * the `mokata-hook` console entry point is declared in pyproject;
  * the plugin `hooks/hooks.json` is wired via `mokata-hook` with NO bare `python3` / `sh`;
  * the `setup` writer emits a `mokata-hook` command (no bare `python3`), still detected by
    `_is_mokata_hook` (idempotency/unsetup), on every platform's PATH shape.
"""

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path

import _support  # noqa: F401  (puts src/ on the path)

from mokata import hook_cli
from mokata import harness_setup

ROOT = Path(__file__).resolve().parents[1]
HOOKS_JSON = ROOT / "hooks" / "hooks.json"

FAKE_SECRET = "AKIAIOSFODNN7EXAMPLE"
CLEAN = "just some ordinary code"


# --- the runtime: secret-guard -------------------------------------------------------
class TestSecretGuardRuntime(unittest.TestCase):
    def test_blocks_secret_via_text(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(
                hook_cli.secret_guard_main(["--text", FAKE_SECRET, "--path", "/app.py"]),
                hook_cli.BLOCK_EXIT)

    def test_clean_text_passes(self):
        self.assertEqual(hook_cli.secret_guard_main(["--text", CLEAN]), 0)

    def test_dispatch_secret_guard(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(hook_cli.main(["secret-guard", "--text", FAKE_SECRET]),
                             hook_cli.BLOCK_EXIT)
            self.assertEqual(hook_cli.main(["secret-guard", "--text", CLEAN]), 0)


# --- the runtime: session-start ------------------------------------------------------
@contextlib.contextmanager
def _empty_stdin():
    """Feed an EOF stdin so session-start's cwd read never blocks on a piped stdin."""
    old = sys.stdin
    sys.stdin = io.StringIO("")
    try:
        yield
    finally:
        sys.stdin = old


class TestSessionStartRuntime(unittest.TestCase):
    def test_emits_and_never_blocks(self):
        buf = io.StringIO()
        with _empty_stdin(), contextlib.redirect_stdout(buf):
            rc = hook_cli.session_start_main([])
        self.assertEqual(rc, 0)
        # whatever it decides (offer vs briefing), it emits a SessionStart hook payload
        payload = json.loads(buf.getvalue().strip().splitlines()[-1])
        self.assertEqual(
            payload["hookSpecificOutput"]["hookEventName"], "SessionStart")

    def test_dispatch_session_start(self):
        with _empty_stdin(), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(hook_cli.main(["session-start"]), 0)


# --- the dispatcher degrades clean ---------------------------------------------------
class TestDispatcherDegradesClean(unittest.TestCase):
    def test_unknown_subcommand_exits_zero(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(hook_cli.main(["bogus"]), 0)

    def test_missing_subcommand_exits_zero(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(hook_cli.main([]), 0)


# --- the entry point is declared -----------------------------------------------------
class TestEntryPointDeclared(unittest.TestCase):
    def test_pyproject_declares_mokata_hook(self):
        txt = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('mokata-hook = "mokata.hook_cli:main"', txt)


# --- the plugin hooks.json uses the console entry point (no python3 / sh) -------------
class TestPluginHooksJson(unittest.TestCase):
    def test_hooks_json_uses_mokata_hook_no_python3_no_sh(self):
        flat = json.dumps(json.loads(HOOKS_JSON.read_text(encoding="utf-8")))
        self.assertIn("mokata-hook session-start", flat)
        self.assertIn("mokata-hook secret-guard", flat)
        self.assertIn("SessionStart", flat)
        self.assertIn("PreToolUse", flat)
        # the whole point of the stage: no fragile resolution remains
        self.assertNotRegex(flat, r'(^|\s)python3(\s|\\|$)')
        self.assertNotIn("launch.sh", flat)
        self.assertNotRegex(flat, r'(^|\s)sh\s')


# --- the setup writer emits a mokata-hook command, still detected --------------------
class TestSetupWriter(unittest.TestCase):
    def test_hook_command_uses_mokata_hook_not_python3(self):
        for script in ("session_start.py", "secret_guard.py"):
            cmd = harness_setup._hook_command(script)
            self.assertIn("mokata-hook", cmd)
            self.assertNotRegex(cmd, r'(^|\s)python3(\s|$)')

    def test_session_start_command_forwards_plugin_root(self):
        cmd = harness_setup._hook_command("session_start.py")
        self.assertIn("session-start", cmd)
        self.assertIn("--plugin-root", cmd)

    def test_wired_command_is_detected(self):
        # idempotency + unsetup rely on _is_mokata_hook matching our wired command
        for script in ("session_start.py", "secret_guard.py"):
            cmd = harness_setup._hook_command(script)
            self.assertTrue(harness_setup._is_mokata_hook(
                {"hooks": [{"type": "command", "command": cmd}]}))

    def test_legacy_hooks_dir_command_still_detected(self):
        # back-compat: an entry written by an older mokata (hooks-dir path) is still
        # recognized so `unsetup` can clean it.
        legacy = {"hooks": [{"type": "command",
                             "command": f'"/py" "{harness_setup._hooks_dir()}/x.py"'}]}
        self.assertTrue(harness_setup._is_mokata_hook(legacy))


if __name__ == "__main__":
    unittest.main()
