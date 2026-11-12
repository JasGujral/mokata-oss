"""CONFIG / lifecycle / stacks write tools — human-gated (SI.3) repo setup + manifest edits.

Domain split out of `mcp/tools_write.py` (PRE-SIMP, release 0.0.15): `import_stack`, `reset`, `init`,
`reconfigure`, `config_set`, `export_stack`, `stacks_install`. Every tool routes through the one
consent boundary in `mcp/consent.py`; registration order + tool names are preserved by the
`tools_write.py` aggregator.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
from typing import Annotated, Any, Dict, List, Literal

from .. import MOKATA_DIR, approval
from ..config import Surface
from ..govern import AuditLedger, plan_reset, reset_state
from .consent import (_consent, _gated_write, _policy, _propose, _refused,
                      _require)
from .registry import _mokata_dir, _surface
from .tool_annotations import Field
from .validation import ValidationError

_Path = Annotated[str, Field(
    description="Repo root the tool operates on (default: the server's working directory).")]
_ProposalId = Annotated[str, Field(
    description="Id of a proposal a human approved with `mokata approve <id>`; omit on the first call.")]
_Force = Annotated[bool, Field(description="Overwrite an existing config instead of refusing (default false).")]


# import_stack — gated (SI.3): propose-only until a human-minted proposal_id is redeemed. The stack
# file is UNTRUSTED and secret-scanned at the boundary (SI.6b): a credential anywhere in it refuses
# the whole file even when approved.
def import_stack(path: _Path = ".", *,
                 file: Annotated[str, Field(description="Path to a shared stack manifest (JSON) to apply.")],
                 force: _Force = False,
                 proposal_id: _ProposalId = "") -> Dict[str, Any]:
    """Apply a shared stack manifest file to this repo; call it to adopt a teammate's stack.

    Validates the file and reports what would apply; a human approves before anything is written.
    The file is untrusted and secret-scanned: a credential anywhere in it refuses the whole file,
    even when approved. An existing config is only overwritten with `force`.

    Returns: status "proposed" (valid, errors, blocked_keys, would_apply_profile), then "committed"
    once redeemed; "blocked" when the manifest is invalid or carries a secret.
    Errors: refused (invalid_input) when `file` is empty, unreadable or not JSON."""
    from ..share import apply_manifest, blocked_keys, load_shared, validate_shared
    if not file.strip():
        raise ValidationError("file", "a stack manifest path is required")
    try:
        data = load_shared(file)
        with open(file, encoding="utf-8") as fh:
            raw = fh.read()
    except (OSError, ValueError):
        raise ValidationError("file", "cannot be read as a JSON stack manifest")
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
                        summary=f"apply stack manifest {file} (profile {data.get('profile')})")
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


# reset — gated (SI.3): propose-only, lists what WOULD be removed. Takes no required argument: it is
# a whole-repo reset (`keep_config` is the only knob), so it is exempt from the required-parameter rule.
def reset(path: _Path = ".",
          keep_config: Annotated[bool, Field(
              description="Keep the manifest and constitution, remove the rest (default false).")] = False,
          proposal_id: _ProposalId = "") -> Dict[str, Any]:
    """Remove this repo's mokata state (.mokata/); call it to start over or uninstall.

    Lists what would be removed and deletes nothing until a human approves. `keep_config` keeps the
    manifest and constitution.

    Returns: status "proposed" (targets), then "committed" with `result.removed` once redeemed;
    "noop" when there is nothing to remove.
    Errors: refused when `proposal_id` is not a live human approval of this exact call."""
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
                        preview="\n".join(plan.targets))
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


# init — gated (SI.3): propose-only, previews the plan. `profile` has a default, so nothing is
# required (exempt). A manifest that already exists needs `force`: a profile switch is never silent.
# A2/F8.3 — this path never reaches the wizard and wires NOTHING, so it carries the same
# gate-enforcement disclosure the CLI routes make, MEASURED after the write (three states, never two).
def init(path: _Path = ".",
         profile: Annotated[Literal["minimal", "standard", "full", "custom"], Field(
             description="Profile to initialize with (default standard).")] = "standard",
         force: _Force = False,
         proposal_id: _ProposalId = "") -> Dict[str, Any]:
    """Initialize mokata in a repo (.mokata/ manifest and constitution); call it to set up a new project.

    Previews the plan (detected tools, profile, files to write) and writes nothing until a human
    approves. An existing manifest is only overwritten with `force`; a profile switch is never silent.

    Returns: status "proposed" (profile, already_initialized, preview), then "committed" with
    `written`, `gate_enforcement` and `gate_notice`; "blocked" when a manifest exists and `force` is off.
    Errors: refused (invalid_input) when `profile` is not a known profile."""
    from ..init import init_repo, plan_init, render_plan
    from ..profiles import profile_names
    if profile not in profile_names():
        raise ValidationError("profile", "is not a known profile", allowed=profile_names())
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
                        preview=render_plan(plan_init(path, profile)))
    if already and not force:
        return {"status": "blocked", "committed": False,
                "reason": "a manifest already exists — re-call with force=true to "
                          "overwrite (a profile switch is never silent)"}
    box: Dict[str, Any] = {}

    def _do_init() -> Any:
        res = init_repo(root=path, profile=profile, assume_yes=True, force=force,
                        out=lambda *_a: None)
        box["init"] = res
        from ..hook_wiring import gate_enforcement_state, render_gate_enforcement
        state = gate_enforcement_state(path)
        return {"written": res.written, "aborted": res.aborted, "profile": profile,
                "gate_enforcement": state, "gate_notice": render_gate_enforcement(state)}

    return _gated_write(_mokata_dir(path), "config", _mokata_dir(path), "", _do_init, gate,
                        _policy(path, "init", human_approved=True))


# reconfigure (56b) — gated (SI.3): propose-only, returns the current->proposed DIFF. Idempotent and
# reversible; an ABSENT `add` is recommended, never installed. No required argument (no change is a
# friendly no-op), so it is exempt from the required-parameter rule.
def reconfigure(path: _Path = ".",
                profile: Annotated[Literal["", "minimal", "standard", "full", "custom"], Field(
                    description="Profile to switch to; empty keeps the current one.")] = "",
                add: Annotated[str, Field(
                    description="Comma-separated integration ids to wire (e.g. a code-graph tool); empty adds none.")] = "",
                remove: Annotated[str, Field(
                    description="Comma-separated integration ids to unwire; empty removes none.")] = "",
                proposal_id: _ProposalId = "") -> Dict[str, Any]:
    """Change what is wired on an already-initialized repo: switch profile, add or remove integrations.

    Returns the current-to-proposed diff and writes nothing until a human approves. Removing an
    integration leaves no residue; an integration that is absent is recommended, never installed.

    Returns: status "proposed" (diff, added, removed, profile, recommended), then "committed";
    "unchanged" when the setup already matches; "uninitialized" when the repo has no mokata yet
    (run `mokata init` first).
    Errors: refused (invalid_input) when `profile` is not a known profile."""
    from .. import onboarding
    if not Surface.is_initialized(path):
        return {"status": "uninitialized", "committed": False,
                "message": "this repo isn't initialized — run `mokata init` first"}
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
                        preview=onboarding.render_reconfigure_diff(plan))
    ledger = AuditLedger.from_mokata_dir(_mokata_dir(path))
    res = onboarding.run_reconfigure(path, profile=(profile or None), add=(add_l or None),
                                     remove=(rem_l or None),
                                     assume_yes=True,        # human-approved (SI.3)
                                     ledger=ledger, out=lambda *_a: None)
    approval.record_redemption(ledger, _require(gate), committed=res.changed)
    return {"status": "committed" if res.changed else "blocked", "committed": res.changed,
            "added": res.added, "removed": res.removed, "profile": res.profile,
            "recommended": res.recommended}


# config_set (Stage 24A) — gated (SI.3): propose-only, previews old->new. Writes through the WriteGate;
# a secret in the resulting manifest (an inline DSN/credential) is a hard block even when approved, and
# a structurally invalid edit is refused, not committed. A manifest that cannot be read on disk is a
# repo fault, so `ConfigCommandError` stays `status: "error"`.
def config_set(path: _Path = ".", *,
               key: Annotated[str, Field(
                   description="Dotted manifest key to set, e.g. `tools.sqlite.config.path`.")],
               value: Annotated[str, Field(
                   description="New value as text (JSON literals such as true or 3 are parsed).")],
               proposal_id: _ProposalId = "") -> Dict[str, Any]:
    """Set one dotted key in the committed mokata manifest; call it to change backend config.

    Previews the old-to-new change and writes nothing until a human approves. A secret in the result
    (an inline DSN or credential) is blocked even when approved: reference an env var instead, such
    as `config.dsn_env`. A structurally invalid edit is refused.

    Returns: status "proposed" (key, old, new), then "committed" with old/new; "blocked" with
    `findings` when a secret is detected.
    Errors: refused (invalid_input) when `key` is empty; error when the manifest cannot be read."""
    from .. import config_cmd
    if not [p for p in key.split(".") if p.strip()]:
        raise ValidationError("key", "must be a non-empty dotted path")
    _surface(path)      # an uninitialized repo is refused with `mokata init`, never an error
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
                            summary=f"set {key}: {res.old!r} -> {res.new!r}")
        res = config_cmd.config_set(path, key, value,      # human-approved (SI.3)
                                    policy=_policy(path, "config_set", human_approved=True),
                                    out=msgs.append, ledger=ledger)
    except config_cmd.ConfigCommandError as exc:
        return {"status": "error", "message": str(exc)}
    approval.record_redemption(ledger, _require(gate), committed=res.committed)
    return {"status": "committed" if res.committed else "blocked",
            "committed": res.committed, "key": key, "old": res.old, "new": res.new,
            "findings": [f.kind for f in res.findings], "reason": res.message, "detail": msgs}


# export_stack (J3) — gated (SI.3): propose-only. Exported content is secret-scanned at EGRESS
# strength (SI.6b): a hit hard-blocks that key (named in `blocked_keys`, left out of the artifact).
# `file` defaults to .mokata/mokata-stack.json, so nothing is required (exempt). The read-only
# counterpart is `export_preview`.
def export_stack(path: _Path = ".",
                 file: Annotated[str, Field(
                     description="Destination file; empty writes .mokata/mokata-stack.json.")] = "",
                 proposal_id: _ProposalId = "") -> Dict[str, Any]:
    """Export this repo's manifest as a shareable stack file; call it to hand your setup to a teammate.

    Reports what would be written and writes no file until a human approves. Content is secret-scanned:
    a credential-bearing key is named in `blocked_keys` and left out of the file entirely. Use
    `export_preview` to look without proposing.

    Returns: status "proposed" (dest, profile, blocked_keys), then "committed" with `dest`;
    "blocked" when every key is refused.
    Errors: refused when `proposal_id` is not a live human approval of this exact call."""
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
                        preview=content)
    # SI.6b: kind `send`, not `config` — an export is EGRESS, so the gate's own scan runs at outbound
    # strength over the exact bytes that leave. Per-key blocking already happened in `plan_export`;
    # this is the belt-and-suspenders on the assembled artifact (the C2 `memory_export` shape).
    res = _gated_write(surface.mokata_dir, "send", dest, content,
                       lambda: (export_manifest(surface, dest=dest), dest)[1], gate,
                       _policy(path, "export_stack", human_approved=True, surface=surface))
    return {"status": res["status"], "committed": res["committed"], "dest": dest,
            "blocked": len(plan.blocked), "blocked_keys": plan.blocked,
            "reason": res["reason"]}


# stacks_install — gated (SI.3): the human-gated, secret-scanned ADOPT path (reuses `apply_manifest`).
# The manifest is secret-scanned at the WriteGate so a secret is hard-blocked even when approved
# (community content is untrusted). No hosted marketplace is involved. An unknown stack or an
# unreadable catalog source is the caller's fault, so it is refused.
def stacks_install(path: _Path = ".", *,
                   name: Annotated[str, Field(
                       description="Catalog stack name to install (see `stacks_list`).")],
                   source: Annotated[str, Field(
                       description="Catalog index path or URL; empty uses the bundled catalog.")] = "",
                   force: _Force = False,
                   proposal_id: _ProposalId = "") -> Dict[str, Any]:
    """Install a catalog stack as this repo's config; call it to adopt a curated per-framework stack.

    Reports what would apply and writes nothing until a human approves. The manifest is secret-scanned
    even when approved, because community content is untrusted. `force` overwrites an existing config.
    Find names with `stacks_list` or `stacks_search`.

    Returns: status "proposed" (name, profile, framework), then "committed"; "blocked" when the
    manifest carries a secret or a config exists and `force` is off.
    Errors: refused (invalid_input) when `name` is empty, unknown, or the `source` catalog is unreadable."""
    from .. import stacks as ST
    if not name.strip():
        raise ValidationError("name", "a stack name is required")
    try:
        raw, data = ST.resolve_stack_manifest(name, source=source or None)
    except ST.StackError:
        raise ValidationError("name", "is not a known stack, or the catalog source is unreadable")
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
                        preview=raw)
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
