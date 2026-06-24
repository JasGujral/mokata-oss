"""D1 — the remaining pipeline phases + the end-to-end runner.

Implements the still-thin `analysis` and `strawman` phases and a runner that drives all
seven phases in order — brainstorm → analysis → strawman → pre_mortem → probes →
completeness_gate → emit — each consuming the prior phase's output. Built on the existing
PIPELINE_PHASES, the pre-mortem (D4) and completeness gate (D2/D3), and the human-gated
WriteGate (I2); there is no parallel pipeline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..brainstorm import PIPELINE_PHASES
from ..govern import WriteGate, WriteRequest
from .completeness import run_completeness_gate
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
    content = json.dumps(ctx.spec.to_dict())

    def commit() -> None:
        if store is not None:
            store.write("emitted_spec", ctx.spec.to_dict())

    # approve=True -> auto-approve; approve=False -> a deterministic decline (no prompt).
    out = gate.submit(WriteRequest("config", "spec:emit", content), commit=commit,
                      assume_yes=approve, confirm=None if approve else (lambda _t: False))
    if out.committed:
        ctx.emitted = ctx.spec.to_dict()
    return PhaseRecord("emit", out.committed,
                       "spec emitted" if out.committed else "emit declined at the gate",
                       gate_id="emit-approval", gate_passed=out.committed)


def run_pipeline(handoff: Any, spec: Spec, tests: List[TestRef],
                 knowledge: Any = None, ledger: Any = None, store: Any = None,
                 approve: bool = True) -> PipelineRun:
    """Drive all seven phases end-to-end. Each phase consumes the prior output via the
    shared context; emit is human-gated and refused unless the completeness gate passed."""
    ctx = PhaseContext(handoff=handoff, spec=spec, tests=tests, knowledge=knowledge)
    gate = WriteGate(ledger=ledger)
    records: List[PhaseRecord] = []

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
        records.append(rec)

    return PipelineRun(
        phases=records, context=ctx, sequence=[r.name for r in records],
        emitted=ctx.emitted, ok=all(r.ok for r in records) and ctx.emitted is not None,
    )
