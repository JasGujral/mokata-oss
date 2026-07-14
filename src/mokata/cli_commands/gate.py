"""gate — the SI.1 run-state gates: their status, and the P14 session-scoped override.

The `gate-guard` PreToolUse hook (see `gate_hook`) blocks a native Write/Edit that violates a
persisted run-state gate. These gates are METHODOLOGY gates, not security ones, so P14 says they
are overridable — under an exact discipline, and only that discipline:

  * **explicit**       — you name the gate and give a `--reason`. There is no "override everything",
                         and deliberately NO env-var kill switch: an env var is a side door any
                         process (or an over-eager agent) can open silently, which is the opposite
                         of a human decision.
  * **re-confirmed**   — an interactive y/N that restates exactly what will stop being enforced.
  * **session-scoped** — the override is stored under `gate_override__<run_id>`, so it dies with the
                         run. A new session mints a new run_id, which has no override file, and
                         enforcement is back. Nothing to remember to turn off.
  * **ledgered**       — who / when / which gate / why, appended to the hash-chained audit ledger
                         (`govern/enforce.py`'s pattern: an override is a recorded decision, never a
                         silent one). `mokata audit` shows it forever.

The secret-guard is NOT here and never will be: a security block is not overridable (P2/I1).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from ._common import AuditLedger, _load_surface

# The ledger kind for a run-state gate override (sibling of enforce.py's `rule_override`, which
# covers the 4-tier governance rules; this one covers the run-state gates).
GATE_OVERRIDE_KIND = "gate_override"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _resolve(args: argparse.Namespace):
    """(surface, run_id, ambiguous) — the run the HOOK would enforce, resolved by the hook's OWN
    resolver so an override can never land on a different run than the one being gated."""
    from ..gate_hook import resolve_run
    surface = _load_surface(args.path)
    run = resolve_run(surface.root, getattr(args, "run", None))
    return surface, run.run_id, run


def cmd_gate_status(args: argparse.Namespace) -> int:
    """What the gates would decide here, and what is overridden (read-only)."""
    from ..gate_hook import GATES, read_override

    surface, run_id, run = _resolve(args)
    print("mokata run-state gates (enforced on native Write/Edit by the gate-guard hook):")
    if run.ambiguous:
        print(f"  run: AMBIGUOUS — {len(run.candidates)} runs have state here and none is pinned.")
        print("  Gates are OFF for any window mokata cannot identify (it will not guess which")
        print("  run your edits belong to). Pin one with MOKATA_SESSION_ID to enforce.")
        return 0
    if run_id is None:
        print("  run: none — no mokata run has state in this repo; nothing to enforce.")
        return 0

    overrides = read_override(surface.root, run_id)
    print(f"  run: {run_id[:8]}")
    for gate in GATES:
        state = "OVERRIDDEN (this session)" if gate in overrides else "enforced"
        print(f"  {gate:32} {state}")
    if overrides:
        print("\n  The override expires with this session — a new session enforces again.")
    return 0


def cmd_gate_override(args: argparse.Namespace) -> int:
    """Explicitly, re-confirmedly, session-scopedly, ledgeredly stop enforcing ONE gate (P14)."""
    from ..gate_hook import GATES, OVERRIDE_PREFIX, read_override
    from ..prompt import read_yes_no

    gate = args.gate
    if gate not in GATES:
        print(f"error: unknown gate '{gate}' (expected one of: {', '.join(GATES)})")
        return 1

    surface, run_id, run = _resolve(args)
    if run.ambiguous:
        print(f"error: {len(run.candidates)} runs have state in this repo and none is pinned, so "
              f"mokata cannot tell which run to override.")
        print("       The gates are already OFF for an unidentifiable window — there is nothing "
              "to override.")
        return 1
    if run_id is None:
        print("error: no mokata run has state in this repo — the gates are not enforcing, so "
              "there is nothing to override.")
        return 1

    # Re-confirmation (P14): restate EXACTLY what stops being enforced, then require a yes.
    plan = (f"Override the '{gate}' gate for this session (run {run_id[:8]}).\n"
            f"  What stops being enforced: the gate-guard hook will ALLOW native Write/Edit to\n"
            f"  implementation files that this gate would otherwise block.\n"
            f"  Scope: THIS session only — a new session enforces again.\n"
            f"  Reason (recorded to the audit ledger): {args.reason}\n"
            f"  The secret-guard is unaffected — security blocks are never overridable.")
    if not (args.yes or read_yes_no(plan, f"Override '{gate}' for this session?")):
        print("aborted — the gate stays enforced.")
        return 1

    scopes = sorted(set(read_override(surface.root, run_id)) | {gate})
    surface.state.write(OVERRIDE_PREFIX + run_id, {
        "run_id": run_id, "scopes": scopes, "actor": args.actor,
        "reason": args.reason, "at": _now_iso(),
    })
    AuditLedger.from_mokata_dir(surface.mokata_dir).record(
        GATE_OVERRIDE_KIND, gate=gate, run=run_id, actor=args.actor,
        decision="override", scope="session", reason=args.reason)

    print(f"'{gate}' overridden for this session (run {run_id[:8]}) — recorded to the audit ledger.")
    print("It expires with this session. Clear it now with: mokata gate clear")
    return 0


def cmd_gate_clear(args: argparse.Namespace) -> int:
    """Drop this session's overrides and enforce again (also ledgered — a decision either way)."""
    from ..gate_hook import OVERRIDE_PREFIX, read_override

    surface, run_id, run = _resolve(args)
    if run_id is None:
        print("nothing to clear — no identified mokata run in this repo.")
        return 0
    overrides = read_override(surface.root, run_id)
    if not overrides:
        print("nothing to clear — no gate is overridden for this session.")
        return 0

    surface.state.delete(OVERRIDE_PREFIX + run_id)
    AuditLedger.from_mokata_dir(surface.mokata_dir).record(
        GATE_OVERRIDE_KIND, gate=",".join(sorted(overrides)), run=run_id, actor=args.actor,
        decision="cleared", scope="session", reason="override cleared")
    print(f"cleared: {', '.join(sorted(overrides))} — the gates are enforcing again.")
    return 0


def register(sub, common):
    p_gate = sub.add_parser(
        "gate", parents=[common],
        help="run-state gates: status, and the session-scoped override (P14)",
    )
    gsub = p_gate.add_subparsers(dest="gate_command", required=True)

    p_status = gsub.add_parser("status", parents=[common],
                               help="what the run-state gates would enforce here (read-only)")
    p_status.add_argument("--run", default=None, help="the run id to inspect (default: resolved)")
    p_status.set_defaults(func=cmd_gate_status)

    p_over = gsub.add_parser(
        "override", parents=[common],
        help="stop enforcing ONE gate for THIS session (explicit, re-confirmed, ledgered)")
    p_over.add_argument("gate", help="the gate to override (e.g. no-code-without-failing-test)")
    p_over.add_argument("--reason", required=True,
                        help="why (required; recorded to the audit ledger)")
    p_over.add_argument("--run", default=None, help="the run id to override (default: resolved)")
    p_over.add_argument("--actor", default="human", help="who is overriding (default: human)")
    p_over.add_argument("--yes", action="store_true",
                        help="skip the interactive re-confirmation (non-interactive use)")
    p_over.set_defaults(func=cmd_gate_override)

    p_clear = gsub.add_parser("clear", parents=[common],
                              help="drop this session's overrides and enforce again")
    p_clear.add_argument("--run", default=None, help="the run id to clear (default: resolved)")
    p_clear.add_argument("--actor", default="human", help="who is clearing (default: human)")
    p_clear.set_defaults(func=cmd_gate_clear)
