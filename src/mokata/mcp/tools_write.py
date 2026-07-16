"""WRITE / durable tools — ALWAYS human-gated, with an approval THE MODEL CANNOT MINT (SI.3).

Every tool here is PROPOSE-ONLY. Calling one returns the staged change and writes nothing. The only
thing that commits it is an approval a HUMAN minted out-of-band, in a process this one is not
driving:

    1. the model calls the tool                  -> status "proposed" + a proposal_id. Nothing writes.
    2. the HUMAN runs `mokata approve <id>`      -> a separate process, a TTY re-confirm, ledgered.
    3. the model re-calls with proposal_id=<id>  -> verified, committed once, approval BURNED.

What changed, and why (P2 — the principle this stage exists to make TRUE)
------------------------------------------------------------------------
This module used to gate on `_approved(approve, confirm)` — and `approve` is **a parameter the model
types**. "Every durable write is human-gated" therefore reduced to *the model decided to say it was
approved*, after which we handed `assume_yes=True` straight to the WriteGate. Prompt injection, a
confused model, or plain eagerness minted consent. Doc 74 filed it C-class; the SS.S5 audit flagged
the `assume_yes=True` sites again. SI.1 had already closed this exact class of hole for the gate
OVERRIDE by giving it no MCP surface — "a model-invocable override would let the model clear its own
gate". SI.3 applies that logic to the approval itself.

`approve` and `confirm` are still ACCEPTED (schema stability — no caller breaks), but they are
DEMOTED: they no longer commit anything. Passing `approve=true` gets you the proposal and an honest
note saying so. The consent now lives in `approval.py`: content-hashed (so what was approved is what
commits — change an argument and the hash, and therefore the id, changes), single-use, session-scoped,
TTL-bounded, and recorded on the hash-chained audit ledger, which links proposal → human → the commit
it licensed.

The `assume_yes=True` still handed downstream is NOT a gate any more and is no longer reachable from
any tool parameter: `_require()` makes a commit path without a verified, on-disk, human-minted
approval a programming error. It is now the *carrier* of a human decision that has already been made,
verified, and burned — not the decision.

Every function is pure and SDK-free; each registers into the shared `TOOLS` registry via
`@_tool(name, "write")`.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Optional

from .. import MOKATA_DIR, approval
from ..config import Surface
from ..govern import (AuditLedger, WriteGate, WriteRequest, plan_reset,
                      reset_state)
from ..govern.trust import (MCP_SURFACE, REFUSED_READ_ONLY, TrustPolicy,
                            WritePolicy)
from ..knowledge import KnowledgeLayer
from ..memory import DECISION, MemoryItem, MemoryStore
from .registry import _mokata_dir, _surface, _tool

__all__ = [
    "remember", "import_stack", "reset", "apply_proposal", "memory_export",
    "memory_import", "vault_push", "session_push", "session_pull", "session_name",
    "audit_share", "spec_check", "init", "reconfigure", "config_set", "export_stack",
    "stacks_install",
]


# ======================================================================================
# the consent boundary — the whole of SI.3 lives in these five helpers
# ======================================================================================

def _trust(path: str) -> TrustPolicy:
    """This repo's trust dial. Degrade-clean: an UNINITIALIZED repo has no manifest and therefore no
    dial, which must read as the gated-write default — otherwise `init` itself, the one tool whose
    job is to create the manifest, could never run."""
    from ..config import ConfigError
    from ..manifest import ManifestError
    try:
        return TrustPolicy.from_surface(_surface(path))
    except (ConfigError, ManifestError, OSError):
        return TrustPolicy()


def _policy(path: str, tool: str, *, human_approved: bool = False) -> WritePolicy:
    """The ONE seam (SI.4), resolved once at the MCP boundary and threaded down to whichever
    WriteGate actually performs the write — `_gated_write`'s, or one buried in config_cmd /
    session_bundle / memory / team, which is where several of these tools really commit."""
    return WritePolicy(trust=_trust(path), tool=tool, surface=MCP_SURFACE,
                       human_approved=human_approved)


def _consent(path: str, tool: str, args: Dict[str, Any],
             proposal_id: str) -> approval.Consent:
    """THE gate. Two questions, in order.

    1. MAY this tool write at all? (K3/SI.4) A `read-only` tool is refused HERE, before it proposes
       — a proposal a human could never redeem is worse than useless: it would walk them to a
       terminal to approve a write that is structurally incapable of committing. The refusal is
       recorded on the audit ledger, because a blocked write is a decision like any other (I3).
    2. Is there a human-minted approval for this EXACT call? `proposal_id` names one, and it is
       verified (content hash + freshness + single-use + session). No id -> NEEDED, and the tool
       proposes.

    There is deliberately no argument here a model could pass to reach GRANTED — the only way in is
    a record a human wrote, from a terminal, in another process."""
    from ..session import current_run_id
    if not _trust(path).can_write(tool, MCP_SURFACE):
        AuditLedger.from_mokata_dir(_mokata_dir(path)).record(
            "write_gate", write_kind="mcp", target=f"tool:{tool}", actor="mcp",
            decision="blocked", reason="read-only trust")
        return approval.Consent(
            approval.REFUSED, code=REFUSED_READ_ONLY,
            reason=(f"'{tool}' is read-only on the mcp surface — writes are not permitted "
                    f"(settings.trust)"))
    return approval.consent(path, tool=tool, args=args, run_id=current_run_id(),
                            proposal_id=proposal_id)


def _require(gate: approval.Consent) -> approval.Proposal:
    """The structural tripwire under every commit path in this module.

    Reaching a durable write without a GRANTED consent is a BUG, not a user input: no combination of
    tool parameters can produce it (that is the stage's whole claim, and `test_si_3_*` sweeps every
    tool × every consent-ish param to prove it). If it ever happens, refuse loudly rather than
    write."""
    if gate is None or not gate.granted or gate.proposal is None:
        raise RuntimeError(
            "mokata internal: a durable write reached the commit path without a human-minted "
            "approval (SI.3). Refusing to write.")
    return gate.proposal


def _refused(gate: approval.Consent) -> Dict[str, Any]:
    """This write is refused. Say WHICH way — a read-only dial and a bad approval are different
    problems, and only one of them has a next step the model can take."""
    if gate.code == REFUSED_READ_ONLY:
        return {
            "status": "refused", "committed": False,
            "reason_code": gate.code, "reason": gate.reason,
            "hint": ("this is a CONFIGURATION bound, not a missing approval — no proposal, and no "
                     "human approval, can lift it. A human must change `settings.trust` in the "
                     "manifest to allow this tool to write."),
        }
    return {
        "status": "refused", "committed": False,
        "reason_code": gate.code, "reason": gate.reason,
        "hint": ("re-call with no proposal_id to get a fresh proposal, then ask the human to run "
                 "`mokata approve <id>`. An approval is bound to one write, one session, one use."),
    }


def _propose(path: str, tool: str, args: Dict[str, Any], payload: Dict[str, Any], *,
             target: str = "", summary: str = "", preview: str = "",
             approve: bool = False, confirm: Optional[bool] = None) -> Dict[str, Any]:
    """Stage the write, return what WOULD change, and tell the model how a human approves it."""
    from ..session import current_run_id
    p = approval.propose(path, tool=tool, args=args, run_id=current_run_id(),
                         target=target, summary=summary, preview=preview)
    out: Dict[str, Any] = {"status": "proposed", "action": tool, "committed": False,
                           "proposal_id": p.proposal_id, "expires_in": p.expires_in(),
                           "approved": p.approved}
    out.update(payload)
    if p.approved:
        out["hint"] = (f"a human has ALREADY approved this write — re-call {tool} with "
                       f"proposal_id=\"{p.proposal_id}\" to commit it (once).")
    else:
        out["hint"] = (f"NOTHING was written. Ask the human to run:  mokata approve {p.proposal_id}"
                       f"  — then re-call {tool} with proposal_id=\"{p.proposal_id}\".")
    if approve or confirm:
        out["note"] = ("`approve`/`confirm` no longer commit: an approval is MINTED BY A HUMAN "
                       "out-of-band (`mokata approve <id>`) and can only be REFERENCED here by id. "
                       "Typing approve=true is not consent — it never was.")
    return out


def _record(mokata_dir: str, gate: approval.Consent, *, write_seq: Any = None,
            committed: bool = True) -> None:
    """Close the audit chain for a commit that did NOT go through `_gated_write` (the tools that own
    their own gated commit): proposal-hash → who approved → what landed."""
    approval.record_redemption(AuditLedger.from_mokata_dir(mokata_dir), _require(gate),
                               write_seq=write_seq, committed=committed)


def _gated_write(mokata_dir: str, kind: str, target: str, content: str,
                 commit_fn: Callable[[], Any], gate: approval.Consent,
                 policy: Optional[WritePolicy] = None) -> Dict[str, Any]:
    """Run a durable write through the universal WriteGate, under a verified human approval.

    Secrets stay a HARD BLOCK that no approval can override (P2/I1 — a security block is not a
    methodology gate). The decision goes to the audit ledger, and the redemption record links the
    human's approval to the `write_gate` entry of the commit it licensed.

    The `policy` carries the K3 trust dial and the tool's identity down to the gate (SI.4), and marks
    the write `human_approved` — which is a STATEMENT OF FACT, not a bypass: `_require` above has
    already verified a human minted this approval out-of-band and burned it. That is what lets a
    `propose-only` tool commit here despite the MCP server having no terminal to prompt at."""
    p = _require(gate)                  # no human approval on disk -> we do not get here at all
    ledger = AuditLedger.from_mokata_dir(mokata_dir)
    wgate = WriteGate(ledger=ledger, trust=(policy.trust if policy else None))
    box: Dict[str, Any] = {}
    outcome = wgate.submit(
        WriteRequest(kind, target, content=content, actor="mcp",
                     tool=(policy.tool if policy else None), surface=MCP_SURFACE),
        commit=lambda: box.update(result=commit_fn()),
        human_approved=True)            # the ALREADY-VERIFIED human decision (SI.3), never a tool
                                        # parameter — see `_require` above
    approval.record_redemption(ledger, p, write_seq=outcome.approval_seq,
                               committed=outcome.committed)
    return {"status": "committed" if outcome.committed else "blocked",
            "committed": outcome.committed,
            "reason": outcome.reason,
            "findings": [f.kind for f in outcome.findings],
            "result": box.get("result")}


# ======================================================================================
# the tools
# ======================================================================================

@_tool("remember", "write")
def remember(path: str = ".", subject: str = "", value: str = "",
             memory_type: str = DECISION, kind: str = "", approve: bool = False,
             confirm: Optional[bool] = None, mtype: Optional[str] = None,
             proposal_id: str = "", about_code: Optional[list] = None) -> Dict[str, Any]:
    """Remember a fact/decision in memory. `memory_type` is the storage tier
    (persistent/decision/episodic); `kind` is the typed project part (rule/guardrail/
    best-practice/context/reference) captured by /mokata:onboard; `mtype` is a DEPRECATED alias
    for `memory_type`. HUMAN-GATED (SI.3): this is PROPOSE-ONLY. It returns a proposal_id and writes
    nothing. Only an approval a HUMAN minted with `mokata approve <id>` — then referenced back here
    as `proposal_id` — commits it, once. `approve=true` does NOT commit (it never really did: it was
    a flag the model typed). A secret in `subject` OR `value` is blocked even when approved. A part
    kind is stored as persistent project knowledge."""
    from ..memory import PART_KINDS, PERSISTENT, normalize_kind
    surface = _surface(path)
    store = MemoryStore.from_surface(surface)
    if not store.enabled_types:
        return {"status": "unavailable", "message": "memory is disabled for this profile"}
    mtype = mtype or memory_type      # `mtype` (deprecated) overrides only if explicitly passed
    norm = normalize_kind(kind)
    if norm in PART_KINDS:
        mtype = PERSISTENT          # the captured "parts" are persistent project knowledge
    item = MemoryItem.create(subject, value, mtype=mtype, kind=norm or kind,
                             about_code=about_code)

    args = {"path": path, "subject": subject, "value": value, "memory_type": mtype, "kind": norm}
    gate = _consent(path, "remember", args, proposal_id)
    if gate.refused:
        return _refused(gate)
    if not gate.granted:
        # GR.S4 — validate any about_code anchors against the code graph. A PROPOSAL-level
        # warning rides on the proposal (never a block, never an auto-write — P2 untouched).
        payload = {"preview": store.render_write(item)}
        if about_code:
            try:
                from ..knowledge.about_code import check_about_code_anchors
                from ..knowledge.layer import KnowledgeLayer
                chk = check_about_code_anchors(about_code, KnowledgeLayer.from_surface(surface))
                if chk.warning:
                    payload["about_code_warning"] = chk.warning
            except Exception:
                pass                       # validation never blocks a proposal
        return _propose(path, "remember", args, payload,
                        target=f"memory:{subject}", summary=f"remember '{subject}' = {value[:60]}",
                        preview=store.render_write(item), approve=approve, confirm=confirm)
    # H4: scan subject AND value so a secret pasted into the subject can't slip the gate.
    return _gated_write(surface.mokata_dir, "memory", f"memory:{subject}",
                        f"{subject}\n{value}",
                        lambda: store.remember(item, assume_yes=True).committed, gate,
                        _policy(path, "remember", human_approved=True))


@_tool("import_stack", "write")
def import_stack(path: str = ".", file: str = "", approve: bool = False,
                 force: bool = False, confirm: Optional[bool] = None,
                 proposal_id: str = "") -> Dict[str, Any]:
    """Validate and apply a shared stack manifest. HUMAN-GATED (SI.3): PROPOSE-ONLY — it validates
    and reports what WOULD apply, and writes nothing. A human mints the approval with
    `mokata approve <id>`; re-call with that `proposal_id` to apply (use `force=true` to overwrite
    an existing config). The stack file is UNTRUSTED content and is secret-scanned at the boundary
    (SI.6b): a credential anywhere in it REFUSES THE WHOLE FILE, even when approved."""
    from ..share import apply_manifest, blocked_keys, load_shared, validate_shared
    try:
        data = load_shared(file)
        with open(file, encoding="utf-8") as fh:
            raw = fh.read()
    except (OSError, ValueError) as exc:
        return {"status": "error", "message": f"cannot read {file}: {exc}"}
    errors = validate_shared(data)
    # SI.6b — the hole this closes: the gate below was fed `content=""`, so its secret-scan ran over
    # an empty string. This tool was gated, ledgered, and BLIND. The seam (`apply_manifest`) now
    # refuses a poisoned file regardless of what a caller hands the gate; naming the keys here is
    # what lets the tool REPORT it instead of failing opaquely at commit.
    blocked = blocked_keys(data)

    args = {"path": path, "file": file, "force": force}
    gate = _consent(path, "import_stack", args, proposal_id)
    if gate.refused:
        return _refused(gate)
    if not gate.granted:
        return _propose(path, "import_stack", args,
                        {"valid": not errors, "errors": errors,
                         "blocked": len(blocked), "blocked_keys": blocked,
                         "would_apply_profile": data.get("profile")},
                        target=_mokata_dir(path),
                        summary=f"apply stack manifest {file} (profile {data.get('profile')})",
                        approve=approve, confirm=confirm)
    if blocked:
        return {"status": "blocked", "committed": False, "blocked": len(blocked),
                "blocked_keys": blocked,
                "reason": (f"refused: this shared stack carries a secret in {', '.join(blocked)} — "
                           f"a stack must carry an env-var pointer, never a credential")}
    if errors:
        return {"status": "blocked", "committed": False,
                "reason": "rejected: shared manifest is invalid", "errors": errors}
    surface_dir = _mokata_dir(path)
    box: Dict[str, Any] = {}

    def _apply() -> Any:
        result = apply_manifest(path, data, assume_yes=True, force=force)
        box["apply"] = result
        return {"applied": result.applied, "path": result.path,
                "message": result.message}

    # Feed the gate the RAW untrusted manifest text (the `stacks_install` shape) so its own
    # secret-scan is a real hard block, not a scan of the empty string.
    res = _gated_write(surface_dir, "config", surface_dir, raw, _apply, gate,
                       _policy(path, "import_stack", human_approved=True))
    return {**res, "blocked": len(blocked), "blocked_keys": blocked}


@_tool("reset", "write")
def reset(path: str = ".", keep_config: bool = False,
          approve: bool = False, confirm: Optional[bool] = None,
          proposal_id: str = "") -> Dict[str, Any]:
    """Remove mokata state (.mokata/). HUMAN-GATED (SI.3): PROPOSE-ONLY — it lists what WOULD be
    removed and deletes nothing. A human mints the approval with `mokata approve <id>`; re-call with
    that `proposal_id` to remove them. `keep_config` keeps the manifest + constitution."""
    plan = plan_reset(path, keep_config=keep_config)
    if not plan.targets:
        return {"status": "noop", "message": "reset: nothing to remove."}

    args = {"path": path, "keep_config": keep_config, "targets": sorted(plan.targets)}
    gate = _consent(path, "reset", args, proposal_id)
    if gate.refused:
        return _refused(gate)
    if not gate.granted:
        return _propose(path, "reset", args, {"targets": plan.targets},
                        target=_mokata_dir(path),
                        summary=f"remove {len(plan.targets)} mokata state target(s)",
                        preview="\n".join(plan.targets), approve=approve, confirm=confirm)
    # RESET-CRASH (R-13F) — the removal erases THIS repo's `.mokata`, and with it the very ledger
    # `_gated_write` writes its approved/redemption record to. Deleting INSIDE the gated commit meant
    # that post-commit write opened a path `_do_reset` had already removed → FileNotFoundError. So we
    # DEFER: the gate commits (recording the approved decision against the still-present ledger), and
    # only THEN do we perform the physical removal — which lands its own deletion-proof record (the
    # user-scoped tombstone, KB.S1, actor="mcp"). The CLI reset is untouched: it calls `reset_state`
    # directly and never rode this gate.
    def _approve_removal() -> Any:
        return {"planned": sorted(plan.targets)}

    out = _gated_write(_mokata_dir(path), "config", _mokata_dir(path), "", _approve_removal, gate,
                       _policy(path, "reset", human_approved=True))
    if out.get("committed"):
        result = reset_state(path, keep_config=keep_config, assume_yes=True, actor="mcp")
        out["result"] = {"removed": result.removed, "aborted": result.aborted}
    return out


@_tool("apply_proposal", "write")
def apply_proposal(path: str = ".", subject: str = "", decision: str = "approve",
                   approve: bool = False, confirm: Optional[bool] = None,
                   proposal_id: str = "") -> Dict[str, Any]:
    """Resolve a surfaced self-healing memory proposal (contradiction/staleness). HUMAN-GATED
    (SI.3): PROPOSE-ONLY — it shows the staged old->new change and writes nothing. A human mints the
    approval with `mokata approve <id>`; re-call with that `proposal_id` to apply your `decision`
    (approve/reject/defer)."""
    if decision not in ("approve", "reject", "defer"):
        return {"status": "error",
                "message": "decision must be one of approve/reject/defer"}
    surface = _surface(path)
    store = MemoryStore.from_surface(surface)
    match = next((p for p in store.detect_issues() if p.subject == subject), None)
    if match is None:
        return {"status": "error",
                "message": f"no pending proposal for subject '{subject}'"}

    args = {"path": path, "subject": subject, "decision": decision, "diff": match.diff()}
    gate = _consent(path, "apply_proposal", args, proposal_id)
    if gate.refused:
        return _refused(gate)
    if not gate.granted:
        return _propose(path, "apply_proposal", args,
                        {"subject": subject, "kind": match.kind, "diff": match.diff(),
                         "decision": decision},
                        target=f"memory:{subject}",
                        summary=f"{decision} the self-healing proposal for '{subject}'",
                        preview=match.diff(), approve=approve, confirm=confirm)
    return _gated_write(
        surface.mokata_dir, "memory", f"memory:{subject}", match.diff(),
        lambda: store.apply_proposal(match, decision, assume_yes=True).message, gate,
        _policy(path, "apply_proposal", human_approved=True))


@_tool("memory_export", "write")
def memory_export(path: str = ".", file: str = "", approve: bool = False,
                  confirm: Optional[bool] = None, proposal_id: str = "") -> Dict[str, Any]:
    """Export local memory (active items + provenance) to a committable share file. READ-ONLY on the
    store. HUMAN-GATED (SI.3): PROPOSE-ONLY — it reports how many items WOULD be written and writes
    no file. A human mints the approval with `mokata approve <id>`; re-call with that `proposal_id`
    to write the share file. Every exported value is secret-scanned at EGRESS strength (SI.6): a hit
    HARD-BLOCKS that item — it is named in `blocked` and left out of the artifact entirely."""
    from ..memory import MEMORY_SHARE_FILENAME, export_memory, export_payload
    surface = _surface(path)
    store = MemoryStore.from_surface(surface)
    data = export_memory(store)             # read-only; scans + computes the items without writing
    dest = file or os.path.join(path, MOKATA_DIR, MEMORY_SHARE_FILENAME)
    blocked = data["blocked"]

    args = {"path": path, "dest": dest, "items": len(data["items"])}
    gate = _consent(path, "memory_export", args, proposal_id)
    if gate.refused:
        return _refused(gate)
    if not gate.granted:
        return _propose(path, "memory_export", args,
                        {"items": len(data["items"]), "dest": dest,
                         "blocked": len(blocked), "blocked_keys": blocked},
                        target=dest, summary=f"export {len(data['items'])} memory item(s) to {dest}",
                        approve=approve, confirm=confirm)
    # SI.6 (74 C2): the write now goes through the universal WriteGate as kind `send` — an export is
    # EGRESS, so the gate's own scan runs at outbound strength over the exact bytes that leave, and
    # the ledger records the write with that content hashed. Per-item blocking already happened in
    # `export_memory`; this is the belt-and-suspenders on the assembled artifact.
    res = _gated_write(surface.mokata_dir, "send", dest, export_payload(data),
                       lambda: export_memory(store, dest=dest), gate,
                       _policy(path, "memory_export", human_approved=True))
    return {"status": res["status"], "committed": res["committed"], "dest": dest,
            "items": len(data["items"]), "blocked": len(blocked), "blocked_keys": blocked,
            "reason": res["reason"]}


@_tool("memory_import", "write")
def memory_import(path: str = ".", file: str = "", approve: bool = False,
                  confirm: Optional[bool] = None, proposal_id: str = "") -> Dict[str, Any]:
    """Merge a memory share file into local memory. HUMAN-GATED (SI.3): PROPOSE-ONLY — it validates
    and reports how many items WOULD merge, and writes nothing. A human mints the approval with
    `mokata approve <id>`; re-call with that `proposal_id` to dedup, gate-add new items, and route
    conflicts through the self-healing surface (never a silent overwrite). The imported content is
    UNTRUSTED, so each item is secret-scanned through the WriteGate and audit-logged — a secret is
    HARD-BLOCKED, not imported. Provenance is preserved."""
    from ..memory import import_memory, load_memory_share
    surface = _surface(path)
    try:
        data = load_memory_share(file)
    except (OSError, ValueError) as exc:
        return {"status": "error", "message": f"cannot read {file}: {exc}"}
    n = len(data["items"]) if isinstance(data.get("items"), list) else 0

    args = {"path": path, "file": file, "incoming": n}
    gate = _consent(path, "memory_import", args, proposal_id)
    if gate.refused:
        return _refused(gate)
    if not gate.granted:
        return _propose(path, "memory_import", args, {"incoming": n},
                        target=f"memory:import:{file}",
                        summary=f"merge {n} memory item(s) from {file}",
                        approve=approve, confirm=confirm)
    ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
    res = import_memory(MemoryStore.from_surface(surface), data, assume_yes=True,
                        ledger=ledger)
    approval.record_redemption(ledger, _require(gate), committed=not res.aborted)
    return {"status": "aborted" if res.aborted else "committed",
            "committed": not res.aborted, "added": res.added, "skipped": res.skipped,
            "resolved": res.resolved, "declined": res.declined,
            "blocked": res.blocked, "errors": res.errors}


@_tool("vault_push", "write")
def vault_push(path: str = ".", name: str = "", file: str = "", kind: str = "",
               author: str = "", force: bool = False,
               approve: bool = False, confirm: Optional[bool] = None,
               proposal_id: str = "") -> Dict[str, Any]:
    """Push a brainstorm/spec markdown artifact into the team vault under `name`. HUMAN-GATED
    (SI.3): PROPOSE-ONLY — it reports what WOULD happen and writes nothing. A human mints the
    approval with `mokata approve <id>`; re-call with that `proposal_id` to write through the
    WriteGate (a secret in the artifact is blocked even when approved). A changed re-push needs
    `force=true` (it versions, keeping prior metadata — never a silent clobber)."""
    from ..vault import VaultError, commit_push, plan_push, _artifact_path
    try:
        plan = plan_push(path, name, file, kind=kind or None, force=force)
    except VaultError as exc:
        return {"status": "error", "message": str(exc)}
    if plan.status == "unchanged":
        return {"status": "unchanged", "committed": False, "name": plan.name,
                "reason": plan.reason()}
    if plan.blocked:
        return {"status": "conflict", "committed": False, "name": plan.name,
                "reason": plan.reason(),
                "hint": "re-call with force=true to version it (nothing is clobbered)"}

    args = {"path": path, "name": plan.name, "kind": plan.kind, "force": force,
            "content": plan.content}
    gate = _consent(path, "vault_push", args, proposal_id)
    if gate.refused:
        return _refused(gate)
    if not gate.granted:
        return _propose(path, "vault_push", args,
                        {"name": plan.name, "kind": plan.kind,
                         "next_version": plan.next_version, "reason": plan.reason()},
                        target=_artifact_path(path, plan.name),
                        summary=f"push '{plan.name}' (v{plan.next_version}) to the team vault",
                        preview=plan.content, approve=approve, confirm=confirm)
    res = _gated_write(
        _mokata_dir(path), "config", _artifact_path(path, plan.name), plan.content,
        lambda: commit_push(path, plan, author=author).version, gate,
        _policy(path, "vault_push", human_approved=True))
    res["name"] = plan.name
    res["version"] = res.get("result")
    return res


@_tool("session_push", "write")
def session_push(path: str = ".", tag: str = "", run_id: str = "", author: str = "",
                 force: bool = False, transport: str = "local", approve: bool = False,
                 confirm: Optional[bool] = None, save_first: bool = False,
                 allow_in_progress: bool = False,
                 requirements_only: bool = False, proposal_id: str = "") -> Dict[str, Any]:
    """Stage 55a/55b + SS.S4 — package the CURRENT session into a MACHINE-PATH-FREE, VERSIONED (v2),
    secret-scanned bundle (session_state + a bounded, per-turn-scanned transcript + counts-only
    meta) and share it over `transport` (local | vault | postgres). HUMAN-GATED (SI.3): PROPOSE-ONLY
    — it reports what WOULD happen and writes nothing. A human mints the approval with
    `mokata approve <id>`; re-call with that `proposal_id` to write through the WriteGate (a secret
    in the session — or a single transcript turn — is hard-blocked even when approved) — on EVERY
    transport. A changed re-push needs `force=true` (never a silent clobber); no session in progress
    → a friendly no-op. An unreachable remote degrades clean (status 'unavailable') and NEVER
    silently falls back to a less-secure store.

    SS.S4 flags:
      * `save_first`         — snapshot through the ungated SS.S0/SS.S2 save path FIRST, then
                               bundle that snapshot (one atomic action).
      * `allow_in_progress`  — REQUIRED to share an IN-PROGRESS (not-yet-approved) session; without
                               it such a push REFUSES (status 'in_progress') with an honest summary
                               + a hint. Completed sessions need no flag.
      * `requirements_only`  — bundle ONLY the distilled requirements (anchor + synthesis +
                               requirement lines; NO approaches/approval/transcript/checkpoints),
                               marked cross-repo with an origin-repo label the receiver sees."""
    from .. import session_bundle as SB
    from .. import session_transport as STX
    try:
        t = STX.make_transport(transport, path)
    except STX.SessionTransportUnavailable as exc:
        return {"status": "unavailable", "committed": False, "transport": transport,
                "message": str(exc)}
    try:
        plan = SB.plan_session_push(path, _surface(path), tag, run_id=run_id or None,
                                    force=force, author=author, transport=t,
                                    save_first=save_first,
                                    requirements_only=requirements_only)
    except SB.SessionBundleError as exc:
        return {"status": "error", "message": str(exc)}
    if plan.status == "empty":
        return {"status": "empty", "committed": False, "reason": plan.reason()}
    if plan.status == "unchanged":
        return {"status": "unchanged", "committed": False, "tag": plan.tag,
                "reason": plan.reason()}
    if plan.blocked:
        return {"status": "conflict", "committed": False, "tag": plan.tag,
                "reason": plan.reason(),
                "hint": "re-call with force=true to overwrite (the prior bundle is replaced)"}
    in_progress = SB.in_progress_summary(plan.bundle)   # SS.S3 — honest label of what leaves
    transcript = SB.transcript_summary(plan.bundle)     # SS.S4 — counts-only (never turn text)
    # SS.S4 — sharing UNFINISHED thinking (a not-yet-approved brainstorm) is now an EXPLICIT
    # consent. A checkpoint-only / spec-only / approved session is unaffected; a requirements-only
    # bundle already IS that consent (it distils to requirements only), so it is exempt.
    if SB.shares_unfinished_thinking(plan.bundle) and not allow_in_progress \
            and not requirements_only:
        return {"status": "in_progress", "committed": False, "tag": plan.tag,
                "in_progress": in_progress, "transcript": transcript,
                "reason": (f"refusing to share an in-progress session ({in_progress['label']}) — "
                           "unfinished thinking needs explicit consent"),
                "hint": "re-call with allow_in_progress=true to share this in-progress session "
                        "(or requirements_only=true to share just the distilled requirements)"}

    args = {"path": path, "tag": plan.tag, "transport": transport, "force": force,
            "requirements_only": requirements_only, "allow_in_progress": allow_in_progress,
            "fingerprint": SB.bundle_hash(plan.bundle) if hasattr(SB, "bundle_hash")
            else plan.bundle.get("content_hash", "")}
    gate = _consent(path, "session_push", args, proposal_id)
    if gate.refused:
        return _refused(gate)
    if not gate.granted:
        return _propose(path, "session_push", args,
                        {"tag": plan.tag, "resume": plan.bundle.get("resume"),
                         "in_progress": in_progress, "transcript": transcript,
                         "cross_repo": plan.bundle.get("cross_repo", False),
                         "origin_repo": plan.bundle.get("origin_repo", ""),
                         "reason": plan.reason()},
                        target=f"session:{plan.tag}",
                        summary=f"share session '{plan.tag}' over {transport}",
                        approve=approve, confirm=confirm)
    ledger = AuditLedger.from_mokata_dir(_mokata_dir(path))
    res = SB.commit_session_push_gated(
        plan, ledger=ledger,
        policy=_policy(path, "session_push", human_approved=True))   # human-approved (SI.3)
    approval.record_redemption(ledger, _require(gate), committed=res.committed)
    out = {"status": "committed" if res.committed else "blocked",
           "committed": res.committed, "tag": plan.tag, "reason": res.reason,
           "in_progress": in_progress, "transcript": transcript,
           "bundle_version": plan.bundle.get("schema_version"),
           "cross_repo": plan.bundle.get("cross_repo", False),
           "origin_repo": plan.bundle.get("origin_repo", ""),
           "findings": [f.kind for f in res.findings]}
    if not res.committed and res.findings:
        # P23 — name the offending KEY(s) and TURN(s) so the refusal is actionable, never the
        # secret VALUE.
        out["blocked_keys"] = sorted(SB.scan_bundle_values(plan.bundle.get("state", {})).keys())
        out["blocked_turns"] = sorted(SB.scan_transcript_turns(plan.bundle).keys())
    return out


@_tool("session_pull", "write")
def session_pull(path: str = ".", tag: str = "", into: str = "", force: bool = False,
                 transport: str = "local", approve: bool = False,
                 confirm: Optional[bool] = None, proposal_id: str = "") -> Dict[str, Any]:
    """Stage 55a/55b — pull a tagged session bundle over `transport` (local | vault | postgres) and
    re-hydrate it into a repo so `mokata resume` continues the work. The bundle is UNTRUSTED, so this
    is HUMAN-GATED (SI.3) and SECRET-SCANNED on pull — on EVERY transport: it is PROPOSE-ONLY (it
    reports what WOULD hydrate and writes nothing), and only an approval a human minted with
    `mokata approve <id>`, referenced back as `proposal_id`, hydrates it through the WriteGate (a
    secret is hard-blocked). The content-hash is verified (corruption caught from any source), and a
    CROSS-CODEBASE fingerprint mismatch is surfaced (status 'mismatch') and NOT applied unless
    `force=true`. `into` is the target repo (default: this repo). An unreachable remote degrades
    clean (status 'unavailable'). The HARD-GATE survives: a not-yet-approved brainstorm stays not
    approved."""
    from .. import session_bundle as SB
    from .. import session_transport as STX
    try:
        t = STX.make_transport(transport, path)
    except STX.SessionTransportUnavailable as exc:
        return {"status": "unavailable", "committed": False, "transport": transport,
                "message": str(exc)}
    target = into or path
    try:
        plan = SB.plan_session_pull(path, tag, target, force=force, transport=t)
    except SB.SessionBundleError as exc:
        return {"status": "error", "message": str(exc)}
    if plan.status == "missing":
        return {"status": "missing", "committed": False, "tag": plan.tag,
                "reason": plan.reason()}
    if plan.status == "mismatch":
        return {"status": "mismatch", "committed": False, "tag": plan.tag,
                "reason": plan.reason(), "bundle_fingerprint": plan.bundle_fingerprint,
                "target_fingerprint": plan.target_fingerprint,
                "hint": "re-call with force=true to apply it here anyway (explicit override)"}

    args = {"path": path, "tag": plan.tag, "into": target, "transport": transport, "force": force}
    gate = _consent(path, "session_pull", args, proposal_id)
    if gate.refused:
        return _refused(gate)
    if not gate.granted:
        return _propose(path, "session_pull", args,
                        {"tag": plan.tag, "into": target,
                         "resume": plan.bundle.get("resume"),
                         "in_progress": SB.in_progress_summary(plan.bundle),
                         "transcript": SB.transcript_summary(plan.bundle),
                         "cross_repo": plan.cross_repo, "origin_repo": plan.origin_repo,
                         "reason": plan.reason()},
                        target=f"session:{plan.tag}",
                        summary=f"hydrate session '{plan.tag}' into {target}",
                        approve=approve, confirm=confirm)
    target_surface = _surface(target)
    ledger = AuditLedger.from_mokata_dir(_mokata_dir(target))
    res = SB.hydrate_bundle(
        target_surface, plan.bundle, ledger=ledger,
        policy=_policy(path, "session_pull", human_approved=True))    # human-approved (SI.3)
    approval.record_redemption(ledger, _require(gate), committed=res.committed)
    return {"status": "committed" if res.committed else "blocked",
            "committed": res.committed, "tag": plan.tag, "into": target,
            "resume": plan.bundle.get("resume"),
            "transcript": SB.transcript_summary(plan.bundle),
            "cross_repo": plan.cross_repo, "origin_repo": plan.origin_repo,
            "reason": res.reason, "findings": [f.kind for f in res.findings]}


@_tool("session_name", "write")
def session_name(path: str = ".", tag: str = "", new: str = "", force: bool = False,
                 transport: str = "local", approve: bool = False,
                 confirm: Optional[bool] = None, proposal_id: str = "") -> Dict[str, Any]:
    """Stage 55b — give a tagged session a human-friendly name (rename) over `transport`
    (local | vault | postgres). HUMAN-GATED (SI.3) where it writes durable: PROPOSE-ONLY — it reports
    what WOULD change and writes nothing; a human mints the approval with `mokata approve <id>`, and
    re-calling with that `proposal_id` moves the bundle through the WriteGate. Idempotent (renaming
    to the current name is a no-op); a name collision is REFUSED unless `force=true` (NEVER a silent
    clobber); provenance is preserved and the content-hash is untouched. An unreachable remote
    degrades clean (status 'unavailable')."""
    from .. import session_bundle as SB
    from .. import session_transport as STX
    try:
        t = STX.make_transport(transport, path)
    except STX.SessionTransportUnavailable as exc:
        return {"status": "unavailable", "committed": False, "transport": transport,
                "message": str(exc)}
    try:
        plan = SB.plan_session_rename(path, tag, new, transport=t, force=force)
    except SB.SessionBundleError as exc:
        return {"status": "error", "message": str(exc)}
    if plan.status == "noop":
        return {"status": "noop", "committed": False, "old": plan.old, "new": plan.new,
                "reason": plan.reason()}
    if plan.status == "missing":
        return {"status": "missing", "committed": False, "old": plan.old, "new": plan.new,
                "reason": plan.reason()}
    if plan.status == "collision":
        return {"status": "conflict", "committed": False, "old": plan.old, "new": plan.new,
                "reason": plan.reason(),
                "hint": "re-call with force=true to overwrite the colliding name"}

    args = {"path": path, "old": plan.old, "new": plan.new, "transport": transport, "force": force}
    gate = _consent(path, "session_name", args, proposal_id)
    if gate.refused:
        return _refused(gate)
    if not gate.granted:
        return _propose(path, "session_name", args,
                        {"old": plan.old, "new": plan.new, "reason": plan.reason()},
                        target=f"session:{plan.old}",
                        summary=f"rename session '{plan.old}' -> '{plan.new}'",
                        approve=approve, confirm=confirm)
    ledger = AuditLedger.from_mokata_dir(_mokata_dir(path))
    res = SB.commit_session_rename_gated(
        plan, ledger=ledger,
        policy=_policy(path, "session_name", human_approved=True))    # human-approved (SI.3)
    approval.record_redemption(ledger, _require(gate), committed=res.committed)
    return {"status": "committed" if res.committed else "blocked",
            "committed": res.committed, "old": plan.old, "new": plan.new, "reason": res.reason,
            "findings": [f.kind for f in res.findings]}


@_tool("audit_share", "write")
def audit_share(path: str = ".", approve: bool = False,
                confirm: Optional[bool] = None, proposal_id: str = "") -> Dict[str, Any]:
    """Stage 71 — publish this dev's NEW local audit entries to the team's SHARED log (the team's OWN
    managed Postgres — NO telemetry, nothing phoned home to mokata/Anthropic). OPT-IN
    (`settings.audit.shared`) + LOCAL-FIRST. HUMAN-GATED (SI.3): PROPOSE-ONLY — it reports how many
    entries WOULD publish and writes nothing. A human mints the approval with `mokata approve <id>`;
    re-calling with that `proposal_id` publishes through the universal WriteGate (kind `send`: a
    secret is hard-blocked even when approved), APPEND-ONLY + per-actor + namespaced so concurrent
    teammates never clobber each other. Degrade-clean: sharing off / no driver-or-DSN → a clear
    message, the log stays LOCAL, no crash. The DSN secret is never stored."""
    from ..team_audit import pending_share, share_audit, shared_enabled
    surface = _surface(path)
    if not shared_enabled(surface.manifest.data):
        return {"status": "disabled", "committed": False,
                "message": ("team audit sharing is OFF (local-first). Opt in with "
                            "`mokata config set settings.audit.shared true`.")}

    args = {"path": path}
    gate = _consent(path, "audit_share", args, proposal_id)
    if gate.refused:
        return _refused(gate)
    if not gate.granted:
        available, pending_n, dsn_env, message = pending_share(path, surface)
        return _propose(path, "audit_share", args,
                        {"available": available, "pending": pending_n, "dsn_env": dsn_env,
                         "message": message},
                        target="team:audit", summary=f"publish {pending_n} audit entr(ies) to the "
                                                     f"team's shared log",
                        approve=approve, confirm=confirm)
    msgs: List[str] = []
    ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
    res = share_audit(path, surface,                          # human-approved (SI.3)
                      policy=_policy(path, "audit_share", human_approved=True),
                      out=msgs.append, ledger=ledger)
    approval.record_redemption(ledger, _require(gate), committed=res.committed)
    status = ("committed" if res.committed and res.published else
              "unavailable" if res.reason == "unavailable" else
              "in_sync" if res.reason == "in sync" else
              "blocked")
    return {"status": status, "committed": res.committed, "published": res.published,
            "reason": res.reason, "findings": [f.kind for f in res.findings],
            "message": res.message, "log": msgs}


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


@_tool("spec_emit", "write")
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

    # Gate 2 — the human. Same consent boundary as every other durable write (SI.3/SI.4).
    args = {"path": path, "title": title, "criteria": criteria or [], "tests": tests or [],
            "approach": approach, "domains": domains or [], "scope": scope or {}}
    gate = _consent(path, EMIT_TOOL, args, proposal_id)
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
                       gate, _policy(path, EMIT_TOOL, human_approved=True))
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


@_tool("spec_amend", "write")
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
                "hint": ("map every acceptance criterion to a test and re-call. The run stays "
                         "regressed to SPEC (development writes are blocked) until a correct "
                         "amendment lands or you abort it — mokata will not let you build against "
                         "a spec it knows is wrong.")}

    # Gate 3 — the HUMAN (SI.3). While this is pending, the run is REGRESSED and writes are blocked:
    # that is not a gap in the enforcement, it IS the enforcement.
    args = {"path": path, "title": title, "criteria": criteria or [], "tests": tests or [],
            "approach": approach, "domains": domains or [], "scope": scope or {},
            "reason": reason, "item": item}
    gate = _consent(path, AMEND_TOOL, args, proposal_id)
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
            approve=approve, confirm=confirm)
        out["note"] = ("the run has REGRESSED to the SPEC phase — development writes are blocked "
                       "until this amendment is approved or aborted.")
        return out

    wgate = WriteGate(ledger=ledger, trust=_trust(path))
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


@_tool("spec_check", "write")
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
    surface = _surface(path)
    change = ChangeSet(
        symbols=[s.strip() for s in symbols.split(",") if s.strip()],
        files=[f.strip() for f in files.split(",") if f.strip()], text=text)
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
    gate = _consent(path, "spec_check", args, proposal_id)
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


@_tool("init", "write")
def init(path: str = ".", profile: str = "standard", approve: bool = False,
         force: bool = False, confirm: Optional[bool] = None,
         proposal_id: str = "") -> Dict[str, Any]:
    """Initialize mokata in a repo (write .mokata/manifest.json + constitution) so a new project can
    be set up from inside Claude Code — no terminal trip for the SETUP itself. HUMAN-GATED (SI.3):
    PROPOSE-ONLY — it PREVIEWS the plan (detected tools, profile, files it would write) and writes
    nothing. A human mints the approval with `mokata approve <id>`; re-calling with that
    `proposal_id` applies it. An existing manifest needs `force=true` to overwrite (a profile switch
    is never silent)."""
    from ..init import init_repo, plan_init, render_plan
    from ..profiles import profile_names
    if profile not in profile_names():
        return {"status": "error",
                "message": f"unknown profile '{profile}'; choose one of {profile_names()}"}
    already = Surface.is_initialized(path)

    args = {"path": path, "profile": profile, "force": force}
    gate = _consent(path, "init", args, proposal_id)
    if gate.refused:
        return _refused(gate)
    if not gate.granted:
        return _propose(path, "init", args,
                        {"profile": profile, "already_initialized": already,
                         "preview": render_plan(plan_init(path, profile))},
                        target=_mokata_dir(path),
                        summary=f"initialize mokata (profile: {profile})"
                                + (" — OVERWRITING the existing manifest" if already and force
                                   else ""),
                        preview=render_plan(plan_init(path, profile)),
                        approve=approve, confirm=confirm)
    if already and not force:
        return {"status": "blocked", "committed": False,
                "reason": "a manifest already exists — re-call with force=true to "
                          "overwrite (a profile switch is never silent)"}
    box: Dict[str, Any] = {}

    def _do_init() -> Any:
        res = init_repo(root=path, profile=profile, assume_yes=True, force=force,
                        out=lambda *_a: None)
        box["init"] = res
        return {"written": res.written, "aborted": res.aborted, "profile": profile}

    return _gated_write(_mokata_dir(path), "config", _mokata_dir(path), "", _do_init, gate,
                        _policy(path, "init", human_approved=True))


@_tool("reconfigure", "write")
def reconfigure(path: str = ".", profile: str = "", add: str = "", remove: str = "",
                approve: bool = False, confirm: Optional[bool] = None,
                proposal_id: str = "") -> Dict[str, Any]:
    """Stage 56b — re-runnable reconfigure: change what's wired on an ALREADY-INITIALIZED repo
    (switch `profile`, `add`/`remove` integrations — comma-separated tool ids) WITHOUT a terminal
    trip. HUMAN-GATED (SI.3): PROPOSE-ONLY — it returns the current→proposed DIFF and writes nothing.
    A human mints the approval with `mokata approve <id>`; re-calling with that `proposal_id` applies
    it (gated, idempotent, reversible — a removed integration leaves no residue; an ABSENT add is
    recommended, never installed). No changes → a friendly no-op; an uninitialized repo degrades
    clean (run `init`/`setup` first)."""
    from .. import onboarding
    if not Surface.is_initialized(path):
        return {"status": "uninitialized", "committed": False,
                "message": "this repo isn't initialized — run init/setup first"}
    add_l = [t.strip() for t in add.split(",") if t.strip()]
    rem_l = [t.strip() for t in remove.split(",") if t.strip()]
    plan = onboarding.plan_reconfigure(path, profile=(profile or None),
                                       add=(add_l or None), remove=(rem_l or None))
    if not plan.changed:
        return {"status": "unchanged", "committed": False,
                "reason": "no changes — your setup already matches",
                "recommended": plan.recommended}

    args = {"path": path, "profile": profile, "add": add_l, "remove": rem_l}
    gate = _consent(path, "reconfigure", args, proposal_id)
    if gate.refused:
        return _refused(gate)
    if not gate.granted:
        return _propose(path, "reconfigure", args,
                        {"diff": onboarding.render_reconfigure_diff(plan),
                         "added": plan.added, "removed": plan.removed,
                         "profile": plan.target_profile if plan.profile_changed else None,
                         "recommended": plan.recommended},
                        target=_mokata_dir(path), summary="reconfigure what mokata has wired",
                        preview=onboarding.render_reconfigure_diff(plan),
                        approve=approve, confirm=confirm)
    ledger = AuditLedger.from_mokata_dir(_mokata_dir(path))
    res = onboarding.run_reconfigure(path, profile=(profile or None), add=(add_l or None),
                                     remove=(rem_l or None),
                                     assume_yes=True,        # human-approved (SI.3)
                                     ledger=ledger, out=lambda *_a: None)
    approval.record_redemption(ledger, _require(gate), committed=res.changed)
    return {"status": "committed" if res.changed else "blocked", "committed": res.changed,
            "added": res.added, "removed": res.removed, "profile": res.profile,
            "recommended": res.recommended}


@_tool("config_set", "write")
def config_set(path: str = ".", key: str = "", value: str = "", approve: bool = False,
               confirm: Optional[bool] = None, proposal_id: str = "") -> Dict[str, Any]:
    """Set a dotted backend-config key in the committed manifest (Stage 24A), e.g.
    `tools.sqlite.config.path`. HUMAN-GATED (SI.3): PROPOSE-ONLY — it PREVIEWS the old->new change
    and writes nothing. A human mints the approval with `mokata approve <id>`; re-calling with that
    `proposal_id` writes it through the WriteGate. A secret in the resulting manifest (an inline
    DSN/credential) is a HARD BLOCK even when approved — reference an env var (e.g. config.dsn_env)
    instead. A structurally-invalid edit is refused, not committed."""
    from .. import config_cmd
    ledger = AuditLedger.from_mokata_dir(_mokata_dir(path))
    msgs: List[str] = []

    args = {"path": path, "key": key, "value": value}
    gate = _consent(path, "config_set", args, proposal_id)
    if gate.refused:
        return _refused(gate)
    try:
        if not gate.granted:
            # Propose: run the full secret-scan + schema-validate, but NEVER commit.
            res = config_cmd.config_set(path, key, value, assume_yes=False,
                                        confirm=lambda _q: False, out=msgs.append,
                                        ledger=ledger)
            if res.findings:
                return {"status": "blocked", "committed": False,
                        "reason": "secret detected in the manifest — reference an env var "
                                  "instead (e.g. config.dsn_env)",
                        "findings": [f.kind for f in res.findings], "detail": msgs}
            return _propose(path, "config_set", args,
                            {"key": key, "old": res.old, "new": res.new, "detail": msgs},
                            target=f"config:{key}",
                            summary=f"set {key}: {res.old!r} -> {res.new!r}",
                            approve=approve, confirm=confirm)
        res = config_cmd.config_set(path, key, value,      # human-approved (SI.3)
                                    policy=_policy(path, "config_set", human_approved=True),
                                    out=msgs.append, ledger=ledger)
    except config_cmd.ConfigCommandError as exc:
        return {"status": "error", "message": str(exc)}
    approval.record_redemption(ledger, _require(gate), committed=res.committed)
    return {"status": "committed" if res.committed else "blocked",
            "committed": res.committed, "key": key, "old": res.old, "new": res.new,
            "findings": [f.kind for f in res.findings], "reason": res.message, "detail": msgs}


@_tool("export_stack", "write")
def export_stack(path: str = ".", file: str = "", approve: bool = False,
                 confirm: Optional[bool] = None, proposal_id: str = "") -> Dict[str, Any]:
    """Export the current manifest as a shareable stack file (J3). HUMAN-GATED (SI.3): PROPOSE-ONLY —
    it reports what WOULD be written and writes no file. A human mints the approval with
    `mokata approve <id>`; re-calling with that `proposal_id` writes it through the WriteGate — the
    exported content is secret-scanned at EGRESS strength (SI.6b): a hit HARD-BLOCKS that key — it is
    named in `blocked_keys` and left out of the artifact entirely. Default destination is
    .mokata/mokata-stack.json. The read-only counterpart is `export_preview`."""
    from ..share import SHARE_FILENAME, export_manifest, plan_export
    surface = _surface(path)
    plan = plan_export(surface)                 # scans + drops; writes nothing
    dest = file or os.path.join(path, MOKATA_DIR, SHARE_FILENAME)
    if plan.refused:
        return {"status": "blocked", "committed": False, "reason": plan.message,
                "blocked": len(plan.blocked), "blocked_keys": plan.blocked}
    content = plan.payload()                    # the REDACTED bytes — what actually leaves

    args = {"path": path, "dest": dest, "content": content}
    gate = _consent(path, "export_stack", args, proposal_id)
    if gate.refused:
        return _refused(gate)
    if not gate.granted:
        return _propose(path, "export_stack", args,
                        {"dest": dest, "profile": plan.data.get("profile"),
                         "blocked": len(plan.blocked), "blocked_keys": plan.blocked},
                        target=dest, summary=f"export this stack to {dest}",
                        preview=content, approve=approve, confirm=confirm)
    # SI.6b: kind `send`, not `config` — an export is EGRESS, so the gate's own scan runs at outbound
    # strength over the exact bytes that leave. Per-key blocking already happened in `plan_export`;
    # this is the belt-and-suspenders on the assembled artifact (the C2 `memory_export` shape).
    res = _gated_write(surface.mokata_dir, "send", dest, content,
                       lambda: (export_manifest(surface, dest=dest), dest)[1], gate,
                       _policy(path, "export_stack", human_approved=True))
    return {"status": res["status"], "committed": res["committed"], "dest": dest,
            "blocked": len(plan.blocked), "blocked_keys": plan.blocked,
            "reason": res["reason"]}


@_tool("stacks_install", "write")
def stacks_install(path: str = ".", name: str = "", source: str = "", force: bool = False,
                   approve: bool = False, confirm: Optional[bool] = None,
                   proposal_id: str = "") -> Dict[str, Any]:
    """Install a catalog stack as this repo's config — the human-gated, secret-scanned ADOPT path
    (reuses `apply_manifest`). HUMAN-GATED (SI.3): PROPOSE-ONLY — it reports what WOULD apply and
    writes nothing. A human mints the approval with `mokata approve <id>`; re-calling with that
    `proposal_id` applies it through the WriteGate, where the stack manifest is secret-scanned so a
    secret is hard-blocked EVEN when approved (community content is untrusted). `force` overwrites an
    existing config. No hosted marketplace is involved."""
    from .. import stacks as ST
    try:
        raw, data = ST.resolve_stack_manifest(name, source=source or None)
    except ST.StackError as exc:
        return {"status": "error", "message": str(exc)}
    stack_meta = (data.get("settings") or {}).get("stack") or {}

    args = {"path": path, "name": name, "source": source, "force": force, "manifest": raw}
    gate = _consent(path, "stacks_install", args, proposal_id)
    if gate.refused:
        return _refused(gate)
    if not gate.granted:
        return _propose(path, "stacks_install", args,
                        {"name": name, "profile": data.get("profile"),
                         "framework": stack_meta.get("framework")},
                        target=_mokata_dir(path),
                        summary=f"install the '{name}' stack as this repo's config",
                        preview=raw, approve=approve, confirm=confirm)
    surface_dir = _mokata_dir(path)
    box: Dict[str, Any] = {}

    def _apply() -> Any:
        from ..share import apply_manifest
        result = apply_manifest(path, data, assume_yes=True, force=force)
        box["apply"] = result
        return {"applied": result.applied, "path": result.path, "message": result.message}

    # Feed the RAW manifest text so the WriteGate secret-scan is the absolute hard block.
    return _gated_write(surface_dir, "config", surface_dir, raw, _apply, gate,
                        _policy(path, "stacks_install", human_approved=True))
