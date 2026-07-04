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
