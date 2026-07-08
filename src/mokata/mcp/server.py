"""MCP wiring — lazily imports the SDK; never imported by the core/CLI.

The MCP SDK is an unconditional dependency of mokata (Python 3.10+ floor), so a plain
`pip install mokata` always provides it. It is still imported LAZILY inside `build_server`, so
the core package and CLI import and run even in a stripped/broken env where the SDK is absent —
`mokata-mcp` then fails LOUD with a fix rather than crashing at import. Note: `mokata.mcp` (this
namespaced package) does NOT shadow the SDK's top-level `mcp` — the absolute
`from mcp.server.fastmcp import FastMCP` below resolves to the installed SDK, and
`mcp_available()`'s import looks up the same top-level SDK, never this package.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import sys
from typing import Any, List, Optional

from .registry import SERVER_NAME, TOOLS
# tools_read / tools_write populate the shared TOOLS registry as an import side effect.
from . import tools_read, tools_write  # noqa: F401


def mcp_available() -> bool:
    """True only when the MCP SDK is actually IMPORTABLE — not merely present on disk.
    A `find_spec("mcp")` check reports True for a present-but-broken SDK (e.g. the SDK imports
    jsonschema, but jsonschema is absent), which then explodes inside build_server()'s
    `from mcp...`. So mirror that import here and treat any ImportError as unavailable: `main`
    fails loud with guidance (Stage 3b.1 guarantee #2), and the SDK stays lazily imported (the
    import is inside this function, keyed by string — no top-level `import mcp`)."""
    import importlib
    try:
        importlib.import_module("mcp.server.fastmcp")
        return True
    except ImportError:
        return False


def _public_module() -> Any:
    """The compat surface callers monkeypatch — the `mokata.mcp_server` shim, falling back to
    this module. `main` resolves `mcp_available` through it so the historical patch point
    (`mokata.mcp_server.mcp_available = ...`) keeps working unchanged after the package split
    (zero behavior change)."""
    return sys.modules.get("mokata.mcp_server", sys.modules[__name__])


def build_server() -> Any:
    """Construct the FastMCP server with every tool registered. The `mcp` SDK is an
    unconditional dependency, so this succeeds on any healthy `pip install mokata`."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - exercised only in a stripped/broken env
        raise RuntimeError(
            "the MCP SDK is not installed; reinstall mokata (`pip install -U mokata`) to "
            "restore the mokata MCP server"
        ) from exc

    server = FastMCP(SERVER_NAME)
    for spec in TOOLS:
        server.add_tool(spec.fn, name=spec.name,
                        description=(spec.fn.__doc__ or "").strip())
    return server


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="mokata-mcp",
        description="mokata MCP server (stdio) — mokata operations as native MCP tools.")
    parser.add_argument("--path", default=".",
                        help="repo root the tools operate on (default: current dir)")
    parser.parse_args(argv)

    # Fail LOUD, not dead (Stage 3b.1). The MCP SDK is an unconditional dependency, so a healthy
    # `pip install mokata` always has it — but a stripped or broken environment can still be
    # missing it. Rather than let build_server() raise an uncaught ImportError/RuntimeError
    # traceback — which Claude Code surfaces only as a failed/absent server the user can't
    # diagnose — name the cause and the fix on stderr and exit non-zero, so the failure is legible.
    if not _public_module().mcp_available():
        sys.stderr.write(
            "mokata-mcp: the MCP SDK is not installed, so the mokata MCP server cannot start.\n"
            "  Cause: your mokata install is missing the `mcp` package (a required dependency).\n"
            "  Fix:   reinstall mokata — `pip install -U mokata` — to restore the SDK. The "
            "mokata CLI works fully without the MCP server.\n")
        return 1

    _public_module().build_server().run()   # stdio (plugin-launched); each tool takes its `path`
    return 0
