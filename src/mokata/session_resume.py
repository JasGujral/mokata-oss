"""IDE-RESUME-LOSES-RUN (0.0.26 O10) — a resumed conversation keeps the run it was in.

THE DEFECT (Jas 2026-10-21: "resume after develop or brainstorm both cause issues")
-----------------------------------------------------------------------------------
Every per-session fact mokata keeps — the run claim the gate polices by (`run_intent`), the run a
snapshot is attributed to, the session the IDE groups writes under — is keyed by Claude Code's
`session_id`. A resume can come back under a NEW id:

  * `--fork-session` / `/branch` / `/fork` always do (SessionStart `source: "fork"`), and older
    Claude Code builds minted a new id on every `--resume` (`source: "resume"`).

The new id held no claim, so: its writes were snapshotted with run "", it showed in the IDE as a
separate run-less session, MCP-side stage marks resolved some other run (or none), and — the
correctness half — the phase gate stopped policing the run (fail-open on two runs' state).

When the id is KEPT (current `--resume`/`--continue`), the claim survives — a bound claim never
depended on its MCP process being alive — but SessionStart's badge binding could be overwritten by
the one-live-run GUESS (another window's run), and nothing recorded that a break happened.

THE FIX — ONE identity, carried, never guessed
----------------------------------------------
On SessionStart with `source` resume/fork:

  * id KEPT and it holds a claim -> record the resume time (the IDE's marker); the claim stands.
  * new id -> read the transcript Claude Code hands the hook (`transcript_path`). Its earlier lines
    carry the `sessionId` they were written under. The NEWEST predecessor that holds a claim is
    carried (`run_intent.carry_claim`): bound -> same run; open -> the same open intent with the
    original expiry. Never "the newest run in the repo": no predecessor proven by the transcript,
    no carry. A session that already holds its own claim is never overwritten.
  * the inheritance is a `session_resumed` progress event on the run, so the run's lifecycle shows
    "resumed (from <S1>)" and the record is not only in a state file.

PARALLEL RUNS: a fork may leave the original conversation running beside its copy; both then hold
the same run, which is the run both conversations are in. Unrelated windows never share anything:
the only join is the transcript's own lines.

BOUNDED: the transcript is read only in its first and last `SCAN_BYTES` (a byte regex — a line cut by
the window boundary is simply skipped). SessionStart is the async/observability hook; every path
here returns quietly on any fault. Stdlib-only.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import re
import time
from typing import List, Optional

#: SessionStart sources that continue an earlier conversation.
RESUME_SOURCES = ("resume", "fork")

#: Bytes read from EACH end of the transcript.
SCAN_BYTES = 64 * 1024

#: At most this many distinct predecessor ids are considered (newest first).
MAX_PREDECESSORS = 16

_SID = re.compile(rb'"sessionId"\s*:\s*"([A-Za-z0-9_-]{1,128})"')


def predecessors(transcript_path: Optional[str], session_id: Optional[str],
                 scan_bytes: int = SCAN_BYTES) -> List[str]:
    """The session ids (other than `session_id`) the transcript's lines were written under, NEWEST
    FIRST (by last position in the file). Reads at most `2 * scan_bytes`; [] on anything odd."""
    if not isinstance(transcript_path, str) or not transcript_path:
        return []
    try:
        if not os.path.isfile(transcript_path):
            return []
        size = os.path.getsize(transcript_path)
        with open(transcript_path, "rb") as fh:
            head = fh.read(scan_bytes)
            tail = b""
            if size > scan_bytes:
                fh.seek(max(scan_bytes, size - scan_bytes))
                tail = fh.read(scan_bytes)
    except (OSError, ValueError):
        return []
    last: dict = {}
    for i, m in enumerate(_SID.finditer(head + b"\n" + tail)):
        sid = m.group(1).decode("ascii")
        if sid != session_id:
            last[sid] = i
    return sorted(last, key=lambda s: last[s], reverse=True)[:MAX_PREDECESSORS]


def _run_on_record(root: str, rid: str) -> bool:
    from .run_resolver import CHECKPOINT_PREFIX
    from .state import StateStore
    from .tdd_state import state_dir
    try:
        return StateStore(state_dir(root)).exists(CHECKPOINT_PREFIX + rid)
    except OSError:
        return False


def _record_event(root: str, rid: str, session_id: str, from_session: str) -> None:
    try:
        from .progress_events import record_session_resumed
        from .tdd_state import state_dir
        record_session_resumed(state_dir(root), rid, session_id, from_session)
    except (OSError, ValueError, TypeError):
        pass                               # the claim is carried; only the lifecycle mark is lost


def _carry_worktree(root: str, session_id: str, transcript_path: Optional[str]) -> bool:
    """P31 — a resumed conversation under a NEW id carries its predecessor's worktree binding (the
    newest predecessor that has one), so it picks the same code back. A kept id already owns its
    binding. Bounded: the transcript's two ends only (`predecessors`)."""
    from . import session_worktrees as SW
    if SW.binding(root, session_id):
        return False
    for pred in predecessors(transcript_path, session_id):
        if SW.carry_binding(root, session_id, pred):
            return True
    return False


def on_session_start(root: str, session_id: Optional[str], source: Optional[str],
                     transcript_path: Optional[str] = None,
                     now: Optional[float] = None) -> Optional[str]:
    """SessionStart(resume|fork): keep this conversation in its run. Returns the run the session is
    in after the resume (kept or carried), or None. Never raises."""
    try:
        if not session_id or source not in RESUME_SOURCES:
            return None
        from . import run_intent as RI
        ts = time.time() if now is None else float(now)
        _carry_worktree(root, session_id, transcript_path)
        own, rid = RI.session_claim(root, session_id, now=ts)
        if own != RI.CLAIM_NONE:                     # the id was kept: the claim is already its own
            RI.mark_resumed(root, session_id, now=ts)
            if rid:
                _record_event(root, rid, session_id, session_id)
            return rid
        for pred in predecessors(transcript_path, session_id):
            state, prid = RI.session_claim(root, pred, now=ts)
            if state == RI.CLAIM_NONE:
                continue
            if prid and not _run_on_record(root, prid):
                return None                          # the newest claim names a ghost: carry nothing
            state, carried = RI.carry_claim(root, session_id, pred, now=ts)
            if carried:
                _record_event(root, carried, session_id, pred)
            return carried
        return None
    except Exception as exc:  # noqa: BLE001 — SessionStart is async/observability: it SAYS so
        from .run_intent import _say_degraded
        _say_degraded("a resumed session was NOT joined to its run, so its edits are not "
                      "attributed to it (and with several runs here the gate falls open)", exc)
        return None


__all__ = ["RESUME_SOURCES", "SCAN_BYTES", "MAX_PREDECESSORS", "predecessors", "on_session_start"]
