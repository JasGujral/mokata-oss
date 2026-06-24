"""mokata governance, token & safety layer (Parts F/G/I, P1).

Integrates the engine with token governance, enforcement, and a full audit trail:
  - F1 TokenTracker · F2 jit_retrieve            (token & context governance)
  - E1 TddGuard (RED before GREEN)
  - G1 rules (4 tiers, line caps) · G2 taxonomy (rule/gate/hook) · G4 hooks (sync/async)
  - I1 secret scan (4 layers) · I2 WriteGate (universal human-gated writes)
  - I3 AuditLedger (append-only)
"""

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
from .learning import RulePromotion, RulesLearner
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
from .doctor import DoctorFinding, DoctorReport, diagnose
from .lifecycle import ResetPlan, ResetResult, plan_reset, reset_state
from .trust import (
    DEFAULT_TRUST,
    GATED_WRITE,
    PROPOSE_ONLY,
    READ_ONLY,
    TRUST_LEVELS,
    TrustPolicy,
    trust_for,
)
from .compress import OutputDensity, compress_output, compress_tool_output, density_enabled
from .gate import WRITE_KINDS, WriteGate, WriteOutcome, WriteRequest
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
from .tokens import TokenTracker, UsageEntry

__all__ = [
    # F1/F2
    "TokenTracker", "UsageEntry", "jit_retrieve", "RetrievalResult",
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
    # G3 — Karpathy gates (hybrid)
    "KARPATHY_GATES", "KarpathyGate", "KarpathyContext", "GateFire",
    "karpathy_enabled", "run_karpathy_gate", "run_karpathy_for_phase",
    "run_karpathy_pipeline",
    # G5 — rules learning
    "RulesLearner", "RulePromotion",
    # G6 — self-authoring skills
    "SkillDraft", "DocRequirement", "DocCheckResult", "AuthoringError",
    # I4 — lethal-trifecta gate
    "TrifectaState", "OutboundRequest", "OutboundDecision", "TrifectaGuard",
    "detect_trifecta",
    # I5 — reversibility
    "ReversibleStateStore", "UndoRecord", "RevertError", "gated_reversible_write",
    # I6 — resume / recovery
    "PipelineCheckpoint", "CHECKPOINT_PREFIX",
    # K3 — per-adapter trust dial
    "TrustPolicy", "trust_for", "READ_ONLY", "PROPOSE_ONLY", "GATED_WRITE",
    "TRUST_LEVELS", "DEFAULT_TRUST",
    # K5 — doctor
    "diagnose", "DoctorReport", "DoctorFinding",
    # K6 — lifecycle
    "plan_reset", "reset_state", "ResetPlan", "ResetResult",
]
