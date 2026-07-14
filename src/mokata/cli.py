"""mokata CLI — the spine's command surface.

Stage 1 commands (the conductor everything else plugs into):
  init       A7  scaffold a valid config; detect tools; pick a profile
  bootstrap  A4  print the SessionStart briefing (and its token count)
  validate   A1  parse + validate the committed manifest
  route      A2  resolve a capability to its tool (and fallback)
  detect     A3  show tool-presence for the whole catalog
  status         one-line summary of the current stack
  brainstorm D6  launch the Socratic pre-spec brainstorm (standalone, L1)
  query      B2  run a structural query (graph if present, else grep floor)
  memory     C   surface memory + healing proposals (read-only, human-gated writes)
  skills     L4  list the skill/command catalog (name for detail)
  run        L1  run a skill/command standalone (no pipeline prerequisite)
  enter      L2  enter the pipeline at a phase (only that phase's gates apply)
  rules      G1  show the 4-tier rules and their line budgets
  audit      I3  show the append-only audit ledger
  budget     F5  show token savings (live budget + statusline)
  index      B4  build/refresh the freshness index; report stale files
  lat-check  B5  scan @lat anchors and flag concept drift
  coverage   A6  capability coverage + unmet gaps + overlaps
  mcp        H4  discover MCP servers and map them to roles
  doctor     K5  diagnose the manifest/config
  baseline       report the test suite green/red at baseline (degrade-clean)
  config     K1  get/set backend config in the manifest (set is human-gated)
  reset      K6  remove mokata state (uninstall / reset)
  suggest    L6  suggest a relevant command (never runs it)
  chain      L5  plan a manual chain of skills (gates still apply)
  export     J3  export the current manifest as a shareable stack
  import     J3  validate + apply a shared stack manifest (human-gated)
  harness    J2  show the harness boundary's capabilities
  exec       E8  show/select the execution mode (sequential default / parallel)
  playbook       run the full v1 story end-to-end (integration check)
  preview    E7  dry-run: planned phases + gates + file touches (no side effects)
  progress       run-progress tracker (done/current/pending); read-only over run-state

Later stages add more subcommands; this keeps the spine usable from the shell today.
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from . import __version__
from .config import Surface

# ---------------------------------------------------------------------------
# Compat shim (Stage 3d.2): cli.py is now a thin parse+dispatch shell. Every
# `cmd_*`, the private helpers, and the shared `_common` helpers are re-exported
# here so existing `from mokata.cli import X` / `from .cli import X` imports keep
# working unchanged.
# ---------------------------------------------------------------------------
from .cli_commands import (
    setup, core, knowledge, memory, collab, mode, sync, skills, rules, index, mcp, diagnostics,
    distribution, reset, pipeline, runviews, plan, menu, docs, docsync, gate, approve, spec,
)
from .cli_commands._common import (
    _load_surface, _review_scope, _backend_projects, _SCOPE_CURRENT, _cli_ask, _profile_for,
    _ledger_for,
)
from .cli_commands.setup import (
    cmd_init, cmd_tour, cmd_reconfigure, cmd_setup, cmd_unsetup,
)
from .cli_commands.core import (
    cmd_bootstrap, cmd_validate, cmd_release_check, cmd_route, cmd_detect, cmd_status,
    cmd_version, cmd_upgrade,
)
from .cli_commands.knowledge import (
    cmd_brainstorm, cmd_onboard, cmd_query, _split_csv, cmd_spec_check, cmd_ci_check,
    _git_changed_files,
)
from .cli_commands.memory import (
    cmd_memory, _memory_edit,
)
from .cli_commands.collab import (
    cmd_vault, cmd_session,
)
from .cli_commands.skills import (
    _search_skills, cmd_skills, cmd_skill, _skill_author, cmd_run, cmd_enter,
)
from .cli_commands.rules import (
    cmd_rules, cmd_budget, cmd_bench, cmd_audit, _audit_surface_or_none, _cmd_audit_team,
    _cmd_audit_share,
)
from .cli_commands.index import (
    cmd_index, cmd_coverage, cmd_lat_check,
)
from .cli_commands.mcp import (
    cmd_mcp, _cmd_mcp_discover, _cmd_mcp_start, _cmd_mcp_status, _cmd_mcp_install,
)
from .cli_commands.diagnostics import (
    cmd_doctor, cmd_baseline, cmd_config,
)
from .cli_commands.distribution import (
    cmd_export, cmd_import, cmd_stacks, cmd_team, cmd_harness,
)
from .cli_commands.reset import (
    cmd_reset,
)
from .cli_commands.pipeline import (
    cmd_exec, cmd_decompose, cmd_preview, cmd_playbook, cmd_suggest, cmd_chain,
)
from .cli_commands.runviews import (
    cmd_progress, cmd_sessions, cmd_windows, cmd_resume, cmd_watch, cmd_govern,
)
from .cli_commands.plan import (
    cmd_plan, _cmd_plan_list, _cmd_plan_show, _cmd_plan_export,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mokata",
        description="mokata — spec-driven TDD framework for Claude Code (spine).",
    )
    parser.add_argument("--version", action="version", version=f"mokata {__version__}")

    # Shared --path option so it works both before and after the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--path",
        default=".",
        help="repo root to operate on (default: current directory)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # Each command group registers its own parsers (same names/args/help/defaults as
    # before). Call order == the original add_parser order, so `--help` is byte-identical.
    setup.register(sub, common)
    core.register(sub, common)
    knowledge.register(sub, common)
    memory.register(sub, common)
    collab.register(sub, common)
    mode.register(sub, common)
    sync.register(sub, common)
    skills.register(sub, common)
    rules.register(sub, common)
    index.register(sub, common)
    mcp.register(sub, common)
    diagnostics.register(sub, common)
    distribution.register(sub, common)
    reset.register(sub, common)
    pipeline.register(sub, common)
    runviews.register(sub, common)
    plan.register(sub, common)
    menu.register(sub, common)
    docs.register(sub, common)
    docsync.register(sub, common)
    gate.register(sub, common)
    approve.register(sub, common)
    spec.register(sub, common)

    return parser


def _first_command_token(argv: List[str]) -> Optional[str]:
    """The first positional token (the subcommand). Returns None for help/version or when the
    leading tokens are global options, so argparse handles those itself."""
    for tok in argv:
        if tok in ("-h", "--help", "--version"):
            return None
        if tok.startswith("-"):
            continue
        return tok
    return None


def _subcommand_set(parser: argparse.ArgumentParser) -> set:
    for action in parser._actions:
        if action.__class__.__name__ == "_SubParsersAction":
            return set(action.choices)
    return set()


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    raw = list(sys.argv[1:] if argv is None else argv)
    # Stage 56 — a friendly "did you mean …" + next step on an unknown subcommand, BEFORE
    # argparse exits with its terse error.
    token = _first_command_token(raw)
    known = _subcommand_set(parser)
    if token is not None and token not in known:
        from . import onboarding
        try:
            pi = raw.index("--path")
            root = raw[pi + 1]
        except (ValueError, IndexError):
            root = "."
        print(onboarding.unknown_command_message(
            token, known=known, initialized=Surface.is_initialized(root)), file=sys.stderr)
        return 2
    args = parser.parse_args(raw)
    # argparse stores --path before the subcommand; subcommands read args.path.
    if not hasattr(args, "path"):
        args.path = "."
    return args.func(args)


__all__ = [
    "main", "build_parser", "_load_surface", "_review_scope", "_backend_projects",
    "_SCOPE_CURRENT", "_cli_ask", "_profile_for", "_ledger_for", "cmd_init", "cmd_tour",
    "cmd_reconfigure", "cmd_setup", "cmd_unsetup", "cmd_bootstrap", "cmd_validate",
    "cmd_release_check", "cmd_route", "cmd_detect", "cmd_status", "cmd_version",
    "cmd_upgrade", "cmd_brainstorm", "cmd_onboard", "cmd_query", "_split_csv",
    "cmd_spec_check", "cmd_ci_check", "_git_changed_files", "cmd_memory", "_memory_edit",
    "cmd_vault", "cmd_session", "_search_skills", "cmd_skills", "cmd_skill",
    "_skill_author", "cmd_run", "cmd_enter", "cmd_rules", "cmd_budget", "cmd_bench",
    "cmd_audit", "_audit_surface_or_none", "_cmd_audit_team", "_cmd_audit_share",
    "cmd_index", "cmd_coverage", "cmd_lat_check", "cmd_mcp", "_cmd_mcp_discover",
    "_cmd_mcp_start", "_cmd_mcp_status", "_cmd_mcp_install", "cmd_doctor", "cmd_baseline",
    "cmd_config", "cmd_export", "cmd_import", "cmd_stacks", "cmd_team", "cmd_harness",
    "cmd_reset", "cmd_exec", "cmd_decompose", "cmd_preview", "cmd_playbook", "cmd_suggest",
    "cmd_chain", "cmd_progress", "cmd_sessions", "cmd_resume", "cmd_watch", "cmd_govern",
    "cmd_plan", "_cmd_plan_list", "_cmd_plan_show", "_cmd_plan_export",
]


if __name__ == "__main__":
    raise SystemExit(main())
