"""bootstrap / validate / release-check / route / detect / status / version / upgrade — the spine's core read + lifecycle commands."""
from __future__ import annotations

import argparse
import sys

from ._common import (
    __version__,
    build_bootstrap,
    Detector,
    read_yes_no,
    ManifestError,
    TOOL_CATALOG,
    _load_surface,
    _profile_for,
    _ledger_for,
)


def cmd_bootstrap(args: argparse.Namespace) -> int:
    surface = _load_surface(args.path)
    result = build_bootstrap(surface)
    sys.stdout.write(result.text)
    if args.show_tokens:
        status = "OK" if result.within_budget else "OVER BUDGET"
        print(
            f"\n[tokens ~{result.token_estimate} / budget {result.budget} — {status}]",
            file=sys.stderr,
        )
    return 0 if result.within_budget else 1


def cmd_validate(args: argparse.Namespace) -> int:
    surface = _load_surface(args.path)
    m = surface.manifest
    print(
        f"OK — manifest valid: profile '{m.profile}', "
        f"{len(m.capabilities)} capabilit{'y' if len(m.capabilities) == 1 else 'ies'}, "
        f"{len(m.tools)} tool(s)."
    )
    return 0


def cmd_release_check(args: argparse.Namespace) -> int:
    """Stage 61b — PURE/OFFLINE: assert every version field == the intended tag. Exit 1
    (fail-closed) naming any mismatch so `release.sh` can REFUSE to tag a lagging commit."""
    from ..packaging import check_release_consistency
    target = args.version or __version__
    res = check_release_consistency(target, root=args.root)
    print(res.render())
    return 0 if res.consistent else 1


def cmd_branch_protection_check(args: argparse.Namespace) -> int:
    """TM.S12a — FAIL-CLOSED: verify the public mirror's default branch is protected (no
    force-push, no deletion, required status checks). Exit 1 on any inability to prove it so
    `release.sh` REFUSES to release onto an unprotected `main`. No token hard-coded (gh supplies
    auth: login locally / GH_TOKEN in CI)."""
    from ..branch_protection import check_branch_protection
    verdict = check_branch_protection(repo=args.repo, branch=args.branch)
    print(verdict.render())
    return 0 if verdict.ok else 1


def cmd_route(args: argparse.Namespace) -> int:
    surface = _load_surface(args.path)
    try:
        targets = [args.need] if args.need else list(surface.manifest.capabilities)
        for need in targets:
            r = surface.router.resolve(need)
            chain = ", ".join(
                f"{t}{'+' if present else '-'}" for t, present in r.attempted
            )
            print(r.summary())
            print(f"    attempted: {chain}")
            print(f"    reason: {r.reason}")
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_detect(args: argparse.Namespace) -> int:
    detector = Detector()
    for tid in sorted(TOOL_CATALOG):
        present = detector.is_present(tid, TOOL_CATALOG[tid])
        mark = "present" if present else "absent "
        print(f"[{mark}] {tid}  ({TOOL_CATALOG[tid]['provides']})")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    surface = _load_surface(args.path)
    m = surface.manifest
    live = [r.summary() for r in surface.router.resolve_all()]
    from ..run_mode import read_mode
    print(f"mokata {m.mokata_version} · profile '{m.profile}' · mode: {read_mode(surface)}")
    for line in live:
        print(f"  {line}")
    # Stage 25 Part B — actionable code-graph hint (active queries, or how to wire one).
    from ..knowledge import graph_guidance
    print(graph_guidance(surface))
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    # 45b — OFFLINE by default: version + profile + install method + Python, zero egress.
    from ..version import check_for_update, version_info
    print(version_info(profile=_profile_for(args.path)).render())
    if args.check:
        # OPT-IN outbound check — netguard-accounted (logged) + degrade-clean offline.
        print(check_for_update(ledger=_ledger_for(args.path)).render())
    return 0


def cmd_upgrade(args: argparse.Namespace) -> int:
    # 45b — easy, HUMAN-GATED upgrade. `--check` just reports; never auto-runs an install.
    #
    # DOC-ONBOARD — and it FINISHES THE JOB. Installing the package was never the whole upgrade:
    # `.claude/settings.json` still carries the wiring the previous version wrote, so a gate
    # added since the user's last `mokata setup claude` is silently missing. After the gated pip
    # run, the tail re-wires (through `setup`'s own preview-diff gate — nothing silent) and then
    # verifies. Human-gated end to end: the pip run has its gate, the settings write has setup's.
    from ..version import (
        check_for_update,
        detect_install_method,
        finish_upgrade,
        run_pip_upgrade,
        upgrade_steps,
    )
    if args.check:
        print(check_for_update(ledger=_ledger_for(args.path)).render())
        return 0
    method = args.method if args.method != "auto" else detect_install_method()
    print(f"mokata {__version__} · install: {method}")
    if method == "plugin":
        # The CLI can't upgrade the plugin itself — print the steps to run in Claude Code.
        print("This is a plugin install — upgrade it from Claude Code:")
        for step in upgrade_steps("plugin"):
            print(f"  {step}")
        return 0
    steps = upgrade_steps(method)
    if method == "source":
        # A source checkout can't be upgraded FOR you (it's your working tree), so the tail
        # rides the printed recipe instead of the runner — the same two steps, same order.
        print("Source checkout — upgrade with:")
        for step in steps:
            print(f"  {step}")
        return 0
    # pip install — propose `pip install -U mokata`, HUMAN-GATED (never auto-runs).
    print(f"To upgrade: {steps[0]}")
    if not args.yes:
        if not read_yes_no(f"run `{steps[0]}` now?", "Run the upgrade?"):
            print("not run — run it yourself when ready (or re-run with --yes).")
            return 0
    run_pip_upgrade()
    print(f"ran: {steps[0]}")
    if getattr(args, "no_refresh", False):
        # Opting out of the tail must still leave the user holding the step they now owe —
        # an upgrade that stops here is exactly the half-done state this deliverable exists for.
        print("wiring refresh skipped (--no-refresh). Your harness wiring is still the "
              "previous version's — run `mokata setup claude`, then `mokata doctor --wiring`.")
        return 0
    finish_upgrade(root=args.path, scope=getattr(args, "scope", "project"),
                   assume_yes=args.yes)
    return 0


def register(sub, common):
    p_boot = sub.add_parser(
        "bootstrap", parents=[common], help="print the SessionStart briefing"
    )
    p_boot.add_argument(
        "--show-tokens",
        action="store_true",
        help="print the token estimate + budget check to stderr",
    )
    p_boot.set_defaults(func=cmd_bootstrap)

    p_val = sub.add_parser(
        "validate", parents=[common], help="validate the committed manifest"
    )
    p_val.set_defaults(func=cmd_validate)

    p_relchk = sub.add_parser(
        "release-check",
        help="verify all version fields == the intended tag (pure/offline; exit 1 on mismatch)",
    )
    p_relchk.add_argument(
        "version", nargs="?", default=None,
        help="the intended tag/version (e.g. 0.0.5 or v0.0.5); default: this package's version",
    )
    p_relchk.add_argument(
        "--root", default=".",
        help="the checkout to verify (e.g. the public mirror before tagging); default: cwd",
    )
    p_relchk.set_defaults(func=cmd_release_check)

    p_bpc = sub.add_parser(
        "branch-protection-check",
        help="verify the public mirror's default branch is protected "
             "(fail-closed; exit 1 if unprotected/unverifiable)",
    )
    p_bpc.add_argument(
        "--repo", default="JasGujral/mokata-oss",
        help="owner/repo to verify (default: the public mirror JasGujral/mokata-oss)",
    )
    p_bpc.add_argument(
        "--branch", default="main",
        help="the default branch to verify (default: main)",
    )
    p_bpc.set_defaults(func=cmd_branch_protection_check)

    p_route = sub.add_parser(
        "route", parents=[common], help="resolve a capability to its tool"
    )
    p_route.add_argument("need", nargs="?", help="capability name (default: all)")
    p_route.set_defaults(func=cmd_route)

    p_det = sub.add_parser(
        "detect", parents=[common], help="show tool presence for the catalog"
    )
    p_det.set_defaults(func=cmd_detect)

    p_stat = sub.add_parser(
        "status", parents=[common], help="one-line stack summary"
    )
    p_stat.set_defaults(func=cmd_status)

    p_ver = sub.add_parser(
        "version", parents=[common],
        help="show version + profile + install method + Python (offline by default)",
    )
    p_ver.add_argument("--check", action="store_true",
                       help="opt-in: check for a newer release (outbound; degrades clean "
                            "offline)")
    p_ver.set_defaults(func=cmd_version)

    p_up = sub.add_parser(
        "upgrade", parents=[common],
        help="upgrade mokata (human-gated pip install, or print the plugin-update steps)",
    )
    p_up.add_argument("--check", action="store_true",
                      help="opt-in: just check for a newer release, don't upgrade")
    p_up.add_argument("--method", choices=("auto", "pip", "plugin"), default="auto",
                      help="override install-method detection (default: auto)")
    p_up.add_argument("--yes", action="store_true",
                      help="approve the pip upgrade non-interactively (never auto-runs "
                           "without this or a confirm)")
    p_up.add_argument("--scope", choices=("project", "user"), default="project",
                      help="scope for the post-upgrade harness re-wiring (default: project)")
    p_up.add_argument("--no-refresh", action="store_true",
                      help="don't refresh the harness wiring after upgrading (you must then "
                           "run `mokata setup claude` yourself, or the new gates stay unwired)")
    p_up.set_defaults(func=cmd_upgrade)


__all__ = [
    "cmd_bootstrap",
    "cmd_validate",
    "cmd_release_check",
    "cmd_branch_protection_check",
    "cmd_route",
    "cmd_detect",
    "cmd_status",
    "cmd_version",
    "cmd_upgrade",
]
