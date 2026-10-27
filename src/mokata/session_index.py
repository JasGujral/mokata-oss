"""SESSION-INDEX (0.0.26 P33) — one durable row per Claude Code session that ran in this repo.

Before this, a session existed for the IDE only once it had a snapshot or a run claim (B-F3): a
brainstorm-only, read-only, Bash-only or fully-blocked session never appeared, an unbound claim
vanished two hours later, and the snapshot prune dropped whole old sessions. The index is the
record those sessions were missing — written by the hooks mokata already runs, independent of what
the session wrote, and NEVER pruned:

    SessionStart       a segment opens: when, `source` (startup|resume|clear|compact|fork), the
                       hook `cwd` (which follows a Claude Code worktree — P31), its branch, the
                       Claude process (pid + start token, so a killed session reads as ended), the
                       transcript path, and the session it continues (new-id resume, O10)
    UserPromptSubmit   the FIRST prompt becomes the row's label (secret-scanned, one line); every
                       later prompt only refreshes the heartbeat (the file's mtime — no bytes move)
    Stop / dirty-track the heartbeat, at HOOK time (a drained hook carries it in the env)
    SessionEnd         the durable end marker of the open segment, with Claude Code's `reason`

A `--resume` that keeps the id opens a NEW SEGMENT on the same row (R11: one row per chain).

Layout: `<main checkout>/.mokata/temp_local/sessions/<sid>.json`, one small file per session —
no shared file, so two sessions never contend, and the mtime IS the heartbeat. It is mokata's own
run-state under `temp_local/` (gitignored, like the snapshot store and the run-state store), never
the user's files; every write is tmp + `os.replace` under a per-session OS lock.

Stdlib-only (+ the secret scan on the label, which is stdlib too). Never raises into a hook.
Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Iterator, List, Optional, Tuple

SCHEMA = 1
DIRNAME = "sessions"
SUFFIX = ".json"
LABEL_CHARS = 160
#: The hook-time end of a spooled call (`hook_cli.HOOK_TIME_ENV`, pinned by a test).
HOOK_TIME_ENV = "MOKATA_HOOK_T_NS"
#: An ancestor started at least this long before the hook is the Claude process (the launch
#: shells — `bash -c`, the plugin shim — are born with the hook, within milliseconds).
CLAUDE_MIN_AGE_SECS = 0.5
#: How far up the hook's parent chain the Claude process can be (hook ← sh ← shim ← claude).
CLAUDE_MAX_DEPTH = 4
WITHHELD = "(prompt withheld — mokata's secret scan flagged it)"

_SID_SAFE = frozenset("0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-_")


# ------------------------------------------------------------------------------------- where

def index_dir(root: str) -> str:
    """`.mokata/temp_local/sessions/` of the MAIN checkout (a worktree shares it — P31)."""
    from .tdd_state import state_dir
    return os.path.join(os.path.dirname(state_dir(root)), DIRNAME)


def safe_sid(session_id: str) -> str:
    return "".join(c if c in _SID_SAFE else "_" for c in session_id)[:120] or "_"


def path_of(root: str, session_id: str) -> str:
    return os.path.join(index_dir(root), safe_sid(session_id) + SUFFIX)


def hook_time() -> float:
    """When the hook ran: the spooled hook-time stamp when drained, else now."""
    raw = os.environ.get(HOOK_TIME_ENV, "")
    try:
        return int(raw) / 1e9 if raw else time.time()
    except ValueError:
        return time.time()


# ------------------------------------------------------------------------------------- facts

def label_of(prompt: str) -> str:
    """The row label: the prompt's first non-empty line, whitespace collapsed, cut to
    `LABEL_CHARS`. A prompt the secret scan flags is never stored — not even a cut of it."""
    text = " ".join((prompt or "").split())
    if not text:
        return ""
    try:
        from .govern.secrets import scan
        if any(f.layer in ("signature", "entropy") for f in scan(prompt)):
            return WITHHELD
    except Exception:  # noqa: BLE001 — a scan that cannot run stores nothing rather than a guess
        return WITHHELD
    return text if len(text) <= LABEL_CHARS else text[:LABEL_CHARS - 1] + "…"


def branch_of(cwd: str) -> str:
    """The checked-out branch at `cwd`, read from git's own files (no subprocess): `.git` is a
    directory (main checkout) or a `gitdir:` file (a linked worktree). "" when not on a branch."""
    try:
        cur = os.path.abspath(cwd or ".")
        for _ in range(64):
            gp = os.path.join(cur, ".git")
            if os.path.isdir(gp):
                gitdir = gp
                break
            if os.path.isfile(gp):
                with open(gp, encoding="utf-8") as fh:
                    line = fh.readline().strip()
                if not line.startswith("gitdir:"):
                    return ""
                gitdir = line[7:].strip()
                if not os.path.isabs(gitdir):
                    gitdir = os.path.join(cur, gitdir)
                break
            parent = os.path.dirname(cur)
            if parent == cur:
                return ""
            cur = parent
        else:
            return ""
        with open(os.path.join(gitdir, "HEAD"), encoding="utf-8") as fh:
            head = fh.readline().strip()
        return head[16:] if head.startswith("ref: refs/heads/") else ""
    except (OSError, UnicodeDecodeError):
        return ""


def claude_process(now: Optional[float] = None) -> Optional[List[Any]]:
    """`[pid, start token]` of the Claude Code process that launched this hook: the nearest
    ancestor (within `CLAUDE_MAX_DEPTH`) born at least `CLAUDE_MIN_AGE_SECS` before the hook. None
    when it cannot be told — liveness then falls back to the heartbeat, and says so."""
    try:
        from .session_ancestry import ancestors, system_lookup
        lookup = system_lookup()
        if lookup is None:
            return None
        me = lookup(os.getpid())
        if me is None:
            return None
        for pid, info in ancestors(lookup, os.getpid(), depth=CLAUDE_MAX_DEPTH):
            if info.token and float(me.started) - float(info.started) >= CLAUDE_MIN_AGE_SECS:
                return [int(pid), str(info.token)]
    except Exception:  # noqa: BLE001 — an unknown process is a heartbeat-only row, never a raise
        return None
    return None


def process_alive(proc: Any, lookup: Any = None) -> Optional[bool]:
    """True/False when the recorded Claude process can be checked (same pid AND same start token,
    so a recycled pid is dead); None when it cannot (nothing recorded, or no process table).
    `lookup`: one `session_ancestry.system_lookup()` snapshot shared across many rows."""
    if not (isinstance(proc, list) and len(proc) == 2):
        return None
    try:
        if lookup is None:
            from .session_ancestry import system_lookup
            lookup = system_lookup()
        if lookup is None:
            return None
        info = lookup(int(proc[0]))
    except Exception:  # noqa: BLE001
        return None
    return info is not None and str(info.token) == str(proc[1])


# ------------------------------------------------------------------------------------- write

def _read_file(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("schema") == SCHEMA else None


def _write_file(path: str, rec: Dict[str, Any], t: float) -> None:
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(rec, fh, sort_keys=True)
    os.replace(tmp, path)
    os.utime(path, (t, t))


def _update(root: str, session_id: Optional[str], change, t: float) -> bool:
    """Read → `change(rec)` → write, under the session's own OS lock. `change` returns False to
    write nothing (only the heartbeat moves). Never raises."""
    if not isinstance(session_id, str) or not session_id.strip():
        return False
    sid = session_id.strip()
    try:
        from .oslock import file_lock
        path = path_of(root, sid)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with file_lock(path + ".lock"):
            rec = _read_file(path)
            fresh = rec is None
            if fresh:
                rec = {"schema": SCHEMA, "id": sid, "segments": [], "prompt": "",
                       "prompt_at": 0.0, "transcript": "", "resumed_from": ""}
            if change(rec) is False and not fresh:
                os.utime(path, (t, t))
                return False
            _write_file(path, rec, t)
            return True
    except Exception:  # noqa: BLE001 — the index is observability: a hook never fails on it
        return False


def on_start(root: str, session_id: Optional[str], source: Optional[str], cwd: Optional[str],
             transcript_path: Optional[str] = None, t: Optional[float] = None,
             proc: Any = "detect") -> bool:
    """SessionStart: open a segment (a kept-id resume opens a second one on the same row)."""
    t = hook_time() if t is None else float(t)
    src = source if isinstance(source, str) and source else "startup"
    if proc == "detect":
        proc = claude_process(t)

    def change(rec: Dict[str, Any]) -> None:
        segs = rec.setdefault("segments", [])
        for seg in segs:                       # a segment left open (killed) closes here, unknown
            if not seg.get("end"):
                seg["end"], seg["end_reason"] = 0.0, "no SessionEnd (process gone)"
        segs.append({"start": t, "source": src, "cwd": cwd or "", "branch": branch_of(cwd or ""),
                     "proc": proc, "end": None, "end_reason": ""})
        if isinstance(transcript_path, str) and transcript_path:
            rec["transcript"] = transcript_path
        if src in ("resume", "fork") and not rec.get("resumed_from"):
            rec["resumed_from"] = _predecessor(transcript_path, rec["id"])

    return _update(root, session_id, change, t)


def _predecessor(transcript_path: Optional[str], session_id: str) -> str:
    """A NEW-id resume/fork continues the newest other session its transcript names (O10)."""
    try:
        from .session_resume import predecessors
        found = predecessors(transcript_path, session_id)
    except Exception:  # noqa: BLE001
        return ""
    return found[0] if found else ""


def on_prompt(root: str, session_id: Optional[str], prompt: str,
              t: Optional[float] = None) -> bool:
    """UserPromptSubmit: the FIRST prompt labels the row; every later one only beats the heart.
    A prompt with no SessionStart before it (a hook wired mid-session) opens the row itself."""
    t = hook_time() if t is None else float(t)

    def change(rec: Dict[str, Any]) -> Optional[bool]:
        wrote = False
        if not rec.get("segments"):
            rec["segments"] = [{"start": t, "source": "unknown", "cwd": "", "branch": "",
                                "proc": claude_process(t), "end": None, "end_reason": "",
                                "note": "first seen at a prompt (no SessionStart recorded)"}]
            wrote = True
        if not rec.get("prompt"):
            label = label_of(prompt)
            if label:
                rec["prompt"], rec["prompt_at"] = label, t
                wrote = True
        return None if wrote else False

    return _update(root, session_id, change, t)


def on_end(root: str, session_id: Optional[str], reason: Optional[str] = None,
           t: Optional[float] = None) -> bool:
    """SessionEnd: the durable end marker of the open segment."""
    t = hook_time() if t is None else float(t)

    def change(rec: Dict[str, Any]) -> None:
        segs = rec.setdefault("segments", [])
        if not segs or segs[-1].get("end"):
            segs.append({"start": t, "source": "unknown", "cwd": "", "branch": "", "proc": None,
                         "end": None, "end_reason": "", "note": "an end with no recorded start"})
        segs[-1]["end"] = t
        segs[-1]["end_reason"] = reason if isinstance(reason, str) and reason else "other"

    return _update(root, session_id, change, t)


def heartbeat(root: str, session_id: Optional[str], t: Optional[float] = None) -> bool:
    """Stop / a drained write: move the row's mtime (no bytes) — only for a row that exists."""
    if not isinstance(session_id, str) or not session_id.strip():
        return False
    t = hook_time() if t is None else float(t)
    try:
        path = path_of(root, session_id.strip())
        if os.path.getmtime(path) < t:
            os.utime(path, (t, t))
        return True
    except OSError:
        return False


# ------------------------------------------------------------------------------------- read

def read(root: str, session_id: str) -> Optional[Dict[str, Any]]:
    rec = _read_file(path_of(root, session_id))
    if rec is not None:
        try:
            rec["heartbeat"] = os.path.getmtime(path_of(root, session_id))
        except OSError:
            rec["heartbeat"] = 0.0
    return rec


_CACHE: Dict[str, Tuple[int, float, Dict[str, Any]]] = {}


def read_all(root: str) -> Iterator[Dict[str, Any]]:
    """Every row, with `heartbeat` (the file's mtime). Re-reads only files whose mtime or size
    moved since the last call (the IDE asks often; 400 rows cost one `scandir`)."""
    d = index_dir(root)
    try:
        entries = list(os.scandir(d))
    except OSError:
        return
    seen = set()
    for e in entries:
        if not e.name.endswith(SUFFIX):
            continue
        try:
            st = e.stat()
        except OSError:
            continue
        key = e.path
        seen.add(key)
        hit = _CACHE.get(key)
        if hit is not None and hit[0] == st.st_size and hit[1] == st.st_mtime:
            yield hit[2]
            continue
        rec = _read_file(e.path)
        if rec is None:
            continue
        rec["heartbeat"] = st.st_mtime
        _CACHE[key] = (st.st_size, st.st_mtime, rec)
        yield rec
    for key in [k for k in _CACHE if k.startswith(d) and k not in seen]:
        _CACHE.pop(key, None)


__all__ = ["SCHEMA", "DIRNAME", "LABEL_CHARS", "WITHHELD", "HOOK_TIME_ENV", "index_dir",
           "path_of", "safe_sid", "hook_time", "label_of", "branch_of", "claude_process",
           "process_alive", "on_start", "on_prompt", "on_end", "heartbeat", "read", "read_all"]
