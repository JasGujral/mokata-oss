"""mokata governance, token & safety layer (Parts F/G/I, P1).

Integrates the engine with token governance, enforcement, and a full audit trail:
  - F1 TokenTracker · F2 jit_retrieve            (token & context governance)
  - E1 TddGuard (RED before GREEN)
  - G1 rules (4 tiers, line caps) · G2 taxonomy (rule/gate/hook) · G4 hooks (sync/async)
  - I1 secret scan (4 layers) · I2 WriteGate (universal human-gated writes)
  - I3 AuditLedger (append-only)

LAZY BY DESIGN (0.0.25 Lane HB, HOOK-BUDGET). Every name in ``__all__`` is importable from
``mokata.govern`` exactly as before; the submodule that defines it is imported on FIRST USE (PEP 562
``__getattr__``) instead of when the package loads. Python runs this file for EVERY
``mokata.govern.<sub>`` import, so the eager form made the per-tool-call ``secret-guard`` hook load
karpathy, doctor, the memory package and the rest (~80 ms measured) just to reach ``secrets.scan``.
The eager import list stays VERBATIM under ``TYPE_CHECKING`` so type checkers and the repo's AST
walkers (``tests/_writegraph.py``, ``tests/test_gate_reachability.py``) still see every re-export;
``tests/test_hb_hook_budget.py`` pins that list, ``_EXPORTS`` and ``__all__`` to each other.
"""

from typing import TYPE_CHECKING, Any, Dict, Tuple

if TYPE_CHECKING:  # the eager list, for static readers only — see the module docstring
    from .authoring import AuthoringError, DocCheckResult, DocRequirement, SkillDraft
    from .budget import BudgetReport, SavingsEvent, SavingsTracker, budget_statusline
    from .karpathy import (
        KARPATHY_GATES,
        GateFire,
        KarpathyContext,
        KarpathyGate,
        karpathy_enabled,
        run_karpathy_for_phase,
        run_karpathy_gate,
        run_karpathy_pipeline,
    )
    from .learning import RulePromotion, RulesLearner, learn_from_ledger
    from .outbound import gate_outbound_publish, looks_private
    from .resume import CHECKPOINT_PREFIX, PipelineCheckpoint
    from .revert import (
        RevertError,
        ReversibleStateStore,
        UndoRecord,
        gated_reversible_write,
    )
    from .trifecta import (
        OutboundDecision,
        OutboundRequest,
        TrifectaGuard,
        TrifectaState,
        detect_trifecta,
    )
    from .cache import (
        CachePrefix,
        build_stable_prefix,
        is_cache_stable,
        prefix_fingerprint,
        stable_prefix_for,
    )
    from .compaction import Handback, cap_summary
    from .doctor import (
        CoverageMatrix,
        CoverageRow,
        DoctorFinding,
        DoctorReport,
        coverage_matrix,
        diagnose,
        hook_resolution_findings,
    )
    from .lifecycle import ResetPlan, ResetResult, plan_reset, reset_state
    from .trust import (
        CLI_SURFACE,
        DEFAULT_TRUST,
        GATED_WRITE,
        MCP_SURFACE,
        PROPOSE_ONLY,
        READ_ONLY,
        REFUSED_READ_ONLY,
        SURFACES,
        TRUST_LEVELS,
        TrustPolicy,
        WritePolicy,
        trust_for,
    )
    from .compress import OutputDensity, compress_output, compress_tool_output, density_enabled
    from .deviation import (
        DEVIATION_KIND,
        DEVIATION_TARGETS,
        DeviationGate,
        DeviationOutcome,
        DeviationRequest,
        render_deviation,
    )
    from .gate import WRITE_KINDS, WriteGate, WriteOutcome, WriteRequest
    # TM.S8 imports `..memory.item`, which loads the memory package (→ migrate → govern.WriteGate),
    # so it must come AFTER `.gate` binds WriteGate to avoid a partially-initialized-module cycle.
    from .enforce import (
        RULE_BLOCK_KIND,
        RULE_OVERRIDE_KIND,
        EnforcementGate,
        EnforcementOutcome,
        EnforcementVerdict,
        PendingAction,
        RuleViolation,
        evaluate as evaluate_enforcement,
        in_scope_rules,
        render_verdict,
    )
    from .hooks import HookResult, run_async_hook, run_sync_hook
    from .ledger import AuditLedger
    from .retrieval import RetrievalResult, jit_retrieve
    from .rules import (
        ADVISORY,
        ALWAYS_ON_RULES,
        BLOCKING,
        CAPS,
        EVENT,
        RULE_TAXONOMY,
        RULE_TIERS,
        RuleSet,
        always_on_rules,
        classify,
        load_rules,
        mechanism_for,
        validate_caps,
    )
    from .secrets import Finding, has_secrets, scan
    from .tdd import GATE_ID as TDD_GATE_ID
    from .tdd import RedBeforeGreenError, TddGuard
    from .tokens import (
        CALIBRATION_KIND,
        CALIBRATION_MARGIN_RATIO,
        CalibrationRecord,
        TokenTracker,
        UsageEntry,
        calibration_record,
        log_bootstrap_calibration,
        log_calibration,
    )
    from .calibration import (
        CALIBRATION_MIN_ESTIMATE_TOKENS,
        CALIBRATION_STATS_KEY,
        CalibrationOutcome,
        ModelCalibration,
        calibration_of,
        fold,
        merged_stats,
        observe,
    )

#: public name -> (submodule, attribute). Resolved on first access by `__getattr__` below.
_EXPORTS: Dict[str, Tuple[str, str]] = {
    "AuthoringError": ("authoring", "AuthoringError"),
    "DocCheckResult": ("authoring", "DocCheckResult"),
    "DocRequirement": ("authoring", "DocRequirement"),
    "SkillDraft": ("authoring", "SkillDraft"),
    "BudgetReport": ("budget", "BudgetReport"),
    "SavingsEvent": ("budget", "SavingsEvent"),
    "SavingsTracker": ("budget", "SavingsTracker"),
    "budget_statusline": ("budget", "budget_statusline"),
    "KARPATHY_GATES": ("karpathy", "KARPATHY_GATES"),
    "GateFire": ("karpathy", "GateFire"),
    "KarpathyContext": ("karpathy", "KarpathyContext"),
    "KarpathyGate": ("karpathy", "KarpathyGate"),
    "karpathy_enabled": ("karpathy", "karpathy_enabled"),
    "run_karpathy_for_phase": ("karpathy", "run_karpathy_for_phase"),
    "run_karpathy_gate": ("karpathy", "run_karpathy_gate"),
    "run_karpathy_pipeline": ("karpathy", "run_karpathy_pipeline"),
    "RulePromotion": ("learning", "RulePromotion"),
    "RulesLearner": ("learning", "RulesLearner"),
    "learn_from_ledger": ("learning", "learn_from_ledger"),
    "gate_outbound_publish": ("outbound", "gate_outbound_publish"),
    "looks_private": ("outbound", "looks_private"),
    "CHECKPOINT_PREFIX": ("resume", "CHECKPOINT_PREFIX"),
    "PipelineCheckpoint": ("resume", "PipelineCheckpoint"),
    "RevertError": ("revert", "RevertError"),
    "ReversibleStateStore": ("revert", "ReversibleStateStore"),
    "UndoRecord": ("revert", "UndoRecord"),
    "gated_reversible_write": ("revert", "gated_reversible_write"),
    "OutboundDecision": ("trifecta", "OutboundDecision"),
    "OutboundRequest": ("trifecta", "OutboundRequest"),
    "TrifectaGuard": ("trifecta", "TrifectaGuard"),
    "TrifectaState": ("trifecta", "TrifectaState"),
    "detect_trifecta": ("trifecta", "detect_trifecta"),
    "CachePrefix": ("cache", "CachePrefix"),
    "build_stable_prefix": ("cache", "build_stable_prefix"),
    "is_cache_stable": ("cache", "is_cache_stable"),
    "prefix_fingerprint": ("cache", "prefix_fingerprint"),
    "stable_prefix_for": ("cache", "stable_prefix_for"),
    "Handback": ("compaction", "Handback"),
    "cap_summary": ("compaction", "cap_summary"),
    "CoverageMatrix": ("doctor", "CoverageMatrix"),
    "CoverageRow": ("doctor", "CoverageRow"),
    "DoctorFinding": ("doctor", "DoctorFinding"),
    "DoctorReport": ("doctor", "DoctorReport"),
    "coverage_matrix": ("doctor", "coverage_matrix"),
    "diagnose": ("doctor", "diagnose"),
    "hook_resolution_findings": ("doctor", "hook_resolution_findings"),
    "ResetPlan": ("lifecycle", "ResetPlan"),
    "ResetResult": ("lifecycle", "ResetResult"),
    "plan_reset": ("lifecycle", "plan_reset"),
    "reset_state": ("lifecycle", "reset_state"),
    "CLI_SURFACE": ("trust", "CLI_SURFACE"),
    "DEFAULT_TRUST": ("trust", "DEFAULT_TRUST"),
    "GATED_WRITE": ("trust", "GATED_WRITE"),
    "MCP_SURFACE": ("trust", "MCP_SURFACE"),
    "PROPOSE_ONLY": ("trust", "PROPOSE_ONLY"),
    "READ_ONLY": ("trust", "READ_ONLY"),
    "REFUSED_READ_ONLY": ("trust", "REFUSED_READ_ONLY"),
    "SURFACES": ("trust", "SURFACES"),
    "TRUST_LEVELS": ("trust", "TRUST_LEVELS"),
    "TrustPolicy": ("trust", "TrustPolicy"),
    "WritePolicy": ("trust", "WritePolicy"),
    "trust_for": ("trust", "trust_for"),
    "OutputDensity": ("compress", "OutputDensity"),
    "compress_output": ("compress", "compress_output"),
    "compress_tool_output": ("compress", "compress_tool_output"),
    "density_enabled": ("compress", "density_enabled"),
    "DEVIATION_KIND": ("deviation", "DEVIATION_KIND"),
    "DEVIATION_TARGETS": ("deviation", "DEVIATION_TARGETS"),
    "DeviationGate": ("deviation", "DeviationGate"),
    "DeviationOutcome": ("deviation", "DeviationOutcome"),
    "DeviationRequest": ("deviation", "DeviationRequest"),
    "render_deviation": ("deviation", "render_deviation"),
    "WRITE_KINDS": ("gate", "WRITE_KINDS"),
    "WriteGate": ("gate", "WriteGate"),
    "WriteOutcome": ("gate", "WriteOutcome"),
    "WriteRequest": ("gate", "WriteRequest"),
    "RULE_BLOCK_KIND": ("enforce", "RULE_BLOCK_KIND"),
    "RULE_OVERRIDE_KIND": ("enforce", "RULE_OVERRIDE_KIND"),
    "EnforcementGate": ("enforce", "EnforcementGate"),
    "EnforcementOutcome": ("enforce", "EnforcementOutcome"),
    "EnforcementVerdict": ("enforce", "EnforcementVerdict"),
    "PendingAction": ("enforce", "PendingAction"),
    "RuleViolation": ("enforce", "RuleViolation"),
    "evaluate_enforcement": ("enforce", "evaluate"),
    "in_scope_rules": ("enforce", "in_scope_rules"),
    "render_verdict": ("enforce", "render_verdict"),
    "HookResult": ("hooks", "HookResult"),
    "run_async_hook": ("hooks", "run_async_hook"),
    "run_sync_hook": ("hooks", "run_sync_hook"),
    "AuditLedger": ("ledger", "AuditLedger"),
    "RetrievalResult": ("retrieval", "RetrievalResult"),
    "jit_retrieve": ("retrieval", "jit_retrieve"),
    "ADVISORY": ("rules", "ADVISORY"),
    "ALWAYS_ON_RULES": ("rules", "ALWAYS_ON_RULES"),
    "BLOCKING": ("rules", "BLOCKING"),
    "CAPS": ("rules", "CAPS"),
    "EVENT": ("rules", "EVENT"),
    "RULE_TAXONOMY": ("rules", "RULE_TAXONOMY"),
    "RULE_TIERS": ("rules", "RULE_TIERS"),
    "RuleSet": ("rules", "RuleSet"),
    "always_on_rules": ("rules", "always_on_rules"),
    "classify": ("rules", "classify"),
    "load_rules": ("rules", "load_rules"),
    "mechanism_for": ("rules", "mechanism_for"),
    "validate_caps": ("rules", "validate_caps"),
    "Finding": ("secrets", "Finding"),
    "has_secrets": ("secrets", "has_secrets"),
    "scan": ("secrets", "scan"),
    "TDD_GATE_ID": ("tdd", "GATE_ID"),
    "RedBeforeGreenError": ("tdd", "RedBeforeGreenError"),
    "TddGuard": ("tdd", "TddGuard"),
    "CALIBRATION_KIND": ("tokens", "CALIBRATION_KIND"),
    "CALIBRATION_MARGIN_RATIO": ("tokens", "CALIBRATION_MARGIN_RATIO"),
    "CalibrationRecord": ("tokens", "CalibrationRecord"),
    "TokenTracker": ("tokens", "TokenTracker"),
    "UsageEntry": ("tokens", "UsageEntry"),
    "calibration_record": ("tokens", "calibration_record"),
    "log_bootstrap_calibration": ("tokens", "log_bootstrap_calibration"),
    "log_calibration": ("tokens", "log_calibration"),
    "CALIBRATION_MIN_ESTIMATE_TOKENS": ("calibration", "CALIBRATION_MIN_ESTIMATE_TOKENS"),
    "CALIBRATION_STATS_KEY": ("calibration", "CALIBRATION_STATS_KEY"),
    "CalibrationOutcome": ("calibration", "CalibrationOutcome"),
    "ModelCalibration": ("calibration", "ModelCalibration"),
    "calibration_of": ("calibration", "calibration_of"),
    "fold": ("calibration", "fold"),
    "merged_stats": ("calibration", "merged_stats"),
    "observe": ("calibration", "observe"),
}


def __getattr__(name: str) -> Any:
    """PEP 562: import the defining submodule on first use and cache the name on the package."""
    from importlib import import_module
    if name not in _EXPORTS and not name.startswith("__"):
        # a SUBMODULE reached as an attribute (`pkg.sub` after `import pkg`): the eager form
        # imported it as a side effect, so it is imported here — anything else is a real miss
        try:
            return import_module(f".{name}", __name__)
        except ModuleNotFoundError as exc:
            if exc.name != f"{__name__}.{name}":
                raise
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    sub, attr = _EXPORTS[name]
    value = getattr(import_module(f".{sub}", __name__), attr)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_EXPORTS))


__all__ = [
    # F1/F2
    "TokenTracker", "UsageEntry", "jit_retrieve", "RetrievalResult",
    # R11 — token-estimate calibration logging
    "CalibrationRecord", "calibration_record", "log_calibration",
    "log_bootstrap_calibration", "CALIBRATION_KIND", "CALIBRATION_MARGIN_RATIO",
    # H-2 — the half R11 wired and nothing closed: a REAL actual, read from the transcript
    "ModelCalibration", "CalibrationOutcome", "observe", "fold", "merged_stats",
    "calibration_of", "CALIBRATION_STATS_KEY", "CALIBRATION_MIN_ESTIMATE_TOKENS",
    # F3 — handback cap
    "Handback", "cap_summary",
    # F4 — output density
    "OutputDensity", "compress_output", "compress_tool_output", "density_enabled",
    # F5 — savings / budget
    "SavingsTracker", "SavingsEvent", "BudgetReport", "budget_statusline",
    # F6 — prompt-cache awareness
    "CachePrefix", "build_stable_prefix", "prefix_fingerprint", "is_cache_stable",
    "stable_prefix_for",
    # E
    "TddGuard", "RedBeforeGreenError", "TDD_GATE_ID",
    # G
    "RULE_TIERS", "CAPS", "RuleSet", "always_on_rules", "load_rules", "validate_caps",
    "ALWAYS_ON_RULES", "ADVISORY", "BLOCKING", "EVENT", "classify", "mechanism_for",
    "RULE_TAXONOMY", "run_sync_hook", "run_async_hook", "HookResult",
    # I
    "scan", "has_secrets", "Finding", "WriteGate", "WriteRequest", "WriteOutcome",
    "WRITE_KINDS", "AuditLedger",
    # TM.S8 — in-run hard-rule enforcement (doc 62 §4, Sentinel)
    "PendingAction", "RuleViolation", "EnforcementVerdict", "EnforcementOutcome",
    "EnforcementGate", "evaluate_enforcement", "in_scope_rules", "render_verdict",
    "RULE_OVERRIDE_KIND", "RULE_BLOCK_KIND",
    # G3 — Karpathy gates (hybrid)
    "KARPATHY_GATES", "KarpathyGate", "KarpathyContext", "GateFire",
    "karpathy_enabled", "run_karpathy_gate", "run_karpathy_for_phase",
    "run_karpathy_pipeline",
    # G5 — rules learning
    "RulesLearner", "RulePromotion", "learn_from_ledger",
    # G6 — self-authoring skills
    "SkillDraft", "DocRequirement", "DocCheckResult", "AuthoringError",
    # I4 — lethal-trifecta gate
    "TrifectaState", "OutboundRequest", "OutboundDecision", "TrifectaGuard",
    "detect_trifecta", "gate_outbound_publish", "looks_private",
    # I5 — reversibility
    "ReversibleStateStore", "UndoRecord", "RevertError", "gated_reversible_write",
    # I6 — resume / recovery
    "PipelineCheckpoint", "CHECKPOINT_PREFIX",
    # K3 — per-adapter trust dial
    "TrustPolicy", "WritePolicy", "trust_for", "READ_ONLY", "PROPOSE_ONLY", "GATED_WRITE",
    "MCP_SURFACE", "CLI_SURFACE", "SURFACES", "REFUSED_READ_ONLY",
    "TRUST_LEVELS", "DEFAULT_TRUST",
    # K5 — doctor
    "diagnose", "DoctorReport", "DoctorFinding",
    # HOOK-RESOLVE — the wired-hook resolvability check (gates wired vs gates FIRING)
    "hook_resolution_findings",
    # R13 — doctor coverage matrix
    "coverage_matrix", "CoverageMatrix", "CoverageRow",
    # K6 — lifecycle
    "plan_reset", "reset_state", "ResetPlan", "ResetResult",
    # Stage 31 — plan-adherence deviation gate
    "DeviationGate", "DeviationRequest", "DeviationOutcome", "render_deviation",
    "DEVIATION_KIND", "DEVIATION_TARGETS",
]
