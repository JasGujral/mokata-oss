"""mokata adapter & negotiation layer (Part A6 / H4–H6).

A typed adapter ecosystem mokata can reason about, built on the Stage-1 capability model:
  - A6: AdapterContract + negotiate -> coverage and unmet gaps.
  - H5: validate_adapter -> validate a third-party adapter against the contract.
  - H4: MCPRegistry / discover_mcp_servers -> enumerate MCP servers, map to roles
        (degrades cleanly when none present).
  - H6: declared_precedence / overlapping_capabilities / resolve_conflict -> two tools
        claiming one role are resolved by manifest precedence; the router honors it.

LAZY BY DESIGN (0.0.25 Lane HB, HOOK-BUDGET). Every name below is importable from
``mokata.adapters`` exactly as before; the submodule that defines it is imported on FIRST USE
(PEP 562 ``__getattr__``), so the dirty-track hook's transcript reader (``adapters.transcript``) no
longer loads the MCP registry (and ``subprocess``) with it. The eager import list stays VERBATIM
under ``TYPE_CHECKING`` for type checkers and the repo's AST walkers;
``tests/test_hb_hook_budget.py`` pins that list, ``_EXPORTS`` and ``__all__`` together.
"""

from typing import TYPE_CHECKING, Any, Dict, Tuple

if TYPE_CHECKING:  # the eager list, for static readers only — see the module docstring
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

#: public name -> (submodule, attribute). Resolved on first access by `__getattr__` below.
_EXPORTS: Dict[str, Tuple[str, str]] = {
    "AdapterContract": ("contract", "AdapterContract"),
    "CoverageReport": ("contract", "CoverageReport"),
    "negotiate": ("contract", "negotiate"),
    "validate_adapter": ("contract", "validate_adapter"),
    "MCPRegistry": ("mcp", "MCPRegistry"),
    "MCPServer": ("mcp", "MCPServer"),
    "discover_mcp_servers": ("mcp", "discover_mcp_servers"),
    "declared_precedence": ("precedence", "declared_precedence"),
    "overlapping_capabilities": ("precedence", "overlapping_capabilities"),
    "resolve_conflict": ("precedence", "resolve_conflict"),
}


def __getattr__(name: str) -> Any:
    """PEP 562: import the defining submodule on first use and cache the name on the package."""
    from importlib import import_module
    if name not in _EXPORTS and not name.startswith("__"):
        # a SUBMODULE reached as an attribute (`pkg.sub` after `import pkg`): the eager form
        # imported it as a side effect, so it is imported here — anything else is a real miss
        try:
            return import_module(f".{name}", __name__)
        except ModuleNotFoundError as exc:
            if exc.name != f"{__name__}.{name}":
                raise
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    sub, attr = _EXPORTS[name]
    value = getattr(import_module(f".{sub}", __name__), attr)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_EXPORTS))


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
