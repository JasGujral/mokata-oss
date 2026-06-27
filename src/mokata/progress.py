"""Stage 27 — run-progress UX (a read-only view over existing run-state).

A glanceable step tracker so a multi-phase run is legible instead of opaque: the ordered
phases (with their gates) marked done / current / pending, plus counts ([done/total]), and a
one-line active-skill banner. It is **derived** from the persisted run-state
(`pipeline_run__<id>` checkpoints) + the canonical `PIPELINE_PHASES`, so it is a single
source that can't drift from what the engine actually did. Read-only, local, no telemetry; a
run that doesn't exist degrades to a friendly message, never an error.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, List, Optional

from .brainstorm import PIPELINE_PHASES
from .govern.resume import CHECKPOINT_PREFIX, PipelineCheckpoint
from .pipeline import PHASE_GATES

DONE, CURRENT, PENDING = "done", "current", "pending"
_GLYPHS = {DONE: "✓", CURRENT: "▶", PENDING: "○"}        # ✓ ▶ ○
_ASCII_GLYPHS = {DONE: "[x]", CURRENT: "[>]", PENDING: "[ ]"}

NO_RUN_MESSAGE = (
    "mokata · no run in progress — start with /mokata:brainstorm (a new problem) "
    "or /mokata:refine (existing code)."
)


@dataclass
class ProgressStep:
    phase: str
    status: str                       # done | current | pending
    gate_id: Optional[str] = None
    note: str = ""


@dataclass
class RunProgress:
    active: bool
    total: int
    done: int = 0
    pending: int = 0
    run_id: Optional[str] = None
    steps: List[ProgressStep] = field(default_factory=list)
    current: Optional[str] = None
    next_phase: Optional[str] = None
    complete: bool = False
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "active": self.active, "run_id": self.run_id,
            "done": self.done, "total": self.total, "pending": self.pending,
            "current": self.current, "next": self.next_phase, "complete": self.complete,
            "steps": [{"phase": s.phase, "status": s.status, "gate": s.gate_id,
                       "note": s.note} for s in self.steps],
            "message": self.message,
        }


# --------------------------------------------------------------- run discovery
def list_runs(store: Any) -> List[str]:
    """Run ids with a persisted checkpoint, by scanning the state dir (the StateStore has
    no list API). Sorted, deterministic; empty when none / unreadable."""
    root = getattr(store, "root", None)
    if not root or not os.path.isdir(root):
        return []
    out: List[str] = []
    for fn in sorted(os.listdir(root)):
        if fn.startswith(CHECKPOINT_PREFIX) and fn.endswith(".json"):
            out.append(fn[len(CHECKPOINT_PREFIX):-len(".json")])
    return out


def find_active_run(store: Any, phases=PIPELINE_PHASES) -> Optional[str]:
    """The run to show: the first incomplete run, else the most recent, else None."""
    runs = list_runs(store)
    for rid in runs:
        if not PipelineCheckpoint(store, rid).is_complete(phases):
            return rid
    return runs[-1] if runs else None


# --------------------------------------------------------------- the model
def build_progress(store: Any, run_id: Optional[str] = None,
                   phases=PIPELINE_PHASES) -> RunProgress:
    """Derive the progress view for `run_id` (or the active run). No run → a clean,
    inactive view carrying the friendly message (never raises)."""
    total = len(phases)
    rid = run_id or find_active_run(store, phases)
    if rid is None:
        return RunProgress(active=False, total=total, pending=total,
                           message=NO_RUN_MESSAGE)

    if store.read(CHECKPOINT_PREFIX + rid) is None:
        # an explicit run_id that doesn't exist -> friendly, inactive (never an error)
        return RunProgress(active=False, total=total, pending=total, run_id=run_id,
                           message=f"mokata · no run '{run_id}' on record. " + NO_RUN_MESSAGE)

    cp = PipelineCheckpoint(store, rid)
    passed = [p for p in cp.passed if p in phases]
    passed_set = set(passed)
    current = cp.resume_phase(phases)        # first phase after the last passed (or None)
    complete = current is None

    steps: List[ProgressStep] = []
    for p in phases:
        gate = PHASE_GATES.get(p)
        if p in passed_set:
            status, note = DONE, (f"{gate.id} passed" if gate else "")
        elif p == current:
            status = CURRENT
            note = f"{gate.id} ({gate.kind})" if gate else "in progress"
        else:
            status, note = PENDING, ""
        steps.append(ProgressStep(p, status, gate.id if gate else None, note))

    done = len(passed_set)
    next_phase = None
    if current is not None:
        i = phases.index(current)
        next_phase = phases[i + 1] if i + 1 < len(phases) else None

    return RunProgress(active=True, total=total, done=done, pending=total - done,
                       run_id=rid, steps=steps, current=current,
                       next_phase=next_phase, complete=complete)


# --------------------------------------------------------------- renderers
def render_progress(progress: RunProgress, ascii_only: bool = False) -> str:
    """The compact, glanceable block. No run → the friendly message (degrade-clean)."""
    if not progress.active:
        return progress.message
    glyphs = _ASCII_GLYPHS if ascii_only else _GLYPHS
    lines = [f"mokata · run  [{progress.done}/{progress.total} done]"]
    for s in progress.steps:
        suffix = ""
        if s.status == CURRENT:
            suffix = "   ← you are here" if not ascii_only else "   <- you are here"
            if s.note:
                suffix += f"  ({s.note})"
        elif s.status == DONE and s.note:
            suffix = f"   {s.note}"
        lines.append(f"  {glyphs[s.status]} {s.phase:<16}{suffix}")
    if progress.complete:
        lines.append("run complete ✓" if not ascii_only else "run complete [x]")
    else:
        nxt = progress.next_phase or "—"
        lines.append(f"next: {nxt}     ·     pending: {progress.pending}/"
                     f"{progress.total}")
    return "\n".join(lines)


def active_banner(label: str, running: bool = True,
                  sub_done: Optional[int] = None,
                  sub_total: Optional[int] = None,
                  state: Optional[str] = None) -> str:
    """§2b — the always-on banner for what mokata is doing RIGHT NOW. e.g.
    `mokata · brainstorm (running)` / `mokata · develop [2/3] (done)`. `state` overrides the
    running/done label (Stage 29 uses `engaged` when brainstorm auto-activates)."""
    state = state or ("running" if running else "done")
    if sub_total:
        return f"mokata · {label} [{sub_done or 0}/{sub_total}] ({state})"
    return f"mokata · {label} ({state})"


# =============================================================== Stage 40 — parallel-aware lanes
# A read-only, multi-lane view of what mokata is doing right now: one lane per concurrent
# subagent (parallel) or a single lane (sequential), DERIVED from the persisted run-state +
# the execmode records the orchestrator already writes to the audit ledger. Nothing new is
# persisted; nothing leaves the machine; absent run-state / ledger degrade to less detail.

L_RUNNING, L_DONE, L_BLOCKED, L_DEGRADED = "running", "done", "blocked", "degraded"
_LANE_GLYPHS = {L_RUNNING: "▶", L_DONE: "✓", L_BLOCKED: "✗", L_DEGRADED: "⚠"}
_LANE_ASCII = {L_RUNNING: "[>]", L_DONE: "[x]", L_BLOCKED: "[!]", L_DEGRADED: "[~]"}

# How many trailing ledger entries the lanes view reads (frugal/bounded — never the full log).
LANE_LEDGER_TAIL = 200

_PARALLEL_MODES = ("parallel", "fanout")


@dataclass
class Lane:
    name: str
    state: str                       # running | done | blocked | degraded
    note: str = ""
    at: str = ""                     # last ledger timestamp for this lane (dashboard use)

    def to_dict(self) -> dict:
        return {"name": self.name, "state": self.state, "note": self.note, "at": self.at}


@dataclass
class RunLanes:
    active: bool
    mode: str                        # sequential | parallel | fanout | none
    lanes: List[Lane] = field(default_factory=list)
    degraded: bool = False
    progress: Optional[RunProgress] = None
    message: str = ""

    @property
    def header(self) -> str:
        p = self.progress
        if p is None or not p.active:
            return "mokata · no active run"
        cur = f" · {p.current}" if p.current else (" · complete" if p.complete else "")
        return f"mokata · run [{p.done}/{p.total} done]{cur}"

    def to_dict(self) -> dict:
        return {"active": self.active, "mode": self.mode, "degraded": self.degraded,
                "lanes": [ln.to_dict() for ln in self.lanes],
                "progress": self.progress.to_dict() if self.progress else None,
                "message": self.message}


def _ledger_tail(ledger: Any, n: int) -> List[dict]:
    """The last `n` ledger entries (bounded, frugal). Read-only; [] when absent/unreadable."""
    if ledger is None:
        return []
    try:
        return list(ledger.entries())[-n:]
    except Exception:
        return []


def _subagent_state(entry: dict) -> str:
    if not entry.get("ok", True):
        return L_BLOCKED
    if entry.get("review_passed") is False:
        return L_BLOCKED
    return L_DONE


def build_run_lanes(store: Any, ledger: Any = None, run_id: Optional[str] = None,
                    phases=PIPELINE_PHASES, tail: int = LANE_LEDGER_TAIL) -> RunLanes:
    """Derive the lane view from run-state (phase header) + the most recent execmode batch in a
    BOUNDED tail of the ledger. Parallel → one lane per subagent; sequential (or a parallel run
    that degraded) → a single lane; no batch → a single lane from the current phase; no run →
    a friendly empty state. Never raises, never writes."""
    progress = build_progress(store, run_id=run_id, phases=phases)
    if not progress.active:
        return RunLanes(active=False, mode="none", progress=progress,
                        message=progress.message)

    entries = _ledger_tail(ledger, tail)
    # the current batch begins at the last exec_estimate within the bounded tail.
    est_idx = None
    for i in range(len(entries) - 1, -1, -1):
        if entries[i].get("kind") == "exec_estimate":
            est_idx = i
            break

    if est_idx is None:
        # No execmode batch on record — fall back to a single lane from the current phase
        # (the back-compatible single-line feel).
        state = L_DONE if progress.complete else L_RUNNING
        name = progress.current or "run"
        return RunLanes(active=True, mode="none", progress=progress,
                        lanes=[Lane(name=name, state=state)])

    est = entries[est_idx]
    batch = entries[est_idx + 1:]
    mode = str(est.get("mode", "sequential"))
    tasks_n = int(est.get("tasks", 0) or 0)
    degraded = any(e.get("kind") == "exec_degrade" for e in batch)

    if mode in _PARALLEL_MODES and not degraded:
        lanes: List[Lane] = []
        for e in batch:
            if e.get("kind") != "subagent":
                continue
            st = _subagent_state(e)
            note = "review failed" if e.get("review_passed") is False else (
                "isolated" if e.get("isolated") else "")
            lanes.append(Lane(name=str(e.get("task", f"task{len(lanes)}")),
                              state=st, note=note, at=e.get("at", "")))
        # tasks estimated but not yet reported are still running (live mid-flight view).
        for i in range(max(tasks_n - len(lanes), 0)):
            lanes.append(Lane(name=f"task[{len(lanes)}]", state=L_RUNNING))
        return RunLanes(active=True, mode=mode, lanes=lanes, progress=progress)

    # sequential, or a parallel run that degraded to sequential — a single lane.
    seqs = [e for e in batch if e.get("kind") == "sequential"]
    if degraded:
        state = L_DEGRADED
    elif seqs and all(e.get("ok", True) for e in seqs) and (
            not tasks_n or len(seqs) >= tasks_n):
        state = L_DONE
    else:
        state = L_RUNNING
    note = ("degraded to sequential" if degraded else f"{len(seqs)} task(s)")
    at = seqs[-1].get("at", "") if seqs else est.get("at", "")
    return RunLanes(active=True, mode="sequential", degraded=degraded, progress=progress,
                    lanes=[Lane(name="sequential", state=state, note=note, at=at)])


def render_lanes(rl: RunLanes, ascii_only: bool = False) -> str:
    """The multi-lane terminal block. No run → the friendly message (degrade-clean). A single
    lane reads like the old single-line banner; N lanes show N concurrent lanes."""
    if not rl.active:
        return rl.message or NO_RUN_MESSAGE
    glyphs = _LANE_ASCII if ascii_only else _LANE_GLYPHS
    lines = [rl.header]
    if rl.mode in _PARALLEL_MODES:
        lines.append(f"  lanes ({len(rl.lanes)} concurrent):")
    for ln in rl.lanes:
        suffix = f"  ({ln.note})" if ln.note else ""
        lines.append(f"  {glyphs.get(ln.state, '?')} {ln.name:<20}{ln.state}{suffix}")
    return "\n".join(lines)
