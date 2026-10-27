"""init / tour / reconfigure / setup / unsetup — first-run + lifecycle wiring."""
from __future__ import annotations

import argparse
import sys
from typing import Optional

from ..adoption_modes import (
    mode_names,
    profile_for_mode,
    render_outcome,
    render_quickstart,
    wire_mode,
)
from ._common import (
    Surface,
    init_repo,
    plan_init,
    plan_setup,
    render_plan,
    render_setup_plan,
    DEFAULT_PROFILE,
    profile_names,
    HARNESSES,
    SCOPES,
    SetupError,
    setup_harness,
    unsetup_harness,
)


# A2/F8 (0.0.19) — the default an init wires under. `mokata init` takes no `--harness`/`--scope`
# (there is one gate-bearing route and it is the project one), and this is the SAME default
# `onboarding.run_wizard` and `harness_setup.resolve_targets` already carry, so the wizard route
# and the scripted route wire the identical target rather than two spellings of it.
INIT_HARNESS = "claude"
INIT_SCOPE = "project"


# ======================================================================================
# F8a (0.0.19 stage 10a) — THE PREVIEW COVERS THE WRITE, AND ONE DECLARATION KEEPS IT THERE.
#
# `--preview` rendered the `.mokata/` manifest plan and returned BEFORE `_wire_or_disclose`,
# so a human approving `/mokata:init`'s preview approved three files and got the harness too:
# 37 commands, 26 Agent Skills, `settings.json`, `.mcp.json`. That is a P2 violation — every
# durable write is human-gated — and F8 introduced it in THIS release by teaching `--yes` to
# wire without teaching the preview to say so.
#
# The patch is one branch. The GUARD is these two functions: the preview and the executor ask
# the SAME pair of questions — *will this run wire?* and *with what?* — so the next flag added
# to the wiring reaches both surfaces in one edit or neither. Two spellings of "what an init
# does" is how this defect was born, and re-spelling it here would only reschedule it.
# ======================================================================================

def init_wires_harness(args: argparse.Namespace) -> bool:
    """Does THIS init wire the harness? Asked by the preview and by the executor.

    F8.1 — `--yes` is consent to the whole init plan, so `--yes` wires and nothing else on this
    route does: the wizard's own offer (F8.2) is a different consent path and is not this run.
    A preview that answered this question its own way would be free to describe a run that never
    happens, in either direction — the omission this stage closes, or a promise of wiring a
    non-`--yes` init would never perform."""
    return bool(getattr(args, "yes", False))


def init_wiring_kwargs(args: argparse.Namespace, profile: str) -> dict:
    """The ONE argument set an init wires the harness with.

    `_wire_or_disclose` hands it to `setup_harness` (which applies it); the preview hands it to
    `plan_setup` (which renders it). They are the same keywords by construction, so a target,
    scope or profile that moves moves on both surfaces at once. Apply-only arguments —
    `assume_yes`, `force` — are NOT here: they say how the write proceeds, not what it is, and
    `plan_setup` does not accept them."""
    return {"harness": INIT_HARNESS, "root": args.path, "scope": INIT_SCOPE, "profile": profile}


def render_init_preview(args: argparse.Namespace, profile: str) -> str:
    """The whole dry-run: the `.mokata/` plan, and then what the harness wiring would write.

    THREE CASES, THREE FACTS — the preview describes the run it was handed, never a generic one:

      * `--preview` alone previews an init that wires nothing here, and SAYS so. Showing the
        harness plan would be the same lie pointing the other way.
      * `--preview --yes` previews the consented run, so it renders the harness plan too — the
        `--yes` half that `/mokata:init` actually executes.
      * `--preview --mode <m>` previews the mode's resolved profile (a mode IS a profile plus a
        quickstart), and wires on exactly the same `--yes` condition.

    Degrade-clean: a harness plan that cannot be built (no command templates — the pip-without-
    clone case) is REPORTED as unplannable, never dropped. An absent section reads as "nothing
    happens here", which is the failure this function exists to prevent."""
    parts = [render_plan(plan_init(args.path, profile))]

    if not init_wires_harness(args):
        parts.append(
            "Harness wiring: NOT part of this run.\n"
            f"  Without `--yes` this init writes the files above and nothing else — no "
            f"{INIT_HARNESS} commands,\n"
            "  no Agent Skills, no MCP registration, no hooks. The interactive first-run wizard\n"
            "  asks separately, and `mokata setup claude` wires it later.\n"
            "  Add `--yes` to this preview to see exactly what that wiring would write.")
        return "\n\n".join(parts)

    parts.append(
        "`--yes` is consent to the whole init plan, and the harness wiring is part of it.\n"
        "This is that half of the write — the same plan the wiring itself renders:")
    try:
        parts.append(render_setup_plan(plan_setup(**init_wiring_kwargs(args, profile))))
    except (SetupError, ValueError) as exc:
        parts.append(
            f"Harness wiring: mokata could NOT plan it here ({exc}).\n"
            "  `--yes` would attempt the wiring and degrade cleanly if it fails, so this preview\n"
            "  cannot show you that half. Do not read this as 'nothing would be written'.")
    return "\n\n".join(parts)


def cmd_init(args: argparse.Namespace) -> int:
    # G1 — graduated adoption. `--mode` is an ALIAS onto an existing profile plus an onboarding
    # flavour; the manifest it writes is byte-identical to the same init via `--profile`.
    # `--profile` and `--mode` are mutually exclusive at the parser (two names for one axis).
    mode = getattr(args, "mode", None)
    profile = profile_for_mode(mode) if mode else args.profile

    if getattr(args, "preview", False):
        # Dry-run for the human gate (Stage 23): print the plan, write nothing, exit 0.
        # Used by /mokata:init to preview before the user approves the real write.
        print(render_init_preview(args, profile))
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
    _wire_or_disclose(args, args.profile)
    return 0


def _wire_or_disclose(args: argparse.Namespace, profile: str) -> None:
    """A2/F8 — an init either WIRES the harness or SAYS the gate is not enforcing. Never neither.

    #28's harm is the silence, not the default: `init_repo` writes `.mokata/` and the run-state
    gates run as HARNESS HOOKS, so every scripted init produced a repo that records runs, polices
    nothing, and said nothing about it. Two halves, one principle — *a user can always tell
    whether the seatbelt is on*:

      * **F8.1 — `--yes` wires.** `--yes` is consent to the whole init plan, and wiring the gate
        is part of that plan, so it is consent ALREADY GIVEN, never consent assumed (P2). Without
        `--yes` nothing is wired here: the interactive wizard's own offer is the consent path and
        it stays exactly as it was (F8.2).
      * **F8.3 — everything else DISCLOSES.** The state is MEASURED, not inferred from what this
        function just did: a plugin-route repo is already enforcing with no `.claude/settings.json`
        anywhere, and telling that user the gate is off would be the disclosure lying in the other
        direction. Three states, never two (doc 85 §7g) — `hook_wiring` carries `unverifiable`
        separately and this prints it as its own sentence.

    Degrade-clean: a `SetupError` from the wiring never fails an init that has already succeeded.
    The manifest is on disk either way; the user simply falls through to the disclosure, which is
    then TRUE — nothing wired the gate."""
    from ..hook_wiring import gate_enforcement_state, render_gate_enforcement

    if init_wires_harness(args):
        try:
            # F8a — the SAME keywords the preview rendered (`init_wiring_kwargs`), plus the two
            # apply-only ones. Spelling the targets out again here is what let `--preview` and
            # `--yes` describe different runs.
            sres = setup_harness(
                **init_wiring_kwargs(args, profile),
                assume_yes=True, force=args.force,
            )
            if not sres.aborted:
                print(f"\n✓ wired the run-state gate into {INIT_HARNESS} ({INIT_SCOPE} scope) — "
                      f"`--yes` is consent to the whole init plan, and the gate is part of it.")
        except SetupError as exc:
            # The init SUCCEEDED; only the wiring failed. Say which, then fall through to the
            # disclosure below — which now measures the truth rather than repeating the attempt.
            print(f"\nnote: the harness wiring was skipped ({exc}).", file=sys.stderr)

    line = render_gate_enforcement(gate_enforcement_state(args.path))
    if line:
        print("\n" + line)


def _init_with_mode(args: argparse.Namespace, mode: str, profile: str) -> int:
    """G1 — the mode-flavoured init: the SAME `init_repo` write, then the mode's detect-and-wire
    step, then its printed quickstart.

    `--mode` never routes through the wizard: the mode IS the answer the wizard would ask for,
    and re-asking it would be a second config axis wearing a prompt. The durable write stays
    human-gated by `init_repo` (P2); the dependency step installs nothing in ANY mode (0.0.26 L1:
    the installer fetches `mokata[full]`, setup only detects and wires)."""
    result = init_repo(
        root=args.path,
        profile=profile,
        assume_yes=args.yes,
        force=args.force,
    )
    if result.aborted:
        print(f"\n{result.message}", file=sys.stderr)
        return 1

    outcome = _mode_dependencies(args, mode)
    # A2/F8 — `--mode` NEVER routes through the wizard (see this function's docstring), so it is
    # one of the paths that reached `init_repo` and wired nothing. It owes the same two halves as
    # the plain route, and it owes them BEFORE the quickstart: a quickstart that tells you to run
    # the pipeline, printed under a repo whose gate is off, is the seatbelt claim #28 is about.
    _wire_or_disclose(args, profile)
    print(render_quickstart(mode, outcome))
    return 0


def _mode_dependencies(args: argparse.Namespace, mode: str):
    """The mode's optional dependencies: detect, wire, warm — then the outcome table. Shared by
    `init --mode` and `setup <harness> --mode` so the two routes cannot drift.

    0.0.26 L1 — NEVER installs: the extras come from the install line (`uv tool install
    "mokata[full]"`), and a missing one is reported with that ONE line for this installer. The
    `--with-deps` flag that consented to a self-install is gone with the self-install."""
    rerun = ""
    if getattr(args, "plugin", False):
        from ..adoption_modes import plugin_setup_command
        rerun = plugin_setup_command(mode)
    outcome = wire_mode(args.path, mode, rerun=rerun)
    table = render_outcome(outcome)
    if table:
        print(table)
    return outcome


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
    # 0.0.25 I-02 (`SETUP-MODE-FLAG`): `mokata setup claude --mode full` is the ONE documented
    # instruction (Jas 2026-10-16). A mode is a profile alias plus its dependencies + quickstart,
    # exactly as on `init --mode`; the two routes share `_mode_dependencies`.
    mode = getattr(args, "mode", None)
    profile = profile_for_mode(mode) if mode else args.profile
    if getattr(args, "plugin", False):
        return _setup_plugin_route(args, mode, profile)
    try:
        result = setup_harness(
            harness=args.harness,
            root=args.path,
            scope=args.scope,
            profile=profile,
            with_hooks=not args.no_hooks,
            grant=not args.no_grant,
            assume_yes=args.yes,
            force=args.force,
            ide_bridge=getattr(args, "ide_bridge", False),
            http_gates=getattr(args, "http_gates", False),
            skills_only=getattr(args, "skills_only", False),
        )
    except SetupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if result.aborted:
        print(f"\n{result.message}", file=sys.stderr)
        return 1
    if mode:
        outcome = _mode_dependencies(args, mode)
        print(render_quickstart(mode, outcome))
    return 0


def _setup_plugin_route(args: argparse.Namespace, mode: Optional[str], profile: str) -> int:
    """0.0.26 L8 (MP.S1) — `setup claude --mode <m> --plugin`: the plugin route's end state.

    The Claude Code plugin IS the harness wiring (commands, skills, hooks, MCP server), so this
    writes none of it — a second, project-scope copy would pin hooks to one uv cache path and
    outlive the next plugin update. It only (1) initializes the repo if it has no `.mokata/`
    (human-gated by `init_repo`, P2) and (2) runs the mode's detect-and-wire step — embedder on +
    model warm-up, code-review-graph pinned — in the environment it runs in, which on this route
    is the plugin's pinned `mokata[full]` (`uvx --from "mokata[full]==<v>"`)."""
    if not mode:
        print("error: --plugin needs --mode (seatbelt / standard / memory / full)", file=sys.stderr)
        return 2
    from ..config import Surface
    if not Surface.is_initialized(args.path):
        result = init_repo(root=args.path, profile=profile, assume_yes=args.yes, force=args.force)
        if result.aborted:
            print(f"\n{result.message}", file=sys.stderr)
            return 1
    if not _plugin_worktree_settings(args):
        return 1
    outcome = _mode_dependencies(args, mode)
    print(render_quickstart(mode, outcome))
    return 0


def _plugin_worktree_settings(args: argparse.Namespace) -> bool:
    """0.0.26 P31 — the plugin carries hooks, not project settings, so the plugin route still sets
    Claude Code's session-worktree settings (`worktree.baseRef`, `.worktreeinclude`, .gitignore)
    itself: shown first, written only on the human's yes (P2). False = the human declined."""
    from pathlib import Path
    from ..harness_setup import _default_confirm
    from ..worktree_setup import STATE_WRITE, apply_rows, plan_rows, render_rows
    settings = Path(args.path).resolve() / ".claude" / "settings.json"
    rows = plan_rows(args.path, settings)
    for line in render_rows(rows):
        print(line)
    if not any(r.state == STATE_WRITE for r in rows):
        return True
    if not args.yes and not _default_confirm("\nSet these session-worktree settings? [y/N] "):
        print("session worktree settings NOT written (declined).", file=sys.stderr)
        return False
    touched = apply_rows(args.path, settings)
    for p in touched:
        print(f"  wrote {p}")
    from ..harness_setup import _record_setup_ledger
    _record_setup_ledger(args.path, "setup-worktrees", touched, harness="claude", scope="project")
    return True


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
             "memory and full additionally detect and wire what `mokata[embeddings]` / "
             "`mokata[full]` installed (setup never installs)",
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
    p_setup_axis = p_setup.add_mutually_exclusive_group()
    p_setup_axis.add_argument("--profile", default=DEFAULT_PROFILE, choices=profile_names(),
                              help=f"profile to init with if not already set up "
                                   f"(default: {DEFAULT_PROFILE})")
    p_setup_axis.add_argument("--mode", default=None, choices=mode_names(),
                              help="seatbelt / memory / full: a profile, plus detecting and "
                                   "wiring what `mokata[full]` installed (embedder, code graph, "
                                   "model warm-up), and a printed quickstart. Never installs: a "
                                   "missing package is named with ONE install line")
    p_setup_axis.add_argument("--skills-only", dest="skills_only", action="store_true",
                              help="refresh ONLY the vendored commands + Agent Skills from this "
                                   "mokata (after an upgrade; `mokata update` says when). No init, "
                                   "MCP, hooks or settings.json; installs no dependency")
    p_setup.add_argument("--no-hooks", action="store_true",
                         help="skip wiring mokata's hooks (SessionStart briefing + "
                              "user-prompt-submit per-turn recall + secret-guard + run-state "
                              "gate-guard + dirty-track + run-bind + pre-compact + subagent-start + "
                              "subagent-stop + stop + stop-gate + session-end) — this also skips the MCP tool grant, "
                              "which shares settings.json")
    p_setup.add_argument("--plugin", action="store_true",
                         help="the Claude Code plugin route: the plugin already wires commands, "
                              "skills, hooks and the MCP server, so only init the repo (if "
                              "needed) and detect + wire the --mode (embedder, code graph, model "
                              "warm-up). Run it through the plugin's pinned environment: "
                              "uvx --python \">=3.12\" --from \"mokata[full]==<plugin version>\" mokata setup claude "
                              "--mode full --plugin")
    p_setup.add_argument("--no-grant", action="store_true",
                         help="don't grant Claude Code permission for mokata's MCP tools / "
                              "enable the server in settings.json (default: grant, so Claude "
                              "Code doesn't gate each mcp__mokata__* call)")
    p_setup.add_argument("--ide-bridge", dest="ide_bridge", action="store_true",
                         help="claude, project scope: also wire observe-only http hooks that post "
                              "edits / turn ends to the running `mokata ide` (live follow mode); "
                              "written to .claude/settings.local.json")
    p_setup.add_argument("--http-gates", dest="http_gates", action="store_true",
                         help="claude, project scope, OFF by default: ALSO wire FAIL-CLOSED http "
                              "PreToolUse gate hooks (onFailure: block) to the running `mokata ide` "
                              "— while it is not running, every gated tool call is blocked; the "
                              "mokata-hook command gates stay wired")
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
    "init_wires_harness",
    "init_wiring_kwargs",
    "render_init_preview",
    "cmd_tour",
    "cmd_reconfigure",
    "cmd_setup",
    "cmd_unsetup",
]
