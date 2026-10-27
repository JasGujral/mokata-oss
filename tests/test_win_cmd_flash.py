"""0.0.25 `IDE-WIN-CMD-FLASH` + `WIN-SHIM-WIRED` — mokata never makes Windows flash cmd windows.

Two causes, both reported by Jas on Windows/pyenv-win (2026-10-19: "terminal continuously
opening … even when ide is closed"):
  1. the IDE server (below);
  2. setup wiring pyenv-win's `.bat` SHIM into settings.json — every statusLine refresh, hook and
     MCP start then runs through cmd.exe, IDE or no IDE (`TheWiringNeverPicksAShim`).

Cause 1, the IDE:

Reported by Jas on Windows (2026-10-19): with `mokata ide` open, cmd windows open and close
continuously. Mechanism (read from what a polling IDE spawns): the server was started with
DETACHED_PROCESS, so it had no console; Windows then gives every console child — `git` on each
`/api/file` refresh, `rg`, `code-review-graph`, any pyenv-win `.bat` shim — a NEW, VISIBLE window.

Business rules asserted here:
  * the server is started with a HIDDEN console (CREATE_NO_WINDOW), never DETACHED_PROCESS;
  * every spawn the IDE makes while serving pages passes CREATE_NO_WINDOW on Windows — checked by
    DRIVING the real IDE source (not by reading code), plus a static sweep so a new spawn added to
    `ide/` without the flag reds even if no test exercises it.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import ast
import os
import subprocess
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import quiet_spawn
from mokata.ide import launch as L

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "src", "mokata")


class TheServerGetsAHiddenConsole(unittest.TestCase):
    def test_windows_launch_flags_hide_the_console_and_never_detach_it(self):
        with mock.patch.object(L.os, "name", "nt"):
            flags = quiet_spawn.detached()["creationflags"]
        self.assertTrue(flags & CREATE_NO_WINDOW, "the server must own a hidden console")
        self.assertFalse(flags & DETACHED_PROCESS,
                         "DETACHED_PROCESS leaves the server console-less, so every console child "
                         "opens a visible window (and Windows ignores CREATE_NO_WINDOW beside it)")

    def test_hidden_is_empty_off_windows(self):
        with mock.patch.object(quiet_spawn, "_windows", return_value=False):
            self.assertEqual(quiet_spawn.hidden(), {})


class EverySpawnWhileServingIsWindowless(unittest.TestCase):
    """Drive the REAL IDE source as a polling page does, recording every spawn's kwargs."""

    def setUp(self):
        self.spawns = []
        real_init = subprocess.Popen.__init__
        spawns = self.spawns

        def _record(popen, args, *a, **kw):
            spawns.append((list(args) if not isinstance(args, str) else [args],
                           kw.get("creationflags", 0)))
            kw.pop("creationflags", None)        # POSIX Popen refuses the Windows-only kwarg
            return real_init(popen, args, *a, **kw)

        self._patches = [mock.patch.object(subprocess.Popen, "__init__", _record),
                         mock.patch.object(quiet_spawn, "_windows", return_value=True)]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def test_file_tree_and_search_spawn_only_hidden_children(self):
        from mokata.ide.source import RealSource
        with tempfile.TemporaryDirectory() as d:
            env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
            subprocess.run(["git", "init", "-q", d], check=True, env=env)
            with open(os.path.join(d, "a.py"), "w", encoding="utf-8") as fh:
                fh.write("def f():\n    return 1\n")
            subprocess.run(["git", "-C", d, "add", "."], check=True, env=env)
            subprocess.run(["git", "-C", d, "commit", "-qm", "x"], check=True, env=env)
            self.spawns.clear()                  # the fixture's own git calls are not the IDE's
            src = RealSource(d)
            for _ in range(3):                   # a polling page
                src.live()
                src.tree()
                src.file("a.py")
            src.search("f", "text", False, None)
        self.assertTrue(self.spawns, "the IDE spawned nothing — the drive no longer reaches git")
        loud = [argv for argv, flags in self.spawns if not flags & CREATE_NO_WINDOW]
        self.assertEqual(loud, [], "these IDE spawns would each flash a cmd window on Windows")


# CORPUS: THE WORKING TREE — the IDE source this checkout would run (a static floor over it).
def _ide_reach_files():
    return [os.path.join("ide", f) for f in sorted(os.listdir(os.path.join(SRC, "ide")))
            if f.endswith(".py")] + [os.path.join("knowledge", "crg_client.py"),
                             os.path.join("knowledge", "crg_build.py")]


class NoSpawnInTheIdeReachIsUnflagged(unittest.TestCase):
    """Static floor: every subprocess call in `ide/` and the CRG client passes `**hidden()`
    (launch.py's server start passes `**detached()`, asserted above)."""

    SPAWNS = {"run", "Popen", "check_output", "check_call", "call"}

    def test_every_spawn_is_flagged(self):
        bad = []
        for rel in _ide_reach_files():
            with open(os.path.join(SRC, rel), encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in self.SPAWNS
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "subprocess"):
                    continue
                starred = [k.value for k in node.keywords if k.arg is None]
                ok = any(isinstance(v, ast.Call) and getattr(v.func, "id", "") in
                         ("hidden", "detached") for v in starred)
                if not ok:
                    bad.append(f"{rel}:{node.lineno}")
        self.assertEqual(bad, [], "spawns in the IDE's reach without **hidden() — each one is a "
                                  "flashing cmd window on Windows")


class TheWiringNeverPicksAShim(unittest.TestCase):
    """`resolved_console_script` feeds the hooks, the statusLine and the MCP registration."""

    def _resolve(self, which_hit, files=(), windows=True, scripts=None):
        from mokata import harness_setup as HS
        with tempfile.TemporaryDirectory() as d:
            for f in files:
                open(os.path.join(d, f), "w", encoding="utf-8").close()
            with mock.patch.object(HS.shutil, "which", return_value=which_hit), \
                 mock.patch.object(HS.sys, "executable", os.path.join(d, "python.exe")), \
                 mock.patch.object(HS.sysconfig, "get_path", return_value=scripts or d):
                got = HS.resolved_console_script("mokata-hook", windows=windows)
            return got, d

    def test_a_pyenv_shim_on_path_loses_to_the_real_exe(self):
        got, d = self._resolve(r"C:\Users\j\.pyenv\pyenv-win\shims\mokata-hook.bat",
                               files=["mokata-hook.exe"])
        self.assertEqual(got, os.path.join(d, "mokata-hook.exe"))

    def test_pyenv_win_non_venv_scripts_dir_is_searched(self):
        with tempfile.TemporaryDirectory() as scripts:
            open(os.path.join(scripts, "mokata-hook.exe"), "w", encoding="utf-8").close()
            got, _ = self._resolve(r"C:\x\shims\mokata-hook.cmd", scripts=scripts)
            self.assertEqual(got, os.path.join(scripts, "mokata-hook.exe"))

    def test_a_shim_is_still_the_last_resort(self):
        shim = r"C:\x\shims\mokata-hook.bat"
        self.assertEqual(self._resolve(shim)[0], shim, "a flashing hook beats a dead one")

    def test_posix_is_unchanged(self):
        self.assertEqual(self._resolve("/usr/local/bin/mokata-hook", files=["mokata-hook"],
                                       windows=False)[0], "/usr/local/bin/mokata-hook")


class DoctorNamesAnAlreadyWiredShim(unittest.TestCase):
    def _root(self, statusline=None, mcp=None):
        import json
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d, True)
        os.makedirs(os.path.join(d, ".claude"))
        if statusline:
            with open(os.path.join(d, ".claude", "settings.json"), "w", encoding="utf-8") as fh:
                json.dump({"statusLine": {"type": "command", "command": statusline}}, fh)
        if mcp:
            with open(os.path.join(d, ".mcp.json"), "w", encoding="utf-8") as fh:
                json.dump({"mcpServers": {"mokata": {"command": mcp}}}, fh)
        return d

    def test_statusline_and_mcp_through_a_shim_are_one_warning_with_the_fix(self):
        from mokata.govern.doctor import windows_shim_findings
        d = self._root(statusline=r'"C:\p\shims\mokata-hook.bat" statusline',
                       mcp=r"C:\p\shims\mokata-mcp.cmd")
        f = windows_shim_findings(d, d, [])
        self.assertEqual(len(f), 1)
        self.assertEqual((f[0].severity, f[0].code), ("warning", "windows-shim-wired"))
        self.assertIn("statusLine", f[0].detail)
        self.assertIn("MCP server", f[0].detail)
        self.assertIn("mokata setup claude", f[0].detail)

    def test_a_shim_hook_is_named(self):
        from mokata.govern.doctor import windows_shim_findings
        hook = mock.Mock(event="PreToolUse", program=r"C:\p\shims\mokata-hook.bat")
        f = windows_shim_findings(self._root(), self._root(), [hook])
        self.assertIn("PreToolUse hook", f[0].detail)

    def test_real_exes_are_quiet(self):
        from mokata.govern.doctor import windows_shim_findings
        d = self._root(statusline=r'"C:\p\Scripts\mokata-hook.exe" statusline',
                       mcp=r"C:\p\Scripts\mokata-mcp.exe")
        hook = mock.Mock(event="PreToolUse", program=r"C:\p\Scripts\mokata-hook.exe")
        self.assertEqual(windows_shim_findings(d, d, [hook]), [])


if __name__ == "__main__":
    unittest.main()
