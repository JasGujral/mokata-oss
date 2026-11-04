"""evidence — `mokata evidence commit [--run <id>]` (G3-EVIDENCE, 0.0.27).

Commits a run's ledger rows and gate evidence to `.mokata/runs/<run>/` so the PR check can show
them on a CI checkout (which has no `temp_local/`). The ONE writer is `run_record.commit_run_record`:
it prints every byte it would write, asks, and writes through the WriteGate (secret scan, audit
ledger). The in-harness twin is the `evidence_commit` MCP tool (propose → `mokata approve <id>`).
"""
from __future__ import annotations

import argparse
import sys


def cmd_evidence_commit(args: argparse.Namespace) -> int:
    from ..config import ConfigError, Surface
    from ..govern.ledger import AuditLedger
    from ..run_record import commit_run_record
    from ..run_resolver import resolve_run
    root = args.path
    if not Surface.is_initialized(root):
        print("mokata evidence commit: mokata is not initialized here — nothing to commit",
              file=sys.stderr)
        return 2
    try:
        surface = Surface.load(root)
    except (ConfigError, OSError) as exc:
        print(f"mokata evidence commit: cannot load the project ({exc})", file=sys.stderr)
        return 2
    res = resolve_run(root, run_id=args.run or None)
    if not res.run_id:
        names = f" (candidates: {', '.join(res.candidates)})" if getattr(res, "candidates", ()) \
            else ""
        print("mokata evidence commit: no run could be resolved here — name it with "
              f"--run <id>{names}", file=sys.stderr)
        return 2
    try:
        result = commit_run_record(root, res.run_id, assume_yes=args.yes,
                                   ledger=AuditLedger.from_mokata_dir(surface.mokata_dir))
    except (OSError, ValueError) as exc:
        print("mokata evidence commit: the run's machine-local ledger or evidence cannot be read "
              f"whole ({exc}) — nothing was written", file=sys.stderr)
        return 2
    if result.committed:
        return 0
    return 1


def register(sub, common):
    p = sub.add_parser(
        "evidence", parents=[common],
        help="commit a run's ledger + gate evidence to .mokata/runs/ for the PR check "
             "(human-gated)",
    )
    esub = p.add_subparsers(dest="evidence_cmd", metavar="<action>")
    esub.required = True
    pc = esub.add_parser("commit", parents=[common],
                         help="write .mokata/runs/<run>/ (preview every byte, then ask)")
    pc.add_argument("--run", default="", help="the run id (default: the resolved run)")
    pc.add_argument("--yes", action="store_true",
                    help="approve the previewed write without the interactive question")
    pc.set_defaults(func=cmd_evidence_commit)


__all__ = ["cmd_evidence_commit", "register"]
