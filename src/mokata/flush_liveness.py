"""CM.S4 — FLUSH LIVENESS: bounded retry/backoff + a surfaced pending count (C-4).

The bug this closes (doc 48 finding-3 follow-through): a failed flush was silent and passive. An
unhealthy connection left the approved write PENDING in the journal and simply waited for some
future incidental touchpoint to retry — nothing counted or reported the backlog, so a team could
accumulate approved-but-unflushed writes indefinitely (C-4) with ZERO signal. The user had no way
to see "3 approved writes are still local-only."

This module makes the flush LIVE and OBSERVABLE without adding any infrastructure:

  * **Bounded retry with backoff (no daemon).** Retries piggyback on the SAME existing journal/
    store touchpoints that already call the flush (E2 discipline — no background thread, timer, or
    process). After a skipped flush the backlog is scheduled for retry with exponential backoff +
    jitter, capped, with a per-process retry cap. Within the backoff window a touchpoint does NOT
    re-attempt (the hot path stays non-blocking and never hammers a down DB); once the window
    elapses the next touchpoint retries. A successful drain resets the machinery.

  * **Pending count surfaced.** `TeamJournal.pending_count()` feeds one `PendingStatus` snapshot
    (count + oldest-backlog age + last-failure class, reusing CM.S2's failure-class vocabulary)
    that the statusline badge, `mokata doctor`, and the MCP read responses all render — the SAME
    count everywhere, and the SAME routing verdict CM.S2 uses (no second probe), so the degraded
    read notice and the pending count can never disagree.

Reuse, not re-derivation: the failure class comes from the SAME cached E2 health verdict the flush
and `degrade.resolve_read_routing` already compute (`degrade.failure_class`) — one routing input.
The liveness state is a small best-effort file under gitignored `temp_local/` — it is NOT the
journal (no journal schema change): the durable write records are untouched (C-5 approval-id still
rides the existing journal fields). Local mode never engages any of this — zero-config stays
byte-identical, with no pending concept surfaced at all.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import random  # non-security: retry-backoff jitter only (never a token/secret/decision)
from dataclasses import dataclass
from typing import Any, Callable, Optional

from . import TEMP_LOCAL_DIRNAME, run_mode as _rm
from .atomicfile import atomic_write_text, lock_path_for
from .degrade import FAILURE_LOCAL_IO, note_degraded
from .oslock import file_lock

# The backoff schedule for a down connection. Exponential from BASE, capped at MAX, plus jitter so
# many processes don't retry in lock-step. RETRY_CAP bounds auto-retries PER PROCESS: past it the
# backlog stops auto-retrying (it waits for a healthy drain or a manual `mokata sync`, which
# bypasses this gate) — never a silent hot loop against a dead DB.
BACKOFF_BASE_S = 2.0
BACKOFF_MAX_S = 300.0
RETRY_CAP = 8
STATE_FILENAME = "team_flush_liveness.json"


@dataclass
class LivenessState:
    """The small, best-effort retry/backoff record for the current unflushed backlog. Persisted
    beside the health cache (NOT in the journal — no journal schema change). `backlog_since` is
    when this backlog first failed to flush (the oldest-pending age proxy); it is cleared the
    moment the backlog drains."""

    attempts: int = 0
    next_retry_at: float = 0.0
    last_failure_class: str = ""
    backlog_since: float = 0.0

    @property
    def capped(self) -> bool:
        return self.attempts >= RETRY_CAP

    def to_dict(self) -> dict:
        return {"attempts": self.attempts, "next_retry_at": self.next_retry_at,
                "last_failure_class": self.last_failure_class,
                "backlog_since": self.backlog_since}

    @classmethod
    def from_dict(cls, d: dict) -> "LivenessState":
        return cls(attempts=int(d.get("attempts", 0) or 0),
                   next_retry_at=float(d.get("next_retry_at", 0.0) or 0.0),
                   last_failure_class=str(d.get("last_failure_class", "") or ""),
                   backlog_since=float(d.get("backlog_since", 0.0) or 0.0))


# ------------------------------------------------------------------------------ state IO
def _state_path(surface: Any) -> Optional[str]:
    try:
        return os.path.join(surface.mokata_dir, TEMP_LOCAL_DIRNAME, STATE_FILENAME)
    except AttributeError:  # pragma: no cover - a surface with no `mokata_dir` has no path
        return None


def load_state(surface: Any) -> LivenessState:
    """The persisted liveness state, or a fresh (empty) one. Degrade-clean: a missing/corrupt file
    reads as a fresh state (never raises — liveness is observability, not correctness)."""
    path = _state_path(surface)
    if not path or not os.path.exists(path):
        return LivenessState()
    try:
        with open(path, encoding="utf-8") as fh:
            return LivenessState.from_dict(json.load(fh))
    except Exception:
        return LivenessState()


def store_state(surface: Any, state: LivenessState) -> None:
    """Persist the liveness state ATOMICALLY (MS.S6). Best-effort — a write failure never breaks
    the flush. The atomic replace closes the torn-read window: the badge and `mokata doctor` run in
    SEPARATE processes and read this file while a flush may be writing it, and a half-written file
    read as "no backlog" would hide the very thing this state exists to surface."""
    path = _state_path(surface)
    if not path:
        return
    try:
        atomic_write_text(path, json.dumps(state.to_dict()))
    except Exception:  # pragma: no cover - best-effort persistence
        pass


def update_state(surface: Any, mutator: Callable[[LivenessState], LivenessState]) -> LivenessState:
    """Locked read-modify-write of the liveness state (MS.S6 / M-6): the cross-process `oslock` is
    held across load → `mutator` → atomic replace, so two windows escalating the backlog can't lose
    an update.

    They previously could: a flush loaded the state, attempted, and wrote back a mutated copy. Two
    windows both reading `attempts=1` and both writing `attempts=2` lost an attempt — the retry cap
    (the ONLY thing bounding retries against a dead DB) then took longer to reach, or with enough
    interleaving was never reached at all, which is precisely the silent hammering RETRY_CAP exists
    to prevent. Best-effort like the rest of this module: any failure returns the mutated state
    unpersisted rather than breaking the flush."""
    path = _state_path(surface)
    if not path:
        return mutator(LivenessState())
    try:
        with file_lock(lock_path_for(path)):
            state = mutator(load_state(surface))
            atomic_write_text(path, json.dumps(state.to_dict()))
            return state
    except Exception:  # pragma: no cover - best-effort persistence
        return load_state(surface)


def clear_state(surface: Any) -> None:
    """Drop the liveness state (a clean, fully-drained backlog engages no machinery)."""
    path = _state_path(surface)
    if not path:
        return
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:  # pragma: no cover - best-effort
        # LEGITIMATE SUPPRESS: this deletes a purely OBSERVABILITY file whose absence IS the desired
        # end state ("no backlog → no machinery"). A stale one costs at most one extra backoff read;
        # nothing is lost and there is nothing to announce, so a failure to unlink is genuinely
        # nothing to say — and a notice here would be noise on the drained/HAPPY path.
        pass


def _backoff(attempts: int, jitter: Callable[[], float]) -> float:
    """Exponential backoff (from attempt #1) capped at BACKOFF_MAX_S, plus a small jitter."""
    base = min(BACKOFF_MAX_S, BACKOFF_BASE_S * (2 ** max(0, attempts - 1)))
    try:
        j = float(jitter())
    except (TypeError, ValueError):  # pragma: no cover - a jitter that isn't a number
        j = 0.0
    return base + max(0.0, j)


# ------------------------------------------------------------------------------ the gated flush
def flush_with_liveness(surface: Any, *, ledger: Any = None, environ: Optional[dict] = None,
                        health: Any = None, connect: Optional[Callable[..., Any]] = None,
                        probe: Optional[Callable[[str], Any]] = None,
                        scan: Optional[Callable[[Any], list]] = None,
                        now: Optional[Callable[[], float]] = None,
                        jitter: Optional[Callable[[], float]] = None,
                        out: Optional[Callable[[str], None]] = None):
    """The liveness-aware auto-flush the store touchpoints call (replacing a bare `flush`).

    NEVER blocks, NEVER raises, and NEVER runs a background thread — a retry rides THIS call only.
    Local mode is a passthrough (byte-identical — no journal, no state). In team mode: if the
    backlog is empty, any stale liveness state is cleared and the flush no-ops. If it is within the
    backoff window (or at the retry cap), the call SKIPS without attempting (no connect, no probe).
    Otherwise it attempts the flush: a drain (non-skipped) clears the machinery, a skip records the
    failure class (from the SAME verdict, via `degrade.failure_class`) and schedules the next
    backoff. MS.S5: a skip because ANOTHER PROCESS holds the single-flusher mutex is `contended`,
    not a failure — it engages no backoff at all (see below). `connect`/`health`/`probe`/`scan`/
    `now`/`jitter` are injectable for tests."""
    from . import team_journal
    import time as _time
    clock = now or _time.time
    rnd = jitter or (lambda: random.uniform(0.0, BACKOFF_BASE_S))

    def _flush():
        return team_journal.flush(surface, environ=environ, health=health, probe=probe,
                                  connect=connect, ledger=ledger, scan=scan, out=out)

    # Local / zero-config: no team write path → pure passthrough (byte-identical to today).
    # `read_mode` is fail-closed BY CONTRACT ("Never raises" — it swallows a broken surface
    # internally and returns LOCAL), so the try/except that used to wrap this was dead code that
    # could only ever have hidden a bug in this module.
    if _rm.read_mode(surface) != _rm.TEAM:
        return _flush()

    journal = team_journal.TeamJournal.for_surface(surface)
    if journal.pending_count() == 0:
        clear_state(surface)            # nothing pending → no machinery engaged
        return _flush()                 # returns "nothing to flush" (byte-identical)

    state = load_state(surface)
    t = clock()

    # Backoff gate — do NOT re-attempt within the window or once the retry cap is hit. This is the
    # non-blocking, no-hammer promise: no connect/probe happens here.
    if state.capped or t < state.next_retry_at:
        return team_journal.FlushResult(
            flushed=0, pending=journal.pending_count(), skipped=True,
            reason=("retry backoff in effect — journaled locally, will retry on a later "
                    "touchpoint (work-locally, nothing lost)"))

    result = _flush()

    if not result.skipped:
        # attempted + processed the backlog (drained to flushed/conflict/blocked) → reset.
        clear_state(surface)
        return result

    if getattr(result, "contended", False):
        # MS.S5 — SKIPPED BECAUSE ANOTHER WINDOW IS FLUSHING, which is not a failure: the connection
        # was never tried and the holder is draining this same journal. Escalating backoff here would
        # be a lie twice over — it would blame a healthy DB for a failure that never happened, and it
        # would push out the retry of a backlog that is at this moment being drained by someone else.
        # So: no attempt counted, no backoff scheduled, no failure class recorded, and the pending
        # count (read live off the journal) stays honest.
        return result

    # a skipped flush = the connection was not healthy → schedule the next bounded retry. The
    # escalation is a LOCKED RMW against the CURRENT persisted state (MS.S6): `state` above was read
    # before the flush attempt, and a sibling window may have escalated in the meantime — counting
    # the attempt off that stale copy is how the retry cap used to leak.
    from .degrade import failure_class
    fclass = failure_class(surface, result.verdict, environ=environ)

    def _escalate(cur: LivenessState) -> LivenessState:
        cur.attempts += 1
        cur.last_failure_class = fclass
        if cur.backlog_since <= 0.0:
            cur.backlog_since = t
        cur.next_retry_at = t + _backoff(cur.attempts, rnd)
        return cur

    update_state(surface, _escalate)
    return result


# ------------------------------------------------------------------------------ surfaced status
@dataclass
class PendingStatus:
    """The ONE surfaced snapshot of the local-only backlog — rendered identically by the badge,
    `mokata doctor`, and the MCP read responses. Carries only counts + a failure CLASS + an age
    (seconds); NEVER a DSN value and NEVER any memory content (secret-safe by construction)."""

    pending: int
    oldest_age_s: Optional[float] = None
    last_failure_class: str = ""
    capped: bool = False
    next_retry_at: float = 0.0

    @property
    def class_label(self) -> str:
        from .degrade import _CLASS_LABEL
        return _CLASS_LABEL.get(self.last_failure_class, self.last_failure_class)

    def badge_segment(self, *, ascii_only: bool = False) -> str:
        """The compact statusline fragment, e.g. ` 3 pending` (empty when nothing is pending)."""
        if self.pending <= 0:
            return ""
        return f" {self.pending} pending"

    def doctor_line(self, *, ascii_only: bool = False) -> str:
        """The `mokata doctor` line: count + oldest-backlog age + the last-failure class."""
        glyph = "[!]" if ascii_only else "⚠"
        parts = [f"{glyph} {self.pending} approved write(s) journaled locally and NOT yet "
                 f"flushed to the team DB"]
        if self.oldest_age_s is not None and self.oldest_age_s > 0:
            parts.append(f"oldest waiting {_fmt_age(self.oldest_age_s)}")
        if self.last_failure_class:
            parts.append(f"last failure: {self.class_label}")
        if self.capped:
            parts.append("auto-retry paused (cap reached) — run `mokata sync` to flush")
        return "  team pending: " + "; ".join(parts) + "."

    def to_dict(self) -> dict:
        return {"pending": self.pending, "oldest_age_s": self.oldest_age_s,
                "last_failure_class": self.last_failure_class, "capped": self.capped}


def _fmt_age(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def pending_status(surface: Any, *, routing: Any = None, environ: Optional[dict] = None,
                   now: Optional[Callable[[], float]] = None) -> Optional[PendingStatus]:
    """The surfaced backlog snapshot, or None when there is nothing to surface (local mode, or a
    fully-flushed team state). When `routing` (the ONE CM.S2 read-routing decision) is a degrade,
    its failure class is AUTHORITATIVE — so the pending status can never disagree with the degraded
    read notice — else the last-recorded liveness class is used. Degrade-clean (never raises).

    D5 — an UNREADABLE journal is no longer indistinguishable from an EMPTY one. It used to return
    None on any error, and every caller (the badge, `mokata doctor`, the MCP read responses) reads
    None as "no backlog" — so approved-but-unflushed writes sat on disk while every surface reported
    a clean, fully-flushed team state. That is the exact C-4 signal this module exists to raise,
    inverted. The return is UNCHANGED (None — there is no honest count to give), but it is now LOUD:
    the flush state is reported as UNKNOWN rather than as CLEAN."""
    from . import team_journal
    import time as _time
    clock = now or _time.time
    # `read_mode` never raises (fail-closed to LOCAL by contract) — no guard needed.
    if _rm.read_mode(surface) != _rm.TEAM:
        return None
    try:
        pending = team_journal.TeamJournal.for_surface(surface).pending_count()
    except OSError as exc:
        # The ONLY raisable here: the journal replay opens a file (`_records`), and a torn JSON line
        # is already skipped inside it. A locked/permission-broken `temp_local/` is the real case.
        note_degraded("team-flush", FAILURE_LOCAL_IO,
                      fallback="the pending-write backlog could NOT be read — treat the flush "
                               "state as UNKNOWN",
                      fix="check permissions on `.mokata/temp_local/`, then run `mokata sync`",
                      detail=f"{type(exc).__name__}: {exc}")
        return None
    if pending <= 0:
        return None

    state = load_state(surface)
    failure_class = ""
    if routing is not None and getattr(routing, "degraded", False):
        notice = getattr(routing, "notice", None)
        failure_class = getattr(notice, "failure_class", "") if notice is not None else ""
    if not failure_class:
        failure_class = state.last_failure_class

    age = (clock() - state.backlog_since) if state.backlog_since > 0 else None
    return PendingStatus(pending=pending, oldest_age_s=age, last_failure_class=failure_class,
                         capped=state.capped, next_retry_at=state.next_retry_at)


def badge_segment(surface: Any, *, ascii_only: bool = False,
                  now: Optional[Callable[[], float]] = None) -> str:
    """The ZERO-NETWORK statusline segment for the pending backlog — reads only the local journal
    count (no probe, no routing), so the badge can't hang. Empty in local mode / when nothing is
    pending (byte-identical to today)."""
    from . import team_journal
    try:
        if _rm.read_mode(surface) != _rm.TEAM:
            return ""
        pending = team_journal.TeamJournal.for_surface(surface).pending_count()
    except Exception:
        # LEGITIMATE SUPPRESS (and deliberately still broad): the statusline runs in a SEPARATE
        # process on a zero-network hot path, on every prompt. It must never break, never hang and
        # never print — a notice here would be shouted into a rendering context that is not a log.
        # The authoritative surface for this failure is `pending_status`, which doctor/MCP call and
        # which DOES announce it (above); the badge merely goes quiet.
        return ""
    if pending <= 0:
        return ""
    return f" {pending} pending"
