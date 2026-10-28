"""ID.S3 — the CLI↔IDE event bus: events in, live SSE out (0.0.25 Lane ID; doc 109 §4.1, §17).

ONE in-process bus per IDE server. Four sources feed it, one fan-out (the page's SSE stream) reads it:

  * PUSH, harness-neutral — `POST /api/bus` with a neutral event
    (`{"kind": "edit", "path": "src/a.py", "session": "S1"}`). Any harness, script or CLI can post.
  * PUSH, Claude Code adapter — the SAME endpoint receives Claude Code's own hook payload, sent by an
    observe-only `type: "http"` settings hook (`mokata setup claude --ide-bridge`). `from_claude_hook`
    is the adapter: it maps `hook_event_name` + `tool_input` onto the neutral kinds. Policy lives here,
    in Python — the hook config only says WHERE to post.
  * TAIL, the typed event store (`mokata.events`, 0.0.21) — new rows after the high-water `seq`.
  * TAIL, the snapshot timeline — the `dirty-track` COMMAND hook's AFTER captures. This is the
    fallback that keeps follow mode working when no http hook is wired: no extra spawn, no extra hook.

The tails are stat-gated (a `stat` per file per tick; a read only when something grew), run by ONE
watcher task per server, and only while a page is attached — the server stays light (B-01).

The bus never blocks a publisher and never grows without bound: a fixed ring; a subscriber that falls
further behind than the ring is told to resync (`changed`) instead of being queued for.

The bridge record (`temp_local/ide/bridge.json`, 0600) holds the two values an http hook has to bake
into settings: a STABLE per-repo port and a hook token. The hook token authorises `POST /api/bus` and
nothing else — it can never read the repo through the page API (least privilege, doc 109 §6).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import socket
import time
from collections import OrderedDict, deque
from typing import Any, Deque, Dict, List, Optional, Tuple


RING = 512                 # events kept for resume (Last-Event-ID) and slow subscribers
JOBS_KEPT = 64             # P32: the latest event of this many jobs (coalesced, beside the ring)
TAIL_SECS = 0.5            # the stat-gated fallback cadence (store + timeline + fingerprint)
DEDUPE_SECS = 2.0          # one edit reported by the http hook AND the timeline is one event
MAX_BODY = 8 * 1024 * 1024 # a hook payload carries the written content; bounded read
HOOK_HEADER = "x-mokata-hook"
BUS_PATH = "/api/bus"

# The neutral vocabulary. Closed: anything else posted is dropped (never echoed to a page).
EDIT, TURN_END, SESSION_START, SESSION_END, STORE, CHANGED, FOCUS, JOB = (
    "edit", "turn_end", "session_start", "session_end", "event", "changed", "focus", "job")
POSTABLE = (EDIT, TURN_END, SESSION_START, SESSION_END)

# Claude Code → neutral. The file-mutating tools are the ones `dirty-track` already watches.
_CC_FILE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
_CC_EVENT_KIND = {"Stop": TURN_END, "SessionStart": SESSION_START, "SessionEnd": SESSION_END}

# Stable-port range for the bridge: below the Linux/macOS/Windows ephemeral ranges, so an outgoing
# connection never squats on it by chance.
PORT_LO, PORT_SPAN = 20000, 10000


# ------------------------------------------------------------------------------ the bridge record

def bridge_path(root: str) -> str:
    from .statedir import ide_dir
    return os.path.join(ide_dir(root), "bridge.json")


def read_bridge(root: str) -> Optional[Dict[str, Any]]:
    try:
        with open(bridge_path(root), encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or not isinstance(d.get("token"), str) or not d.get("token"):
        return None
    try:
        port = int(d.get("port") or 0)
    except (TypeError, ValueError):
        return None
    return {"port": port, "token": d["token"]} if 0 < port < 65536 else None


def port_free(port: int) -> bool:
    """Can a listener bind 127.0.0.1:`port` right now? SO_REUSEADDR on POSIX only, so a port in
    TIME_WAIT from the previous server counts as free (uvicorn sets it too); on Windows that option
    would let a bind STEAL a live port, so it is not set there."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if os.name != "nt":
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _pick_port(root: str) -> int:
    start = int(hashlib.sha256(os.path.realpath(root).encode("utf-8")).hexdigest()[:8], 16)
    for i in range(64):
        port = PORT_LO + (start + i * 7919) % PORT_SPAN
        if port_free(port):
            return port
    raise OSError("no free port for the IDE bridge in 20000-29999")


def ensure_bridge(root: str) -> Dict[str, Any]:
    """The repo's bridge record, created once (port + hook token) and then STABLE — the http hooks
    in `.claude/settings.local.json` carry both, so they must not move between launches."""
    rec = read_bridge(root)
    if rec:
        return rec
    rec = {"port": _pick_port(root), "token": secrets.token_urlsafe(24)}
    path = bridge_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + f".tmp-{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(rec, fh)
    os.replace(tmp, path)
    return rec


def preferred_port(root: str) -> int:
    """The bridge port when this repo has one and it is free; 0 (= pick any) otherwise."""
    rec = read_bridge(root)
    if rec and port_free(int(rec["port"])):
        return int(rec["port"])
    return 0


def bridge_url(port: int) -> str:
    return f"http://127.0.0.1:{port}{BUS_PATH}"


# ------------------------------------------------------------------------------ normalisation

def _session(v: Any) -> str:
    return v[:120] if isinstance(v, str) else ""


def _contained(root: str, path: Any, cwd: Any) -> Optional[str]:
    if not isinstance(path, str) or not path or len(path) > 4096:
        return None
    from .snapshots import rel_path
    return rel_path(root, path, cwd if isinstance(cwd, str) and cwd else None)


def from_claude_hook(payload: Dict[str, Any], root: str) -> Optional[Dict[str, Any]]:
    """The Claude Code ADAPTER: one hook payload → one neutral event, or None (not ours to show).

    Only observe-only events are mapped; a PreToolUse payload is never turned into anything (gates
    stay command hooks — ruling U8). A file edit outside the repo has no path here and is dropped."""
    name = payload.get("hook_event_name")
    sid = _session(payload.get("session_id"))
    if name == "PostToolUse":
        tool = payload.get("tool_name")
        if tool not in _CC_FILE_TOOLS:
            return None
        from ..gate_hook import target_path
        rel = _contained(root, target_path(payload.get("tool_input")), payload.get("cwd"))
        if not rel:
            return None
        return {"kind": EDIT, "path": rel, "session": sid, "tool": tool, "source": "claude-code"}
    kind = _CC_EVENT_KIND.get(name) if isinstance(name, str) else None
    if kind:
        return {"kind": kind, "session": sid, "source": "claude-code"}
    return None


def from_neutral(payload: Dict[str, Any], root: str) -> Optional[Dict[str, Any]]:
    """The harness-neutral shape: `{"kind": <POSTABLE>, "session"?, "path"? (edit), "tool"?}`."""
    kind = payload.get("kind")
    if kind not in POSTABLE:
        return None
    out: Dict[str, Any] = {"kind": kind, "session": _session(payload.get("session")),
                           "source": "neutral"}
    tool = payload.get("tool")
    if isinstance(tool, str) and tool:
        out["tool"] = tool[:60]
    if kind == EDIT:
        rel = _contained(root, payload.get("path"), None)
        if not rel:
            return None
        out["path"] = rel
    return out


def normalise(payload: Any, root: str) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None
    if "hook_event_name" in payload:
        return from_claude_hook(payload, root)
    return from_neutral(payload, root)


# ------------------------------------------------------------------------------ the bus

class EventBus:
    """A bounded ring of numbered events with an async wait. Single event loop (uvicorn's)."""

    def __init__(self, cap: int = RING):
        self._ring: Deque[Dict[str, Any]] = deque(maxlen=cap)
        # 0.0.26 P32 — `job` events are COALESCED: only the latest per job id is kept, beside the
        # ring, so a 9-minute build's progress ticks can never push session events off it.
        self._jobs: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._dropped = 0                  # the id of the newest event that fell off the ring
        self.seq = 0
        self._cond: Optional[asyncio.Condition] = None
        self._recent: Dict[Tuple[str, str], float] = {}

    def _condition(self) -> asyncio.Condition:
        if self._cond is None:
            self._cond = asyncio.Condition()
        return self._cond

    def _duplicate(self, evt: Dict[str, Any], now: float) -> bool:
        if evt.get("kind") != EDIT:
            return False
        key = (str(evt.get("session") or ""), str(evt.get("path") or ""))
        prev = self._recent.get(key)
        self._recent[key] = now
        if len(self._recent) > 4 * RING:
            cut = now - DEDUPE_SECS
            self._recent = {k: t for k, t in self._recent.items() if t >= cut}
        return prev is not None and now - prev < DEDUPE_SECS

    async def publish(self, evt: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        now = time.time()
        if self._duplicate(evt, now):
            return None
        self.seq += 1
        out = dict(evt, id=self.seq, ts=now)
        if evt.get("kind") == JOB:
            jid = str((evt.get("job") or {}).get("id") or "")
            self._jobs.pop(jid, None)
            self._jobs[jid] = out
            while len(self._jobs) > JOBS_KEPT:
                self._jobs.popitem(last=False)
        else:
            if len(self._ring) == self._ring.maxlen:
                self._dropped = self._ring[0]["id"]
            self._ring.append(out)
        cond = self._condition()
        async with cond:
            cond.notify_all()
        return out

    def recent(self) -> List[Dict[str, Any]]:
        """A copy of the ring, oldest first (call on the loop: the copy is what crosses threads)."""
        return list(self._ring)

    def job_events(self) -> List[Dict[str, Any]]:
        """The latest `job` event of every job the bus still keeps (<= JOBS_KEPT), oldest first.
        A page's FIRST stream opens at the bus's current seq, so a job that started — or even
        finished — before the stream connected would never reach it (0.0.26 O9: a job started at
        page load stayed "queued" in the Activity drawer forever). The stream sends these first."""
        return sorted(self._jobs.values(), key=lambda e: e["id"])

    def after(self, seq: int) -> Tuple[List[Dict[str, Any]], bool]:
        """Events after `seq`, oldest first, and whether the caller MISSED some (fell off the ring)."""
        if seq >= self.seq:
            return [], False
        # Missed = a RING event this subscriber had not seen fell off the ring. (Ids spent by
        # coalesced job events were never on the ring, so a gap in ids is not a loss.)
        missed = seq < self._dropped
        ring = [e for e in self._ring if e["id"] > seq]
        jobs = [e for e in self._jobs.values() if e["id"] > seq]
        if not jobs:
            return ring, missed
        return sorted(ring + jobs, key=lambda e: e["id"]), missed

    async def wait_after(self, seq: int, timeout: float) -> Tuple[List[Dict[str, Any]], bool]:
        evts, missed = self.after(seq)
        if evts or missed:
            return evts, missed
        cond = self._condition()
        try:
            async with cond:
                await asyncio.wait_for(cond.wait_for(lambda: self.seq > seq), timeout)
        except asyncio.TimeoutError:
            return [], False
        return self.after(seq)


# ------------------------------------------------------------------------------ the tails

class Tails:
    """The stat-gated fallback readers: the typed event store and the snapshot timeline."""

    def __init__(self, root: str):
        self.root = root
        self.store_sig: Optional[Tuple] = None
        self.store_seq: Optional[int] = None
        self.tl_off: Optional[int] = None

    def _store_paths(self) -> Tuple[str, str]:
        from ..events import events_path
        p = events_path(self.root)
        return p, p + "-wal"

    def _sig(self, paths) -> Tuple:
        out = []
        for p in paths:
            try:
                st = os.stat(p)
                out.append((st.st_size, st.st_mtime_ns))
            except OSError:
                out.append(None)
        return tuple(out)

    def prime(self) -> None:
        """Start at NOW: a page that attaches is shown what happens next, not the store's history
        (it loads history through the read API)."""
        from ..events import store_for_root
        self.store_sig = self._sig(self._store_paths())
        self.store_seq = store_for_root(self.root).max_seq() or 0
        try:
            from .snapshots import _timeline_path
            self.tl_off = os.path.getsize(_timeline_path(self.root))
        except OSError:
            self.tl_off = 0

    def store_events(self) -> List[Dict[str, Any]]:
        sig = self._sig(self._store_paths())
        if sig == self.store_sig:
            return []
        self.store_sig = sig
        from ..events import store_for_root
        rows = store_for_root(self.root).after_seq(self.store_seq or 0, limit=200)
        out = []
        for r in rows:
            self.store_seq = max(self.store_seq or 0, r.seq)
            out.append({"kind": STORE, "type": r.type, "session": r.session_id, "seq": r.seq,
                        "data": r.data, "source": "store"})
        return out

    def timeline_edits(self) -> List[Dict[str, Any]]:
        from .snapshots import AFTER, _timeline_path
        path = _timeline_path(self.root)
        try:
            size = os.path.getsize(path)
        except OSError:
            return []
        off = self.tl_off or 0
        if size < off:                       # pruned/rewritten: resume at the new end, no replay
            self.tl_off = size
            return []
        if size == off:
            return []
        try:
            with open(path, "rb") as fh:
                fh.seek(off)
                chunk = fh.read(min(size - off, 1024 * 1024))
        except OSError:
            return []
        end = chunk.rfind(b"\n")
        if end < 0:
            return []                        # a line still being written — next tick
        self.tl_off = off + end + 1
        out = []
        for line in chunk[:end].splitlines():
            try:
                d = json.loads(line.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(d, dict) and d.get("kind") == AFTER and isinstance(d.get("path"), str):
                out.append({"kind": EDIT, "path": d["path"], "session": _session(d.get("session")),
                            "source": "timeline"})
        return out


async def read_body(request: Any, limit: Optional[int] = None) -> Optional[bytes]:
    """The request body, or None when it is larger than `limit` (read stops there)."""
    limit = MAX_BODY if limit is None else limit
    buf = bytearray()
    async for chunk in request.stream():
        buf.extend(chunk)
        if len(buf) > limit:
            return None
    return bytes(buf)


__all__ = ["EventBus", "Tails", "normalise", "from_claude_hook", "from_neutral", "ensure_bridge",
           "read_bridge", "preferred_port", "bridge_url", "bridge_path", "port_free", "POSTABLE",
           "HOOK_HEADER", "BUS_PATH", "TAIL_SECS"]
