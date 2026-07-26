"""SI-DEV — `spec amend`: the FORCED PHASE REGRESSION that is the only road back into scope.

An out-of-scope write is an exit-2 (see `gate_hook` + `spec_scope`). This module is what the block
tells you to run — and it is deliberately NOT a text edit to the spec. Scope enters through the
gate, never around it (P4), so amending is a *phase transition*: the run regresses out of develop
back to SPEC, development writes stay blocked for the duration, and the new scope has to re-earn
every gate the original spec earned.

    develop  --(an out-of-scope write, or a deliberate `spec amend`)-->  SPEC
       ^                                                                  |
       |                 completeness -> impact (if scope widened)        |
       |                 -> HUMAN approval (SI.3) -> gated commit         |
       +---------------- vN+1 persisted, vN superseded, diff ledgered ----+
                         ...and RED owed for every NEW acceptance criterion

Why a phase marker at all
-------------------------
`PIPELINE_PHASES` is the SEVEN BRAINSTORM PHASES — brainstorm, analysis, strawman, pre_mortem,
probes, completeness_gate, emit. **There is no `develop` phase.** So "this run has regressed out of
develop" had nowhere to live in the existing checkpoint vocabulary. `spec_amend__<run_id>` (see
`spec_scope`) is the minimal persisted marker that says it, and the hook reads it. The checkpoint
itself is rolled back past `completeness_gate` + `emit` (`PipelineCheckpoint.regress`), which is
what makes the resume a P17 resume — from the last PASSED gate, not from scratch.

The gates are RE-INVOKED, never reimplemented
---------------------------------------------
    completeness   engine/completeness.py:run_completeness_gate   (every AC, old and new, maps to a test)
    blast radius   brainstorm_impact.py:compute_impact            (Lens-1 — only when scope WIDENS)
    the deviation  govern/deviation.py:DeviationGate(target=SCOPE) (a scope change IS a plan change)
    consent        approval.py propose/approve/redeem              (SI.3 — the human mints it, out-of-band)
    the write      govern WriteGate + engine/emit.py:spec_commit   (SI-DEV.0's one committer)

The consent is SI.3-grade on purpose: an amendment is precisely the moment a model would most like
to approve its own scope change, and "a user said 'oh also add X'" is authorization to ASK, not to
build (P14). There is no parameter on this path that reaches a commit — only a record a human wrote
in a terminal the model does not own.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..govern.deviation import SCOPE as DEVIATION_SCOPE
from ..govern.deviation import DeviationGate, DeviationRequest
from ..govern.resume import PipelineCheckpoint
from ..spec_scope import (STATUS_CLOSED, STATUS_OPEN, AmendRecord, amend_from_state,
                          amend_key, archive_key)
from .completeness import run_completeness_gate
from .emit import EMIT_KIND, EMIT_TARGET, preview_content, spec_commit, spec_version
from .spec import Spec, TestRef
from .spec_gate import SPEC_STATE_KEY, load_emitted_spec

# The WriteGate / trust identity of an amendment. Distinct from `spec_emit`: a team may well want to
# let a tool emit the FIRST spec and still hold a scope CHANGE to a stricter dial (K3/SI.4).
AMEND_TOOL = "spec_amend"

# The ledger kind carrying the diff. `mokata audit` shows: what scope changed, from which version to
# which, who approved it, and which archived spec it superseded — forever.
LEDGER_KIND = "spec_amend"

# The gates an amendment must re-earn. Everything BEFORE these stays passed (P17).
REGRESSED_PHASES = ("completeness_gate", "emit")


@dataclass(frozen=True)
class SpecDiff:
    """What changed between vN and vN+1 — the thing the ledger records and a human reviews."""

    added_criteria: Tuple[str, ...] = ()
    removed_criteria: Tuple[str, ...] = ()
    authorized_added: Tuple[str, ...] = ()
    authorized_removed: Tuple[str, ...] = ()
    undeferred: Tuple[str, ...] = ()          # deferred items the amendment RELEASED
    newly_deferred: Tuple[str, ...] = ()

    @property
    def widens_scope(self) -> bool:
        """Does this amendment let the build TOUCH MORE than it could before? Only then is the
        blast radius re-computed — narrowing a spec cannot widen its impact, and re-running the
        lens on every amendment would be a cost with no answer in it."""
        return bool(self.authorized_added or self.undeferred)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "added_criteria": list(self.added_criteria),
            "removed_criteria": list(self.removed_criteria),
            "authorized_added": list(self.authorized_added),
            "authorized_removed": list(self.authorized_removed),
            "undeferred": list(self.undeferred),
            "newly_deferred": list(self.newly_deferred),
        }

    def render(self) -> str:
        lines: List[str] = []
        for label, items in (("+ acceptance criteria", self.added_criteria),
                             ("- acceptance criteria", self.removed_criteria),
                             ("+ authorized paths", self.authorized_added),
                             ("- authorized paths", self.authorized_removed),
                             ("UN-DEFERRED (now in scope)", self.undeferred),
                             ("newly deferred", self.newly_deferred)):
            if items:
                lines.append(f"  {label}: {', '.join(items)}")
        return "\n".join(lines) or "  (no scope change)"


@dataclass(frozen=True)
class AmendOutcome:
    committed: bool
    reason: str
    gate: str = ""
    version: int = 0
    unmapped: Tuple[str, ...] = ()
    red_owed: Tuple[str, ...] = ()
    diff: Optional[SpecDiff] = None
    impact: Optional[Any] = None
    scope_widened: bool = False

    @property
    def blocked_by_completeness(self) -> bool:
        return not self.committed and self.gate == "completeness"


def _ids(scope: Any, attr: str) -> Tuple[str, ...]:
    if scope is None:
        return ()
    return tuple(getattr(scope, attr, ()) or ())


def diff_specs(old: Spec, new: Spec) -> SpecDiff:
    """The scope delta. Deferred items are compared by ID, so re-wording an item is not a release —
    only actually dropping it from the deferred list is."""
    old_acs, new_acs = set(old.ac_ids), set(new.ac_ids)
    old_auth = set(_ids(old.scope, "authorized"))
    new_auth = set(_ids(new.scope, "authorized"))
    old_def = {d.id: d for d in _ids(old.scope, "deferred")}
    new_def = {d.id: d for d in _ids(new.scope, "deferred")}

    return SpecDiff(
        added_criteria=tuple(sorted(new_acs - old_acs)),
        removed_criteria=tuple(sorted(old_acs - new_acs)),
        authorized_added=tuple(sorted(new_auth - old_auth)),
        authorized_removed=tuple(sorted(old_auth - new_auth)),
        undeferred=tuple(sorted(set(old_def) - set(new_def))),
        newly_deferred=tuple(sorted(set(new_def) - set(old_def))),
    )


def red_owed_for(diff: SpecDiff, tests: List[TestRef]) -> Tuple[str, ...]:
    """The tests the amendment's NEW acceptance criteria owe a RED for.

    Taken from the completeness gate's own AC->test map, so this is not a second opinion about what
    covers what — it is the same mapping the gate just insisted on. SI.1's TDD gate cannot carry
    this obligation itself: it keys on "was anything EVER red in this run", and mid-develop the
    red-set is a non-empty high-water mark, so a widened spec would sail straight through it with no
    failing test for the new behaviour."""
    added = set(diff.added_criteria)
    if not added:
        return ()
    return tuple(sorted({t.name for t in tests if added & set(t.ac_ids)}))


# ======================================================================================
# the regression
# ======================================================================================

def open_amend(store: Any, run_id: str, *, reason: str = "", item: str = "",
               from_version: int = 0, ledger: Any = None) -> AmendRecord:
    """Regress the run: `develop -> SPEC`. Development writes are blocked from this moment.

    Two persisted facts, in the order that keeps them safe: the marker FIRST (so a crash between the
    two leaves the run blocked, not silently open), then the checkpoint rollback."""
    record = {"run_id": run_id, "status": STATUS_OPEN, "reason": reason, "item": item,
              "from_version": from_version, "to_version": from_version + 1, "red_owed": []}
    store.write(amend_key(run_id), record)
    PipelineCheckpoint(store, run_id, ledger=ledger).regress(REGRESSED_PHASES)
    return amend_from_state(record)


def close_amend(store: Any, run_id: str, *, red_owed: Tuple[str, ...], reason: str, item: str,
                from_version: int, to_version: int, ledger: Any = None) -> AmendRecord:
    """The amendment landed: the run resumes from the last PASSED gate (P17).

    The record is NOT deleted — it closes. It stays as the audit trail, and it carries `red_owed`,
    which the hook keeps enforcing until the new criteria have a failing test on record."""
    record = {"run_id": run_id, "status": STATUS_CLOSED, "reason": reason, "item": item,
              "from_version": from_version, "to_version": to_version,
              "red_owed": list(red_owed)}
    store.write(amend_key(run_id), record)
    cp = PipelineCheckpoint(store, run_id, ledger=ledger)
    for phase in REGRESSED_PHASES:           # the re-run gates passed again -> develop resumes
        cp.mark_passed(phase)
    return amend_from_state(record)


# ======================================================================================
# the use-case
# ======================================================================================

@dataclass
class AmendPlan:
    """A regressed run with its pre-consent gates already run — everything decided EXCEPT the human.

    `begin_amend` produces one; `finish_amend` commits it. The split exists because the consent is
    an OUT-OF-BAND round-trip (SI.3): the model proposes, a human runs `mokata approve <id>` in
    another process, the model re-calls. That window is not a gap in the enforcement — it is the
    heart of it. The run is ALREADY regressed while it stands, so development writes are blocked for
    exactly as long as the human takes to decide."""

    ok: bool
    run_id: str
    spec: Spec
    tests: List[TestRef]
    diff: SpecDiff
    from_version: int
    reason: str = ""
    item: str = ""
    gate: str = ""                      # the gate that refused, when one did
    reason_text: str = ""
    unmapped: Tuple[str, ...] = ()
    impact: Optional[Any] = None
    red_owed: Tuple[str, ...] = ()
    request: Optional[DeviationRequest] = None

    @property
    def to_version(self) -> int:
        return self.from_version + 1

    @property
    def scope_widened(self) -> bool:
        return self.diff.widens_scope

    def outcome(self) -> AmendOutcome:
        """This plan's refusal, as an outcome (only called when `ok` is False)."""
        return AmendOutcome(False, self.reason_text, gate=self.gate, unmapped=self.unmapped,
                            diff=self.diff, impact=self.impact,
                            scope_widened=self.diff.widens_scope)


def begin_amend(surface: Any, spec: Spec, tests: List[TestRef], *, run_id: str,
                store: Any = None, reason: str = "", item: str = "",
                ledger: Any = None, layer: Any = None) -> AmendPlan:
    """REGRESS the run and run every gate that does not need the human. `develop -> SPEC`.

    Order is deliberate: the regression happens FIRST, before a single gate is judged. An amendment
    that then fails completeness leaves the run blocked — which is correct. The spec no longer
    describes what is being built, and mokata will not let development continue against a spec it
    knows is wrong. The way forward is a correct amendment (or `spec amend --abort`, or the P14
    override) — never silently carrying on."""
    store = surface.state if store is None else store

    current = load_emitted_spec(store)
    if current is None or not current.criteria:
        empty = SpecDiff()
        return AmendPlan(
            False, run_id, spec, tests, empty, 0, gate="spec-persisted",
            reason_text=("no spec is emitted for this run — there is nothing to amend. Emit one "
                         "first (/mokata:spec)."))

    from_version = spec_version(store) or 1
    diff = diff_specs(current, spec)

    # --- THE REGRESSION. From here until the amendment lands (or is aborted), the hook blocks
    # development writes. Only log the deviation on the FIRST call, so a propose->approve->re-call
    # round-trip leaves one surfaced deviation, not two.
    already_open = amend_from_state(store.read(amend_key(run_id))).is_open
    open_amend(store, run_id, reason=reason, item=item, from_version=from_version, ledger=ledger)

    req = DeviationRequest(
        what=f"amend the spec v{from_version} -> v{from_version + 1}:\n{diff.render()}",
        why=reason or "the approved scope no longer covers the work",
        options=["approve the amendment (the new scope is re-gated)",
                 "decline, and implement strictly to the approved spec"],
        target=DEVIATION_SCOPE, phase="spec")
    if not already_open:
        DeviationGate(ledger).request(req)      # a scope change IS a plan change (P2) — surfaced

    plan = AmendPlan(True, run_id, spec, tests, diff, from_version,
                     reason=reason, item=item, request=req)

    # --- gate 1: COMPLETENESS. Every criterion — the old ones AND the new — maps to a test.
    gr = run_completeness_gate(spec, tests, store=store)
    if not gr.passed:
        plan.ok = False
        plan.gate = "completeness"
        plan.reason_text = gr.reason
        plan.unmapped = tuple(gr.unmapped_ids)
        return plan

    # --- gate 2: the BLAST RADIUS (Lens-1), re-run ONLY when the amendment WIDENS what may be
    # touched. Narrowing cannot widen impact, so re-running the lens would cost tokens for no answer.
    if diff.widens_scope:
        try:
            from ..brainstorm_impact import compute_impact
            targets = list(diff.authorized_added) + list(diff.added_criteria)
            plan.impact = compute_impact(spec.approach or spec.title, targets, layer=layer)
        except Exception as exc:
            # D5 — FAIL CLOSED. The old handler set `impact=None`, left `ok=True`, and let the
            # amendment through: a scope-WIDENING change approved exactly as if the blast-radius
            # lens had run and found nothing. A gate that fails open silently is not a gate.
            #
            # Its comment ("degrade-clean: no graph -> no lens") was also simply false.
            # `compute_impact` is ITSELF degrade-clean: `layer=None` and a failing `blast_radius`
            # query are both handled INSIDE it (it marks `degraded` and keeps scoring), and it does
            # not raise for a missing graph. So nothing that lands here is the no-graph case — it is
            # a GENUINE fault (an unimportable engine, a bug), and the honest answer to "what does
            # this widened scope touch?" is: unknown. Unknown blast radius, on the one amendment
            # shape this gate exists for, refuses. The run stays REGRESSED (as after a failed
            # completeness gate) — the way forward is a fix, a narrower amendment, or P14.
            #
            # BROAD ON PURPOSE: `compute_impact` reaches an optional, pluggable graph backend, so
            # the fault types are not nameable at module scope. Broad + FAIL-CLOSED is safe; broad
            # + fail-open was the bug.
            plan.impact = None
            plan.ok = False
            plan.gate = "blast-radius"
            plan.reason_text = (
                f"gate 2 (blast radius) could NOT run on a scope-WIDENING amendment "
                f"({type(exc).__name__}) — the impact of the newly-authorized surface is UNKNOWN. "
                f"The amendment is REFUSED rather than approved as if the lens had run and found "
                f"nothing. Fix the fault (`mokata doctor`), or amend without widening scope.")
            return plan
        if ledger is not None and plan.impact is not None:
            ledger.record("impact", phase="spec-amend", approach=spec.approach or spec.title,
                          widened=list(diff.authorized_added) + list(diff.undeferred))

    plan.red_owed = red_owed_for(diff, tests)
    return plan


def finish_amend(plan: AmendPlan, *, store: Any, gate: Any = None, ledger: Any = None,
                 assume_yes: bool = False, confirm: Any = None,
                 human_approved: bool = False, surface_name: str = "") -> AmendOutcome:
    """Commit the amendment: vN+1 persisted, vN superseded, the diff ledgered, the run RESUMED.

    `gate` is the caller's WriteGate — the MCP path hands in one carrying a verified SI.3 approval
    (`human_approved=True`), the CLI one that re-confirms at a TTY. There is no path to a commit
    that does not pass through it."""
    from ..govern import WriteGate, WriteRequest
    from ..govern.trust import CLI_SURFACE

    wgate = gate if gate is not None else WriteGate(ledger=ledger)
    dgate = DeviationGate(ledger)

    def commit() -> None:
        spec_commit(store, plan.spec, version=plan.to_version,
                    archive_key=archive_key(plan.run_id, plan.from_version))

    out = wgate.submit(
        WriteRequest(EMIT_KIND, EMIT_TARGET, content=preview_content(store, plan.spec),
                     tool=AMEND_TOOL, surface=surface_name or CLI_SURFACE),
        commit=commit, assume_yes=assume_yes, confirm=confirm, human_approved=human_approved)

    if not out.committed:
        if plan.request is not None:
            dgate.resolve(plan.request, approved=False)
        # The run stays REGRESSED: a declined amendment does not restore a spec that no longer
        # describes the work. The human aborts, or amends correctly, or overrides (P14).
        return AmendOutcome(False, out.reason, gate="write-gate", diff=plan.diff,
                            impact=plan.impact, scope_widened=plan.scope_widened)

    if plan.request is not None:
        dgate.resolve(plan.request, approved=True)
    if ledger is not None:
        ledger.record(LEDGER_KIND, run=plan.run_id, from_version=plan.from_version,
                      to_version=plan.to_version, reason=plan.reason, item=plan.item,
                      superseded=archive_key(plan.run_id, plan.from_version),
                      red_owed=list(plan.red_owed), scope_widened=plan.scope_widened,
                      **plan.diff.to_dict())

    close_amend(store, plan.run_id, red_owed=plan.red_owed, reason=plan.reason, item=plan.item,
                from_version=plan.from_version, to_version=plan.to_version, ledger=ledger)

    return AmendOutcome(True, f"spec amended to v{plan.to_version}", version=plan.to_version,
                        red_owed=plan.red_owed, diff=plan.diff, impact=plan.impact,
                        scope_widened=plan.scope_widened)


def abort_amend(store: Any, run_id: str, *, ledger: Any = None) -> bool:
    """Abandon an open amendment: the run returns to the spec it already had (vN, untouched).

    The escape hatch that keeps a rejected amendment from wedging a run. It restores the PREVIOUS
    phase state — it does not approve anything, and it changes no spec. Ledgered, because deciding
    NOT to change scope is a decision too."""
    record = amend_from_state(store.read(amend_key(run_id)))
    if not record.is_open:
        return False
    close_amend(store, run_id, red_owed=(), reason=record.reason, item=record.item,
                from_version=record.from_version, to_version=record.from_version, ledger=ledger)
    if ledger is not None:
        ledger.record(LEDGER_KIND, run=run_id, decision="aborted",
                      from_version=record.from_version, to_version=record.from_version,
                      reason=record.reason, item=record.item)
    return True


def amend_spec(surface: Any, spec: Spec, tests: List[TestRef], *, run_id: str,
               store: Any = None, reason: str = "", item: str = "",
               ledger: Any = None, layer: Any = None, gate: Any = None,
               assume_yes: bool = False, confirm: Any = None,
               human_approved: bool = False, surface_name: str = "") -> AmendOutcome:
    """begin + finish, for an in-process caller that already holds the human's decision."""
    store = surface.state if store is None else store
    plan = begin_amend(surface, spec, tests, run_id=run_id, store=store, reason=reason,
                       item=item, ledger=ledger, layer=layer)
    if not plan.ok:
        return plan.outcome()
    return finish_amend(plan, store=store, gate=gate, ledger=ledger, assume_yes=assume_yes,
                        confirm=confirm, human_approved=human_approved,
                        surface_name=surface_name)


__all__ = [
    "AMEND_TOOL", "LEDGER_KIND", "REGRESSED_PHASES",
    "AmendOutcome", "AmendPlan", "SpecDiff",
    "abort_amend", "amend_spec", "begin_amend", "close_amend", "diff_specs", "finish_amend",
    "open_amend", "red_owed_for",
]
