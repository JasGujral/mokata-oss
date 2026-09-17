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

import errno
import sys
from typing import Any, Callable, Dict, List, Optional, Set

from .degrade import FAILURE_INTERNAL, FAILURE_LOCAL_IO, note_degraded
from .session import current_run_id
from .session_save import SaveResult, save_session


# --------------------------------------------------------------- degrade-clean warn (once/moment)
# Process-lifetime set of MOMENT KEYS whose persist-failure notice has already been printed, so a
# failing disk warns ONCE per moment — not once per checkpoint call (a long run must not spam).
# Mirrors the CM.S2 once-per-subsystem discipline (`degrade._EMITTED`).
_WARNED: Set[str] = set()

#: EVERY failure, counted per moment — not just the first. ⛔ THE ONCE-PER-MOMENT NOTICE AND THE
#: COUNT ARE DIFFERENT FACTS AND THEY WERE THE SAME NUMBER. OSS #68 reports *"session_save was
#: degrading all session"*: one line scrolled past early and every later failure was silent, so a
#: session where the checkpoint failed a hundred times is indistinguishable, on screen, from one
#: where it failed once and recovered. Suppressing the REPEAT NOTICE is right (a long run must not
#: spam); suppressing the repeat FACT is doc 85 §7g — two different states sharing one
#: representation. `mokata doctor` reads this and says how many.
_FAILURES: Dict[str, int] = {}

#: The last secret-free failure summary per moment, for `doctor` to render after the notice has
#: scrolled away. Values are produced by `describe_persist_error` and carry NO exception text.
_LAST_ERROR: Dict[str, str] = {}


def describe_persist_error(error: Optional[BaseException]) -> str:
    """A SECRET-FREE summary of `error`: its type, plus an OSError's errno/winerror SYMBOL.

    ⛔ THE EXCEPTION'S TEXT IS NEVER INCLUDED, and that is not fastidiousness. `str(OSError)` on
    every platform interpolates the PATH it failed on, and this module's notice is bound by the
    CM.S1 rule that a degrade may not leak a directory layout. A type name is bounded, non-user
    data; `errno.errorcode[e.errno]` is a symbol from a fixed table; `e.winerror` is an integer.

    ⭐ AND THAT BOUNDED SUMMARY IS THE ENTIRE ANSWER OSS #67 NEEDED. `PermissionError (EACCES)` and
    `TypeError` route the reader to opposite places, and the old notice printed the same sentence
    for both — the disk one — so the reader tested the disk. Naming the type costs one line and
    settles which of the two stories is true.
    """
    if error is None:
        return "unknown (the failure was not captured)"
    name = type(error).__name__
    if isinstance(error, OSError):
        symbols = []
        if error.errno is not None:
            symbols.append(errno.errorcode.get(error.errno, "errno %d" % error.errno))
        win = getattr(error, "winerror", None)
        if win is not None:
            symbols.append("WinError %d" % win)
        if symbols:
            return "%s (%s)" % (name, ", ".join(symbols))
    return name


def classify_persist_error(error: Optional[BaseException]) -> str:
    """WHOSE FAULT IT IS — the one distinction the old notice could not make.

    An `OSError` means the FILESYSTEM refused the write: permissions, disk, a lock, a read-only
    mount. `FAILURE_LOCAL_IO`'s advice — check permissions and disk — is true, and that is the only
    case where it is true.

    ⛔ ANYTHING ELSE IS MOKATA'S OWN CODE RAISING, and the disk advice is then actively false. This
    is the OSS #67 case exactly: a checkpoint write that *"fails internally and is swallowed as
    degraded"* on one platform, reported to the user as a disk problem, on a machine with 670 GB
    free. `FAILURE_INTERNAL` says the opposite sentence — nothing here is yours to fix — and points
    at the issue tracker instead of at the filesystem.

    ⚠ An unclassifiable failure is NOT quietly filed as internal: `None` means the caller never
    captured the exception, which is a different fact from a captured non-OSError, and it is told
    apart in `describe_persist_error` rather than rounded off here."""
    return FAILURE_LOCAL_IO if isinstance(error, OSError) else FAILURE_INTERNAL


def persist_failures(seen: Optional[Dict[str, int]] = None) -> Dict[str, int]:
    """How many checkpoint writes failed this process, per moment. `{}` when none did."""
    return dict(_FAILURES if seen is None else seen)


def last_persist_error(moment: str) -> str:
    """The last secret-free failure summary recorded for `moment` (empty when it never failed)."""
    return _LAST_ERROR.get(moment, "")


def note_persist_failure(moment: str, out: Optional[Callable[[str], None]] = None, *,
                         seen: Optional[Set[str]] = None,
                         error: Optional[BaseException] = None) -> bool:
    """Warn LOUDLY that a coarse-state persist failed — but only ONCE per `moment` this process.

    Degrade-clean by contract: the pipeline moment itself already succeeded; only its crash-safety
    checkpoint is best-effort. SECRET-SAFE BY CONSTRUCTION — the message names the MOMENT KEY (a
    controlled vocabulary: `milestone` / `approval` / `gate:<phase>` / `session_save`), the failure
    CLASS, and the exception's TYPE; it NEVER interpolates any brainstorm state content (topic /
    question / answer / approach) and never the exception's text. Returns True when it actually
    printed, False when suppressed as a repeat.

    ⛔ WHAT THIS USED TO SAY, AND WHY IT IS GONE. The message was:

        "could not checkpoint '<moment>' to local state — the pipeline moment still succeeded
         (your work is not blocked). Retry a save once the disk/permissions recover."

    Every clause after the dash is a DIAGNOSIS THIS FUNCTION NEVER MADE. It was printed for a
    `PermissionError` and for a `TypeError` alike, because the caller's `except Exception` discarded
    the exception before this function could see it. OSS #67 and #68 are one user acting on that
    sentence — checking the disk (670 GB free), the permissions (the shell wrote to that exact
    directory), the locks (cleared), across a restart and an upgrade — and arriving unaided at the
    conclusion the notice should have handed them in its first line: *this is a defect in mokata*.

    ⭐ A DEGRADE NOTICE THAT NAMES A CAUSE IT HAS NOT MEASURED IS WORSE THAN ONE THAT NAMES NONE,
    because the reader spends their time on the hypothesis it printed. The notice now carries the
    exception's type and routes through the D5 register, so `mokata doctor` still answers *"what
    degraded this session?"* after the line has scrolled away — which is the whole reason D5 exists
    and which this bespoke `print` had quietly opted out of.
    """
    store = _WARNED if seen is None else seen
    summary = describe_persist_error(error)
    # ⚠ COUNTED AND RECORDED BEFORE THE SUPPRESSION CHECK. The notice is once per moment; the FACT
    # is every time. Doing this after the early return is how "warned once" became "failed once".
    _FAILURES[moment] = _FAILURES.get(moment, 0) + 1
    _LAST_ERROR[moment] = summary
    if moment in store:
        return False
    store.add(moment)
    emit = out or (lambda m: print(m, file=sys.stderr))
    failure_class = classify_persist_error(error)
    # ⚠ THE SUMMARY RIDES `fallback`, WHICH IS RENDERED, AND ALSO `detail`, WHICH IS NOT (YET).
    # `CapabilityDegradeNotice.render()` drops `detail` on purpose — twenty-odd sites in this tree
    # put a RAW exception there and one of them is a psycopg connection error carrying host, port
    # and user, so showing them all would break the notice's own never-the-DSN-value promise. This
    # site's summary is bounded BY CONSTRUCTION (`describe_persist_error`), so it is safe on screen
    # — and it is the entire point of the fix, so it goes where a person will actually read it.
    note_degraded(
        "checkpoint:" + moment, failure_class,
        fallback="your work is not blocked (the moment itself succeeded), but this checkpoint is "
                 "NOT on record and an interrupted run will not resume from it [%s]" % summary,
        detail=summary, out=emit)
    return True


def reset_persist_warnings(seen: Optional[Set[str]] = None) -> None:
    """Clear the once-per-moment memory (test hook; a fresh process starts empty anyway)."""
    (seen if seen is not None else _WARNED).clear()
    if seen is None:
        _FAILURES.clear()
        _LAST_ERROR.clear()


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
        except Exception as exc:
            # ⛔ THE EXCEPTION IS PASSED ON, NOT DISCARDED. `except Exception:` with a bare handler
            # threw away the one fact that identifies the failure, and the notice then guessed —
            # see `note_persist_failure`, and OSS #67/#68 for what the guess cost.
            note_persist_failure(moment, self._warn, error=exc)
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
           "classify_persist_error", "describe_persist_error", "persist_failures",
           "last_persist_error", "save_session", "current_run_id"]
