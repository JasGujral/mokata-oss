"""Neutral leaf module: path + name constants shared by the harness installer
(``harness_setup``) and the client-side MCP admin (``mcp_admin``).

Stage 3d.1 extraction. Previously ``mcp_admin`` imported ``MCP_SERVER_NAME`` and the
Claude MCP config-path resolution from ``harness_setup`` at top level, while
``harness_setup``/``cli``/``hook_cli`` imported ``mcp_admin`` only LAZILY (inside
functions) to avoid a circular import. That cycle is real; it was papered over by the
lazy imports. Moving the shared pieces down HERE — a module that imports nothing from
the ``mokata`` package, so it can never sit in a cycle — lets both sides depend on this
leaf instead of on each other, and the previously-lazy imports get hoisted to module top.

The two functions below are the SINGLE source of the scope base + Claude MCP config path;
``harness_setup.resolve_targets`` delegates to them, so there is no drift.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

# The MCP server key (mirrors .claude-plugin/plugin.json's mcpServers entry).
MCP_SERVER_NAME = "mokata"

# The Claude Code permission pattern that trusts EVERY mokata MCP tool (`mcp__<server>__*`).
# Without this in `permissions.allow`, Claude Code GATES each `mcp__mokata__*` call (the
# approval-loop hang). The server segment must be literal; only the trailing tool is wild.
MCP_TOOL_PERMISSION = f"mcp__{MCP_SERVER_NAME}__*"

# AP-MCP (doc 85 §5 D26 amendment): the in-chat `approve` tool must NEVER ride the auto-grant. The
# wildcard above ALREADY matches `mcp__mokata__approve`, so silence would let the model's approve
# call sail through un-prompted — re-opening the exact hole SI.3 closed. Claude Code evaluates
# deny → ask → allow, so this EXPLICIT `permissions.ask` entry overrides the allow-wildcard and
# forces a human prompt on EVERY approve call. `ask` (not `deny`) is deliberate: `deny` would block
# the opt-in tool outright; `ask` is the strongest rule that still lets the human say yes in-flow.
MCP_APPROVE_TOOL = f"mcp__{MCP_SERVER_NAME}__approve"
MCP_APPROVE_TOOL_ASK = MCP_APPROVE_TOOL


def scope_base(scope: str, root: str, home: Optional[str] = None) -> Path:
    """The base directory a (scope) choice resolves under: the project ``root`` for the
    ``project`` scope, else the user home (``home`` if given, else ``Path.home()``)."""
    return Path(root).resolve() if scope == "project" else (
        Path(home).resolve() if home else Path.home())


def claude_mcp_config_path(scope: str, root: str, home: Optional[str] = None) -> Path:
    """Claude Code's MCP config path for a scope: project → ``<root>/.mcp.json``; user →
    ``<home>/.claude.json`` (where ``claude mcp add --scope user`` writes)."""
    base = scope_base(scope, root, home)
    return (base / ".mcp.json") if scope == "project" else (base / ".claude.json")


def claude_settings_path(scope: str, root: str, home: Optional[str] = None) -> Path:
    """Claude Code's ``settings.json`` for a scope — the file mokata merges hooks, the
    statusLine, AND the MCP tool grant (``enabledMcpjsonServers`` + ``permissions.allow``)
    into: ``<scope base>/.claude/settings.json``. The single source of this path, shared by
    ``harness_setup`` (which writes it) and ``mcp_admin`` (which reads the grant back)."""
    return scope_base(scope, root, home) / ".claude" / "settings.json"
