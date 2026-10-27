"""`python -m bench.ab …` — the R7 A/B benchmark CLI (NOT part of the `mokata` CLI).

    plan          print the matrix and what a live run would spend; runs nothing
    check-corpus  prove every task discriminates: hidden suite fails on repo/, passes on reference/
    run           run the matrix: `--agent dry` (default; no model call — what CI runs) or
                  `--agent live --yes` (your own `claude` login, headless; refused under CI)
    summarize     re-derive summary.json + RESULTS.md from a committed raw.jsonl (replay)

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from . import harness as H


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m bench.ab", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "check-corpus", "run"):
        sp = sub.add_parser(name)
        sp.add_argument("--tasks", default=H.DEFAULT_TASKS_DIR)
        sp.add_argument("--only", nargs="*", default=None)
        if name in ("plan", "run"):
            sp.add_argument("--repeats", type=int, default=H.MIN_PUBLISHABLE_REPEATS)
        if name == "run":
            sp.add_argument("--agent", choices=("dry", "live"), default="dry")
            sp.add_argument("--yes", action="store_true",
                            help="required for --agent live: a live run spends YOUR plan quota")
            sp.add_argument("--out", required=True)
            sp.add_argument("--claude-bin", default="claude")
    sp = sub.add_parser("summarize")
    sp.add_argument("--raw", required=True)
    sp.add_argument("--out", default=None)
    args = p.parse_args(argv)

    if args.cmd == "summarize":
        rows = H.read_raw(args.raw)
        out = args.out or os.path.dirname(os.path.abspath(args.raw))
        run_json = os.path.join(out, "run.json")
        meta = json.load(open(run_json, encoding="utf-8")) if os.path.exists(run_json) else {}
        summary = H.write_report(out, rows, meta)
        print(json.dumps(summary.get("overall", {}), indent=2, sort_keys=True))
        return 0

    tasks = H.load_tasks(args.tasks, args.only)
    if args.cmd == "plan":
        runs = len(tasks) * len(H.ARMS) * args.repeats
        print(f"{len(tasks)} tasks × {len(H.ARMS)} arms × {args.repeats} repeats = {runs} agent runs")
        print(f"turn ceiling: {sum(t.max_turns for t in tasks) * len(H.ARMS) * args.repeats} "
              f"model turns · billing: your Claude login (API-key env scrubbed)")
        return 0
    if args.cmd == "check-corpus":
        problems = H.check_corpus(tasks)
        for line in problems:
            print("CORPUS:", line)
        print(f"{len(tasks)} task(s) checked, {len(problems)} problem(s)")
        return 1 if problems else 0

    if args.agent == "live":
        if os.environ.get("CI"):
            print("refused: a live A/B run spends a person's own Claude login; it never runs in CI")
            return 2
        if not args.yes:
            print("refused: --agent live needs --yes (it spends your plan quota)")
            return 2
        agent = H.LiveAgent(args.claude_bin)
        if not agent.available():
            print(f"refused: `{args.claude_bin}` is not on PATH")
            return 2
    else:
        agent = H.DryAgent()
    rows = H.run_matrix(tasks, repeats=args.repeats, agent=agent, out_dir=args.out,
                        prepare=args.agent == "live")
    summary = H.write_report(args.out, rows, H.make_meta(tasks, agent, args.repeats))
    print(json.dumps(summary.get("overall", {}), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
