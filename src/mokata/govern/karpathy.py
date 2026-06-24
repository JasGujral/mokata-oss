"""G3 (hybrid) — Karpathy principles as gates.

The ENGINE implements each of the four checks (think-first, simplicity, surgical
diff-scope, success-criteria + verify); the RULES layer owns registration, per-config
toggle, and audit — reusing the `Gate` type (from skills), the PHASE_GATES registry
pattern, and the audit ledger. Each gate fires at its pipeline point and is toggleable
per profile/config (`settings.governance.karpathy.<id>`), defaulting on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Tuple

from ..skills import Gate

GOVERNANCE_KEY = "governance"
KARPATHY_KEY = "karpathy"


@dataclass
class KarpathyContext:
    """The signals the engine checks read. Built from a pipeline run's state."""
    has_plan: bool = False
    complexity: int = 0
    max_complexity: int = 5
    touched_files: int = 0
    max_scope: int = 10
    has_success_criteria: bool = False
    verified: bool = False


@dataclass
class KarpathyGate:
    gate: Gate                       # reuse the shared Gate type (id/description/kind)
    phase: str                       # pipeline point it fires at
    check: Callable[[KarpathyContext], Tuple[bool, str]]

    @property
    def id(self) -> str:
        return self.gate.id


@dataclass
class GateFire:
    gate_id: str
    phase: str
    fired: bool
    passed: Optional[bool]
    detail: str


# --- the four engine checks ----------------------------------------------------
def _think_first(ctx: KarpathyContext) -> Tuple[bool, str]:
    return (bool(ctx.has_plan),
            "a plan/approach exists before code" if ctx.has_plan
            else "no plan/approach recorded before code")


def _simplicity(ctx: KarpathyContext) -> Tuple[bool, str]:
    ok = ctx.complexity <= ctx.max_complexity
    return (ok, f"complexity {ctx.complexity} (cap {ctx.max_complexity})")


def _surgical_scope(ctx: KarpathyContext) -> Tuple[bool, str]:
    ok = ctx.touched_files <= ctx.max_scope
    return (ok, f"touched {ctx.touched_files} file(s) (cap {ctx.max_scope})")


def _verify(ctx: KarpathyContext) -> Tuple[bool, str]:
    ok = bool(ctx.has_success_criteria and ctx.verified)
    return (ok, f"success_criteria={ctx.has_success_criteria}, verified={ctx.verified}")


KARPATHY_GATES = {
    "think-first": KarpathyGate(
        Gate("think-first", "Think before coding: a plan/approach exists first.",
             "check"), "analysis", _think_first),
    "simplicity": KarpathyGate(
        Gate("simplicity", "Prefer the simplest approach that works.", "check"),
        "strawman", _simplicity),
    "surgical-scope": KarpathyGate(
        Gate("surgical-scope", "Change only what's needed; keep the diff scoped.",
             "check"), "emit", _surgical_scope),
    "verify": KarpathyGate(
        Gate("verify", "Define success criteria and verify against them.", "check"),
        "completeness_gate", _verify),
}


def karpathy_enabled(manifest: Any, gate_id: str) -> bool:
    gov = manifest.setting(GOVERNANCE_KEY, {}) or {}
    toggles = gov.get(KARPATHY_KEY, {}) or {}
    return bool(toggles.get(gate_id, True))      # default on


def run_karpathy_gate(gate_id: str, ctx: KarpathyContext,
                      ledger: Any = None) -> GateFire:
    g = KARPATHY_GATES[gate_id]
    passed, detail = g.check(ctx)
    if ledger is not None:
        ledger.record("karpathy_gate", gate=gate_id, phase=g.phase,
                      passed=passed, detail=detail)
    return GateFire(gate_id, g.phase, fired=True, passed=passed, detail=detail)


def run_karpathy_for_phase(phase: str, ctx: KarpathyContext, manifest: Any = None,
                           ledger: Any = None) -> List[GateFire]:
    """Fire the Karpathy gates registered at `phase`. Disabled gates (per config) do not
    fire and are not audited."""
    fires: List[GateFire] = []
    for gate_id, g in KARPATHY_GATES.items():
        if g.phase != phase:
            continue
        if manifest is not None and not karpathy_enabled(manifest, gate_id):
            continue
        fires.append(run_karpathy_gate(gate_id, ctx, ledger=ledger))
    return fires


def run_karpathy_pipeline(ctx: KarpathyContext, manifest: Any = None,
                          ledger: Any = None) -> List[GateFire]:
    """Fire every (enabled) Karpathy gate in pipeline-phase order."""
    from ..brainstorm import PIPELINE_PHASES
    fires: List[GateFire] = []
    for phase in PIPELINE_PHASES:
        fires.extend(run_karpathy_for_phase(phase, ctx, manifest=manifest,
                                            ledger=ledger))
    return fires
