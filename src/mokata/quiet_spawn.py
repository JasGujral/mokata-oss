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
    from typing import Dict, Optional, Sequence, Tuple


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


class WinJob:
    """A KILL_ON_JOB_CLOSE Windows Job Object holding one process tree (moved from `ide/lsp.py`).
    `make` raises `OSError(NO_JOB …)` when Windows refuses any step — there is no "not ok" job.
    `kernel32` is injectable so the logic runs off Windows; `ctypes` is imported only here."""

    _KILL_ON_JOB_CLOSE = 0x00002000
    _EXTENDED_LIMIT_INFORMATION = 9
    _BASIC_ACCOUNTING_INFORMATION = 1

    def __init__(self, k32: object, handle: object, structs: object) -> None:
        self._k32, self.handle, self._structs = k32, handle, structs

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
        if job is not None:
            self._k32.CloseHandle(job)


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
            try:
                rc = self.proc.wait(timeout) if timeout != 0 else self.proc.poll()
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
                evs = kq.control(None, 1, timeout)  # type: ignore[attr-defined]
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
                                 flags | (0 if deadline is None else os.WNOHANG))  # type: ignore[attr-defined]
            except ChildProcessError:
                return True                    # reaped by something else: exited, status lost
            if info is not None and info.si_pid:
                self._status = (info.si_status if info.si_code == os.CLD_EXITED  # type: ignore[attr-defined]
                                else -info.si_status)
                return True
            if deadline is None:
                continue
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

    # ---- the one kill ----------------------------------------------------------------------
    def end(self, leftovers_only: bool = False) -> Ended:
        """End the whole tree and reap the leader. Never raises; idempotent (the second call
        signals nothing and returns the first answer). `leftovers_only`: the caller has seen the
        leader exit (`poll_exit`) and ends what it left behind.
        POSIX: the group is signalled only if the leader is unreaped (zombie or running); then
        the leader is reaped. Windows: `TerminateJobObject`, then wait until the job is EMPTY,
        bounded by `END_BOUND_S` — `ok=False` (with why) when it is not, never swallowed."""
        with self._lock:
            if self.last_end is not None:
                return self.last_end
            try:
                self.last_end = self._end_win() if self._nt else self._end_posix(leftovers_only)
            except (OSError, ValueError) as exc:   # an end reports; it never raises mid-cleanup
                self.last_end = Ended(False, False, f"ending the process tree failed: {exc}")
            return self.last_end

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
            deadline = _monotonic() + END_BOUND_S
            delay = _POLL_S
            while True:
                n = job.active()
                if n == 0:
                    break
                left = deadline - _monotonic()
                if left <= 0:
                    return Ended(False, False,
                                 f"{'some' if n is None else n} process(es) mokata started were "
                                 f"still running {END_BOUND_S:.0f} s after it ended their Job "
                                 f"Object — they may still hold files in the working directory")
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
        self.end()
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


def spawn_jobbed(argv: "Optional[Sequence[str]]" = None, *, shell_line: "Optional[str]" = None,
                 stdin: object = None, nt: "Optional[bool]" = None, _kernel32: object = None,
                 _resume: object = None, **popen_kw: object) -> Jobbed:
    """Start exactly one of `argv` (a list; the normal case) or `shell_line` (a string run by
    PYTHON's own `shell=True`: `/bin/sh` on POSIX, the parent's `ComSpec /c "…"` on Windows — no
    `sh` looked up on a repo-activated PATH, no re-implemented quoting) as a tree the returned
    handle owns. Own session / process group, hidden console (`detached`).
    Windows: CREATE_SUSPENDED → into a new KILL_ON_JOB_CLOSE job → resumed. Any failure while it
    is held (an `OSError` or anything else) kills it while still suspended and propagates; a job
    Windows refuses raises `OSError(NO_JOB …)` with the remedy. Nothing ran.
    `nt`, `_kernel32` and `_resume` inject the platform for the tests."""
    if (argv is None) == (shell_line is None):
        raise ValueError("spawn_jobbed takes exactly one of argv or shell_line")
    is_nt = _windows() if nt is None else nt
    args: object = list(argv) if argv is not None else shell_line
    flags = detached(suspended=is_nt, nt=is_nt) if _windows() or not is_nt else {}
    try:
        proc = subprocess.Popen(args, stdin=stdin, shell=shell_line is not None,  # type: ignore[call-overload]  # nosec B602 — the door's one shell: shell_line
                                **popen_kw, **flags)
    except OSError as exc:
        # Inside a job at its process limit Windows refuses the CREATE itself (ERROR_NOT_ENOUGH_
        # QUOTA): the same "no job of our own" fact as a refused assignment, so the same words.
        if is_nt and getattr(exc, "winerror", None) == _ERROR_NOT_ENOUGH_QUOTA:
            raise _no_job("CreateProcess", str(exc)) from exc
        raise
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


def run_jobbed(argv: "Optional[Sequence[str]]" = None, *, shell_line: "Optional[str]" = None,
               timeout: float, cwd: "Optional[str]" = None,
               env: "Optional[Dict[str, str]]" = None, stdin_bytes: bytes = b"",
               nt: "Optional[bool]" = None) -> "Tuple[int, str, str]":
    """Run a tree to its end and return `(rc, stdout, stderr)` (UTF-8, `replace`). stdin and the
    output are TEMP FILES, not pipes: a grandchild that inherited them blocks nobody (on Windows
    `subprocess.run(capture_output, timeout)` answers a timeout with an UNBOUNDED `communicate()`).
    After `timeout` seconds the tree is ended and `subprocess.TimeoutExpired` raised; after a normal
    exit what the leader left behind is ended too. `OSError` when it cannot start (`NO_JOB` on
    Windows) or when its tree could not be emptied (the reason is the message)."""
    import tempfile
    with tempfile.TemporaryFile() as fi, tempfile.TemporaryFile() as fo, \
            tempfile.TemporaryFile() as fe:
        if stdin_bytes:
            fi.write(stdin_bytes)
            fi.seek(0)
        tree = spawn_jobbed(argv, shell_line=shell_line, stdin=fi, stdout=fo, stderr=fe,
                            cwd=cwd, env=env, nt=nt)
        try:
            try:
                tree.wait_exit(timeout)
            except subprocess.TimeoutExpired:
                tree.end()
                raise
            ended = tree.end(leftovers_only=True)
            if not ended.ok:
                raise OSError(ended.why)
            rc = tree.proc.returncode if tree.proc.returncode is not None else -1
        finally:
            tree.close()
        fo.seek(0)
        fe.seek(0)
        return rc, fo.read().decode("utf-8", "replace"), fe.read().decode("utf-8", "replace")


# ============================================================================ the spawn register
#: Kinds of spawn that are NOT the door (R1). Each `TREELESS` row says which and why; the sweep in
#: `tests/test_b_one_process_door.py` reds on any `Popen` / `subprocess.run|call|check_*` in `src/`
#: that is neither the door nor a row here — a new spawn is a red, not a review comment.
SINGLE = "single-process"      # a known binary that starts no children of its own (git, ps, rg…)
OUTLIVES = "outlives"          # the child must OUTLIVE mokata by design (ends on its own)
OWN_TREE = "own-tree"          # ended by a mechanism of its own, owned by another row
FILED = "filed"                # a launcher with the door's hang shape, outside lane B's files:
                               # disclosed to doc 84 (`SPAWN-REGISTER-FILED-LAUNCHERS`), not hidden

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
        (OWN_TREE, "the user's shell in a pty", "its own session, ended by `signal_group` on POSIX "
                                               "(row `TERMINAL-*`); interactive by design"),
    ("mokata/knowledge/crg_client.py", "_McpStdioSession._ensure"):
        (OWN_TREE, "code-review-graph serve", "row `CRG-SESSION-CLOSE-LEAKS-FDS` owns its close"),
    ("mokata/ide/health.py", "_stream"):
        (OWN_TREE, "mokata's own updater", "streamed under kill_on_cancel; mokata's own program"),
    ("mokata/harness_cert.py", "StdioMcpClient.start"):
        (OWN_TREE, "mokata-mcp", "the G4 harness's client of mokata's own MCP server (CI script)"),
    ("mokata/harness_cert.py", "default_approver._approve"):
        (OWN_TREE, "python -m mokata approve", "mokata's own CLI, run by the G4 harness"),
    ("mokata/claude_code_version.py", "installed_version"):
        (FILED, "claude --version", "a node launcher, capture_output + timeout"),
    ("mokata/install_isolation.py", "_ask_version"):
        (FILED, "mokata --version", "a console-script launcher, capture_output + timeout"),
    ("mokata/_updater.py", "_run"):
        (FILED, "pip / uv", "the detached updater's steps, capture_output + timeout"),
    ("mokata/ide/handoff.py", "_update"):
        (FILED, "pip / uv", "the IDE's update hand-off; output discarded, no timeout"),
    ("mokata/govern/domain_gates.py", "_run_tool"):
        (FILED, "a domain gate's tool (eslint, …; repo node_modules/.bin first)",
         "capture_output + timeout — a repo-chosen launcher, the class R1 names"),
    ("mokata/knowledge/crg_client.py", "CodeReviewGraphClient._default_run_cli"):
        (FILED, "code-review-graph (CLI)", "a console-script launcher, capture_output + timeout"),
}

#: Spawn call sites per registered function when more than one (default 1). The sweep counts
#: SITES, so a second spawn added inside a registered function is a red (verifier M2, round 2).
TREELESS_SITES: "Dict[Tuple[str, str], int]" = {
    ("mokata_emit/__init__.py", "spawn_detached"): 3,
}


__all__ = ["END_BOUND_S", "Ended", "Jobbed", "NO_JOB", "TREELESS", "TREELESS_SITES", "WinJob", "detached",
           "hidden", "run_jobbed", "signal_group", "signal_pid", "spawn_jobbed"]
