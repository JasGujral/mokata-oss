"""menu — the one-screen command palette (`mokata menu`).

Read-only: enumerates every shipped `/mokata:` command and bundled skill from their files and
prints the palette through the A4 legibility helpers. Colour + a Unicode box only on a real TTY;
a piped / redirected / NO_COLOR run degrades to a plain-ASCII palette with zero escape codes.
"""
from __future__ import annotations

import argparse


def cmd_menu(args: argparse.Namespace) -> int:
    from ..menu import render_menu
    print(render_menu())
    return 0


def register(sub, common):
    p_menu = sub.add_parser(
        "menu", parents=[common],
        help="command palette: every /mokata command + skill, with gate markers (read-only)",
    )
    p_menu.set_defaults(func=cmd_menu)


__all__ = ["cmd_menu"]
