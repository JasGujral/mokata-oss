"""WRITE / durable tools — the thin AGGREGATOR over the per-domain write-tool modules.

Every tool here is PROPOSE-ONLY and ALWAYS human-gated, with an approval THE MODEL CANNOT MINT
(SI.3). Calling one returns the staged change and writes nothing. The only thing that commits it is
an approval a HUMAN minted out-of-band, in a process this one is not driving:

    1. the model calls the tool                  -> status "proposed" + a proposal_id. Nothing writes.
    2. the HUMAN runs `mokata approve <id>`      -> a separate process, a TTY re-confirm, ledgered.
    3. the model re-calls with proposal_id=<id>  -> verified, committed once, approval BURNED.

`approve`/`confirm` are still ACCEPTED (schema stability — no caller breaks) but DEMOTED: they commit
nothing. The consent lives in `approval.py`, and the whole consent boundary lives in `mcp/consent.py`.

Layout (PRE-SIMP, release 0.0.15 — split for the 0.0.17 SIMP.S3 removal)
-----------------------------------------------------------------------
This module used to hold the consent boundary AND all the tool bodies (1.3k LOC). It is now a thin
aggregator: the consent boundary is `mcp/consent.py`, and the tools live in per-domain modules —

  * `tools_memory`  — memory writes: core (remember, apply_proposal) + the BACKUP surface
                      (memory_export, memory_import) re-homed here at 35b (it survives SIMP)
  * `tools_share`   — the artifact-VAULT tool (vault_push): the 0.0.17 SIMP.S3 DELETION SET,
                      isolated so the removal is a one-file diff
  * `tools_session` — portable-session share/hydrate/rename
  * `tools_team`    — shared-audit publish
  * `tools_spec`    — spec emit/amend/check (+ the GR emit backstops)
  * `tools_config`  — repo setup, reconfigure, config-set, stacks

Registration stays REGISTRY-DRIVEN and its ORDER is unchanged: the domain modules define the tool
functions, and THIS module registers each into the shared `TOOLS` list (via `@_tool`'s `_tool`) in
the exact historical sequence — so `build_server` sees the identical ordered tool set. `__all__` and
every tool function remain importable from `mokata.mcp.tools_write` exactly as before (the mcp_server
shim's `from .mcp.tools_write import *` is unchanged).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from .registry import _tool
from .tools_config import (config_set, export_stack, import_stack, init,
                           reconfigure, reset, stacks_install)
from .tools_memory import (apply_proposal, consolidate, memory_export,
                           memory_import, remember)
from .tools_session import session_name, session_pull, session_push
from .tools_share import vault_push
from .tools_spec import spec_amend, spec_check, spec_emit
from .tools_team import audit_share

__all__ = [
    "remember", "import_stack", "reset", "apply_proposal", "memory_export",
    "memory_import", "vault_push", "session_push", "session_pull", "session_name",
    "audit_share", "spec_check", "init", "reconfigure", "config_set", "export_stack",
    "stacks_install",
    "consolidate",          # M-4/R5 — appended, matching the registration discipline below
]

# ---- registration: registry-driven, in the EXACT historical order (parity with the pre-split
# single-file registration). The domain modules define the functions; the `@_tool` mechanism
# (registry._tool) records each ToolSpec into the shared TOOLS list here, once, on import. SIMP.S3
# drops the deprecated vault by deleting `tools_share` + the `vault_push` line below (the memory
# BACKUP surface re-homed to `tools_memory` at 35b and survives).
_tool("remember", "write")(remember)
_tool("import_stack", "write")(import_stack)
_tool("reset", "write")(reset)
_tool("apply_proposal", "write")(apply_proposal)
_tool("memory_export", "write")(memory_export)
_tool("memory_import", "write")(memory_import)
_tool("vault_push", "write")(vault_push)
_tool("session_push", "write")(session_push)
_tool("session_pull", "write")(session_pull)
_tool("session_name", "write")(session_name)
_tool("audit_share", "write")(audit_share)
_tool("spec_emit", "write")(spec_emit)
_tool("spec_amend", "write")(spec_amend)
_tool("spec_check", "write")(spec_check)
_tool("init", "write")(init)
_tool("reconfigure", "write")(reconfigure)
_tool("config_set", "write")(config_set)
_tool("export_stack", "write")(export_stack)
_tool("stacks_install", "write")(stacks_install)
# M-4/R5 (0.0.16) — APPENDED, not slotted beside `apply_proposal` where it belongs by domain: the
# registry snapshot pins tool order and its rule is that a new tool keeps every existing position.
_tool("consolidate", "write")(consolidate)      # submit an agent-DRAFTED summary (human-gated)
