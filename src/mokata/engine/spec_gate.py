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

# D5 — the spec IS there; it just could not be parsed. Telling that user "no saved spec — draft and
# emit it first" is a lie with a cost: it sends them to rewrite a spec they already have, and the
# real fault (a torn write, a version skew, a hand-edit) goes uninvestigated and unfixed. The gate
# still BLOCKS — fail-closed is right, an unreadable spec is not a spec — but for the TRUE reason.
SPEC_MALFORMED_MESSAGE = (
    "a saved spec EXISTS but could not be read (corrupt/unparseable state) — this is NOT 'no spec': "
    "do not rewrite it blind. Run `mokata doctor`, then re-emit it (/mokata:spec) to replace the "
    "unreadable copy."
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

    def verdict(self, ascii_only: bool = False) -> str:
        """Stage 54c — the shared one-line gate verdict (read-only; no re-derivation)."""
        from ..legibility import verdict
        return verdict(self, ascii_only=ascii_only)


def read_emitted_spec(store: Any) -> "tuple[Optional[Spec], bool]":
    """`(spec, malformed)` — the persisted spec, and whether one is PRESENT BUT UNREADABLE.

    D5: the two failures a single `None` used to collapse into one. "Absent" and "corrupt" are
    different facts with different remediations (emit one vs. repair/replace the one you have), and
    a gate that cannot tell them apart cannot tell the user the truth about either."""
    if store is None:
        return None, False
    try:
        data = store.read(SPEC_STATE_KEY)
    except (OSError, AttributeError):
        # The state artifact could not be READ at all (a permission/IO fault, or a store double
        # with no `read`). Nothing is known about the spec — treat it as absent, not malformed.
        return None, False
    if not data:
        return None, False
    try:
        return Spec.from_dict(data), False
    except (KeyError, TypeError, ValueError, AttributeError):
        # A spec IS persisted; its SHAPE is wrong (a missing AC `id`, a non-list `criteria`, a
        # non-dict payload). Present-but-unparseable — the caller must say so, not say "absent".
        return None, True


def load_emitted_spec(store: Any) -> Optional[Spec]:
    """The persisted spec from the state store, or None when absent/unreadable. (Unchanged
    contract — every existing caller keeps its `Optional[Spec]`; `read_emitted_spec` is the one
    that also reports WHICH of the two it was.)"""
    return read_emitted_spec(store)[0]


def check_spec_persisted(store: Any, ledger: Any = None,
                         phase: str = "develop") -> SpecGateResult:
    """Block implementation unless a persisted spec with ≥1 acceptance criterion exists.

    `store` is the pipeline state surface (`surface.state`); None (uninitialized repo) reads
    as "no spec" → blocked. The decision is logged to the audit ledger when one is wired.

    D5 — still FAILS CLOSED on every path (an unreadable spec is not a spec, and implementation
    does not proceed on one). What changed is the REASON: a malformed spec is reported as malformed
    instead of as absent."""
    spec, malformed = read_emitted_spec(store)
    ac_count = len(spec.criteria) if spec is not None else 0

    if malformed:
        passed, reason = False, SPEC_MALFORMED_MESSAGE
    elif spec is None or ac_count < 1:
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
