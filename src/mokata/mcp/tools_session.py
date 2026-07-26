"""SESSION write tools — human-gated (SI.3) portable-session share/hydrate/rename.

Domain split out of `mcp/tools_write.py` (PRE-SIMP, release 0.0.15): `session_push`, `session_pull`,
`session_name`. Every tool routes through the one consent boundary in `mcp/consent.py`; registration
order + tool names are preserved by the `tools_write.py` aggregator.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .. import approval
from ..govern import AuditLedger
from .consent import _consent, _policy, _propose, _refused, _require
from .registry import _mokata_dir, _surface


def session_push(path: str = ".", tag: str = "", run_id: str = "", author: str = "",
                 force: bool = False, transport: str = "", approve: bool = False,
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


def session_pull(path: str = ".", tag: str = "", into: str = "", force: bool = False,
                 transport: str = "", approve: bool = False,
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


def session_name(path: str = ".", tag: str = "", new: str = "", force: bool = False,
                 transport: str = "", approve: bool = False,
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
