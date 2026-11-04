"""explain — `mokata explain <target>` PRINTS the Claude Code command that writes a change
explainer (0.0.27 P46). It never starts Claude Code and never calls a model: Claude Code runs on
the user's own login, and the page is written through the route's `explain` slash command
(`/mokata:explain` on the plugin route, `/explain` after `mokata init`).

The target is validated (`session <id>` · `run <id>` · `range <base> <head>` · `live`) and a range
is resolved to its two commit SHAs here, so the printed line carries only hex and id characters —
nothing a shell, cmd.exe or PowerShell would read as syntax, so it needs no quoting on any of them.
When a billing credential is set in this environment the same notice the embedded terminal shows
is printed first (names only, never values).
"""
from __future__ import annotations

import argparse
import os
import sys


def command_for(root: str, words) -> str:
    """The `claude "<slash> explain …"` line for `words` (raises BundleRefused on a bad target)."""
    from ..explain import bundle as B
    spec = B.parse_target(" ".join(words) if words else "live")
    if spec["kind"] == "range":
        b, h = B.rev(root, spec["base"]), B.rev(root, spec["head"])
        if not (b and h):
            raise B.BundleRefused("the range's refs do not resolve to commits here")
        spec = {"kind": "range", "base": b, "head": h}
    from ..plugin_runtime import slash_command
    return f'claude "{slash_command("explain", root)} {B.target_text(spec)}"'


def cmd_explain(args: argparse.Namespace) -> int:
    from ..billing_env import billing_overrides, notice
    from ..config import find_project_root
    from ..explain.bundle import BundleRefused
    root = os.path.realpath(find_project_root(getattr(args, "path", None) or "."))
    try:
        line = command_for(root, args.target)
    except BundleRefused as exc:
        print(f"mokata explain: {exc}", file=sys.stderr)
        return 2
    names = billing_overrides()
    if names:
        print("mokata: " + notice(names), file=sys.stderr)
    print("Run this in Claude Code's terminal (it uses your own Claude login; mokata calls no "
          "model):")
    print(line)
    return 0


def register(sub, common):
    p = sub.add_parser(
        "explain", parents=[common],
        help="print the Claude Code command that explains a change set (never runs it)",
    )
    p.add_argument("target", nargs="*",
                   help="session <id> | run <id> | range <base> <head> | live (default: live)")
    p.set_defaults(func=cmd_explain)


__all__ = ["cmd_explain", "command_for", "register"]
