"""The single tool REGISTRY — the one source of truth for the MCP tool specs.

Every tool is a pure, SDK-free function decorated with `@_tool(name, kind)`, which appends a
`ToolSpec` to the shared `TOOLS` list. `mcp.server.build_server` registers each entry with
FastMCP; `mcp.tools_read` / `mcp.tools_write` hold the functions themselves. Keeping the specs
in one registry removes the hand-maintained CLI↔MCP drift class the 0.0.9 re-audit flagged.

This module is pure stdlib — it never imports the optional MCP SDK.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, List

from .. import MOKATA_DIR
from ..config import Surface

SERVER_NAME = "mokata"


@dataclass
class ToolSpec:
    name: str
    kind: str          # "read" | "write" | "approve" (AP-MCP: the in-chat approval act — neither
                       # a read nor a propose-only write, so `read_tool_names`/`write_tool_names`
                       # both exclude it and the SI.3 write-tool sweeps never touch it)
    fn: Callable[..., Any]


TOOLS: List[ToolSpec] = []


def _tool(name: str, kind: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def register(fn: Callable[..., Any]) -> Callable[..., Any]:
        TOOLS.append(ToolSpec(name=name, kind=kind, fn=fn))
        return fn
    return register


def tool_names() -> List[str]:
    """Every tool the server exposes (works with the MCP SDK absent)."""
    return [t.name for t in TOOLS]


def read_tool_names() -> List[str]:
    return [t.name for t in TOOLS if t.kind == "read"]


def write_tool_names() -> List[str]:
    return [t.name for t in TOOLS if t.kind == "write"]


def _surface(path: str) -> Surface:
    return Surface.load(path)


def _mokata_dir(path: str) -> str:
    return os.path.join(path, MOKATA_DIR)
