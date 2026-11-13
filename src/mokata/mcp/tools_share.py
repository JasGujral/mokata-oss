"""artifact-VAULT write tool — `vault_push`, the gated write half of the design vault.

Domain split out of `mcp/tools_write.py` (PRE-SIMP, release 0.0.15).

⚠⚠ THIS DOCSTRING CALLED ITSELF *"the SIMP.S3 DELETION SET, isolated"* FOR THREE RELEASES AND IT
WAS WRONG — it named this file for deletion at `deprecation.REMOVAL_RELEASE`, and the deletion lane
arrived, and this file SURVIVED (0.0.18 lane D slice 4). What SIMP.S3 removed was the vault
SESSION-TRANSPORT kind; `vault.py` is the Stage 35d design-artifact store, which was never the
channel — the deprecation notice offered a replacement for sessions only, `mokata migrate vault`
moved session bundles only, and nothing on this path ever warned a user it was going. Four CLI
verbs, three MCP read tools, this write tool and `team join --vault` all depend on it.

★ IT IS LEFT HERE AS A CORRECTION RATHER THAN A DELETION because a file that spent three releases
telling every reader it was scheduled for removal is exactly how the gate came to point at a
feature: the lane's acceptance test mapped `vault` → `mokata.vault`, and the only way to satisfy it
as written was to delete a supported command (doc 85 §7h — a stale claim in prose is a claim, and
this one was load-bearing).

35b — the memory BACKUP surface (`memory_export` / `memory_import`) that ALSO lived here has been
re-homed to `tools_memory.py` and survives as the single gated backup-and-restore command.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
from typing import Annotated, Any, Dict, Literal

from .consent import _consent, _gated_write, _policy, _propose, _refused
from .registry import _mokata_dir
from .tool_annotations import Field
from .validation import ValidationError


# vault_push — gated (SI.3): propose-only. A secret in the artifact is blocked even when approved; a
# changed re-push needs `force` (it versions, keeping prior metadata — never a silent clobber). Name,
# file and kind are validated up front (caller faults -> refused); a VaultError that survives that is a
# malformed vault index on disk, a repo fault, so it stays `status: "error"`.
def vault_push(path: Annotated[str, Field(
                   description="Repo root the tool operates on (default: the server's working directory).")] = ".",
               *,
               name: Annotated[str, Field(
                   description="Vault name for the artifact: a simple slug, no path separators.")],
               file: Annotated[str, Field(
                   description="Path to the brainstorm or spec markdown file to push.")],
               kind: Annotated[Literal["", "brainstorm", "spec"], Field(
                   description="Artifact kind; empty infers it from the name and file.")] = "",
               author: Annotated[str, Field(
                   description="Author recorded on the entry; empty records none.")] = "",
               force: Annotated[bool, Field(
                   description="Push a changed artifact as a new version instead of reporting a conflict (default false).")] = False,
               proposal_id: Annotated[str, Field(
                   description="Id of a proposal a human approved with `mokata approve <id>`; omit on the first call.")] = "",
               ) -> Dict[str, Any]:
    """Push a brainstorm or spec markdown file into the team design vault; call it to share a design artifact.

    Reports what would happen and writes nothing until a human approves. A secret in the artifact is
    blocked even when approved. Pushing changed content under an existing name needs `force`, which
    versions it and never overwrites silently.

    Returns: status "proposed" (name, kind, next_version), then "committed" with `version`;
    "unchanged" when identical content is already there; "conflict" when it changed and `force` is off.
    Errors: refused (invalid_input) when `name` is not a simple slug, `file` is missing, or `kind`
    is not allowed; error when the vault index on disk is malformed."""
    from ..vault import ARTIFACT_KINDS, VaultError, _artifact_path, _safe_name, commit_push, plan_push
    try:
        _safe_name(name)
    except VaultError:
        raise ValidationError("name", "must be a simple name with no path separators")
    if not os.path.isfile(file):
        raise ValidationError("file", "must be an existing markdown file")
    if kind and kind not in ARTIFACT_KINDS:
        raise ValidationError("kind", "is not an artifact kind", allowed=list(ARTIFACT_KINDS))
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
                        preview=plan.content)
    res = _gated_write(
        _mokata_dir(path), "config", _artifact_path(path, plan.name), plan.content,
        lambda: commit_push(path, plan, author=author).version, gate,
        _policy(path, "vault_push", human_approved=True))
    res["name"] = plan.name
    res["version"] = res.get("result")
    return res
