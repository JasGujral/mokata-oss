"""`mokata events` — the typed stream's one human surface (CM.S5).

⛔ **THIS FILE EXISTS BECAUSE OF §7i, AND IT WAS NEARLY FORGOTTEN.** `team_events.publish` was
written, graded against a real Postgres, and reachable from NOTHING — a perfect mechanism nobody
runs, which is the class 0.0.21 has filed more often than any other. Two surfaces, no more:

  * `mokata events` — STATUS. What would publish, whether sharing is on, and ⭐ **whether a
    notification could be received at all over the configured DSN.** The pooler verdict exists so
    a human can see it; a §7e declaration nothing displays is a declaration nobody reads.
  * `mokata events --share` — the gated publish.

⚠ **AND NO MORE THAN THAT, deliberately.** A local-stream viewer is adjacent, useful and OUT OF
SCOPE: it belongs to the IDE's navigation work (ID.S2), and doc 00 forbids growing a stage's
surface on the way past. CM.S5 is "populate the table and wake its readers"; this is the smallest
surface that makes both reachable.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import argparse

from ..govern.ledger import AuditLedger
from ._common import _load_surface


def _surface_or_none(path: str):
    try:
        return _load_surface(path)
    except Exception:  # noqa: BLE001 — "not a mokata repo" is an ANSWER here, not a crash
        return None


def cmd_events(args: argparse.Namespace) -> int:
    if getattr(args, "share", False):
        return _cmd_events_share(args)
    return _cmd_events_status(args)


def _cmd_events_status(args: argparse.Namespace) -> int:
    from .. import team_events as TE
    from ..events import store_for_root
    surface = _surface_or_none(args.path)
    if surface is None:
        print("mokata is not initialized here — run `mokata init` first.")
        return 0
    data = surface.manifest.data
    local = store_for_root(args.path).count()
    print(f"events · local store: {local} event(s) recorded")
    if not TE.shared_enabled(data):
        print("events · publishing to the team store: OFF (local-first default)")
        print("  turn it on: `mokata config set settings.events.shared true`")
        return 0
    available, pending, env_name, msg = TE.pending_publish(args.path, surface)
    print(f"events · publishing: ON → ${env_name}")
    print(f"  {msg}")
    # ⭐ The §7e half. "Nobody is listening" and "nothing CAN listen" are different facts, and the
    # second one is invisible from the publisher's side — so it is printed, not just returned.
    state, why = TE.notify_support(TE.resolve_dsn(data=data))
    label = {TE.NOTIFY_SUPPORTED: "deliverable",
             TE.NOTIFY_POOLED: "CANNOT BE RECEIVED",
             TE.NOTIFY_UNKNOWN: "unknown"}[state]
    print(f"events · live notifications: {label} — {why}")
    if not available and pending == 0:
        return 1
    return 0


def _cmd_events_share(args: argparse.Namespace) -> int:
    # CM.S5 — publish this dev's NEW local events to the team's shared store. Data leaving the
    # machine → human-gated + secret-scanned (the WriteGate, kind `send`). Degrade-clean.
    from .. import team_events as TE
    surface = _surface_or_none(args.path)
    if surface is None:
        print("mokata is not initialized here — nothing to publish. Run `mokata init` first.")
        return 0
    ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
    res = TE.publish(args.path, surface, assume_yes=args.yes, confirm=None, out=print,
                     ledger=ledger)
    # A clean no-op (off / nothing new / backend absent) is SUCCESS; a real decline or a secret
    # block is non-zero so a script sees it. Same split as `audit --share`.
    if res.ok or res.reason in ("shared-off", "nothing-new", "unavailable"):
        return 0
    return 1


def register(sub, common):
    p_events = sub.add_parser(
        "events", parents=[common],
        help="show the typed event stream's status, or publish it to the team store",
    )
    p_events.add_argument("--share", action="store_true",
                          help="publish new local events to the team's shared store "
                               "(human-gated + secret-scanned; opt-in via settings.events.shared)")
    p_events.add_argument("--yes", action="store_true",
                          help="non-interactive; approve a --share publish (still secret-scanned)")
    p_events.set_defaults(func=cmd_events)


__all__ = ["cmd_events", "register"]
