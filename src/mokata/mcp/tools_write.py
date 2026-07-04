"""WRITE / durable tools — ALWAYS human-gated. Propose-only without `approve`.

With no `approve`, each tool is PROPOSE-ONLY: it returns the staged change and writes nothing.
Only an explicit `approve=true` — a human decision — performs the write, and even then it goes
through the universal WriteGate (secrets are a HARD BLOCK that approval cannot override) and is
recorded in the audit ledger. An MCP call NEVER writes silently. (`confirm=true` is accepted as
a deprecated alias for `approve=true`.)

Every function is pure and SDK-free; each registers into the shared `TOOLS` registry via
`@_tool(name, "write")`.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional

from .. import MOKATA_DIR
from ..config import Surface
from ..govern import (AuditLedger, WriteGate, WriteRequest, plan_reset,
                      reset_state)
from ..knowledge import KnowledgeLayer
from ..memory import DECISION, MemoryItem, MemoryStore
from .registry import _mokata_dir, _surface, _tool

__all__ = [
    "remember", "import_stack", "reset", "apply_proposal", "memory_export",
    "memory_import", "vault_push", "session_push", "session_pull", "session_name",
    "audit_share", "spec_check", "init", "reconfigure", "config_set", "export_stack",
    "stacks_install",
]


def _approved(approve: bool, confirm: Optional[bool]) -> bool:
    """The gate boolean for a write tool. `approve` is the convention (a bool, matching
    `assume_yes` elsewhere — the project keeps `confirm` for the Callable gate); `confirm` is a
    DEPRECATED alias kept so earlier MCP callers that passed `confirm=true` still work."""
    return bool(approve) or bool(confirm)


def _gated_write(mokata_dir: str, kind: str, target: str, content: str,
                 commit_fn: Callable[[], Any]) -> Dict[str, Any]:
    """Run a durable write through the universal WriteGate. Secrets are a hard block that
    `confirm` cannot override; the decision is recorded in the audit ledger; the actual
    work happens in `commit_fn`, only if the gate approves."""
    ledger = AuditLedger.from_mokata_dir(mokata_dir)
    gate = WriteGate(ledger=ledger)
    box: Dict[str, Any] = {}
    outcome = gate.submit(
        WriteRequest(kind, target, content=content, actor="mcp"),
        commit=lambda: box.update(result=commit_fn()),
        assume_yes=True)            # the explicit `confirm` IS the human approval
    return {"status": "committed" if outcome.committed else "blocked",
            "committed": outcome.committed,
            "reason": outcome.reason,
            "findings": [f.kind for f in outcome.findings],
            "result": box.get("result")}


@_tool("remember", "write")
def remember(path: str = ".", subject: str = "", value: str = "",
             memory_type: str = DECISION, kind: str = "", approve: bool = False,
             confirm: Optional[bool] = None, mtype: Optional[str] = None) -> Dict[str, Any]:
    """Remember a fact/decision in memory. `memory_type` is the storage tier
    (persistent/decision/episodic); `kind` is the typed project part (rule/guardrail/
    best-practice/context/reference) captured by /mokata:onboard; `mtype` is a DEPRECATED alias
    for `memory_type`. HUMAN-GATED: without `approve=true` this is propose-only and writes
    nothing. With `approve=true` it commits through the WriteGate (a secret in `subject` OR
    `value` is blocked even when approved). A part kind is stored as persistent project knowledge."""
    from ..memory import PART_KINDS, PERSISTENT, normalize_kind
    surface = _surface(path)
    store = MemoryStore.from_surface(surface)
    if not store.enabled_types:
        return {"status": "unavailable", "message": "memory is disabled for this profile"}
    mtype = mtype or memory_type      # `mtype` (deprecated) overrides only if explicitly passed
    norm = normalize_kind(kind)
    if norm in PART_KINDS:
        mtype = PERSISTENT          # the captured "parts" are persistent project knowledge
    item = MemoryItem.create(subject, value, mtype=mtype, kind=norm or kind)
    if not _approved(approve, confirm):
        return {"status": "proposed", "action": "remember",
                "preview": store.render_write(item),
                "hint": "re-call with approve=true to commit (your explicit approval)"}
    # H4: scan subject AND value so a secret pasted into the subject can't slip the gate.
    res = _gated_write(surface.mokata_dir, "memory", f"memory:{subject}",
                       f"{subject}\n{value}",
                       lambda: store.remember(item, assume_yes=True).committed)
    return res


@_tool("import_stack", "write")
def import_stack(path: str = ".", file: str = "", approve: bool = False,
                 force: bool = False, confirm: Optional[bool] = None) -> Dict[str, Any]:
    """Validate and apply a shared stack manifest. HUMAN-GATED: without `approve=true` this
    only validates and reports what WOULD apply (no write). With `approve=true` it applies
    (use `force=true` to overwrite an existing config)."""
    from ..share import apply_manifest, load_shared, validate_shared
    try:
        data = load_shared(file)
    except (OSError, ValueError) as exc:
        return {"status": "error", "message": f"cannot read {file}: {exc}"}
    errors = validate_shared(data)
    if not _approved(approve, confirm):
        return {"status": "proposed", "action": "import",
                "valid": not errors, "errors": errors,
                "would_apply_profile": data.get("profile"),
                "hint": "re-call with approve=true to apply (your explicit approval)"}
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

    res = _gated_write(surface_dir, "config", surface_dir, "", _apply)
    return res


@_tool("reset", "write")
def reset(path: str = ".", keep_config: bool = False,
          approve: bool = False, confirm: Optional[bool] = None) -> Dict[str, Any]:
    """Remove mokata state (.mokata/). HUMAN-GATED: without `approve=true` this lists what
    WOULD be removed (no deletion). With `approve=true` it removes them. `keep_config`
    keeps the manifest + constitution."""
    plan = plan_reset(path, keep_config=keep_config)
    if not plan.targets:
        return {"status": "noop", "message": "reset: nothing to remove."}
    if not _approved(approve, confirm):
        return {"status": "proposed", "action": "reset", "targets": plan.targets,
                "hint": "re-call with approve=true to remove (your explicit approval)"}
    box: Dict[str, Any] = {}

    def _do_reset() -> Any:
        result = reset_state(path, keep_config=keep_config, assume_yes=True)
        box["reset"] = result
        return {"removed": result.removed, "aborted": result.aborted}

    res = _gated_write(_mokata_dir(path), "config", _mokata_dir(path), "", _do_reset)
    return res


@_tool("apply_proposal", "write")
def apply_proposal(path: str = ".", subject: str = "", decision: str = "approve",
                   approve: bool = False, confirm: Optional[bool] = None) -> Dict[str, Any]:
    """Resolve a surfaced self-healing memory proposal (contradiction/staleness).
    HUMAN-GATED: without `approve=true` it shows the staged old->new change (no write).
    With `approve=true` it applies your `decision` (approve/reject/defer)."""
    if decision not in ("approve", "reject", "defer"):
        return {"status": "error",
                "message": "decision must be one of approve/reject/defer"}
    surface = _surface(path)
    store = MemoryStore.from_surface(surface)
    match = next((p for p in store.detect_issues() if p.subject == subject), None)
    if match is None:
        return {"status": "error",
                "message": f"no pending proposal for subject '{subject}'"}
    if not _approved(approve, confirm):
        return {"status": "proposed", "action": "apply_proposal", "subject": subject,
                "kind": match.kind, "diff": match.diff(), "decision": decision,
                "hint": "re-call with approve=true to apply (your explicit approval)"}
    res = _gated_write(
        surface.mokata_dir, "memory", f"memory:{subject}", match.diff(),
        lambda: store.apply_proposal(match, decision, assume_yes=True).message)
    return res


@_tool("memory_export", "write")
def memory_export(path: str = ".", file: str = "", approve: bool = False,
                  confirm: Optional[bool] = None) -> Dict[str, Any]:
    """Export local memory (active items + provenance) to a committable share file. READ-ONLY
    on the store. HUMAN-GATED: without `approve=true` it reports how many items WOULD be
    written (no file); with `approve=true` it writes the share file."""
    from ..memory import MEMORY_SHARE_FILENAME, export_memory
    surface = _surface(path)
    store = MemoryStore.from_surface(surface)
    data = export_memory(store)             # read-only; computes the items without writing
    dest = file or os.path.join(path, MOKATA_DIR, MEMORY_SHARE_FILENAME)
    if not _approved(approve, confirm):
        return {"status": "proposed", "action": "memory_export",
                "items": len(data["items"]), "dest": dest,
                "hint": "re-call with approve=true to write the share file"}
    export_memory(store, dest=dest)
    return {"status": "committed", "committed": True, "dest": dest,
            "items": len(data["items"])}


@_tool("memory_import", "write")
def memory_import(path: str = ".", file: str = "", approve: bool = False,
                  confirm: Optional[bool] = None) -> Dict[str, Any]:
    """Merge a memory share file into local memory. HUMAN-GATED: without `approve=true` it
    validates + reports how many items WOULD merge (no write); with `approve=true` it dedups,
    gate-adds new items, and routes conflicts through the self-healing surface (never a silent
    overwrite). The imported content is UNTRUSTED, so each item is secret-scanned through the
    WriteGate and audit-logged — a secret is HARD-BLOCKED, not imported. Provenance is preserved."""
    from ..memory import import_memory, load_memory_share
    surface = _surface(path)
    try:
        data = load_memory_share(file)
    except (OSError, ValueError) as exc:
        return {"status": "error", "message": f"cannot read {file}: {exc}"}
    if not _approved(approve, confirm):
        n = len(data["items"]) if isinstance(data.get("items"), list) else 0
        return {"status": "proposed", "action": "memory_import", "incoming": n,
                "hint": "re-call with approve=true to merge (dedup + gated healing)"}
    ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
    res = import_memory(MemoryStore.from_surface(surface), data, assume_yes=True,
                        ledger=ledger)
    return {"status": "aborted" if res.aborted else "committed",
            "committed": not res.aborted, "added": res.added, "skipped": res.skipped,
            "resolved": res.resolved, "declined": res.declined,
            "blocked": res.blocked, "errors": res.errors}


@_tool("vault_push", "write")
def vault_push(path: str = ".", name: str = "", file: str = "", kind: str = "",
               author: str = "", force: bool = False,
               approve: bool = False, confirm: Optional[bool] = None) -> Dict[str, Any]:
    """Push a brainstorm/spec markdown artifact into the team vault under `name`. HUMAN-GATED:
    without `approve=true` this reports what WOULD happen (no write). With `approve=true` it
    writes through the WriteGate (a secret in the artifact is blocked even when approved). A
    changed re-push needs `force=true` (it versions, keeping prior metadata — never a silent
    clobber)."""
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
    if not _approved(approve, confirm):
        return {"status": "proposed", "action": "vault_push", "name": plan.name,
                "kind": plan.kind, "next_version": plan.next_version, "reason": plan.reason(),
                "hint": "re-call with approve=true to write (your explicit approval)"}
    res = _gated_write(
        _mokata_dir(path), "config", _artifact_path(path, plan.name), plan.content,
        lambda: commit_push(path, plan, author=author).version)
    res["name"] = plan.name
    res["version"] = res.get("result")
    return res


@_tool("session_push", "write")
def session_push(path: str = ".", tag: str = "", run_id: str = "", author: str = "",
                 force: bool = False, transport: str = "local", approve: bool = False,
                 confirm: Optional[bool] = None) -> Dict[str, Any]:
    """Stage 55a/55b — package the CURRENT session (run checkpoint(s) + approved approach +
    emitted spec + in-progress brainstorm) into a MACHINE-PATH-FREE, versioned, secret-scanned
    bundle and share it over `transport` (local | vault | postgres). HUMAN-GATED: without
    `approve=true` this reports what WOULD happen (no write). With `approve=true` it writes through
    the WriteGate (a secret in the session is hard-blocked even when approved) — on EVERY
    transport. A changed re-push needs `force=true` (never a silent clobber); no session in
    progress → a friendly no-op. An unreachable remote (no psycopg/DSN) degrades clean
    (status 'unavailable') and NEVER silently falls back to a less-secure store."""
    from .. import session_bundle as SB
    from .. import session_transport as STX
    try:
        t = STX.make_transport(transport, path)
    except STX.SessionTransportUnavailable as exc:
        return {"status": "unavailable", "committed": False, "transport": transport,
                "message": str(exc)}
    try:
        plan = SB.plan_session_push(path, _surface(path), tag, run_id=run_id or None,
                                    force=force, author=author, transport=t)
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
    if not _approved(approve, confirm):
        return {"status": "proposed", "action": "session_push", "tag": plan.tag,
                "resume": plan.bundle.get("resume"), "reason": plan.reason(),
                "hint": "re-call with approve=true to write (your explicit approval)"}
    ledger = AuditLedger.from_mokata_dir(_mokata_dir(path))
    res = SB.commit_session_push_gated(plan, ledger=ledger, assume_yes=True)
    return {"status": "committed" if res.committed else "blocked",
            "committed": res.committed, "tag": plan.tag, "reason": res.reason,
            "findings": [f.kind for f in res.findings]}


@_tool("session_pull", "write")
def session_pull(path: str = ".", tag: str = "", into: str = "", force: bool = False,
                 transport: str = "local", approve: bool = False,
                 confirm: Optional[bool] = None) -> Dict[str, Any]:
    """Stage 55a/55b — pull a tagged session bundle over `transport` (local | vault | postgres)
    and re-hydrate it into a repo so `mokata resume` continues the work. The bundle is UNTRUSTED,
    so this is HUMAN-GATED and SECRET-SCANNED on pull — on EVERY transport: without `approve=true`
    it reports what WOULD hydrate (no write); with `approve=true` it hydrates through the WriteGate
    (a secret is hard-blocked). The content-hash is verified (corruption caught from any source),
    and a CROSS-CODEBASE fingerprint mismatch is surfaced (status 'mismatch') and NOT applied
    unless `force=true`. `into` is the target repo (default: this repo). An unreachable remote
    degrades clean (status 'unavailable'). The HARD-GATE survives: a not-yet-approved brainstorm
    stays not approved."""
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
    if not _approved(approve, confirm):
        return {"status": "proposed", "action": "session_pull", "tag": plan.tag,
                "into": target, "resume": plan.bundle.get("resume"), "reason": plan.reason(),
                "hint": "re-call with approve=true to hydrate (your explicit approval)"}
    target_surface = _surface(target)
    ledger = AuditLedger.from_mokata_dir(_mokata_dir(target))
    res = SB.hydrate_bundle(target_surface, plan.bundle, ledger=ledger, assume_yes=True)
    return {"status": "committed" if res.committed else "blocked",
            "committed": res.committed, "tag": plan.tag, "into": target,
            "resume": plan.bundle.get("resume"), "reason": res.reason,
            "findings": [f.kind for f in res.findings]}


@_tool("session_name", "write")
def session_name(path: str = ".", tag: str = "", new: str = "", force: bool = False,
                 transport: str = "local", approve: bool = False,
                 confirm: Optional[bool] = None) -> Dict[str, Any]:
    """Stage 55b — give a tagged session a human-friendly name (rename) over `transport`
    (local | vault | postgres). HUMAN-GATED where it writes durable: without `approve=true` it
    reports what WOULD change (no write); with `approve=true` it moves the bundle through the
    WriteGate. Idempotent (renaming to the current name is a no-op); a name collision is REFUSED
    unless `force=true` (NEVER a silent clobber); provenance is preserved and the content-hash is
    untouched. An unreachable remote degrades clean (status 'unavailable')."""
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
    if not _approved(approve, confirm):
        return {"status": "proposed", "action": "session_name", "old": plan.old,
                "new": plan.new, "reason": plan.reason(),
                "hint": "re-call with approve=true to rename (your explicit approval)"}
    ledger = AuditLedger.from_mokata_dir(_mokata_dir(path))
    res = SB.commit_session_rename_gated(plan, ledger=ledger, assume_yes=True)
    return {"status": "committed" if res.committed else "blocked",
            "committed": res.committed, "old": plan.old, "new": plan.new, "reason": res.reason,
            "findings": [f.kind for f in res.findings]}


@_tool("audit_share", "write")
def audit_share(path: str = ".", approve: bool = False,
                confirm: Optional[bool] = None) -> Dict[str, Any]:
    """Stage 71 — publish this dev's NEW local audit entries to the team's SHARED log (the team's
    OWN managed Postgres — NO telemetry, nothing phoned home to mokata/Anthropic). OPT-IN
    (`settings.audit.shared`) + LOCAL-FIRST. HUMAN-GATED: without `approve=true` this is
    propose-only — it reports how many entries WOULD publish and writes nothing. With
    `approve=true` it publishes through the universal WriteGate (kind `send`: a secret is
    hard-blocked even when approved), APPEND-ONLY + per-actor + namespaced so concurrent
    teammates never clobber each other. Degrade-clean: sharing off / no driver-or-DSN → a clear
    message, the log stays LOCAL, no crash. The DSN secret is never stored."""
    from ..team_audit import pending_share, share_audit, shared_enabled
    surface = _surface(path)
    if not shared_enabled(surface.manifest.data):
        return {"status": "disabled", "committed": False,
                "message": ("team audit sharing is OFF (local-first). Opt in with "
                            "`mokata config set settings.audit.shared true`.")}
    if not _approved(approve, confirm):
        available, pending, dsn_env, message = pending_share(path, surface)
        return {"status": "proposed", "action": "audit_share", "available": available,
                "pending": pending, "dsn_env": dsn_env, "message": message,
                "hint": "re-call with approve=true to publish (your explicit approval)"}
    msgs: List[str] = []
    res = share_audit(path, surface, assume_yes=True, out=msgs.append,
                      ledger=AuditLedger.from_mokata_dir(surface.mokata_dir))
    status = ("committed" if res.committed and res.published else
              "unavailable" if res.reason == "unavailable" else
              "in_sync" if res.reason == "in sync" else
              "blocked")
    return {"status": status, "committed": res.committed, "published": res.published,
            "reason": res.reason, "findings": [f.kind for f in res.findings],
            "message": res.message, "log": msgs}


@_tool("spec_check", "write")
def spec_check(path: str = ".", symbols: str = "", files: str = "", text: str = "",
               phase: str = "develop", approve: bool = False,
               confirm: Optional[bool] = None) -> Dict[str, Any]:
    """Stage 37 regression guard: check a change's touch-set (comma-separated `symbols`/`files`)
    against the SAVED specs + decision memory. If it would affect one, this is HUMAN-GATED:
    without `approve=true` it SURFACES the conflict and writes nothing but the surfaced-deviation
    record (status 'blocked'); with `approve=true` it records your confirmation (amend/supersede)
    through the deviation gate. No saved corpus → 'skipped'; no graph → lexical/file overlap (the
    result says so). Frugal: only the touch-set is checked."""
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
    ledger.record("spec_conflict", phase=phase, degraded=report.degraded,
                  conflicts=[c.to_dict() for c in report.conflicts],
                  touch_set=report.touch_set)
    refs = ", ".join(f"{c.source_kind} '{c.ref}'" for c in report.conflicts)
    req = DeviationRequest(
        what=f"this change affects saved {refs}",
        why="the touched surface is already specified/decided",
        options=["confirm + amend/supersede the affected spec(s)/decision(s)",
                 "re-plan so the change does not break them"],
        target=ACCEPTANCE_CRITERIA, phase=phase)
    gate = DeviationGate(ledger)
    if not _approved(approve, confirm):
        gate.request(req)       # log that it was surfaced (proposed); resolve nothing yet
        return {"status": "blocked", "committed": False,
                "conflicts": [c.to_dict() for c in report.conflicts],
                "degraded": report.degraded, "render": report.render(),
                "hint": "re-call with approve=true to confirm the change (amend/supersede), "
                        "or re-plan so it doesn't break the saved spec/decision"}
    outcome = gate.submit(req, assume_yes=True)
    return {"status": "confirmed", "committed": True, "reason": outcome.reason,
            "conflicts": [c.to_dict() for c in report.conflicts]}


@_tool("init", "write")
def init(path: str = ".", profile: str = "standard", approve: bool = False,
         force: bool = False, confirm: Optional[bool] = None) -> Dict[str, Any]:
    """Initialize mokata in a repo (write .mokata/manifest.json + constitution) so a new
    project can be set up from inside Claude Code — no terminal trip. HUMAN-GATED: without
    `approve=true` this PREVIEWS the plan (detected tools, profile, files it would write)
    and writes nothing. With `approve=true` it applies; an existing manifest needs
    `force=true` to overwrite (a profile switch is never silent)."""
    from ..init import init_repo, plan_init, render_plan
    from ..profiles import profile_names
    if profile not in profile_names():
        return {"status": "error",
                "message": f"unknown profile '{profile}'; choose one of {profile_names()}"}
    already = Surface.is_initialized(path)
    if not _approved(approve, confirm):
        return {"status": "proposed", "action": "init", "profile": profile,
                "already_initialized": already,
                "preview": render_plan(plan_init(path, profile)),
                "hint": ("re-call with approve=true to apply"
                         + (" plus force=true to overwrite the existing manifest"
                            if already else ""))}
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

    return _gated_write(_mokata_dir(path), "config", _mokata_dir(path), "", _do_init)


@_tool("reconfigure", "write")
def reconfigure(path: str = ".", profile: str = "", add: str = "", remove: str = "",
                approve: bool = False, confirm: Optional[bool] = None) -> Dict[str, Any]:
    """Stage 56b — re-runnable reconfigure: change what's wired on an ALREADY-INITIALIZED repo
    (switch `profile`, `add`/`remove` integrations — comma-separated tool ids) WITHOUT a terminal
    trip. HUMAN-GATED: without `approve=true` it returns the current→proposed DIFF and writes
    nothing; with `approve=true` it applies (gated, idempotent, reversible — a removed integration
    leaves no residue; an ABSENT add is recommended, never installed). No changes → a friendly
    no-op; an uninitialized repo degrades clean (run `init`/`setup` first)."""
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
    if not _approved(approve, confirm):
        return {"status": "proposed", "action": "reconfigure",
                "diff": onboarding.render_reconfigure_diff(plan),
                "added": plan.added, "removed": plan.removed,
                "profile": plan.target_profile if plan.profile_changed else None,
                "recommended": plan.recommended,
                "hint": "re-call with approve=true to apply (your explicit approval)"}
    ledger = AuditLedger.from_mokata_dir(_mokata_dir(path))
    res = onboarding.run_reconfigure(path, profile=(profile or None), add=(add_l or None),
                                     remove=(rem_l or None), assume_yes=True, ledger=ledger,
                                     out=lambda *_a: None)
    return {"status": "committed" if res.changed else "blocked", "committed": res.changed,
            "added": res.added, "removed": res.removed, "profile": res.profile,
            "recommended": res.recommended}


@_tool("config_set", "write")
def config_set(path: str = ".", key: str = "", value: str = "", approve: bool = False,
               confirm: Optional[bool] = None) -> Dict[str, Any]:
    """Set a dotted backend-config key in the committed manifest (Stage 24A), e.g.
    `tools.sqlite.config.path`. HUMAN-GATED: without `approve=true` it PREVIEWS the old->new
    change and writes nothing; with `approve=true` it writes through the WriteGate. A secret
    in the resulting manifest (an inline DSN/credential) is a HARD BLOCK even when approved —
    reference an env var (e.g. config.dsn_env) instead. A structurally-invalid edit is refused,
    not committed."""
    from .. import config_cmd
    ledger = AuditLedger.from_mokata_dir(_mokata_dir(path))
    msgs: List[str] = []
    try:
        if not _approved(approve, confirm):
            # Propose: run the full secret-scan + schema-validate, but NEVER commit.
            res = config_cmd.config_set(path, key, value, assume_yes=False,
                                        confirm=lambda _q: False, out=msgs.append,
                                        ledger=ledger)
            if res.findings:
                return {"status": "blocked", "committed": False,
                        "reason": "secret detected in the manifest — reference an env var "
                                  "instead (e.g. config.dsn_env)",
                        "findings": [f.kind for f in res.findings], "detail": msgs}
            return {"status": "proposed", "action": "config_set", "key": key,
                    "old": res.old, "new": res.new, "detail": msgs,
                    "hint": "re-call with approve=true to write (your explicit approval)"}
        res = config_cmd.config_set(path, key, value, assume_yes=True, out=msgs.append,
                                    ledger=ledger)
    except config_cmd.ConfigCommandError as exc:
        return {"status": "error", "message": str(exc)}
    return {"status": "committed" if res.committed else "blocked",
            "committed": res.committed, "key": key, "old": res.old, "new": res.new,
            "findings": [f.kind for f in res.findings], "reason": res.message, "detail": msgs}


@_tool("export_stack", "write")
def export_stack(path: str = ".", file: str = "", approve: bool = False,
                 confirm: Optional[bool] = None) -> Dict[str, Any]:
    """Export the current manifest as a shareable stack file (J3). HUMAN-GATED: without
    `approve=true` it reports what WOULD be written (no file); with `approve=true` it writes
    through the WriteGate — the exported content is secret-scanned, so a secret is hard-blocked
    even when approved. Default destination is .mokata/mokata-stack.json. The read-only
    counterpart is `export_preview`."""
    from ..manifest import Manifest
    from ..share import SHARE_FILENAME, export_manifest
    surface = _surface(path)
    data = export_manifest(surface)             # dest=None → no write
    dest = file or os.path.join(path, MOKATA_DIR, SHARE_FILENAME)
    content = Manifest.from_dict(data).to_json()
    if not _approved(approve, confirm):
        return {"status": "proposed", "action": "export_stack", "dest": dest,
                "profile": data.get("profile"),
                "hint": "re-call with approve=true to write the stack file"}
    return _gated_write(surface.mokata_dir, "config", dest, content,
                        lambda: (export_manifest(surface, dest=dest), dest)[1])


@_tool("stacks_install", "write")
def stacks_install(path: str = ".", name: str = "", source: str = "", force: bool = False,
                   approve: bool = False, confirm: Optional[bool] = None) -> Dict[str, Any]:
    """Install a catalog stack as this repo's config — the human-gated, secret-scanned ADOPT
    path (reuses `apply_manifest`). HUMAN-GATED: without `approve=true` it reports what WOULD
    apply (no write); with `approve=true` it applies through the WriteGate, where the stack
    manifest is secret-scanned so a secret is hard-blocked EVEN when approved (community content
    is untrusted). `force` overwrites an existing config. No hosted marketplace is involved."""
    from .. import stacks as ST
    try:
        raw, data = ST.resolve_stack_manifest(name, source=source or None)
    except ST.StackError as exc:
        return {"status": "error", "message": str(exc)}
    stack_meta = (data.get("settings") or {}).get("stack") or {}
    if not _approved(approve, confirm):
        return {"status": "proposed", "action": "stacks_install", "name": name,
                "profile": data.get("profile"), "framework": stack_meta.get("framework"),
                "hint": "re-call with approve=true to install (the gated, secret-scanned adopt)"}
    surface_dir = _mokata_dir(path)
    box: Dict[str, Any] = {}

    def _apply() -> Any:
        from ..share import apply_manifest
        result = apply_manifest(path, data, assume_yes=True, force=force)
        box["apply"] = result
        return {"applied": result.applied, "path": result.path, "message": result.message}

    # Feed the RAW manifest text so the WriteGate secret-scan is the absolute hard block.
    return _gated_write(surface_dir, "config", surface_dir, raw, _apply)
