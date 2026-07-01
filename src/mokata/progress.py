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


# --------------------------------------------------------------- session lifecycle (Stage 50)
@dataclass
class SessionInfo:
    run_id: str
    done: int
    total: int
    last_passed: Optional[str]      # the most recent passed gate (None for a fresh run)
    resume_phase: Optional[str]     # the phase resume would continue at (None = complete)
    complete: bool
    active: bool                    # the run `resume` (with no id) would pick


def list_sessions(store: Any, phases=PIPELINE_PHASES) -> List["SessionInfo"]:
    """All runs with a checkpoint — id, progress, last-passed/resume phase, complete/active.
    Read-only + bounded (one row per recorded run); empty list when there are none."""
    active = find_active_run(store, phases)
    out: List[SessionInfo] = []
    for rid in list_runs(store):
        cp = PipelineCheckpoint(store, rid)
        passed = [p for p in cp.passed if p in phases]
        rp = cp.resume_phase(phases)
        out.append(SessionInfo(run_id=rid, done=len(passed), total=len(phases),
                               last_passed=cp.last_passed(), resume_phase=rp,
                               complete=rp is None, active=(rid == active)))
    return out


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


# =============================================================== Stage 70c — native to-do widget
# One MORE renderer over the SAME RunProgress — never a second progress model. It projects the
# existing build_progress() view into the shape a harness's NATIVE to-do widget wants: a one-line
# summary + ordered items each marked done | in_progress | pending (the harness's own idea of a
# checklist). mokata cannot call the to-do tool itself — the AGENT renders the widget, driven by
# the single PROGRESS_INSTRUCTION; this function just gives it the derived items so it never has to
# invent them. Read-only, deterministic, degrade-clean: no run (or a surface it can't read) -> an
# empty summary + [] items, which the agent falls back from to the printed run-progress block.

# Native-widget vocabulary: RunProgress' `current` is the widget's `in_progress`.
_TODO_STATUS = {DONE: "done", CURRENT: "in_progress", PENDING: "pending"}


def _todo_summary(prog: "RunProgress") -> str:
    """The widget's top summary line, DERIVED from the same RunProgress counts."""
    if prog.complete:
        return f"mokata · run [{prog.done}/{prog.total} done] — complete"
    cur = prog.current or "—"
    return f"mokata · run [{prog.done}/{prog.total} done] — current: {cur}"


def build_todo_items(surface: Any, run_id: Optional[str] = None,
                     phases=PIPELINE_PHASES) -> dict:
    """Project the EXISTING RunProgress into `{summary, items:[{step, status}]}` for a native
    to-do widget. A THIN renderer: it calls build_progress() and never recomputes progress, so it
    cannot drift from the badge / block / lanes views. The active stage's sub-steps are surfaced
    when the run-state carries them (none today — degrade-clean, so none are invented). Pure,
    read-only, deterministic; no run / an unreadable surface -> `{"summary": "", "items": []}`."""
    try:
        store = surface.state
        prog = build_progress(store, run_id=run_id, phases=phases)
    except Exception:
        return {"summary": "", "items": []}
    if not prog.active:
        return {"summary": "", "items": []}
    items = [{"step": s.phase, "status": _TODO_STATUS[s.status]} for s in prog.steps]
    return {"summary": _todo_summary(prog), "items": items}


# =============================================================== Stage 54b — the stage badge
# A persistent "mode badge" (the feel of Claude Code's own "plan mode on" indicator) that
# always shows which USER-FACING stage a run is in. The five user stages are the pipeline
# SKILLS a user steps through — distinct from the 7 internal PIPELINE_PHASES the engine runs
# under the hood. We DERIVE the active user stage from the same read-only run-state
# build_progress() already exposes (+ an in-progress brainstorm), collapsing the internal
# phases onto the user-visible arc. Read-only, deterministic, degrade-clean: no run -> a
# minimal `mokata`, never an error; nothing new is ever written.

STAGE_BADGE_STAGES = ("brainstorm", "spec", "develop", "review", "ship")

# How the internal pipeline maps onto the user-facing arc:
#   brainstorm phase / in-progress brainstorm -> "brainstorm"
#   analysis..emit (building + emitting the spec) -> "spec"
#   the pipeline is complete (spec emitted) -> "develop" (the next thing the user does)
# develop/review/ship beyond "spec emitted -> develop" are separate skills with no shared
# run-state checkpoint, so they aren't distinguishable from the pipeline run alone — those
# cells simply render un-highlighted (honest; we never invent state we don't have).


def statusline_enabled(surface: Any) -> bool:
    """settings.ux.statusline — the badge is opt-OUT (default True). A broken/absent surface
    reads as enabled so the default-on behaviour is never silently lost."""
    try:
        return bool((surface.manifest.setting("ux", {}) or {}).get("statusline", True))
    except Exception:
        return True


def _badge_state(surface: Any):
    """(active_user_stage, counter) derived read-only from run-state — (None, "") with no
    run. `counter` is the pipeline phase fraction shown only during the spec stage."""
    store = surface.state                       # may raise on a broken surface -> caller guards
    try:
        from .brainstorm import restore_brainstorm_progress
        if restore_brainstorm_progress(store) is not None:
            return "brainstorm", ""             # mid-stream exploration (HARD-GATE still holds)
    except Exception:
        pass
    prog = build_progress(store)
    if not prog.active:
        return None, ""
    if prog.complete:
        return "develop", ""                    # spec emitted -> the user moves to develop
    if prog.current == "brainstorm":
        return "brainstorm", ""
    return "spec", f"{prog.done}/{prog.total}"  # building the spec (analysis..emit)


def build_stage_badge(surface: Any, *, session_name: Optional[str] = None,
                      ascii_only: bool = False) -> str:
    """The one-line mode badge, e.g. `mokata ▸ [brainstorm · spec · ›develop‹ · review · ship]`.

    Highlights the active user stage among STAGE_BADGE_STAGES; appends a compact phase
    counter during the spec stage; takes an optional `session_name` (the Stage-55 hook —
    Claude Code passes it on the statusLine stdin, omitted gracefully when absent). Pure,
    read-only, deterministic; with no run (or a surface it can't read) it degrades to a
    minimal `mokata`, never an error."""
    try:
        active, counter = _badge_state(surface)
    except Exception:
        active, counter = None, ""
    if active is None:
        return "mokata"
    lo, hi = (">", "<") if ascii_only else ("›", "‹")
    arrow = ">" if ascii_only else "▸"
    cells = [f"{lo}{s}{hi}" if s == active else s for s in STAGE_BADGE_STAGES]
    strip = "[" + " · ".join(cells) + "]"
    name = f"{session_name} · " if session_name else ""
    badge = f"mokata {arrow} {name}{strip}"
    if counter:
        badge += f" · {counter}"
    agents = _badge_agents(surface)          # Stage 54d — compact fan-out summary, if any
    if agents:
        badge += f" · {agents}"
    return badge


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


# =============================================================== Stage 54d — badge agents summary
# A COMPACT one-line agents summary for the 54b badge during a fan-out — e.g. "2 running ·
# 1 blocked". DERIVED from the SAME build_run_lanes view (no re-derivation); shown only for a
# parallel/fanout batch, omitted for sequential / no parallel run (degrade-clean).

def agents_summary(rl: RunLanes) -> str:
    """`<n> running · <n> done · <n> blocked` (only the non-zero states, in that order) for a
    PARALLEL/fanout batch; `""` for a sequential run, a degraded-to-sequential run, or no run."""
    if rl is None or not rl.active or rl.mode not in _PARALLEL_MODES:
        return ""
    counts: dict = {}
    for ln in rl.lanes:
        counts[ln.state] = counts.get(ln.state, 0) + 1
    parts = [f"{counts[s]} {s}"
             for s in (L_RUNNING, L_DONE, L_BLOCKED, L_DEGRADED) if counts.get(s)]
    return " · ".join(parts)


def _badge_agents(surface: Any) -> str:
    """The agents summary for `surface`'s active run (read-only, bounded ledger tail). `""` on
    any problem / no parallel batch — the badge degrades clean."""
    try:
        from .govern import AuditLedger
        ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
        return agents_summary(build_run_lanes(surface.state, ledger=ledger))
    except Exception:
        return ""
