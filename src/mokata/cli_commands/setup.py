"""init / tour / reconfigure / setup / unsetup — first-run + lifecycle wiring."""
from __future__ import annotations

import argparse
import sys

from ..adoption_modes import (
    mode_names,
    offer_mode_extras,
    profile_for_mode,
    render_quickstart,
)
from ._common import (
    Surface,
    init_repo,
    plan_init,
    render_plan,
    DEFAULT_PROFILE,
    profile_names,
    HARNESSES,
    SCOPES,
    SetupError,
    setup_harness,
    unsetup_harness,
)


def cmd_init(args: argparse.Namespace) -> int:
    # G1 — graduated adoption. `--mode` is an ALIAS onto an existing profile plus an onboarding
    # flavour; the manifest it writes is byte-identical to the same init via `--profile`.
    # `--profile` and `--mode` are mutually exclusive at the parser (two names for one axis).
    mode = getattr(args, "mode", None)
    profile = profile_for_mode(mode) if mode else args.profile

    if getattr(args, "preview", False):
        # Dry-run for the human gate (Stage 23): print the plan, write nothing, exit 0.
        # Used by /mokata:init to preview before the user approves the real write.
        print(render_plan(plan_init(args.path, profile)))
        return 0

    if mode:
        return _init_with_mode(args, mode, profile)

    # Stage 56 — the magical first-run: when run INTERACTIVELY on a fresh repo (or with an
    # explicit --wizard / --setup-harness), use the guided Q&A wizard. The non-interactive
    # --yes/--profile path is preserved verbatim for CI/scripts.
    explicit_profile = (args.profile != DEFAULT_PROFILE)
    interactive = sys.stdin.isatty() and not args.yes and not args.force
    use_wizard = getattr(args, "wizard", False) or (
        interactive and not explicit_profile and not Surface.is_initialized(args.path))
    if use_wizard:
        from .. import onboarding
        res = onboarding.run_wizard(
            root=args.path, force=args.force,
            wire_harness=(True if getattr(args, "setup_harness", False) else None))
        return 1 if res.aborted else 0
    result = init_repo(
        root=args.path,
        profile=args.profile,
        assume_yes=args.yes,
        force=args.force,
    )
    if result.aborted:
        print(f"\n{result.message}", file=sys.stderr)
        return 1
    return 0


def _init_with_mode(args: argparse.Namespace, mode: str, profile: str) -> int:
    """G1 — the mode-flavoured init: the SAME `init_repo` write, then the mode's consented
    offers, then its printed quickstart.

    `--mode` never routes through the wizard: the mode IS the answer the wizard would ask for,
    and re-asking it would be a second config axis wearing a prompt. The durable write stays
    human-gated by `init_repo` (P2); the offers are interactive-only, so a `--yes`/CI init in
    ANY mode reaches neither the ask nor `pip` (the DB.S4 posture)."""
    result = init_repo(
        root=args.path,
        profile=profile,
        assume_yes=args.yes,
        force=args.force,
    )
    if result.aborted:
        print(f"\n{result.message}", file=sys.stderr)
        return 1

    interactive = sys.stdin.isatty() and not args.yes
    offer_mode_extras(args.path, mode, interactive=interactive)
    print(render_quickstart(mode))
    return 0


def cmd_tour(args: argparse.Namespace) -> int:
    # Stage 56 — a short, self-contained, READ-ONLY demo (graph query, memory recall, gate
    # catch). Writes nothing to the repo; the memory recall runs in an in-memory store.
    from .. import onboarding
    print(onboarding.build_tour(ascii_only=getattr(args, "ascii", False)))
    return 0


def cmd_reconfigure(args: argparse.Namespace) -> int:
    # Stage 56b — the re-runnable reconfigure wizard: change what's wired on an already-
    # initialized repo (add/remove an integration, switch a backend, change profile), gated +
    # idempotent + reversible. Interactive by default; explicit flags / --yes for scripts.
    from .. import onboarding
    config_edits = {}
    for pair in (args.set or []):
        if "=" not in pair:
            print(f"error: --set expects KEY=VALUE (got '{pair}')", file=sys.stderr)
            return 2
        key, val = pair.split("=", 1)
        config_edits[key] = val
    wire_harness = True if args.wire_harness else (False if args.unwire_harness else None)
    res = onboarding.run_reconfigure(
        root=args.path, profile=args.profile, add=(args.add or None),
        remove=(args.remove or None), config_edits=(config_edits or None),
        wire_harness=wire_harness, scope=args.scope, assume_yes=args.yes)
    if not res.initialized:
        return 1
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    try:
        result = setup_harness(
            harness=args.harness,
            root=args.path,
            scope=args.scope,
            profile=args.profile,
            with_hooks=not args.no_hooks,
            grant=not args.no_grant,
            assume_yes=args.yes,
            force=args.force,
        )
    except SetupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if result.aborted:
        print(f"\n{result.message}", file=sys.stderr)
        return 1
    return 0


def cmd_unsetup(args: argparse.Namespace) -> int:
    try:
        result = unsetup_harness(
            harness=args.harness,
            root=args.path,
            scope=args.scope,
            assume_yes=args.yes,
        )
    except SetupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if result.aborted:
        print(f"\n{result.message}", file=sys.stderr)
        return 1
    return 0


def register(sub, common):
    p_init = sub.add_parser(
        "init", parents=[common],
        help="scaffold config; detect tools; pick profile",
    )
    # G1 — `--profile` and `--mode` are two names for ONE axis (a mode resolves to a profile),
    # so they are mutually exclusive: passing both is a contradiction, and silently letting one
    # win would write a manifest the user did not ask for. argparse reports it as a usage error.
    p_init_axis = p_init.add_mutually_exclusive_group()
    p_init_axis.add_argument(
        "--profile",
        default=DEFAULT_PROFILE,
        choices=profile_names(),
        help=f"starting profile (default: {DEFAULT_PROFILE})",
    )
    p_init_axis.add_argument(
        "--mode",
        default=None,
        choices=mode_names(),
        help="graduated adoption on-ramp: seatbelt (the gates) / memory (gates + persistent "
             "memory) / full (everything). An alias for a profile plus a printed quickstart; "
             "memory and full additionally OFFER the local embeddings model when interactive",
    )
    p_init.add_argument(
        "--yes", action="store_true", help="non-interactive; skip the write prompt"
    )
    p_init.add_argument(
        "--force", action="store_true", help="overwrite an existing manifest"
    )
    p_init.add_argument(
        "--preview", action="store_true",
        help="print the plan and exit without writing (dry-run for the human gate)"
    )
    p_init.add_argument(
        "--wizard", action="store_true",
        help="force the guided interactive first-run wizard (detect → ask → wire, gated)"
    )
    p_init.add_argument(
        "--setup-harness", action="store_true",
        help="in the wizard, also wire mokata into the harness (commands + MCP + hooks)"
    )
    p_init.set_defaults(func=cmd_init)

    p_tour = sub.add_parser(
        "tour", parents=[common],
        help="a 60-second read-only demo (graph query, memory recall, gate catch)",
    )
    p_tour.add_argument("--ascii", action="store_true",
                        help="ASCII-only glyphs (no unicode arrows/checks)")
    p_tour.set_defaults(func=cmd_tour)

    p_recfg = sub.add_parser(
        "reconfigure", parents=[common],
        help="re-runnable wizard: change what's wired later (add/remove integration, switch "
             "backend, change profile) — gated, idempotent, reversible",
    )
    p_recfg.add_argument("--profile", choices=profile_names(), default=None,
                         help="switch the profile (default: keep the current one)")
    p_recfg.add_argument("--add", action="append", metavar="TOOL",
                         help="wire a detected integration (repeatable; absent → recommended)")
    p_recfg.add_argument("--remove", action="append", metavar="TOOL",
                         help="cleanly unwire an integration (repeatable; no residue)")
    p_recfg.add_argument("--set", action="append", metavar="KEY=VALUE",
                         help="switch a backend setting in the manifest (repeatable; gated)")
    p_recfg.add_argument("--wire-harness", action="store_true",
                         help="wire mokata into the harness (commands + MCP + hooks)")
    p_recfg.add_argument("--unwire-harness", action="store_true",
                         help="remove the harness wiring (reversible; no residue)")
    p_recfg.add_argument("--scope", choices=SCOPES, default="project",
                         help="harness scope for --wire-harness/--unwire-harness")
    p_recfg.add_argument("--yes", action="store_true",
                         help="non-interactive; apply the explicit changes without prompting")
    p_recfg.set_defaults(func=cmd_reconfigure)

    p_setup = sub.add_parser(
        "setup", parents=[common],
        help="one command: wire mokata into a harness without the plugin "
             "(commands + MCP + hooks)",
    )
    p_setup.add_argument("harness", choices=HARNESSES,
                         help="the harness to wire (currently: claude)")
    p_setup.add_argument("--scope", choices=SCOPES, default="project",
                         help="install into this project (default) or user-global (~/.claude)")
    p_setup.add_argument("--profile", default=DEFAULT_PROFILE, choices=profile_names(),
                         help=f"profile to init with if not already set up "
                              f"(default: {DEFAULT_PROFILE})")
    p_setup.add_argument("--no-hooks", action="store_true",
                         help="skip wiring mokata's hooks (SessionStart briefing + secret-guard "
                              "+ run-state gate-guard + dirty-track) — this also skips the MCP "
                              "tool grant, which shares settings.json")
    p_setup.add_argument("--no-grant", action="store_true",
                         help="don't grant Claude Code permission for mokata's MCP tools / "
                              "enable the server in settings.json (default: grant, so Claude "
                              "Code doesn't gate each mcp__mokata__* call)")
    p_setup.add_argument("--yes", action="store_true",
                         help="non-interactive; skip the confirmation prompt")
    p_setup.add_argument("--force", action="store_true",
                         help="re-init even if a manifest already exists")
    p_setup.set_defaults(func=cmd_setup)

    p_unsetup = sub.add_parser(
        "unsetup", parents=[common],
        help="reverse `mokata setup`: remove wired commands, MCP entry, and hooks",
    )
    p_unsetup.add_argument("harness", choices=HARNESSES,
                           help="the harness to unwire (currently: claude)")
    p_unsetup.add_argument("--scope", choices=SCOPES, default="project",
                           help="which scope to remove from (default: project)")
    p_unsetup.add_argument("--yes", action="store_true",
                           help="non-interactive; skip the confirmation prompt")
    p_unsetup.set_defaults(func=cmd_unsetup)


__all__ = [
    "cmd_init",
    "cmd_tour",
    "cmd_reconfigure",
    "cmd_setup",
    "cmd_unsetup",
]
