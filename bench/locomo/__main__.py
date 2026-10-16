"""`python -m bench.locomo …` — the LOCOMO benchmark harness CLI.

    fetch    download the dataset + write its lock (the ONLY network fetch; explicit)
    run      run the ablation (refuses without --yes)
    report   render the draft report from an archive (fixtures bannered)
    publish  the pin-gated publish path — refuses with every reason unless all 8 pins hold

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from .pins import PINS


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m bench.locomo")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--data", required=True)
    r = sub.add_parser("run")
    r.add_argument("--data", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--repeats", type=int, default=5)
    r.add_argument("--embedder", default="model2vec")
    r.add_argument("--answerer", choices=("claude-cli", "anthropic-api"), default="claude-cli")
    r.add_argument("--answerer-model", default=None)
    r.add_argument("--judge", choices=("claude-cli", "anthropic-api"), default="claude-cli")
    r.add_argument("--judge-model", default=None)
    r.add_argument("--max-questions", type=int, default=None)
    r.add_argument("--yes", action="store_true")
    for name in ("report", "publish"):
        sp = sub.add_parser(name)
        sp.add_argument("--run", required=True)
        sp.add_argument("--out", default=None)
        if name == "publish":
            sp.add_argument("--data", required=True)
            sp.add_argument("--acknowledge-below-field", action="store_true",
                            help="pin 8: publish a below-field headline AS-IS (it is never reframed)")
    sub.add_parser("pins")
    args = p.parse_args(argv)

    if args.cmd == "pins":
        for n, text, where in PINS:
            print(f"{n}. {text}\n   enforced: {where}")
        return 0
    if args.cmd == "fetch":
        from .dataset import fetch
        lock = fetch(args.data)
        print(f"fetched {lock['bytes']} bytes, sha256 {lock['sha256']}")
        return 0
    if args.cmd == "run":
        from .dataset import load, read_lock
        from .harness import run_benchmark
        from .llm import make_completer
        from .retrieval import resolve_embedder
        convs, lock = load(args.data), read_lock(args.data)
        n_q = sum(len(c.qa[:args.max_questions] if args.max_questions else c.qa) for c in convs)
        print(f"LOCOMO: {len(convs)} conversations, ~{n_q} questions × 5 arms × {args.repeats} "
              f"repeats = ~{n_q * 5 * args.repeats} answerer calls + as many judge calls")
        if not args.yes:
            print("REFUSING without --yes (answerer + judge calls spend plan quota or API tokens).",
                  file=sys.stderr)
            return 2
        try:
            answerer = make_completer(args.answerer, args.answerer_model)
            judge = make_completer(args.judge, args.judge_model)
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            return 2
        run_benchmark(conversations=convs, out_dir=args.out, answerer=answerer, judge=judge,
                      dataset_lock=lock, repeats=args.repeats,
                      embedder=resolve_embedder(args.embedder),
                      max_questions=args.max_questions, log=print)
        print(args.out)
        return 0
    from .report import LocomoArchive, PublishRefused, publish, render_report
    arch = LocomoArchive(args.run)
    try:
        text = (render_report(arch) if args.cmd == "report" else
                publish(arch, args.data, acknowledge_below_field=args.acknowledge_below_field))
    except PublishRefused as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
