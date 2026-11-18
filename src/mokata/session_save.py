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


def failure_summary(error: BaseException) -> str:
    """`<Type> [errno N]` / `[winerror N]` — type and code only, NEVER the exception's text (it can
    carry a path or session content). Secret-safe by construction."""
    out = type(error).__name__
    for attr in ("winerror", "errno"):
        code = getattr(error, attr, None)
        if isinstance(code, int):
            return f"{out} [{attr} {code}]"
    return out


#: 0.0.29 lane G (#83) — the read-back of what is on disk, when it is the part that failed.
READ_BACK = "read_back"


def loss_message(res: "SaveResult", again: str) -> str:
    """What a save that did not fully land says: what is NOT on record, its consequence, and the
    remedy that fits the CAUSE (type + errno only, never the exception's text). `again` is the
    caller's own retry road (the MCP tool's re-call, the CLI's re-run). Shared by every reader of
    a `SaveResult` (round 2, M-2), so the CLI cannot say less than the MCP tool."""
    from .brainstorm import BrainstormGateError
    parts = []
    for key, exc in res.failed.items():
        what = ("the approach approval" if key == APPROACH_STATE_KEY else
                "what is on disk (it could not be read back)" if key == READ_BACK else f"`{key}`")
        if isinstance(exc, BrainstormGateError):
            parts.append(f"{what} was refused as sent: the session is not an approved session with a "
                         "chosen approach — fix the session (an approved, chosen approach) and save "
                         "again; a re-call with the same session fails the same way")
        elif isinstance(exc, OSError):
            parts.append(f"{what} is NOT fully on record ({failure_summary(exc)}). {again}; if it "
                         "fails again, `mokata doctor` names the state directory's problem")
        else:
            parts.append(f"{what} is NOT on record ({failure_summary(exc)}) — this is a mokata "
                         "defect, not your disk; report it")
    if res.approval_persisted is False:
        parts.append("the approach approval is NOT on record, so the approach-approval gate blocks "
                     "implementation until it lands")
    elif res.approval_persisted is True:
        keys = [f"`{k}`" for k in res.failed]
        parts.append("the approval IS on record; only " + ", ".join(keys)
                     + (" is" if len(keys) == 1 else " are")
                     + " not — no re-call is needed for the approval")
    return "; ".join(parts) + "."


@dataclass
class SaveResult:
    """What a save persisted — keys + COUNTS only, never state content (secret-safe by
    construction). `wrote` is the subset of keys THIS call actually persisted (a no-input
    `save_session` re-affirms the on-disk state and writes nothing). `empty` is honest: a
    session with no resumable state at all yields an empty (not failed) result.

    0.0.29 lane G (#83): `failed` holds every write that did NOT land (key -> the exception), so a
    lost approval is said where it was asked; `approval_persisted` is True/False when the session
    carried an approval, None when it did not."""

    session_id: str                                 # short id — a label, never a secret
    run_id: str
    saved: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    wrote: List[str] = field(default_factory=list)
    failed: Dict[str, BaseException] = field(default_factory=dict)
    approval_persisted: Optional[bool] = None

    @property
    def empty(self) -> Optional[bool]:
        """True only when what is on disk was READ and holds nothing; None (unknown) when the
        read-back failed — so no reader can take a failure for "nothing to save" (round 2, M-2)."""
        if READ_BACK in self.failed:
            return None
        return not self.saved

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "ok": not self.failed,                  # empty is honest, never an error; a loss is
            "session_id": self.session_id,
            "run_id": self.run_id,
            "saved": self.saved,
            "wrote": self.wrote,
            "empty": self.empty,                    # None: the read-back failed, so unknown
        }
        if self.failed:
            out["status"] = "degraded"
            out["failed"] = {k: failure_summary(e) for k, e in self.failed.items()}
        if self.approval_persisted is not None:
            out["approval_persisted"] = self.approval_persisted
        return out


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


def register_run(surface: Any, run_id: Optional[str] = None) -> str:
    """RUN-REG — the protocol-start state write. Ensure THIS run is REGISTERED (a
    `pipeline_run__<rid>` checkpoint exists) so a run driven conversationally cannot silently bypass
    tracking: after this, `progress` reports the run active, `spec` has a run to attach to, and
    PH-GATE.S0's phase gate has state to bind on. Returns the run id.

    Rides the EXISTING write path (`PipelineCheckpoint.ensure_registered`) — no new persistence
    logic — and is idempotent + non-destructive: a run already past its first gate keeps its
    progress. UNGATED, like every save here: registering run state is transient run-tracking under
    the MS.S2 session scope, not a durable write (P2 governs durable writes)."""
    rid = run_id or current_run_id()
    PipelineCheckpoint(surface.state, rid).ensure_registered()
    return rid


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
    failed: Dict[str, BaseException] = {}
    approval: Optional[bool] = None
    # Parsed BEFORE any write: a `brainstorm` that is not a session raises here and nothing is saved.
    session = BrainstormSession.from_dict(brainstorm) if brainstorm is not None else None

    # 0.0.29 lane G (#83) — APPROACH FIRST, then progress, then the checkpoint, each attempted on its
    # own: a write that fails is RECORDED (and said by the caller), never allowed to abort the rest.
    # The approval is the write whose loss blocks the user (the approach-approval gate), so it goes
    # first and a later failure cannot take it down with it.
    if session is not None and session.approved:
        # Orphan #2 — the approved approach, whenever the session SAYS it is approved: `handoff()`
        # (inside `persist_approach`) is the HARD-GATE and refuses an approval with no chosen
        # approach — that refusal is a lost approval too, and is said as one, never skipped in
        # silence. No `plans_dir`: the save's footprint stays under temp_local.
        try:
            persist_approach(session, store)
            wrote.append(APPROACH_STATE_KEY)
            approval = True
        except Exception as exc:  # noqa: BLE001 — recorded in `failed`, said by every caller
            failed[APPROACH_STATE_KEY] = exc
            approval = False

    if session is not None:
        # Orphan #1 — the in-progress session (resumable progress; not an approval).
        try:
            save_brainstorm_progress(session, store)
            wrote.append(BRAINSTORM_PROGRESS_KEY)
        except Exception as exc:  # noqa: BLE001 — recorded in `failed`, said by every caller
            failed[BRAINSTORM_PROGRESS_KEY] = exc

    if passed:
        # Orphan #3 — the per-gate checkpoint. `mark_passed` is idempotent (a repeated phase is
        # a no-op), so a re-save never double-records.
        key = CHECKPOINT_PREFIX + rid
        try:
            cp = PipelineCheckpoint(store, rid)
            for phase in passed:
                cp.mark_passed(phase)
            wrote.append(key)
        except Exception as exc:  # noqa: BLE001 — recorded in `failed`, said by every caller
            failed[key] = exc

    try:
        saved = _describe(store, rid)
    except Exception as exc:  # noqa: BLE001 — what is on disk is then UNKNOWN, and said so
        saved = {}
        failed[READ_BACK] = exc
    return SaveResult(session_id=short_id(current_session_id()), run_id=rid,
                      saved=saved, wrote=wrote, failed=failed, approval_persisted=approval)

