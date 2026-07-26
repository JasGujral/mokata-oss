"""TEAM write tools — human-gated (SI.3) shared-audit publish: `audit_share`.

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
    status = ("committed" if res.committed and res.published else
              "unavailable" if res.reason == "unavailable" else
              "in_sync" if res.reason == "in sync" else
              "blocked")
    return {"status": status, "committed": res.committed, "published": res.published,
            "reason": res.reason, "findings": [f.kind for f in res.findings],
            "message": res.message, "log": msgs}
