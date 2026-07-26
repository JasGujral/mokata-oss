"""migrate — one-time, human-gated migration of a DEPRECATED channel into the canonical store.

`mokata migrate <channel>` previews (counts + a sample), asks for approval, then routes the move
through the EXISTING gated write path (SIMP.S2). Non-destructive, idempotent (a re-run is a no-op),
secret-scanned on ingest. The deprecated channels work TODAY (with a deprecation warning); they
are scheduled for removal in 0.0.17.
"""
from __future__ import annotations

import argparse
import sys

from ._common import _load_surface, _ledger_for
from ..migrate_channels import CHANNELS, plan_channel_migration, run_channel_migration
from ..prompt import read_yes_no


def cmd_migrate(args: argparse.Namespace) -> int:
    surface = _load_surface(args.path)
    channel = args.channel

    plan = plan_channel_migration(surface, channel, file=args.file)
    print(plan.render())
    if plan.already_migrated and not args.force:
        return 0
    if plan.count == 0:
        return 0                       # nothing to migrate (preview already said so)

    def _confirm(text: str) -> bool:
        return read_yes_no(text, "Proceed with the migration?")

    res = run_channel_migration(
        surface, channel, confirm=_confirm, assume_yes=args.yes, file=args.file,
        force=args.force, ledger=_ledger_for(args.path))
    print(res.render(), file=sys.stderr if res.aborted else sys.stdout)
    return 1 if res.aborted else 0


def register(sub, common):
    p = sub.add_parser(
        "migrate", parents=[common],
        help="one-time gated migration of a deprecated channel into the canonical store "
             "(obsidian/native-memory/memory-share/vault; the channels work today with a "
             "deprecation warning, scheduled for removal in 0.0.17)",
        description="One-time, human-gated migration of a deprecated channel into the canonical "
                    "store. The deprecated channels work today with a deprecation warning; they "
                    "are scheduled for removal in 0.0.17.")
    p.add_argument("channel", choices=CHANNELS,
                   help="the deprecated channel to migrate into the canonical shape")
    p.add_argument("--file", default="",
                   help="the memory-share file to read (memory-share channel; default "
                        ".mokata/memory-share.json)")
    p.add_argument("--yes", action="store_true",
                   help="non-interactive (approve the gated migration)")
    p.add_argument("--force", action="store_true",
                   help="re-run even if this channel was already migrated for this repo")
    p.set_defaults(func=cmd_migrate)


__all__ = ["cmd_migrate", "register"]
