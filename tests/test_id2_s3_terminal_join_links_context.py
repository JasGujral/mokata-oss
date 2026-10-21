"""ID.S3 (0.0.25 Lane ID2) — the rest of the CLI↔IDE bridge: embedded terminal, session join,
OSC-8 deep links, bounded context injection.

Doc 109 §10, the ID.S3 row: "Event bus, extend the hooks to emit (widen `PostToolUse`, add `Stop`),
SSE, follow mode, `ide_*` MCP tools, context injection (bounded), OSC-8 deep links, session join,
embedded terminal running real `claude`" — exit: "A selection in the IDE appears in Claude's next
turn. Clicking a CLI reference opens the IDE."

Business rules — what must be true for the user:
  * EMBEDDED TERMINAL (doc 109 §5): the page runs an unmodified program in a REAL pty (isatty, a
    controlling terminal, a window size that follows the page). The program set is closed — the page
    names `claude` or `shell`, never an argv. It is bounded (a cap, a kill on close that takes the
    whole process group, an idle reaper for a page that went away) and its stream is never written
    anywhere. It is offered only to a window a human started at a TTY, never on SAMPLE data, and on
    Windows it says so as its own state instead of a blank.
  * SESSION JOIN (doc 109 §4): the joinable sessions are the ones `mokata windows`' registry and the
    snapshot timeline already know — live ones first. Joining focuses the window, tells every page to
    follow, and makes the session the default target of context the human sends. An unknown id
    changes nothing.
  * OSC-8 (doc 109 §4.2 item 4): `mokata query`'s `path:line` references become links to
    `/open?path=&line=` on the running IDE — only on a TTY that renders them with an IDE up; the
    bytes are exactly the old plain text otherwise. The link carries no token; `/open` is contained.
  * CONTEXT (doc 109 §4.2 item 1): the human's "send to session" queues a NAMED item whose text the
    server reads, bounds and redacts; the next turn of the target session carries it through the
    EXISTING UserPromptSubmit injection inside the SAME turn budget, once.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import hook_cli
from mokata.ide import context as CX
from mokata.ide import links as L
from mokata.ide import terminal as TM
from mokata.init import init_repo
from mokata.state import StateStore
from mokata.tdd_state import state_dir

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "mokata" / "ide" / "static"
POSIX = os.name != "nt"
ORIGIN = {"Origin": "http://127.0.0.1:9999"}

FAKE = r'''
import os, sys, subprocess
print("tty", os.isatty(0), flush=True)
try:
    os.close(os.open("/dev/tty", os.O_RDWR)); print("ctty yes", flush=True)
except OSError:
    print("ctty no", flush=True)
print("argv", " ".join(sys.argv[1:]) or "-", flush=True)
for line in sys.stdin:
    line = line.strip()
    if line == "size":
        c, r = os.get_terminal_size(0); print("size %dx%d" % (c, r), flush=True)
    elif line == "spawn":
        p = subprocess.Popen([sys.executable, "-c",
                              "import signal, time; signal.signal(signal.SIGHUP, signal.SIG_IGN); "
                              "print('gc-ready', flush=True); time.sleep(120)"])
        print("child %d" % p.pid, flush=True)
    else:
        print("echo:" + line, flush=True)
'''


def _silent(*_a, **_k):
    pass


def _repo(d: str) -> None:
    init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
    os.makedirs(os.path.join(d, "src"), exist_ok=True)
    with open(os.path.join(d, "src", "a.py"), "w", encoding="utf-8") as fh:
        fh.write("def total(x):\n    return x + 1\n\ny = total(1)\nz = 'plain'\n")


def _fake_programs(d: str):
    script = os.path.join(d, "fake_prog.py")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(FAKE)

    def claude(resume):
        return [sys.executable, script, "--resume", resume] if resume else [sys.executable, script]
    return {"shell": lambda _r: [sys.executable, script], "claude": claude}


def _alive(pid: int) -> bool:
    from mokata.session_registry import pid_alive   # the tree's own cross-platform probe
    if not pid_alive(pid):
        return False
    try:                                            # a zombie is not alive
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            return fh.read().split(")")[-1].split()[0] != "Z"
    except OSError:
        return True


def _register(d: str, sessions: dict) -> None:
    StateStore(state_dir(d)).write("session_registry", {"sessions": sessions})


def _entry(sid: str, pid: int, phase: str = "develop") -> dict:
    return {"session_id": sid, "started_at": "2026-10-19T10:00:00Z", "pid": pid, "repo_root": "",
            "last_seen": "2026-10-19T10:05:00Z", "phase": phase, "scope": "cache layer",
            "branch": ""}


def _dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"], stdin=subprocess.DEVNULL)
    p.wait()
    return p.pid


class _App(unittest.TestCase):
    """A real repo, the real app in-process, a paired browser client."""
    terminal = True
    demo = False

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.d = os.path.realpath(self._t.name)
        _repo(self.d)
        from starlette.testclient import TestClient
        from mokata.ide.server import AppState, build_app
        self.state = AppState(self.d, "tok", 9999, None, self.demo, terminal=self.terminal)
        if self.terminal and POSIX:
            self.state.terminals = TM.TerminalHost(self.d, programs=_fake_programs(self.d),
                                                   enabled=TM.available())
        self.app = build_app(self.state)
        self.c = TestClient(self.app, base_url="http://127.0.0.1:9999")
        self.assertEqual(200, self.c.get("/pair?t=tok").status_code)

    def tearDown(self):
        self.state.terminals.close_all()
        self._t.cleanup()

    def post(self, path, body, origin=True):
        return self.c.post(path, json=body, headers=ORIGIN if origin else {})

    def read_until(self, tid, marker, after=0, timeout=10.0):
        got, end = b"", time.time() + timeout
        while time.time() < end:
            r = self.c.get("/api/term/out", params={"id": tid, "after": after, "wait": 1}).json()
            got += base64.b64decode(r["data"])
            after = r["next"]
            if marker.encode() in got:
                return got.decode("utf-8", "replace"), after, r
            if not r["alive"]:
                break
        self.fail(f"{marker!r} never arrived; got {got!r}")


# ================================================================================ the terminal

@unittest.skipUnless(POSIX, "the pty terminal is POSIX-only; Windows reports its own state (below)")
class TheEmbeddedTerminalIsARealPtyThePageDrives(_App):

    def test_the_program_runs_in_a_real_pty_with_a_controlling_terminal_and_echoes_input(self):
        r = self.post("/api/term/open", {"program": "shell", "cols": 90, "rows": 20})
        self.assertEqual(200, r.status_code, r.text)
        tid = r.json()["id"]
        out, after, _ = self.read_until(tid, "argv")
        self.assertIn("tty True", out)
        self.assertIn("ctty yes", out)            # a session leader owning the pty, like a terminal
        self.assertEqual(200, self.post("/api/term/input", {"id": tid, "data": "hello\r"}).status_code)
        out, after, _ = self.read_until(tid, "echo:hello", after)

    def test_the_window_size_follows_the_page(self):
        tid = self.post("/api/term/open", {"program": "shell", "cols": 90, "rows": 20}).json()["id"]
        _, after, _ = self.read_until(tid, "argv")
        self.post("/api/term/input", {"id": tid, "data": "size\r"})
        _, after, _ = self.read_until(tid, "size 90x20", after)
        self.assertEqual({"cols": 132, "rows": 41},
                         self.post("/api/term/resize", {"id": tid, "cols": 132, "rows": 41}).json())
        self.post("/api/term/input", {"id": tid, "data": "size\r"})
        self.read_until(tid, "size 132x41", after)

    def test_the_program_set_is_closed_the_page_never_sends_an_argv(self):
        before = len(self.state.terminals.list())
        for body in ({"program": "bash -c 'touch /tmp/x'"}, {"program": ["sh", "-c", "id"]},
                     {"program": "python"}, {"program": "shell", "resume": "S1"},
                     {"program": "claude", "resume": "../../etc"}):
            with self.subTest(body=body):
                r = self.post("/api/term/open", body)
                self.assertIn(r.status_code, (400, 409), r.text)
        self.assertEqual(before, len(self.state.terminals.list()))

    def test_claude_is_started_unmodified_and_resume_only_names_a_known_session_not_live(self):
        dead = _dead_pid()
        _register(self.d, {"OLD1": _entry("OLD1", dead), "LIVE1": _entry("LIVE1", os.getpid())})
        tid = self.post("/api/term/open", {"program": "claude"}).json()["id"]
        self.read_until(tid, "argv -")                       # no flag of ours: no -p, no key
        r = self.post("/api/term/open", {"program": "claude", "resume": "OLD1"})
        self.assertEqual(200, r.status_code, r.text)
        self.read_until(r.json()["id"], "argv --resume OLD1")
        self.assertEqual(400, self.post("/api/term/open",
                                        {"program": "claude", "resume": "NOPE"}).status_code)
        live = self.post("/api/term/open", {"program": "claude", "resume": "LIVE1"})
        self.assertEqual(409, live.status_code)
        self.assertIn("join it", live.json()["note"])

    def test_it_is_bounded_a_cap_and_a_close_that_kills_the_whole_process_group(self):
        ids = [self.post("/api/term/open", {"program": "shell"}).json()["id"]
               for _ in range(TM.MAX_TERMINALS)]
        over = self.post("/api/term/open", {"program": "shell"})
        self.assertEqual(409, over.status_code)
        self.assertIn(f"at most {TM.MAX_TERMINALS}", over.json()["note"])
        tid = ids[0]
        _, after, _ = self.read_until(tid, "argv")
        self.post("/api/term/input", {"id": tid, "data": "spawn\r"})
        out, _, _ = self.read_until(tid, "gc-ready", after)   # it now ignores SIGHUP (nohup-like)
        grandchild = int(re.search(r"child (\d+)", out).group(1))
        pid = self.state.terminals.get(tid).proc.pid
        self.assertTrue(_alive(grandchild))
        self.assertEqual({"closed": True}, self.post("/api/term/close", {"id": tid}).json())
        end = time.time() + 5
        while time.time() < end and (_alive(pid) or _alive(grandchild)):
            time.sleep(0.05)
        self.assertFalse(_alive(pid))
        self.assertFalse(_alive(grandchild), "a program's children die with its terminal")
        self.assertEqual(404, self.c.get("/api/term/out", params={"id": tid}).status_code)
        self.assertEqual(200, self.post("/api/term/open", {"program": "shell"}).status_code)

    def test_a_page_that_stops_polling_has_its_terminal_reaped(self):
        self.state.terminals.idle_secs = 0.4
        tid = self.post("/api/term/open", {"program": "shell"}).json()["id"]
        pid = self.state.terminals.get(tid).proc.pid
        end = time.time() + 6
        while time.time() < end and self.state.terminals.list():
            time.sleep(0.1)
        self.assertEqual([], self.state.terminals.list())
        end = time.time() + 3
        while time.time() < end and _alive(pid):
            time.sleep(0.05)
        self.assertFalse(_alive(pid))

    def test_the_server_closes_every_terminal_on_exit(self):
        tids = [self.post("/api/term/open", {"program": "shell"}).json()["id"] for _ in range(2)]
        pids = [self.state.terminals.get(t).proc.pid for t in tids]
        self.assertEqual(2, self.state.terminals.close_all())
        for p in pids:
            self.assertFalse(_alive(p))
        import inspect
        from mokata.ide import server
        self.assertIn("state.terminals.close_all()", inspect.getsource(server.serve))

    def test_the_stream_is_never_written_anywhere(self):
        marker = "pty_stream_marker_never_stored"
        tid = self.post("/api/term/open", {"program": "shell"}).json()["id"]
        _, after, _ = self.read_until(tid, "argv")
        self.post("/api/term/input", {"id": tid, "data": marker + "\r"})
        self.read_until(tid, "echo:" + marker, after)
        self.post("/api/term/close", {"id": tid})
        for dirpath, _dirs, files in os.walk(self.d):
            for f in files:
                if f == "fake_prog.py":
                    continue
                with open(os.path.join(dirpath, f), "rb") as fh:
                    self.assertNotIn(marker.encode(), fh.read(), os.path.join(dirpath, f))

    def test_the_output_ring_is_bounded_and_a_late_reader_resumes_at_the_oldest_kept(self):
        tid = self.post("/api/term/open", {"program": "shell"}).json()["id"]
        _, after, _ = self.read_until(tid, "argv")
        # Under 1024: macOS's pty discards a canonical-mode line past MAX_CANON (1024), so a 2000-byte
        # line echoed nothing there and the ring never filled. 900 fits every POSIX pty.
        big = "y" * 900
        for _ in range(TM.MAX_BUFFER // 900 + 40):
            self.post("/api/term/input", {"id": tid, "data": big + "\r"})
        term = self.state.terminals.get(tid)
        end = time.time() + 10
        while time.time() < end and term.end < TM.MAX_BUFFER + 4096:
            time.sleep(0.05)
        r = self.c.get("/api/term/out", params={"id": tid, "after": 0, "wait": 0}).json()
        self.assertTrue(r["truncated"])
        self.assertLessEqual(len(base64.b64decode(r["data"])), TM.MAX_BUFFER)

    def test_the_doors_are_the_page_doors(self):
        from starlette.testclient import TestClient
        r = self.c.post("/api/term/open", json={"program": "shell"},
                        headers={"Origin": "http://evil.example"})
        self.assertEqual(403, r.status_code)
        anon = TestClient(self.app, base_url="http://127.0.0.1:9999")
        self.assertEqual(401, anon.post("/api/term/open", json={"program": "shell"},
                                        headers=ORIGIN).status_code)
        evil = TestClient(self.app, base_url="http://evil.example:9999")
        self.assertEqual(403, evil.post("/api/term/open", json={"program": "shell"}).status_code)
        self.assertEqual([], self.state.terminals.list())


@unittest.skipUnless(POSIX and os.path.exists("/bin/sh"), "a POSIX shell is needed")
class ALiveWindowRunsARealShellAndKillsItOnExit(unittest.TestCase):
    """A real `mokata ide` server child, started the way `mokata ide` at a TTY starts it, with the
    DEFAULT program set: the user's shell runs in the pty; stopping the window kills the shell."""

    def _call(self, path, body=None, timeout=10.0):
        import urllib.request
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", method="POST" if body is not None else "GET",
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers={"Host": f"127.0.0.1:{self.port}", "X-Mokata-Token": self.rec["token"],
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:     # noqa: S310 — loopback
            return json.loads(resp.read().decode("utf-8"))

    def test_shell_round_trip_then_the_window_exit_kills_it(self):
        from mokata.ide import launch
        with tempfile.TemporaryDirectory() as d:
            d = os.path.realpath(d)
            _repo(d)
            with mock.patch.dict(os.environ, {"SHELL": "/bin/sh"}):
                launch.open_or_focus(d, None, browser=False, idle_secs=60, terminal=True)
            self.rec = launch.running(d)
            self.port = int(self.rec["port"])
            try:
                caps = self._call("/api/term/caps")
                self.assertTrue(caps["available"], caps)
                t0 = time.perf_counter()
                tid = self._call("/api/term/open", {"program": "shell", "cols": 80, "rows": 24})["id"]
                self._call("/api/term/input", {"id": tid, "data": "echo pid=$$ sum=$((40+2))\r"})
                got, after, end = b"", 0, time.time() + 10
                while b"sum=42" not in got and time.time() < end:
                    r = self._call(f"/api/term/out?id={tid}&after={after}&wait=2")
                    got += base64.b64decode(r["data"])
                    after = r["next"]
                rt = (time.perf_counter() - t0) * 1000
                print(f"\n[ID2] live shell open+echo round trip: {rt:.0f} ms", file=sys.stderr)
                self.assertIn(b"sum=42", got)
                pid = int(re.search(rb"pid=(\d+) sum=42", got).group(1))
                self.assertTrue(_alive(pid))
            finally:
                os.kill(int(self.rec["pid"]), 15)
                for _ in range(60):
                    if not launch.running(d):
                        break
                    time.sleep(0.1)
            end = time.time() + 5
            while time.time() < end and _alive(pid):
                time.sleep(0.05)
            self.assertFalse(_alive(pid), "the shell outlived its window")


class TheTerminalIsOfferedOnlyWhereItShouldBe(unittest.TestCase):

    def _state(self, **kw):
        from mokata.ide.server import AppState
        d = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        _repo(d)
        return AppState(d, "tok", 9999, None, kw.pop("demo", False), **kw)

    def test_a_window_not_started_by_a_human_at_a_tty_cannot_start_processes(self):
        from mokata.ide.server import TERMINAL_NEEDS_TTY
        st = self._state(terminal=False)
        caps = st.terminals.caps()
        self.assertEqual((False, TERMINAL_NEEDS_TTY), (caps["available"], caps["reason"]))
        with self.assertRaises(TM.TerminalRefused):
            st.terminals.open("shell")

    def test_sample_data_has_no_terminal(self):
        st = self._state(demo=True, terminal=True)
        self.assertFalse(st.terminals.caps()["available"])

    def test_windows_says_not_available_as_its_own_state(self):
        with mock.patch.object(TM.os, "name", "nt"):
            ok, why = TM.available()
        self.assertFalse(ok)
        self.assertEqual(TM.NOT_ON_PLATFORM, why)
        self.assertIn("not available on Windows yet", why)
        host = TM.TerminalHost("/", enabled=(ok, why))
        self.assertEqual(why, host.caps()["reason"])
        with self.assertRaises(TM.TerminalRefused) as cm:
            host.open("shell")
        self.assertEqual(why, str(cm.exception))

    def test_the_launcher_passes_the_terminal_only_for_a_human_tty(self):
        from mokata.cli_commands import ide as cli_ide
        for stdin_tty, stdout_tty, want in ((True, True, True), (False, True, False),
                                            (True, False, False)):
            with mock.patch.object(sys, "stdin") as si, mock.patch.object(sys, "stdout") as so:
                si.isatty.return_value, so.isatty.return_value = stdin_tty, stdout_tty
                self.assertEqual(want, cli_ide._human_tty())
        from mokata.ide import launch
        seen = []

        def fake_popen(cmd, **kw):
            seen.append(cmd)
            raise RuntimeError("stop here")
        with mock.patch.object(launch, "running", return_value=None), \
                mock.patch.object(launch.subprocess, "Popen", fake_popen):
            for terminal in (True, False):
                with self.assertRaises(RuntimeError):
                    launch._open_or_focus("/r", None, False, False, None, terminal)
        self.assertIn("--terminal", seen[0])
        self.assertNotIn("--terminal", seen[1])

    def test_the_terminal_is_opt_in_human_only_and_never_inside_claude_code(self):
        """0.0.25 — `mokata ide` offers no terminal unless asked (`--terminal`); asked, it still
        needs a human TTY and refuses inside a Claude Code session (CLAUDECODE=1)."""
        import argparse
        import io
        from mokata.cli_commands import ide as cli_ide
        cases = ((False, True, None, False), (True, True, None, True),
                 (True, False, None, False), (True, True, "1", False))
        for flag, tty, cc, want in cases:
            with self.subTest(flag=flag, tty=tty, claudecode=cc), \
                    mock.patch.object(cli_ide, "_human_tty", return_value=tty), \
                    mock.patch.dict(os.environ, {"CLAUDECODE": cc} if cc else {}, clear=False), \
                    mock.patch("sys.stderr", io.StringIO()) as err:
                if not cc:
                    os.environ.pop("CLAUDECODE", None)
                self.assertEqual(want, cli_ide._terminal_wanted(argparse.Namespace(terminal=flag)))
                if flag and not want:
                    self.assertIn("--terminal ignored", err.getvalue())

    def test_a_running_window_without_the_terminal_is_restarted_for_a_tty_launch_never_downgraded(self):
        from mokata.ide import launch
        rec = {"port": 1, "token": "t", "demo": False}
        calls = []
        with mock.patch.object(launch, "running", return_value=rec), \
                mock.patch.object(launch, "_has_terminal", return_value=False), \
                mock.patch.object(launch, "_call", lambda *a, **k: calls.append(a[2]) or {}), \
                mock.patch.object(launch, "_open", lambda *a: None):
            got = launch._open_or_focus("/r", None, False, False, None, False)
        self.assertFalse(got.started)                      # a hook-opened refocus never restarts
        self.assertIn("/api/focus", calls)


class TheTerminalScreenModel(unittest.TestCase):
    """The page's VT model, driven under node (skipped without it): what an Ink TUI and a shell
    emit lands on the right cells."""

    def _run(self, js: str):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not on PATH — the page logic is exercised in a browser instead")
        src = (STATIC / "terminal.js").read_text(encoding="utf-8")
        prelude = ("const window = {MK: {$: () => null, el: () => null}};"
                   "const location = {href: 'http://127.0.0.1:9/', origin: 'http://127.0.0.1:9'};\n")
        helpers = ("const S = window.MK.TermScreen;\n"
                   "const txt = (s) => s.g.map(l => l.map(c => c.ch).join('').replace(/ +$/, ''));\n")
        p = subprocess.run([node, "-e", prelude + src + helpers + js], capture_output=True,
                           text=True, timeout=60, stdin=subprocess.DEVNULL, encoding="utf-8")
        self.assertEqual(0, p.returncode, p.stderr)
        return json.loads(p.stdout)

    def test_text_cursor_addressing_erase_and_wrap(self):
        got = self._run(r"""
const s = new S(10, 4);
s.feed('hello\r\nworld');
const a = txt(s);
s.feed('\x1b[2J\x1b[H' + 'abcdefghijKL');        // clear + home, then wrap past col 10
const b = txt(s);
s.feed('\x1b[1;3H\x1b[K');                        // erase to end of line from col 3
const c = txt(s);
console.log(JSON.stringify([a, b, c]));
""")
        self.assertEqual(["hello", "world", "", ""], got[0])
        self.assertEqual(["abcdefghij", "KL", "", ""], got[1])
        self.assertEqual(["ab", "KL", "", ""], got[2])

    def test_an_ink_style_redraw_overwrites_in_place(self):
        got = self._run(r"""
const s = new S(20, 5);
s.feed('> thinking\r\n  step 1\r\n');
s.feed('\x1b[2K\x1b[1A\x1b[2K\x1b[1A\x1b[2K\x1b[G' + '> done\r\n  ok\r\n');
console.log(JSON.stringify(txt(s)));
""")
        self.assertEqual(["> done", "  ok", "", "", ""], got)

    def test_colours_alt_screen_scrollback_and_osc8_links(self):
        got = self._run(r"""
const s = new S(12, 3);
s.feed('\x1b[1;31mred\x1b[0m \x1b[38;5;46mg\x1b[38;2;1;2;3mt\x1b[0m');
const st = [s.g[0][0].st, s.g[0][4].st, s.g[0][5].st];
s.feed('\r\nl2\r\nl3\r\nl4');                     // scrolls one line into the scrollback
const sb = s.sb.map(l => l.map(c => c.ch).join('').trim());
s.feed('\x1b[?1049h\x1b[H' + 'ALT');
const alt = txt(s);
s.feed('\x1b[?1049l');
const back = txt(s);
s.feed('\r\n\x1b]8;;http://127.0.0.1:9/open?path=src/a.py&line=3\x1b\\src/a.py:3\x1b]8;;\x1b\\ x');
const link = [s.g[2][0].link || null, s.g[2][11].link || null, txt(s)[2]];
s.feed('\x1b[?1h\x1b[?2004h');
console.log(JSON.stringify({st, sb, alt, back, link, app: s.appCursor, paste: s.paste}));
""")
        self.assertEqual({"b": 1, "fg": "#ff6b6b"}, got["st"][0])
        self.assertEqual("rgb(0,255,0)", got["st"][1]["fg"])
        self.assertEqual("rgb(1,2,3)", got["st"][2]["fg"])
        self.assertEqual(["red gt"], got["sb"])
        self.assertEqual(["ALT", "", ""], got["alt"])
        self.assertEqual(["l2", "l3", "l4"], got["back"])
        self.assertEqual("http://127.0.0.1:9/open?path=src/a.py&line=3", got["link"][0])
        self.assertIsNone(got["link"][1])               # the link ended where OSC 8 closed it
        self.assertEqual("src/a.py:3 x", got["link"][2])  # the escape itself is never drawn
        self.assertTrue(got["app"])
        self.assertTrue(got["paste"])


class ThePageCarriesTheBridge(unittest.TestCase):

    def test_the_new_scripts_are_served_loaded_and_build_no_markup(self):
        from mokata.ide.server import STATIC_FILES
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        for name in ("terminal.js", "bridge.js"):
            self.assertIn(name, STATIC_FILES)
            self.assertIn(f'<script src="/static/{name}"></script>', html)
            js = (STATIC / name).read_text(encoding="utf-8")
            for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(",
                        "new Function", "http://", "https://"):
                self.assertNotIn(bad, js, f"{name}: {bad}")
        self.assertLess(html.index("terminal.js"), html.index("boot.js"))
        for ident in ("term-drawer", "term-screen", "term-claude", "term-shell", "join-btn",
                      "join-pop", "send-ctx", "send-diff"):
            self.assertIn(f'id="{ident}"', html)

    def test_join_turns_follow_on_and_an_open_link_opens_the_file(self):
        bridge = (STATIC / "bridge.js").read_text(encoding="utf-8")
        app = (STATIC / "app.js").read_text(encoding="utf-8")
        boot = (STATIC / "boot.js").read_text(encoding="utf-8")
        self.assertIn('e.kind === "join"', bridge)
        self.assertIn("MK.setFollow(true)", bridge)
        self.assertIn("MK.setFollow = setFollow", app)
        self.assertIn("MK.onBusExtra", app)
        self.assertIn("MK.openFromLink", boot)
        self.assertIn('searchParams.get("open")', bridge)


# ================================================================================ session join

class TheIdeJoinsALiveSession(_App):
    terminal = False

    def setUp(self):
        super().setUp()
        from mokata.ide import snapshots as SN
        self.dead = _dead_pid()
        _register(self.d, {"LIVE-AAAA1111": _entry("LIVE-AAAA1111", os.getpid()),
                           "GONE-BBBB2222": _entry("GONE-BBBB2222", self.dead, "review")})
        SN.capture(self.d, os.path.join(self.d, "src", "a.py"), SN.AFTER, session_id="SNAP-CCCC3333")

    def test_the_joinable_list_is_the_registry_plus_the_snapshots_live_first(self):
        r = self.c.get("/api/join").json()
        rows = {s["id"]: s for s in r["sessions"]}
        self.assertEqual({"LIVE-AAAA1111", "GONE-BBBB2222", "SNAP-CCCC3333"}, set(rows))
        self.assertEqual("LIVE-AAAA1111", r["sessions"][0]["id"])
        self.assertIs(True, rows["LIVE-AAAA1111"]["alive"])
        self.assertIs(False, rows["GONE-BBBB2222"]["alive"])
        self.assertIsNone(rows["SNAP-CCCC3333"]["alive"])
        self.assertEqual(["snapshots"], rows["SNAP-CCCC3333"]["known_by"])
        self.assertEqual("develop", rows["LIVE-AAAA1111"]["phase"])
        self.assertIsNone(r["joined"])
        # A GET prunes nothing: the dead window's registry row is still there afterwards.
        reg = StateStore(state_dir(self.d)).read("session_registry")
        self.assertIn("GONE-BBBB2222", reg["sessions"])

    def test_joining_focuses_follows_and_targets_that_session(self):
        seq = self.state.bus.seq
        r = self.post("/api/join", {"session": "LIVE-AAAA1111"})
        self.assertEqual(200, r.status_code, r.text)
        self.assertEqual({"joined": "LIVE-AAAA1111", "focus": "LIVE-AAAA1111", "follow": True,
                          "alive": True}, r.json())
        self.assertEqual("LIVE-AAAA1111", self.state.focus)
        evts, _ = self.state.bus.after(seq)
        self.assertEqual([("focus", "LIVE-AAAA1111"), ("join", "LIVE-AAAA1111")],
                         [(e["kind"], e.get("focus") or e.get("session")) for e in evts])
        self.assertEqual("LIVE-AAAA1111", self.c.get("/api/state").json()["focus"])
        self.assertEqual("LIVE-AAAA1111", self.c.get("/api/join").json()["joined"])
        q = self.post("/api/context", {"kind": "file", "path": "src/a.py"}).json()["queued"]
        self.assertEqual("LIVE-AAAA1111", q["target"])

    def test_an_unknown_session_changes_nothing_and_leaving_clears_the_join(self):
        self.post("/api/join", {"session": "LIVE-AAAA1111"})
        r = self.post("/api/join", {"session": "NOT-A-SESSION"})
        self.assertEqual(404, r.status_code)
        self.assertEqual("LIVE-AAAA1111", self.state.joined)
        self.assertEqual({"joined": None}, self.post("/api/join", {"session": None}).json())
        self.assertIsNone(self.state.joined)
        self.assertEqual(403, self.c.post("/api/join", json={"session": "LIVE-AAAA1111"},
                                          headers={"Origin": "http://evil.example"}).status_code)
        self.assertIsNone(self.state.joined)


# ================================================================================ OSC-8 links

class _Tty(io.StringIO):
    def isatty(self):
        return True


class CliReferencesBecomeLinksOnlyWhereTheyWork(unittest.TestCase):

    def test_hyperlinks_are_only_claimed_for_a_tty_that_renders_them(self):
        tty, pipe = _Tty(), io.StringIO()
        self.assertFalse(L.hyperlinks_supported(pipe, {"TERM_PROGRAM": "WezTerm"}))
        for env in ({"TERM_PROGRAM": "WezTerm"}, {"TERM_PROGRAM": "iTerm.app"},
                    {"WT_SESSION": "x"}, {"TERM": "xterm-kitty"}, {"VTE_VERSION": "7600"},
                    {"TERM_PROGRAM": "mokata-ide"}, {"MOKATA_HYPERLINKS": "1", "TERM": "dumb"}):
            with self.subTest(env=env):
                self.assertTrue(L.hyperlinks_supported(tty, env))
        for env in ({}, {"TERM": "xterm-256color"}, {"TERM": "dumb", "TERM_PROGRAM": "WezTerm"},
                    {"CI": "true", "TERM_PROGRAM": "WezTerm"}, {"VTE_VERSION": "4000"},
                    {"MOKATA_HYPERLINKS": "0", "TERM_PROGRAM": "WezTerm"}):
            with self.subTest(env=env):
                self.assertFalse(L.hyperlinks_supported(tty, env))

    def test_a_reference_links_to_the_running_ide_with_no_token_and_the_same_visible_text(self):
        with tempfile.TemporaryDirectory() as d:
            d = os.path.realpath(d)
            os.makedirs(os.path.join(d, "src"))
            link = L.Linker(d, _Tty(), {"TERM_PROGRAM": "WezTerm"}, port=23456)
            out = link.ref("src/a b.py", 3, "src/a b.py:3")
            self.assertEqual("\x1b]8;;http://127.0.0.1:23456/open?path=src/a%20b.py&line=3"
                             "\x1b\\src/a b.py:3\x1b]8;;\x1b\\", out)
            self.assertEqual("src/a b.py:3", re.sub(r"\x1b\]8;;[^\x1b]*\x1b\\", "", out))
            self.assertEqual("/etc/passwd:1", link.ref("/etc/passwd", 1, "/etc/passwd:1"))
            self.assertEqual("x:1", link.ref(os.path.join(d, ".mokata", "x"), 1, "x:1"))
            with mock.patch.object(L, "_running_port", return_value=None):
                self.assertEqual("src/a.py:3", L.Linker(d, _Tty(), {"TERM_PROGRAM": "WezTerm"})
                                 .ref("src/a.py", 3, "src/a.py:3"))

    def _query(self, d, stream, env, port):
        from mokata.cli_commands.knowledge import cmd_query
        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.object(L, "_running_port", return_value=port), \
                redirect_stdout(stream):
            for k in ("CI", "MOKATA_HYPERLINKS"):
                os.environ.pop(k, None)
            cmd_query(argparse.Namespace(path=d, kind="callers", target="total", depth=2,
                                         json=False))
        return stream.getvalue()

    def test_mokata_query_links_on_a_tty_with_an_ide_and_is_byte_identical_otherwise(self):
        with tempfile.TemporaryDirectory() as d:
            d = os.path.realpath(d)
            _repo(d)
            env = {"TERM_PROGRAM": "WezTerm"}
            linked = self._query(d, _Tty(), env, 24680)
            self.assertIn("\x1b]8;;http://127.0.0.1:24680/open?path=src/a.py&line=4\x1b\\"
                          "src/a.py:4\x1b]8;;\x1b\\", linked)
            plain_pipe = self._query(d, io.StringIO(), env, 24680)
            plain_no_ide = self._query(d, _Tty(), env, None)
            self.assertNotIn("\x1b", plain_pipe)
            self.assertEqual(plain_pipe, plain_no_ide)
            self.assertIn("\n  src/a.py:4  ", plain_pipe)
            self.assertEqual(plain_pipe, re.sub(r"\x1b\]8;;[^\x1b]*\x1b\\", "", linked))

    def test_the_link_target_opens_the_file_for_the_paired_browser_only(self):
        from starlette.testclient import TestClient
        from mokata.ide.server import AppState, build_app
        with tempfile.TemporaryDirectory() as d:
            d = os.path.realpath(d)
            _repo(d)
            app = build_app(AppState(d, "tok", 9999, None, False))
            anon = TestClient(app, base_url="http://127.0.0.1:9999")
            self.assertEqual(401, anon.get("/open?path=src/a.py&line=4",
                                           follow_redirects=False).status_code)
            c = TestClient(app, base_url="http://127.0.0.1:9999")
            c.get("/pair?t=tok")
            r = c.get("/open?path=src/a.py&line=4", follow_redirects=False)
            self.assertEqual((303, "/?open=src/a.py&line=4"), (r.status_code, r.headers["location"]))
            for bad in ("../etc/passwd", "/etc/passwd", ".mokata/config.json", ""):
                with self.subTest(bad=bad):
                    self.assertEqual(400, c.get("/open", params={"path": bad, "line": 1},
                                                follow_redirects=False).status_code)


# ================================================================================ context

class _Ctx(_App):
    terminal = False

    def hook(self, sid: str, prompt: str = "please continue"):
        """Run the REAL UserPromptSubmit hook as Claude Code would; return its additionalContext
        payloads (each printed JSON object), or [] when it said nothing."""
        payload = {"session_id": sid, "cwd": self.d, "prompt": prompt,
                   "hook_event_name": "UserPromptSubmit"}
        buf = io.StringIO()
        with mock.patch.object(hook_cli, "_read_stdin_bounded", return_value=json.dumps(payload)), \
                redirect_stdout(buf):
            self.assertEqual(0, hook_cli.user_prompt_submit_main([]))
        return [json.loads(x) for x in buf.getvalue().splitlines() if x.strip()]

    def write(self, rel: str, text: str) -> None:
        p = os.path.join(self.d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)


class ASelectionInTheIdeAppearsInClaudesNextTurn(_Ctx):

    def test_the_selection_rides_the_next_turn_once_through_the_existing_injection(self):
        r = self.post("/api/context", {"kind": "selection", "path": "src/a.py", "lines": "1-2",
                                       "text": "IGNORED page text"})
        self.assertEqual(200, r.status_code, r.text)
        q = r.json()["queued"]
        self.assertEqual(("selection", "src/a.py", [1, 2], 0), (q["kind"], q["path"], q["lines"],
                                                                q["redacted"]))
        out = self.hook("S1")
        self.assertEqual(1, len(out), "ONE payload: the existing channel, not a second one")
        hso = out[0]["hookSpecificOutput"]
        self.assertEqual("UserPromptSubmit", hso["hookEventName"])
        ctx = hso["additionalContext"]
        self.assertIn(CX.HEADER, ctx)
        self.assertIn("selection src/a.py:1-2", ctx)
        self.assertIn("def total(x):\n    return x + 1", ctx)
        self.assertNotIn("IGNORED page text", ctx)          # the server read the repo, not the page
        self.assertEqual([], CX.read(self.d))                # drained on delivery
        again = self.hook("S1")
        self.assertFalse(any(CX.HEADER in o["hookSpecificOutput"]["additionalContext"]
                             for o in again), "unchanged context is never re-sent")

    def test_a_secret_line_is_redacted_before_anything_is_stored_and_a_sensitive_file_refused(self):
        key = "AKIA" + "IOSFODNN7" + "EXAMPLE"
        self.write("src/conf.py", f"region = 'eu'\naws = '{key}'\nport = 5432\n")
        q = self.post("/api/context", {"kind": "file", "path": "src/conf.py"}).json()["queued"]
        self.assertEqual(1, q["redacted"])
        text = CX.read(self.d)[0]["text"]
        self.assertIn("region = 'eu'", text)
        self.assertIn("port = 5432", text)
        self.assertNotIn(key, text)
        self.assertIn("redacted by mokata's secret scan", text)
        for dirpath, _d, files in os.walk(os.path.join(self.d, ".mokata")):
            for f in files:
                with open(os.path.join(dirpath, f), "rb") as fh:
                    self.assertNotIn(key.encode(), fh.read())
        self.assertNotIn(key, self.hook("S1")[0]["hookSpecificOutput"]["additionalContext"])
        self.write(".env", "TOKEN=abc\n")
        r = self.post("/api/context", {"kind": "file", "path": ".env"})
        self.assertEqual(400, r.status_code)
        self.assertIn("sensitive", r.json()["note"])
        self.assertEqual([], CX.read(self.d))

    def test_only_contained_named_items_are_accepted(self):
        for body in ({"kind": "file", "path": "../etc/passwd"},
                     {"kind": "file", "path": "/etc/passwd"},
                     {"kind": "file", "path": ".mokata/config.json"},
                     {"kind": "shell", "path": "src/a.py"},
                     {"kind": "selection", "path": "src/a.py"},
                     {"kind": "selection", "path": "src/a.py", "lines": "9-2"},
                     {"kind": "selection", "path": "src/a.py", "lines": "1-99999"},
                     {"kind": "diff", "path": "src/a.py"}):            # not a git repo: no diff
            with self.subTest(body=body):
                self.assertEqual(400, self.post("/api/context", body).status_code)
        self.assertEqual(403, self.c.post("/api/context", json={"kind": "file", "path": "src/a.py"},
                                          headers={"Origin": "http://evil.example"}).status_code)
        self.assertEqual([], CX.read(self.d))

    def test_it_is_bounded_items_text_and_the_turn_budget(self):
        self.write("src/big.py", "".join(f"v{i} = {i}\n" for i in range(5000)))
        q = self.post("/api/context", {"kind": "file", "path": "src/big.py"}).json()["queued"]
        self.assertTrue(q["truncated"])
        self.assertEqual(CX.MAX_ITEM_CHARS, q["chars"])
        ctx = self.hook("S1")[0]["hookSpecificOutput"]["additionalContext"]
        from mokata.config import Surface
        from mokata.context_budget import context_budget
        turn = context_budget(Surface.load(self.d).manifest).turn_tokens
        self.assertLessEqual(len(ctx), turn * 4 + 200)       # the block within the turn budget
        self.assertIn("excerpt cut to fit the turn budget — read src/big.py for the rest", ctx)
        for i in range(CX.MAX_ITEMS + 3):
            self.post("/api/context", {"kind": "selection", "path": "src/big.py",
                                       "lines": f"{i + 1}-{i + 1}"})
        self.assertEqual(CX.MAX_ITEMS, len(CX.read(self.d)))
        self.post("/api/context", {"kind": "selection", "path": "src/big.py", "lines": "1-1"})
        self.post("/api/context", {"kind": "selection", "path": "src/big.py", "lines": "1-1"})
        self.assertEqual(1, sum(1 for i in CX.read(self.d) if i["lines"] == [1, 1]))

    def test_a_zero_turn_budget_injects_nothing_and_keeps_the_item(self):
        self.post("/api/context", {"kind": "file", "path": "src/a.py"})
        text, ids = CX.pending_block(self.d, "S1", 0)
        self.assertEqual(("", []), (text, ids))
        self.assertEqual(1, len(CX.read(self.d)))
        text, ids = CX.render(CX.read(self.d), 40)          # header + names, no room for an excerpt
        self.assertLessEqual(len(text), 160)
        self.assertIn("- file src/a.py", text)

    def test_an_item_for_one_session_is_not_delivered_to_another(self):
        _register(self.d, {"S2": _entry("S2", os.getpid())})
        self.post("/api/context", {"kind": "file", "path": "src/a.py", "session": "S2"})
        self.assertFalse(any(CX.HEADER in o["hookSpecificOutput"]["additionalContext"]
                             for o in self.hook("S1")))
        self.assertEqual(1, len(CX.read(self.d)))
        self.assertIn(CX.HEADER, self.hook("S2")[0]["hookSpecificOutput"]["additionalContext"])
        self.assertEqual([], CX.read(self.d))
        self.assertEqual(404, self.post("/api/context", {"kind": "file", "path": "src/a.py",
                                                         "session": "NOBODY"}).status_code)

    @unittest.skipUnless(shutil.which("git"), "git is needed for a live diff")
    def test_a_diff_is_the_files_uncommitted_change(self):
        g = lambda *a: subprocess.run(["git", "-C", self.d, *a], capture_output=True,  # noqa: E731
                                      stdin=subprocess.DEVNULL, check=True)
        g("init", "-q")
        g("-c", "user.email=t@t", "-c", "user.name=t", "add", "src/a.py")
        g("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "a")
        self.write("src/a.py", "def total(x):\n    return x + 2\n")
        q = self.post("/api/context", {"kind": "diff", "path": "src/a.py"})
        self.assertEqual(200, q.status_code, q.text)
        text = CX.read(self.d)[0]["text"]
        self.assertIn("-    return x + 1", text)
        self.assertIn("+    return x + 2", text)

    def test_the_page_can_see_and_discard_its_queue_without_the_text(self):
        self.post("/api/context", {"kind": "file", "path": "src/a.py"})
        got = self.c.get("/api/context").json()
        self.assertEqual(1, len(got["items"]))
        self.assertNotIn("text", got["items"][0])
        self.assertEqual({"cleared": 1}, self.post("/api/context/clear", {}).json())
        self.assertEqual([], CX.read(self.d))


class SampleDataSendsNothing(_App):
    terminal = False
    demo = True

    def test_sample_mode_queues_nothing(self):
        r = self.post("/api/context", {"kind": "file", "path": "src/a.py"})
        self.assertEqual(409, r.status_code)
        self.assertEqual([], CX.read(self.d))
        self.assertEqual(404, self.post("/api/join", {"session": "anything"}).status_code)


if __name__ == "__main__":
    unittest.main()
