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
  reset      K6  remove mokata state (uninstall / reset)
  suggest    L6  suggest a relevant command (never runs it)
  chain      L5  plan a manual chain of skills (gates still apply)
  export     J3  export the current manifest as a shareable stack
  import     J3  validate + apply a shared stack manifest (human-gated)
  harness    J2  show the harness boundary's capabilities
  exec       E8  show/select the execution mode (sequential default / parallel)
  playbook       run the full v1 story end-to-end (integration check)
  preview    E7  dry-run: planned phases + gates + file touches (no side effects)

Later stages add more subcommands; this keeps the spine usable from the shell today.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from . import __version__
from .bootstrap import build_bootstrap
from .brainstorm import ground, load_approved_approach, render_launch
from .config import ConfigError, Surface
from .detect import Detector
from .init import init_repo
from . import MOKATA_DIR
from .adapters import (
    AdapterContract,
    MCPRegistry,
    negotiate,
    overlapping_capabilities,
)
from .brainstorm import PIPELINE_PHASES
from .compose import SuggestionContext, plan_chain, suggest
from .execmode import PARALLEL, SEQUENTIAL, ExecutionChoice, select_execution_mode
from .govern import (
    AuditLedger,
    BudgetReport,
    budget_statusline,
    diagnose,
    load_rules,
    plan_reset,
    reset_state,
    validate_caps,
)
from .harness import HARNESS_CAPABILITIES, claude_code_harness
from .share import SHARE_FILENAME, apply_manifest, export_manifest, load_shared
from .knowledge import QUERY_KINDS, KnowledgeIndex, KnowledgeLayer, lat_check
from .manifest import ManifestError
from .memory import MemoryStore
from .engine import preview_pipeline
from .pipeline import PhaseError, plan_entry, render_entry
from .playbook import run_playbook
from .skills import SKILL_NAMES, SkillNotFound, get_skill, list_skills, render_skill
from .profiles import DEFAULT_PROFILE, TOOL_CATALOG, profile_names


def _load_surface(root: str) -> Surface:
    try:
        return Surface.load(root)
    except (ConfigError, ManifestError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)


def cmd_init(args: argparse.Namespace) -> int:
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
    # Standalone launch (L1): print the clean-room protocol + live grounding. No prior
    # pipeline phase is required to run this.
    grounding = ground(surface.router)
    sys.stdout.write(render_launch(grounding))
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


def cmd_memory(args: argparse.Namespace) -> int:
    # Read-only: surface active memory, the read/write ratio, and pending healing
    # proposals. Never commits — durable memory writes are human-gated elsewhere.
    surface = _load_surface(args.path)
    store = MemoryStore.from_surface(surface)
    if not store.enabled_types:
        print("memory: disabled for this profile (no memory types enabled).")
        return 0

    active = store.all_active()
    print(f"memory backend: {store.backend.name} · "
          f"types on: {', '.join(store.enabled_types)}")
    print(store.stats.log_line())
    print(f"active items: {len(active)}")
    for it in active:
        print(f"  [{it.mtype}] {it.subject} = {it.value}")

    proposals = store.detect_issues()
    if proposals:
        print(f"\nself-healing — {len(proposals)} item(s) need your decision "
              f"(nothing changes until you act):")
        for p in proposals:
            print(f"  ({p.kind}) [{p.mtype}] {p.subject}: {p.diff()}")
    return 0


def cmd_skills(args: argparse.Namespace) -> int:
    # L4 — catalog with progressive disclosure: bare list is cheap; a name reveals detail.
    if args.name:
        try:
            skill = get_skill(args.name)
        except SkillNotFound as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"/{skill.name} — {skill.summary}")
        print(f"  gate: {skill.gate.id} ({skill.gate.kind}) — {skill.gate.description}")
        if skill.phase:
            print(f"  pipeline phase: {skill.phase}")
        if skill.scaffold:
            print("  (scaffold — deeper engine in a later stage)")
        print("\n" + skill.prompt)
        return 0
    print("mokata skills (run `mokata skills <name>` for detail):")
    for name, summary in list_skills():
        print(f"  /{name:10} {summary}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    # L1/L3 — run a skill standalone. No init and no upstream phase are required; if the
    # repo is initialized we add live grounding, otherwise we degrade cleanly.
    try:
        skill = get_skill(args.name)
    except SkillNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    grounding = ground(None)
    if Surface.is_initialized(args.path):
        try:
            grounding = ground(Surface.load(args.path).router)
        except (ConfigError, ManifestError):
            grounding = ground(None)
    sys.stdout.write(render_skill(skill, grounding))
    return 0


def cmd_enter(args: argparse.Namespace) -> int:
    # L2 — enter the pipeline at a phase; only the run phases' gates apply.
    try:
        plan = plan_entry(args.phase, stop=args.to)
    except PhaseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(render_entry(plan))
    return 0


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


def cmd_audit(args: argparse.Namespace) -> int:
    ledger = AuditLedger.from_mokata_dir(os.path.join(args.path, MOKATA_DIR))
    entries = ledger.entries()
    if not entries:
        print("audit ledger: empty.")
        return 0
    print(f"audit ledger — {len(entries)} entr{'y' if len(entries) == 1 else 'ies'}:")
    for e in entries:
        extra = " ".join(f"{k}={v}" for k, v in e.items()
                         if k not in ("seq", "kind", "at"))
        print(f"  #{e['seq']:<3} {e['kind']:<11} {extra}")
    return 0


def cmd_exec(args: argparse.Namespace) -> int:
    # E8 — report the execution mode for a run. Default is the sequential gated flow;
    # flags select parallel + isolation/fan-out non-interactively.
    if args.parallel:
        isolation = args.isolation or not args.fanout   # parallel implies ≥ isolation
        choice = ExecutionChoice(PARALLEL, isolation=isolation, fanout=args.fanout)
    else:
        choice = select_execution_mode()                # no asker -> sequential default
    print(f"execution mode: {choice.label()}")
    if choice.is_parallel:
        print("  parallel modes surface a token/cost estimate before running, stay "
              "under the gates + audit ledger + token budget, and degrade to "
              "sequential flow if subagents are unavailable.")
    else:
        print("  the sequential gated flow is the default, lowest-cost path.")
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
    # subagent harness degrades to sequential (degrade-safe).
    surface = _load_surface(args.path)
    if args.parallel:
        choice = ExecutionChoice(PARALLEL, isolation=True, fanout=args.fanout)
    else:
        choice = ExecutionChoice(SEQUENTIAL)
    result = run_playbook(surface, choice)
    print(result.render())
    return 0 if result.ok else 1


def cmd_index(args: argparse.Namespace) -> int:
    # B4 — build/refresh the per-file freshness index; report what changed + stale files.
    surface = _load_surface(args.path)
    store = surface.state
    data = store.read("knowledge_index")
    idx = KnowledgeIndex.from_dict(data) if data else KnowledgeIndex()
    if data is None:
        built = idx.build(surface.root)
        print(f"index: built {len(built)} file(s)")
    else:
        d = idx.diff(surface.root)
        reindexed = idx.reindex(surface.root)
        print(f"index: reindexed {len(reindexed)} changed, "
              f"+{len(d['added'])} added, -{len(d['removed'])} removed")
    store.write("knowledge_index", idx.to_dict())
    print(f"index: tracking {len(idx.entries)} file(s)")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    # J3 — export the current manifest as a shareable stack file.
    surface = _load_surface(args.path)
    dest = args.file or os.path.join(args.path, SHARE_FILENAME)
    export_manifest(surface, dest=dest)
    print(f"exported stack to {dest}")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    # J3 — validate + apply a shared manifest (human-gated).
    try:
        data = load_shared(args.file)
    except (OSError, ValueError) as exc:
        print(f"error: cannot read {args.file}: {exc}", file=sys.stderr)
        return 1
    result = apply_manifest(args.path, data, assume_yes=args.yes, force=args.force)
    if result.errors:
        print("import rejected — invalid manifest:", file=sys.stderr)
        for e in result.errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    if not result.applied:
        print(f"\n{result.message}", file=sys.stderr)
        return 1
    print(f"applied shared stack to {result.path}")
    return 0


def cmd_harness(args: argparse.Namespace) -> int:
    # J2 — report the harness boundary's reference harness + capabilities.
    h = claude_code_harness()
    print(f"harness: {h.name}")
    for cap in HARNESS_CAPABILITIES:
        print(f"  [{'yes' if h.supports(cap) else 'no '}] {cap}")
    print("(the engine is harness-agnostic; a harness lacking a capability degrades "
          "with a clear message, never a crash.)")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    surface = _load_surface(args.path)
    report = diagnose(surface)
    print(report.render())
    return 0 if report.ok else 1


def cmd_reset(args: argparse.Namespace) -> int:
    plan = plan_reset(args.path, keep_config=args.keep_config)
    if not plan.targets:
        print("reset: nothing to remove.")
        return 0
    print("reset will remove:")
    for t in plan.targets:
        print(f"  {t}")
    result = reset_state(args.path, keep_config=args.keep_config,
                         assume_yes=args.yes, backup_dir=args.backup)
    if result.aborted:
        print(f"\n{result.message}", file=sys.stderr)
        return 1
    print(f"removed {len(result.removed)} path(s)"
          + (f"; backed up to {args.backup}" if args.backup else ""))
    return 0


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


def cmd_coverage(args: argparse.Namespace) -> int:
    # A6 — treat the manifest's tools as adapters; report capability coverage + gaps.
    surface = _load_surface(args.path)
    m = surface.manifest
    adapters = [AdapterContract(name=tid, provides=[t.get("provides")],
                                kind=t.get("kind", "external"))
                for tid, t in m.tools.items() if t.get("provides")]
    report = negotiate(list(m.capabilities), adapters)
    print(report.render())
    overlaps = overlapping_capabilities(m)
    if overlaps:
        print("overlaps (resolved by manifest precedence):")
        for need, providers in overlaps.items():
            print(f"  {need}: {' > '.join(providers)}")
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    # H4 — discover MCP servers (from .mokata/mcp.json) and map to roles; degrade cleanly.
    surface = _load_surface(args.path)
    reg = MCPRegistry.discover(path=os.path.join(surface.mokata_dir, "mcp.json"))
    if not reg.servers:
        print("mcp: no servers discovered (degraded — none present).")
        return 0
    print(f"mcp: {len(reg.servers)} server(s)")
    for cap, names in reg.map_to_roles().items():
        print(f"  {cap}: {', '.join(names)}")
    return 0


def cmd_lat_check(args: argparse.Namespace) -> int:
    # B5 — scan @lat anchors and flag concept drift (degrades cleanly when absent).
    surface = _load_surface(args.path)
    report = lat_check(surface.root)
    print(report.render())
    return 1 if report.has_drift else 0


def cmd_status(args: argparse.Namespace) -> int:
    surface = _load_surface(args.path)
    m = surface.manifest
    live = [r.summary() for r in surface.router.resolve_all()]
    print(f"mokata {m.mokata_version} · profile '{m.profile}'")
    for line in live:
        print(f"  {line}")
    return 0


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

    p_init = sub.add_parser(
        "init", parents=[common],
        help="scaffold config; detect tools; pick profile",
    )
    p_init.add_argument(
        "--profile",
        default=DEFAULT_PROFILE,
        choices=profile_names(),
        help=f"starting profile (default: {DEFAULT_PROFILE})",
    )
    p_init.add_argument(
        "--yes", action="store_true", help="non-interactive; skip the write prompt"
    )
    p_init.add_argument(
        "--force", action="store_true", help="overwrite an existing manifest"
    )
    p_init.set_defaults(func=cmd_init)

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

    p_brain = sub.add_parser(
        "brainstorm", parents=[common],
        help="launch the Socratic pre-spec brainstorm (standalone)",
    )
    p_brain.add_argument(
        "--status", action="store_true",
        help="show whether an approved approach is persisted, instead of launching",
    )
    p_brain.set_defaults(func=cmd_brainstorm)

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

    p_mem = sub.add_parser(
        "memory", parents=[common],
        help="surface active memory, read/write ratio, and healing proposals (read-only)",
    )
    p_mem.set_defaults(func=cmd_memory)

    p_skills = sub.add_parser(
        "skills", parents=[common],
        help="list the skill/command catalog (add a name for detail)",
    )
    p_skills.add_argument("name", nargs="?", help="reveal detail for one skill")
    p_skills.set_defaults(func=cmd_skills)

    p_run = sub.add_parser(
        "run", parents=[common],
        help="run a skill/command standalone (no pipeline prerequisite)",
    )
    p_run.add_argument("name", choices=SKILL_NAMES, help="the skill to run")
    p_run.set_defaults(func=cmd_run)

    p_enter = sub.add_parser(
        "enter", parents=[common],
        help="enter the pipeline at a phase (applies only that phase's gates)",
    )
    p_enter.add_argument("phase", choices=PIPELINE_PHASES, help="phase to start at")
    p_enter.add_argument("--to", choices=PIPELINE_PHASES, default=None,
                         help="optional phase to stop after (default: just the start)")
    p_enter.set_defaults(func=cmd_enter)

    p_rules = sub.add_parser(
        "rules", parents=[common],
        help="show the 4-tier rules and their line budgets",
    )
    p_rules.set_defaults(func=cmd_rules)

    p_audit = sub.add_parser(
        "audit", parents=[common], help="show the append-only audit ledger",
    )
    p_audit.set_defaults(func=cmd_audit)

    p_budget = sub.add_parser(
        "budget", parents=[common],
        help="show token savings (live budget readout + statusline)",
    )
    p_budget.set_defaults(func=cmd_budget)

    p_index = sub.add_parser(
        "index", parents=[common],
        help="build/refresh the freshness index (incremental); report stale files",
    )
    p_index.set_defaults(func=cmd_index)

    p_lat = sub.add_parser(
        "lat-check", parents=[common],
        help="scan @lat anchors and flag concept drift (degrades cleanly when absent)",
    )
    p_lat.set_defaults(func=cmd_lat_check)

    p_cov = sub.add_parser(
        "coverage", parents=[common],
        help="report capability coverage + unmet gaps + overlaps (A6/H6)",
    )
    p_cov.set_defaults(func=cmd_coverage)

    p_mcp = sub.add_parser(
        "mcp", parents=[common],
        help="discover MCP servers and map them to roles (H4; degrades cleanly)",
    )
    p_mcp.set_defaults(func=cmd_mcp)

    p_doc = sub.add_parser(
        "doctor", parents=[common],
        help="diagnose the manifest/config (missing deps, conflicts, bad trust)",
    )
    p_doc.set_defaults(func=cmd_doctor)

    p_exp = sub.add_parser(
        "export", parents=[common],
        help="export the current manifest as a shareable stack (J3)",
    )
    p_exp.add_argument("file", nargs="?", default=None,
                       help="destination file (default: <path>/mokata-stack.json)")
    p_exp.set_defaults(func=cmd_export)

    p_imp = sub.add_parser(
        "import", parents=[common],
        help="validate + apply a shared stack manifest (human-gated, J3)",
    )
    p_imp.add_argument("file", help="shared manifest file to apply")
    p_imp.add_argument("--yes", action="store_true", help="non-interactive apply")
    p_imp.add_argument("--force", action="store_true", help="overwrite existing config")
    p_imp.set_defaults(func=cmd_import)

    p_harn = sub.add_parser(
        "harness", parents=[common],
        help="show the harness boundary's capabilities (J2)",
    )
    p_harn.set_defaults(func=cmd_harness)

    p_reset = sub.add_parser(
        "reset", parents=[common],
        help="remove mokata state (.mokata/); --keep-config keeps the manifest",
    )
    p_reset.add_argument("--keep-config", action="store_true",
                         help="keep manifest + constitution; remove only state")
    p_reset.add_argument("--backup", default=None,
                         help="move state to this dir instead of deleting (reversible)")
    p_reset.add_argument("--yes", action="store_true",
                         help="non-interactive; skip the confirmation prompt")
    p_reset.set_defaults(func=cmd_reset)

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

    p_play = sub.add_parser(
        "playbook", parents=[common],
        help="run the full v1 story end-to-end on this repo (integration check)",
    )
    p_play.add_argument("--parallel", action="store_true",
                        help="use parallel subagents (degrades to sequential w/o a harness)")
    p_play.add_argument("--fanout", action="store_true",
                        help="concurrent fan-out (with --parallel)")
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

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # argparse stores --path before the subcommand; subcommands read args.path.
    if not hasattr(args, "path"):
        args.path = "."
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
