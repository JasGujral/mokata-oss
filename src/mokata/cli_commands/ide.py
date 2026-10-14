"""`mokata ide` — open the read-only review window (0.0.22 E2), or re-focus the open one."""

from __future__ import annotations

import argparse
import os
import sys


def cmd_ide(args: argparse.Namespace) -> int:
    from ..config import find_project_root
    root = os.path.realpath(find_project_root(args.path or os.getcwd()))
    if args.foreground:
        from ..ide.server import serve
        print("mokata ide: serving in the foreground (Ctrl-C to stop) — the pairing link follows")
        import secrets
        token = secrets.token_urlsafe(24)
        port = args.port or 0
        if not port:
            from ..ide.server import free_port
            port = free_port()
        print(f"  http://127.0.0.1:{port}/pair?t={token}"
              + (f"&focus={args.session}" if args.session else ""))
        return serve(root, port, args.session, args.demo, None, token)
    try:
        from ..ide.launch import open_or_focus
        launched = open_or_focus(root, args.session, demo=args.demo, browser=not args.no_browser)
    except ImportError:
        from ..ide.server import INSTALL_LINE
        print(INSTALL_LINE, file=sys.stderr)
        return 3
    except RuntimeError as exc:
        print(f"mokata ide: {exc}", file=sys.stderr)
        return 1
    print(launched.line())
    return 0


def register(sub, common):
    p = sub.add_parser(
        "ide", parents=[common],
        help="open the read-only review window (live diff, any point in time, per session, PRs); "
             "if it is already open, re-focus it",
    )
    p.add_argument("--session", default=None,
                   help="focus this Claude Code session (its run, files and timeline)")
    p.add_argument("--demo", action="store_true",
                   help="show a clearly-labelled SAMPLE dataset (nothing real), to review the UI")
    p.add_argument("--no-browser", dest="no_browser", action="store_true",
                   help="print the link instead of opening a browser")
    p.add_argument("--foreground", action="store_true",
                   help="serve in this terminal until Ctrl-C (default: a child that exits when idle)")
    p.add_argument("--port", type=int, default=0, help="with --foreground: the port (default: free)")
    p.set_defaults(func=cmd_ide)


__all__ = ["cmd_ide"]
