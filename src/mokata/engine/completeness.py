"""D2 — completeness gate: the provable-completeness blocker.

Emit is refused until every acceptance criterion maps to a test (RED-before-GREEN
traceability via D3). The gate reads the approved approach/handoff from the brainstorm
phase, so completeness is judged with the approved direction in view. It is wired as the
executable check for the existing `completeness_gate` phase (PHASE_GATES) — no parallel
pipeline.

The gate BLOCKS on any unmapped AC (and on an empty spec); it never silently passes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

from ..brainstorm import load_approved_approach
from ..pipeline import PHASE_GATES
from .acmapper import MapResult, map_acceptance_criteria
from .spec import Spec, TestRef

COMPLETENESS_PHASE = "completeness_gate"
# Metadata for this gate already lives in the pipeline; bind to it (don't duplicate).
GATE = PHASE_GATES[COMPLETENESS_PHASE]


@dataclass
class GateResult:
    passed: bool
    reason: str
    map_result: MapResult
    approach: Optional[str] = None
    approach_present: bool = False
    unmapped_ids: List[str] = field(default_factory=list)
    gate_id: str = GATE.id

    def render(self) -> str:
        head = "PASS" if self.passed else "BLOCK"
        lines = [
            f"[{head}] completeness gate — {self.reason}",
            f"  coverage: {self.map_result.coverage:.0%} "
            f"({len(self.map_result.mappings)} AC(s))",
        ]
        if self.unmapped_ids:
            lines.append(f"  unmapped: {', '.join(self.unmapped_ids)}")
        if self.approach_present:
            lines.append(f"  approved approach: {self.approach}")
        else:
            lines.append("  approved approach: none on record (brainstorm not run)")
        return "\n".join(lines)


def run_completeness_gate(spec: Spec, tests: List[TestRef],
                          handoff: Any = None, store: Any = None) -> GateResult:
    """Block emit unless every AC maps to a test. Reads the brainstorm handoff
    (directly, or from a state store) so the approved approach is in view."""
    if handoff is None and store is not None:
        handoff = load_approved_approach(store)
    approach_present = handoff is not None
    approach = handoff.approach.name if approach_present else None

    map_result = map_acceptance_criteria(spec, tests)

    if not spec.criteria:
        passed, reason = False, "no acceptance criteria — a spec must state at least one"
    elif map_result.unmapped_ids:
        passed = False
        reason = (f"{len(map_result.unmapped_ids)} acceptance criterion/criteria "
                  f"unmapped to any test")
    else:
        passed = True
        reason = (f"all {len(spec.criteria)} acceptance criteria map to tests "
                  "(RED-before-GREEN traceability)")

    return GateResult(
        passed=passed, reason=reason, map_result=map_result,
        approach=approach, approach_present=approach_present,
        unmapped_ids=list(map_result.unmapped_ids),
    )


# Wire the executable check to the existing pipeline phase (single pipeline, no parallel).
PHASE_GATE_CHECKS = {COMPLETENESS_PHASE: run_completeness_gate}
