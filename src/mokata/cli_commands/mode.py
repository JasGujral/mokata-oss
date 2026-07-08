"""mode — show/set the run mode (TM.S1).

`mokata mode` shows the current mode + the team-readiness preflight (a session is never
ambiguous about which mode it's in). `mokata mode set local|team` changes it:

  * `set team` runs the FAIL-CLOSED preflight and refuses at TM.S1 with a named,
    actionable pointer to TM.S2 — team mode is never half-activated, and a refused
    activation writes NOTHING;
  * `set local` is the zero-config default: on an already-local repo it is a no-op that
    writes nothing (local stays byte-for-byte unchanged); it only writes (human-gated, via
    the same `config set` WriteGate path) when switching back from a non-local setting.
"""
from __future__ import annotations

import argparse
import sys

from ._common import (
    _load_surface,
    AuditLedger,
)


def cmd_mode(args: argparse.Namespace) -> int:
    from .. import run_mode as RM
    from ..legibility import _color_enabled

    root = args.path
    surface = _load_surface(root)                      # requires an initialized repo
    ascii_only = not _color_enabled()

    # --- show ----------------------------------------------------------------------------
    if getattr(args, "action", None) is None:
        print(RM.mode_line(surface))
        # surface the team-readiness preflight too, so `mode` is never ambiguous.
        print(RM.team_preflight(surface).render(ascii_only=ascii_only))
        # TM.S5 — the ONE health surface: in team mode, show the same cached connection verdict
        # the badge/doctor/in-chat use (a broken connection is never silent; trouble offers
        # work-locally). Runs the bounded ≤500ms probe, refreshing the shared cache.
        if RM.read_mode(surface) == RM.TEAM:
            import os as _os
            from .. import team_health
            verdict = team_health.check(surface, environ=_os.environ)
            print(team_health.status_block(verdict, ascii_only=ascii_only))
        return 0

    # --- set -----------------------------------------------------------------------------
    target = args.mode
    if target is None:
        print("error: `mokata mode set <local|team>` requires a target mode",
              file=sys.stderr)
        return 2
    if target == RM.TEAM:
        # Fail-closed: reach CONNECTED then activate — the shared `activate_team` primitive runs
        # the REAL preflight (identity + DSN + reachable + compatible schema) and REFUSES unless
        # every prerequisite passes (writing nothing), else performs the human-gated durable mode
        # write. Every refusal links the canonical setup & operations page.
        ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
        res = RM.activate_team(root, surface, assume_yes=args.yes, ledger=ledger, out=print)
        if res.activated:
            print("mokata mode: activated team mode. "
                  + res.report.render(ascii_only=ascii_only))
            return 0
        return 1

    # target == local
    if RM.read_mode(surface) == RM.LOCAL and RM.stored_mode(surface) in (None, RM.LOCAL):
        # already local (the zero-config default) → no-op, nothing written.
        print("mokata mode: already local (zero-config default) — nothing to change.")
        return 0

    # switching back to local from a non-local setting → the human-gated config write path.
    from .. import config_cmd
    ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
    res = config_cmd.config_set(root, f"settings.{RM.MODE_SETTING}", RM.LOCAL,
                                assume_yes=args.yes, ledger=ledger)
    if res.committed:
        print("mokata mode: set to local.")
        return 0
    return 1


def register(sub, common):
    p_mode = sub.add_parser(
        "mode", parents=[common],
        help="show the run mode (local|team) + team readiness, or `set local|team`",
    )
    p_mode.add_argument("action", nargs="?", choices=("set",), default=None,
                        help="`set` to change the mode (omit to show the current mode)")
    p_mode.add_argument("mode", nargs="?", choices=("local", "team"), default=None,
                        help="the target mode for `set` (local = zero-config default; team "
                             "= shared databases, gated by a fail-closed preflight)")
    p_mode.add_argument("--yes", action="store_true",
                        help="non-interactive (approve the gated mode write)")
    p_mode.set_defaults(func=cmd_mode)


__all__ = ["cmd_mode"]
