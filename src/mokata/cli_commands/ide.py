"""`mokata ide` — open the read-only review window (0.0.22 E2), or re-focus the open one."""

from __future__ import annotations

import argparse
import os
import sys


def cmd_ide(args: argparse.Namespace) -> int:
    from ..config import find_project_root
    root = os.path.realpath(find_project_root(args.path or os.getcwd()))
    if getattr(args, "trust", False) or getattr(args, "untrust", False):
        # 0.0.27 P24 — workspace trust (a language server runs the repo's code): the CLI twin of
        # the window's gated Trust. TTY-only, never inside a Claude Code session, ledgered.
        from ..ide.trust import cli_decide
        return cli_decide(root, bool(getattr(args, "trust", False)))
    target = None
    if getattr(args, "target", None):
        # 0.0.27 P11 — `mokata ide path[:line[:col]]`: a file inside the workspace, read from the
        # directory the command ran in. Refused loudly otherwise (nothing is started for it).
        from ..ide.locator import resolve
        from ..ide.nav import PathRefused
        if args.demo or args.foreground:
            print("mokata ide: a path:line:col opens in the normal window — drop "
                  f"{'--demo' if args.demo else '--foreground'}", file=sys.stderr)
            return 2
        try:
            loc = resolve(root, args.target, os.getcwd())
        except PathRefused as exc:
            print(f"mokata ide: {exc}", file=sys.stderr)
            return 2
        target = (loc.path, loc.line, loc.col)
    if args.foreground:
        from ..ide.server import serve
        print("mokata ide: serving in the foreground (Ctrl-C to stop) — the pairing link follows",
              flush=True)
        import secrets
        token = secrets.token_urlsafe(24)
        port = args.port or 0
        if not port and not args.demo:
            from ..ide.bus import preferred_port
            port = preferred_port(root)    # ID.S3: the stable port `--ide-bridge` hooks post to
        if not port:
            from ..ide.server import free_port
            port = free_port()
        # `flush=True` because `serve()` below never returns: the link is the one thing this mode
        # exists to hand over, and it must not depend on a buffer being pushed out by whatever
        # the server happens to print next. (Measured: today it does get out through a pipe
        # either way — this is the guarantee, not a repair of a seen defect.)
        print(f"  http://127.0.0.1:{port}/pair?t={token}"
              + (f"&focus={args.session}" if args.session else ""), flush=True)
        # P38: a foreground window at a human TTY may print one-time approval codes to this
        # terminal (re-checked by the server; never in a detached child, never in CLAUDECODE).
        return serve(root, port, args.session, args.demo, None, token, terminal=_terminal_wanted(args),
                     approve_here=not args.demo)
    try:
        from ..ide.launch import open_or_focus
        launched = open_or_focus(root, args.session, demo=args.demo, browser=not args.no_browser,
                                 terminal=_terminal_wanted(args), target=target)
    except ImportError:
        from ..ide.server import INSTALL_LINE
        print(INSTALL_LINE, file=sys.stderr)
        return 3
    except RuntimeError as exc:
        print(f"mokata ide: {exc}", file=sys.stderr)
        return 1
    print(launched.line())
    _bridge_notice(root, launched.url, args.demo)
    return 0


def _terminal_wanted(args) -> bool:
    """0.0.25 — the embedded terminal starts processes, so it is OPT-IN (`--terminal`), offered only
    to a human at a TTY, and never inside a Claude Code session (`CLAUDECODE=1`: a window the agent
    opened must not hand the agent a second shell). A refused request says why."""
    if not getattr(args, "terminal", False):
        return False
    if os.environ.get("CLAUDECODE"):
        print("note: --terminal ignored — this is a Claude Code session (CLAUDECODE is set); run "
              "`mokata ide --terminal` from your own terminal", file=sys.stderr)
        return False
    if not _human_tty():
        print("note: --terminal ignored — no interactive terminal (stdin/stdout is not a TTY)",
              file=sys.stderr)
        return False
    return True


def _human_tty() -> bool:
    """ID.S3 — the embedded terminal is offered only to a window a human started at a TTY (doc 109
    §6's bar for approvals: a model-run `mokata ide` has no TTY, so it gets no process-starting
    window)."""
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError, OSError):
        return False


def _bridge_notice(root: str, url: str, demo: bool) -> None:
    """ID.S3 — say so when `--ide-bridge` hooks cannot reach this server (their port was taken)."""
    from ..ide.bus import read_bridge
    rec = None if demo else read_bridge(root)
    if rec and f"127.0.0.1:{rec['port']}/" not in url:
        print(f"note: the live-bridge port {rec['port']} was busy, so Claude Code's http hooks "
              "cannot reach this window; follow mode falls back to the hook timeline (≤1 s). Free "
              "the port, or re-run `mokata setup claude --ide-bridge` after deleting "
              ".mokata/temp_local/ide/bridge.json.")


def register(sub, common):
    p = sub.add_parser(
        "ide", parents=[common],
        help="open the read-only review window (live diff, any point in time, per session, PRs); "
             "if it is already open, re-focus it",
    )
    p.add_argument("target", nargs="?", default=None, metavar="path[:line[:col]]",
                   help="open the window at this file (and line, column) inside the workspace, e.g. "
                        "src/mokata/cli.py:42:7")
    p.add_argument("--session", default=None,
                   help="focus this Claude Code session (its run, files and timeline)")
    p.add_argument("--demo", action="store_true",
                   help="show a clearly-labelled SAMPLE dataset (nothing real), to review the UI")
    p.add_argument("--no-browser", dest="no_browser", action="store_true",
                   help="print the link instead of opening a browser")
    p.add_argument("--foreground", action="store_true",
                   help="serve in this terminal until Ctrl-C (default: a child that exits when idle)")
    p.add_argument("--port", type=int, default=0, help="with --foreground: the port (default: free)")
    p.add_argument("--terminal", action="store_true",
                   help="offer the embedded terminal in the window (off by default; human TTY only, "
                        "never inside a Claude Code session)")
    p.add_argument("--trust", action="store_true",
                   help="mark this workspace TRUSTED so the window may run its language servers "
                        "(they execute project code); a human at a TTY, ledgered")
    p.add_argument("--untrust", action="store_true",
                   help="revoke workspace trust (language servers stop; ledgered)")
    p.set_defaults(func=cmd_ide)


__all__ = ["cmd_ide"]
