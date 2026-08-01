"""Stage B1 — Hook PATH fix (minimal-PATH regression).

`hooks/hooks.json` invokes the hook by BARE name (`mokata-hook session-start` /
`mokata-hook secret-guard`). On a GUI-launched macOS app the process inherits a minimal
PATH that does NOT include the console-script dir, so a bare `mokata-hook` never resolves
and the SessionStart briefing + PreToolUse secret-guard silently don't run — the same
failure class as the old `python3: command not found` lineage, already fixed for
`mokata-mcp`.

The fix: `setup` writes the ABSOLUTE `mokata-hook` entry-point path into the installed
hooks config, resolved by the SAME shared resolver `mokata-mcp` uses (shutil.which ->
console-script sibling of the running interpreter -> bare name). These tests assert:

  * the shared resolver returns the interpreter-sibling ABSOLUTE path when PATH is stripped
    of the console-script dir (the minimal-PATH / GUI-launch case `which` can't see);
  * `mokata-hook` and `mokata-mcp` resolve IDENTICALLY (one resolver, no drift);
  * `setup` writes that absolute path into the installed hooks config (SessionStart +
    PreToolUse secret-guard);
  * resolution DEGRADES CLEAN — bare name when nothing resolves, setup never crashes;
  * `--no-hooks` still skips hook installation entirely.
"""

import contextlib
import json
import os
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import harness_setup as HS

HOOK_COMMAND = "mokata-hook"


@contextlib.contextmanager
def _support_tmp():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


def _make_bindir(tmp: Path):
    """A fake interpreter bin dir holding `mokata-hook` + `mokata-mcp` console scripts and a
    `python` interpreter, so `Path(sys.executable).parent` finds the console-script siblings."""
    bindir = tmp / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    for name in (HOOK_COMMAND, HS.MCP_COMMAND, "python"):
        (bindir / name).write_text("#!/bin/sh\n", encoding="utf-8")
    return bindir


class _MinimalPath:
    """Context manager: strip PATH of any dir that resolves the console scripts and point
    `sys.executable` at a fake bin dir holding the siblings — the exact GUI-launch shape."""

    def __init__(self, bindir: Path, empty_path: Path):
        self.bindir = bindir
        self.empty_path = empty_path
        self._patches = []

    def __enter__(self):
        # PATH points ONLY at an empty dir → shutil.which(...) returns None (step 1 misses).
        self._patches.append(mock.patch.dict(os.environ, {"PATH": str(self.empty_path)}))
        # sys.executable's parent IS the sibling bin dir (step 2 must find it).
        self._patches.append(
            mock.patch.object(sys, "executable", str(self.bindir / "python")))
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        return False


def _exe_of(command: str) -> str:
    r"""The resolved executable token from a RENDERED hook command string (`"<exe>" <sub>`).

    `posix=False` matters on Windows: in posix mode `\` is an ESCAPE, so an UNQUOTED Windows
    path silently loses its separators (`C:\Users\…\bin\mokata-hook` parses to
    `C:UsersRUNNER~1…binmokata-hook`) and it is the PARSE, not the product, that then fails
    `isabs`. Non-posix mode keeps the quotes attached, so they are stripped explicitly."""
    token = shlex.split(command, posix=False)[0]
    if len(token) >= 2 and token[0] == token[-1] == '"':
        token = token[1:-1]
    return token


class TestSharedResolverMinimalPath(unittest.TestCase):
    def test_hook_resolves_to_interpreter_sibling_under_minimal_path(self):
        with _support_tmp() as tmp:
            bindir = _make_bindir(tmp)
            empty = tmp / "empty"
            empty.mkdir()
            with _MinimalPath(bindir, empty):
                resolved = HS.resolved_console_script(HOOK_COMMAND)
            self.assertTrue(os.path.isabs(resolved), f"expected absolute, got {resolved!r}")
            self.assertEqual(Path(resolved), bindir / HOOK_COMMAND)

    def test_hook_and_mcp_resolve_identically(self):
        # One shared resolver — mokata-hook and mokata-mcp resolve by the SAME rule, no fork.
        with _support_tmp() as tmp:
            bindir = _make_bindir(tmp)
            empty = tmp / "empty"
            empty.mkdir()
            with _MinimalPath(bindir, empty):
                hook = HS.resolved_console_script(HOOK_COMMAND)
                mcp = HS.resolved_mcp_command()
            self.assertEqual(Path(hook).parent, Path(mcp).parent)
            self.assertEqual(Path(hook).name, HOOK_COMMAND)
            self.assertEqual(Path(mcp).name, HS.MCP_COMMAND)

    def test_hook_command_is_absolute_under_minimal_path(self):
        with _support_tmp() as tmp:
            bindir = _make_bindir(tmp)
            empty = tmp / "empty"
            empty.mkdir()
            with _MinimalPath(bindir, empty):
                for script in ("session_start.py", "secret_guard.py"):
                    exe = _exe_of(HS._hook_command(script))
                    self.assertTrue(os.path.isabs(exe),
                                    f"{script}: expected absolute mokata-hook, got {exe!r}")
                    self.assertEqual(Path(exe), bindir / HOOK_COMMAND)

    def test_degrades_clean_to_bare_name_when_unresolvable(self):
        # No sibling anywhere + stripped PATH → the bare name (never crash). launch.sh stays
        # the documented fallback.
        with _support_tmp() as tmp:
            noexe = tmp / "noexe"      # a bin dir WITHOUT the console scripts
            noexe.mkdir()
            (noexe / "python").write_text("#!/bin/sh\n", encoding="utf-8")
            empty = tmp / "empty"
            empty.mkdir()
            with _MinimalPath(noexe, empty):
                self.assertEqual(HS.resolved_console_script(HOOK_COMMAND), HOOK_COMMAND)


class TestSetupWritesAbsoluteHookPath(unittest.TestCase):
    def test_setup_writes_absolute_mokata_hook_into_installed_config(self):
        with _support_tmp() as tmp:
            bindir = _make_bindir(tmp)
            empty = tmp / "empty"
            empty.mkdir()
            root = tmp / "proj"
            root.mkdir()
            with _MinimalPath(bindir, empty):
                HS.setup_harness("claude", root=str(root), assume_yes=True,
                                 out=lambda _s: None)
            settings = json.loads(
                (root / ".claude" / "settings.json").read_text(encoding="utf-8"))
            for event in ("SessionStart", "PreToolUse"):
                for entry in settings["hooks"][event]:
                    for h in entry["hooks"]:
                        # EXEC form: `command` IS the executable (`args` carries the subcommand),
                        # so it is read STRAIGHT — never shell-parsed. Parsing it as a shell word
                        # is what used to mangle the Windows path and fake a product failure.
                        exe = h["command"]
                        self.assertTrue(os.path.isabs(exe),
                                        f"{event}: hook not absolute: {exe!r}")
                        self.assertEqual(Path(exe), bindir / HOOK_COMMAND)
                        # HOOK-RESOLVE, the property that actually matters on Windows: an
                        # absolute path that names nothing is a silently dead gate — exactly
                        # the failure class B1 exists to prevent.
                        self.assertTrue(os.path.exists(exe),
                                        f"{event}: wired hook does not resolve: {exe!r}")


class TestNoHooksSkips(unittest.TestCase):
    def test_no_hooks_writes_no_hook_entries(self):
        with _support_tmp() as tmp:
            root = tmp / "proj"
            root.mkdir()
            HS.setup_harness("claude", root=str(root), with_hooks=False,
                             assume_yes=True, out=lambda _s: None)
            settings_path = root / ".claude" / "settings.json"
            # with_hooks=False leaves Claude settings alone (hooks + statusline + grant all
            # ride on with_hooks): no settings.json, or one with no hooks key.
            if settings_path.exists():
                settings = json.loads(settings_path.read_text(encoding="utf-8"))
                self.assertNotIn("hooks", settings)


if __name__ == "__main__":
    unittest.main()
