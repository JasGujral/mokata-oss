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

import functools
import sys
from typing import Any, Callable, List, Optional

from .registry import SERVER_NAME, TOOLS
# tools_read / tools_write / tools_approve populate the shared TOOLS registry as an import side
# effect (tools_approve = AP-MCP's default-off in-chat `approve` tool).
from . import tools_read, tools_write, tools_approve  # noqa: F401


# ======================================================================================
# R-MCP — the run self-registration seam
# ======================================================================================
#
# SI.1's gate hook narrows an AMBIGUOUS multi-run repo by consulting the MS.S2 live-session
# registry (`gate_hook._live_runs`) — but that is only SOUND if every MCP process is actually IN
# the registry. Before this stage the MCP process self-registered ONLY when the user called the
# `session_windows` tool, so the hook could not rely on it and had to fail open. This seam closes
# that: the server self-registers on the FIRST tool call it serves (and refreshes on every
# subsequent one), making registry liveness a STRUCTURAL fact, not a user-dependent one.

def _call_path(args: tuple, kwargs: dict) -> str:
    """The repo path a served tool call targets. Every mokata MCP tool takes `path` (default ".")
    as its first parameter, so the value is either the `path` keyword or the first positional."""
    p = kwargs.get("path")
    if isinstance(p, str) and p:
        return p
    if args and isinstance(args[0], str) and args[0]:
        return args[0]
    return "."


def _register_this_window(path: str) -> None:
    """Self-register / refresh THIS MCP process in the MS.S2 live-session registry (R-MCP).

    `session_registry.touch` is an idempotent UPSERT-SELF, so the FIRST tool call registers this
    run (session_id/run_id + pid + repo_root + last_seen — the existing MS.S2 `_ENTRY_FIELDS`, no
    new field) and every subsequent call refreshes it. Lazy by construction: this only ever runs
    from `_with_registration`, i.e. when a tool is actually SERVED — never at import or startup.

    Degrade-clean and D5-classed: a registry failure is `note_degraded` ONCE, loudly (the hook then
    stays fail-open on ambiguity, exactly as before this stage), then swallowed — self-registering
    this window must NEVER break the tool call the user asked for. The class is broad on purpose:
    `touch` spans identity minting, PID/OS probing, and cross-process-locked transient-file IO, and
    none of that is worth failing a user's tool call over."""
    try:
        from ..config import Surface
        from .. import session_registry as SR
        SR.touch(Surface.load(path))
    except Exception as exc:  # noqa: BLE001 - the contract IS broad (see docstring); it SPEAKS.
        from ..degrade import FAILURE_LOCAL_IO, note_degraded
        note_degraded(
            "session-registry", FAILURE_LOCAL_IO,
            fallback="this window is not recorded in the live-session registry; the run-state gate "
                     "stays fail-open when this repo has ambiguous run state",
            fix="check permissions/disk under `.mokata/temp_local/`, then run `mokata doctor`",
            detail=str(exc))


def _with_registration(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a tool fn so THIS window self-registers just before the tool runs (R-MCP).

    Signature-transparent: `functools.wraps` sets `__wrapped__`, which `inspect.signature` follows,
    so the FastMCP tool schema built from the wrapper is byte-identical to the unwrapped fn (no
    behaviour change to any tool's inputs/outputs — registration is a pure side effect)."""
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        _register_this_window(_call_path(args, kwargs))
        return fn(*args, **kwargs)
    return wrapper


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
        # R-MCP: every served tool call self-registers this window first (see `_with_registration`).
        server.add_tool(_with_registration(spec.fn), name=spec.name,
                        description=(spec.fn.__doc__ or "").strip())
    return server


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="mokata-mcp",
        description="mokata MCP server (stdio) — mokata operations as native MCP tools.")
    parser.add_argument("--path", default=".",
                        help="repo root the tools operate on (default: current dir)")
    # B-VER: `--version` prints mokata's version and exits 0 BEFORE any SDK import — it is the
    # target of the version-parity probe (`mcp_admin.version_parity`), which launches this exact
    # registered command to learn which mokata the server actually serves. argparse's `version`
    # action fires during `parse_args`, ahead of the `mcp_available()` SDK check below, so the
    # probe target never hangs and never needs MCP deps (works in a stripped env). A server that
    # PREDATES this flag rejects it with exit 2 — and that failure is itself the staleness signal.
    from .. import __version__
    parser.add_argument("--version", action="version", version=__version__)
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
