"""L2 — mid-pipeline entry/exit ("from between").

You can enter the spine's pipeline at any phase and stop after it. The gates of the
phases you actually run all apply (never silently skipped); the phases upstream of your
entry are NOT forced, and their gates are NOT applied — but the skip is reported
explicitly, so nothing is hidden.

Built on the existing `PIPELINE_PHASES` (from the brainstorm/engine layer) — there is no
parallel phase list. Each gated phase reuses the same `Gate` type as the skills registry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .brainstorm import PIPELINE_PHASES
from .skills import Gate

# Which phases carry a gate (others are advisory/no-gate). Reuses the same Gate type as
# the skill registry so a phase-gate and a command-gate are one concept.
PHASE_GATES: Dict[str, Gate] = {
    "brainstorm": Gate(
        "approach-approval",
        "HARD-GATE: no spec until exactly one approach is explicitly approved.",
        "human"),
    "refine": Gate(
        "refinement-approval",
        "HARD-GATE: no spec until the user explicitly approves a scoped set of "
        "refinements; the approved set hands off to the existing spec skill.",
        "human"),
    "completeness_gate": Gate(
        "completeness",
        "Provable-completeness blocker: every acceptance criterion maps to a test "
        "before anything is emitted.",
        "check"),
    "emit": Gate(
        "emit-approval",
        "Emitting durable output (spec/code) is human-gated.",
        "human"),
}

# Front-end phases are alternative entry points that GATE and then hand off into the linear
# pipeline (they are not members of the linear 7-phase sequence). `brainstorm` is in the
# sequence; `refine` (Stage 26) runs standalone and hands to `spec`. ENTRY_PHASES is the
# set of valid `mokata enter` start phases.
FRONT_END_PHASES = ("brainstorm", "refine")
ENTRY_PHASES = ("refine",) + tuple(PIPELINE_PHASES)


class PhaseError(Exception):
    pass


@dataclass
class PipelinePlan:
    start: str
    stop: str
    phases_run: List[str] = field(default_factory=list)
    gates_applied: List[Gate] = field(default_factory=list)
    skipped_upstream: List[str] = field(default_factory=list)
    skipped_downstream: List[str] = field(default_factory=list)


def plan_entry(start: str, stop: Optional[str] = None) -> PipelinePlan:
    """Plan entering the pipeline at `start`, stopping after `stop` (default: just
    `start`). Applies only the run phases' gates; reports skipped phases explicitly."""
    phases = list(PIPELINE_PHASES)

    # A standalone front-end (refine) isn't a member of the linear sequence: it runs alone,
    # applies its own HARD-GATE, then hands off to the pipeline (which is all "downstream").
    if start in FRONT_END_PHASES and start not in phases:
        if stop is not None and stop != start:
            raise PhaseError(
                f"'{start}' is a front-end phase that runs standalone and hands off to "
                f"`spec`; it takes no --to target")
        return PipelinePlan(
            start=start, stop=start, phases_run=[start],
            gates_applied=[PHASE_GATES[start]] if start in PHASE_GATES else [],
            skipped_upstream=[], skipped_downstream=phases)

    if start not in phases:
        raise PhaseError(f"unknown phase '{start}'; one of {phases}")
    stop = stop if stop is not None else start
    if stop not in phases:
        raise PhaseError(f"unknown phase '{stop}'; one of {phases}")
    i, j = phases.index(start), phases.index(stop)
    if j < i:
        raise PhaseError(
            f"stop phase '{stop}' comes before start phase '{start}'"
        )

    run = phases[i:j + 1]
    return PipelinePlan(
        start=start,
        stop=stop,
        phases_run=run,
        gates_applied=[PHASE_GATES[p] for p in run if p in PHASE_GATES],
        skipped_upstream=phases[:i],
        skipped_downstream=phases[j + 1:],
    )


def render_entry(plan: PipelinePlan) -> str:
    lines = [
        f"# mokata · pipeline entry: {plan.start}"
        + (f" → {plan.stop}" if plan.stop != plan.start else ""),
        "",
        "Phases to run (each applies its own gate):",
    ]
    for p in plan.phases_run:
        gate = PHASE_GATES.get(p)
        if gate:
            lines.append(f"  - {p}  [gate: {gate.id} ({gate.kind})] — {gate.description}")
        else:
            lines.append(f"  - {p}  (no gate)")
    if plan.skipped_upstream:
        lines += [
            "",
            "Skipped upstream (NOT run, NOT forced — their gates do not apply):",
            "  " + ", ".join(plan.skipped_upstream),
        ]
    if plan.skipped_downstream:
        lines += ["", "Not reached (after your stop):",
                  "  " + ", ".join(plan.skipped_downstream)]
    return "\n".join(lines) + "\n"
