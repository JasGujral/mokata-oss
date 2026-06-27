"""Stage 28 — cross-platform Python invocation (Windows + macOS + Linux).

Two layers (see docs/build/15-stage-28-cross-platform-python.md):

1. Anything mokata *writes* (the `setup claude` hook block) embeds the absolute
   interpreter discovered at run time (``sys.executable``) — no PATH dependency.
2. The static, shipped plugin ``hooks/hooks.json`` can't bake in the user's
   interpreter, so it calls ``hooks/launch.sh``, a POSIX launcher that resolves
   *a* Python 3 (``python3`` / ``python`` / ``py -3`` + common-location
   fallbacks) and degrades to a clear no-op (exit 0) when none exists.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _support  # noqa: F401  (puts src/ on the path)

from mokata import harness_setup

ROOT = Path(__file__).resolve().parents[1]
LAUNCH = ROOT / "hooks" / "launch.sh"
HOOKS_JSON = ROOT / "hooks" / "hooks.json"

import shutil

SH = shutil.which("sh")
needs_sh = unittest.skipUnless(SH, "POSIX sh not available (e.g. Windows without git-bash)")


# ---------------------------------------------------------------------------
# Layer 1 — mokata-written commands use the absolute interpreter (sys.executable)
# ---------------------------------------------------------------------------

class TestSetupUsesAbsoluteInterpreter(unittest.TestCase):
    def test_hook_command_uses_sys_executable_not_bare_python3(self):
        cmd = harness_setup._hook_command("session_start.py")
        # Absolute interpreter, quoted — never a bare `python3` that a minimal
        # PATH (macOS GUI launch) or Windows (python/py) wouldn't find.
        self.assertIn(sys.executable, cmd)
        self.assertNotRegex(cmd, r'(^|\s)python3(\s|$)')
        self.assertIn("session_start.py", cmd)

    def test_setup_writes_absolute_interpreter_into_settings(self):
        with tempfile.TemporaryDirectory() as d:
            harness_setup.setup_harness(
                "claude", root=d, assume_yes=True, out=lambda _: None)
            settings = json.loads(
                (Path(d) / ".claude" / "settings.json").read_text())
            blob = json.dumps(settings)
            self.assertIn(sys.executable, blob)
            # the wired command must not rely on bare `python3` on PATH
            for event in ("SessionStart", "PreToolUse"):
                for entry in settings["hooks"][event]:
                    for h in entry["hooks"]:
                        self.assertNotRegex(
                            h["command"], r'(^|\s)python3(\s|$)')

    def test_mokata_hook_still_detected_after_change(self):
        # idempotency/unsetup rely on _is_mokata_hook matching our wired command
        cmd = harness_setup._hook_command("secret_guard.py")
        self.assertTrue(
            harness_setup._is_mokata_hook(
                {"hooks": [{"type": "command", "command": cmd}]}))


# ---------------------------------------------------------------------------
# Layer 2 — the static plugin hooks.json delegates to the launcher
# ---------------------------------------------------------------------------

class TestHooksJsonUsesLauncher(unittest.TestCase):
    def test_launcher_ships(self):
        self.assertTrue(LAUNCH.exists(), "hooks/launch.sh must ship")

    def test_hooks_json_calls_launcher_not_bare_python3(self):
        blob = HOOKS_JSON.read_text()
        data = json.loads(blob)
        flat = json.dumps(data)
        self.assertIn("launch.sh", flat)
        # both scripts still referenced (test_ship asserts this too)
        self.assertIn("session_start.py", flat)
        self.assertIn("secret_guard.py", flat)
        # no bare `python3 "..."` invocation remains in the static file
        self.assertNotIn('python3 "', flat)


# ---------------------------------------------------------------------------
# The launcher resolver (run as a real subprocess)
# ---------------------------------------------------------------------------

@needs_sh
class TestLauncherResolver(unittest.TestCase):
    def _run(self, target, *, env=None, extra_args=()):
        return subprocess.run(
            [SH, str(LAUNCH), str(target), *extra_args],
            capture_output=True, text=True, env=env)

    def _marker_script(self, d):
        p = Path(d) / "probe.py"
        p.write_text(
            "import sys\n"
            "print('MOKATA_OK ' + sys.executable)\n"
            "print('argv ' + ' '.join(sys.argv[1:]))\n")
        return p

    def test_resolves_and_runs_target(self):
        with tempfile.TemporaryDirectory() as d:
            probe = self._marker_script(d)
            res = self._run(probe)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("MOKATA_OK", res.stdout)

    def test_forwards_extra_args(self):
        with tempfile.TemporaryDirectory() as d:
            probe = self._marker_script(d)
            res = self._run(probe, extra_args=("--flag", "value"))
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("argv --flag value", res.stdout)

    def test_mokata_python_override_is_honored(self):
        # Force resolution to depend ONLY on the override: empty PATH + empty
        # common-dirs means nothing else can satisfy it.
        with tempfile.TemporaryDirectory() as d:
            probe = self._marker_script(d)
            env = dict(os.environ)
            env["PATH"] = ""
            env["MOKATA_PYTHON_DIRS"] = ""
            env["MOKATA_PYTHON"] = sys.executable
            res = self._run(probe, env=env)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("MOKATA_OK", res.stdout)

    def test_degrades_clean_when_no_interpreter(self):
        # No PATH, no common dirs, no override → exit 0 (never block) + a clear
        # one-line message on stderr. The target is NOT run.
        with tempfile.TemporaryDirectory() as d:
            probe = self._marker_script(d)
            env = dict(os.environ)
            env["PATH"] = ""
            env["MOKATA_PYTHON_DIRS"] = ""
            env.pop("MOKATA_PYTHON", None)
            res = self._run(probe, env=env)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertNotIn("MOKATA_OK", res.stdout)
            self.assertTrue(res.stderr.strip(), "expected a clear message on stderr")
            self.assertIn("mokata", res.stderr.lower())

    def test_missing_target_arg_degrades_clean(self):
        res = subprocess.run([SH, str(LAUNCH)], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("mokata", res.stderr.lower())

    def test_resolves_via_common_dir_when_path_empty(self):
        # Empty PATH, but a common dir holds a `python3` → the launcher must
        # find it there (the macOS-GUI minimal-PATH case). Build a deterministic
        # bin dir with a `python3` symlink to this interpreter.
        with tempfile.TemporaryDirectory() as d:
            probe = self._marker_script(d)
            bindir = Path(d) / "bin"
            bindir.mkdir()
            link = bindir / "python3"
            os.symlink(sys.executable, link)
            env = dict(os.environ)
            env["PATH"] = ""
            env["MOKATA_PYTHON_DIRS"] = str(bindir)
            env.pop("MOKATA_PYTHON", None)
            res = self._run(probe, env=env)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("MOKATA_OK", res.stdout)


if __name__ == "__main__":
    unittest.main()
