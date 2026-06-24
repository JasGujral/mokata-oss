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
]
