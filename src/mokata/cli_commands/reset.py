"""reset — remove mokata state (uninstall / reset; --keep-config keeps the manifest)."""
from __future__ import annotations

import argparse
import sys

from ._common import (
    plan_reset,
    reset_state,
)


def cmd_reset(args: argparse.Namespace) -> int:
    plan = plan_reset(args.path, keep_config=args.keep_config)
    if not plan.targets:
        print("reset: nothing to remove.")
        return 0
    print("reset will remove:")
    for t in plan.targets:
        print(f"  {t}")
    result = reset_state(args.path, keep_config=args.keep_config,
                         assume_yes=args.yes, backup_dir=args.backup)
    if result.aborted:
        print(f"\n{result.message}", file=sys.stderr)
        return 1
    print(f"removed {len(result.removed)} path(s)"
          + (f"; backed up to {args.backup}" if args.backup else ""))
    return 0


def register(sub, common):
    p_reset = sub.add_parser(
        "reset", parents=[common],
        help="remove mokata state (.mokata/); --keep-config keeps the manifest",
    )
    p_reset.add_argument("--keep-config", action="store_true",
                         help="keep manifest + constitution; remove only state")
    p_reset.add_argument("--backup", default=None,
                         help="move state to this dir instead of deleting (reversible)")
    p_reset.add_argument("--yes", action="store_true",
                         help="non-interactive; skip the confirmation prompt")
    p_reset.set_defaults(func=cmd_reset)


__all__ = [
    "cmd_reset",
]
