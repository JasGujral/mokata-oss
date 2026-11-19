"""No console window for a background spawn on Windows (0.0.25 `IDE-WIN-CMD-FLASH`).

The IDE server runs with no visible console. On Windows, every CONSOLE program it starts — `git`,
`rg`, `code-review-graph`, and any `.bat`/`.cmd` shim (pyenv-win's `python`/`mokata` shims always
run through `cmd.exe`) — gets a console of its own. Unless the spawn says CREATE_NO_WINDOW, that
console is a VISIBLE cmd window that opens and closes with the child: the IDE polls `/api/file`,
`/api/file` runs `git`, so the user sees cmd windows flash continuously (reported by Jas on Windows,
2026-10-19; mechanism read from the spawns a polling IDE makes — `git` ×10 per refresh round).

Two halves, either of which alone stops the flash:
  * `detached()` (the IDE server start, the hook drainer) gives the CHILD a hidden console (CREATE_NO_WINDOW, no longer
    DETACHED_PROCESS — Windows ignores CREATE_NO_WINDOW beside DETACHED_PROCESS, and a process
    with no console at all forces every console child to make a new, visible one);
  * every captured-output spawn in the IDE's reach passes `hidden()` — so a server started any
    other way (`--foreground` under pythonw, a service) still never flashes.

Only for spawns whose output is captured or discarded. Never for an interactive child the user is
meant to see (an editor, `claude` in a terminal): CREATE_NO_WINDOW gives it a console nobody sees.

THE PROCESS DOOR (0.0.28 lane B, `WIN-MERGE-SUITE-NO-JOB`, `WIN-TREEWATCH-PID-REUSE`). Every
program mokata starts that may start programs of its own — a test runner, a language server, the
baseline one-liner, a statusLine command, `poetry`/`pipenv` — goes through `spawn_jobbed` /
`run_jobbed`, and its whole tree is ended only through the `Jobbed` handle they return:
  * Windows: the process is created SUSPENDED, put in a KILL_ON_JOB_CLOSE Job Object, and only then
    resumed — nothing it starts is ever outside the job. If Windows refuses the job, the process is
    killed while still suspended and `OSError(NO_JOB …)` is raised: nothing ran (never degrade to a
    pid walk). `end()` returns only once the job is EMPTY (`ActiveProcesses == 0`).
  * POSIX: its own session (process group = its pid). The group is signalled only while the leader
    is UNREAPED — a zombie still holds the pid, so the group id cannot have been recycled. Exit is
    detected without reaping (`os.waitid(WNOWAIT)` on Linux; a kqueue `NOTE_EXIT` on macOS, where
    CPython has no `waitid`), and the handle alone reaps.
Every other spawn in `src/` is a row of `TREELESS` (derived by `tests/test_b_one_process_door.py`).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import subprocess

TYPE_CHECKING = False        # `typing` costs a hook ~3 ms and is only for readers here
if TYPE_CHECKING:
    from typing import Callable, Dict, List, Optional, Sequence, Tuple


def _windows() -> bool:
    return os.name == "nt"


def hidden() -> Dict[str, int]:
    """`subprocess` kwargs that suppress the console window on Windows; empty elsewhere."""
    if _windows():
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}


def detached(suspended: bool = False, nt: "Optional[bool]" = None) -> "Dict[str, object]":
    """`subprocess` kwargs for a background child that must OUTLIVE its parent and show no window:
    a new session on POSIX; on Windows a new process group with its own HIDDEN console.

    0.0.25 `IDE-WIN-CMD-FLASH` (B-01): NOT `DETACHED_PROCESS`. Windows ignores CREATE_NO_WINDOW beside
    it, so the child had no console at all — and a console-less process makes every console child it
    starts (`git`, `rg`, a pyenv-win `.bat` shim through `cmd.exe`) open a new VISIBLE window. A hidden
    console is inherited by those children instead, and CREATE_NEW_PROCESS_GROUP keeps the child out
    of the launching console's Ctrl-C/close group, so it is not taken down with its parent
    (`start_new_session` is POSIX-only and silently ignored on Windows). Used by the IDE server start
    (`ide/launch.py`), the hook drainer (`mokata_emit`, 0.0.26 O3) and the code-graph index
    builder (0.0.26 O13).

    `suspended` adds CREATE_SUSPENDED on Windows (nothing elsewhere): the process is created but
    does not run until the caller resumes it — `spawn_jobbed` below puts it in its Job Object
    first. `nt` injects the platform for the tests."""
    if _windows() if nt is None else nt:
        return {"creationflags": (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
                                  | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                                  | (0x00000004 if suspended else 0))}
    return {"start_new_session": True}


def _signalable(n: object, *own: int) -> bool:
    """A real process number we may signal: a plain `int` (not `bool`, not a test double whose
    `__index__` is 1), greater than 1, and none of `own`. 0, 1 and negatives are never a target:
    POSIX `killpg(1, sig)` is `kill(-1, sig)` on Linux — EVERY process the user owns (it took
    down the CI runner, 0.0.27) — and `killpg(0)` / `kill(0)` is the caller's own group."""
    return type(n) is int and n > 1 and n not in own


def signal_group(pgid: object, sig: int) -> bool:
    """`os.killpg(pgid, sig)` — only for a group that is not 0, 1, negative, a non-int, or OUR OWN
    group or our own pid (POSIX only; there is no process group to signal on Windows). Returns False, sending
    nothing, when refused; OS errors (the group is gone, not ours) propagate as from `killpg`.
    The ONE door for a group signal in mokata (`tests/test_signals_never_broadcast.py`)."""
    if _windows() or not _signalable(pgid, os.getpgrp(), os.getpid()):
        return False
    os.killpg(pgid, sig)  # type: ignore[arg-type]
    return True


def signal_pid(pid: object, sig: int) -> bool:
    """`os.kill(pid, sig)` — only for a pid that is not 0, 1, negative, a non-int, or OUR OWN
    process. Returns False, sending nothing, when refused; OS errors propagate as from `os.kill`.
    The ONE door for a non-probe signal to a pid in mokata."""
    if not _signalable(pid, os.getpid()):
        return False
    os.kill(pid, sig)  # type: ignore[arg-type]
    return True




# ============================================================================ the process door
#: Every refusal to hold a process in a Job Object starts with this (`OSError`, no new class).
NO_JOB = "mokata could not hold this process in a Windows Job Object"
#: What the user can do about it — part of every `NO_JOB` message (refute M6).
NO_JOB_REMEDY = (
    "mokata starts test runs and language servers inside a Windows Job Object so it can end "
    "everything they start, and Windows refused, so nothing was run. This happens when mokata "
    "itself runs inside a job that forbids nesting or caps the process count — start mokata from "
    "a normal terminal, or ask whoever launched it (CI runner, sandbox) to allow nested jobs")
#: How long `Jobbed.end` waits for the tree to be gone (the job empty / the leader reaped). Returns
#: the moment it is; only a tree that genuinely cannot end costs this, and that is REPORTED.
END_BOUND_S = 30.0
#: CreateProcess inside a parent job whose ActiveProcessLimit is reached.
_ERROR_NOT_ENOUGH_QUOTA = 1816
#: How often a timed exit wait or the empty-job wait looks again (a poll slice, not a deadline).
_POLL_S = 0.02
_POLL_MAX_S = 0.25


def _no_job(call: str, detail: str) -> OSError:
    return OSError(f"{NO_JOB} ({call}: {detail}). {NO_JOB_REMEDY}")


def _monotonic() -> float:
    import time
    return time.monotonic()


def _sleep(s: float) -> None:
    import time
    time.sleep(s)


def _cancel_mod() -> object:
    """`mokata.knowledge.cancel` IF something already imported it, else None — the door reads the
    caller's cancel flag and marks its critical windows without importing anything (the hook
    import graph stays what it is; 0.0.29 R-B3)."""
    import sys
    return sys.modules.get("mokata.knowledge.cancel")


def _enter_critical() -> None:
    c = _cancel_mod()
    if c is not None:
        c.enter_critical()  # type: ignore[attr-defined]


def _leave_critical() -> None:
    c = _cancel_mod()
    if c is not None:
        c.leave_critical()  # type: ignore[attr-defined]


#: Every live door tree in this process (0.0.29 R-B6): `end_all` ends them at server exit. Strong
#: references, dropped on `close` — a weak one would let a leaked handle's tree outlive the server.
_LIVE: "set" = set()
_LIVE_LOCK = __import__("threading").Lock()


def end_all() -> int:
    """End (and close) every door tree this process still holds — the MCP server's exit path.
    Returns how many. Never raises."""
    with _LIVE_LOCK:
        trees = list(_LIVE)
    for t in trees:
        try:
            t.close()
        except Exception:  # noqa: BLE001 — an exit path ends what it can
            pass
    return len(trees)


# ---------------------------------------------------------------- the POSIX lifeline (R-B6)
#: `[read fd, write fd]` once `enable_lifeline` ran in this process (the MCP server only).
_LIFELINE: "list" = []


def lifeline_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "_lifeline.py")


def enable_lifeline() -> bool:
    """POSIX: from now on every door tree is started under `_lifeline.py`, which SIGKILLs its own
    group when this process's end of a pipe closes — however this process dies (SIGKILL, OOM, a
    crash). Called once by the MCP server; the CLI and hooks never pay the extra process. False
    (nothing changes) on Windows (KILL_ON_JOB_CLOSE already does this) or when the shim cannot be
    run (no `sys.executable`, the file missing)."""
    import sys
    if _windows():
        return False
    if _LIFELINE:
        return True
    if not sys.executable or not os.path.isfile(lifeline_path()):
        return False
    r, w = os.pipe()                          # both non-inheritable; `r` goes to shims by pass_fds
    _LIFELINE[:] = [r, w]
    return True


def lifeline_enabled() -> bool:
    return bool(_LIFELINE)


def _lifelined(argv: "Sequence[str]", popen_kw: "Dict[str, object]") -> "Tuple[list, int]":
    """The argv that runs `argv` under the shim, and the shim's start-status pipe (read end)."""
    import sys
    sr, sw = os.pipe()
    fds = tuple(popen_kw.pop("pass_fds", ()) or ()) + (_LIFELINE[0], sw)
    popen_kw["pass_fds"] = fds
    return ([sys.executable, "-I", lifeline_path(), str(_LIFELINE[0]), str(sw), "--", *argv],
            (sr, sw))  # type: ignore[return-value]


def _read_start_status(pipe: "Tuple[int, int]") -> "Optional[OSError]":
    """The shim's word on its start: EOF with nothing = the command started; `errno\0text\0file`
    = it could not, and the same `OSError` Popen would have raised is rebuilt."""
    sr, _sw = pipe
    data = b""
    try:
        while True:
            chunk = os.read(sr, 4096)
            if not chunk:
                break
            data += chunk
    finally:
        os.close(sr)
    if not data:
        return None
    parts = data.decode("utf-8", "replace").split("\0")
    try:
        errno_ = int(parts[0])
    except ValueError:
        errno_ = 0
    text = parts[1] if len(parts) > 1 else "could not start"
    fname = parts[2] if len(parts) > 2 and parts[2] else None
    import errno as _e
    cls = {_e.ENOENT: FileNotFoundError, _e.EACCES: PermissionError,
           _e.EPERM: PermissionError, _e.ENOTDIR: NotADirectoryError}.get(errno_, OSError)
    return cls(errno_, text, fname) if fname else cls(errno_, text)


class WinJob:
    """A KILL_ON_JOB_CLOSE Windows Job Object holding one process tree (moved from `ide/lsp.py`).
    `make` raises `OSError(NO_JOB …)` when Windows refuses any step — there is no "not ok" job.
    `kernel32` is injectable so the logic runs off Windows; `ctypes` is imported only here."""

    _KILL_ON_JOB_CLOSE = 0x00002000
    _EXTENDED_LIMIT_INFORMATION = 9
    _BASIC_ACCOUNTING_INFORMATION = 1

    _ASSOCIATE_COMPLETION_PORT_INFORMATION = 7
    _MSG_ACTIVE_PROCESS_ZERO = 4

    def __init__(self, k32: object, handle: object, structs: object) -> None:
        self._k32, self.handle, self._structs = k32, handle, structs
        self.port: object = None

    def _watch_port(self) -> None:
        """Associate an I/O completion port, so `wait_empty` can wait for Windows' own
        `JOB_OBJECT_MSG_ACTIVE_PROCESS_ZERO` — an event, not a poll. Best effort: a kernel32 that
        cannot (an injected double) leaves `wait_empty` asking `active()` per slice."""
        import ctypes
        from ctypes import wintypes
        k32 = self._k32
        if not hasattr(k32, "CreateIoCompletionPort"):
            return
        try:
            k32.CreateIoCompletionPort.restype = wintypes.HANDLE
            k32.CreateIoCompletionPort.argtypes = [wintypes.HANDLE, wintypes.HANDLE,
                                                  ctypes.c_size_t, wintypes.DWORD]
            port = k32.CreateIoCompletionPort(wintypes.HANDLE(-1), None, 0, 1)
            if not port:
                return

            class _Assoc(ctypes.Structure):
                _fields_ = [("CompletionKey", ctypes.c_void_p),
                            ("CompletionPort", wintypes.HANDLE)]
            assoc = _Assoc(None, port)
            if not k32.SetInformationJobObject(self.handle,
                                               self._ASSOCIATE_COMPLETION_PORT_INFORMATION,
                                               ctypes.byref(assoc), ctypes.sizeof(assoc)):
                k32.CloseHandle(port)
                return
            self.port = port
        except (AttributeError, OSError, TypeError, ValueError):
            return

    def wait_empty(self, slice_s: float = 0.25) -> None:
        """Return once the job holds no process: Windows posts ACTIVE_PROCESS_ZERO to the job's
        completion port (queued, so a job that emptied earlier answers at once). Waits in slices
        of `slice_s` so Ctrl-C reaches Python.
        NO DEADLINE, by design (B2 verifier L7): this answers "may I touch what the tree held?",
        and a bound would let the caller go ahead while a member still holds the file — the race
        it exists to close. A caller that must not wait ends the tree first (`Jobbed.end()`
        terminates the job and is itself progress-bounded by `END_BOUND_S`); after that this
        returns at once. It has no cancel check either: the cancel road is `end()`, not this."""
        import ctypes
        from ctypes import wintypes
        if self.handle is None:
            return
        port = self.port
        if port is None:
            while self.active() not in (0, None):
                _sleep(slice_s)
            return
        k32 = self._k32
        k32.GetQueuedCompletionStatus.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(ctypes.c_size_t),
            ctypes.POINTER(ctypes.c_void_p), wintypes.DWORD]
        msg, key, ov = wintypes.DWORD(), ctypes.c_size_t(), ctypes.c_void_p()
        while True:
            if self.active() == 0:
                return
            if k32.GetQueuedCompletionStatus(port, ctypes.byref(msg), ctypes.byref(key),
                                             ctypes.byref(ov), int(slice_s * 1000)):
                if msg.value == self._MSG_ACTIVE_PROCESS_ZERO:
                    return

    @staticmethod
    def _types() -> object:
        import ctypes

        # DWORD is 4 bytes in the Windows ABI; fixed-width fields keep the layout host-independent
        # so the injected tests check the same struct off Windows (verify2 N5).
        class _Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", ctypes.c_uint32),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", ctypes.c_uint32),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", ctypes.c_uint32),
                        ("SchedulingClass", ctypes.c_uint32)]

        class _Io(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class _Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", _Basic), ("IoInfo", _Io),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        class _Accounting(ctypes.Structure):
            _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                        ("ThisPeriodTotalUserTime", ctypes.c_int64),
                        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                        ("TotalPageFaultCount", ctypes.c_uint32),
                        ("TotalProcesses", ctypes.c_uint32),
                        ("ActiveProcesses", ctypes.c_uint32),
                        ("TotalTerminatedProcesses", ctypes.c_uint32)]

        return _Basic, _Extended, _Accounting

    @classmethod
    def make(cls, process_handle: int, kernel32: object = None) -> "WinJob":
        """A new job, its KILL_ON_JOB_CLOSE limit set, `process_handle` assigned to it. Raises
        `OSError(NO_JOB …)` naming the call that failed and Windows' error."""
        import ctypes
        if kernel32 is None:
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
            k32.CreateJobObjectW.restype = wintypes.HANDLE
            k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                    ctypes.c_void_p, wintypes.DWORD]
            k32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                      ctypes.c_void_p, wintypes.DWORD,
                                                      ctypes.c_void_p]
            k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
        else:
            k32 = kernel32

        def err() -> str:
            if kernel32 is not None:           # an injected kernel32 says its own error
                return str(getattr(k32, "last_error", "refused"))
            code = ctypes.get_last_error()  # type: ignore[attr-defined]
            return f"WinError {code}: {ctypes.FormatError(code).strip()}"  # type: ignore[attr-defined]
        structs = cls._types()
        job = k32.CreateJobObjectW(None, None)
        if not job:
            raise _no_job("CreateJobObjectW", err())
        made = cls(k32, job, structs)
        held = False
        try:
            info = structs[1]()
            info.BasicLimitInformation.LimitFlags = cls._KILL_ON_JOB_CLOSE
            if not k32.SetInformationJobObject(job, cls._EXTENDED_LIMIT_INFORMATION,
                                               ctypes.byref(info), ctypes.sizeof(info)):
                raise _no_job("SetInformationJobObject", err())
            made._watch_port()           # before the process runs: no message can be missed
            if not k32.AssignProcessToJobObject(job, process_handle):
                raise _no_job("AssignProcessToJobObject", err())
            held = True
        finally:
            if not held:                       # never leak the job handle on any failure
                made.close()
        return made

    def active(self) -> "Optional[int]":
        """How many processes are in the job now (None when Windows will not say)."""
        import ctypes
        if self.handle is None:
            return 0
        acct = self._structs[2]()
        if not self._k32.QueryInformationJobObject(self.handle, self._BASIC_ACCOUNTING_INFORMATION,
                                                   ctypes.byref(acct), ctypes.sizeof(acct), None):
            return None
        return int(acct.ActiveProcesses)

    def totals(self) -> "Optional[int]":
        """How many processes were EVER in the job (the accounting's `TotalProcesses`)."""
        import ctypes
        if self.handle is None:
            return None
        acct = self._structs[2]()
        if not self._k32.QueryInformationJobObject(self.handle, self._BASIC_ACCOUNTING_INFORMATION,
                                                   ctypes.byref(acct), ctypes.sizeof(acct), None):
            return None
        return int(acct.TotalProcesses)

    def terminate(self) -> None:
        """Kill every process in the job. The handle stays (the job is asked whether it emptied)."""
        if self.handle is not None:
            self._k32.TerminateJobObject(self.handle, 1)

    def close(self) -> None:
        """Release the handle (KILL_ON_JOB_CLOSE ends any survivor). Idempotent."""
        job, self.handle = self.handle, None
        port, self.port = self.port, None
        if job is not None:
            self._k32.CloseHandle(job)
        if port is not None:
            self._k32.CloseHandle(port)


def _resume_process(proc: object) -> None:
    """Let a process created CREATE_SUSPENDED run: `NtResumeProcess` on its handle (Popen drops
    the primary thread's handle, so there is no thread to `ResumeThread`). Raises OSError when
    Windows refuses."""
    import ctypes
    from ctypes import wintypes
    ntdll = ctypes.WinDLL("ntdll")  # type: ignore[attr-defined]
    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    ntdll.NtResumeProcess.restype = ctypes.c_long
    status = ntdll.NtResumeProcess(int(proc._handle))  # type: ignore[attr-defined]
    if status != 0:
        raise OSError(f"NtResumeProcess failed: NTSTATUS {status & 0xFFFFFFFF:#x}")


class Ended(tuple):
    """What `Jobbed.end` could say: `ok` (the tree is gone — the job is empty / the group was
    killed while its leader was unreaped and the leader is reaped), `leftovers_unknown` (the leader
    had already been reaped by something else, so no group signal was sent: said, not guessed) and
    `why` (empty when ok)."""

    def __new__(cls, ok: bool, leftovers_unknown: bool = False, why: str = "") -> "Ended":
        return tuple.__new__(cls, (ok, leftovers_unknown, why))

    ok = property(lambda self: self[0])
    leftovers_unknown = property(lambda self: self[1])
    why = property(lambda self: self[2])


class Jobbed:
    """One process tree started through the door. `proc` is for reading (pipes, pid) only: never
    `proc.poll()` / `proc.wait()` / `proc.kill()` a door process — on POSIX a reap frees the group
    id for reuse. Use `poll_exit` / `wait_exit` (never reap) and `end` (the only kill, the only
    reap). Thread-safe; `end` and `close` are idempotent."""

    def __init__(self, proc: "subprocess.Popen", job: "Optional[WinJob]", nt: bool) -> None:
        import threading
        self.proc = proc
        self._job = job
        self._nt = nt
        # Only a real process number is ever watched or signalled: a test double's pid (whose
        # int may be 1 — the 0.0.27 `killpg(1)` CI case) is no process this handle started.
        self._real = type(proc.pid) is int and proc.pid > 1
        self._pgid: "Optional[int]" = proc.pid if self._real and not nt else None
        self._lock = threading.Lock()          # end / close
        self._waiter = threading.Lock()        # one thread asks the OS at a time
        self._exited = threading.Event()
        self._status: "Optional[int]" = None
        self._kq: object = None
        self.last_end: "Optional[Ended]" = None
        # How exit is seen without reaping: Linux `waitid(WNOWAIT)`, macOS a kqueue. A host with
        # neither (only Windows, whose door uses its handle) asks the handle like Windows does.
        import select
        self._by_handle = nt or not (hasattr(os, "waitid") or hasattr(select, "kqueue"))
        if not nt and not self._real:
            self._exited.set()
        elif not self._by_handle and not hasattr(os, "waitid"):
            self._watch_kqueue()

    # ---- exit, without reaping -------------------------------------------------------------
    def _watch_kqueue(self) -> None:
        """macOS: CPython has no `os.waitid`, so exit is seen through a kqueue `NOTE_EXIT`, which
        reports the exit status and leaves the zombie in place. Registered at spawn: a process that
        already exited cannot be registered (ESRCH) — it is then a zombie nobody else has reaped
        (`Popen` did not), and it is marked exited with its status read at the reap."""
        import select
        kq = select.kqueue()  # type: ignore[attr-defined]
        try:
            kq.control([select.kevent(  # type: ignore[attr-defined]
                self.proc.pid, filter=select.KQ_FILTER_PROC,  # type: ignore[attr-defined]
                flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,  # type: ignore[attr-defined]
                fflags=select.KQ_NOTE_EXIT | getattr(select, "KQ_NOTE_EXITSTATUS",  # type: ignore[attr-defined]
                                                     0x04000000))], 0)
        except ProcessLookupError:
            kq.close()
            self._exited.set()                 # exited before we could watch: status at reap
            return
        self._kq = kq

    def _ask_os(self, timeout: "Optional[float]") -> bool:
        """True once the leader has exited (zombie or reaped). Never reaps."""
        if self.proc.returncode is not None:
            self._status = self.proc.returncode
            return True
        if self._by_handle:
            # R-B4: never one unbounded OS wait — `WaitForSingleObject(INFINITE)` does not let
            # Ctrl-C reach Python on Windows (N1). The callers loop on the slice.
            try:
                rc = (self.proc.poll() if timeout == 0 else
                      self.proc.wait(_POLL_MAX_S if timeout is None else min(timeout, _POLL_MAX_S)))
            except subprocess.TimeoutExpired:
                return False
            if rc is None:
                return False
            self._status = rc
            return True
        if not hasattr(os, "waitid"):
            kq = self._kq
            if kq is None:                     # exited before it could be watched (see above)
                return self._exited.is_set()
            try:
                evs = kq.control(None, 1, _POLL_MAX_S if timeout is None  # type: ignore[attr-defined]
                                 else min(timeout, _POLL_MAX_S))
            except (OSError, ValueError):      # closed under us: `close` ends the tree first
                return self._exited.is_set()
            if not evs:
                return False
            try:
                self._status = os.waitstatus_to_exitcode(evs[0].data)
            except ValueError:
                self._status = None
            return True
        flags = os.WEXITED | os.WNOWAIT  # type: ignore[attr-defined]
        deadline = None if timeout is None else _monotonic() + timeout
        delay = _POLL_S
        while True:
            try:
                info = os.waitid(os.P_PID, self.proc.pid,  # type: ignore[attr-defined]
                                 flags | os.WNOHANG)  # type: ignore[attr-defined]
            except ChildProcessError:
                return True                    # reaped by something else: exited, status lost
            if info is not None and info.si_pid:
                self._status = (info.si_status if info.si_code == os.CLD_EXITED  # type: ignore[attr-defined]
                                else -info.si_status)
                return True
            if deadline is None:               # R-B4: one slice per call; the caller loops
                _sleep(delay)
                return False
            left = deadline - _monotonic()
            if left <= 0:
                return False
            _sleep(min(delay, left))
            delay = min(delay * 2, _POLL_MAX_S)

    def _wait(self, timeout: "Optional[float]") -> bool:
        deadline = None if timeout is None else _monotonic() + timeout
        while not self._exited.is_set():
            left = None if deadline is None else max(0.0, deadline - _monotonic())
            if not (self._waiter.acquire() if left is None else self._waiter.acquire(timeout=left)):
                return self._exited.is_set()
            try:
                if not self._exited.is_set() and self._ask_os(left):
                    self._exited.set()
            finally:
                self._waiter.release()
            if deadline is not None and _monotonic() >= deadline:
                break
        return self._exited.is_set()

    def _code(self) -> int:
        if self.proc.returncode is not None:
            return self.proc.returncode
        return self._status if self._status is not None else -1

    def poll_exit(self) -> "Optional[int]":
        """The leader's exit code once it has exited, else None. Never reaps."""
        return self._code() if self._wait(0) else None

    def wait_exit(self, timeout: "Optional[float]" = None) -> int:
        """Wait for the leader to exit and return its code; `subprocess.TimeoutExpired` after
        `timeout` seconds. Never reaps."""
        if not self._wait(timeout):
            raise subprocess.TimeoutExpired(self.proc.args, timeout or 0)
        return self._code()

    def active(self) -> "Optional[int]":
        """How many processes the tree holds now: Windows asks the job (None when it will not
        say); POSIX answers 1 while the leader is unexited and 0 after (the group's other members
        are not counted — `end` reaches them all)."""
        if self._nt:
            job = self._job
            return 0 if job is None else job.active()
        return 0 if self.poll_exit() is not None else 1

    def wait_empty(self) -> None:
        """Windows: wait for the job's ACTIVE_PROCESS_ZERO message (an event; a tree whose leader
        exited may still have members holding files). POSIX: returns at once. For lane F's spool
        test and any caller that must know the tree is empty before it touches what it held."""
        if self._nt and self._job is not None:
            self._job.wait_empty()

    # ---- the one kill ----------------------------------------------------------------------
    def end(self, leftovers_only: bool = False) -> Ended:
        """End the whole tree and reap the leader. Never raises; idempotent (the second call
        signals nothing and returns the first answer). `leftovers_only`: the caller has seen the
        leader exit (`poll_exit`) and ends what it left behind.
        POSIX: the group is signalled only if the leader is unreaped (zombie or running); then
        the leader is reaped. Windows: `TerminateJobObject`, then wait until the job is EMPTY,
        bounded by `END_BOUND_S` — `ok=False` (with why) when it is not, never swallowed."""
        _enter_critical()                      # R-B5: a 2nd Ctrl-C waits for the end to finish
        try:
            with self._lock:
                if self.last_end is not None:
                    return self.last_end
                try:
                    self.last_end = self._end_win() if self._nt else self._end_posix(leftovers_only)
                except (OSError, ValueError) as exc:   # an end reports; never raises mid-cleanup
                    self.last_end = Ended(False, False, f"ending the process tree failed: {exc}")
                return self.last_end
        finally:
            _leave_critical()

    def _end_posix(self, leftovers_only: bool) -> Ended:
        # `leftovers_only` changes nothing here: the leader leads its own group, so one group
        # signal reaches it and what it left (it is named for the callers' readability).
        import signal
        pgid = self._pgid                      # `end` runs once (`last_end`), so no re-signal
        unknown = False
        if pgid is not None:
            reaped = self.proc.returncode is not None
            if not reaped and hasattr(os, "waitid"):
                try:
                    os.waitid(os.P_PID, self.proc.pid,  # type: ignore[attr-defined]
                              os.WEXITED | os.WNOWAIT | os.WNOHANG)  # type: ignore[attr-defined]
                except ChildProcessError:
                    reaped = True              # someone reaped it behind the handle's back
            if reaped:
                unknown = True                 # the group id may already be someone else's
            else:
                try:
                    signal_group(pgid, signal.SIGKILL)   # never 0/1/our own group (0.0.27 CI)
                except (ProcessLookupError, PermissionError):
                    pass                       # the group is already empty
        if self._real and self.proc.returncode is None:
            try:
                self.proc.wait(END_BOUND_S)
            except subprocess.TimeoutExpired:
                return Ended(False, unknown, f"the process did not end within {END_BOUND_S:.0f} s")
            except ChildProcessError:
                unknown = True
        self._exited.set()
        return Ended(True, unknown)

    def _end_win(self) -> Ended:
        job = self._job
        if job is not None:
            job.terminate()
            # R-B8: wait while the count FALLS. Only END_BOUND_S with no fall — Windows' own
            # rundown after TerminateJobObject, not the user's work — is reported.
            deadline = _monotonic() + END_BOUND_S
            delay = _POLL_S
            last = None
            while True:
                n = job.active()
                if n == 0:
                    break
                if n is not None and (last is None or n < last):
                    last = n
                    deadline = _monotonic() + END_BOUND_S
                left = deadline - _monotonic()
                if left <= 0:
                    return Ended(False, False,
                                 f"{'some' if n is None else n} process(es) mokata started were "
                                 f"still running {END_BOUND_S:.0f} s after their number last fell "
                                 f"(mokata ended their Job Object) — they may still hold files in "
                                 f"the working directory")
                _sleep(min(delay, left))
                delay = min(delay * 2, _POLL_MAX_S)
        try:
            self.proc.wait(END_BOUND_S)
        except subprocess.TimeoutExpired:
            return Ended(False, False, f"the process did not end within {END_BOUND_S:.0f} s")
        self._exited.set()
        return Ended(True, False)

    def close(self) -> None:
        """End the tree if nobody has (as Windows' KILL_ON_JOB_CLOSE would), then release the Job
        Object handle and the macOS kqueue. Idempotent; never raises."""
        try:
            self.end()
        finally:                               # released even when the end was interrupted
            with self._lock:
                job, self._job = self._job, None
                kq, self._kq = self._kq, None
                try:
                    if job is not None:
                        job.close()
                    if kq is not None:
                        kq.close()  # type: ignore[attr-defined]
                except OSError:
                    pass
            with _LIVE_LOCK:
                _LIVE.discard(self)


#: cmd.exe's internal commands: a first word that is one of these is cmd's own, never a program.
_CMD_BUILTINS = frozenset((
    "assoc break call cd chdir cls color copy date del dir echo endlocal erase exit for ftype goto "
    "if md mkdir mklink move path pause popd prompt pushd rd rem ren rename rmdir set setlocal "
    "shift start time title type ver verify vol").split())
#: The builtins that run ANOTHER command named in their own arguments — a program mokata would
#: have to read a second grammar to find, so a line using one is refused (round 4).
_CMD_RUNS_ANOTHER = frozenset(("call", "start", "if", "for"))


#: cmd.exe's TOKEN DELIMITERS (B2 verifier M1): what separates the command word from its arguments
#: — blank, tab, `,` `;` `=`. A leading run of them is skipped. (VT and FF are delimiters too, but
#: a line holding one is refused before this is read.) The no-break space is NOT one: real cmd ran a
#: planted `\xa0.cmd` for the line `\xa0` (round 4, probe 43464625189), so NBSP is part of the word
#: — a word holding it is not on PATH and is refused, the safe side of being wrong about it.
_CMD_DELIMS = " \t,;=\x0b\x0c"
#: What else ends the command word: the redirection / chaining / grouping specials, and `/` (an
#: external command's switch starts there: `ping/?`).
_CMD_WORD_END = _CMD_DELIMS + "&|<>()/"


def _cmd_first_word(line: str) -> "Tuple[Optional[str], int, int]":
    """`(word, start, end)`: the first program word of ONE `cmd /c` command as cmd.exe reads it —
    a leading run of delimiters and `@` skipped; quotes toggle and are dropped (`my"tool"` is
    `mytool`); a `^` is dropped (a line holding an unquoted `^` is refused before this is read).
    `word` is None when the command is empty or starts with a group or a redirection."""
    n = len(line)
    i = 0
    while i < n and (line[i] in _CMD_DELIMS or line[i] == "@"):
        i += 1
    if i >= n or line[i] in "(&|<>)":
        return None, i, i
    out: "List[str]" = []
    quoted = False
    j = i
    while j < n:
        c = line[j]
        if c == '"':
            quoted = not quoted
            j += 1
            continue
        if not quoted:
            if c == "^":
                j += 1
                continue
            if c in _CMD_WORD_END:
                break
        out.append(c)
        j += 1
    return "".join(out), i, j


def _cmd_refused(line: str, why: str, fix: str) -> "OSError":
    from .errors import CmdLineRefused     # lazy: the hook path never pays for `errors`/typing
    return CmdLineRefused(f"refused: the command {line!r} {why} — {fix}")


def _cmd_control_char(line: str) -> "Optional[str]":
    """The first C0 control character other than tab, DEL, or C1 control character in `line`."""
    for c in line:
        o = ord(c)
        if (o < 0x20 and c != "\t") or 0x7F <= o <= 0x9F:
            return c
    return None


def _cmd_commands(line: str) -> "List[Tuple[int, int]]":
    """The `(start, end)` of every command in the line: cmd runs one after another at an unquoted
    `&`, `&&`, `|` or `||` (a `&` right after `<`/`>` is a handle — `2>&1` — not a separator).
    Raises the refusal for an unquoted `^` (an escape changes what is a separator) and an unquoted
    `(` / `)` (a group: commands inside it that this reading does not split)."""
    out: "List[Tuple[int, int]]" = []
    quoted = False
    start = j = 0
    n = len(line)
    while j < n:
        c = line[j]
        if c == '"':
            quoted = not quoted
        elif not quoted:
            if c == "^":
                raise _cmd_refused(line, "uses cmd's `^` escape",
                                   "quote the character instead (`\"&\"`), or put the command in "
                                   "a script and name the script")
            if c in "()":
                raise _cmd_refused(line, "uses a `( … )` group",
                                   "write the commands without the parentheses, joined by `&&`")
            if c in "&|" and not (c == "&" and j and line[j - 1] in "<>"):
                out.append((start, j))
                j += 2 if j + 1 < n and line[j + 1] == c else 1
                start = j
                continue
        j += 1
    out.append((start, n))
    return out


def _cmd_refused_named(text: str) -> "OSError":
    from .errors import CmdLineRefused
    return CmdLineRefused(text)


def __getattr__(name: str) -> object:
    """`quiet_spawn.CmdLineRefused` without importing `errors` on the hook path (PEP 562)."""
    if name == "CmdLineRefused":
        from .errors import CmdLineRefused
        return CmdLineRefused
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def win_shell_line(line: str, cwd: "Optional[str]" = None,
                   env: "Optional[Dict[str, str]]" = None) -> str:
    """The baseline / statusLine one-liner as `cmd /c` must receive it on Windows (0.0.29 R-B10).

    cmd.exe searches the CURRENT directory before PATH for every program it runs — and, when a
    name is found nowhere, it tries `<name> <arguments>.ext` there too — so a repo could answer for
    `pytest` with a planted `pytest.cmd` (probe #45, run 43406170295). mokata therefore never lets
    cmd search for a program: it reads the line, finds every program cmd would look for, resolves
    each ITSELF from the user's PATH (absolute directories only, never the cwd, nothing inside the
    cwd however spelled; PATHEXT, `.cmd`/`.bat` included — cmd runs a PATH batch file anyway) and
    hands cmd the quoted absolute path in its place. A line it cannot read exactly is REFUSED by
    name, never passed through — the rule is "resolved or refused", not a list of bad characters:

    * a control character — CR, LF, NUL, any C0 other than tab, DEL, any C1: cmd ends or rewrites
      a command there (round-3 verifier H1: `mytool\\nq`);
    * `%` or `!` anywhere: cmd expands variables BEFORE it parses, so the text it parses is not the
      text mokata read (`%X%` runs whatever X names, `%X:~0,0%mytool` hides a word). The value is
      the user's own environment, but the program it names would still be searched for in the
      cwd — write the program itself;
    * an unquoted `^` or `(` / `)`: an escape or a group changes where commands begin;
    * a command that starts with a redirection, has an empty program name, or holds text but no
      program (`, ;`, `@`) — only a blank command is "nothing to run";
    * `call` / `start` / `if` / `for`: they run another command from their own arguments;
    * a program that is not on PATH (round-3 verifier M2: `mytool q` ran a planted `mytool q.cmd`)
      — with the planted file named when there is one, and the two ways out;
    * a program that is itself a shell host (`cmd`, `powershell`, `wsl`, … — `exe_path.SHELL_HOSTS`):
      it would re-parse its own arguments and search the cwd again.

    Every command in an `&` / `&&` / `|` / `||` chain is resolved, not only the first. Left as
    written: cmd's other builtins (`cd`, `echo`, … — cmd's own, unquoted) and an explicit path
    (`tools\\x`, `.\\gradlew.bat`, `C:x`): a path names the file the user meant, in the repository
    if that is where it points. No environment variable is set, so nothing changes for the suite's
    own children."""
    bad = _cmd_control_char(line)
    if bad is not None:
        raise _cmd_refused(line, f"holds the control character U+{ord(bad):04X}",
                           "cmd.exe ends or rewrites a command there, so mokata cannot know what "
                           "it would run; write the command on one line of plain characters")
    for ch in "%!":
        if ch in line:
            raise _cmd_refused(line, f"uses `{ch}` (a cmd variable)",
                               "cmd expands it before it reads the command, so mokata cannot "
                               "resolve the program; write the program and value themselves")
    from .exe_path import SHELL_HOSTS, path_dirs, which_for_cmd
    env_ = os.environ if env is None else env
    here = os.path.abspath(cwd or os.getcwd())
    dirs = path_dirs(env_, here)
    out = line
    for a, b in reversed(_cmd_commands(line)):
        seg = line[a:b]
        word, i, end = _cmd_first_word(seg)
        if word is None:
            if not seg.strip(" \t"):
                continue                                   # nothing to run (`x &` / `& x`)
            if i >= len(seg):                              # `, ;` / `@`: real cmd ran `,.cmd`
                raise _cmd_refused(line, "has a command with no program (only `,;=@`)",
                                   "remove the stray characters")
            raise _cmd_refused(line, "starts a command with a redirection",
                               "put the redirection after the program (`pytest > out.txt`)")
        if not word:
            raise _cmd_refused(line, "has an empty program name (`\"\"`)",
                               "name the program")
        base = word.rstrip(". ")
        if "\\" in word or (len(word) > 1 and word[1] == ":"):
            continue                                       # an explicit path: the user's file
        if base and seg[i:end] == word and base.lower() in _CMD_BUILTINS:
            if base.lower() in _CMD_RUNS_ANOTHER:
                raise _cmd_refused(line, f"uses `{base}`, which runs another command",
                                   "name the program directly, or put the logic in a script")
            continue                                       # cmd's own: no search
        found, planted = (which_for_cmd(base, dirs, env_.get("PATHEXT"), here) if base
                          else (None, None))
        if found is None and planted is not None:
            raise _cmd_refused_named(
                f"refused: `{word}` would run `{planted}` from the repository's own directory "
                f"(cmd.exe looks in the current directory before PATH) — name the test program "
                f"itself, or spell it explicitly as `.\\{planted}` if running the repository's own "
                f"script is what you mean")
        if found is None:
            raise _cmd_refused(line, f"runs `{word}`, which is not on PATH",
                               "mokata resolves a program from PATH only, never the working "
                               "directory: install it or add its directory to PATH, or name it "
                               "by path (`.\\script.cmd` for the repository's own script)")
        if os.path.splitext(os.path.basename(found))[0].lower() in SHELL_HOSTS:
            raise _cmd_refused(line, f"runs the shell `{found}`",
                               "it would read its own arguments as commands; run the program "
                               "directly")
        out = f'{out[:a + i]}"{found}"{out[a + end:]}'
    return out


def spawn_jobbed(argv: "Optional[Sequence[str]]" = None, *, shell_line: "Optional[str]" = None,
                 stdin: object = None, nt: "Optional[bool]" = None, _kernel32: object = None,
                 _resume: object = None, **popen_kw: object) -> Jobbed:
    """Start exactly one of `argv` (a list; the normal case) or `shell_line` (a string run by
    PYTHON's own `shell=True`: `/bin/sh` on POSIX, the parent's `ComSpec /c "…"` on Windows — no
    `sh` looked up on a repo-activated PATH, no re-implemented quoting; on Windows its first
    program is resolved by `win_shell_line`) as a tree the returned handle owns. Own session /
    process group, hidden console (`detached`).
    Windows: CREATE_SUSPENDED → into a new KILL_ON_JOB_CLOSE job → resumed. Any failure while it
    is held (an `OSError` or anything else) kills it while still suspended and propagates; a job
    Windows refuses raises `OSError(NO_JOB …)` with the remedy. Nothing ran.
    POSIX with the lifeline on (the MCP server): the command runs under `_lifeline.py`, which ends
    the group if this process dies; a start the shim could not make is the same `OSError`.
    A command Python refuses to start (a NUL byte: `ValueError`) is `OSError("could not start:
    …")` — the one place (0.0.29 PROCESS-DOOR-LOWS a).
    From Popen to the handle's registration is a CRITICAL window (R-B5): a second Ctrl-C there is
    delivered after the tree is registered, and the tree is then ended.
    `nt`, `_kernel32` and `_resume` inject the platform for the tests."""
    if (argv is None) == (shell_line is None):
        raise ValueError("spawn_jobbed takes exactly one of argv or shell_line")
    is_nt = _windows() if nt is None else nt
    if shell_line is not None and is_nt:
        shell_line = win_shell_line(shell_line, popen_kw.get("cwd"),  # type: ignore[arg-type]
                                    popen_kw.get("env"))  # type: ignore[arg-type]
    args: object = list(argv) if argv is not None else shell_line
    use_shell = shell_line is not None
    status: "Optional[Tuple[int, int]]" = None
    if not is_nt and _LIFELINE:
        base = list(argv) if argv is not None else ["/bin/sh", "-c", shell_line]
        args, status = _lifelined(base, popen_kw)  # type: ignore[arg-type]
        use_shell = False
    flags = detached(suspended=is_nt, nt=is_nt) if _windows() or not is_nt else {}
    tree: "Optional[Jobbed]" = None
    _enter_critical()
    try:
        try:
            try:
                proc = subprocess.Popen(args, stdin=stdin, shell=use_shell,  # type: ignore[call-overload]  # nosec B602 — the door's one shell: shell_line
                                        **popen_kw, **flags)
            except ValueError as exc:          # a NUL byte in the command: it cannot start
                raise OSError(f"could not start: {exc}") from exc
            except OSError as exc:
                # Inside a job at its process limit Windows refuses the CREATE itself
                # (ERROR_NOT_ENOUGH_QUOTA): the same "no job of our own" fact as a refused
                # assignment, so the same words.
                if is_nt and getattr(exc, "winerror", None) == _ERROR_NOT_ENOUGH_QUOTA:
                    raise _no_job("CreateProcess", str(exc)) from exc
                raise
            finally:
                if status is not None:
                    os.close(status[1])        # only the shim holds the write end now
        except BaseException:
            if status is not None:
                os.close(status[0])
            raise
        tree = _hold(proc, is_nt, status, _kernel32, _resume)
        with _LIVE_LOCK:
            _LIVE.add(tree)
    finally:
        try:
            _leave_critical()
        except BaseException:
            if tree is not None:               # escalated inside the window: end what started
                tree.close()
            raise
    return tree


def _hold(proc: "subprocess.Popen", is_nt: bool, status: "Optional[Tuple[int, int]]",
          _kernel32: object, _resume: object) -> Jobbed:
    """The handle for a process `spawn_jobbed` just started — or, when it cannot be held, the
    process ended and the refusal raised: nothing is ever left running unowned."""
    if status is not None:
        try:
            refused = _read_start_status(status)
        except BaseException:
            _end_unheld(proc)
            raise
        if refused is not None:
            _end_unheld(proc)                  # the shim exits 127 on its own; reap it
            raise refused
    if not is_nt:
        try:
            return Jobbed(proc, None, False)
        except BaseException:
            # The handle could not be built (macOS: `kqueue` at EMFILE). A door that refuses
            # leaves nothing running: the process is ours and unreaped, so its group id is still
            # its own — end the group and reap it, then let the refusal through (verifier M1).
            _end_unheld(proc)
            raise
    held = False
    job: "Optional[WinJob]" = None
    try:
        handle = int(getattr(proc, "_handle", proc.pid))
        job = WinJob.make(handle, _kernel32)  # type: ignore[arg-type]
        (_resume or _resume_process)(proc)  # type: ignore[operator]
        held = True
    finally:
        if not held:                           # never leave a suspended process behind
            try:
                proc.kill()
                proc.wait(END_BOUND_S)
            except (OSError, subprocess.SubprocessError):
                pass
            if job is not None:
                job.close()
    try:
        return Jobbed(proc, job, True)
    except BaseException:                      # the same rule as POSIX above: nothing left running
        if job is not None:
            job.close()                        # KILL_ON_JOB_CLOSE ends the whole tree
        _end_unheld(proc)
        raise


def _end_unheld(proc: "subprocess.Popen") -> None:
    """End a process the door started but could not hold, and reap it. Only a real, unreaped
    child of ours reaches here, so its process group (its own session) is still its own."""
    import signal
    sig = getattr(signal, "SIGKILL", signal.SIGTERM)
    if type(proc.pid) is int and proc.returncode is None:
        try:
            grouped = signal_group(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            grouped = False                    # it leads no group of its own
        if not grouped:                        # no group (Windows), or none: the process itself
            try:
                signal_pid(proc.pid, sig)
            except (ProcessLookupError, PermissionError):
                pass                           # already gone
    try:
        proc.wait(END_BOUND_S)
    except (OSError, subprocess.SubprocessError):
        pass


def _decode(data: bytes, nt: bool) -> str:
    """R-B11: UTF-8 strictly; on Windows then the console's code page (cp1252 / an OEM page); else
    UTF-8 with `replace`. The locale step is Windows-only: elsewhere it would silently turn one
    stray byte into a whole page of mojibake (attack M12)."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    if nt:
        import locale
        try:
            return data.decode(locale.getpreferredencoding(False))
        except (UnicodeDecodeError, LookupError):
            pass
    return data.decode("utf-8", "replace")


def _read_out(f: object, tail_bytes: "Optional[int]") -> bytes:
    """The whole file — or, for a DISPLAY caller that asked for `tail_bytes`, exactly its last N
    bytes, read by seeking from the end (no middle cut ever), started at a UTF-8 character
    boundary so the strict decode does not fail on a split character."""
    if tail_bytes is None:
        f.seek(0)  # type: ignore[attr-defined]
        return f.read()  # type: ignore[attr-defined]
    size = os.fstat(f.fileno()).st_size  # type: ignore[attr-defined]
    start = max(0, size - max(0, int(tail_bytes)))
    f.seek(start)  # type: ignore[attr-defined]
    data = f.read()  # type: ignore[attr-defined]
    if start:
        k = 0
        while k < len(data) and k < 3 and (data[k] & 0xC0) == 0x80:
            k += 1
        data = data[k:]
    return data


def run_jobbed(argv: "Optional[Sequence[str]]" = None, *, shell_line: "Optional[str]" = None,
               timeout: "Optional[float]", cwd: "Optional[str]" = None,
               env: "Optional[Dict[str, str]]" = None, stdin_bytes: bytes = b"",
               tail_bytes: "Optional[int]" = None,
               progress: "Optional[Callable[[int], None]]" = None,
               nt: "Optional[bool]" = None) -> "Tuple[int, str, str]":
    """Run a tree to its end and return `(rc, stdout, stderr)`. stdin and the output are TEMP
    FILES, not pipes: a grandchild that inherited them blocks nobody.

    THE CONTRACT (0.0.29 R-B3/R-B11, owned by lane B; lane G consumes it):
      * One wait loop, in slices of `_POLL_MAX_S`. Each slice: (1) if the calling thread's
        `knowledge.cancel` flag is set (read through `sys.modules`, never imported) the tree is
        ended and `Cancelled` raised; (2) `progress(total)` with `total` = bytes written so far
        to the stdout + stderr files (`fstat`, no read; output a child buffers is not counted
        until it writes it); (3) `timeout` seconds elapsed → the tree is ended and
        `subprocess.TimeoutExpired` raised. `timeout=None`: no clock — until exit or cancel.
      * Anything `progress` raises ends the tree and propagates unchanged. The callback must not
        end or close the tree itself.
      * Output is returned WHOLE by default — every parsing caller gets every byte. `tail_bytes=N`
        (display callers only) returns exactly the last N bytes of each stream.
      * Decoded UTF-8 strictly, else (Windows) the locale's code page, else UTF-8 `replace`.
      * After a normal exit what the leader left behind is ended too. `OSError` when it cannot
        start (`NO_JOB` on Windows; a NUL byte: "could not start") or when its tree could not be
        emptied (the reason is the message)."""
    import tempfile
    import time
    is_nt = _windows() if nt is None else nt
    with tempfile.TemporaryFile() as fi, tempfile.TemporaryFile() as fo, \
            tempfile.TemporaryFile() as fe:
        if stdin_bytes:
            fi.write(stdin_bytes)
            fi.seek(0)
        tree = spawn_jobbed(argv, shell_line=shell_line, stdin=fi, stdout=fo, stderr=fe,
                            cwd=cwd, env=env, nt=nt)
        try:
            start = time.perf_counter()
            while True:
                c = _cancel_mod()
                if c is not None and c.cancelled():  # type: ignore[attr-defined]
                    tree.end()
                    raise c.Cancelled("cancelled")  # type: ignore[attr-defined]
                if progress is not None:
                    progress(os.fstat(fo.fileno()).st_size + os.fstat(fe.fileno()).st_size)
                left = None
                if timeout is not None:
                    left = timeout - (time.perf_counter() - start)
                    if left <= 0:
                        tree.end()
                        raise subprocess.TimeoutExpired(tree.proc.args, timeout)
                try:
                    tree.wait_exit(_POLL_MAX_S if left is None else min(left, _POLL_MAX_S))
                    break
                except subprocess.TimeoutExpired:
                    continue
            ended = tree.end(leftovers_only=True)
            if not ended.ok:
                raise OSError(ended.why)
            rc = tree.proc.returncode if tree.proc.returncode is not None else -1
        finally:
            tree.close()                       # any exit — a raising callback too — ends the tree
        return (rc, _decode(_read_out(fo, tail_bytes), is_nt),
                _decode(_read_out(fe, tail_bytes), is_nt))


# ============================================================================ the spawn register
#: Kinds of spawn that are NOT the door (R1). Each `TREELESS` row says which and why; the sweep in
#: `tests/test_b_one_process_door.py` reds on any `Popen` / `subprocess.run|call|check_*` in `src/`
#: that is neither the door nor a row here — a new spawn is a red, not a review comment.
SINGLE = "single-process"      # a known binary that starts no children of its own (git, ps, rg…)
OUTLIVES = "outlives"          # the child must OUTLIVE mokata by design (ends on its own)
INTERACTIVE = "interactive"    # the user's own shell in a pty: its session is the user's to end
STANDALONE = "standalone"      # a STDLIB-ONLY program that cannot import the door (it replaces it)
#: The four kinds (0.0.29 R-B9). `own-tree` and `filed` are gone: every launcher that had the
#: door's hang shape now IS the door (`SPAWN-REGISTER-FILED-LAUNCHERS`).
KINDS = (SINGLE, OUTLIVES, INTERACTIVE, STANDALONE)

#: (path under src/, enclosing qualname) -> (kind, program, reason).
TREELESS = {
    ("mokata/git_safe.py", "Git._exec"): (SINGLE, "git", "mokata's git, one process per call"),
    ("mokata/harness_setup.py", "local_settings_ignored"): (SINGLE, "git", "`git check-ignore`"),
    ("mokata/live_context.py", "git_line"): (SINGLE, "git", "`git status`"),
    ("mokata/merge_lock.py", "_sysctl"): (SINGLE, "/usr/sbin/sysctl", "absolute path, one process"),
    ("mokata/merge_lock.py", "pid_start"): (SINGLE, "/bin/ps", "absolute path, one process"),
    ("mokata/notify.py", "_run_argv"):
        (SINGLE, "osascript / notify-send / powershell toast",
         "the platform notifier; output discarded (no pipe a grandchild could hold)"),
    ("mokata/project.py", "_default_git"): (SINGLE, "git", "mokata's git"),
    ("mokata/session_ancestry.py", "_ps_lookup"): (SINGLE, "ps", "`ps -A`, one process"),
    ("mokata/session_worktrees.py", "_git"): (SINGLE, "git", "mokata's git"),
    ("mokata/session_worktrees.py", "_git_bytes"): (SINGLE, "git", "mokata's git"),
    ("mokata/test_ids.py", "tracked_ending"): (SINGLE, "git", "`git ls-files`"),
    ("mokata/worktree.py", "_default_git"): (SINGLE, "git", "mokata's git"),
    ("mokata/ide/diffs.py", "_git"): (SINGLE, "git", "mokata's git"),
    ("mokata/ide/diffview.py", "context_page"): (SINGLE, "git", "`git show`, kill_on_cancel"),
    ("mokata/ide/imagediff.py", "_git_size"): (SINGLE, "git", "`git cat-file --batch-check`"),
    ("mokata/ide/review_context.py", "_git"): (SINGLE, "git", "mokata's git"),
    ("mokata/ide/review_context.py", "_streamed"): (SINGLE, "git", "streamed git, kill_on_cancel"),
    ("mokata/ide/search.py", "_rg"): (SINGLE, "rg", "ripgrep (threads, not processes)"),
    ("mokata/ide/tableview.py", "spool_revision"): (SINGLE, "git", "`git show` to a spool file"),
    ("mokata/explain/bundle.py", "_git_z"): (SINGLE, "git", "mokata's git"),
    ("mokata/explain/bundle.py", "base_objects"): (SINGLE, "git", "`git cat-file --batch-check`"),
    ("mokata/explain/bundle.py", "cat_blobs"): (SINGLE, "git", "`git cat-file --batch`"),
    ("mokata/explain/bundle.py", "git_grep"): (SINGLE, "git", "`git grep`"),
    ("mokata/memory/grounding.py", "resolve_shas"): (SINGLE, "git", "`git cat-file`"),
    ("mokata/memory/grounding.py", "_is_ancestor"): (SINGLE, "git", "`git merge-base`"),
    ("mokata/memory/intentions.py", "session_signals._git"): (SINGLE, "git", "mokata's git"),
    ("mokata/cli_commands/knowledge.py", "_git_changed_files"): (SINGLE, "git", "`git diff`"),
    ("mokata/knowledge/freshness.py", "_default_git_run"): (SINGLE, "git", "mokata's git"),
    ("mokata/ide/launch.py", "_open_or_focus"):
        (OUTLIVES, "mokata ide server", "the IDE server must outlive the command that opened it"),
    ("mokata/ide/handoff.py", "_spawn"):
        (OUTLIVES, "python -m mokata.ide.handoff", "a hand-off worker that outlives the page"),
    ("mokata/knowledge/crg_build.py", "_spawn_worker"):
        (OUTLIVES, "python -m mokata.knowledge.crg_build", "the background index builder"),
    ("mokata_emit/__init__.py", "spawn_detached"):
        (OUTLIVES, "the hook drainer",
         "drains the hook journal after the hook has returned (Windows `Popen`; POSIX "
         "`os.posix_spawn` in a new session, with a new-group fallback: three sites)"),
    ("mokata/update.py", "_fg_run"):
        (OUTLIVES, "the installer (pip / uv / brew)",
         "foreground, stdio inherited, no timeout: the user watches it and Ctrl-C ends it"),
    ("mokata/ide/terminal.py", "Terminal.__init__"):
        (INTERACTIVE, "the user's shell in a pty", "its own session, ended by `signal_group` on "
                                                   "POSIX (row `TERMINAL-*`); interactive by design"),
    ("mokata/_updater.py", "_run"):
        (STANDALONE, "pip / uv", "the detached updater's steps: stdlib only (it replaces mokata), "
                                 "output to a temp file, sliced wait, the step killed at its bound"),
}

#: Spawn call sites per registered function when more than one (default 1). The sweep counts
#: SITES, so a second spawn added inside a registered function is a red (verifier M2, round 2).
TREELESS_SITES: "Dict[Tuple[str, str], int]" = {
    ("mokata_emit/__init__.py", "spawn_detached"): 3,
}


__all__ = ["END_BOUND_S", "Ended", "Jobbed", "KINDS", "NO_JOB", "TREELESS", "TREELESS_SITES", "WinJob",
           "detached", "enable_lifeline", "end_all", "hidden", "lifeline_enabled", "run_jobbed",
           "signal_group", "signal_pid", "spawn_jobbed", "win_shell_line", "CmdLineRefused"]
