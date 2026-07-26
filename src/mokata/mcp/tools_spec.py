"""SPEC write tools — human-gated (SI.3) spec emit/amend/check: the in-harness half of /mokata:spec.

Domain split out of `mcp/tools_write.py` (PRE-SIMP, release 0.0.15): `spec_emit`, `spec_amend`,
`spec_check`, with the GR.S3 / GR-PA-WIRE emit backstops and the preview/impact helpers they own.
Every tool routes through the one consent boundary in `mcp/consent.py`; registration order + tool
names are preserved by the `tools_write.py` aggregator.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import approval
from ..awaiting import AMEND_ABORT_CMD, AMEND_REGRESSED_NOTE
from ..govern import AuditLedger, WriteGate
from ..govern.trust import MCP_SURFACE
from ..knowledge import KnowledgeLayer
from ..memory import MemoryStore
from .consent import (_consent, _gated_write, _policy, _propose, _refused,
                      _require, _trust)
from .registry import _mokata_dir, _surface
from .validation import validate_comma_list


def _graph_required_emit_refusal(surface: Any, path: str,
                                 approach: str) -> Optional[Dict[str, Any]]:
    """GR.S3 MCP parity — refuse `spec_emit` when the persisted brainstorm's CHOSEN approach has a
    DEGRADED blast radius and `graph.required` is on with no ledgered override. Returns a blocked
    result, or None when there is nothing to refuse on (no brainstorm / no degraded chosen impact).
    Best-effort + fail-open on any read fault: this is a parity guard, never a new failure mode."""
    from ..govern import graph_required as GR
    try:
        if not GR.graph_required_enabled(surface):
            return None
        from ..brainstorm import restore_brainstorm_progress
        session = restore_brainstorm_progress(surface.state)
        if session is None:
            return None
        chosen = getattr(session, "chosen", None)
        name = approach or (chosen.name if chosen is not None else None)
        if not name:
            return None
        imp = (getattr(session, "impacts", {}) or {}).get(name)
        if imp is None or not getattr(imp, "graph_degraded", False):
            return None
        from ..session import current_run_id
        run_id = current_run_id()
        if GR.read_degraded_override(surface.root, run_id):
            return None
        notice = GR.fire_upgrade_notice_once(surface.root)
        gate = GR.check_graph_required(
            degraded=True, required=True, overridden=False,
            consumer="blast radius (Lens 1)", mentions=int(getattr(imp, "caller_count", 0) or 0),
            files=int(getattr(imp, "file_count", 0) or 0),
            targets=list(getattr(imp, "targets", []) or []), notice=notice)
        if not gate.refused:
            return None
        return {"status": "blocked", "committed": False, "gate": "graph-required",
                "reason": gate.render(),
                "hint": ("this approach's blast radius is a degraded lexical estimate — adopt a "
                         "code graph (`mokata graph adopt`) or accept it for this session with "
                         "`--allow-degraded`. Nothing was written; there is nothing to approve.")}
    except Exception:                                     # noqa: BLE001 — a parity guard never crashes emit
        return None


def _prior_art_emit_refusal(surface: Any) -> Optional[Dict[str, Any]]:
    """GR-PA-WIRE MCP parity — refuse `spec_emit` when the persisted CHOSEN approach's prior-art
    step did not run. Returns a blocked result, or None when there is nothing to refuse on (no
    approved approach persisted → a standalone spec).

    DELIBERATE seam difference from `_graph_required_emit_refusal` (documented, one shared verdict):
    that reads the resume-state `brainstorm_progress`; THIS reads the DURABLE `approved_approach`
    Handoff (`handoff.prior_art`, stamped at approval). The Handoff is guaranteed present exactly
    when there is an approved approach to gate, whereas `brainstorm_progress` survives to emit only
    because `clear_brainstorm_progress` has no production caller today (GR.S3-HOLE, filed for
    grooming) — gating on the absence of a cleanup call would be a time bomb. Both surfaces
    (this + the CLI `mokata spec emit`) read the SAME key and the SAME `check_prior_art_ran` verdict.
    Fail-CLOSED: a legacy/missing `handoff.prior_art` refuses, naming the road back.

    Best-effort + fail-open on any read fault: like its GR.S3 sibling, a fault reading persisted
    state must never turn this backstop into a NEW failure mode for a write the user asked for."""
    try:
        from ..brainstorm import load_approved_approach
        from ..govern.prior_art_gate import handoff_prior_art_gate
        handoff = load_approved_approach(surface.state)
        if handoff is None:
            return None
        gate = handoff_prior_art_gate(handoff)
        if not gate.refused:
            return None
        return {"status": "blocked", "committed": False, "gate": "prior-art",
                "reason": gate.render(),
                "hint": ("this approach's prior-art step never ran — re-run the prior-art pass for "
                         "the chosen approach and re-approve, then emit. Nothing was written and "
                         "there is nothing to approve.")}
    except Exception:                                     # noqa: BLE001 — a backstop never crashes emit
        return None


def spec_emit(path: str = ".", title: str = "", criteria: Optional[list] = None,
              tests: Optional[list] = None, approach: str = "", domains: Optional[list] = None,
              scope: Optional[dict] = None, approve: bool = False,
              confirm: Optional[bool] = None, proposal_id: str = "") -> Dict[str, Any]:
    """SI-DEV.0 — EMIT the spec: persist it as this run's `emitted_spec` and append it to the
    shared `spec_corpus`, in ONE human-gated commit. The in-harness half of `/mokata:spec`, and the
    only way a spec reaches disk.

    `criteria` is `[{"id": "AC1", "text": "..."}]` and `tests` is `[{"name": "test_x",
    "ac_ids": ["AC1"]}]` — every acceptance criterion must map to a test or the COMPLETENESS GATE
    refuses the emit outright (no proposal: a human approving an incomplete spec could not make it
    complete). A passing gate then goes through the normal human gate: no `proposal_id` -> a
    proposal and NOTHING written; a human mints the approval with `mokata approve <id>`; re-call
    with that id to commit it, once.

    Emitting is what unblocks implementation: the `spec-persisted` run-state gate reads exactly the
    key this writes, and `spec-check`'s regression guard reads exactly the corpus this appends to.

    SI-DEV — `scope` is the spec's MACHINE-CHECKABLE boundary, and it is what makes the spec bind:
    `{"authorized": ["src/api/*"], "deferred": [{"id": "D1", "item": "batch update/delete",
    "paths": ["src/api/batch*.py"], "markers": ["batch_update"]}]}`. The gate hook enforces exactly
    what is declared here — a write outside `authorized`, or one spelling a deferred `marker`, is
    blocked until the spec is AMENDED (`spec_amend`). Declare what you are NOT building, in the
    words the code would use. Omit `scope` and the spec is not policed at all (the honest default —
    but then "we deferred that" is a note, not a gate)."""
    from ..engine.emit import (EMIT_KIND, EMIT_TARGET, EMIT_TOOL, preview_content,
                               spec_commit, spec_from_payload)
    from ..engine.completeness import run_completeness_gate

    surface = _surface(path)
    payload = {"title": title, "criteria": criteria or [], "tests": tests or [],
               "approach": approach, "domains": domains or [], "scope": scope}
    try:
        spec, test_refs = spec_from_payload(payload)
    except ValueError as exc:
        return {"status": "error", "committed": False, "reason": str(exc),
                "hint": ("send the spec as title + criteria [{id, text}] + tests "
                         "[{name, ac_ids}] — every criterion mapped to a test.")}

    # Gate 1 — completeness. The SAME gate the engine pipeline runs. It fires BEFORE the consent
    # boundary on purpose: an incomplete spec is not a write awaiting permission, it is a spec that
    # is wrong, and proposing it would walk a human to a terminal to approve something no approval
    # could fix.
    gr = run_completeness_gate(spec, test_refs, store=surface.state)
    if not gr.passed:
        return {"status": "blocked", "committed": False, "gate": "completeness",
                "reason": gr.reason, "unmapped": list(gr.unmapped_ids),
                "ac_count": len(spec.criteria),
                "hint": ("map every acceptance criterion to a test, then re-call. Nothing was "
                         "written and there is nothing to approve — this is a completeness "
                         "failure, not a missing approval.")}

    # Gate 1b — GR.S3 `graph.required`. MCP-loop parity for the Lens-1 HARD-GATE: a spec emitted
    # from a brainstorm whose CHOSEN approach's blast radius fell to the lexical floor is refused as
    # a decision input, exactly as the CLI approve gate refuses. Read the persisted brainstorm's
    # chosen-approach radius; degrade-clean — no persisted brainstorm / no chosen impact → skip (we
    # only refuse when we can positively see a degraded radius). The escape is the session-scoped
    # `--allow-degraded` override the same human-consent flow writes.
    grr = _graph_required_emit_refusal(surface, path, approach)
    if grr is not None:
        return grr

    # Gate 1c — GR-PA-WIRE prior-art step-ran. The bound prior-art pass must have RUN for the
    # approved approach before it can be turned into a spec: a spec emitted from a brainstorm whose
    # chosen approach never ran the pass is refused, exactly as the CLI `mokata spec emit` refuses.
    # Reads the DURABLE `approved_approach` Handoff (the one both surfaces read); degrade-clean — no
    # persisted approach → skip (a standalone spec is never gated). Fail-CLOSED on a missing record.
    par = _prior_art_emit_refusal(surface)
    if par is not None:
        return par

    # Gate 2 — the human. Same consent boundary as every other durable write (SI.3/SI.4).
    args = {"path": path, "title": title, "criteria": criteria or [], "tests": tests or [],
            "approach": approach, "domains": domains or [], "scope": scope or {}}
    gate = _consent(path, EMIT_TOOL, args, proposal_id, surface=surface)
    if gate.refused:
        return _refused(gate)
    if not gate.granted:
        return _propose(
            path, EMIT_TOOL, args,
            {"ac_count": len(spec.criteria), "test_count": len(test_refs),
             "gate": "completeness", "completeness": gr.reason},
            target="state/emitted_spec.json",
            summary=f"emit the spec '{spec.title}' ({len(spec.criteria)} AC(s), all mapped)",
            preview=_spec_preview(spec, test_refs), approve=approve, confirm=confirm)

    out = _gated_write(_mokata_dir(path), EMIT_KIND, EMIT_TARGET,
                       preview_content(surface.state, spec),
                       lambda: spec_commit(surface.state, spec),
                       gate, _policy(path, EMIT_TOOL, human_approved=True, surface=surface))
    out["ac_count"] = len(spec.criteria)
    out["corpus_size"] = out.pop("result", None)
    if out.get("committed"):
        from ..engine.emit import mark_emitted
        from ..session import current_run_id
        mark_emitted(surface.state, current_run_id(),
                     ledger=AuditLedger.from_mokata_dir(surface.mokata_dir))
        out["next"] = ("the spec is on record — implementation is unblocked once a failing test "
                       "is too (/mokata:test).")
        if spec.scope is not None:
            out["scope"] = ("enforced: a write outside the authorized surface, or spelling a "
                            "deferred marker, is now blocked until the spec is amended.")
    return out


def spec_amend(path: str = ".", title: str = "", criteria: Optional[list] = None,
               tests: Optional[list] = None, approach: str = "", domains: Optional[list] = None,
               scope: Optional[dict] = None, reason: str = "", item: str = "",
               approve: bool = False, confirm: Optional[bool] = None,
               proposal_id: str = "") -> Dict[str, Any]:
    """SI-DEV — AMEND the spec. The ONE road back when a write is out of scope, and deliberately
    NOT a text edit: it is a FORCED PHASE REGRESSION.

    Calling this regresses the run `develop -> SPEC` IMMEDIATELY — development writes are blocked
    from this moment until the amendment is approved (or aborted with `mokata spec amend --abort`).
    The new spec must then re-earn every gate: completeness (every criterion, old and new, maps to a
    test), the blast radius (re-computed when the scope WIDENS), and a HUMAN approval — which the
    model cannot mint. It lands as vN+1 with vN superseded (never deleted) and the diff on the audit
    ledger, and its new criteria then OWE a failing test before implementation resumes.

    Send the WHOLE amended spec (not a patch): title + criteria + tests + the `scope` section
    (`{"authorized": [globs], "deferred": [{"id", "item", "paths", "markers"}]}`). `reason` is why
    the scope must change, and `item` names the deferred item being released, if any.

    A user asking for something the spec deferred is authorization to ASK — which is this tool —
    never authorization to build. Do not work around a block; amend, or stop."""
    from ..engine.amend import AMEND_TOOL, begin_amend, finish_amend
    from ..engine.emit import spec_from_payload

    surface = _surface(path)
    payload = {"title": title, "criteria": criteria or [], "tests": tests or [],
               "approach": approach, "domains": domains or [], "scope": scope}
    try:
        spec, test_refs = spec_from_payload(payload)
    except ValueError as exc:
        return {"status": "error", "committed": False, "reason": str(exc)}

    from ..session import current_run_id
    run_id = current_run_id()
    store = surface.state
    ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
    layer = KnowledgeLayer.from_surface(surface)

    # Gates 1 + 2 (and THE REGRESSION, which happens before either of them — an amendment in flight
    # blocks development writes even if it later turns out to be incomplete).
    plan = begin_amend(surface, spec, test_refs, run_id=run_id, store=store, reason=reason,
                       item=item, ledger=ledger, layer=layer)
    if not plan.ok:
        out = plan.outcome()
        return {"status": "blocked", "committed": False, "gate": out.gate, "reason": out.reason,
                "unmapped": list(out.unmapped),
                # MCP-R.D2: the gate-blocked path is the OTHER way a user lands mid-amend with the
                # run regressed and no proposal to approve — so it must name the road back just as
                # loudly. There is no proposal_id here (nothing was staged), which makes `--abort`
                # the operative escape hatch, not `approve`.
                "blocked_recovery": (f"the run is REGRESSED and development writes are BLOCKED. "
                                     f"Two ways out: fix the amendment and re-call spec_amend, or "
                                     f"abandon it with `{AMEND_ABORT_CMD}`. Nothing is stuck — "
                                     f"mokata is refusing to build against a spec it knows is "
                                     f"wrong."),
                "abort_command": AMEND_ABORT_CMD,
                "hint": ("map every acceptance criterion to a test and re-call. The run stays "
                         "regressed to SPEC (development writes are blocked) until a correct "
                         "amendment lands or you abort it — mokata will not let you build against "
                         "a spec it knows is wrong.")}

    # Gate 3 — the HUMAN (SI.3). While this is pending, the run is REGRESSED and writes are blocked:
    # that is not a gap in the enforcement, it IS the enforcement.
    args = {"path": path, "title": title, "criteria": criteria or [], "tests": tests or [],
            "approach": approach, "domains": domains or [], "scope": scope or {},
            "reason": reason, "item": item}
    gate = _consent(path, AMEND_TOOL, args, proposal_id, surface=surface)
    if gate.refused:
        return _refused(gate)
    if not gate.granted:
        out = _propose(
            path, AMEND_TOOL, args,
            {"gate": "scope", "from_version": plan.from_version, "to_version": plan.to_version,
             "diff": plan.diff.to_dict(), "scope_widened": plan.scope_widened,
             "red_owed": list(plan.red_owed),
             "regressed": True},
            target="state/emitted_spec.json",
            summary=(f"amend the spec v{plan.from_version} -> v{plan.to_version}"
                     f"{' — releases: ' + item if item else ''}"),
            preview=f"{plan.diff.render()}\n\nwhy: {reason or '(none given)'}",
            # MCP-R.D2 (B-AMEND-STUCK): the regression rides the SHARED awaiting head, as
            # `awaiting_blocks`, instead of being appended afterwards as `note`. Two reasons, both
            # bugs in the old line: (1) it landed LAST, under the payload, which is exactly how the
            # headline P22 flow came to read as wedged; (2) `_propose` already uses `note` for the
            # approve/confirm demotion, so an amend call passing approve=true SILENTLY LOST that
            # warning to this overwrite.
            blocked_note=AMEND_REGRESSED_NOTE,
            approve=approve, confirm=confirm)
        return out

    wgate = WriteGate(ledger=ledger, trust=_trust(path, surface))
    outcome = finish_amend(plan, store=store, gate=wgate, ledger=ledger,
                           human_approved=True, surface_name=MCP_SURFACE)
    approval.record_redemption(ledger, _require(gate), committed=outcome.committed)

    if not outcome.committed:
        return {"status": "blocked", "committed": False, "gate": outcome.gate,
                "reason": outcome.reason}
    return {"status": "committed", "committed": True, "reason": outcome.reason,
            "version": outcome.version, "diff": outcome.diff.to_dict() if outcome.diff else {},
            "scope_widened": outcome.scope_widened,
            "impact": _impact_summary(outcome.impact),
            "red_owed": list(outcome.red_owed),
            "next": (f"spec v{outcome.version} is on record (v{outcome.version - 1} superseded, "
                     f"the diff ledgered). Implementation of the NEW criteria is blocked until "
                     f"{', '.join(outcome.red_owed) or 'their tests'} "
                     f"{'is' if len(outcome.red_owed) == 1 else 'are'} RED (/mokata:test).")}


def _impact_summary(impact: Any) -> Optional[Dict[str, Any]]:
    """The re-run blast radius, as data. `degraded` is reported, not hidden: an impact computed
    without a code graph is a weaker claim, and the model must be able to see that it is."""
    if impact is None:
        return None
    return {"magnitude": impact.magnitude, "files": list(impact.impacted_files)[:20],
            "symbols": list(impact.impacted_symbols)[:20], "callers": impact.caller_count,
            "decisions": [d.subject for d in impact.affected_decisions],
            "degraded": impact.degraded, "note": impact.note}


def _spec_preview(spec: Any, tests: List[Any]) -> str:
    """What the human sees in their own terminal before approving the emit."""
    by_ac = {}
    for t in tests:
        for ac in t.ac_ids:
            by_ac.setdefault(ac, []).append(t.name)
    lines = [f"spec: {spec.title}"]
    if spec.approach:
        lines.append(f"approach: {spec.approach}")
    if spec.domains:
        lines.append(f"domains: {', '.join(spec.domains)}")
    for c in spec.criteria:
        lines.append(f"  {c.id}: {c.text}")
        lines.append(f"      -> {', '.join(by_ac.get(c.id, [])) or '(unmapped)'}")
    return "\n".join(lines)


def spec_check(path: str = ".", symbols: str = "", files: str = "", text: str = "",
               phase: str = "develop", approve: bool = False,
               confirm: Optional[bool] = None, proposal_id: str = "") -> Dict[str, Any]:
    """Stage 37 regression guard: check a change's touch-set (comma-separated `symbols`/`files`)
    against the SAVED specs + decision memory. If it would affect one, this is HUMAN-GATED (SI.3):
    it SURFACES the conflict and records nothing but the surfaced-deviation entry (status 'blocked'),
    returning a proposal_id. A human mints the confirmation with `mokata approve <id>`; re-calling
    with that `proposal_id` records the amend/supersede through the deviation gate. No saved corpus →
    'skipped'; no graph → lexical/file overlap (the result says so). Frugal: only the touch-set is
    checked."""
    from ..engine import ChangeSet, check_change, load_decisions, load_spec_corpus
    from ..govern.deviation import ACCEPTANCE_CRITERIA, DeviationGate, DeviationRequest
    # D1d — validate the two comma-lists BEFORE the surface load and the corpus/graph work: a list
    # of nothing-but-separators used to parse to an empty touch-set, so the guard checked NOTHING
    # and reported 'skipped'/'ok' while the caller believed they had scoped it.
    change = ChangeSet(symbols=validate_comma_list(symbols, "symbols"),
                       files=validate_comma_list(files, "files"), text=text)
    surface = _surface(path)
    specs = load_spec_corpus(surface.state)
    decisions = load_decisions(MemoryStore.from_surface(surface))
    layer = KnowledgeLayer.from_surface(surface)
    report = check_change(change, specs, decisions, layer=layer)
    if not report.checked:
        return {"status": "skipped", "message": report.note, "conflicts": []}
    if not report.has_conflicts:
        return {"status": "ok", "conflicts": [], "degraded": report.degraded,
                "note": report.note}

    ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
    refs = ", ".join(f"{c.source_kind} '{c.ref}'" for c in report.conflicts)
    req = DeviationRequest(
        what=f"this change affects saved {refs}",
        why="the touched surface is already specified/decided",
        options=["confirm + amend/supersede the affected spec(s)/decision(s)",
                 "re-plan so the change does not break them"],
        target=ACCEPTANCE_CRITERIA, phase=phase)
    dgate = DeviationGate(ledger)

    args = {"path": path, "symbols": symbols, "files": files, "text": text, "phase": phase,
            "conflicts": [c.to_dict() for c in report.conflicts]}
    gate = _consent(path, "spec_check", args, proposal_id, surface=surface)
    if gate.refused:
        return _refused(gate)
    if not gate.granted:
        ledger.record("spec_conflict", phase=phase, degraded=report.degraded,
                      conflicts=[c.to_dict() for c in report.conflicts],
                      touch_set=report.touch_set)
        dgate.request(req)      # log that it was surfaced (proposed); resolve nothing yet
        out = _propose(path, "spec_check", args,
                       {"conflicts": [c.to_dict() for c in report.conflicts],
                        "degraded": report.degraded, "render": report.render()},
                       target="spec:acceptance-criteria",
                       summary=f"confirm a change that affects saved {refs}",
                       preview=report.render(), approve=approve, confirm=confirm)
        out["status"] = "blocked"       # the conflict IS the headline; the proposal rides along
        return out
    outcome = dgate.submit(req, assume_yes=True)             # human-approved (SI.3)
    approval.record_redemption(ledger, _require(gate), committed=True)
    return {"status": "confirmed", "committed": True, "reason": outcome.reason,
            "conflicts": [c.to_dict() for c in report.conflicts]}
