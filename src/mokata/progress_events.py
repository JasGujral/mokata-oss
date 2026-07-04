"""Stage 6b — a minimal, EVENT-STREAM-SHAPED pipeline progress log.

An append-only JSONL record of the user-stage transitions a run passes through
(brainstorm → spec → develop → review → ship). It exists so the always-on stage badge
(Stage 54b `build_stage_badge`) can tell `develop` / `review` / `ship` apart — three
separate skills with no shared pipeline checkpoint, indistinguishable from the run-state
alone (the old "spec emitted → always develop" collapse).

Trust tier: OBSERVABILITY. Like the audit ledger (`govern/ledger.py`), this is append-only
and UNGATED — it is NOT a P2 durable code/memory/config write, so it never routes through
the WriteGate/human-gate. It mirrors the ledger's append/read pattern deliberately: one
persistence mechanism, not a second invention.

Event envelope — a COMPATIBLE SUBSET of 0.1.0's R1.S1a event stream, so R1.S1a can absorb
this log as a superset rather than a second store to migrate:

    {event_id, ts, schema_version, type, stage, run_id, data}

`type` ∈ {stage_enter, stage_pass, review_verdict}. These map conceptually onto R1.S1a's
`PhaseTransition` (enter/pass) and verdict events. `run_id` reuses the EXISTING pipeline /
session_bundle run identity; R1.S1a introduces the fuller `session_id` (uuid-per-session) —
this field is the forward-compatible hook it will bind to.

Degrade-clean (P11): an absent / corrupt / unreadable log reads as EMPTY, never raises —
the badge falls back to its checkpoint-derived default.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# The log lives beside the pipeline run-state (StateStore.root =
# .mokata/temp_local/state/), so a reader that already holds `surface.state` finds it
# without new plumbing. It is transient runtime data, gitignored like the state store.
PROGRESS_EVENTS_FILENAME = "progress-events.jsonl"

# Bump ONLY on an incompatible envelope change; R1.S1a keys off this to migrate the log.
PROGRESS_SCHEMA_VERSION = 1

# The event types this minimal log records. A strict subset of R1.S1a's vocabulary.
STAGE_ENTER = "stage_enter"
STAGE_PASS = "stage_pass"
REVIEW_VERDICT = "review_verdict"
PROGRESS_EVENT_TYPES = (STAGE_ENTER, STAGE_PASS, REVIEW_VERDICT)

# The envelope keys every entry carries — the R1.S1a-compatible subset the drift/subset
# test asserts against (so the two schemas can never silently diverge).
ENVELOPE_KEYS = ("event_id", "ts", "schema_version", "type", "stage", "run_id", "data")

# How many trailing events a bounded read returns by default (frugal, P11 — a tail, not
# the whole history; the badge only needs the most recent transitions).
DEFAULT_TAIL = 200

# One process-wide lock serialises read-len-then-append across threads, exactly like the
# audit ledger: a POSIX text-mode append is atomic (O_APPEND) but Windows concurrent
# appends can clobber, so the lock keeps the append-only log correct on every OS.
_APPEND_LOCK = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProgressLog:
    """Append-only progress-event log — the observability twin of `AuditLedger`."""

    def __init__(self, path: str) -> None:
        self.path = path

    @classmethod
    def from_state_dir(cls, state_root: str) -> "ProgressLog":
        """The log inside a StateStore's directory (`surface.state.root`)."""
        return cls(os.path.join(state_root, PROGRESS_EVENTS_FILENAME))

    @classmethod
    def from_surface(cls, surface: Any) -> "ProgressLog":
        """The log for a loaded Surface (reads/writes under .mokata/temp_local/state/)."""
        return cls.from_state_dir(surface.state.root)

    def append_event(self, type: str, stage: str, run_id: Optional[str] = None,
                     data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Append one R1.S1a-shaped event and return it. Never rewrites existing entries;
        the read-then-append is locked so concurrent writers never drop one. UNGATED —
        this writes straight to the JSONL, never through the WriteGate (observability)."""
        entry: Dict[str, Any] = {
            "event_id": uuid.uuid4().hex,
            "ts": _now_iso(),
            "schema_version": PROGRESS_SCHEMA_VERSION,
            "type": type,
            "stage": stage,
            "run_id": run_id,
            "data": dict(data) if data else {},
        }
        with _APPEND_LOCK:
            parent = os.path.dirname(self.path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        return entry

    def read_events(self, tail: Optional[int] = DEFAULT_TAIL) -> List[Dict[str, Any]]:
        """The last `tail` well-formed events (all of them when `tail` is falsy). Bounded
        + degrade-clean: an absent file → []; a corrupt / half-written line is SKIPPED, so
        a truncated log never raises into the read-only badge/progress hot paths."""
        if not os.path.exists(self.path):
            return []
        out: List[Dict[str, Any]] = []
        try:
            with open(self.path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue                 # skip a corrupt/partial line, don't raise
                    if isinstance(obj, dict):
                        out.append(obj)
        except OSError:
            return []
        if tail and tail > 0:
            return out[-tail:]
        return out


# ============================================================ Stage 6r — the review verdict
#
# The closing review's verdict is PERSISTED here as a `review_verdict` event — the SAME
# append-only, event-stream-shaped log, NOT a second store (the 6b↔6r unification the
# re-audit asked for). `/mokata:ship` reads the persisted record instead of trusting the
# conversation: evidence over vibes, applied to review itself. The event data carries:
#     {passed: bool, independent: bool, findings?: int|str}
# `independent` is True when the review ran as a fresh-context subagent (re-derived from a
# self-contained brief), False when it degraded to the inline two-pass (no subagents / the
# user opted the isolation off). Ship surfaces the difference; it never hard-blocks on inline.


# Sentinel default for `record_review_verdict(run_id=...)`: distinguishes an OMITTED run_id
# (bind to the active run — the common case) from an EXPLICIT `run_id=None` (record a truly
# run-less verdict, e.g. a standalone review outside any pipeline run). Needed by the 6r-gate
# fix (doc-49 #3): a run-less verdict must never silently become the active run's evidence.
_CURRENT_RUN: Any = object()


def record_review_verdict(surface: Any, *, passed: bool, independent: bool,
                          findings: Optional[Any] = None,
                          run_id: Any = _CURRENT_RUN) -> Dict[str, Any]:
    """Append the closing review's verdict as a `review_verdict` event and return it.

    Observability tier, exactly like `stage_enter`: append-only + UNGATED (it writes no
    durable code/memory/config, so it never routes through the WriteGate/human-gate). When
    `run_id` is OMITTED, the current pipeline run identity is reused so the verdict binds to
    the same run the badge tracks; passing `run_id=None` EXPLICITLY records a run-less
    verdict (which, per the strict 6r gate, satisfies only a run-less ship check)."""
    if run_id is _CURRENT_RUN:
        try:
            from .progress import find_active_run
            run_id = find_active_run(surface.state)
        except Exception:
            run_id = None
    data: Dict[str, Any] = {"passed": bool(passed), "independent": bool(independent)}
    if findings is not None:
        data["findings"] = findings
    return ProgressLog.from_surface(surface).append_event(
        REVIEW_VERDICT, "review", run_id=run_id, data=data)


def latest_review_verdict(surface: Any,
                          run_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The most recent `review_verdict` event's data (`{passed, independent, findings?}`),
    or None when none was recorded. STRICTLY run-scoped (6r-gate fix, doc-49 #3): with a
    `run_id`, ONLY that run's verdicts count — a verdict recorded with `run_id: None`
    (outside any run) does NOT satisfy a real run, so neither a prior run's verdict nor a
    run-less one can leak into a fresh run's ship gate. With `run_id=None` (no active run)
    every verdict is considered, as before. Degrade-clean: an absent/corrupt/unreadable
    log -> None (ship then blocks as if no review ran — fail-closed, evidence over vibes)."""
    try:
        events = ProgressLog.from_surface(surface).read_events()
    except Exception:
        return None
    found: Optional[Dict[str, Any]] = None
    for e in events:
        if e.get("type") != REVIEW_VERDICT:
            continue
        if run_id is not None and e.get("run_id") != run_id:
            continue
        data = e.get("data")
        if isinstance(data, dict):
            found = data                # forward-appended; the last match is the current one
    return found


# settings.review.independent = on | off  (default "on") — mirrors settings.brainstorm.auto.
# `on` (default) runs the closing review as a fresh-context subagent; `off` restores the old
# inline two-pass for users who want it. Degrade-clean: an absent/broken/unrecognised value
# reads as `on`, so the stronger, independent review is never silently lost.
REVIEW_INDEPENDENT_ON, REVIEW_INDEPENDENT_OFF = "on", "off"


def review_independent_mode(surface: Any) -> str:
    """The saved `settings.review.independent` preference (Stage 6r). Default `on`."""
    try:
        s = surface.manifest.setting("review", {}) or {}
        v = s.get("independent", REVIEW_INDEPENDENT_ON) if isinstance(s, dict) else \
            REVIEW_INDEPENDENT_ON
        return REVIEW_INDEPENDENT_OFF if v == REVIEW_INDEPENDENT_OFF else REVIEW_INDEPENDENT_ON
    except Exception:
        return REVIEW_INDEPENDENT_ON


@dataclass(frozen=True)
class ReviewGate:
    """Ship's read of the persisted review verdict — evidence, not conversation context.

    `blocks` is True when ship must STOP (no verdict recorded, or the review FAILED). An
    inline (non-independent) PASS never blocks — capability-degraded harnesses must still
    ship — but `independent` is False so ship makes the difference visible and logs it."""

    present: bool          # a verdict was recorded at all
    passed: bool           # the review passed
    independent: bool      # ran as a fresh-context subagent (vs inline two-pass)
    blocks: bool           # ship must STOP
    message: str           # the one line ship surfaces
    unblock: str = ""      # the single action that clears a block


def ship_review_gate(surface: Any, run_id: Optional[str] = None) -> ReviewGate:
    """Derive ship's review gate from the PERSISTED verdict (Stage 6r).

    No verdict -> BLOCK (`review hasn't run — run /mokata:review first`). A failed verdict ->
    BLOCK. A passed verdict -> proceed, surfacing whether it was independent (`review passed
    (independent ✓)`) or inline (`review passed (inline — not independent)`). Ship does NOT
    hard-block on inline; it surfaces + logs the weaker signal."""
    if run_id is None:
        try:
            from .progress import find_active_run
            run_id = find_active_run(surface.state)
        except Exception:
            run_id = None
    v = latest_review_verdict(surface, run_id=run_id)
    if v is None:
        return ReviewGate(present=False, passed=False, independent=False, blocks=True,
                          message="review hasn't run — run /mokata:review first",
                          unblock="run /mokata:review first")
    passed = bool(v.get("passed"))
    independent = bool(v.get("independent"))
    if not passed:
        return ReviewGate(present=True, passed=False, independent=independent, blocks=True,
                          message="review failed — findings are unresolved",
                          unblock="run /mokata:review and address its findings")
    if independent:
        return ReviewGate(present=True, passed=True, independent=True, blocks=False,
                          message="review passed (independent ✓)")
    return ReviewGate(present=True, passed=True, independent=False, blocks=False,
                      message="review passed (inline — not independent)")
