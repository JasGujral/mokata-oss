"""TEAM write tools — human-gated (SI.3) shared publishes: `audit_share`, `events_share`.

Domain split out of `mcp/tools_write.py` (PRE-SIMP, release 0.0.15). Routes through the one consent
boundary in `mcp/consent.py`; registration order + tool name are preserved by the `tools_write.py`
aggregator.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import approval
from ..govern import AuditLedger
from .consent import _consent, _policy, _propose, _refused, _require
from .registry import _surface


#: The committed reason BOTH publishers use. One literal, because the success arm below compares
#: against it and two spellings of "it worked" is how one of them stops matching.
COMMITTED_REASON = "committed"


def publish_status(reason: str, *, committed: bool, in_sync: "tuple",
                   unavailable: "tuple" = ("unavailable",)) -> str:
    """The MCP `status` word for one publish, from its `reason` and whether it committed.

    A PURE FUNCTION over the reason STRING, so it can be graded against every reason a publisher
    can return rather than the two a live call happens to produce — and ONE function for both
    publishers, with each one's reason vocabulary DECLARED at its call site, because the two
    vocabularies really are different and hiding that inside the function is how a word gets
    silently mapped to `blocked`.

    ⛔ **KEYED ON `reason`, NEVER ON `published`.** Both call sites read
    `committed and res.published`, so a publish that COMMITTED and found every row already there
    — a correct, successful, fully deduplicated re-publish — fell through every arm to
    `"blocked"`, which is the SAME WORD the secret-scan refusal returns, with `committed: true`
    sitting beside it (review F5, on the events surface; the audit surface shipped the identical
    shape and is fixed in the same change). Two opposite outcomes under one word is §7g at the API
    boundary: a caller that branches on `status` cannot recover the difference, and the field
    exists to be branched on."""
    if reason in unavailable:
        return "unavailable"
    if reason in in_sync:
        return "in_sync"
    if committed and reason == COMMITTED_REASON:
        return COMMITTED_REASON
    return "blocked"


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
    gate = _consent(path, "audit_share", args, proposal_id, surface=surface)
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
                      policy=_policy(path, "audit_share", human_approved=True, surface=surface),
                      out=msgs.append, ledger=ledger)
    approval.record_redemption(ledger, _require(gate), committed=res.committed)
    status = publish_status(res.reason, committed=res.committed, in_sync=("in sync",))
    return {"status": status, "committed": res.committed, "published": res.published,
            "reason": res.reason, "findings": [f.kind for f in res.findings],
            "message": res.message, "log": msgs}


def events_share(path: str = ".", approve: bool = False,
                 confirm: Optional[bool] = None, proposal_id: str = "") -> Dict[str, Any]:
    """CM.S5 — publish this dev's NEW local typed EVENTS to the team's shared store (the team's OWN
    managed Postgres — NO telemetry, nothing phoned home). OPT-IN (`settings.events.shared`) +
    LOCAL-FIRST. HUMAN-GATED (SI.3): PROPOSE-ONLY — it reports how many events WOULD publish and
    writes nothing. A human mints the approval with `mokata approve <id>`; re-calling with that
    `proposal_id` publishes through the universal WriteGate (kind `send`: a secret is hard-blocked
    even when approved), APPEND-ONLY and idempotent by `(namespace, event_id)`.

    ⭐ The proposal also carries `notify_state`, and that is not decoration: over a transaction-mode
    pooler a notification is SENT and can never be RECEIVED, so an agent reading this must be able
    to tell *"nobody is listening"* from *"nothing can listen"* (§7g). Degrade-clean throughout."""
    from ..team_events import notify_support, pending_publish, publish, resolve_dsn, shared_enabled
    surface = _surface(path)
    data = surface.manifest.data
    if not shared_enabled(data):
        return {"status": "disabled", "committed": False,
                "message": ("publishing events is OFF (local-first). Opt in with "
                            "`mokata config set settings.events.shared true`.")}

    args = {"path": path}
    gate = _consent(path, "events_share", args, proposal_id, surface=surface)
    if gate.refused:
        return _refused(gate)
    if not gate.granted:
        available, pending_n, dsn_env, message = pending_publish(path, surface)
        notify_state, notify_why = notify_support(resolve_dsn(data=data))
        return _propose(path, "events_share", args,
                        {"available": available, "pending": pending_n, "dsn_env": dsn_env,
                         "notify_state": notify_state, "notify_why": notify_why,
                         "message": message},
                        target="team:events",
                        summary=f"publish {pending_n} typed event(s) to the team's shared store",
                        approve=approve, confirm=confirm)
    msgs: List[str] = []
    ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
    res = publish(path, surface,                              # human-approved (SI.3)
                  policy=_policy(path, "events_share", human_approved=True, surface=surface),
                  out=msgs.append, ledger=ledger)
    approval.record_redemption(ledger, _require(gate), committed=res.ok)
    status = publish_status(res.reason, committed=res.ok,
                            in_sync=("nothing-new", "only-own-trail"),
                            unavailable=("unavailable", "local-unreadable"))
    return {"status": status, "committed": res.ok, "published": res.published,
            "already_there": res.already_there, "remaining": res.remaining,
            "notify_state": res.notify_state, "notify_why": res.notify_why,
            "reason": res.reason, "findings": [f.kind for f in res.findings],
            "message": res.message, "log": msgs}
