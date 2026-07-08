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
def render_progress(progress: RunProgress, ascii_only: bool = False,
                    surface: Any = None) -> str:
    """The run-progress block. No run → the friendly message (degrade-clean).

    Stage 6d — when a `surface` is passed (the `/progress` command's MAX-detail view), the
    detailed 7-phase tracker is FOLLOWED by the 5 user-stage arc (brainstorm→spec→develop→
    review→ship, done/current/pending) with the 6c develop sub-counter and an explicit
    "what's pending this session". That arc is DERIVED from `_badge_state` — the SAME single
    source the always-on badge uses, never a second progress model. With no surface (the CLI
    `--lanes`-less default callers that don't pass one, and every existing caller) the block
    is byte-identical to before, so nothing regresses."""
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
    lines.extend(_user_stage_arc_lines(surface, ascii_only))   # 6d — MAX detail, degrade-clean
    return "\n".join(lines)


def _user_stage_arc_lines(surface: Any, ascii_only: bool = False) -> List[str]:
    """The 5 user-stage arc (done/current/pending) + the 6c develop sub-counter + an explicit
    "what's pending this session" (the stages after the active one), DERIVED from `_badge_state`
    — the SAME source the always-on badge reads (the 6b log + the checkpoint), never a new model.
    Returns `[]` (nothing appended) with no surface or on ANY derivation problem, so the block
    degrades clean to today's phase-only view."""
    if surface is None:
        return []
    try:
        active, counter = _badge_state(surface)
    except Exception:
        return []
    if active is None:
        return []
    glyphs = _ASCII_GLYPHS if ascii_only else _GLYPHS
    active_idx = STAGE_BADGE_STAGES.index(active)
    cells = []
    for i, s in enumerate(STAGE_BADGE_STAGES):
        if i < active_idx:
            cells.append(f"{glyphs[DONE]} {s}")
        elif i == active_idx:
            label = f"{s} [{counter}]" if counter else s     # 6c develop / spec sub-counter
            cells.append(f"{glyphs[CURRENT]} {label}")
        else:
            cells.append(f"{glyphs[PENDING]} {s}")
    pending = list(STAGE_BADGE_STAGES[active_idx + 1:])
    return ["",
            "user stages:  " + "  ·  ".join(cells),
            "pending this session: " + (", ".join(pending) if pending else "—")]


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
# develop/review/ship are separate skills with no shared pipeline checkpoint, so the run
# checkpoint alone can't tell them apart. Stage 6b closes that: the develop/review/ship
# skills append a `stage_enter` to the append-only progress-event log, and _badge_state
# reads it to advance the badge across ALL FIVE stages. The log is a COMPATIBLE SUBSET of
# 0.1.0's R1.S1a event stream (one persistence layer, not two). Zero fabrication — the
# badge only claims a stage the log actually recorded; absent/corrupt log -> the
# checkpoint-derived default (today's behaviour), never an error.


def _logged_user_stage(surface: Any, run_id: Optional[str]):
    """The furthest user-stage the Stage-6b progress log recorded (its most recent
    `stage_enter`), or None when the log is absent/empty/unreadable. When `run_id` is
    given (an active pipeline run exists) only that run's events count, so a NEW run's
    fresh brainstorm is never shadowed by a PRIOR run's stale ship event; with no active
    run the whole log is the only signal, so its latest transition is used. Degrade-clean:
    any problem -> None (the caller falls back to the checkpoint-derived default)."""
    try:
        from .progress_events import ProgressLog, STAGE_ENTER
        events = ProgressLog.from_surface(surface).read_events()
    except Exception:
        return None
    stage = None
    for e in events:
        if e.get("type") != STAGE_ENTER:
            continue
        if e.get("stage") not in STAGE_BADGE_STAGES:
            continue
        if run_id is not None and e.get("run_id") not in (run_id, None):
            continue
        stage = e.get("stage")          # forward-appended; the last match is "where we are"
    return stage


def statusline_enabled(surface: Any) -> bool:
    """settings.ux.statusline — the badge is opt-OUT (default True). A broken/absent surface
    reads as enabled so the default-on behaviour is never silently lost."""
    try:
        return bool((surface.manifest.setting("ux", {}) or {}).get("statusline", True))
    except Exception:
        return True


# Stage 6d — the badge is EVERYTHING-ON by default. `full` renders the complete arc (per-stage
# glyphs + the spec/develop counters + the fan-out agents summary); `minimal` dials DOWN to the
# active cell alone. Opt-DOWN and degrade-clean, read EXACTLY like `statusline_enabled`: any
# absent / broken / unrecognised value reads as `full`, so the complete picture is never silently
# lost — the user must deliberately choose `minimal`.
BADGE_FULL, BADGE_MINIMAL = "full", "minimal"


def badge_verbosity(surface: Any) -> str:
    """settings.ux.badge_verbosity — `full` (default, everything on) | `minimal` (active cell
    only). Opt-DOWN, degrade-clean: an absent/broken/unrecognised value reads as `full`, mirroring
    statusline_enabled's opt-out default so the full badge is never silently lost."""
    try:
        v = (surface.manifest.setting("ux", {}) or {}).get("badge_verbosity", BADGE_FULL)
        return BADGE_MINIMAL if v == BADGE_MINIMAL else BADGE_FULL
    except Exception:
        return BADGE_FULL


def _badge_state(surface: Any):
    """(active_user_stage, counter) derived read-only from run-state + the Stage-6b progress
    log — (None, "") with no run and no log. `counter` is the pipeline phase fraction shown
    only during the spec stage.

    Two sources, combined as FURTHEST-FORWARD (honest, forward-only): the pipeline checkpoint
    tells brainstorm/spec/develop; the append-only progress log advances the badge into
    develop/review/ship (the transitions the checkpoint can't see). The log can only move the
    badge FORWARD past the checkpoint-derived stage, never backward — a stage is claimed only
    if one source genuinely recorded it. Degrade-clean: no log -> the checkpoint default; no
    checkpoint but a log -> the log's stage (the user did enter it)."""
    store = surface.state                       # may raise on a broken surface -> caller guards
    try:
        from .brainstorm import restore_brainstorm_progress
        if restore_brainstorm_progress(store) is not None:
            return "brainstorm", ""             # mid-stream exploration (HARD-GATE still holds)
    except Exception:
        pass

    prog = build_progress(store)
    # checkpoint-derived candidate (today's behaviour) + the active run id to scope the log.
    if not prog.active:
        ckpt_stage, counter, run_id = None, "", None
    elif prog.complete:
        ckpt_stage, counter, run_id = "develop", "", prog.run_id  # spec emitted -> develop
    elif prog.current == "brainstorm":
        ckpt_stage, counter, run_id = "brainstorm", "", prog.run_id
    else:
        ckpt_stage, counter, run_id = "spec", f"{prog.done}/{prog.total}", prog.run_id

    logged = _logged_user_stage(surface, run_id)
    if logged is None:
        stage = ckpt_stage                      # degrade-clean -> checkpoint default
    elif ckpt_stage is None:
        stage, counter = logged, ""             # no checkpoint, but the log recorded a stage
    else:
        # furthest-forward: the log only advances the badge PAST the checkpoint stage.
        ck_i = STAGE_BADGE_STAGES.index(ckpt_stage)
        lg_i = STAGE_BADGE_STAGES.index(logged)
        if lg_i > ck_i:
            stage, counter = logged, ""         # advanced into develop/review/ship
        else:
            stage = ckpt_stage

    # Stage 6c — at develop, surface the REAL develop task counter (done/total) mirroring
    # the spec `[n/m]` counter, DERIVED from the same build_run_lanes fan-out view (single
    # source). Only when a real decomposition/execmode batch is on record; no batch / a
    # sequential run / unreadable ledger -> no counter, exactly like review/ship (honesty).
    if stage == "develop":
        counter = _develop_counter(surface)
    return stage, counter


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
    name = f"{session_name} · " if session_name else ""

    # Stage 6d — opt-DOWN verbosity. `minimal` collapses to the active cell alone (no per-stage
    # glyphs, no spec/develop counter, no agents summary); `full` (the degrade-clean default)
    # renders today's complete badge below. Never raises — a bad config reads as full.
    try:
        verbosity = badge_verbosity(surface)
    except Exception:
        verbosity = BADGE_FULL
    if verbosity == BADGE_MINIMAL:
        ell = "..." if ascii_only else "…"
        return f"mokata {arrow} {name}{ell}{lo}{active}{hi}{ell}"

    glyphs = _ASCII_GLYPHS if ascii_only else _GLYPHS
    active_idx = STAGE_BADGE_STAGES.index(active)
    # Every cell carries a done/current/pending glyph (reusing the on-demand block's
    # vocabulary) so a passed stage reads as ✓ done, not flat-and-indistinguishable from a
    # not-yet-reached one; the active cell keeps its ›bracket‹ emphasis ON TOP of its glyph.
    cells = []
    for i, s in enumerate(STAGE_BADGE_STAGES):
        if i < active_idx:
            cells.append(f"{glyphs[DONE]}{s}")
        elif i == active_idx:
            cells.append(f"{glyphs[CURRENT]}{lo}{s}{hi}")
        else:
            cells.append(f"{glyphs[PENDING]}{s}")
    strip = "[" + " · ".join(cells) + "]"
    badge = f"mokata {arrow} {name}{strip}"
    if counter:
        badge += f" · {counter}"
    agents = _badge_agents(surface)          # Stage 54d — compact fan-out summary, if any
    if agents:
        badge += f" · {agents}"
    return badge


def statusline_badge(surface: Any, *, session_name: Optional[str] = None,
                     ascii_only: bool = False) -> str:
    """The full statusline segment: the run-mode badge + the pipeline-stage badge, e.g.
    `local · mokata ▸ [brainstorm · ›spec‹ · develop · review · ship]`.

    TM.S1 — the run mode (local|team) ALWAYS prefixes the badge so a session is never
    ambiguous about which mode it's in. The stage badge is left byte-for-byte as
    `build_stage_badge` produces it (it degrades to a plain `mokata` with no run); the mode
    is a SEPARATE segment, not folded into the stage strip. Pure, read-only, degrade-clean:
    a broken surface reads as `local`, never an error."""
    from .run_mode import mode_badge
    try:
        mode = mode_badge(surface, ascii_only=ascii_only)
    except Exception:
        from .run_mode import LOCAL
        mode = LOCAL
    stage = build_stage_badge(surface, session_name=session_name, ascii_only=ascii_only)
    return f"{mode} · {stage}"


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
    tasks: int = 0                   # the batch's task total (exec_estimate.tasks); 0 = no batch

    @property
    def header(self) -> str:
        p = self.progress
        if p is None or not p.active:
            return "mokata · no active run"
        cur = f" · {p.current}" if p.current else (" · complete" if p.complete else "")
        return f"mokata · run [{p.done}/{p.total} done]{cur}"

    def to_dict(self) -> dict:
        return {"active": self.active, "mode": self.mode, "degraded": self.degraded,
                "tasks": self.tasks,
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
        return RunLanes(active=True, mode=mode, lanes=lanes, progress=progress,
                        tasks=tasks_n)

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
                    lanes=[Lane(name="sequential", state=state, note=note, at=at)],
                    tasks=tasks_n)


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


# =============================================================== Stage 6c — develop task counter
# When the badge is at `develop` AND a real decomposition/execmode batch exists, the badge shows
# a REAL `<done>/<total>` task counter — the develop analogue of the spec `[n/m]` phase counter.
# It is DERIVED from the SAME build_run_lanes fan-out view the agents summary uses (one source,
# no independent task tracking, no second ledger read of its own): total = the batch's task count
# (exec_estimate.tasks, carried on RunLanes.tasks); done = the `done` lanes. Zero fabrication —
# no real per-task batch on record → NO counter (exactly as review/ship show none today), never
# a fabricated `0/0`. Changing the ledger's per-task state changes ONLY this number.

def develop_counter(rl: "RunLanes") -> str:
    """`<done>/<total>` from a RunLanes fan-out view, or "" when no real per-task batch exists.
    total = the batch's task count (`rl.tasks`, from exec_estimate.tasks); done = the `done`
    lanes. Only a parallel/fanout batch carries genuine per-task lanes, so a sequential run, a
    degraded-to-sequential run, a no-batch run, or a run with no task total yields "" — never an
    invented count where no real per-item state exists (the honesty rule)."""
    if rl is None or not rl.active or rl.mode not in _PARALLEL_MODES:
        return ""
    total = rl.tasks
    if total <= 0:
        return ""
    done = sum(1 for ln in rl.lanes if ln.state == L_DONE)
    return f"{done}/{total}"


def _develop_counter(surface: Any) -> str:
    """The develop task counter for `surface`'s active run — the single-source read the badge
    uses at the develop stage. Reuses build_run_lanes (never re-derives task state); `""` on any
    read problem / no real batch, so the badge degrades clean and never fabricates a count."""
    try:
        from .govern import AuditLedger
        ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
        return develop_counter(build_run_lanes(surface.state, ledger=ledger))
    except Exception:
        return ""
