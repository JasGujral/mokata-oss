"""O5 (0.0.26) — the embedded IDE terminal on Windows: a ConPTY backend through `pywinpty`.

Business rules — what must be true for the user:
  * On Windows the page's terminal works: the program runs in a pseudo-console in the repo, input the
    page sends reaches it, its output reaches the page (UTF-8 intact, even a character split across
    two input posts), the window size follows the page, and closing the terminal ends the program.
    Same `/api/term/*` doors, same interface as the POSIX pty.
  * `pywinpty` is OPTIONAL (the `ide` extra, Windows only). Without it the IDE says so through
    `/api/term/caps` — never a blank — and nothing is spawned.
  * The Windows shell is the user's `%COMSPEC%`; an npm `claude.cmd` is run through it.
  * The POSIX backend is untouched (its own tests: test_id2_s3_terminal_join_links_context.py).

These run on Linux: a FAKE `winpty` module (pywinpty's `PtyProcess` surface — spawn/read/write/
setwinsize/isalive/terminate/close/exitstatus, read from pywinpty 3.1.0's ptyprocess.py) is injected
into `sys.modules` and the platform probe `terminal._windows` is patched. The real ConPTY run is owed
on a Windows machine.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import base64
import os
import queue
import shutil
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata.ide import terminal as TM
from mokata.init import init_repo

ORIGIN = {"Origin": "http://127.0.0.1:9999"}


class FakePty:
    """pywinpty's `PtyProcess`, in-memory: an echo program that exits on the line `exit`."""
    spawned: list = []

    def __init__(self, argv, cwd, env, dimensions):
        self.argv, self.cwd, self.env, self.dimensions = argv, cwd, env, dimensions
        self.sizes = []
        self.written = []
        self._q: "queue.Queue" = queue.Queue()
        self._alive = True
        self.terminated = self.closed = False
        self._q.put("PS ready ✓\r\n")

    @classmethod
    def spawn(cls, argv, cwd=None, env=None, dimensions=(24, 80), backend=None):
        if not isinstance(argv, list):
            raise TypeError("argv must be a list")
        inst = cls(argv, cwd, env, dimensions)
        cls.spawned.append(inst)
        return inst

    def read(self, size=1024):
        item = self._q.get()
        if item is None:
            raise EOFError("Pty is closed")
        return item

    def write(self, s):
        if not isinstance(s, str):
            raise TypeError("pywinpty writes str")
        self.written.append(s)
        if s.strip() == "exit":
            self._alive = False
            self._q.put(None)
            return len(s)
        self._q.put("echo:" + s)
        return len(s)

    def setwinsize(self, rows, cols):
        self.sizes.append((rows, cols))

    def isalive(self):
        return self._alive

    def terminate(self, force=False):
        self.terminated = True
        self._alive = False
        self._q.put(None)
        return True

    def close(self, force=False):
        self.closed = True
        self._alive = False
        self._q.put(None)

    @property
    def exitstatus(self):
        return None if self._alive else 0


def _fake_winpty():
    mod = types.ModuleType("winpty")
    mod.PtyProcess = FakePty
    return mod


def _silent(*_a, **_k):
    pass


class _OnWindows(unittest.TestCase):
    """The real app, in-process, on a "Windows" whose winpty is the fake."""

    with_winpty = True

    def setUp(self):
        FakePty.spawned = []
        self._t = tempfile.TemporaryDirectory()
        self.d = os.path.realpath(self._t.name)
        init_repo(root=self.d, profile="standard", assume_yes=True, out=_silent)
        patches = [mock.patch.object(TM, "_windows", lambda: True),
                   mock.patch.dict(sys.modules, {"winpty": _fake_winpty() if self.with_winpty
                                                 else None})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        from starlette.testclient import TestClient
        from mokata.ide.server import AppState, build_app
        self.state = AppState(self.d, "tok", 9999, None, False, terminal=True)
        self.state.terminals.programs = {"shell": lambda _r: ["cmd.exe"],
                                         "claude": lambda r: ["claude.exe"] + (
                                             ["--resume", r] if r else [])}
        self.c = TestClient(build_app(self.state), base_url="http://127.0.0.1:9999")
        self.assertEqual(200, self.c.get("/pair?t=tok").status_code)

    def tearDown(self):
        self.state.terminals.close_all()
        self._t.cleanup()

    def post(self, path, body):
        return self.c.post(path, json=body, headers=ORIGIN)

    def read_until(self, tid, marker, after=0, timeout=5.0):
        got, end = b"", time.time() + timeout
        while time.time() < end:
            r = self.c.get("/api/term/out", params={"id": tid, "after": after, "wait": 1}).json()
            got += base64.b64decode(r["data"])
            after = r["next"]
            if marker.encode("utf-8") in got:
                return got.decode("utf-8"), after, r
            if not r["alive"]:
                break
        self.fail(f"{marker!r} never arrived; got {got!r}")


class TheWindowsTerminalIsAConPtyThePageDrives(_OnWindows):

    def test_caps_offer_the_terminal_on_windows_with_pywinpty(self):
        caps = self.c.get("/api/term/caps").json()
        self.assertTrue(caps["available"], caps)
        self.assertEqual("", caps["reason"])

    def test_the_program_runs_in_a_pseudo_console_in_the_repo_and_round_trips_utf8(self):
        r = self.post("/api/term/open", {"program": "shell", "cols": 120, "rows": 40})
        self.assertEqual(200, r.status_code, r.text)
        tid = r.json()["id"]
        (pty,) = FakePty.spawned
        self.assertIsInstance(self.state.terminals.get(tid), TM.WinTerminal)
        self.assertEqual(["cmd.exe"], pty.argv)
        self.assertEqual(self.d, pty.cwd)
        self.assertEqual((40, 120), pty.dimensions, "pywinpty takes (rows, cols)")
        self.assertEqual("mokata-ide", pty.env["TERM_PROGRAM"])
        _, after, _ = self.read_until(tid, "PS ready ✓")
        self.assertEqual(200, self.post("/api/term/input", {"id": tid, "data": "dir é\r"}).status_code)
        self.read_until(tid, "echo:dir é", after)

    def test_a_character_split_across_two_input_posts_arrives_whole(self):
        tid = self.post("/api/term/open", {"program": "shell"}).json()["id"]
        term = self.state.terminals.get(tid)
        raw = "€".encode("utf-8")
        term.write(raw[:1])
        term.write(raw[1:])
        self.assertEqual("€", "".join(FakePty.spawned[0].written))

    def test_the_window_size_follows_the_page(self):
        tid = self.post("/api/term/open", {"program": "shell"}).json()["id"]
        self.assertEqual(200, self.post("/api/term/resize",
                                        {"id": tid, "cols": 90, "rows": 20}).status_code)
        self.assertEqual([(20, 90)], FakePty.spawned[0].sizes)

    def test_the_program_exiting_ends_the_terminal(self):
        tid = self.post("/api/term/open", {"program": "shell"}).json()["id"]
        self.post("/api/term/input", {"id": tid, "data": "exit"})
        term = self.state.terminals.get(tid)
        deadline = time.time() + 5
        while term.alive and time.time() < deadline:
            time.sleep(0.02)
        self.assertFalse(term.alive)
        self.assertEqual(0, term.exit_code)
        self.assertEqual(404, self.post("/api/term/input", {"id": tid, "data": "x"}).status_code)

    def test_close_ends_the_program_and_frees_the_console(self):
        tid = self.post("/api/term/open", {"program": "shell"}).json()["id"]
        self.assertTrue(self.post("/api/term/close", {"id": tid}).json()["closed"])
        pty = FakePty.spawned[0]
        self.assertTrue(pty.terminated, "the program was not ended")
        self.assertTrue(pty.closed, "the pseudo-console was not freed")
        self.assertEqual([], self.state.terminals.list())

    def test_the_program_set_stays_closed_and_resume_reaches_claude(self):
        self.assertEqual(409, self.post("/api/term/open", {"program": "powershell"}).status_code)
        self.state.terminals.open("claude", resume="S1")
        self.assertEqual(["claude.exe", "--resume", "S1"], FakePty.spawned[-1].argv)


class WithoutPywinptyTheIdeSaysSo(_OnWindows):
    with_winpty = False

    def test_caps_name_the_missing_package_and_nothing_spawns(self):
        caps = self.c.get("/api/term/caps").json()
        self.assertFalse(caps["available"])
        self.assertEqual(TM.NO_WINPTY, caps["reason"])
        self.assertIn("pywinpty", caps["reason"])
        r = self.post("/api/term/open", {"program": "shell"})
        self.assertEqual(409, r.status_code)
        self.assertEqual([], FakePty.spawned)


class TheWindowsProgramsAreTheUsers(unittest.TestCase):

    def test_the_shell_is_comspec(self):
        with mock.patch.object(TM, "_windows", lambda: True), \
                mock.patch.dict(os.environ, {"COMSPEC": r"C:\Windows\System32\cmd.exe"}):
            self.assertEqual([r"C:\Windows\System32\cmd.exe"], TM.default_programs()["shell"](None))

    def test_an_npm_claude_cmd_runs_through_the_shell(self):
        with mock.patch.object(TM, "_windows", lambda: True), \
                mock.patch.dict(os.environ, {"COMSPEC": "cmd.exe"}), \
                mock.patch.object(TM.shutil, "which", lambda _n: r"C:\npm\claude.cmd"):
            self.assertEqual(["cmd.exe", "/c", r"C:\npm\claude.cmd", "--resume", "S1"],
                             TM.default_programs()["claude"]("S1"))

    def test_posix_keeps_the_stdlib_pty_backend(self):
        if os.name != "nt" and shutil.which("sh"):
            host = TM.TerminalHost(tempfile.gettempdir(), programs={"shell": lambda _r: ["sh"]},
                                   enabled=(True, ""))
            try:
                t = host.open("shell")
                self.assertIs(type(t), TM.Terminal)
            finally:
                host.close_all()


if __name__ == "__main__":
    unittest.main()
