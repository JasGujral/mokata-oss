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
from ..refine import load_approved_refinements
from .acmapper import MapResult, map_acceptance_criteria
from .spec import Spec, TestRef

COMPLETENESS_PHASE = "completeness_gate"
# Metadata for this gate already lives in the pipeline; bind to it (don't duplicate).
GATE = PHASE_GATES[COMPLETENESS_PHASE]


# AP-SD — the named, non-blocking warning when the approved approach records no structured
# decisions[]. Adoption is gradual: the gate PASSES, but names the missing contract so a run is
# nudged toward it rather than silently proceeding without it (doc 85 §3 naming).
WARN_NO_DECISIONS = ("no structured decisions[] on the approved approach — the diff has no "
                     "machine-readable contract for review to verify against (adoption is gradual; "
                     "record decisions in brainstorm to enable it)")


# SPEC-STANDALONE-SILENT — THE sentence for "this spec has no upstream approved direction".
#
# Standalone spec is a SUPPORTED path (`skills/spec/SKILL.md`: "this command runs on its own — no
# upstream pipeline phase is required") and the gate below degrades clean into it. What was missing
# was never a gate; it was a WORD. `emit_spec` built the GateResult and discarded it, and the MCP
# `spec_emit` threaded only `gr.reason`, so NEITHER emit surface said anything at all — a supported
# path was indistinguishable from a silent failure, which is the P16 shape `awaiting.py` exists to
# kill on the wait path and this kills on the informational one.
#
# ONE constant, read by every surface (the R3/R4 single-source discipline). `render()` below reads
# it too, rather than keeping the second copy it used to carry — a wording that lives in two places
# is a wording that drifts, and this one now reaches a human's approval terminal.
STANDALONE_NOTE = (
    "STANDALONE: no approved brainstorm approach or refinement set is on record for this spec — "
    "it stands alone. That is a SUPPORTED path, not an error and not a failure. To give this spec "
    "an approved direction, run /mokata:brainstorm (or /mokata:refine), approve an approach, then "
    "emit.")


@dataclass
class GateResult:
    passed: bool
    reason: str
    map_result: MapResult
    approach: Optional[str] = None
    approach_present: bool = False
    unmapped_ids: List[str] = field(default_factory=list)
    refinements_present: bool = False
    refinements: Optional[str] = None         # Stage 26 — approved refinement set label
    gate_id: str = GATE.id
    # AP-SD — non-blocking advisories that do NOT flip `passed` (e.g. the approved approach carries
    # no decisions[]). Empty on the happy path; named so a warning is legible, never a silent gap.
    warnings: List[str] = field(default_factory=list)

    @property
    def standalone(self) -> bool:
        """SPEC-STANDALONE-SILENT — this spec has NO approved direction behind it (neither a
        brainstorm approach nor a refinement set). Exactly the condition `render()`'s else-branch
        has always described; named so every surface asks the gate instead of re-deriving it."""
        return not (self.approach_present or self.refinements_present)

    def render(self) -> str:
        head = "PASS" if self.passed else "BLOCK"
        lines = [
            f"[{head}] completeness gate — {self.reason}",
            f"  coverage: {self.map_result.coverage:.0%} "
            f"({len(self.map_result.mappings)} AC(s))",
        ]
        if self.unmapped_ids:
            lines.append(f"  unmapped: {', '.join(self.unmapped_ids)}")
        for w in self.warnings:
            lines.append(f"  ⚠ {w}")
        if self.approach_present:
            lines.append(f"  approved approach: {self.approach}")
        elif self.refinements_present:
            lines.append(f"  approved refinements: {self.refinements}")
        else:
            # The SHARED constant, not a second copy of it (SPEC-STANDALONE-SILENT).
            lines.append(f"  {STANDALONE_NOTE}")
        if not self.passed:
            # Stage 54c — every block names the single next action that clears it.
            from ..legibility import unblock_hint
            action = unblock_hint(self.gate_id)
            if action:
                lines.append(f"  → to unblock: {action}")
        return "\n".join(lines)

    def verdict(self, ascii_only: bool = False) -> str:
        """Stage 54c — the shared one-line gate verdict (read-only; no re-derivation)."""
        from ..legibility import verdict
        return verdict(self, ascii_only=ascii_only)


def run_completeness_gate(spec: Spec, tests: List[TestRef],
                          handoff: Any = None, store: Any = None) -> GateResult:
    """Block emit unless every AC maps to a test. Reads the brainstorm handoff
    (directly, or from a state store) so the approved approach is in view."""
    refinements = None
    if handoff is None and store is not None:
        handoff = load_approved_approach(store)
        if handoff is None:
            # Stage 26 — the refine front-end is the other approved direction the gate
            # reads, the same way it reads a brainstorm approach.
            refinements = load_approved_refinements(store)
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

    # AP-SD — WARN (never block) when an approved approach carries no structured decisions[]. Scoped
    # to when an approach is actually present: a standalone/refine run has no approach to hang
    # decisions on, so it is not nagged. The verdict stays exactly as computed above.
    warnings: List[str] = []
    if approach_present and not getattr(handoff.approach, "decisions", None):
        warnings.append(WARN_NO_DECISIONS)

    return GateResult(
        passed=passed, reason=reason, map_result=map_result,
        approach=approach, approach_present=approach_present,
        unmapped_ids=list(map_result.unmapped_ids),
        refinements_present=refinements is not None,
        refinements=refinements.label if refinements is not None else None,
        warnings=warnings,
    )


def standalone_note(result: GateResult) -> str:
    """SPEC-STANDALONE-SILENT — the informational line for `result`, or "" when a direction IS on
    record. THE shared builder both emit surfaces call; no tool writes this sentence itself.

    INFORMATIONAL, NOT A GATE. It is derived from a verdict that has already been decided and it
    cannot change one: a standalone spec still passes on AC→test mapping alone and still emits.
    Turning "no brainstorm ran" into a refusal would break the standalone path `skills/spec`
    documents as supported — the defect was silence, and the fix for silence is a sentence.

    Pure, total and read-only: it reads two booleans off the result and returns a module constant.
    """
    return "" if not result.standalone else STANDALONE_NOTE


# Wire the executable check to the existing pipeline phase (single pipeline, no parallel).
PHASE_GATE_CHECKS = {COMPLETENESS_PHASE: run_completeness_gate}
