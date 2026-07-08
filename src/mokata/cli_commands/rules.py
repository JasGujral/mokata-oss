"""rules / audit / budget / bench — governance readouts (4-tier rules, the audit ledger + team audit, token budget, latency bench)."""
from __future__ import annotations

import argparse
import os

from ._common import (
    Surface,
    MOKATA_DIR,
    AuditLedger,
    BudgetReport,
    budget_statusline,
    load_rules,
    validate_caps,
    _load_surface,
    _SCOPE_CURRENT,
    _review_scope,
    _backend_projects,
)


def cmd_rules(args: argparse.Namespace) -> int:
    surface = _load_surface(args.path)
    rules = load_rules(surface)
    print("mokata rules (4 tiers):")
    for tier, rs in rules.items():
        cap = "no cap" if rs.cap is None else f"cap {rs.cap}"
        flag = "OK" if rs.within_cap else "OVER CAP"
        print(f"  {tier:13} {rs.line_count:4d} lines  ({cap}) — {flag}")
    errors = validate_caps(rules)
    if errors:
        for e in errors:
            print(f"  ! {e}")
        return 1
    # G5 — surface human-gated rule PROPOSALS distilled from recurring ledger corrections
    # (declined writes, reverts, spec conflicts). Proposal-only — never auto-added; quiet
    # and bounded when there are none (P11).
    from ..govern import learn_from_ledger
    ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
    proposals = learn_from_ledger(ledger)
    if proposals:
        print("\nRule proposals (recurring corrections — human-gated, not auto-added):")
        for p in proposals:
            print(f"  - {p.proposed_rule} [{p.rationale}]")
    return 0


def cmd_budget(args: argparse.Namespace) -> int:
    # F5 — aggregate logged savings from the audit ledger into a live budget report.
    ledger = AuditLedger.from_mokata_dir(os.path.join(args.path, MOKATA_DIR))
    report = BudgetReport.from_ledger(ledger)
    if not report.events:
        print("budget: no savings recorded yet.")
        return 0
    print(report.render())
    print(f"statusline: {budget_statusline(report)}")
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    # Stage 67 — wall-clock latency of the hot paths vs their budgets (read-only). Distinct from
    # `mokata budget` (tokens). Degrade-clean: an uninitialized repo just says so.
    if not Surface.is_initialized(args.path):
        print(f"bench: mokata is not initialized in '{args.path}' — run `mokata init` first "
              "(nothing to measure).")
        return 0
    from ..perf import DEFAULT_REPEAT, render_report, run_benchmarks
    surface = _load_surface(args.path)
    repeat = getattr(args, "repeat", None) or DEFAULT_REPEAT
    results = run_benchmarks(surface, repeat=repeat)
    print(render_report(results, ascii_only=getattr(args, "ascii", False)))
    over = [r.name for r in results if not r.within_budget]
    return 1 if over else 0


def cmd_audit(args: argparse.Namespace) -> int:
    # Stage 71 — team audit / shared activity log (shared OR local, conflict-free, NO telemetry).
    if getattr(args, "consent", None):
        return _cmd_audit_consent(args)
    if getattr(args, "share", False):
        return _cmd_audit_share(args)
    if getattr(args, "team", False) or getattr(args, "list_projects", False):
        return _cmd_audit_team(args)
    ledger = AuditLedger.from_mokata_dir(os.path.join(args.path, MOKATA_DIR))
    entries = ledger.entries()
    if not entries:
        print("audit ledger: empty.")
        return 0
    if getattr(args, "why", False):
        # Stage 49 — the read-only "what you did and WHY" timeline: bounded (a tail) and
        # derived from the ledger; surfaces each entry's decision + rationale. Writes nothing.
        from ..govern.ledger import WHY_TIMELINE_TAIL, why_timeline
        tail = args.tail if getattr(args, "tail", None) else WHY_TIMELINE_TAIL
        lines = why_timeline(entries, tail=tail)
        shown = min(len(lines), len(entries))
        print(f"audit — why timeline (last {shown} of {len(entries)}):")
        for line in lines:
            print(f"  {line}")
        return 0
    print(f"audit ledger — {len(entries)} entr{'y' if len(entries) == 1 else 'ies'}:")
    for e in entries:
        extra = " ".join(f"{k}={v}" for k, v in e.items()
                         if k not in ("seq", "kind", "at"))
        print(f"  #{e['seq']:<3} {e['kind']:<11} {extra}")
    return 0


def _audit_surface_or_none(path: str):
    """The Surface if mokata is initialized, else None (degrade-clean, no crash)."""
    try:
        if Surface.is_initialized(path):
            return Surface.load(path)
    except Exception:
        pass
    return None


def _cmd_audit_team(args: argparse.Namespace) -> int:
    # Stage 71 — the team-wide who-did-what/why over the SHARED log (spans all actors). Read-only;
    # degrade-clean when sharing is off / the backend is absent (LOCAL log unaffected).
    from ..team_audit import make_shared_log, render_team_timeline, team_audit_view
    surface = _audit_surface_or_none(args.path)
    if surface is None:
        print("mokata is not initialized here — no team audit. Run `mokata init` first.")
        return 0
    # Stage 71a — the team read is SCOPED to the current project by default; --all spans, --project
    # selects, --list-projects enumerates the projects present on the shared audit log.
    from ..team_audit import dsn_env_name, shared_enabled
    if getattr(args, "list_projects", False):
        if not shared_enabled(surface.manifest.data):
            print("audit: team sharing is OFF — projects apply to a shared backend.")
            return 0
        try:
            log = make_shared_log(dsn_env_name(surface.manifest.data))
            projs = _backend_projects(log) or []
        except Exception as exc:                                 # degrade-clean
            print(f"audit: shared log unavailable ({exc}).")
            return 0
        print(f"audit: {len(projs)} project(s) on the shared team log:")
        for p in projs:
            print(f"  {p}")
        return 0
    scope = _review_scope(args)
    view = (team_audit_view(args.path, surface) if scope is _SCOPE_CURRENT
            else team_audit_view(args.path, surface, project=scope))
    if not view.available:
        print(view.message)
        return 0
    print(f"team audit — {view.message}")
    if not view.entries:
        print("  (no shared entries yet — a teammate publishes with `mokata audit --share`.)")
        return 0
    tail = args.tail if getattr(args, "tail", None) else None
    for line in render_team_timeline(view, tail=tail):
        print(f"  {line}")
    return 0


def _cmd_audit_share(args: argparse.Namespace) -> int:
    # Stage 71 — publish this dev's NEW local entries to the team's shared log. Data leaving the
    # machine → human-gated + secret-scanned (the WriteGate, kind `send`). Degrade-clean.
    from ..team_audit import share_audit
    surface = _audit_surface_or_none(args.path)
    if surface is None:
        print("mokata is not initialized here — nothing to share. Run `mokata init` first.")
        return 0
    ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
    res = share_audit(args.path, surface, assume_yes=args.yes, confirm=None, out=print,
                      ledger=ledger)
    # A clean no-op (not enabled / already in sync / backend absent) is success; a real decline or
    # a secret block is a non-zero exit so scripts see it.
    if res.committed or res.reason in ("not enabled", "in sync", "unavailable"):
        return 0
    return 1


def _cmd_audit_consent(args: argparse.Namespace) -> int:
    # TM.S4 — the standing audit-publish consent (doc 48 C5/P-10): show | grant | revoke. Grant/
    # revoke are human-gated + ledgered; the batched publish inherits the grant WITHOUT weakening
    # the per-publish secret-scan gate. Revocable any time.
    from ..team_audit import (grant_standing_consent, has_standing_consent,
                              revoke_standing_consent)
    surface = _audit_surface_or_none(args.path)
    if surface is None:
        print("mokata is not initialized here — run `mokata init` first.")
        return 0
    action = args.consent
    if action == "show":
        on = has_standing_consent(surface.manifest.data)
        print("standing audit-publish consent: "
              + ("GRANTED (revoke: `mokata audit --consent revoke`)." if on
                 else "not granted — batched publish stays per-batch human-gated "
                      "(grant: `mokata audit --consent grant`)."))
        return 0
    ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
    if action == "grant":
        res = grant_standing_consent(args.path, surface, assume_yes=args.yes, out=print,
                                     ledger=ledger)
        return 0 if res.granted else 1
    res = revoke_standing_consent(args.path, surface, assume_yes=args.yes, out=print,
                                  ledger=ledger)
    return 0 if res.changed or not res.granted else 1


def register(sub, common):
    p_rules = sub.add_parser(
        "rules", parents=[common],
        help="show the 4-tier rules and their line budgets",
    )
    p_rules.set_defaults(func=cmd_rules)

    p_audit = sub.add_parser(
        "audit", parents=[common], help="show the append-only audit ledger",
    )
    p_audit.add_argument("--why", action="store_true",
                         help="a readable what+decision+why timeline (bounded; read-only)")
    p_audit.add_argument("--tail", type=int, default=None,
                         help="how many recent entries the --why / --team timeline shows "
                              "(default 50)")
    p_audit.add_argument("--team", action="store_true",
                         help="Stage 71: the team-wide who-did-what/why over the SHARED log "
                              "(spans all actors; read-only; NO telemetry). Needs sharing on")
    p_audit.add_argument("--share", action="store_true",
                         help="Stage 71: publish your NEW local entries to the team's SHARED log "
                              "(opt-in; human-gated + secret-scanned; append-only, per-actor)")
    p_audit.add_argument("--yes", action="store_true",
                         help="non-interactive; approve a --share publish (still secret-scanned)")
    p_audit.add_argument("--consent", choices=("show", "grant", "revoke"), default=None,
                         help="TM.S4: the standing audit-publish consent (doc 48 C5/P-10) — show "
                              "| grant | revoke (human-gated + ledgered; revocable; never weakens "
                              "the per-publish secret-scan gate)")
    # Stage 71a — scope the --team read over a shared backend (default: the current project only).
    p_audit.add_argument("--all", action="store_true",
                         help="with --team: span ALL projects on the shared log (default: this one)")
    p_audit.add_argument("--project", default=None,
                         help="with --team: read a specific project id on the shared log")
    p_audit.add_argument("--list-projects", dest="list_projects", action="store_true",
                         help="with --team: list the projects present on the shared log, then exit")
    p_audit.set_defaults(func=cmd_audit)

    p_budget = sub.add_parser(
        "budget", parents=[common],
        help="show token savings (live budget readout + statusline)",
    )
    p_budget.set_defaults(func=cmd_budget)

    p_bench = sub.add_parser(
        "bench", parents=[common],
        help="measure wall-clock latency of the hot paths vs their budget (read-only)",
    )
    p_bench.add_argument("--repeat", type=int, default=None,
                         help="samples per op (median reported; default 7)")
    p_bench.set_defaults(func=cmd_bench)


__all__ = [
    "cmd_rules",
    "cmd_budget",
    "cmd_bench",
    "cmd_audit",
    "_audit_surface_or_none",
    "_cmd_audit_team",
    "_cmd_audit_share",
]
