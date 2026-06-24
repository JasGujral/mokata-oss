"""mokata adapter & negotiation layer (Part A6 / H4–H6).

A typed adapter ecosystem mokata can reason about, built on the Stage-1 capability model:
  - A6: AdapterContract + negotiate -> coverage and unmet gaps.
  - H5: validate_adapter -> validate a third-party adapter against the contract.
  - H4: MCPRegistry / discover_mcp_servers -> enumerate MCP servers, map to roles
        (degrades cleanly when none present).
  - H6: declared_precedence / overlapping_capabilities / resolve_conflict -> two tools
        claiming one role are resolved by manifest precedence; the router honors it.
"""

from .contract import (
    AdapterContract,
    CoverageReport,
    negotiate,
    validate_adapter,
)
from .mcp import MCPRegistry, MCPServer, discover_mcp_servers
from .precedence import (
    declared_precedence,
    overlapping_capabilities,
    resolve_conflict,
)

__all__ = [
    # A6 / H5
    "AdapterContract",
    "CoverageReport",
    "negotiate",
    "validate_adapter",
    # H4
    "MCPServer",
    "MCPRegistry",
    "discover_mcp_servers",
    # H6
    "declared_precedence",
    "overlapping_capabilities",
    "resolve_conflict",
]
