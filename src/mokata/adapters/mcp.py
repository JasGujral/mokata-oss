"""H4 — MCP registry + discovery.

Enumerate available MCP servers (from an injected config or a JSON file) and map them to
stack roles (capabilities) via the capabilities they declare. Discovery is pluggable and
degrades cleanly: with no config/file present, the registry is empty and never errors.
Each MCP server is also an `AdapterContract`, so it flows into A6 negotiation.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .contract import AdapterContract


@dataclass
class MCPServer:
    name: str
    provides: List[str] = field(default_factory=list)
    command: Optional[str] = None
    url: Optional[str] = None

    def to_adapter(self) -> AdapterContract:
        return AdapterContract(name=self.name, provides=list(self.provides), kind="mcp")


def _normalize(entries: Any) -> List[MCPServer]:
    servers: List[MCPServer] = []
    if isinstance(entries, dict):
        # { name: {provides, command, url} } form
        items = [{"name": k, **(v or {})} for k, v in entries.items()]
    elif isinstance(entries, list):
        items = entries
    else:
        return []
    for e in items:
        if not isinstance(e, dict) or not e.get("name"):
            continue
        servers.append(MCPServer(name=e["name"], provides=list(e.get("provides", [])),
                                 command=e.get("command"), url=e.get("url")))
    return servers


def discover_mcp_servers(config: Any = None,
                         path: Optional[str] = None) -> List[MCPServer]:
    """Discover MCP servers from an injected config, a JSON file, or — failing both —
    return [] (degrade cleanly; no MCP present)."""
    if config is not None:
        return _normalize(config)
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                return _normalize(json.load(fh))
        except (OSError, ValueError):
            return []
    return []


class MCPRegistry:
    def __init__(self, servers: List[MCPServer]) -> None:
        self.servers = servers

    @classmethod
    def discover(cls, config: Any = None,
                 path: Optional[str] = None) -> "MCPRegistry":
        return cls(discover_mcp_servers(config=config, path=path))

    def names(self) -> List[str]:
        return [s.name for s in self.servers]

    def map_to_roles(self) -> Dict[str, List[str]]:
        roles: Dict[str, List[str]] = {}
        for s in self.servers:
            for cap in s.provides:
                roles.setdefault(cap, []).append(s.name)
        return roles

    def servers_for(self, capability: str) -> List[MCPServer]:
        return [s for s in self.servers if capability in s.provides]

    def adapters(self) -> List[AdapterContract]:
        return [s.to_adapter() for s in self.servers]
