"""ID.S3 — the embedded terminal: a pseudo-terminal the IDE page drives (0.0.25 Lane ID2; doc 109 §5).

Doc 109 §5, "Companion, embedded terminal (ID.S3)": the page runs the **unmodified `claude` binary**
(or the user's shell) in a pty. Login happens in that terminal through Anthropic's own flow. mokata
never reads, stores or proxies a credential: the pty is a DUMB PIPE —

  * it is never logged and never persisted: output lives in a bounded in-memory ring per terminal
    (`MAX_BUFFER`) and dies with it (doc 109 §5 hard guard "The pty stream is never persisted or
    logged");
  * the program set is CLOSED (`PROGRAMS`): `claude`, optionally `claude --resume <known session>`,
    or the user's `$SHELL`. The page names a program; it never sends an argv;
  * nothing here calls a model (doc 85 §7k); `claude` is started interactive, never with `-p`.

BOUNDED. One process (group) per terminal, at most `MAX_TERMINALS` alive per server. A terminal
whose page stops polling for `IDLE_SECS` is killed (page closed, tab crashed, laptop asleep); the
page also closes it on `pagehide`; the server kills every terminal when it exits.

PLATFORMS. POSIX uses the stdlib (`os.openpty`, `termios`, `fcntl`). The child becomes a session
leader with the pty as its controlling terminal through a tiny stdlib launcher (`_LAUNCHER`), so the
spawn itself stays `subprocess.Popen` (no `fork` in a threaded server). Windows (0.0.26 O5) uses
ConPTY through `pywinpty` (`WinTerminal`, the same interface), an OPTIONAL dependency installed by the
`ide` extra on Windows only. Without it the terminal reports `NO_WINPTY` as its own state through
`/api/term/caps` — never a silent blank — and the IDE keeps working as a companion of the user's own
terminal (doc 109 §17.2, "Running in the Desktop Code tab"). The IDE never installs it itself.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..billing_env import terminal_preface
from ..errors import MokataError
from ..quiet_spawn import hidden

MAX_TERMINALS = 4                  # alive at once, per server
MAX_BUFFER = 512 * 1024            # bytes of output kept per terminal (in memory only)
MAX_INPUT = 64 * 1024              # bytes per input post
IDLE_SECS = 45.0                   # no poll for this long: the page is gone — kill
READ_WAIT_MAX = 20.0               # the longest a long-poll read may hold
KILL_GRACE = 1.5                   # SIGHUP → SIGKILL
PROGRAMS = ("claude", "shell")
NO_WINPTY = ("the embedded terminal on Windows needs the optional `pywinpty` package (ConPTY), "
             "which is not installed — `pip install \"mokata[ide]\"` adds it, or run `claude` in "
             "your own terminal; this window follows it the same way")
_SID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")

# The child side: become a session leader, take the pty (fd 0) as the controlling terminal, exec
# the program UNMODIFIED. Stdlib only; run with `-I` so no user site/env can change what it imports.
_LAUNCHER = ("import os,sys\n"
             "os.setsid()\n"
             "try:\n"
             " import fcntl,termios\n"
             " fcntl.ioctl(0, termios.TIOCSCTTY, 0)\n"
             "except (ImportError, AttributeError, OSError):\n"
             " pass\n"
             "os.execvp(sys.argv[1], sys.argv[1:])\n")


class TerminalRefused(MokataError, ValueError):
    """A terminal request the host will not serve (unknown program, cap reached, no such id,
    unsupported platform) — answered 4xx with the reason, nothing spawned."""


def _windows() -> bool:
    return os.name == "nt"


def _winpty() -> Any:
    """The `winpty` module (pywinpty), or None. Imported only when a terminal is asked about."""
    try:
        import winpty  # type: ignore[import-not-found]
    except ImportError:
        return None
    return winpty if hasattr(winpty, "PtyProcess") else None


def available() -> Tuple[bool, str]:
    """(usable here, why not). The reason is shown by the page as the terminal's own state."""
    if _windows():
        return (True, "") if _winpty() is not None else (False, NO_WINPTY)
    try:
        import fcntl  # noqa: F401
        import termios  # noqa: F401
    except ImportError:
        return False, "this Python has no termios/fcntl — the embedded terminal needs a POSIX pty"
    if not hasattr(os, "openpty"):
        return False, "this platform has no pseudo-terminal (os.openpty)"
    return True, ""


def _shell() -> List[str]:
    if _windows():
        return [os.environ.get("COMSPEC") or "cmd.exe"]
    sh = os.environ.get("SHELL") or ""
    if sh and os.path.isabs(sh) and os.access(sh, os.X_OK):
        return [sh]
    return ["/bin/sh"]


def default_programs() -> Dict[str, Callable[[Optional[str]], Optional[List[str]]]]:
    """program name → argv builder (`resume` is a session id already validated by the caller)."""
    def claude(resume: Optional[str]) -> Optional[List[str]]:
        exe = shutil.which("claude")
        if not exe:
            return None
        argv = [exe, "--resume", resume] if resume else [exe]
        if _windows() and exe.lower().endswith((".cmd", ".bat")):
            # an npm-installed `claude.cmd` is a batch file: CreateProcess (ConPTY) runs it via cmd
            argv = [os.environ.get("COMSPEC") or "cmd.exe", "/c", *argv]
        return argv

    def shell(_resume: Optional[str]) -> Optional[List[str]]:
        return _shell()
    return {"claude": claude, "shell": shell}


def child_env() -> Dict[str, str]:
    """The user's environment, passed through as-is (a dumb pipe), with the terminal named so the
    programs inside it — mokata's own CLI among them — know OSC-8 links render here."""
    env = dict(os.environ)
    env["TERM"] = "xterm-256color"
    env["COLORTERM"] = "truecolor"
    env["TERM_PROGRAM"] = "mokata-ide"
    return env


def _set_size(fd: int, cols: int, rows: int) -> None:
    import fcntl
    import struct
    import termios
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _clamp(v: Any, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


class Terminal:
    """One pty + one process group + one reader thread + one bounded output ring."""

    def __init__(self, tid: str, program: str, argv: List[str], cwd: str, cols: int, rows: int):
        self.id = tid
        self.program = program
        self.cols, self.rows = cols, rows
        self.started = time.time()
        self.last_poll = self.started
        self.exit_code: Optional[int] = None
        # L4 — a billing-credential notice (names only) is the ring's first line, before any output.
        self._buf = bytearray(terminal_preface(program))
        self._base = 0                     # absolute offset of _buf[0]
        self._cond = threading.Condition()
        self._closed = False
        master, slave = os.openpty()
        try:
            _set_size(slave, cols, rows)
            self.proc = subprocess.Popen(
                [sys.executable, "-I", "-c", _LAUNCHER, *argv], stdin=slave, stdout=slave,
                stderr=slave, cwd=cwd, env=child_env(), close_fds=True, **hidden())
        except (OSError, ValueError, subprocess.SubprocessError):
            os.close(master)                 # a spawn that failed leaks no pty
            os.close(slave)
            raise
        os.close(slave)
        self.master = master
        self._reader = threading.Thread(target=self._read_loop, name=f"mokata-term-{tid}",
                                        daemon=True)
        self._reader.start()

    # ---------------------------------------------------------------- output
    @property
    def end(self) -> int:
        return self._base + len(self._buf)

    @property
    def alive(self) -> bool:
        return self.exit_code is None and not self._closed

    def _read_loop(self) -> None:
        import select
        while True:
            try:
                ready, _, _ = select.select([self.master], [], [], 0.5)
            except (OSError, ValueError):
                break
            if self._closed:
                break
            if not ready:
                if self.proc.poll() is not None:
                    # the child is gone; drain whatever is still buffered in the pty, then stop
                    if not self._drain_once():
                        break
                continue
            if not self._drain_once():
                break
        code = self.proc.poll()
        if code is None:
            try:
                code = self.proc.wait(timeout=KILL_GRACE * 2)
            except subprocess.TimeoutExpired:
                code = -1
        with self._cond:
            self.exit_code = code
            self._cond.notify_all()

    def _drain_once(self) -> bool:
        try:
            data = os.read(self.master, 65536)
        except OSError:                    # EIO: the slave side closed
            return False
        if not data:
            return False
        self._push(data)
        return True

    def _push(self, data: bytes) -> None:
        """Append output to the bounded ring and wake readers (shared by both backends)."""
        with self._cond:
            self._buf.extend(data)
            over = len(self._buf) - MAX_BUFFER
            if over > 0:
                del self._buf[:over]
                self._base += over
            self._cond.notify_all()

    def read(self, after: int, wait: float) -> Dict[str, Any]:
        """Output after absolute offset `after`, waiting up to `wait` s for some. `truncated`: the
        caller asked for bytes the ring has already dropped (it resumes at the oldest kept)."""
        self.last_poll = time.time()
        deadline = time.time() + max(0.0, min(wait, READ_WAIT_MAX))
        with self._cond:
            while self.end <= after and self.exit_code is None and not self._closed:
                left = deadline - time.time()
                if left <= 0:
                    break
                self._cond.wait(left)
            start = max(after, self._base)
            data = bytes(self._buf[start - self._base:]) if start < self.end else b""
            out = {"data": data, "next": self.end, "truncated": after < self._base, "alive": self.alive,
                   "exit": self.exit_code}
        self.last_poll = time.time()
        return out

    # ---------------------------------------------------------------- input
    def write(self, data: bytes) -> int:
        if not self.alive:
            raise TerminalRefused("the terminal has ended")
        self.last_poll = time.time()
        view = memoryview(data[:MAX_INPUT])
        sent = 0
        while sent < len(view):
            try:
                sent += os.write(self.master, view[sent:])
            except BlockingIOError:
                time.sleep(0.01)
            except OSError as exc:
                raise TerminalRefused(f"the terminal is not accepting input ({exc})") from exc
        return sent

    def resize(self, cols: int, rows: int) -> None:
        self.cols, self.rows = cols, rows
        if self.alive:
            try:
                _set_size(self.master, cols, rows)
            except OSError:
                pass

    # ---------------------------------------------------------------- end
    def close(self) -> None:
        """Kill the whole process group (the program and anything it started), then free the pty."""
        if not self._closed and self.proc.poll() is None:
            for sig, wait in ((signal.SIGHUP, KILL_GRACE), (signal.SIGKILL, KILL_GRACE)):
                try:
                    os.killpg(self.proc.pid, sig)
                except (ProcessLookupError, PermissionError):
                    # no group yet: the launcher has not reached setsid() — signal the process
                    try:
                        self.proc.send_signal(sig)
                    except OSError:
                        break
                try:
                    self.proc.wait(timeout=wait)
                    break
                except subprocess.TimeoutExpired:
                    continue
        if self.proc.poll() is not None:
            # the leader is gone; anything left in its group (a job that ignored SIGHUP, like a
            # `nohup` child) dies with the terminal too — no orphan outlives its window
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        with self._cond:
            self._closed = True
            self._cond.notify_all()
        self._reader.join(timeout=2.0)
        try:
            os.close(self.master)
        except OSError:
            pass
        if self.exit_code is None:
            self.exit_code = self.proc.poll()


class WinTerminal(Terminal):
    """Windows: one ConPTY (pywinpty's `PtyProcess`) + one reader thread + the SAME bounded ring and
    the same read/write/resize/close interface as the POSIX `Terminal`. pywinpty speaks `str`; the
    ring and the page speak UTF-8 bytes, so output is encoded and input decoded incrementally (a
    character split across two posts is never mangled). Closing the pseudo-console ends every
    process attached to it — the program and anything it started — so no orphan outlives its window."""

    def __init__(self, tid: str, program: str, argv: List[str], cwd: str, cols: int, rows: int):
        import codecs
        winpty = _winpty()
        if winpty is None:
            raise TerminalRefused(NO_WINPTY)
        self.id = tid
        self.program = program
        self.cols, self.rows = cols, rows
        self.started = time.time()
        self.last_poll = self.started
        self.exit_code: Optional[int] = None
        self._buf = bytearray(terminal_preface(program))     # L4 — see Terminal.__init__
        self._base = 0
        self._cond = threading.Condition()
        self._closed = False
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.pty = winpty.PtyProcess.spawn(list(argv), cwd=cwd, env=child_env(),
                                           dimensions=(rows, cols))
        self._reader = threading.Thread(target=self._read_loop, name=f"mokata-term-{tid}",
                                        daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        while not self._closed:
            try:
                text = self.pty.read(65536)
            except (EOFError, OSError, ValueError):
                break
            if text:
                self._push(text.encode("utf-8", "replace") if isinstance(text, str) else bytes(text))
            elif not self.pty.isalive():
                break
        code = getattr(self.pty, "exitstatus", None)
        with self._cond:
            self.exit_code = code if isinstance(code, int) else -1
            self._cond.notify_all()

    def write(self, data: bytes) -> int:
        if not self.alive:
            raise TerminalRefused("the terminal has ended")
        self.last_poll = time.time()
        chunk = data[:MAX_INPUT]
        try:
            self.pty.write(self._decoder.decode(chunk))
        except (OSError, EOFError) as exc:
            raise TerminalRefused(f"the terminal is not accepting input ({exc})") from exc
        return len(chunk)

    def resize(self, cols: int, rows: int) -> None:
        self.cols, self.rows = cols, rows
        if self.alive:
            try:
                self.pty.setwinsize(rows, cols)
            except (OSError, EOFError):
                pass

    def close(self) -> None:
        if not self._closed:
            try:
                if self.pty.isalive():
                    self.pty.terminate(force=True)
            except (OSError, EOFError):
                pass
        with self._cond:
            self._closed = True
            self._cond.notify_all()
        try:
            self.pty.close(force=True)
        except TypeError:
            self.pty.close()
        except (OSError, EOFError):
            pass
        self._reader.join(timeout=2.0)
        if self.exit_code is None:
            code = getattr(self.pty, "exitstatus", None)
            self.exit_code = code if isinstance(code, int) else -1


def _make_terminal(tid: str, program: str, argv: List[str], cwd: str, cols: int,
                   rows: int) -> Terminal:
    """The platform's backend behind the one interface."""
    cls = WinTerminal if _windows() else Terminal
    return cls(tid, program, argv, cwd, cols, rows)


class TerminalHost:
    """Every terminal of one IDE server. Thread-safe; one reaper thread while any terminal exists."""

    def __init__(self, root: str, programs: Optional[Dict[str, Callable]] = None,
                 idle_secs: float = IDLE_SECS, enabled: Optional[Tuple[bool, str]] = None):
        self.root = root
        self.programs = programs if programs is not None else default_programs()
        self.idle_secs = idle_secs
        self.enabled = enabled if enabled is not None else available()
        self._terms: Dict[str, Terminal] = {}
        self._lock = threading.Lock()
        self._reaper: Optional[threading.Thread] = None

    def caps(self) -> Dict[str, Any]:
        ok, why = self.enabled
        progs = {}
        if ok:
            for name in PROGRAMS:
                build = self.programs.get(name)
                progs[name] = bool(build and build(None))
        return {"available": ok, "reason": why, "programs": progs, "max": MAX_TERMINALS,
                "open": len(self.list())}

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [{"id": t.id, "program": t.program, "alive": t.alive, "exit": t.exit_code,
                     "started": t.started} for t in self._terms.values()]

    def open(self, program: Any, cols: Any = 100, rows: Any = 30,
             resume: Optional[str] = None) -> Terminal:
        ok, why = self.enabled
        if not ok:
            raise TerminalRefused(why)
        if program not in PROGRAMS or program not in self.programs:
            raise TerminalRefused(f"program must be one of {list(PROGRAMS)}")
        if resume is not None and (program != "claude" or not isinstance(resume, str)
                                   or not _SID.match(resume)):
            raise TerminalRefused("resume takes a session id, and only with claude")
        argv = self.programs[program](resume)
        if not argv:
            raise TerminalRefused(f"{program} is not on PATH")
        with self._lock:
            if sum(1 for t in self._terms.values() if t.alive) >= MAX_TERMINALS:
                raise TerminalRefused(f"at most {MAX_TERMINALS} terminals are open at once — "
                                      "close one first")
            tid = secrets.token_urlsafe(9)
            term = _make_terminal(tid, program, argv, self.root, _clamp(cols, 10, 500, 100),
                                  _clamp(rows, 4, 300, 30))
            self._terms[tid] = term
            if self._reaper is None or not self._reaper.is_alive():
                self._reaper = threading.Thread(target=self._reap_loop, name="mokata-term-reaper",
                                                daemon=True)
                self._reaper.start()
        return term

    def get(self, tid: Any) -> Terminal:
        with self._lock:
            term = self._terms.get(tid) if isinstance(tid, str) else None
        if term is None:
            raise TerminalRefused("no such terminal (it was closed, or it idled out)")
        return term

    def close(self, tid: Any) -> bool:
        with self._lock:
            term = self._terms.pop(tid, None) if isinstance(tid, str) else None
        if term is None:
            return False
        term.close()
        return True

    def close_all(self) -> int:
        with self._lock:
            terms = list(self._terms.values())
            self._terms.clear()
        for t in terms:
            t.close()
        return len(terms)

    def reap(self, now: Optional[float] = None) -> List[str]:
        """Close every terminal nobody has polled for `idle_secs` (its page is gone)."""
        now = time.time() if now is None else now
        with self._lock:
            stale = [tid for tid, t in self._terms.items() if now - t.last_poll > self.idle_secs]
        for tid in stale:
            self.close(tid)
        return stale

    def _reap_loop(self) -> None:
        while True:
            time.sleep(min(2.0, max(0.05, self.idle_secs / 4)))
            self.reap()
            with self._lock:
                if not self._terms:
                    self._reaper = None
                    return


__all__ = ["TerminalHost", "Terminal", "WinTerminal", "TerminalRefused", "available", "default_programs",
           "child_env", "MAX_TERMINALS", "MAX_BUFFER", "IDLE_SECS", "PROGRAMS", "NO_WINPTY"]
