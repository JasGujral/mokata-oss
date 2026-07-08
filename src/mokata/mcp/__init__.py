"""H4+ — the plugin-first MCP surface (a package around a shared tool registry).

A thin Model Context Protocol server that exposes mokata operations as native tools for
Claude Code, so the framework is driven from inside the harness — not only the CLI. Every
tool delegates to the existing package; this package adds NO engine logic.

Two safety rules are non-negotiable:

  * READ tools (query, recall, doctor, coverage, budget, audit, status, preview, progress,
    lanes, watch, govern, vault_list/search/pull, and the Stage 54e parity reads — rules,
    skills, suggest, lat_check, index_status, baseline, sessions, config_get, export_preview,
    the Stage 55a session_list, and the Stage 70 stacks_list/stacks_search/stacks_show) are safe
    and expose their data directly. They live in `tools_read`.
  * WRITE / durable tools (remember, import_stack, reset, apply_proposal, memory_export/import,
    vault_push, spec_check, init, the Stage 54e config_set + export_stack, the Stage 70 gated
    stacks_install, and the Stage 55a/55b
    human-gated session_push/session_pull/session_name — gated on EVERY transport) are ALWAYS
    human-gated. With no `approve`, they are PROPOSE-ONLY: they return the staged change
    and write nothing. Only an explicit `approve=true` — a human decision — performs the
    write, and even then it goes through the universal WriteGate (secrets are a hard block
    that approval cannot override) and is recorded in the audit ledger. An MCP call NEVER
    writes silently. (`confirm=true` is accepted as a deprecated alias for `approve=true`.)
    They live in `tools_write`.

Layout:
  * `registry`     — the `ToolSpec` type, the `@_tool` registration mechanism, and the single
                     `TOOLS` list (the one source of tool specs — removes CLI↔MCP drift).
  * `tools_read`   — the READ tool functions.
  * `tools_write`  — the WRITE / human-gated tool functions.
  * `server`       — `build_server()`, `main()`, `mcp_available()`, the fail-loud guard.

The MCP SDK is an unconditional dependency (Python 3.10+ floor), imported LAZILY inside
`server.build_server`, so the core package and CLI still import and run even in a stripped/broken
env where the SDK is absent. The tool functions themselves are pure and SDK-free — fully usable
and testable without `mcp` installed. This namespaced `mokata.mcp` package does NOT shadow the
SDK's top-level `mcp`.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from .registry import (SERVER_NAME, TOOLS, ToolSpec, read_tool_names,
                       tool_names, write_tool_names)
# tools_read / tools_write register every tool into TOOLS as an import side effect.
from . import tools_read, tools_write  # noqa: F401
from .server import build_server, main, mcp_available

__all__ = [
    "SERVER_NAME", "TOOLS", "ToolSpec", "tool_names", "read_tool_names",
    "write_tool_names", "build_server", "main", "mcp_available",
]
