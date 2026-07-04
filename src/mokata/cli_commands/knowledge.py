"""brainstorm / onboard / query / spec-check / ci-check — capture typed project knowledge, query the graph, and check changes against the specs."""
from __future__ import annotations

import argparse
import sys
from typing import Optional

from ._common import (
    ground,
    load_approved_approach,
    render_launch,
    ConfigError,
    Surface,
    AuditLedger,
    QUERY_KINDS,
    KnowledgeLayer,
    ManifestError,
    MemoryStore,
    get_skill,
    render_skill,
    _load_surface,
)


def cmd_brainstorm(args: argparse.Namespace) -> int:
    surface = _load_surface(args.path)
    if args.status:
        handoff = load_approved_approach(surface.state)
        if handoff is None:
            print("brainstorm: no approved approach persisted yet.")
        else:
            print(
                f"brainstorm: approved approach '{handoff.approach.name}' for topic "
                f"'{handoff.topic}' (by {handoff.approver} at {handoff.approved_at})."
            )
        return 0
    # Stage 50 — resume a saved IN-PROGRESS brainstorm if one exists (left mid-stream). The
    # HARD-GATE still holds: this only re-hydrates exploration; nothing is approved by resuming.
    from ..brainstorm import build_anchor_brief, restore_brainstorm_progress
    wip = restore_brainstorm_progress(surface.state)
    if wip is not None and not wip.approved:
        print(f"mokata brainstorm: resuming in-progress brainstorm for '{wip.topic}' — "
              f"{len(wip.answered_questions)} answered question(s), {len(wip.approaches)} "
              f"approach(es) on the table; NOT yet approved (the spec stays HARD-GATED).\n")
        # Stage 54g — re-surface the immutable anchor + compact synthesis so the resumed
        # brainstorm picks up grounded to the original ask, not just wherever it left off.
        sys.stdout.write(build_anchor_brief(wip))
        sys.stdout.write("\n")
        sys.stdout.write(wip.design_writeup())
        return 0
    # Standalone launch (L1): print the clean-room protocol + live grounding. No prior
    # pipeline phase is required to run this.
    grounding = ground(surface.router)
    sys.stdout.write(render_launch(grounding))
    return 0


def cmd_onboard(args: argparse.Namespace) -> int:
    # Stage 36 — guided, LLM-driven capture of typed project knowledge (like brainstorm). Prints
    # the clean-room protocol + live grounding; persistence happens through the gated writes the
    # protocol drives. Runs standalone, no prior phase required; degrades cleanly uninitialized.
    skill = get_skill("onboard")
    surface = None
    if Surface.is_initialized(args.path):
        try:
            surface = Surface.load(args.path)
        except (ConfigError, ManifestError):
            surface = None
    grounding = ground(surface.router) if surface is not None else ground(None)
    sys.stdout.write(render_skill(skill, grounding))

    # Stage 59 — surface AUTO-PROPOSED guardrails: rule PROPOSALS distilled from recurring
    # ledger corrections (declined writes / reverts / spec conflicts, G5). PROPOSAL-ONLY —
    # the user approves each through the gated capture; mokata never auto-adds a rule. Quiet
    # + bounded when there are none (degrade-clean).
    if surface is not None:
        from ..govern import learn_from_ledger
        proposals = learn_from_ledger(AuditLedger.from_mokata_dir(surface.mokata_dir))
        if proposals:
            print("\nProposed guardrails (recurring corrections mokata noticed — "
                  "human-gated, NOT auto-added; approve/edit/reject each):")
            for p in proposals:
                print(f"  - {p.proposed_rule} [{p.rationale}]")
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    surface = _load_surface(args.path)
    layer = KnowledgeLayer.from_surface(surface)
    result = layer._run(args.kind, args.target, depth=args.depth)
    mode = "graph" if not result.degraded else "grep fallback"
    print(
        f"{result.kind}({result.target}) via {result.backend} [{mode}] — "
        f"{result.count} result(s)"
    )
    for ref in result.references:
        sym = f"  «{ref.symbol}»" if ref.symbol else ""
        print(f"  {ref.path}:{ref.line}{sym}  {ref.snippet}")
    if result.note:
        print(f"  ({result.note})")
    return 0


def _split_csv(value: Optional[str]) -> list:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def cmd_spec_check(args: argparse.Namespace) -> int:
    # Stage 37 — spec-awareness / regression guard: cross-check a change's touch-set against the
    # saved specs + decision memory; surface a conflict and route it through the deviation gate
    # (human-gated, logged). Degrade-clean: no corpus -> no-op; no graph -> lexical/file overlap.
    from ..engine import ChangeSet, guard_change, load_decisions, load_spec_corpus
    from ..govern import AuditLedger
    surface = _load_surface(args.path)
    change = ChangeSet(symbols=_split_csv(args.symbols), files=_split_csv(args.files),
                       text=args.text or "")
    specs = load_spec_corpus(surface.state)
    store = MemoryStore.from_surface(surface)
    decisions = load_decisions(store)
    layer = KnowledgeLayer.from_surface(surface)
    ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
    outcome = guard_change(change, specs=specs, decisions=decisions, layer=layer,
                           ledger=ledger, phase=args.phase, assume_yes=args.yes)
    print(outcome.render())
    return 0 if outcome.proceeded else 1


def cmd_ci_check(args: argparse.Namespace) -> int:
    # Stage 58 — mokata as a CI / PR check: run the completeness gate + spec-awareness over a PR's
    # changed files. READ-ONLY (surfaces blocks; never posts to GitHub). DEGRADE-CLEAN: nothing to
    # check → PASS (never false-block). Reuses the existing engines (no logic duplicated).
    from .. import ci_check as CI
    files = _split_csv(args.files)
    if args.base and not files:
        files = _git_changed_files(args.path, args.base)
    symbols = _split_csv(args.symbols) if args.symbols else None
    result = CI.run_ci_check(args.path, files, changed_symbols=symbols)
    print(result.render(ascii_only=args.ascii))
    if args.comment_file:
        try:
            with open(args.comment_file, "w", encoding="utf-8") as fh:
                fh.write(result.comment_body() + "\n")
        except OSError as exc:
            print(f"warning: could not write comment file: {exc}", file=sys.stderr)
    # `--no-fail` makes it report-only (the workflow still posts the comment but the job stays
    # green); the default fails the check on a real block so it gates the PR.
    if args.no_fail:
        return 0
    return result.exit_code


def _git_changed_files(root: str, base: str) -> list:
    """The files changed since `base` (`git diff --name-only base...HEAD`). Degrade-clean — any
    git failure yields an empty list (→ nothing to check → PASS), never a crash."""
    import subprocess
    for spec in (f"{base}...HEAD", base):
        try:
            out = subprocess.run(["git", "-C", root, "diff", "--name-only", spec],
                                 capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return []
        if out.returncode == 0:
            return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
    return []


def register(sub, common):
    p_brain = sub.add_parser(
        "brainstorm", parents=[common],
        help="launch the Socratic pre-spec brainstorm (standalone)",
    )
    p_brain.add_argument(
        "--status", action="store_true",
        help="show whether an approved approach is persisted, instead of launching",
    )
    p_brain.set_defaults(func=cmd_brainstorm)

    p_onboard = sub.add_parser(
        "onboard", parents=[common],
        help="guided capture of typed project knowledge (rules/guardrails/context/docs)",
    )
    p_onboard.set_defaults(func=cmd_onboard)

    p_query = sub.add_parser(
        "query", parents=[common],
        help="run a structural query (graph if present, else grep floor)",
    )
    p_query.add_argument("kind", choices=QUERY_KINDS, help="the structural question")
    p_query.add_argument("target", help="symbol or module to ask about")
    p_query.add_argument(
        "--depth", type=int, default=2,
        help="hops for blast_radius (default: 2; ignored by other kinds)",
    )
    p_query.set_defaults(func=cmd_query)

    p_speck = sub.add_parser(
        "spec-check", parents=[common],
        help="check a change against saved specs + decisions; raise a conflict via the "
             "deviation gate (Stage 37 regression guard)",
    )
    p_speck.add_argument("--symbols", default=None,
                         help="comma-separated symbols the change touches")
    p_speck.add_argument("--files", default=None,
                         help="comma-separated files the change touches")
    p_speck.add_argument("--text", default=None,
                         help="optional free-text description of the change")
    p_speck.add_argument("--phase", default="develop",
                         help="phase this runs at (develop/refine/spec; default: develop)")
    p_speck.add_argument("--yes", action="store_true",
                         help="confirm the change at the deviation gate (amend/supersede)")
    p_speck.set_defaults(func=cmd_spec_check)

    p_ci = sub.add_parser(
        "ci-check", parents=[common],
        help="mokata-as-a-check: run the completeness gate + spec-awareness over a PR's "
             "changed files (read-only; degrade-clean — never false-blocks)",
    )
    p_ci.add_argument("--files", default=None,
                      help="comma-separated changed files (the PR's touch-set)")
    p_ci.add_argument("--symbols", default=None,
                      help="comma-separated changed symbols (default: derived from --files)")
    p_ci.add_argument("--base", default=None,
                      help="git base ref/SHA to diff against (changed files via `git diff`)")
    p_ci.add_argument("--comment-file", default=None,
                      help="write the PR review comment body (markdown) to this path")
    p_ci.add_argument("--no-fail", action="store_true",
                      help="report-only: print/post the verdict but always exit 0")
    p_ci.add_argument("--ascii", action="store_true",
                      help="ASCII-only glyphs in the printed report")
    p_ci.set_defaults(func=cmd_ci_check)


__all__ = [
    "cmd_brainstorm",
    "cmd_onboard",
    "cmd_query",
    "_split_csv",
    "cmd_spec_check",
    "cmd_ci_check",
    "_git_changed_files",
]
