"""SS.S0 — `session_save`: THE UNGATED SAVE ENTRY POINT (0.0.13 SS cluster, stage 1).

The whole save/resume stack reads state that NOTHING in production writes: the persistence
functions `brainstorm.save_brainstorm_progress`, `brainstorm.persist_approach`, and
`govern.resume.PipelineCheckpoint.mark_passed` were orphans (only tests + the playbook
self-test called them), so an interrupted brainstorm/pipeline was UNRECOVERABLE despite the
machinery to recover it already existing. This module is the missing production caller.

`save_session` snapshots the CURRENT session's full in-flight state THROUGH those functions
(it wires them as the implementation — it never re-implements their logic), so it writes
EXACTLY the keys the existing resume stack reads back:

  * `brainstorm_progress`  ← `save_brainstorm_progress` — read by `restore_brainstorm_progress`
                              (bootstrap resume-hint) and carried by `session_bundle`.
  * `approved_approach`    ← `persist_approach` (ONLY when the session is approved; ALWAYS
                              WITHOUT `plans_dir`, so the footprint stays under temp_local) —
                              read by `load_approved_approach` (completeness gate) + bundle.
  * `pipeline_run__<rid>`  ← `PipelineCheckpoint.mark_passed` — read by `resume_phase` /
                              `build_resume_hint` / bundle. `rid == current_run_id()`.

It is UNGATED by design — the binding regression guard is "save UNGATED / share GATED"
(doc 85): a local save is the user's own transient state, P2-exempt; the human gate sits at
the SHARE boundary (SS.S3/S4), not here. So there is no approve/confirm param, no WriteGate
submission, no team-journal / transport write. It is local-only and read-your-own: it returns
the keys + counts it saved, NEVER the state CONTENT.

All writes ride MS.S1's atomic + cross-process-locked `StateStore` under the MS.S2 session
scope (`surface.state`), so saving is safe mid-anything, idempotent (a second snapshot wins
atomically), and per-window isolated. A session with nothing to save returns an honest empty
result, not an error.

Stdlib-only; clean-room. Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .brainstorm import (
    APPROACH_STATE_KEY,
    BRAINSTORM_PROGRESS_KEY,
    BrainstormSession,
    load_approved_approach,
    persist_approach,
    restore_brainstorm_progress,
    save_brainstorm_progress,
)
from .govern.resume import CHECKPOINT_PREFIX, PipelineCheckpoint
from .session import current_run_id, current_session_id, short_id


@dataclass
class SaveResult:
    """What a save persisted — keys + COUNTS only, never state content (secret-safe by
    construction). `wrote` is the subset of keys THIS call actually persisted (a no-input
    `save_session` re-affirms the on-disk state and writes nothing). `empty` is honest: a
    session with no resumable state at all yields an empty (not failed) result."""

    session_id: str                                 # short id — a label, never a secret
    run_id: str
    saved: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    wrote: List[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.saved

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": True,                             # empty is honest, never an error
            "session_id": self.session_id,
            "run_id": self.run_id,
            "saved": self.saved,
            "wrote": self.wrote,
            "empty": self.empty,
        }


def _describe(store: Any, run_id: str) -> Dict[str, Dict[str, Any]]:
    """A COUNTS-ONLY view of the resumable state now present in `store` (whatever this call
    just wrote plus anything already staged). Reads through the EXISTING resume readers; emits
    integers/booleans/ids only — never a topic, question, answer, or approach text."""
    out: Dict[str, Dict[str, Any]] = {}

    bs = restore_brainstorm_progress(store)
    if bs is not None:
        out[BRAINSTORM_PROGRESS_KEY] = {
            "answered": len(bs.answered_questions),
            "questions": len(bs.questions),
            "approaches": len(bs.approaches),
            "approved": bool(bs.approved),
        }

    if load_approved_approach(store) is not None:
        out[APPROACH_STATE_KEY] = {"present": True}

    cp = PipelineCheckpoint(store, run_id)
    if cp.passed:
        out[CHECKPOINT_PREFIX + run_id] = {"run_id": run_id, "passed": len(cp.passed)}

    return out


def save_session(surface: Any, brainstorm: Optional[Dict[str, Any]] = None,
                 passed: Optional[List[str]] = None,
                 run_id: Optional[str] = None) -> SaveResult:
    """Snapshot THIS session's in-flight state — UNGATED, local-only, read-your-own.

    `brainstorm` is a `BrainstormSession.to_dict()` (the in-progress session held by the
    caller); when given it is persisted via `save_brainstorm_progress`, and — only if it is
    approved — its handoff via `persist_approach` (without `plans_dir`, so nothing lands
    outside temp_local). `passed` is the run's passed pipeline phases, each recorded via
    `PipelineCheckpoint.mark_passed`. `run_id` defaults to `current_run_id()` (== this
    session's id). With NO inputs the call re-affirms and reports the on-disk state (writing
    nothing). Returns a `SaveResult` (keys + counts, never content)."""
    store = surface.state
    rid = run_id or current_run_id()
    wrote: List[str] = []

    if brainstorm is not None:
        session = BrainstormSession.from_dict(brainstorm)
        # Orphan #1 — the in-progress session (resumable progress; not an approval).
        save_brainstorm_progress(session, store)
        wrote.append(BRAINSTORM_PROGRESS_KEY)
        # Orphan #2 — the approved approach, ONLY when the session carries an approval. No
        # `plans_dir`: the save's footprint stays entirely under temp_local (a plan FILE is a
        # separate, gated concern — never a side effect of an ungated local save).
        if session.approved and session.chosen is not None:
            persist_approach(session, store)
            wrote.append(APPROACH_STATE_KEY)

    if passed:
        # Orphan #3 — the per-gate checkpoint. `mark_passed` is idempotent (a repeated phase is
        # a no-op), so a re-save never double-records.
        cp = PipelineCheckpoint(store, rid)
        for phase in passed:
            cp.mark_passed(phase)
        wrote.append(CHECKPOINT_PREFIX + rid)

    saved = _describe(store, rid)
    return SaveResult(session_id=short_id(current_session_id()), run_id=rid,
                      saved=saved, wrote=wrote)
