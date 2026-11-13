"""SESSION write tools — human-gated (SI.3) portable-session share/hydrate/rename.

Domain split out of `mcp/tools_write.py` (PRE-SIMP, release 0.0.15): `session_push`, `session_pull`,
`session_name`. Every tool routes through the one consent boundary in `mcp/consent.py`; registration
order + tool names are preserved by the `tools_write.py` aggregator.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
from typing import Annotated, Any, Dict, Literal, Optional

from .. import approval
from ..govern import AuditLedger
from .consent import _consent, _policy, _propose, _refused, _require
from .registry import _mokata_dir, _surface
from .tool_annotations import Field
from .validation import ValidationError

_PATH = "Repo root the tool operates on (default: the server's working directory)."
_PROPOSAL = ("Id of a proposal a human approved with `mokata approve <id>`; leave empty on the "
             "first call.")
_TRANSPORT = ("Where bundles live: `local` (this repo) or `postgres` (team database); default "
              "follows the repo mode.")
_Transport = Optional[Literal["local", "postgres"]]


def _require_tag(tag: str, field: str) -> None:
    """A session tag is one path-free slug. Checked up front so a bad tag is a refusal naming the
    field, not a bundle-store exception (`session_bundle._safe_tag` is the same rule)."""
    if not tag or tag != os.path.basename(tag) or tag in (".", "..") or any(c in tag for c in "/\\:"):
        raise ValidationError(field, "must be a simple session name with no path separators")


# Provenance (developers): Stage 55a/55b + SS.S4. Packages the CURRENT session into a machine-path-free,
# versioned (v2), secret-scanned bundle (session_state + a bounded, per-turn-scanned transcript +
# counts-only meta). Human-gated (SI.3) on EVERY transport; a secret in the session or in one
# transcript turn is hard-blocked even when approved. A changed re-push needs `force`. An unreachable
# remote degrades clean (status "unavailable") and never silently falls back to a less-secure store.
# SS.S4 flags: `save_first` snapshots through the ungated save path first; `allow_in_progress` is the
# explicit consent to share a not-yet-approved brainstorm; `requirements_only` bundles only the
# distilled requirements, marked cross-repo. SIMP.S1: an unspecified transport is derived from the
# repo mode (team -> postgres, solo -> local). `vault` was a session transport and is removed.
def session_push(
        path: Annotated[str, Field(description=_PATH)] = ".", *,
        tag: Annotated[str, Field(
            description="Name to share the session under: one simple word, no slashes.")],
        run_id: Annotated[str, Field(
            description="Run to bundle; default is the current session's run.")] = "",
        author: Annotated[str, Field(description="Author label stored in the bundle.")] = "",
        force: Annotated[bool, Field(
            description="Overwrite an existing, different bundle under the same tag.")] = False,
        transport: Annotated[_Transport, Field(description=_TRANSPORT)] = None,
        save_first: Annotated[bool, Field(
            description="Snapshot the session first, then bundle that snapshot.")] = False,
        allow_in_progress: Annotated[bool, Field(
            description="Consent to share an unfinished (not yet approved) session.")] = False,
        requirements_only: Annotated[bool, Field(
            description="Share only the distilled requirements, nothing else.")] = False,
        proposal_id: Annotated[str, Field(description=_PROPOSAL)] = "") -> Dict[str, Any]:
    """Share the current session as a secret-scanned bundle under a tag, so another machine or teammate can pull it.

    Nothing is written until a human approves; a secret anywhere in the bundle blocks it even then. A changed bundle under an existing tag needs `force`. Sharing an unfinished session needs `allow_in_progress` (or use `requirements_only`).

    Returns: `proposed` with a `proposal_id`, then `committed` with `tag`; `empty`, `unchanged`, `conflict`, `in_progress` or `unavailable` explain why nothing was written.
    Errors: refused (invalid_input) when `tag` is not a simple name.
    """
    _require_tag(tag, "tag")
    from .. import session_bundle as SB
    from .. import session_transport as STX
    # SIMP.S1 — an unspecified transport is DERIVED from the repo mode (team-connected → postgres,
    # solo → local); an explicit value is honored verbatim (the escape hatch is transport="local").
    # The derivation itself fails closed on an unreadable manifest, so keep it INSIDE the guard —
    # both an undetermined mode and an unreachable remote degrade clean (status 'unavailable').
    try:
        transport = transport or STX.transport_kind_for_mode(path)
        t = STX.make_transport(transport, path)
    except STX.SessionTransportUnavailable as exc:
        return {"status": "unavailable", "committed": False, "transport": transport,
                "message": str(exc)}
    # A bundle that cannot be read or written (corrupt, failed hash) is a store fault, not the
    # caller's: `error`, carrying the bundle's own reason (the tag was validated above).
    try:
        plan = SB.plan_session_push(path, _surface(path), tag, run_id=run_id or None,
                                    force=force, author=author, transport=t,
                                    save_first=save_first, requirements_only=requirements_only)
    except SB.SessionBundleError as exc:
        return {"status": "error", "committed": False, "message": str(exc)}
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
                        summary=f"share session '{plan.tag}' over {transport}")
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


# Provenance (developers): Stage 55a/55b. Pulls a tagged bundle and re-hydrates it so `mokata resume`
# continues the work. The bundle is UNTRUSTED, so this is human-gated (SI.3) and secret-scanned on
# pull on every transport. The content hash is verified; a cross-codebase fingerprint mismatch is
# surfaced (status "mismatch") and not applied unless `force`. The HARD-GATE survives: a
# not-yet-approved brainstorm stays not approved. SIMP.S1: transport derived from the repo mode.
def session_pull(
        path: Annotated[str, Field(description=_PATH)] = ".", *,
        tag: Annotated[str, Field(description="Name the session was shared under.")],
        into: Annotated[str, Field(
            description="Repo to hydrate into; default is this repo.")] = "",
        force: Annotated[bool, Field(
            description="Apply the bundle even when it comes from a different codebase.")] = False,
        transport: Annotated[_Transport, Field(description=_TRANSPORT)] = None,
        proposal_id: Annotated[str, Field(description=_PROPOSAL)] = "") -> Dict[str, Any]:
    """Pull a shared session bundle by tag and restore it into a repo so `mokata resume` can continue it.

    The bundle is untrusted: it is hash-checked and secret-scanned, and nothing is restored until a human approves. A bundle from a different codebase is reported as `mismatch` and needs `force`.

    Returns: `proposed` with a `proposal_id`, then `committed` with `tag` and `into`; `missing`, `mismatch` or `unavailable` explain why nothing changed.
    Errors: refused (invalid_input) when `tag` is not a simple name.
    """
    _require_tag(tag, "tag")
    from .. import session_bundle as SB
    from .. import session_transport as STX
    # SIMP.S1 — derive the transport from the repo mode when unspecified (explicit value honored).
    # Derivation fails closed on an unreadable manifest, so keep it inside the guard.
    try:
        transport = transport or STX.transport_kind_for_mode(path)
        t = STX.make_transport(transport, path)
    except STX.SessionTransportUnavailable as exc:
        return {"status": "unavailable", "committed": False, "transport": transport,
                "message": str(exc)}
    target = into or path
    try:
        plan = SB.plan_session_pull(path, tag, target, force=force, transport=t)
    except SB.SessionBundleError as exc:
        return {"status": "error", "committed": False, "message": str(exc)}
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
                        summary=f"hydrate session '{plan.tag}' into {target}")
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


# Provenance (developers): Stage 55b. Renames a tagged bundle over a transport; human-gated (SI.3) where
# it writes durable. Idempotent (renaming to the current name is a no-op); a collision is refused
# unless `force`, never a silent clobber; provenance is preserved and the content hash untouched.
def session_name(
        path: Annotated[str, Field(description=_PATH)] = ".", *,
        tag: Annotated[str, Field(description="Current tag of the shared session.")],
        new: Annotated[str, Field(description="New tag: one simple word, no slashes.")],
        force: Annotated[bool, Field(
            description="Overwrite an existing bundle already using the new name.")] = False,
        transport: Annotated[_Transport, Field(description=_TRANSPORT)] = None,
        proposal_id: Annotated[str, Field(description=_PROPOSAL)] = "") -> Dict[str, Any]:
    """Rename a shared session bundle to a friendlier tag.

    Renaming to the current name does nothing. If the new name is taken the call reports a `conflict` unless `force` is set. Nothing changes until a human approves.

    Returns: `proposed` with a `proposal_id`, then `committed` with `old` and `new`; `noop`, `missing`, `conflict` or `unavailable` explain why nothing changed.
    Errors: refused (invalid_input) when `tag` or `new` is not a simple name.
    """
    _require_tag(tag, "tag")
    _require_tag(new, "new")
    from .. import session_bundle as SB
    from .. import session_transport as STX
    # SIMP.S1 — derive the transport from the repo mode when unspecified (explicit value honored).
    # Derivation fails closed on an unreadable manifest, so keep it inside the guard.
    try:
        transport = transport or STX.transport_kind_for_mode(path)
        t = STX.make_transport(transport, path)
    except STX.SessionTransportUnavailable as exc:
        return {"status": "unavailable", "committed": False, "transport": transport,
                "message": str(exc)}
    try:
        plan = SB.plan_session_rename(path, tag, new, transport=t, force=force)
    except SB.SessionBundleError as exc:
        return {"status": "error", "committed": False, "message": str(exc)}
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
                        summary=f"rename session '{plan.old}' -> '{plan.new}'")
    ledger = AuditLedger.from_mokata_dir(_mokata_dir(path))
    res = SB.commit_session_rename_gated(
        plan, ledger=ledger,
        policy=_policy(path, "session_name", human_approved=True))    # human-approved (SI.3)
    approval.record_redemption(ledger, _require(gate), committed=res.committed)
    return {"status": "committed" if res.committed else "blocked",
            "committed": res.committed, "old": plan.old, "new": plan.new, "reason": res.reason,
            "findings": [f.kind for f in res.findings]}
