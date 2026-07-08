"""mcp — discover external MCP servers (H4) + manage the bundled mokata server (start / status / install)."""
from __future__ import annotations

import argparse
import os
import sys

from ._common import (
    mcp_admin,
    MCPRegistry,
    SCOPES,
    SetupError,
    _load_surface,
)


def cmd_mcp(args: argparse.Namespace) -> int:
    # Stage 3b.2 — `mcp` is a command group: manage the bundled mokata MCP server
    # (start/status/install), plus the original H4 external-server discovery as the default.
    action = getattr(args, "action", None) or "discover"
    if action == "discover":
        return _cmd_mcp_discover(args)
    if action == "start":
        return _cmd_mcp_start(args)
    if action == "status":
        return _cmd_mcp_status(args)
    if action == "install":
        return _cmd_mcp_install(args)
    print(f"mcp: unknown action '{action}' (use discover | start | status | install).",
          file=sys.stderr)
    return 2


def _cmd_mcp_discover(args: argparse.Namespace) -> int:
    # H4 — discover MCP servers (from .mokata/mcp.json) and map to roles; degrade cleanly.
    surface = _load_surface(args.path)
    reg = MCPRegistry.discover(path=os.path.join(surface.mokata_dir, "mcp.json"))
    if not reg.servers:
        print("mcp: no servers discovered (degraded — none present).")
        return 0
    print(f"mcp: {len(reg.servers)} server(s)")
    for cap, names in reg.map_to_roles().items():
        print(f"  {cap}: {', '.join(names)}")
    return 0


def _cmd_mcp_start(args: argparse.Namespace) -> int:
    # Run the bundled server foreground on stdio (manual/debug). Delegate to the mcp_server
    # entrypoint so the Stage-3b.1 fail-loud SDK-absent guard is reused verbatim.
    from .. import mcp_server
    return mcp_server.main(["--path", args.path])


def _cmd_mcp_status(args: argparse.Namespace) -> int:
    # The full wiring check — registered? enabled? permitted? CONNECTED? — each with its fix.
    # Uses the SAME shared `mcp_admin.full_status` reporter as `mokata doctor` so they can't
    # drift. Fail-closed. rc reflects reachability (CONNECTED); the enabled/permitted gaps that
    # cause the stuck-loop are surfaced as lines with a named fix (`mokata mcp install`).
    fs = mcp_admin.full_status(root=args.path, home=getattr(args, "home", None))
    stream = sys.stdout if fs.connected else sys.stderr
    for line in fs.lines:
        print(line, file=stream)
    return 0 if fs.connected else 1


def _cmd_mcp_install(args: argparse.Namespace) -> int:
    # Write/repair the Claude Code registration (absolute-path command, merge-safe, idempotent)
    # AND grant Claude Code permission for mokata's MCP tools + enable the server (the
    # "stuck-loop" fix) — unless `--no-grant`. Both writes are merge-safe and reversible.
    from .. import harness_setup
    home = getattr(args, "home", None)
    scope = getattr(args, "scope", None) or "project"
    grant = not getattr(args, "no_grant", False)
    try:
        path = harness_setup.mcp_install(scope, args.path, home=home, grant=grant)
    except SetupError as exc:
        print(f"mcp install: {exc}", file=sys.stderr)
        return 1
    command = harness_setup.resolved_mcp_command()
    print(f"mokata-mcp: registered ({scope} scope) in {path}")
    print(f"  command: {command}")
    if command == harness_setup.MCP_COMMAND:
        print("  note: `mokata-mcp` was not found on PATH — wrote the bare command. Ensure "
              "mokata is installed so Claude Code can launch it.")
    if grant:
        settings = harness_setup.resolve_targets(scope, args.path, home, "claude").settings_path
        print(f"  ✓ Granted Claude Code permission for mokata's MCP tools + enabled the "
              f"server in {settings}")
    print("  next: restart Claude Code, then `mokata mcp status` to verify the handshake.")
    return 0


def register(sub, common):
    p_mcp = sub.add_parser(
        "mcp", parents=[common],
        help="manage the mokata MCP server (start | status | install) or discover external "
             "servers (default)",
    )
    p_mcp.add_argument(
        "action", nargs="?", default="discover",
        choices=("discover", "start", "status", "install"),
        help="discover (default): map external MCP servers to roles (H4) · start: run the "
             "mokata server foreground on stdio (debug) · status: `initialize` handshake "
             "against the registered server (CONNECTED ✓ or a specific failure + fix) · "
             "install: write/repair the Claude Code registration (absolute-path, idempotent)")
    p_mcp.add_argument(
        "--scope", choices=SCOPES, default="project",
        help="install/status registration scope: project (.mcp.json) or user (~/.claude.json)")
    p_mcp.add_argument(
        "--no-grant", action="store_true",
        help="install: do NOT grant Claude Code permission for mokata's MCP tools / enable the "
             "server in settings.json (for teams that manage permissions themselves)")
    p_mcp.add_argument("--home", default=None, help=argparse.SUPPRESS)  # test/override home dir
    p_mcp.set_defaults(func=cmd_mcp)


__all__ = [
    "cmd_mcp",
    "_cmd_mcp_discover",
    "_cmd_mcp_start",
    "_cmd_mcp_status",
    "_cmd_mcp_install",
]
