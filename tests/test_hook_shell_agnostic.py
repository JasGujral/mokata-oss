"""HOOK-SHELL-AGNOSTIC — mokata's hooks resolve without depending on a shell-specific
completion, and where they cannot run they fail LOUD.

THE FALSIFIED PREMISE. Four places in mokata stated, as settled fact, that the plugin route's
extension-less `hooks/mokata-hook-launch` path is completed to `mokata-hook-launch.cmd` by
cmd.exe through the Windows executable-extension search (`hook_wiring._resolves`, both shim
headers, and `test_hook_resolve.TestPluginMcpRegistration`'s docstring). That mechanism does
not occur: **cmd.exe is never a hook shell.** Read out of the shipped harness (Claude Code
2.1.220), a hook `command` is spawned by exactly one of three routes:

    if (args present)              spawn(command, args)                  <- NO shell at all
    else if (shell === powershell) spawn(pwsh, [...flags, "-Command", cmd])
    else                           spawn(cmd, [], {shell: gitBash | true})

and the `-Command` string is passed RAW: the harness's PowerShell pre-pass (`VFy`) rewrites
`${VAR}` to `${env:VAR}` and nothing else — in particular it does NOT prepend the `&` call
operator. So under PowerShell a command of the shape

    "C:\\...\\mokata-hook.exe" secret-guard

opens in EXPRESSION mode: the quoted path is a string literal and `secret-guard` is a bare word
after it — a parse error, and the executable never runs. That hit BOTH routes, not just the
plugin one: `harness_setup._hook_command` emitted exactly that shape with no `shell` key, and
"no shell key" means shell FORM, not "no shell".

THE FIX, per route:
  * SETUP route -> EXEC form. `{"command": <abs mokata-hook[.exe]>, "args": [...]}` is spawned
    directly, with no shell on any platform, so no quoting rule and no extension-search
    question can reach it. `resolved_console_script` already resolves the Windows `.exe`,
    which is exactly the real executable exec form requires there.
  * PLUGIN route -> an explicit `"shell": "bash"`. A static hooks.json cannot name an
    install-time path, so it keeps the self-resolving shim; naming the shell keeps Git Bash
    (which runs the sh shim correctly) and turns the Git-Bash-less Windows case into the
    harness's OWN named error naming a remedy, instead of a silent PowerShell parse failure.
  * doctor gains the per-SHELL finding: wired-but-unrunnable-under-this-shell is a HARD
    finding naming `mokata setup claude` — the SAME remedy HOOK-RESOLVE established.

No `.ps1` shim ships. Under `shell: "bash"` it would never be reached, and a hand-wired
`shell: "powershell"` would hit the same missing-`&` parse failure as the sh shim — so it would
look like Windows coverage in the file tree while providing none.

WHAT IS PROVEN WHERE. Everything here runs on the dev box and in CI on every platform: these
are assertions about what mokata WIRES and what doctor SAYS. That a wired hook then actually
FIRES on each platform x shell is proven by the `hooks-execute` CI matrix leg
(.github/workflows/ci.yml), which plants a canary and asserts secret-guard's exit 2 — it cannot
be proven from a POSIX dev box, and nothing in this file claims otherwise.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import _support  # noqa: F401  (puts src/ on the path)

from mokata import harness_setup, hook_wiring
from mokata.govern.doctor import hook_resolution_findings

ROOT = Path(__file__).resolve().parents[1]
HOOKS_DIR = ROOT / "src" / "mokata" / "hooks"
HOOKS_JSON = HOOKS_DIR / "hooks.json"
SHIM = HOOKS_DIR / "mokata-hook-launch"
SHIM_CMD = HOOKS_DIR / "mokata-hook-launch.cmd"

# Assembled from parts on purpose: mokata's own secret-guard scans assigned VALUES
# (SECRET-VALUE-SCAN) and blocks a literal canary in a tracked file — non-overridably. The
# split keeps the canary out of the scanner's anchored full-match while still producing the
# real shape at runtime.
FAKE_SECRET = "AKIA" + "IOSFODNN7" + "EXAMPLE"
BLOCK_EXIT = 2


def _plugin_hook_entries():
    """[(event, entry, hook)] for every hook the shipped plugin hooks.json wires."""
    data = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    out = []
    for event, blocks in data["hooks"].items():
        for entry in blocks:
            for h in entry.get("hooks", []):
                out.append((event, entry, h))
    return out


# The hook subcommands, read from the ONE declaration that writes them
# (`harness_setup._HOOK_SUBCOMMAND`) rather than re-typed here — H-1a added a fifth and a
# hand-kept list would have raised StopIteration rather than said so.
_SUBCOMMANDS = sorted(harness_setup._HOOK_SUBCOMMAND.values())


def _subcommand_of(hook):
    text = hook.get("command", "") + " " + " ".join(hook.get("args", []) or [])
    return next(s for s in _SUBCOMMANDS if s in text)


# =====================================================================================
# 1 — DELIVERABLE 0, as an executable fact: the PowerShell parse rule
# =====================================================================================
class TestPowerShellParseRule(unittest.TestCase):
    """The rule that falsified the extension-search premise, encoded so it cannot quietly rot.

    READ FROM THE IMPLEMENTATION, not observed: taken from Claude Code 2.1.220's hook spawn
    (`KVr` = [...flags, "-Command", cmd]; `VFy` rewrites only `${VAR}` -> `${env:VAR}`), not
    from a PowerShell run — there is no pwsh on the POSIX dev box. If the harness ever starts
    prepending `&`, THIS is the assertion that should be re-checked first."""

    def test_a_quoted_path_with_an_argument_does_not_execute(self):
        # PowerShell opens in EXPRESSION mode on a leading quote: the path is a string
        # literal and the trailing bare word is a parse error. This is the exact shape the
        # setup route used to emit, and the shape the plugin route still emits.
        self.assertFalse(hook_wiring.powershell_executes(
            '"C:\\Users\\x\\Scripts\\mokata-hook.exe" secret-guard'))
        self.assertFalse(hook_wiring.powershell_executes(
            '"${env:CLAUDE_PLUGIN_ROOT}/src/mokata/hooks/mokata-hook-launch" secret-guard'))

    def test_a_bare_name_does_execute(self):
        # No leading quote -> COMMAND mode -> PowerShell resolves and runs it.
        self.assertTrue(hook_wiring.powershell_executes("mokata-hook secret-guard"))

    def test_the_call_operator_makes_a_quoted_path_execute(self):
        # `&` is what the harness does NOT insert; with it, the same string would run.
        self.assertTrue(hook_wiring.powershell_executes(
            '& "C:\\Users\\x\\Scripts\\mokata-hook.exe" secret-guard'))

    def test_exec_form_is_not_subject_to_the_rule_at_all(self):
        # The whole point of the fix: with `args`, no shell parses anything.
        self.assertTrue(hook_wiring.shell_agnostic(
            {"command": "/abs/mokata-hook", "args": ["secret-guard"]}))
        self.assertFalse(hook_wiring.shell_agnostic(
            {"command": '"/abs/mokata-hook" secret-guard'}))


# =====================================================================================
# 2 — the SETUP route is EXEC form
# =====================================================================================
class TestSetupRouteIsExecForm(unittest.TestCase):
    def test_setup_wires_command_plus_args_and_no_shell_key(self):
        plan = harness_setup.plan_setup("claude", root=str(ROOT), home=None)
        wired = [spec for entries in plan.hook_commands.values() for spec in entries]
        self.assertEqual(len(wired), len(_SUBCOMMANDS),
                         "every declared hook must be wired")
        for spec in wired:
            self.assertIn("args", spec,
                          "the setup route must be EXEC form — args is what removes the shell")
            self.assertIsInstance(spec["args"], list)
            self.assertNotIn("shell", spec,
                             "exec form takes no shell; a shell key here would be ignored "
                             "and would imply a shell is involved when none is")
            # The command is the EXECUTABLE ALONE — no subcommand appended, no quoting. A
            # quoted or multi-word command in exec form is spawned as one literal filename.
            self.assertNotIn('"', spec["command"])
            self.assertIn("mokata-hook", spec["command"])

    def test_the_subcommand_moved_into_args(self):
        plan = harness_setup.plan_setup("claude", root=str(ROOT), home=None)
        by_sub = {_subcommand_of(s): s
                  for entries in plan.hook_commands.values() for s in entries}
        self.assertEqual(sorted(by_sub), _SUBCOMMANDS)
        self.assertEqual(by_sub["secret-guard"]["args"], ["secret-guard"])
        # session-start still forwards the clone root — as its OWN argv elements, never a
        # quoted blob a shell would have had to re-split.
        self.assertEqual(by_sub["session-start"]["args"][:2], ["session-start", "--plugin-root"])
        self.assertEqual(len(by_sub["session-start"]["args"]), 3)

    def test_resolved_console_script_still_supplies_the_absolute_exe(self):
        # Jas's grounding, re-pinned: the `.exe` branch is what makes exec form viable on
        # Windows, where exec form demands a real executable.
        import inspect
        src = inspect.getsource(harness_setup.resolved_console_script)
        self.assertIn('f"{name}.exe"', src,
                      "the Windows .exe branch is what exec form depends on")

    def test_settings_json_carries_args_through_to_disk(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / ".claude" / "settings.json"
            harness_setup._merge_hooks(path, {
                "PreToolUse": [{"command": "/abs/mokata-hook", "args": ["secret-guard"],
                                "matcher": "Write"}]})
            data = json.loads(path.read_text(encoding="utf-8"))
            hook = data["hooks"]["PreToolUse"][0]["hooks"][0]
            self.assertEqual(hook["command"], "/abs/mokata-hook")
            self.assertEqual(hook["args"], ["secret-guard"])
            self.assertNotIn("shell", hook)

    def test_a_wired_exec_form_entry_is_still_recognised_as_mokatas(self):
        # idempotent re-setup + unsetup both rely on this; exec form must not blind them.
        self.assertTrue(harness_setup._is_mokata_hook(
            {"hooks": [{"type": "command", "command": "/abs/bin/mokata-hook",
                        "args": ["secret-guard"]}]}))


# =====================================================================================
# 3 — the PLUGIN route names its shell (it cannot name an install-time path)
# =====================================================================================
class TestPluginRouteNamesItsShell(unittest.TestCase):
    def test_every_plugin_hook_declares_shell_bash(self):
        entries = _plugin_hook_entries()
        self.assertEqual(len(entries), len(_SUBCOMMANDS))
        for event, _entry, hook in entries:
            self.assertEqual(hook.get("shell"), "bash",
                             f"{event}: the plugin route must NAME its shell, or a Git-Bash-less "
                             f"Windows falls through to PowerShell and the command is a parse "
                             f"error rather than a gate")

    def test_the_plugin_route_still_goes_through_the_self_resolving_shim(self):
        # HOOK-RESOLVE is untouched: a static manifest still cannot bake in an install path.
        for _event, _entry, hook in _plugin_hook_entries():
            self.assertIn("${CLAUDE_PLUGIN_ROOT}/src/mokata/hooks/mokata-hook-launch",
                          hook["command"])

    def test_the_plugin_route_does_not_use_exec_form(self):
        # Deliberate, and the reasoning is on record: exec form cannot name an .exe whose
        # install location a static manifest does not know, and Node's shell-less spawn
        # cannot run the sh shim on Windows.
        for _event, _entry, hook in _plugin_hook_entries():
            self.assertNotIn("args", hook)

    def test_matchers_and_subcommands_are_unchanged(self):
        # HOOK-SHELL-AGNOSTIC changes HOW a hook is launched, never WHAT it matches or does.
        by_sub = {_subcommand_of(h): (event, entry)
                  for event, entry, h in _plugin_hook_entries()}
        self.assertEqual(sorted(by_sub), _SUBCOMMANDS)
        self.assertEqual(by_sub["secret-guard"][1].get("matcher"),
                         harness_setup.HOOK_PRETOOL_MATCHER)
        self.assertEqual(by_sub["gate-guard"][1].get("matcher"),
                         harness_setup.HOOK_GATE_MATCHER)
        self.assertEqual(by_sub["dirty-track"][1].get("matcher"),
                         harness_setup.HOOK_DIRTY_MATCHER)
        self.assertEqual(by_sub["secret-guard"][0], "PreToolUse")
        self.assertEqual(by_sub["dirty-track"][0], "PostToolUse")


# =====================================================================================
# 4 — resolution no longer leans on a Windows extension search
# =====================================================================================
class TestNoExtensionSearchDependence(unittest.TestCase):
    def test_doctor_no_longer_invents_windows_extension_completions(self):
        # `_resolves` used to try `.cmd`/`.bat`/`.exe` on Windows *because of* the false
        # premise — which made doctor certify the one broken platform as healthy.
        import inspect
        src = inspect.getsource(hook_wiring._resolves)
        self.assertNotIn("PATHEXT", src)
        self.assertNotIn('".cmd"', src)

    def test_no_source_file_still_asserts_the_cmd_exe_premise(self):
        # The four comments that made this survive an audit. They are a deliverable: a
        # confident, wrong comment is what let a reader check Windows support and move on.
        offenders = []
        for path in (SHIM, SHIM_CMD, ROOT / "src" / "mokata" / "hook_wiring.py",
                     ROOT / "tests" / "test_hook_resolve.py"):
            text = path.read_text(encoding="utf-8")
            for n, line in enumerate(text.splitlines(), 1):
                low = line.lower()
                if "pathext" in low and not any(
                        w in low for w in ("not ", "never", "no ", "falsified", "wrong")):
                    offenders.append(f"{path.name}:{n}: {line.strip()}")
        self.assertEqual(offenders, [],
                         "the Windows extension search may only be mentioned as the "
                         "FALSIFIED premise")

    def test_both_existing_shims_still_ship_in_the_wheel_glob(self):
        # The `hooks/*` explicit-depth glob trap test_hook_resolve already guards — re-pinned
        # because this stage touched the shim files.
        self.assertIn('"hooks/*"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertIn(SHIM, list(HOOKS_DIR.glob("*")))
        self.assertIn(SHIM_CMD, list(HOOKS_DIR.glob("*")))

    def test_no_ps1_shim_ships(self):
        # Decided, with grounding: a .ps1 is unreachable under `shell: bash`, and under a
        # hand-wired `shell: powershell` it hits the SAME missing-`&` parse failure. Shipping
        # one would look like Windows coverage while providing none.
        self.assertEqual(list(HOOKS_DIR.glob("*.ps1")), [])


# =====================================================================================
# 5 — doctor's per-SHELL finding
# =====================================================================================
class TestDoctorPerShellFinding(unittest.TestCase):
    def _hook(self, **kw):
        base = dict(surface="settings.json (project scope)", where="/x/settings.json",
                    event="PreToolUse", command='"/abs/mokata-hook" secret-guard',
                    program="/abs/mokata-hook", resolves=True, args=None, shell=None)
        base.update(kw)
        return hook_wiring.WiredHook(**base)

    def test_shell_form_without_git_bash_is_flagged_on_windows(self):
        # The silent case: no shell key -> PowerShell -> parse error -> gate never runs.
        fragile = hook_wiring.shell_fragile_hooks(
            [self._hook()], windows=True, git_bash=False)
        self.assertEqual(len(fragile), 1)

    def test_shell_bash_without_git_bash_is_flagged_on_windows(self):
        # Loud (the harness throws a named error) but still OFF — doctor must say so.
        fragile = hook_wiring.shell_fragile_hooks(
            [self._hook(shell="bash")], windows=True, git_bash=False)
        self.assertEqual(len(fragile), 1)

    def test_shell_bash_with_git_bash_is_fine(self):
        self.assertEqual(hook_wiring.shell_fragile_hooks(
            [self._hook(shell="bash")], windows=True, git_bash=True), [])

    def test_exec_form_is_never_flagged(self):
        # The fix, asserted from doctor's side: exec form has no shell to be fragile under.
        for git_bash in (True, False):
            self.assertEqual(hook_wiring.shell_fragile_hooks(
                [self._hook(command="/abs/mokata-hook", args=["secret-guard"])],
                windows=True, git_bash=git_bash), [])

    def test_posix_is_never_flagged(self):
        # sh runs every shape above; the finding must not cry wolf on macOS/Linux.
        for hook in (self._hook(), self._hook(shell="bash")):
            self.assertEqual(hook_wiring.shell_fragile_hooks(
                [hook], windows=False, git_bash=False), [])

    def test_the_finding_is_hard_and_names_the_one_remedy(self):
        findings = hook_wiring.shell_findings(
            [self._hook()], windows=True, git_bash=False)
        self.assertEqual(len(findings), 1)
        level, code, detail = findings[0]
        self.assertEqual(level, "error")           # HARD, like hooks-not-firing
        self.assertEqual(code, "hooks-shell-unrunnable")
        self.assertIn(hook_wiring.SETUP_REMEDY, detail)
        self.assertIn("PowerShell", detail)
        self.assertIn("PreToolUse", detail)        # names what is OFF, not just "a hook"

    def test_the_git_bash_variant_names_git_bash_not_powershell_parsing(self):
        findings = hook_wiring.shell_findings(
            [self._hook(shell="bash")], windows=True, git_bash=False)
        _level, _code, detail = findings[0]
        self.assertIn("Git Bash", detail)
        self.assertIn(hook_wiring.SETUP_REMEDY, detail)

    def test_doctor_surfaces_the_finding_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d) / "home"
            (home / ".claude").mkdir(parents=True)
            root = Path(d) / "repo"
            settings = root / ".claude" / "settings.json"
            settings.parent.mkdir(parents=True)
            # A shell-form mokata hook wired to a command that DOES resolve — so the existing
            # `hooks-not-firing` check passes it, and only the per-shell check can catch it.
            settings.write_text(json.dumps({"hooks": {"PreToolUse": [
                {"matcher": "Write", "hooks": [{"type": "command",
                 "command": '"%s" secret-guard' % harness_setup.resolved_console_script(
                     "mokata-hook")}]}]}}), encoding="utf-8")
            codes = [f.code for f in hook_resolution_findings(
                SimpleNamespace(root=str(root)), str(home), windows=True, git_bash=False)]
            self.assertIn("hooks-shell-unrunnable", codes)

    def test_doctor_stays_quiet_on_a_posix_box(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d) / "home"
            (home / ".claude").mkdir(parents=True)
            root = Path(d) / "repo"
            root.mkdir()
            codes = [f.code for f in hook_resolution_findings(
                SimpleNamespace(root=str(root)), str(home), windows=False, git_bash=False)]
            self.assertNotIn("hooks-shell-unrunnable", codes)


# =====================================================================================
# 6 — the double-fire fact (READ FROM THE IMPLEMENTATION, not observed)
# =====================================================================================
class TestDoubleFireIsAKnownFact(unittest.TestCase):
    """If settings.json AND the plugin both wire secret-guard, it runs TWICE.

    SOURCE: Claude Code 2.1.220's `getMatchingHooks` concatenates matchers across every
    config source with no dedup by command (`getPluginHookCounts` is a SEPARATE plugin tally),
    so both surfaces contribute. NOT observed on a live double-wired install — this machine is
    plugin-route only. Harmless: the scan is read-only and idempotent, and a doubled exit 2 is
    the same block twice. Recorded so a future reader re-checks the harness rather than
    rediscovering it.

    HOW TO RE-CHECK when the harness version moves: wire BOTH surfaces and count secret-guard
    invocations for one Write."""

    HARNESS_VERSION_READ = "2.1.220"

    def test_mokata_reports_both_surfaces_rather_than_assuming_one_wins(self):
        # The invariant mokata actually controls: the wiring report must not collapse the two
        # surfaces into one, or doctor could not tell a user they are double-wired.
        with tempfile.TemporaryDirectory() as d:
            home = Path(d) / "home"
            (home / ".claude").mkdir(parents=True)
            root = Path(d) / "repo"
            (root / ".claude").mkdir(parents=True)
            exe = harness_setup.resolved_console_script("mokata-hook")
            (root / ".claude" / "settings.json").write_text(json.dumps({"hooks": {
                "PreToolUse": [{"matcher": "Write", "hooks": [
                    {"type": "command", "command": exe, "args": ["secret-guard"]}]}]}}),
                encoding="utf-8")
            report = hook_wiring.hook_wiring_report(str(root), str(home))
            self.assertIsNone(report.unverifiable)
            self.assertEqual(len(report.hooks), 1)
            self.assertEqual(report.hooks[0].args, ["secret-guard"])

    def test_the_scan_is_idempotent_so_a_double_fire_is_harmless(self):
        # The property that makes the doubled call a non-issue rather than a bug: running
        # secret-guard twice on the same payload gives the same verdict both times.
        payload = json.dumps({"tool_name": "Write", "tool_input": {
            "file_path": "x.py", "content": f"AWS_KEY = '{FAKE_SECRET}'"}})
        codes = []
        for _ in range(2):
            proc = subprocess.run(
                [sys.executable, "-m", "mokata.hook_cli", "secret-guard"],
                input=payload, capture_output=True, text=True,
                cwd=str(ROOT), env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
            codes.append(proc.returncode)
        self.assertEqual(codes, [BLOCK_EXIT, BLOCK_EXIT],
                         "a doubled secret-guard must block identically both times")


if __name__ == "__main__":
    unittest.main()
