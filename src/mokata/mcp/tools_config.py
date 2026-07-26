"""CONFIG / lifecycle / stacks write tools — human-gated (SI.3) repo setup + manifest edits.

Domain split out of `mcp/tools_write.py` (PRE-SIMP, release 0.0.15): `import_stack`, `reset`, `init`,
`reconfigure`, `config_set`, `export_stack`, `stacks_install`. Every tool routes through the one
consent boundary in `mcp/consent.py`; registration order + tool names are preserved by the
`tools_write.py` aggregator.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from .. import MOKATA_DIR, approval
from ..config import Surface
from ..govern import AuditLedger, plan_reset, reset_state
from .consent import (_consent, _gated_write, _policy, _propose, _refused,
                      _require)
from .registry import _mokata_dir, _surface


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
    gate = _consent(path, "export_stack", args, proposal_id, surface=surface)
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
                       _policy(path, "export_stack", human_approved=True, surface=surface))
    return {"status": res["status"], "committed": res["committed"], "dest": dest,
            "blocked": len(plan.blocked), "blocked_keys": plan.blocked,
            "reason": res["reason"]}


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
