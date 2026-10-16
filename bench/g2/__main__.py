"""`python -m bench.g2 …` — the G2 eval harness CLI (NOT part of the `mokata` CLI).

    plan      print the matrix and its cost drivers; runs nothing
    run       run the matrix (refuses without --yes: a real run spends the operator's plan quota
              and, with --judge anthropic-api, metered API tokens)
    validate  check an archive is a complete REAL run
    study     write STUDY.md from an archive
    page      render the public proof page (D9) — refuses anything but a complete real run
    continuity CTX-CONT (D10): context continuity % of seeded decisions
    lean      AXI-LEAN (D11): full vs lean MCP surface verdict (never adopts)
    audit     PIPE-TOKEN-AUDIT: per-phase graph compliance + system-prompt size tracker

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from typing import List, Optional

from . import conditions as C
from .agent import ClaudeCodeAgent
from .archive import ArchiveError, RunArchive
from .judge import JudgeUnavailable, make_judge
from .runner import MIN_REAL_REPEATS, run_matrix
from .study import render_study
from .tasks import DEFAULT_TASKS_DIR, load_tasks


def cost_drivers(n_conditions: int, n_tasks: int, repeats: int, max_turns: List[int],
                 judge: str) -> str:
    runs = n_conditions * n_tasks * repeats
    worst = sum(max_turns) * n_conditions * repeats
    return "\n".join([
        f"  agent runs        : {runs} ({n_conditions} conditions × {n_tasks} tasks × {repeats} repeats)",
        f"  agent turn ceiling: {worst} model turns at most (Σ task max_turns × conditions × repeats)",
        "  agent billing     : your Claude login (Pro/Max plan quota) — API-key env is scrubbed",
        f"  judge calls       : {runs} (one per run, full trajectory each)",
        ("  judge billing     : METERED API tokens on MOKATA_G2_JUDGE_API_KEY "
         "(≈ transcript size × runs)") if judge == "anthropic-api" else
        "  judge billing     : your Claude login (a separate headless session per run)",
        "  biggest driver    : per-turn context growth — every turn re-sends the whole context",
    ])


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m bench.g2", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "run"):
        sp = sub.add_parser(name)
        sp.add_argument("--tasks-dir", default=DEFAULT_TASKS_DIR)
        sp.add_argument("--tasks", default="", help="comma-separated task ids (default: all)")
        sp.add_argument("--conditions", default="bare,mokata")
        sp.add_argument("--repeats", type=int, default=MIN_REAL_REPEATS)
        sp.add_argument("--judge", choices=("claude-cli", "anthropic-api"), default="claude-cli")
        sp.add_argument("--judge-model", default=None)
        if name == "run":
            sp.add_argument("--out", required=True, help="archive directory (must be new/empty)")
            sp.add_argument("--claude-bin", default="claude")
            sp.add_argument("--keep-workdirs", action="store_true")
            sp.add_argument("--yes", action="store_true",
                            help="confirm: this spends plan quota (and API tokens for an API judge)")
    for name in ("validate", "study", "page", "continuity", "lean", "audit"):
        sp = sub.add_parser(name)
        sp.add_argument("--run", required=True)
        if name == "study":
            sp.add_argument("--out", default=None, help="default: <run>/STUDY.md")
        if name == "audit":
            sp.add_argument("--out", default=None, help="default: <run>/PIPE-TOKEN-AUDIT.md")
            sp.add_argument("--history", default=None,
                            help="system-prompt history JSON to append this REAL run to")
        if name == "page":
            sp.add_argument("--out", required=True, help="the static HTML file to write")
            sp.add_argument("--allow-fixture", action="store_true",
                            help="render a FIXTURE archive (stamped FIXTURE everywhere) for review")
    args = p.parse_args(argv)

    if args.cmd in ("plan", "run"):
        try:
            conds = C.resolve([c.strip() for c in args.conditions.split(",") if c.strip()])
            tasks = load_tasks(args.tasks_dir, [t for t in args.tasks.split(",") if t] or None)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"G2 matrix: {[c.name for c in conds]} × {[t.id for t in tasks]} × {args.repeats}")
        print(cost_drivers(len(conds), len(tasks), args.repeats, [t.max_turns for t in tasks],
                           args.judge))
        if args.repeats < MIN_REAL_REPEATS:
            print(f"note: repeats < {MIN_REAL_REPEATS} — the archive will not validate as publishable")
        if args.cmd == "plan":
            return 0
        if not args.yes:
            print("REFUSING to start a real run without --yes (see the cost drivers above).",
                  file=sys.stderr)
            return 2
        agent = ClaudeCodeAgent(claude_bin=args.claude_bin)
        if not agent.available():
            print(f"error: `{args.claude_bin}` not on PATH — install Claude Code and log in first",
                  file=sys.stderr)
            return 2
        try:
            judge = make_judge(args.judge, args.judge_model)
        except JudgeUnavailable as exc:
            print(str(exc), file=sys.stderr)
            return 2
        from .workspace import GitCloner
        cache = tempfile.mkdtemp(prefix="g2-cache-")
        out = run_matrix(conditions=conds, tasks=tasks, repeats=args.repeats, out_dir=args.out,
                         agent=agent, judge=judge, cloner=GitCloner(cache),
                         keep_workdirs=args.keep_workdirs, log=print)
        arch = RunArchive(out)
        _write(os.path.join(out, "STUDY.md"), render_study(arch))
        problems = arch.validate(require_real=True)
        print(f"archive: {out}\nSTUDY.md written. validation: "
              + ("OK" if not problems else f"{len(problems)} problem(s)"))
        for prob in problems:
            print(f"  - {prob}")
        return 0 if not problems else 1

    try:
        arch = RunArchive(args.run)
    except ArchiveError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.cmd == "validate":
        problems = arch.validate(require_real=True)
        for prob in problems:
            print(f"- {prob}")
        print("OK: complete real run" if not problems else f"{len(problems)} problem(s)")
        return 0 if not problems else 1
    if args.cmd == "study":
        dest = args.out or os.path.join(arch.path, "STUDY.md")
        _write(dest, render_study(arch))
        print(dest)
        return 0
    if args.cmd == "continuity":
        from .continuity import continuity_report, render_markdown
        if not arch.is_real:
            print("note: FIXTURE archive — these numbers measure nothing", file=sys.stderr)
        print(render_markdown(continuity_report(arch)))
        return 0
    if args.cmd == "audit":
        from .audit import append_history, render_report as audit_md
        history = None
        if args.history:
            try:
                history = append_history(arch, args.history)
            except ArchiveError as exc:
                print(str(exc), file=sys.stderr)
                return 2
        dest = args.out or os.path.join(arch.path, "PIPE-TOKEN-AUDIT.md")
        _write(dest, audit_md(arch, history))
        print(dest)
        return 0
    if args.cmd == "lean":
        from .lean import lean_verdict, render_markdown as lean_md
        print(lean_md(lean_verdict(arch)))
        return 0
    if args.cmd == "page":
        from .page import render_page
        try:
            _write(args.out, render_page(arch, allow_fixture=args.allow_fixture))
        except ArchiveError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(args.out)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
