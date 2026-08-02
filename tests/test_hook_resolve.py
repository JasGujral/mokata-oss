"""HOOK-RESOLVE — plugin hooks fire on a GUI-minimal PATH, and a dead gate is LOUD.

The bug, live against installed 0.0.15: the plugin `hooks.json` wired all four hooks by the
BARE name `mokata-hook`. A GUI-launched Claude Code (launchd / Explorer — no shell profile)
runs hooks with a minimal PATH that omits the pip console-script dir, so the bare name never
resolved. Claude Code DROPS a hook whose command doesn't resolve: secret-guard and gate-guard
were simply OFF, with no error, no warning, and a `mokata doctor` that reported all clear.
Wired has never implied firing, and nothing in the product said so.

Two fixes, both asserted here at the level the user experiences:

  1. the plugin route resolves ITSELF — `hooks.json` invokes `hooks/mokata-hook-launch`
     under ${CLAUDE_PLUGIN_ROOT}, which runs B1's ladder at hook time (PATH → the console
     script beside a resolved interpreter → the packaged module) and, when nothing resolves,
     exits 1 with one line naming `mokata setup claude` — never a silent 0;
  2. `mokata doctor` HARD-checks every wired hook surface and names a dead one:
     "gates are NOT firing", with the exact command it tried.

The `mokata setup` path (B1, absolute wiring) is untouched and is re-pinned here.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import harness_setup, hook_wiring
from mokata.govern.doctor import hook_resolution_findings

ROOT = Path(__file__).resolve().parents[1]
HOOKS_DIR = ROOT / "src" / "mokata" / "hooks"
HOOKS_JSON = HOOKS_DIR / "hooks.json"
SHIM = HOOKS_DIR / "mokata-hook-launch"
SHIM_CMD = HOOKS_DIR / "mokata-hook-launch.cmd"
PLUGIN_MANIFEST = ROOT / ".claude-plugin" / "plugin.json"

SH = shutil.which("sh")
needs_sh = unittest.skipUnless(SH, "POSIX sh not available (e.g. Windows without git-bash)")

# A real, scannable secret shape — the ONLY way to prove the gate actually ran end to end.
FAKE_SECRET = "AKIAIOSFODNN7EXAMPLE"
BLOCK_EXIT = 2          # hook_cli.BLOCK_EXIT — security block, reserved
MISCONFIG_EXIT = 1      # hook_cli's misconfiguration convention

# The hook subcommands `mokata setup claude` wires, read from the ONE declaration that writes
# them (`harness_setup._HOOK_SUBCOMMAND`) rather than re-typed here. `statusline` is deliberately
# absent: it is a statusLine command, not a hook, and is not wired in hooks.json.
_SUBCOMMANDS = sorted(harness_setup._HOOK_SUBCOMMAND.values())


def _hook_commands():
    """{subcommand: wired command} straight from the shipped plugin hooks.json."""
    data = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    out = {}
    for blocks in data["hooks"].values():
        for entry in blocks:
            for h in entry.get("hooks", []):
                cmd = h["command"]
                # Derived from the declaration, never a hand-kept list: H-1a added a fifth hook
                # and a literal tuple here would simply have raised StopIteration on it.
                sub = next(s for s in _SUBCOMMANDS if s in cmd)
                out[sub] = cmd
    return out


def _stripped_env(**extra):
    """The GUI-launch failure environment: no PATH, no common interpreter dirs, no overrides."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("PATH", "MOKATA_PYTHON", "MOKATA_PYTHON_DIRS", "MOKATA_HOOK",
                        "PYTHONPATH")}
    env["PATH"] = ""
    env["MOKATA_PYTHON_DIRS"] = ""
    env.update(extra)
    return env


def _run_wired(command, env, extra=""):
    """Run a wired hook command exactly as a shell-launched hook would."""
    return subprocess.run([SH, "-c", f"{command} {extra}"], capture_output=True, text=True,
                          env=env, stdin=subprocess.DEVNULL, cwd=str(ROOT))


def _fake_console_script(d, *, exit_code=0):
    """A stand-in `mokata-hook` on PATH that reports its argv and exits how we ask."""
    p = Path(d) / "mokata-hook"
    p.write_text("#!/bin/sh\n"
                 'echo "FAKE_HOOK_RAN $*"\n'
                 f"exit {exit_code}\n", encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


def _wire_settings(root, command, *, event="PreToolUse"):
    """Write a `.claude/settings.json` carrying one mokata-wired hook."""
    path = Path(root) / ".claude" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"hooks": {event: [
        {"matcher": "Write|Edit", "hooks": [{"type": "command", "command": command}]}]}}),
        encoding="utf-8")
    return path


def _fake_plugin(home, *, ship_shim=True, mcp_command="mokata-mcp"):
    """A plugin install: the recorded plugin root + a manifest wiring the real hooks.json."""
    plugin_root = Path(home) / "plugins" / "mokata"
    (plugin_root / "src" / "mokata" / "hooks").mkdir(parents=True)
    shutil.copy(str(HOOKS_JSON), str(plugin_root / "src" / "mokata" / "hooks" / "hooks.json"))
    if ship_shim:
        target = plugin_root / "src" / "mokata" / "hooks" / "mokata-hook-launch"
        shutil.copy(str(SHIM), str(target))
        target.chmod(target.stat().st_mode | stat.S_IXUSR)
    manifest = plugin_root / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({
        "name": "mokata",
        "hooks": "./src/mokata/hooks/hooks.json",
        "mcpServers": {"mokata": {"command": mcp_command, "args": []}},
    }), encoding="utf-8")
    from mokata.plugin_cache import record_plugin_root
    record_plugin_root(str(plugin_root), home=str(home))
    return plugin_root


def _findings(root, home):
    return hook_resolution_findings(SimpleNamespace(root=str(root)), str(home))


# =====================================================================================
# 1 — the plugin route invokes the self-resolving shim (EVERY hook, derived not counted)
# =====================================================================================
class TestPluginHooksInvokeTheShim(unittest.TestCase):
    def test_hooks_json_wires_the_shim_for_every_hook(self):
        commands = _hook_commands()
        self.assertEqual(sorted(commands), _SUBCOMMANDS,
                         "the PLUGIN route must wire every hook the setup route does — a hook "
                         "on only one route is dead for everyone on the other")
        for sub, cmd in commands.items():
            self.assertIn("${CLAUDE_PLUGIN_ROOT}/src/mokata/hooks/mokata-hook-launch", cmd,
                          f"{sub} must go through the self-resolving shim")
            self.assertFalse(cmd.startswith("mokata-hook "),
                             f"{sub} is still wired by BARE name — silently unresolvable")

    def test_session_start_still_forwards_the_plugin_root(self):
        self.assertIn('--plugin-root "${CLAUDE_PLUGIN_ROOT}"',
                      _hook_commands()["session-start"])

    def test_matchers_are_unchanged(self):
        data = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
        matchers = {h["command"].rsplit('" ', 1)[-1].split()[0]: entry.get("matcher")
                    for event, blocks in data["hooks"].items()
                    for entry in blocks for h in entry["hooks"]}
        self.assertEqual(matchers["secret-guard"], harness_setup.HOOK_PRETOOL_MATCHER)
        self.assertEqual(matchers["gate-guard"], harness_setup.HOOK_GATE_MATCHER)
        self.assertEqual(matchers["dirty-track"], harness_setup.HOOK_DIRTY_MATCHER)

    def test_shim_ships_for_posix_and_windows(self):
        # CORRECTED by HOOK-SHELL-AGNOSTIC. This comment used to state that cmd.exe completes
        # the extension-less path to the `.cmd` through the Windows executable-extension
        # search. That is FALSE — cmd.exe is never a hook shell — and stating it as settled
        # fact is what let an auditor check Windows support and move on. The real matrix:
        # hooks.json pins `"shell": "bash"`, so `sh -c` (macOS/Linux) and Git Bash (Windows)
        # run the sh shim, and a Git-Bash-less Windows gets a LOUD named error instead of a
        # silent PowerShell parse failure. The `.cmd` still ships but is NOT on the hook path.
        # See tests/test_hook_shell_agnostic.py for the full derivation.
        self.assertTrue(SHIM.is_file(), "the POSIX shim must ship")
        self.assertTrue(SHIM_CMD.is_file(), "the Windows shim must ship")
        self.assertTrue(SHIM.read_text(encoding="utf-8").startswith("#!/bin/sh"))
        if os.name != "nt":
            self.assertTrue(os.access(str(SHIM), os.X_OK),
                            "the POSIX shim must be executable (a hook runs it directly)")

    def test_shim_ships_in_the_wheel_glob(self):
        # pyproject's package-data glob is explicit-depth `hooks/*` — it must cover an
        # extension-less file, or the plugin ships without its launcher.
        self.assertIn('"hooks/*"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertIn(SHIM, list(HOOKS_DIR.glob("*")))
        self.assertIn(SHIM_CMD, list(HOOKS_DIR.glob("*")))


# =====================================================================================
# 2 — the plugin's MCP registration
# =====================================================================================
class TestPluginMcpRegistration(unittest.TestCase):
    """A static `mcpServers` entry is spawned DIRECTLY (no shell, one command string, no
    per-OS branching), so it cannot invoke the extension-less shim the hooks use. The
    registration therefore stays a bare name and doctor NAMES it when it doesn't resolve.

    CORRECTED by HOOK-SHELL-AGNOSTIC: the original reason given here was that cmd.exe's
    executable-extension completion is a shell behaviour rather than a CreateProcess one. The
    conclusion holds, but the premise did not — cmd.exe never runs a mokata hook either, so
    that completion was never the thing keeping the two routes apart. What actually separates
    them is that a direct spawn needs a real executable, which the sh shim is not on Windows."""

    def test_manifest_mcp_command_is_unchanged(self):
        data = json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(data["mcpServers"]["mokata"]["command"], "mokata-mcp")

    def test_doctor_names_an_unresolvable_plugin_mcp_registration(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d) / "home"
            home.mkdir()
            _fake_plugin(home, mcp_command="/nowhere/mokata-mcp")
            codes = [f.code for f in _findings(d, home)]
            self.assertIn("plugin-mcp-unresolvable", codes)
            finding = next(f for f in _findings(d, home)
                           if f.code == "plugin-mcp-unresolvable")
            self.assertIn("/nowhere/mokata-mcp", finding.detail)
            self.assertIn("mokata setup claude", finding.detail)

    def test_a_resolvable_plugin_mcp_registration_is_quiet(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d) / "home"
            home.mkdir()
            _fake_plugin(home, mcp_command=sys.executable)
            codes = [f.code for f in _findings(d, home)]
            self.assertNotIn("plugin-mcp-unresolvable", codes)


# =====================================================================================
# 3 — FAIL LOUD: a shim that cannot resolve anything exits 1, never 0
# =====================================================================================
@needs_sh
class TestShimFailsLoud(unittest.TestCase):
    def test_unresolvable_shim_exits_1_and_names_the_remedy(self):
        res = _run_wired(f'"{SHIM}"', _stripped_env(), extra="secret-guard")
        self.assertEqual(res.returncode, MISCONFIG_EXIT,
                         f"a dead gate must exit 1, not {res.returncode} (stderr: {res.stderr})")
        self.assertIn("NOT firing", res.stderr)
        self.assertIn("mokata setup claude", res.stderr)
        self.assertEqual(len(res.stderr.strip().splitlines()), 1,
                         "the failure must be ONE line")

    def test_misconfiguration_never_uses_the_security_block_code(self):
        # exit 2 is reserved for a real secret. A misconfigured launcher must never claim it.
        res = _run_wired(f'"{SHIM}"', _stripped_env(), extra="secret-guard")
        self.assertNotEqual(res.returncode, BLOCK_EXIT)
        res = _run_wired(f'"{SHIM}"', _stripped_env())          # no subcommand at all
        self.assertEqual(res.returncode, MISCONFIG_EXIT)
        self.assertIn("mokata setup claude", res.stderr)

    def test_windows_shim_declares_the_same_contract(self):
        txt = SHIM_CMD.read_text(encoding="utf-8")
        self.assertIn("exit /b 1", txt)                     # fail loud, misconfig code
        self.assertIn("mokata setup claude", txt)           # naming the remedy
        self.assertIn("exit /b %ERRORLEVEL%", txt)          # the subcommand's own exit code
        self.assertNotIn("exit /b 2", txt)                  # never invents a security block

    @unittest.skipUnless(os.name == "nt", "the .cmd shim only runs on Windows")
    def test_windows_shim_fails_loud(self):
        env = _stripped_env()
        res = subprocess.run([str(SHIM_CMD), "secret-guard"], capture_output=True, text=True,
                             env=env, stdin=subprocess.DEVNULL)
        self.assertEqual(res.returncode, MISCONFIG_EXIT, res.stderr)
        self.assertIn("mokata setup claude", res.stderr)


# =====================================================================================
# 4 — `mokata doctor`: a wired-but-dead gate is a named ERROR
# =====================================================================================
class TestDoctorNamesDeadGates(unittest.TestCase):
    def test_unresolvable_wired_hook_is_an_error_naming_the_remedy(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d) / "home"
            home.mkdir()
            _wire_settings(d, '"/nowhere/bin/mokata-hook" secret-guard')
            findings = _findings(d, home)
            self.assertEqual([f.code for f in findings], ["hooks-not-firing"])
            f = findings[0]
            self.assertEqual(f.severity, "error")
            self.assertIn("NOT firing", f.detail)              # the verdict
            self.assertIn("/nowhere/bin/mokata-hook", f.detail)  # the exact command tried
            self.assertIn("mokata setup claude", f.detail)     # the remedy

    def test_a_resolvable_wired_hook_is_quiet(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d) / "home"
            home.mkdir()
            _wire_settings(d, f'"{sys.executable}" secret-guard')
            self.assertEqual(_findings(d, home), [])

    def test_a_repo_with_nothing_wired_is_unchanged(self):
        # Today's behaviour, preserved: no hooks wired anywhere -> doctor says nothing about
        # hooks. Silence here means "no mokata hooks are wired", never "the gates are fine".
        with tempfile.TemporaryDirectory() as d:
            home = Path(d) / "home"
            home.mkdir()
            self.assertEqual(_findings(d, home), [])
            self.assertEqual(hook_wiring.hook_wiring_report(d, str(home)).hooks, [])

    def test_one_dead_command_across_events_is_one_finding(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d) / "home"
            home.mkdir()
            path = Path(d) / ".claude" / "settings.json"
            path.parent.mkdir(parents=True)
            dead = '"/nowhere/bin/mokata-hook" secret-guard'
            path.write_text(json.dumps({"hooks": {
                "PreToolUse": [{"hooks": [{"type": "command", "command": dead}]}],
                "PostToolUse": [{"hooks": [{"type": "command", "command": dead}]}],
            }}), encoding="utf-8")
            findings = _findings(d, home)
            self.assertEqual(len(findings), 1, [f.detail for f in findings])
            self.assertIn("PostToolUse/PreToolUse", findings[0].detail)

    def test_a_users_own_hooks_are_never_judged(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d) / "home"
            home.mkdir()
            _wire_settings(d, "/nowhere/their-own-linter.sh --fix")
            self.assertEqual(_findings(d, home), [])

    def test_the_plugin_route_is_checked_too(self):
        # The whole point: a plugin user has NOTHING in settings.json. Ship the manifest but
        # not the shim -> the wired command cannot launch -> doctor must say so.
        with tempfile.TemporaryDirectory() as d:
            home = Path(d) / "home"
            home.mkdir()
            plugin_root = _fake_plugin(home, ship_shim=False, mcp_command=sys.executable)
            findings = [f for f in _findings(d, home) if f.code == "hooks-not-firing"]
            self.assertEqual(len(findings), 1, [f.detail for f in findings])
            self.assertIn(str(plugin_root), findings[0].detail)   # placeholder EXPANDED
            self.assertNotIn("${CLAUDE_PLUGIN_ROOT}", findings[0].detail)
            self.assertIn("mokata setup claude", findings[0].detail)

    def test_a_healthy_plugin_install_is_quiet(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d) / "home"
            home.mkdir()
            _fake_plugin(home, ship_shim=True, mcp_command=sys.executable)
            self.assertEqual([f.code for f in _findings(d, home)], [])

    def test_doctor_spawns_no_subprocess(self):
        # The check runs inside `mokata doctor` on every invocation: it must stay a local
        # which/path probe. A handshake or a `--version` spawn here would put a process launch
        # on the doctor path for every wired hook.
        with tempfile.TemporaryDirectory() as d:
            home = Path(d) / "home"
            home.mkdir()
            _wire_settings(d, '"/nowhere/bin/mokata-hook" secret-guard')
            with mock.patch("subprocess.Popen", side_effect=AssertionError("spawned!")), \
                 mock.patch("subprocess.run", side_effect=AssertionError("spawned!")):
                self.assertEqual([f.code for f in _findings(d, home)], ["hooks-not-firing"])

    def test_the_check_is_wired_into_diagnose(self):
        import inspect

        from mokata.govern import doctor
        self.assertIn("hook_resolution_findings", inspect.getsource(doctor.diagnose))

    def test_the_check_never_raises_on_a_broken_surface(self):
        # An unparseable settings.json carries no wiring to judge — Claude Code cannot load it
        # either, so there are no mokata hooks wired FROM it. doctor must not crash on the way
        # to saying so (it is the command you run when things are already wrong).
        with tempfile.TemporaryDirectory() as d:
            home = Path(d) / "home"
            home.mkdir()
            path = Path(d) / ".claude" / "settings.json"
            path.parent.mkdir(parents=True)
            path.write_text("{not json at all", encoding="utf-8")
            self.assertEqual(_findings(d, home), [])

    def test_a_check_that_blows_up_is_loud_never_a_clean_bill_of_health(self):
        # The D5 lesson: an empty report means "nothing is wired". Handing that back for "the
        # check failed" would tell a plugin user their gates are fine when nobody looked.
        with mock.patch("mokata.hook_wiring._settings_hooks",
                        side_effect=RuntimeError("boom")):
            findings = _findings(".", ".")
        self.assertEqual([f.code for f in findings], ["hooks-unverifiable"])
        self.assertEqual(findings[0].severity, "error")
        self.assertIn("boom", findings[0].detail)
        self.assertIn("UNVERIFIED", findings[0].detail)
        self.assertIn("mokata setup claude", findings[0].detail)


# =====================================================================================
# 6 — nothing else changed
# =====================================================================================
class TestNoBehaviourChange(unittest.TestCase):
    def test_setup_still_wires_an_absolute_console_script(self):
        # B1 is untouched: `mokata setup claude` keeps writing the resolved absolute path.
        cmd = harness_setup._hook_command("secret_guard.py")
        self.assertIn("mokata-hook", cmd)
        self.assertNotIn("mokata-hook-launch", cmd, "setup must NOT route through the shim")
        self.assertNotIn("${CLAUDE_PLUGIN_ROOT}", cmd)
        self.assertEqual(cmd, f'"{harness_setup.resolved_console_script("mokata-hook")}" '
                              f'secret-guard')

    def test_setup_output_is_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            lines = []
            harness_setup.setup_harness("claude", root=d, assume_yes=True, out=lines.append)
            settings = json.loads(
                (Path(d) / ".claude" / "settings.json").read_text(encoding="utf-8"))
            for blocks in settings["hooks"].values():
                for entry in blocks:
                    for h in entry["hooks"]:
                        self.assertNotIn("mokata-hook-launch", h["command"])
            self.assertTrue(lines, "setup still reports what it wrote")

    def test_wired_shim_commands_are_still_recognised_as_mokata_hooks(self):
        # unsetup / re-setup rely on this: the shim path must still read as mokata's.
        for cmd in _hook_commands().values():
            self.assertTrue(harness_setup._is_mokata_hook(
                {"hooks": [{"type": "command", "command": cmd}]}), cmd)

    @needs_sh
    def test_shim_delegates_to_the_console_script_when_it_resolves(self):
        # The resolved-OK path: behaviour is EXACTLY today's — same subcommand, same args,
        # same exit code (including the security block).
        for code in (0, BLOCK_EXIT):
            with tempfile.TemporaryDirectory() as d:
                _fake_console_script(d, exit_code=code)
                env = _stripped_env(PATH=d)
                res = _run_wired(f'"{SHIM}"', env, extra="secret-guard --path x.py")
                self.assertEqual(res.returncode, code, res.stderr)
                self.assertIn("FAKE_HOOK_RAN secret-guard --path x.py", res.stdout)

    @needs_sh
    def test_shim_honours_an_explicit_override(self):
        with tempfile.TemporaryDirectory() as d:
            script = _fake_console_script(d, exit_code=0)
            env = _stripped_env(MOKATA_HOOK=str(script))
            res = _run_wired(f'"{SHIM}"', env, extra="dirty-track")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("FAKE_HOOK_RAN dirty-track", res.stdout)


# =====================================================================================
# REGRESSION — the live bug, at the level the user hits it
# =====================================================================================
@needs_sh
class TestHookResolveRegression(unittest.TestCase):
    """Runs the ACTUAL command shipped in hooks.json, under the ACTUAL failure environment
    (a GUI-launched minimal PATH), and asks the only question that matters: does the secret
    get blocked? On the old bare-name shape it does not — the gate is dead and silent."""

    OLD_SHAPE = "mokata-hook secret-guard"

    def test_hook_resolve_regression(self):
        env = _stripped_env(MOKATA_PYTHON=sys.executable)
        secret = f'--text "{FAKE_SECRET}" --path config.py'

        # (a) the OLD shape: a bare name on a stripped PATH. The gate does not fire — and
        #     nothing about the result says a security control just failed to run.
        old = _run_wired(self.OLD_SHAPE, env, extra=secret)
        self.assertNotEqual(old.returncode, BLOCK_EXIT,
                            "the bare-name shape must NOT be able to block (it never ran)")
        self.assertNotIn("BLOCKED", old.stdout + old.stderr)

        # (b) the NEW shape, same environment: the shim resolves an interpreter itself and the
        #     secret-guard gate BLOCKS with the reserved exit 2.
        wired = _hook_commands()["secret-guard"].replace(
            "${CLAUDE_PLUGIN_ROOT}", str(ROOT))
        new = _run_wired(wired, env, extra=secret)
        self.assertEqual(new.returncode, BLOCK_EXIT,
                         f"the gate must FIRE through the shim (stderr: {new.stderr})")
        self.assertIn("BLOCKED", new.stderr)

        # (c) and when nothing at all resolves, the new shape is LOUD (exit 1 + the remedy)
        #     rather than silently absent.
        dead = _run_wired(wired, _stripped_env(), extra=secret)
        self.assertEqual(dead.returncode, MISCONFIG_EXIT, dead.stderr)
        self.assertIn("mokata setup claude", dead.stderr)

    def test_clean_content_still_passes_through_the_shim(self):
        # The negative: a healthy run on a resolvable path adds no noise and no new exit code.
        env = _stripped_env(MOKATA_PYTHON=sys.executable)
        wired = _hook_commands()["secret-guard"].replace("${CLAUDE_PLUGIN_ROOT}", str(ROOT))
        res = _run_wired(wired, env, extra='--text "print(1)" --path ok.py')
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stderr.strip(), "")


if __name__ == "__main__":
    unittest.main()
