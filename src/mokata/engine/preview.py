"""E7 — plan / dry-run preview.

Preview the pipeline's planned actions, gates, and file touches BEFORE executing — with
zero side effects (pure computation, no writes, no ledger). Built on the existing
PIPELINE_PHASES / PHASE_GATES and the L2 phase-slice planner; no parallel pipeline.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional

from ..pipeline import PHASE_GATES, plan_entry

# Per-phase planned action + the state files it WOULD touch (relative to .mokata/).
_PLAN = {
    "brainstorm": ("explore approaches; approve exactly one (HARD-GATE)",
                   ["state/approved_approach.json"]),
    "analysis": ("analyse the approved approach + codebase structure (read-only)", []),
    "strawman": ("draft a first-cut design mapping the approach to the ACs", []),
    "pre_mortem": ("derive risk probes from the approved approach", []),
    "probes": ("check the spec addresses each probe", []),
    "completeness_gate": ("block emit until every AC maps to a test", []),
    "emit": ("emit the spec — human-gated durable write", ["state/emitted_spec.json"]),
}


@dataclass
class PreviewItem:
    phase: str
    action: str
    gate_id: Optional[str]
    gate_kind: Optional[str]
    file_touches: List[str] = field(default_factory=list)


@dataclass
class Preview:
    items: List[PreviewItem] = field(default_factory=list)

    @property
    def all_file_touches(self) -> List[str]:
        return [t for i in self.items for t in i.file_touches]

    def render(self) -> str:
        lines = ["mokata dry-run preview (no side effects):"]
        for i in self.items:
            gate = f"  [gate: {i.gate_id} ({i.gate_kind})]" if i.gate_id else ""
            lines.append(f"  - {i.phase}: {i.action}{gate}")
            for t in i.file_touches:
                lines.append(f"      would write: {t}")
        touches = self.all_file_touches
        lines.append(f"  planned file touches: {len(touches)}")
        return "\n".join(lines)


def preview_pipeline(start: Optional[str] = None, stop: Optional[str] = None,
                     mokata_dir: str = ".mokata") -> Preview:
    """Build the plan for the phase slice (default: the whole pipeline). Read-only."""
    from ..brainstorm import PIPELINE_PHASES
    start = start or PIPELINE_PHASES[0]
    stop = stop or PIPELINE_PHASES[-1]
    plan = plan_entry(start, stop)

    items: List[PreviewItem] = []
    for phase in plan.phases_run:
        action, rel_touches = _PLAN.get(phase, ("(no planned action)", []))
        gate = PHASE_GATES.get(phase)
        items.append(PreviewItem(
            phase=phase,
            action=action,
            gate_id=gate.id if gate else None,
            gate_kind=gate.kind if gate else None,
            file_touches=[os.path.join(mokata_dir, t) for t in rel_touches],
        ))
    return Preview(items=items)
