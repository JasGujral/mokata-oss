"""D1 — the remaining pipeline phases + the end-to-end runner.

Implements the still-thin `analysis` and `strawman` phases and a runner that drives all
seven phases in order — brainstorm → analysis → strawman → pre_mortem → probes →
completeness_gate → emit — each consuming the prior phase's output. Built on the existing
PIPELINE_PHASES, the pre-mortem (D4) and completeness gate (D2/D3), and the human-gated
WriteGate (I2); there is no parallel pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..brainstorm import PIPELINE_PHASES
from ..govern.trust import CLI_SURFACE
from ..govern import KarpathyContext, WriteGate, run_karpathy_for_phase
from .completeness import run_completeness_gate
from .emit import commit_spec
from .premortem import derive_probes
from .spec import Spec, TestRef


@dataclass
class Analysis:
    approach: str
    notes: List[str] = field(default_factory=list)
    components: List[str] = field(default_factory=list)
    structural: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Strawman:
    components: List[str] = field(default_factory=list)
    coverage: Dict[str, str] = field(default_factory=dict)   # ac_id -> component


@dataclass
class PhaseContext:
    handoff: Any
    spec: Spec
    tests: List[TestRef]
    knowledge: Any = None
    analysis: Optional[Analysis] = None
    strawman: Optional[Strawman] = None
    probes: List[Any] = field(default_factory=list)
    probe_coverage: Dict[str, bool] = field(default_factory=dict)
    gate_result: Any = None
    emitted: Optional[Dict[str, Any]] = None


@dataclass
class PhaseRecord:
    name: str
    ok: bool
    summary: str
    gate_id: Optional[str] = None
    gate_passed: Optional[bool] = None


@dataclass
class PipelineRun:
    phases: List[PhaseRecord]
    context: PhaseContext
    sequence: List[str]
    emitted: Optional[Dict[str, Any]]
    ok: bool
    karpathy: List[Any] = field(default_factory=list)   # G3 gate fires (when enabled)

    def render(self) -> str:
        lines = ["mokata pipeline run:"]
        for r in self.phases:
            gate = f"  [gate {r.gate_id}: {'pass' if r.gate_passed else 'block'}]" \
                if r.gate_id else ""
            lines.append(f"  {'OK ' if r.ok else 'XX '} {r.name:17} {r.summary}{gate}")
        lines.append(f"  RESULT: {'emitted' if self.ok else 'not emitted'}")
        return "\n".join(lines)


# --- individual phases ---------------------------------------------------------
def _analysis(ctx: PhaseContext) -> PhaseRecord:
    notes = [f"approved approach: {ctx.handoff.approach.name}"]
    for q in ctx.handoff.answered_questions:
        notes.append(f"constraint: {q.text} -> {q.answer}")
    components = [f"unit:{ac.id}" for ac in ctx.spec.criteria]
    structural: Dict[str, Any] = {}
    if ctx.knowledge is not None:
        res = ctx.knowledge.callers(ctx.spec.title)
        structural = {"backend": res.backend, "callers": res.count}
    ctx.analysis = Analysis(approach=ctx.handoff.approach.name, notes=notes,
                            components=components, structural=structural)
    return PhaseRecord("analysis", True,
                       f"{len(components)} component(s), {len(notes)} constraint/note(s)")


def _strawman(ctx: PhaseContext) -> PhaseRecord:
    if ctx.analysis is None:
        return PhaseRecord("strawman", False, "no analysis to build on")
    coverage = {ac.id: comp for ac, comp in
                zip(ctx.spec.criteria, ctx.analysis.components)}
    ctx.strawman = Strawman(components=list(ctx.analysis.components), coverage=coverage)
    return PhaseRecord("strawman", True, f"first-cut design covers {len(coverage)} AC(s)")


def _pre_mortem(ctx: PhaseContext) -> PhaseRecord:
    ctx.probes = derive_probes(ctx.handoff)
    return PhaseRecord("pre_mortem", True, f"{len(ctx.probes)} risk probe(s) derived")


def _probes(ctx: PhaseContext) -> PhaseRecord:
    ac_text = " ".join(ac.text.lower() for ac in ctx.spec.criteria)
    addressed = 0
    for p in ctx.probes:
        hit = any(w in ac_text for w in p.risk.lower().split() if len(w) > 3)
        ctx.probe_coverage[p.id] = hit
        addressed += int(hit)
    return PhaseRecord("probes", True,
                       f"{addressed}/{len(ctx.probes)} probe(s) addressed by the spec")


def _completeness(ctx: PhaseContext) -> PhaseRecord:
    gr = run_completeness_gate(ctx.spec, ctx.tests, handoff=ctx.handoff)
    ctx.gate_result = gr
    return PhaseRecord("completeness_gate", gr.passed, gr.reason,
                       gate_id="completeness", gate_passed=gr.passed)


def _emit(ctx: PhaseContext, store: Any, gate: WriteGate, approve: bool) -> PhaseRecord:
    if not (ctx.gate_result and ctx.gate_result.passed):
        return PhaseRecord("emit", False,
                           "emit refused — completeness gate did not pass",
                           gate_id="emit-approval", gate_passed=False)
    # DK.S0 — carry the brainstorm-classified domain set (on the approved hand-off) into the spec
    # as a first-class constraint, so it is persisted into emitted_spec.json and the user approves
    # the domains along with the plan. A spec that already carries domains keeps its own.
    if not ctx.spec.domains:
        ctx.spec.domains = list(getattr(ctx.handoff, "domains", []) or [])

    if store is None:                       # no state surface — nothing durable to write
        return PhaseRecord("emit", False, "emit refused — no state store to persist the spec into",
                           gate_id="emit-approval", gate_passed=False)

    # SI-DEV.0 — the durable write is `engine/emit.py:spec_commit`, the ONE committer every surface
    # shares (the `mokata spec emit` CLI and the `spec_emit` MCP tool land there too). It writes the
    # run's `emitted_spec` AND appends to the shared `spec_corpus` in the same gated commit, so a
    # spec on record is always a spec `spec-check` can see. approve=True -> auto-approve;
    # approve=False -> a deterministic decline (no prompt).
    committed, _reason, _size = commit_spec(
        store, ctx.spec, gate=gate, assume_yes=approve,
        confirm=None if approve else (lambda _t: False), surface=CLI_SURFACE)
    if committed:
        ctx.emitted = ctx.spec.to_dict()
    return PhaseRecord("emit", committed,
                       "spec emitted" if committed else "emit declined at the gate",
                       gate_id="emit-approval", gate_passed=committed)


def _mark_gate_passed(store: Any, phase: str) -> None:
    """SS.S1 (b) double-wire — checkpoint each passed gate in the LIVE pipeline so an interrupted
    run resumes from the last passed phase (not the start). run_id from MS.S2's `current_run_id()`;
    the write rides `PipelineCheckpoint.mark_passed` (idempotent). Degrade-clean: a persist failure
    warns ONCE and is swallowed — it never blocks the phase or changes the run's output."""
    try:
        from ..session import current_run_id
        from ..govern.resume import PipelineCheckpoint
        PipelineCheckpoint(store, current_run_id()).mark_passed(phase)
    except Exception:
        from ..session_flow import note_persist_failure
        note_persist_failure("gate:" + phase)


def _karpathy_context(ctx: PhaseContext) -> KarpathyContext:
    """Read the Karpathy-gate signals (G3) off the current pipeline state."""
    return KarpathyContext(
        has_plan=bool(ctx.handoff),
        complexity=(len(ctx.analysis.components) if ctx.analysis
                    else len(ctx.spec.criteria)),
        touched_files=len(ctx.strawman.components) if ctx.strawman else 0,
        has_success_criteria=bool(ctx.spec.criteria),
        verified=bool(ctx.gate_result and getattr(ctx.gate_result, "passed", False)),
    )


def run_pipeline(handoff: Any, spec: Spec, tests: List[TestRef],
                 knowledge: Any = None, ledger: Any = None, store: Any = None,
                 approve: bool = True, manifest: Any = None) -> PipelineRun:
    """Drive all seven phases end-to-end. Each phase consumes the prior output via the
    shared context; emit is human-gated and refused unless the completeness gate passed.

    G3 (hybrid): when a `manifest` is supplied, the engine fires the Karpathy gates
    registered at each phase (the rules layer owns the per-gate toggle + audit); without
    a manifest the gates are skipped entirely (degrade-clean)."""
    ctx = PhaseContext(handoff=handoff, spec=spec, tests=tests, knowledge=knowledge)
    gate = WriteGate(ledger=ledger)
    records: List[PhaseRecord] = []
    karpathy: List[Any] = []

    for phase in PIPELINE_PHASES:
        if phase == "brainstorm":
            rec = PhaseRecord("brainstorm", bool(handoff),
                              f"approved approach: {handoff.approach.name}",
                              gate_id="approach-approval", gate_passed=True)
        elif phase == "analysis":
            rec = _analysis(ctx)
        elif phase == "strawman":
            rec = _strawman(ctx)
        elif phase == "pre_mortem":
            rec = _pre_mortem(ctx)
        elif phase == "probes":
            rec = _probes(ctx)
        elif phase == "completeness_gate":
            rec = _completeness(ctx)
        else:  # emit
            rec = _emit(ctx, store, gate, approve)
        if ledger is not None:
            ledger.record("phase", phase=phase, ok=rec.ok, summary=rec.summary)
        # SS.S1 (b) — a passed gate in the live pipeline leaves a resume checkpoint (degrade-clean;
        # only when a store is wired, so the runner stays byte-identical but for the side effect).
        if store is not None and rec.ok:
            _mark_gate_passed(store, phase)
        if manifest is not None:
            # G3 — fire the (enabled) Karpathy gates for this phase off the post-phase
            # state; each is audited by the rules layer. Skipped wholesale w/o a manifest.
            karpathy.extend(run_karpathy_for_phase(
                phase, _karpathy_context(ctx), manifest=manifest, ledger=ledger))
        records.append(rec)

    return PipelineRun(
        phases=records, context=ctx, sequence=[r.name for r in records],
        emitted=ctx.emitted, ok=all(r.ok for r in records) and ctx.emitted is not None,
        karpathy=karpathy,
    )
