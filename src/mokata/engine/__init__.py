"""mokata spec & correctness engine (Part D, completion) — D2/D3/D4.

Finishes the 7-phase pipeline so a spec cannot ship incomplete:
  - D3 AC-mapper: statically map each acceptance criterion to a test (traceability).
  - D4 pre-mortem + probes: derive adversarial risk probes from the approved approach.
  - D2 completeness gate: block emit until every AC maps to a test; reads the brainstorm
    handoff; wired as the existing `completeness_gate` phase's executable check.
"""

from .acmapper import ACMapping, MapResult, map_acceptance_criteria, scan_tests
from .compliance import ComplianceFinding, ComplianceResult, FeatureRef, spec_compliance_review
from .completeness import (
    COMPLETENESS_PHASE,
    PHASE_GATE_CHECKS,
    GateResult,
    run_completeness_gate,
)
from .phases import (
    Analysis,
    PhaseContext,
    PhaseRecord,
    PipelineRun,
    Strawman,
    run_pipeline,
)
from .premortem import PreMortemResult, Probe, derive_probes, pre_mortem
from .preview import Preview, PreviewItem, preview_pipeline
from .spec import AcceptanceCriterion, Spec, TestRef
from .ship import (
    LANDING_OPTIONS,
    FinishDecision,
    ShipReadiness,
    build_finish_summary,
    check_ship_readiness,
    record_finish_decision,
)
from .spec_gate import (
    SPEC_PERSISTED_GATE_ID,
    SPEC_PERSISTED_MESSAGE,
    SPEC_STATE_KEY,
    SpecGateResult,
    check_spec_persisted,
    load_emitted_spec,
)
from .spec_awareness import (
    SPEC_CONFLICT_KIND,
    SPEC_CORPUS_KEY,
    ChangeSet,
    GuardOutcome,
    SpecAwarenessReport,
    SpecConflict,
    check_change,
    expand_touch_set,
    guard_change,
    load_decisions,
    load_spec_corpus,
)

__all__ = [
    "AcceptanceCriterion",
    "Spec",
    "TestRef",
    "ACMapping",
    "MapResult",
    "map_acceptance_criteria",
    "scan_tests",
    "Probe",
    "PreMortemResult",
    "derive_probes",
    "pre_mortem",
    "GateResult",
    "run_completeness_gate",
    "PHASE_GATE_CHECKS",
    "COMPLETENESS_PHASE",
    # D1 — full pipeline
    "Analysis",
    "Strawman",
    "PhaseContext",
    "PhaseRecord",
    "PipelineRun",
    "run_pipeline",
    # D5 — spec compliance
    "FeatureRef",
    "ComplianceFinding",
    "ComplianceResult",
    "spec_compliance_review",
    # E7 — dry-run preview
    "Preview",
    "PreviewItem",
    "preview_pipeline",
    # Stage 32 — spec-persisted precondition
    "check_spec_persisted",
    "load_emitted_spec",
    "SpecGateResult",
    "SPEC_PERSISTED_GATE_ID",
    "SPEC_PERSISTED_MESSAGE",
    "SPEC_STATE_KEY",
    # Stage 34 — ship (finish) readiness + landing decision
    "check_ship_readiness",
    "record_finish_decision",
    "build_finish_summary",
    "ShipReadiness",
    "FinishDecision",
    "LANDING_OPTIONS",
    # Stage 37 — spec-awareness / regression guard
    "ChangeSet",
    "SpecConflict",
    "SpecAwarenessReport",
    "GuardOutcome",
    "check_change",
    "guard_change",
    "expand_touch_set",
    "load_spec_corpus",
    "load_decisions",
    "SPEC_CORPUS_KEY",
    "SPEC_CONFLICT_KIND",
]
