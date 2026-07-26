"""artifact-VAULT write tool — the 0.0.17 SIMP.S3 DELETION SET, isolated.

Domain split out of `mcp/tools_write.py` (PRE-SIMP, release 0.0.15). What REMAINS here is
deletion-bound: `vault_push` fronts the DEPRECATED artifact vault (`vault.py`), which SIMP.S3
removes at 0.0.17 — deleting THIS file and its registration line in the `tools_write.py`
aggregator, touching nothing else.

35b — the memory BACKUP surface (`memory_export` / `memory_import`) that ALSO lived here has been
re-homed to `tools_memory.py`: it SURVIVES SIMP as the single gated backup-and-restore command, so
it must not sit in the deletion set. Only the genuine deprecated sharing channel stays here.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .consent import _consent, _gated_write, _policy, _propose, _refused
from .registry import _mokata_dir


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
