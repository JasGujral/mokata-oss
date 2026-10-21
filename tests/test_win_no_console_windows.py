"""0.0.25 B-01, on a REAL Windows desktop — no cmd window ever appears.

`test_win_cmd_flash.py` proves the FLAGS are right, on every OS. This module proves the OUTCOME on
Windows itself (it runs on the `windows-latest` legs of `ci.yml` and in `windows-probe.yml`):

  * CONTROL — the 0.0.24 shape (a console-less parent spawning `git` with no flag) DOES put a
    visible console window on this desktop. If it does not, this runner cannot observe console
    windows at all and every test here SKIPS, saying so: a sampler that can see nothing must
    never report "no windows" as a pass (doc 85 §7g — "not observable" is its own state).
  * THE IDE — `mokata ide` started through the real launcher (`launch.open_or_focus`), driven
    like a polling page (file / tree / search), shows NO new visible console window.
  * THE SHIM — with a pyenv-win-style `mokata-hook.bat` first on PATH, the resolver setup uses
    for the statusLine / hooks / MCP returns the real `.exe`, never the `.bat`.

Windows-only by construction (`skipUnless nt`); stdlib + ctypes only.
Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
import urllib.request

import _support  # noqa: F401  (puts src/ on the path)

WINDOWS = os.name == "nt"
CONSOLE_CLASSES = ("ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS")
SAMPLE_EVERY = 0.02


def _visible_console_windows() -> set:
    """Every visible top-level console window on this desktop, by handle."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    found = set()
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _each(hwnd, _lp):
        if user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, buf, 256)
            if buf.value in CONSOLE_CLASSES:
                found.add(int(hwnd))
        return True
    user32.EnumWindows(proto(_each), 0)
    return found


class _Sampler:
    """Records every console window that becomes visible while the block runs."""

    def __init__(self):
        self.baseline = _visible_console_windows()
        self.new = set()
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            self.new |= _visible_console_windows() - self.baseline
            time.sleep(SAMPLE_EVERY)

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *exc):
        time.sleep(0.3)                  # a window that opens at the very end still counts
        self._stop.set()
        self._t.join(5)
        return False


_CONTROL = textwrap.dedent("""
    import subprocess, time
    for _ in range(6):
        subprocess.run(["git", "--version"], capture_output=True)   # NO creationflags
        time.sleep(0.05)
""")


def _git_repo(d: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    quiet = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with open(os.path.join(d, "a.py"), "w", encoding="utf-8") as fh:
        fh.write("def f():\n    return 1\n")
    for argv in (["git", "init", "-q", d], ["git", "-C", d, "add", "."],
                 ["git", "-C", d, "commit", "-qm", "x"]):
        subprocess.run(argv, capture_output=True, check=True, env=env,
                       stdin=subprocess.DEVNULL, creationflags=quiet)


@unittest.skipUnless(WINDOWS, "Windows desktop behaviour — runs on the windows-latest legs")
class NoConsoleWindowOnWindows(unittest.TestCase):

    _control = None          # (observable, windows_seen, reason) — measured once, per process

    def _require_observable(self) -> None:
        """Run the CONTROL once (the 0.0.24 bug, reproduced on purpose: a DETACHED, console-less
        parent whose git children carry no flag), then skip THIS test by name if the runner
        could not show its window. Per test, not in setUpClass: a class-level skip makes tests
        vanish from the count (`test_suite_count_integrity`)."""
        cls = type(self)
        if cls._control is None:
            if shutil.which("git") is None:
                cls._control = (False, 0, "git is not on PATH on this runner")
            else:
                flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
                with _Sampler() as s:
                    p = subprocess.Popen([sys.executable, "-c", _CONTROL], creationflags=flags,
                                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)
                    p.wait(60)
                cls._control = (bool(s.new), len(s.new),
                                "NOT OBSERVABLE: the control (a console-less parent spawning git "
                                "unflagged) produced no visible console window on this runner, so "
                                "an empty result here would prove nothing. Run on an interactive "
                                "Windows desktop.")
        observable, _seen, reason = cls._control
        if not observable:
            self.skipTest(reason)

    def test_the_ide_shows_no_console_window_while_a_page_polls_it(self):
        self._require_observable()
        from mokata.ide import launch as L
        from mokata.ide.server import TOKEN_HEADER
        with tempfile.TemporaryDirectory() as d:
            _git_repo(d)
            subprocess.run([sys.executable, "-m", "mokata", "init", "--yes", "--path", d],
                           capture_output=True, stdin=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW)
            with _Sampler() as s:
                L.open_or_focus(d, browser=False, idle_secs=120)
                rec = L.running(d)
                self.assertTrue(rec, "the IDE server did not start")
                base = f"http://127.0.0.1:{rec['port']}"
                hdr = {TOKEN_HEADER: rec["token"]}

                def get(path):
                    req = urllib.request.Request(base + path, headers=hdr)
                    with urllib.request.urlopen(req, timeout=60) as r:
                        r.read()
                try:
                    for _ in range(5):                 # a polling page
                        get("/api/live")
                        get("/api/tree")
                        get("/api/file?path=a.py")
                    req = urllib.request.Request(
                        base + "/api/search", method="POST",
                        data=json.dumps({"q": "f"}).encode(),
                        headers={**hdr, "content-type": "application/json"})
                    with urllib.request.urlopen(req, timeout=60) as r:
                        r.read()
                finally:
                    pid = L._pinged_pid(rec)
                    if pid:
                        L._stop(pid, d)
        self.assertEqual(
            s.new, set(),
            f"{len(s.new)} console window(s) appeared while the IDE served a page "
            f"(the control produced {type(self)._control[1]}) — IDE-WIN-CMD-FLASH is back")

    def test_a_pyenv_style_bat_shim_on_path_is_never_wired(self):
        from mokata import harness_setup as HS
        real = HS.resolved_console_script("mokata-hook")
        if HS.is_windows_shim(real) or not os.path.isfile(real):
            self.skipTest(f"no real mokata-hook.exe in this install to prefer ({real!r})")
        with tempfile.TemporaryDirectory() as shims:
            with open(os.path.join(shims, "mokata-hook.bat"), "w", encoding="utf-8") as fh:
                fh.write("@echo off\r\n")
            old = os.environ["PATH"]
            os.environ["PATH"] = shims + os.pathsep + old
            try:
                self.assertTrue(shutil.which("mokata-hook", path=os.environ["PATH"])
                                .lower().endswith(".bat"), "precondition: the shim wins PATH")
                got = HS.resolved_console_script("mokata-hook")
            finally:
                os.environ["PATH"] = old
        self.assertFalse(HS.is_windows_shim(got),
                         f"setup would wire the .bat shim {got!r} — WIN-SHIM-WIRED is back")
        self.assertTrue(got.lower().endswith(".exe"), got)


if __name__ == "__main__":
    unittest.main()
