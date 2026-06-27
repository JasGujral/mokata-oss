"""Stage 32 — the `spec-persisted` precondition for implementation.

"Spec written AND saved before implementation" is the headline promise; this makes it an
*explicit, enforced* precondition rather than a transitive consequence of the test gate. The
`emit` phase persists the spec to `state/emitted_spec.json` (human-gated, only after the
completeness gate passes). Before `develop`/`test` proceed, this gate requires that file to
exist AND carry ≥1 acceptance criterion — fired AHEAD of `no-code-without-failing-test`. On a
block it gives a clear, actionable next step. The decision (block or pass) is a gate decision,
so it's logged to the audit ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .spec import Spec

# The StateStore key the `emit` phase writes the spec under (see engine/phases.py).
SPEC_STATE_KEY = "emitted_spec"

SPEC_PERSISTED_GATE_ID = "spec-persisted"
SPEC_PERSISTED_MESSAGE = (
    "no saved spec — draft and emit it first (/mokata:spec); the completeness gate must "
    "pass before implementation."
)


@dataclass
class SpecGateResult:
    passed: bool
    reason: str
    ac_count: int = 0
    gate_id: str = SPEC_PERSISTED_GATE_ID

    def render(self) -> str:
        head = "PASS" if self.passed else "BLOCK"
        return f"[{head}] {self.gate_id} — {self.reason}"


def load_emitted_spec(store: Any) -> Optional[Spec]:
    """The persisted spec from the state store, or None when absent/unreadable."""
    if store is None:
        return None
    try:
        data = store.read(SPEC_STATE_KEY)
    except Exception:
        return None
    if not data:
        return None
    try:
        return Spec.from_dict(data)
    except Exception:
        return None


def check_spec_persisted(store: Any, ledger: Any = None,
                         phase: str = "develop") -> SpecGateResult:
    """Block implementation unless a persisted spec with ≥1 acceptance criterion exists.

    `store` is the pipeline state surface (`surface.state`); None (uninitialized repo) reads
    as "no spec" → blocked. The decision is logged to the audit ledger when one is wired."""
    spec = load_emitted_spec(store)
    ac_count = len(spec.criteria) if spec is not None else 0

    if spec is None or ac_count < 1:
        # absent, empty, or AC-less spec — all block with the same actionable message
        passed, reason = False, SPEC_PERSISTED_MESSAGE
    else:
        passed = True
        reason = (f"emitted spec present with {ac_count} acceptance "
                  f"criterion{'' if ac_count == 1 else 'a'}")

    if ledger is not None:
        ledger.record("gate", gate=SPEC_PERSISTED_GATE_ID, phase=phase,
                      decision="passed" if passed else "blocked",
                      reason=reason, ac_count=ac_count)
    return SpecGateResult(passed=passed, reason=reason, ac_count=ac_count)
