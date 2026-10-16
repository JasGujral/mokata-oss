"""SESSION-BIND-BY-ANCESTOR (0.0.23 F1) — the MCP server learns WHICH Claude Code session it serves.

THE GAP
-------
Every hook receives Claude Code's `session_id` on stdin. The MCP server never does (harness gap
#25642). So every MCP-side resolution — the run an approval is keyed to, the store `spec_amend`
reads its spec from — has always called `run_resolver.resolve_run` WITHOUT a session id, which
skips the BOUND rung outright. With two live pipelines in one repo (two windows, or several
sessions brought back with `/resume`) the ladder is honestly AMBIGUOUS, and the MCP fell back to its
own process id: `spec_amend` then blocked at `spec-persisted` ("no spec is emitted for this run"),
or keyed the amendment to a run that was not the one being amended. Live report, 2026-10-15.

THE BRIDGE
----------
The hooks and the MCP server are both DESCENDANTS of the same Claude Code process. So:

  * WRITE (SessionStart + UserPromptSubmit only — never PreToolUse): the hook walks its own parent
    chain and records, per ancestor pid, `{session_id, ts, token, below}` in ONE small map under
    `.mokata/temp_local/state/session_ancestry.json`. The newest write per pid wins, so a `/resume`
    that switches session INSIDE one Claude process re-points that process's entry on its
    SessionStart(resume) and again on the next prompt.
  * READ (MCP side): the server walks ITS parent chain, nearest first. The first ancestor present in
    the map (with a matching start token) is the Claude process it shares with the hooks; that
    entry's `session_id` is the session this MCP is serving right now.

WHY IT CANNOT NAME A FOREIGN SESSION (the three guards — any failure answers None, never a guess)
-------------------------------------------------------------------------------------------------
  1. RECYCLED PID — every entry carries the process START TOKEN (Linux `/proc/<pid>/stat` field 22,
     macOS/BSD `ps lstart`, Windows creation FILETIME). A pid reused by a new process has a new
     token and never matches. A parent whose start is LATER than its child's is a recycled parent
     pid, and the walk stops there.
  2. SHARED ANCESTOR — two Claude processes under one tmux server / one IDE host share ancestors
     ABOVE themselves. The map records, for each ancestor, the hook-side branch `below` it. If any
     process on that branch is still ALIVE and is not on the reader's own chain, the matched pid is
     an ancestor of SOMEONE ELSE's Claude too — the answer is None. (The legitimate branch below
     the Claude process is the hook's own short-lived shell, long gone by the time a tool runs.)
  3. STALE WRITER — an entry written BEFORE the reader's own branch below the match was started
     (e.g. a previous Claude in the same terminal pane, now dead) is not about the reader's Claude.

FAIL-CLOSED, unchanged: no map, no matching ancestor, any guard tripping, any error -> None, and the
caller resolves exactly as it did before this module existed.

Bounded and never raises. Linux reads `/proc` directly; macOS/BSD pay ONE `ps` call per snapshot
(1s timeout, `LC_ALL=C`); Windows uses a Toolhelp snapshot via ctypes. Anything else -> no chain.

Stdlib-only. Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections import namedtuple
from typing import Callable, Dict, List, Optional, Tuple

#: The one map, a plain StateStore key under the state dir (not a run-state prefix, so it never
#: becomes a run candidate).
MAP_KEY = "session_ancestry"

#: How far up either side walks (hook -> sh -> wrapper -> claude -> shell -> terminal ...).
MAX_DEPTH = 6

#: The map never grows past this many pids (oldest writes dropped first).
MAX_ENTRIES = 64
#: How many processes a hook's own launch puts between it and the Claude process that spawned it:
#: the hook interpreter, the `bash -c` Claude Code runs it under, and the plugin's sh shim when the
#: shell does not exec it. An entry reached through MORE than this sits above some Claude.
HOOK_LAUNCH_DEPTH = 3
#: A hook that would only re-stamp an unchanged mapping skips the write if the stamp is younger
#: than this. The per-prompt hook must not write on every turn (the injection turn's byte-snapshot
#: pin, `test_h1a_s2_s3`); a session SWITCH (`/resume`) always writes at once. The cost: an MCP
#: server started inside this window resolves by the old ladder until the stamp is refreshed.
REFRESH_SECS = 30.0
#: An ancestor younger than this is the hook's own launcher (`bash -c`), born for this turn.
_TRANSIENT_SECS = 5.0

#: Clock slack for guard 3: `ps lstart` has one-second resolution.
_START_SLACK_SECS = 1.0

#: `ps` must never hold a hook up.
_PS_TIMEOUT_SECS = 1.0

#: One process as the walk sees it: parent pid, an opaque start token, and the start as epoch secs.
ProcInfo = namedtuple("ProcInfo", "ppid token started")

#: A process lookup: pid -> ProcInfo, or None when no such (live) process. Injectable for tests.
ProcLookup = Callable[[int], Optional[ProcInfo]]


# ======================================================================================
# platform process lookups
# ======================================================================================

def _linux_lookup() -> Optional[ProcLookup]:
    try:
        hz = float(os.sysconf("SC_CLK_TCK"))
        btime = None
        with open("/proc/stat", "r", encoding="ascii", errors="replace") as fh:
            for line in fh:
                if line.startswith("btime "):
                    btime = float(line.split()[1])
                    break
        if btime is None or hz <= 0:
            return None
    except (OSError, ValueError, AttributeError):
        return None

    def lookup(pid: int) -> Optional[ProcInfo]:
        try:
            with open(f"/proc/{int(pid)}/stat", "r", encoding="ascii", errors="replace") as fh:
                raw = fh.read()
            rest = raw[raw.rindex(")") + 2:].split()
            if rest[0] in ("Z", "X"):             # a zombie is not a live process
                return None
            return ProcInfo(int(rest[1]), rest[19], btime + int(rest[19]) / hz)
        except (OSError, ValueError, IndexError):
            return None
    return lookup


def _ps_lookup() -> Optional[ProcLookup]:
    """macOS / BSD — ONE `ps` snapshot of every process (pid, ppid, lstart)."""
    env = dict(os.environ, LC_ALL="C", LANG="C")
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,lstart="], capture_output=True,
                             text=True, timeout=_PS_TIMEOUT_SECS, env=env, check=False).stdout
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    table: Dict[int, ProcInfo] = {}
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) != 3:
            continue
        try:
            stamp = " ".join(parts[2].split())
            started = time.mktime(time.strptime(stamp, "%a %b %d %H:%M:%S %Y"))
            table[int(parts[0])] = ProcInfo(int(parts[1]), stamp, started)
        except (ValueError, OverflowError):
            continue
    return table.get if table else None


def _windows_lookup() -> Optional[ProcLookup]:
    """Windows — one Toolhelp snapshot for parent pids, creation time per pid on demand."""
    try:
        import ctypes
        from ctypes import wintypes

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                        ("th32ProcessID", wintypes.DWORD),
                        ("th32DefaultHeapID", ctypes.c_size_t),
                        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                        ("th32ParentProcessID", wintypes.DWORD),
                        ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                        ("szExeFile", ctypes.c_wchar * 260)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        k32.CloseHandle.argtypes = [wintypes.HANDLE]

        snap = k32.CreateToolhelp32Snapshot(0x00000002, 0)          # TH32CS_SNAPPROCESS
        if not snap or snap == wintypes.HANDLE(-1).value:
            return None
        parents: Dict[int, int] = {}
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            ok = k32.Process32FirstW(snap, ctypes.byref(entry))
            while ok and len(parents) < 100000:
                parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                ok = k32.Process32NextW(snap, ctypes.byref(entry))
        finally:
            k32.CloseHandle(snap)

        def created(pid: int) -> Optional[int]:
            h = k32.OpenProcess(0x1000, False, pid)                    # QUERY_LIMITED_INFORMATION
            if not h:
                return None
            try:
                c, e, kt, ut = (wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME(),
                                wintypes.FILETIME())
                if not k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(kt),
                                           ctypes.byref(ut)):
                    return None
                return (c.dwHighDateTime << 32) | c.dwLowDateTime
            finally:
                k32.CloseHandle(h)
    except (ImportError, AttributeError, OSError, TypeError, ValueError):
        return None

    def lookup(pid: int) -> Optional[ProcInfo]:
        try:
            if int(pid) not in parents:
                return None
            ft = created(int(pid))
            if ft is None:
                return None
            # FILETIME: 100ns ticks since 1601-01-01.
            return ProcInfo(parents[int(pid)], str(ft), ft / 1e7 - 11644473600.0)
        except (OSError, ValueError, TypeError, AttributeError):
            return None
    return lookup


def system_lookup() -> Optional[ProcLookup]:
    """This platform's process lookup, or None (unsupported / unreadable -> no ancestry at all)."""
    try:
        if sys.platform.startswith("linux") and os.path.isdir("/proc"):
            return _linux_lookup()
        if sys.platform == "win32":
            return _windows_lookup()
        if sys.platform == "darwin" or "bsd" in sys.platform:
            return _ps_lookup()
    except Exception:  # noqa: BLE001 — ancestry is an optimisation; no chain is today's behaviour
        return None
    return None


# ======================================================================================
# the chain
# ======================================================================================

def ancestors(lookup: Optional[ProcLookup], pid: Optional[int] = None,
              depth: int = MAX_DEPTH) -> List[Tuple[int, ProcInfo]]:
    """`[(pid, info), ...]` for the ancestors of `pid` (default: this process), NEAREST FIRST,
    excluding `pid` itself. Stops at pid 0/1, at an unreadable process, at `depth`, and at a parent
    that STARTED AFTER its child (a recycled parent pid — guard 1). Never raises."""
    chain: List[Tuple[int, ProcInfo]] = []
    if lookup is None:
        return chain
    try:
        cur = os.getpid() if pid is None else int(pid)
        info = lookup(cur)
        while info is not None and len(chain) < depth:
            ppid = int(info.ppid)
            if ppid <= 1 or ppid == cur:
                break
            pinfo = lookup(ppid)
            if pinfo is None or not pinfo.token:
                break
            if pinfo.started > info.started + _START_SLACK_SECS:
                break                               # the parent is younger: its pid was recycled
            chain.append((ppid, pinfo))
            cur, info = ppid, pinfo
    except Exception:  # noqa: BLE001 — a lookup that throws is an empty chain, never a raise
        return chain
    return chain


def _alive_as(lookup: ProcLookup, pid: int, token: str) -> bool:
    try:
        info = lookup(int(pid))
    except Exception:  # noqa: BLE001 — unreadable reads as ALIVE: the guard must fail closed
        return True
    return info is not None and info.token == token


# ======================================================================================
# write (hook side) / read (MCP side)
# ======================================================================================

def _store(root: str):
    from .state import StateStore
    from .tdd_state import state_dir
    return StateStore(state_dir(root))


def record_session(root: str, session_id: Optional[str], *, lookup: Optional[ProcLookup] = None,
                   pid: Optional[int] = None, now: Optional[float] = None) -> bool:
    """HOOK SIDE — record `session_id` against every ancestor of this hook process. Called from
    SessionStart and UserPromptSubmit only. Prunes dead / recycled pids and caps the map. Returns
    True when something was written. Never raises: a hook must never fail on this."""
    if not isinstance(session_id, str) or not session_id.strip():
        return False
    try:
        lookup = lookup if lookup is not None else system_lookup()
        if lookup is None:
            return False
        self_pid = os.getpid() if pid is None else int(pid)
        self_info = lookup(self_pid)
        chain = ancestors(lookup, self_pid)
        if not chain:
            return False
        ts = time.time() if now is None else float(now)
        store = _store(root)
        try:
            current = store.read(MAP_KEY) if store.exists(MAP_KEY) else {}
            have = (current or {}).get("pids") or {}
            # the hook's own transient launcher shells are new every turn — judge only the
            # ancestors that outlive a turn (the Claude process and above)
            durable = [(p, info) for p, info in chain
                       if ts - float(getattr(info, "started", 0) or 0) > _TRANSIENT_SECS]
            if durable and all(isinstance(have.get(str(p)), dict)
                   and have[str(p)].get("session_id") == session_id.strip()
                   and have[str(p)].get("token") == info.token
                   and ts - float(have[str(p)].get("ts") or 0) < REFRESH_SECS
                   for p, info in durable):
                return False                      # unchanged and fresh: no write this turn
        except Exception:  # noqa: BLE001 — an unreadable map is simply rewritten below
            pass
        branch = [[self_pid, self_info.token if self_info else ""]]
        fresh: Dict[str, dict] = {}
        for p, info in chain:
            fresh[str(p)] = {"session_id": session_id.strip(), "ts": ts, "token": info.token,
                             "below": [list(b) for b in branch]}
            branch.append([p, info.token])

        def merge(current):
            pids = current.get("pids") if isinstance(current, dict) else None
            pids = dict(pids) if isinstance(pids, dict) else {}
            pids.update(fresh)
            kept = {}
            for key, entry in pids.items():
                if not isinstance(entry, dict):
                    continue
                try:
                    info = lookup(int(key))
                except Exception:  # noqa: BLE001 — unreadable: keep it, the reader re-checks
                    info = ProcInfo(0, entry.get("token"), 0.0)
                if info is not None and info.token == entry.get("token"):
                    kept[key] = entry
            if len(kept) > MAX_ENTRIES:
                newest = sorted(kept.items(), key=lambda kv: float(kv[1].get("ts") or 0),
                                reverse=True)[:MAX_ENTRIES]
                kept = dict(newest)
            return {"schema": 1, "pids": kept}

        store.update(MAP_KEY, merge, default={})
        return True
    except Exception:  # noqa: BLE001 — observability-class write; the reader then finds nothing
        return False


def session_for_process(root: str, *, lookup: Optional[ProcLookup] = None,
                        pid: Optional[int] = None) -> Optional[str]:
    """MCP SIDE — the Claude Code session id this process is serving, or None.

    Walks this process's ancestors nearest-first; the FIRST one present in the map with a matching
    start token decides (a higher one is only ever less specific). That entry must pass guard 2
    (no live foreign branch below it) and guard 3 (written after this process's own branch below
    it started), else None. Read-only; never raises."""
    try:
        data = _store(root).read(MAP_KEY)
        pids = data.get("pids") if isinstance(data, dict) else None
        if not isinstance(pids, dict) or not pids:
            return None
        lookup = lookup if lookup is not None else system_lookup()
        if lookup is None:
            return None
        self_pid = os.getpid() if pid is None else int(pid)
        self_info = lookup(self_pid)
        chain = ancestors(lookup, self_pid)
        mine = {(p, i.token) for p, i in chain}
        own_branch = [self_info] if self_info is not None else []
        for p, info in chain:
            entry = pids.get(str(p))
            if not isinstance(entry, dict) or entry.get("token") != info.token:
                own_branch.append(info)
                continue
            sid = entry.get("session_id")
            if not isinstance(sid, str) or not sid:
                return None
            # guard 4 (0.0.23 verification M1) — the writer's hook reached this pid through more
            # than a hook's own launcher (python, its shell, the shim): the pid is ABOVE the
            # writer's Claude — a shared tmux / terminal / IDE host — not a Claude itself. Such an
            # entry names whichever session wrote last under that host, never this one.
            if len(entry.get("below") or []) > HOOK_LAUNCH_DEPTH:
                return None
            # guard 2 — a live process on the WRITER's branch that is not on ours means this pid is
            # also an ancestor of somebody else's Claude.
            for item in entry.get("below") or []:
                try:
                    bpid, btok = int(item[0]), str(item[1])
                except (TypeError, ValueError, IndexError):
                    return None
                if (bpid, btok) not in mine and bpid != self_pid and _alive_as(lookup, bpid, btok):
                    return None
            # guard 3 — the write must postdate every process on OUR branch below the match.
            ts = float(entry.get("ts") or 0)
            if any(ts + _START_SLACK_SECS < b.started for b in own_branch):
                return None
            return sid
        return None
    except Exception:  # noqa: BLE001 — no answer is the strict answer; the caller falls back
        return None


__all__ = ["MAP_KEY", "MAX_DEPTH", "MAX_ENTRIES", "ProcInfo", "ancestors", "system_lookup",
           "record_session", "session_for_process"]
