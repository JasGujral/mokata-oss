"""SS.S1 — the session-aware persistence ORCHESTRATOR (0.0.13 SS cluster, stage 2).

SS.S0 built the manual save (`session_save`) but the pipeline still forgot by default: the three
formerly-orphaned persistence functions had A caller, not the RIGHT callers. This module is that
RIGHT caller — the ONE production seam that fires them AUTOMATICALLY at the pipeline's coarse
decision moments, so an approved approach / passed gate / brainstorm milestone survives a kill by
DEFAULT (P17: recovery by default, not by remembering to save). The user never calls
`session_save` by hand — the agent checkpoints through this seam at each milestone (per the
brainstorm skill's Contract), and the agent-facing `session_save` MCP tool routes THROUGH here, so
the seam is reachable from the surface the real flow hits — never a better-tested orphan.

It NEVER re-implements the persistence logic: every write rides SS.S0's `save_session`, which
wires `save_brainstorm_progress` / `persist_approach` / `PipelineCheckpoint.mark_passed` as its
implementation — MS.S1 atomic + cross-process-locked, under the MS.S2 session scope. So the
approval HARD-GATE holds unchanged (`save_session` persists the approach ONLY when the session is
approved, via `handoff()`), and the save stays UNGATED (a local save is the user's own transient
state, P2-exempt — the binding guard is "save UNGATED / share GATED").

Every moment is DEGRADE-CLEAN: a persist failure warns ONCE (secret-free, modeled on the CM.S2
once-per-subsystem notice) and returns `None` — it never blocks, fails, or raises out of the
pipeline moment itself. The moment's own semantics (the gate, the approval, the outputs) are
byte-identical but for this persistence side effect.

Coarse milestones AND per-turn: SS.S1 wired the coarse moments; SS.S2 wired `turn()` — the
per-turn autosave that fires after each answered Q&A turn (one atomic write, same seam), bounding
crash loss to ≤1 turn. `turn()` mirrors a milestone call with its own `moment` key.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import sys
from typing import Any, Callable, Dict, List, Optional, Set

from .session import current_run_id
from .session_save import SaveResult, save_session


# --------------------------------------------------------------- degrade-clean warn (once/moment)
# Process-lifetime set of MOMENT KEYS whose persist-failure notice has already been printed, so a
# failing disk warns ONCE per moment — not once per checkpoint call (a long run must not spam).
# Mirrors the CM.S2 once-per-subsystem discipline (`degrade._EMITTED`).
_WARNED: Set[str] = set()


def note_persist_failure(moment: str, out: Optional[Callable[[str], None]] = None, *,
                         seen: Optional[Set[str]] = None) -> bool:
    """Warn LOUDLY that a coarse-state persist failed — but only ONCE per `moment` this process.

    Degrade-clean by contract: the pipeline moment itself already succeeded; only its crash-safety
    checkpoint is best-effort. SECRET-SAFE BY CONSTRUCTION — the message names the MOMENT KEY (a
    controlled vocabulary: `milestone` / `approval` / `gate:<phase>` / `session_save`) and a
    generic class only; it NEVER interpolates any brainstorm state content (topic / question /
    answer / approach). Returns True when it actually printed, False when suppressed as a repeat."""
    store = _WARNED if seen is None else seen
    if moment in store:
        return False
    store.add(moment)
    emit = out or (lambda m: print(m, file=sys.stderr))
    emit(
        f"⚠ mokata: could not checkpoint '{moment}' to local state — the pipeline moment "
        f"still succeeded (your work is not blocked). Retry a save once the disk/permissions "
        f"recover.\n")
    return True


def reset_persist_warnings(seen: Optional[Set[str]] = None) -> None:
    """Clear the once-per-moment memory (test hook; a fresh process starts empty anyway)."""
    (seen if seen is not None else _WARNED).clear()


# ------------------------------------------------------------------------------- the orchestrator
class SessionFlow:
    """Drives the coarse persistence moments for ONE window's in-flight pipeline. Construct it with
    the live `Surface`; call a milestone/approval/gate method at each natural moment. Stateless
    beyond the surface — every call is an independent, idempotent, atomic snapshot through
    `save_session`."""

    def __init__(self, surface: Any, warn: Optional[Callable[[str], None]] = None) -> None:
        self.surface = surface
        self._warn = warn                      # injectable notice sink (tests); None -> stderr

    def checkpoint(self, *, brainstorm: Optional[Dict[str, Any]] = None,
                   passed: Optional[List[str]] = None, run_id: Optional[str] = None,
                   moment: str = "checkpoint") -> Optional[SaveResult]:
        """The ONE degrade-clean persist seam: snapshot whatever coarse state is given (a
        brainstorm milestone/approval and/or a passed gate) via SS.S0's `save_session`. A persist
        failure warns ONCE for `moment` (secret-free) and returns None — never blocks the moment."""
        try:
            return save_session(self.surface, brainstorm=brainstorm, passed=passed, run_id=run_id)
        except Exception:
            note_persist_failure(moment, self._warn)
            return None

    # --- (c) brainstorm milestones + (a) approval — coarse moments, NOT per-turn ----------------
    def milestone(self, brainstorm: Dict[str, Any],
                  moment: str = "milestone") -> Optional[SaveResult]:
        """A coarse brainstorm milestone (anchor set / synthesis produced / approaches generated /
        approach chosen): persist progress via `save_brainstorm_progress`, and — ONLY when the
        session carries an approval — the approach via `persist_approach` (the HARD-GATE is
        enforced inside `save_session`/`handoff()`; persistence never fabricates an approval)."""
        return self.checkpoint(brainstorm=brainstorm, moment=moment)

    def approval(self, brainstorm: Dict[str, Any]) -> Optional[SaveResult]:
        """The approval moment — an alias for `milestone` with an explicit key. The approach is
        persisted only after the gate passed (it rides `save_session`'s approved-only write)."""
        return self.milestone(brainstorm, moment="approval")

    # --- (b) live gate-pass ---------------------------------------------------------------------
    def gate_passed(self, phase: str, run_id: Optional[str] = None) -> Optional[SaveResult]:
        """The live gate-pass moment: checkpoint a passed pipeline phase via
        `PipelineCheckpoint.mark_passed`. `run_id` defaults to `current_run_id()` (MS.S2)."""
        return self.checkpoint(passed=[phase], run_id=run_id, moment="gate:" + phase)

    # --- SS.S2 — per-turn autosave, WIRED (the loss bound becomes ≤1 turn) -----------------------
    def turn(self, brainstorm: Dict[str, Any]) -> Optional[SaveResult]:
        """The per-turn autosave: persist the running brainstorm state after ONE answered Q&A turn
        — a single atomic session-scoped write via `save_session` (SS.S0 path), so a kill −9 loses
        at most the single in-flight turn (P17). Same shape as `milestone` but its own `moment` key:
        it snapshots progress (`save_brainstorm_progress`) — mid-Q&A the session is not approved, so
        no approval/gate write rides along; one write, no probe, no network. DEGRADE-CLEAN (a
        persist failure warns ONCE for 'turn', returns None, never blocks the conversation) and
        UNGATED (a local save — the user's own transient state). The next turn save retries."""
        return self.checkpoint(brainstorm=brainstorm, moment="turn")


__all__ = ["SessionFlow", "note_persist_failure", "reset_persist_warnings",
           "save_session", "current_run_id"]
