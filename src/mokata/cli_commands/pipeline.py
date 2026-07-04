"""suggest / chain / exec / decompose / playbook / preview — compose skills and drive the execution pipeline."""
from __future__ import annotations

import argparse
import sys

from ._common import (
    ConfigError,
    Surface,
    PIPELINE_PHASES,
    SuggestionContext,
    plan_chain,
    suggest,
    PARALLEL,
    SEQUENTIAL,
    ExecutionChoice,
    resolve_execution_choice,
    AuditLedger,
    claude_code_harness,
    KnowledgeLayer,
    ManifestError,
    preview_pipeline,
    run_playbook,
    _load_surface,
    _cli_ask,
)


def cmd_exec(args: argparse.Namespace) -> int:
    # E8 / Stage 25 — choose the execution mode for a run. Explicit flags select
    # non-interactively; otherwise honor the saved settings.execution.default and ask
    # once (default 'ask') — never fan out without a choice.
    if args.parallel:
        isolation = args.isolation or not args.fanout   # parallel implies ≥ isolation
        choice = ExecutionChoice(PARALLEL, isolation=isolation, fanout=args.fanout)
    else:
        manifest = None
        if Surface.is_initialized(args.path):
            try:
                manifest = Surface.load(args.path).manifest
            except (ConfigError, ManifestError):
                manifest = None
        subagents = claude_code_harness().supports("subagents")
        choice = resolve_execution_choice(
            manifest=manifest, ask=_cli_ask, out=print, subagents_available=subagents)
    from ..progress import active_banner
    print(active_banner(f"exec ({choice.mode})", running=False))
    print(f"execution mode: {choice.label()}")
    if choice.is_parallel:
        print("  parallel modes surface a token/cost estimate before running, stay "
              "under the gates + audit ledger + token budget, and degrade to "
              "sequential flow if subagents are unavailable.")
    else:
        print("  the sequential gated flow is the default, lowest-cost path.")
    return 0


def cmd_decompose(args: argparse.Namespace) -> int:
    # Stage 54f — propose an independent-subtask split of the emitted spec's ACs, then (with
    # --run) human-gate the confirm and feed the confirmed tasks into the EXISTING flow
    # (resolve_execution_choice -> run_tasks). The split itself is read-only.
    from ..engine import load_emitted_spec
    from ..execmode.decompose import (confirm_decomposition, decompose,
                                     run_decomposition)
    surface = _load_surface(args.path)
    spec = load_emitted_spec(surface.state)
    if spec is None or not spec.criteria:
        print("mokata decompose: no emitted spec with acceptance criteria — run "
              "/mokata:spec first (the split is derived from the approved ACs).")
        return 0
    layer = KnowledgeLayer.from_surface(surface)
    plan = decompose(spec, layer=layer)
    if not args.run:
        print(plan.render(ascii_only=args.ascii))
        return 0
    # --run: gated confirm, then the existing execution flow (default sequential).
    ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
    asker = None if args.yes else _cli_ask
    outcome = confirm_decomposition(plan, ask=asker, ledger=ledger, out=print,
                                    assume_yes=args.yes)
    if not outcome.confirmed:
        print("mokata decompose: not confirmed — nothing fanned out (the split stays "
              "read-only until you confirm).")
        return 0
    subagents = claude_code_harness().supports("subagents")
    result = run_decomposition(outcome.plan, manifest=surface.manifest, ask=asker,
                               ledger=ledger, out=print, runner=None,
                               subagents_available=subagents)
    degraded = " · degraded to sequential" if result.degraded else ""
    print(f"mokata decompose: ran {len(result.results)} task(s) "
          f"[{result.choice.label()}{degraded}].")
    return 0


def cmd_preview(args: argparse.Namespace) -> int:
    # E7 — dry-run: print the pipeline plan (actions + gates + file touches). No writes.
    surface = _load_surface(args.path)
    pv = preview_pipeline(start=args.start, stop=args.to,
                          mokata_dir=surface.mokata_dir)
    print(pv.render())
    return 0


def cmd_playbook(args: argparse.Namespace) -> int:
    # Stage 9 — drive the full v1 story end-to-end on this repo. Parallel without a
    # subagent harness degrades to sequential (degrade-safe). Stage 27: announce the
    # active stage (banner) at the start and on completion.
    from ..progress import active_banner
    surface = _load_surface(args.path)
    if args.parallel:
        choice = ExecutionChoice(PARALLEL, isolation=True, fanout=args.fanout)
    else:
        choice = ExecutionChoice(SEQUENTIAL)
    print(active_banner("playbook", running=True))
    result = run_playbook(surface, choice, dense=args.dense)
    print(result.render())
    print(active_banner("playbook", running=False))
    return 0 if result.ok else 1


def cmd_suggest(args: argparse.Namespace) -> int:
    ctx = SuggestionContext(
        starting_fresh=args.fresh, has_spec=args.spec,
        has_failing_test=args.failing_test, has_implementation=args.implementation,
        has_diff=args.diff, has_bug_report=args.bug,
        has_stacktrace=args.stacktrace, has_perf_issue=args.perf)
    suggestions = suggest(ctx)
    if not suggestions:
        print("no suggestions for this context.")
        return 0
    print("suggested (not run — your call):")
    for s in suggestions:
        print(f"  /{s.skill} — {s.reason}")
    return 0


def cmd_chain(args: argparse.Namespace) -> int:
    try:
        steps = plan_chain(args.skills)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("chain (each step applies its own gate):")
    for s in steps:
        print(f"  /{s.skill}  [gate: {s.gate}]")
    return 0


def register(sub, common):
    p_sug = sub.add_parser(
        "suggest", parents=[common],
        help="suggest a relevant command for the context (suggest only, never runs)",
    )
    for flag in ("fresh", "spec", "diff", "bug", "stacktrace", "perf"):
        p_sug.add_argument(f"--{flag}", action="store_true")
    p_sug.add_argument("--failing-test", dest="failing_test", action="store_true")
    p_sug.add_argument("--implementation", action="store_true")
    p_sug.set_defaults(func=cmd_suggest)

    p_chain = sub.add_parser(
        "chain", parents=[common],
        help="plan a manual chain of skills; each step keeps its own gate (L5)",
    )
    p_chain.add_argument("skills", nargs="+", help="skills to chain, in order")
    p_chain.set_defaults(func=cmd_chain)

    p_exec = sub.add_parser(
        "exec", parents=[common],
        help="show/select the execution mode for a run (default: sequential)",
    )
    p_exec.add_argument("--parallel", action="store_true",
                        help="parallel subagents (default is sequential gated flow)")
    p_exec.add_argument("--isolation", action="store_true",
                        help="fresh-subagent isolation + two-stage review (E2/E3)")
    p_exec.add_argument("--fanout", action="store_true",
                        help="concurrent fan-out (run tasks at once)")
    p_exec.set_defaults(func=cmd_exec)

    p_decomp = sub.add_parser(
        "decompose", parents=[common],
        help="propose an independent-subtask split of the emitted spec's ACs; --run "
             "human-gates the confirm then feeds the existing exec flow (Stage 54f)",
    )
    p_decomp.add_argument("--run", action="store_true",
                          help="confirm the split (human-gated) and run it via the existing "
                               "execution flow (default: just show the read-only split)")
    p_decomp.add_argument("--ascii", action="store_true",
                          help="ASCII glyphs instead of unicode in the split view")
    p_decomp.add_argument("--yes", action="store_true",
                          help="approve the confirm non-interactively (with --run)")
    p_decomp.set_defaults(func=cmd_decompose)

    p_play = sub.add_parser(
        "playbook", parents=[common],
        help="run the full v1 story end-to-end on this repo (integration check)",
    )
    p_play.add_argument("--parallel", action="store_true",
                        help="use parallel subagents (degrades to sequential w/o a harness)")
    p_play.add_argument("--fanout", action="store_true",
                        help="concurrent fan-out (with --parallel)")
    p_play.add_argument("--dense", action="store_true",
                        help="F4 output-density: compress sub-agent handbacks (off by "
                             "default; or set settings.governance.output_density)")
    p_play.set_defaults(func=cmd_playbook)

    p_prev = sub.add_parser(
        "preview", parents=[common],
        help="dry-run: list planned phases, gates, and file touches (no side effects)",
    )
    p_prev.add_argument("--start", choices=PIPELINE_PHASES, default=None,
                        help="phase to start the preview at (default: first)")
    p_prev.add_argument("--to", choices=PIPELINE_PHASES, default=None,
                        help="phase to stop the preview at (default: last)")
    p_prev.set_defaults(func=cmd_preview)


__all__ = [
    "cmd_exec",
    "cmd_decompose",
    "cmd_preview",
    "cmd_playbook",
    "cmd_suggest",
    "cmd_chain",
]
