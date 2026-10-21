"""Open the review window, or RE-FOCUS the one already open (0.0.22 E2, Jas 2026-10-11).

One server per repo. `open_or_focus(root, session)`:
  * a live server answers on the recorded port → tell it to focus `session` (its run, its files,
    its timeline) and open the browser on it. No second server.
  * none answers → start one as a child process (it exits after its idle timeout, never a daemon),
    wait for it to come up, and open the browser on the pairing link.

Called from the terminal (`mokata ide`) and from inside a Claude session (`/mokata:review-window`, through
the UserPromptSubmit hook — the one place that knows WHICH session asked, so parallel sessions in
several worktrees each point the window at themselves).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

from .server import TOKEN_HEADER, read_record

START_TIMEOUT = 8.0
LOCK_SLACK = 12.0


@dataclass(frozen=True)
class Launched:
    url: str
    started: bool          # True: a new server; False: an open one was re-focused
    demo: bool

    def line(self) -> str:
        how = "opened" if self.started else "re-focused"
        sample = " (SAMPLE data)" if self.demo else ""
        return f"mokata ide {how}{sample}: {self.url}"


def _call(port: int, token: str, path: str, body: Optional[dict] = None,
          timeout: float = 2.0) -> Optional[dict]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", method="POST" if body is not None else "GET",
        data=json.dumps(body).encode() if body is not None else None,
        headers={TOKEN_HEADER: token, "Host": f"127.0.0.1:{port}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:      # noqa: S310 — loopback
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def running(root: str) -> Optional[dict]:
    """The live server's record, or None (a stale record from a crashed server reads as None)."""
    rec = read_record(root)
    if not rec:
        return None
    pong = _call(int(rec.get("port") or 0), str(rec.get("token") or ""), "/api/ping")
    return rec if pong and pong.get("ok") and os.path.realpath(str(pong.get("root"))) == \
        os.path.realpath(root) else None


def pair_url(rec: dict, focus: Optional[str]) -> str:
    url = f"http://127.0.0.1:{rec['port']}/pair?t={rec['token']}"
    return url + (f"&focus={focus}" if focus else "")


def open_or_focus(root: str, session: Optional[str] = None, demo: bool = False,
                  browser: bool = True, idle_secs: Optional[float] = None,
                  terminal: bool = False) -> Launched:
    """Open the window, or re-focus the running one. Serialised per repo by a lock file, so two
    `/mokata:review-window` at once cannot both start a server (0.0.22 verification)."""
    from ..oslock import file_lock
    from .server import record_path
    lock = record_path(root) + ".lock"
    os.makedirs(os.path.dirname(lock), exist_ok=True)
    # 0.0.23 C4 (VERIFICATION-MINORS-0022 "Windows has no launch lock") — the shared OS lock:
    # `fcntl.flock` on POSIX, `msvcrt.locking` on Windows, released if the holder dies, and a
    # BOUNDED wait (a stuck launcher is named, never waited on forever).
    with file_lock(lock, timeout=START_TIMEOUT + LOCK_SLACK):
        return _open_or_focus(root, session, demo, browser, idle_secs, terminal)


def _open_or_focus(root: str, session: Optional[str], demo: bool, browser: bool,
                   idle_secs: Optional[float], terminal: bool = False) -> Launched:
    """`terminal` (ID.S3): the caller is a human at a TTY (`mokata ide`), so the window may run the
    embedded terminal. A running window WITHOUT it is restarted with it — the same "other mode"
    rule as demo vs real; a request without it never downgrades a window that has it."""
    rec = running(root)
    if rec and bool(rec.get("demo")) == demo and (not terminal or _has_terminal(rec)):
        _call(int(rec["port"]), str(rec["token"]), "/api/focus", {"session": session})
        url = pair_url(rec, session)
        _open(url, browser)
        return Launched(url, False, demo)
    old_pid = _pinged_pid(rec) if rec else 0
    if old_pid:
        # ONE server per repo: the running one is the other mode (demo vs real) — stop it, so the
        # link printed below can only ever be the new server's, never the old one's.
        _stop(old_pid, root)
    cmd = [sys.executable, "-m", "mokata.ide.server", "--root", root]
    if session:
        cmd += ["--focus", session]
    if demo:
        cmd.append("--demo")
    if terminal and not demo:
        cmd.append("--terminal")
    if idle_secs is not None:
        cmd += ["--idle", str(idle_secs)]
    env = dict(os.environ)
    src = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, env=env, **_detach_kwargs())
    deadline = time.time() + START_TIMEOUT
    while time.time() < deadline:
        rec = running(root)
        if rec and bool(rec.get("demo")) == demo and int(rec.get("pid") or 0) != old_pid:
            url = pair_url(rec, session)
            _open(url, browser)
            return Launched(url, True, demo)
        time.sleep(0.15)
    raise RuntimeError("mokata ide did not start within "
                       f"{START_TIMEOUT:g}s — run `mokata ide --foreground` to see why")


def _has_terminal(rec: dict) -> bool:
    pong = _call(int(rec.get("port") or 0), str(rec.get("token") or ""), "/api/ping")
    return bool(pong and pong.get("terminal"))


def _pinged_pid(rec: dict) -> int:
    """The pid the running server REPORTS (its ping), never the record file's — the record is a
    file on disk, and signalling a pid read from it could hit an unrelated process."""
    pong = _call(int(rec.get("port") or 0), str(rec.get("token") or ""), "/api/ping")
    try:
        return int(pong.get("pid") or 0) if pong else 0
    except (TypeError, ValueError):
        return 0


def _detach_kwargs() -> dict:
    """The child outlives this command on every OS: a new session on POSIX; on Windows a new
    process group with its own HIDDEN console (`start_new_session` is POSIX-only and silently
    ignored there, so the child would die with the terminal that started it).

    0.0.25 `IDE-WIN-CMD-FLASH`: NOT `DETACHED_PROCESS`. Windows ignores CREATE_NO_WINDOW beside
    it, so the server had no console at all — and a console-less process makes every console
    child (`git`, `rg`, a pyenv-win `.bat` shim through `cmd.exe`) open a new VISIBLE window. The
    IDE polls, so cmd windows flashed continuously. A hidden console is inherited by those
    children instead, and still does not belong to the launching terminal (`mokata/quiet_spawn.py`)."""
    if os.name == "nt":
        flags = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
                 | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
        return {"creationflags": flags}
    return {"start_new_session": True}


def _stop(pid: int, root: str, wait: float = 3.0) -> None:
    """Stop the server we pinged, then wait until it stops ANSWERING.

    0.0.23 C4: the wait used to poll `os.kill(pid, 0)`. On Windows that call is not a probe — any
    signal other than CTRL_C/CTRL_BREAK is `TerminateProcess` — and on POSIX a recycled pid
    answers it too. The server's own ping is the liveness signal on every OS."""
    import signal
    try:
        os.kill(pid, signal.SIGTERM)       # POSIX: SIGTERM · Windows: TerminateProcess
    except OSError:
        return
    deadline = time.time() + wait
    while time.time() < deadline:
        if running(root) is None:
            return
        time.sleep(0.05)


def _open(url: str, browser: bool) -> None:
    if not browser:
        return
    try:
        import webbrowser
        webbrowser.open(url, new=2)
    except Exception:  # noqa: BLE001 — the URL is printed either way
        pass


__all__ = ["Launched", "running", "pair_url", "open_or_focus"]
