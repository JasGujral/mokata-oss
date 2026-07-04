"""Compat shim — `mokata.mcp_server` now re-exports the `mokata.mcp` package.

The MCP surface was split into the `mokata/mcp/` package (registry + tools_read + tools_write +
server) in 0.0.9 Stage 3d.3 (re-audit advisory #2). This module keeps the historical import
path and the console entry point (`mokata.mcp_server:main`, see pyproject [project.scripts])
working UNCHANGED: every previously-public name is re-exported here, and `main`/`mcp_available`
stay monkeypatchable on `mokata.mcp_server` exactly as before (server.main resolves them through
this module).

The MCP SDK is still imported LAZILY (inside `mokata.mcp.server.build_server`), so importing this
shim never imports the SDK — the core and CLI keep importing it with the SDK absent (Python 3.9).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from .mcp import tools_read, tools_write  # noqa: F401 - register every tool into TOOLS
from .mcp.registry import (SERVER_NAME, TOOLS, ToolSpec, read_tool_names,
                           tool_names, write_tool_names)
from .mcp.server import build_server, main, mcp_available
from .mcp.tools_read import *   # noqa: F401,F403 - re-export the read tool functions
from .mcp.tools_write import *  # noqa: F401,F403 - re-export the write tool functions

__all__ = [
    "SERVER_NAME", "TOOLS", "ToolSpec", "tool_names", "read_tool_names",
    "write_tool_names", "build_server", "main", "mcp_available",
    *tools_read.__all__, *tools_write.__all__,
]


if __name__ == "__main__":
    raise SystemExit(main())
