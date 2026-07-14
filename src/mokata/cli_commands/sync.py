"""sync — manual flush + reconcile of the team write journal (TM.S5, doc 48 E3).

`mokata sync` is the team-mode reconcile: it recovers any rows the old silent fallback stranded
in the local floor, flushes the crash-safe journal to Postgres in one batch (each write carrying
the LEDGER ID of its original human approval — C5/P2, never a governance bypass; a per-publish
secret-scan still applies), then reconciles every compare-and-set conflict through the human gate
(P2) — keep-local / keep-remote, ledgered, never a silent last-writer-wins.

Local mode has no shared write path, so `sync` is a no-op there (zero-config is untouched). When
the connection isn't healthy the flush is SKIPPED (nothing lost) and the work-locally state is
reported — offline never blocks.
"""
from __future__ import annotations

import argparse
import os

from ._common import _load_surface, AuditLedger


def cmd_sync(args: argparse.Namespace) -> int:
    from .. import run_mode as RM, team_health, team_journal
    from ..project import project_id

    root = args.path
    surface = _load_surface(root)                      # requires an initialized repo

    if RM.read_mode(surface) != RM.TEAM:
        print("mokata sync: local mode — nothing to sync (team mode only). "
              "Switch with `mokata mode set team`.")
        return 0

    # The health verdict leads: sync always shows connection state first (never silent).
    verdict = team_health.check(surface, environ=os.environ)
    print(team_health.status_block(verdict))

    ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
    proj = project_id(surface)
    actor = _actor()

    res = team_journal.sync(
        surface, environ=os.environ, assume_yes=args.yes, ledger=ledger,
        recover=lambda: team_journal.live_recover(surface, os.environ, project=proj, actor=actor),
        out=print)

    if res.skipped:
        print(f"mokata sync: not flushed — {res.reason}")
        print(f"  {res.pending} write(s) journaled locally and waiting; {res.recovered} "
              "recovered from the local floor.")
        return 0

    print(f"mokata sync: flushed {res.flushed}, recovered {res.recovered}, "
          f"conflicts {res.conflicts_found} "
          f"(kept-local {res.resolved_local}, kept-remote {res.resolved_remote}, "
          f"deferred {res.deferred}), blocked {res.blocked}, still pending {res.pending}.")
    if res.deferred:
        print("  → some conflicts need your decision — re-run `mokata sync` interactively "
              "to resolve them (nothing was silently overwritten).")
    if res.blocked:
        print("  → some writes were blocked (a secret was detected) — remove it, then re-sync.")
    return 0


def _actor() -> str:
    """Who to attribute this sync to. D5 — narrowed to ImportError (`team_audit.actor()` itself
    only reads `os.environ` and cannot raise, so `Exception` here could only ever have swallowed a
    bug), and the fallback is now `"unknown"`: that is `actor()`'s OWN unknown-sentinel, and two
    different words for the same unknown actor in the SAME shared audit log is a reader's bug."""
    try:
        from ..team_audit import actor
        return actor()
    except ImportError:
        return "unknown"


def register(sub, common):
    p_sync = sub.add_parser(
        "sync", parents=[common],
        help="flush + reconcile the team write journal (team mode; human-gated conflicts)",
    )
    p_sync.add_argument("--yes", action="store_true",
                        help="non-interactive: flush without prompting; conflicts are DEFERRED "
                             "(never silently overwritten) for an interactive re-run")
    p_sync.set_defaults(func=cmd_sync)


__all__ = ["cmd_sync"]
