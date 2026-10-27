"""SE.S1/S2 live tier — run the skill-trigger corpus through REAL Claude Code. Owner-run, never CI.

The deterministic gate (`scripts/skill_evals.py gate`) cannot prove Claude picks a skill; this can.
It runs on the OWNER'S OWN Claude Code login, at the cut, by hand:

  * it REFUSES to run when `CI` is set (a CI runner would need a credential, and mokata never asks
    for an API key — Jas's hard rule);
  * every child `claude` gets an environment SCRUBBED of the variables that would move it off the
    owner's login onto metered API billing (`bench.g2.agent.SCRUBBED_ENV`, the G2 harness's list).

Two engines, both writing the replay fixtures the CI gate checks
(`evals/skill_triggers/replay/decisions.json`) and a pass-rate table, and both exiting 1 when the
pass rate is under the recorded threshold (`skill_evals.LIVE_THRESHOLD`):

  plugin-eval   the research ruling's tool: generates a `claude plugin eval` suite from the corpus
                (`evals/generated/`, gitignored) — one case per prompt, a `tool_used: Skill` grader
                (min 1 for a positive; min 0 / max 0 / `arm: both` for a near-miss) — and runs it
                with `--ablation none`. Records whether the TARGET fired (the documented
                `cases[].aggregates.score`), not which other unit won.
  record        `claude -p` per prompt with only the Skill tool, one turn, the plugin loaded with
                `--plugin-dir` from an empty working directory: records exactly which unit was
                chosen (prompt -> chosen skill), the strongest replay fixture.

    python scripts/skill_evals_live.py record [--runs 1] [--unit spec] [--limit 20]
    python scripts/skill_evals_live.py plugin-eval [--runs 3] [--trust-plugin]

A third engine, `validate`, runs NO model and needs NO login, so it is allowed in CI (the weekly
cc-compat job): it generates the suite and has the installed `claude plugin eval` LOAD every case
under `--max-cost-usd 0` (the ceiling stops before the first run), failing on any case the current
Claude Code rejects — the suite format drifting under a Claude Code release is caught before the cut.

    python scripts/skill_evals_live.py validate

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from typing import Callable, Dict, List, Optional, Sequence

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

import skill_evals as SE  # noqa: E402
from bench.g2.agent import scrubbed_env  # noqa: E402

GENERATED_EVAL_DIR = "evals/generated"     # relative, plain names: what `--eval-dir` accepts
PLUGIN = "mokata"

#: runner(argv, cwd, env, timeout) -> (exit code, stdout, stderr). Tests inject a fake.
Runner = Callable[[List[str], str, Dict[str, str], int], "tuple"]


def _truthy(value: Optional[str]) -> bool:
    return (value or "").strip().lower() not in ("", "0", "false", "no", "off")


def refuse_reason(env: Optional[Dict[str, str]] = None) -> Optional[str]:
    env = dict(os.environ if env is None else env)
    if _truthy(env.get("CI")):
        return ("refusing: CI is set. The live tier runs on the owner's own Claude Code login at "
                "the cut, never in CI and never on an API key; CI runs `skill_evals.py gate`.")
    return None


def real_runner(argv: List[str], cwd: str, env: Dict[str, str], timeout: int):
    from mokata.quiet_spawn import hidden
    try:
        proc = subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, **hidden())
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", "replace")
        return -1, out, f"timeout after {timeout}s"


# ---------------------------------------------------------------------------------------------
# Engine: record (claude -p, exact chosen skill).
# ---------------------------------------------------------------------------------------------

def record_argv(claude: str, prompt: str, root: str) -> List[str]:
    return [claude, "-p", prompt, "--output-format", "stream-json", "--verbose",
            "--max-turns", "1", "--tools", "Skill", "--strict-mcp-config",
            "--setting-sources", "project,local", "--plugin-dir", root]


def chosen_skill(stream: str, plugin: str = PLUGIN) -> Optional[str]:
    """The first Skill tool_use in a stream-json transcript, without the `plugin:` prefix."""
    for line in stream.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get("type") != "assistant":
            continue
        for block in (event.get("message") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use" \
                    and block.get("name") == "Skill":
                inp = block.get("input") or {}
                name = str(inp.get("skill") or inp.get("command") or "").lstrip("/")
                if name.startswith(plugin + ":"):
                    name = name[len(plugin) + 1:]
                return name or None
    return None


def run_record(cases: Sequence[dict], *, root: str, runs: int, runner: Runner,
               claude: str = "claude", timeout: int = 180) -> List[dict]:
    model = SE.Model(SE.load_units(root))
    corpus = SE.load_corpus(root)
    env = scrubbed_env()
    out = []
    with tempfile.TemporaryDirectory(prefix="mokata-skill-evals-") as cwd:
        for i, case in enumerate(cases, 1):
            picks = []
            for _ in range(runs):
                code, stdout, stderr = runner(record_argv(claude, case["prompt"], root), cwd, env,
                                              timeout)
                if code not in (0, 1) and not stdout:
                    raise RuntimeError(f"claude exited {code}: {stderr.strip()[:300]}")
                picks.append(chosen_skill(stdout))
            chosen, _n = Counter(picks).most_common(1)[0]
            out.append(SE.make_decision(case, chosen, model, corpus, source="live",
                                        engine="claude-p", picks=picks))
            print(f"[{i}/{len(cases)}] {case['kind']:9s} {case['target']:16s} -> {chosen}",
                  flush=True)
    return out


# ---------------------------------------------------------------------------------------------
# Engine: plugin-eval (`claude plugin eval`, the research ruling's tool).
# ---------------------------------------------------------------------------------------------

def plugin_eval_argv(claude: str, root: str, runs: int, out_dir: str, *, model: Optional[str],
                     trust: bool) -> List[str]:
    argv = [claude, "plugin", "eval", root, "--eval-dir", GENERATED_EVAL_DIR, "--ablation", "none",
            "--runs", str(runs), "--threshold", "0.5", "--output-dir", out_dir, "--no-publish"]
    if model:
        argv += ["--model", model]
    if trust:
        argv.append("--trust-plugin")
    return argv


def decisions_from_aggregate(aggregate: dict, cases: Sequence[dict], root: str) -> List[dict]:
    """Map `cases[].name` -> corpus case (same naming as the generator) and read
    `cases[].aggregates.score` (the fraction of runs whose trigger grader passed)."""
    model = SE.Model(SE.load_units(root))
    corpus = SE.load_corpus(root)
    by_name, counters = {}, {}
    for case in cases:
        key = (case["target"], case["kind"])
        counters[key] = counters.get(key, 0) + 1
        by_name[SE.case_dir_name(case, counters[key])] = case
    out = []
    for c in aggregate.get("cases", []):
        case = by_name.get(c.get("name"))
        if case is None:
            continue
        score = float((c.get("aggregates") or {}).get("score") or 0.0)
        passed = score >= 0.5
        if case["kind"] == "positive":
            chosen = case["target"] if passed else None
        else:
            chosen = None if passed else case["target"]
        out.append(SE.make_decision(case, chosen, model, corpus, source="live",
                                    engine="plugin-eval", grader_score=score))
    return out


def run_plugin_eval(units: Sequence[str], *, root: str, runs: int, runner: Runner,
                    claude: str = "claude", model: Optional[str] = None, trust: bool = False,
                    timeout: int = 7200) -> List[dict]:
    """Generate the suite for `units` (whole units: case names number within a unit), run it."""
    corpus = {u: e for u, e in SE.load_corpus(root).items() if u in set(units)}
    cases = list(SE.iter_cases(corpus))
    gen = os.path.join(root, *GENERATED_EVAL_DIR.split("/"))
    shutil.rmtree(gen, ignore_errors=True)
    SE.generate_plugin_eval(corpus, gen, runs=runs)
    out_dir = os.path.join(gen, "results", "latest")
    argv = plugin_eval_argv(claude, root, runs, out_dir, model=model, trust=trust)
    code, stdout, stderr = runner(argv, root, scrubbed_env(), timeout)
    sys.stdout.write(stdout)
    agg_path = os.path.join(out_dir, "aggregate-result.json")
    if not os.path.isfile(agg_path):
        raise RuntimeError(f"`claude plugin eval` wrote no aggregate-result.json (exit {code}): "
                           f"{stderr.strip()[:400]}")
    with open(agg_path, encoding="utf-8") as fh:
        aggregate = json.load(fh)
    if aggregate.get("partial"):
        raise RuntimeError(f"partial eval run ({aggregate.get('partialReason')}); not recorded")
    return decisions_from_aggregate(aggregate, cases, root)


# ---------------------------------------------------------------------------------------------
# Engine: validate (`claude plugin eval` LOADS the generated suite — zero runs, $0, no login).
# ---------------------------------------------------------------------------------------------

#: `claude plugin eval` prints this (and a `✗ <case>: <why>` line) for a case it cannot load.
_LOAD_FAILED = ("failed to load", "No eval cases found", "\u2717 ")


def validate_argv(claude: str, root: str, out_dir: str) -> List[str]:
    """`--max-cost-usd 0`: the ceiling is checked BEFORE each run launches, so every case is
    parsed and validated (frontmatter keys, grader schema) and none is run — no model call."""
    return [claude, "plugin", "eval", root, "--eval-dir", GENERATED_EVAL_DIR, "--ablation", "none",
            "--max-cost-usd", "0", "--no-publish", "--trust-plugin", "--output-dir", out_dir]


def isolated_env(home: str) -> Dict[str, str]:
    """No credential and a throwaway config dir: the validate engine proves it needs no login."""
    env = scrubbed_env()
    env.update(HOME=home, USERPROFILE=home, CLAUDE_CONFIG_DIR=os.path.join(home, ".claude"),
               DISABLE_AUTOUPDATER="1", CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
    return env


def run_validate(*, root: str, runner: Runner, claude: str = "claude",
                 timeout: int = 600) -> List[str]:
    """Problems with the generated suite as the installed Claude Code reads it ([] = it loads).

    Also asserts the run really spent nothing: the aggregate must be partial at the cost ceiling
    with zero cases run and $0 — a validate that ran a model would be a defect, not a pass."""
    corpus = SE.load_corpus(root)
    gen = os.path.join(root, *GENERATED_EVAL_DIR.split("/"))
    shutil.rmtree(gen, ignore_errors=True)
    n = len(SE.generate_plugin_eval(corpus, gen, runs=1))
    problems: List[str] = []
    with tempfile.TemporaryDirectory(prefix="mokata-skill-evals-validate-") as tmp:
        out_dir = os.path.join(tmp, "results")
        code, stdout, stderr = runner(validate_argv(claude, root, out_dir), root,
                                      isolated_env(tmp), timeout)
        text = (stdout or "") + (stderr or "")
        for line in text.splitlines():
            if any(m in line for m in _LOAD_FAILED):
                problems.append(line.strip())
        agg_path = os.path.join(out_dir, "aggregate-result.json")
        if not os.path.isfile(agg_path):
            problems.append(f"no aggregate-result.json (exit {code}): {text.strip()[-400:]}")
        else:
            with open(agg_path, encoding="utf-8") as fh:
                agg = json.load(fh)
            if agg.get("cases") or float(agg.get("costUsd") or 0) != 0:
                problems.append(f"validate RAN cases or spent money: {len(agg.get('cases') or [])}"
                                f" case(s), ${agg.get('costUsd')}")
            if agg.get("partialReason") != "cost_ceiling":
                problems.append(f"expected a $0 cost-ceiling stop, got partialReason="
                                f"{agg.get('partialReason')!r}")
    print(f"validate: {n} generated case(s) under {GENERATED_EVAL_DIR} -> "
          + ("LOADED" if not problems else f"{len(problems)} problem(s)"))
    return problems


# ---------------------------------------------------------------------------------------------
# Results: pass-rate table + replay fixtures.
# ---------------------------------------------------------------------------------------------

def passed(d: dict) -> bool:
    return d["chosen"] == d["target"] if d["kind"] == "positive" else d["chosen"] != d["target"]


def pass_rate_table(decisions: Sequence[dict], threshold: float = SE.LIVE_THRESHOLD):
    """(markdown table, overall pass rate)."""
    per: Dict[str, List[int]] = {}
    for d in decisions:
        row = per.setdefault(d["target"], [0, 0, 0, 0])   # pos ok, pos n, nm ok, nm n
        i = 0 if d["kind"] == "positive" else 2
        row[i] += passed(d)
        row[i + 1] += 1
    lines = ["| unit | positives fired | near-misses held | pass rate |", "|---|---|---|---|"]
    total_ok = total = 0
    for unit, (po, pn, no, nn) in sorted(per.items()):
        ok, n = po + no, pn + nn
        total_ok, total = total_ok + ok, total + n
        flag = "" if n and ok / n >= threshold else " ⚠"
        lines.append(f"| {unit} | {po}/{pn} | {no}/{nn} | {ok / n:.0%}{flag} |" if n else
                     f"| {unit} | 0/0 | 0/0 | - |")
    rate = total_ok / total if total else 0.0
    lines.append(f"| **all** | | | **{rate:.1%}** (threshold {threshold:.0%}) |")
    return "\n".join(lines), rate


def merge_into_replay(root: str, fresh: Sequence[dict], meta: dict) -> dict:
    current = SE.load_replay(root)
    key = lambda d: (d["target"], d["kind"], d["prompt"])  # noqa: E731
    merged = {key(d): d for d in current.get("decisions", [])}
    for d in fresh:
        merged[key(d)] = d
    valid = {key(c) for c in SE.iter_cases(SE.load_corpus(root))}
    decisions = [d for k, d in merged.items() if k in valid]
    sources = {d.get("source", current.get("source", "seed")) for d in decisions}
    data = {"source": "live" if sources == {"live"} else "mixed", **meta, "decisions": decisions}
    SE.write_json(os.path.join(root, SE.REPLAY_FILE), data)
    return data


def _claude_version(claude: str) -> str:
    try:
        from mokata.quiet_spawn import hidden
        out = subprocess.run([claude, "--version"], capture_output=True, text=True, timeout=30,
                             stdin=subprocess.DEVNULL, env=scrubbed_env(), **hidden())
        return (out.stdout or out.stderr).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return f"unknown ({exc})"


def main(argv: Optional[Sequence[str]] = None, *, runner: Runner = real_runner,
         env: Optional[Dict[str, str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("engine", choices=("record", "plugin-eval", "validate"))
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--claude", default="claude")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--unit", action="append", help="only these units (repeatable)")
    ap.add_argument("--limit", type=int, help="record: at most this many cases")
    ap.add_argument("--model", help="plugin-eval: pin the model under test")
    ap.add_argument("--trust-plugin", action="store_true",
                    help="plugin-eval: pass --trust-plugin (only for a checkout you trust)")
    ap.add_argument("--threshold", type=float, default=SE.LIVE_THRESHOLD)
    args = ap.parse_args(argv)

    if args.engine == "validate":       # no model call, no login: allowed in CI
        if runner is real_runner and not shutil.which(args.claude):
            print(f"`{args.claude}` is not on PATH: install Claude Code first.", file=sys.stderr)
            return 2
        problems = run_validate(root=args.root, runner=runner, claude=args.claude)
        for p in problems:
            print("  " + p)
        return 1 if problems else 0
    why = refuse_reason(env)
    if why:
        print(why, file=sys.stderr)
        return 2
    if runner is real_runner and not shutil.which(args.claude):
        print(f"`{args.claude}` is not on PATH: install Claude Code and log in first.",
              file=sys.stderr)
        return 2
    corpus = SE.load_corpus(args.root)
    units = [u for u in corpus if not args.unit or u in args.unit]
    if args.engine == "record":
        cases = [c for c in SE.iter_cases(corpus) if c["target"] in units]
        if args.limit:
            cases = cases[:args.limit]
        fresh = run_record(cases, root=args.root, runs=args.runs, runner=runner,
                           claude=args.claude)
    else:
        fresh = run_plugin_eval(units, root=args.root, runs=max(args.runs, 1), runner=runner,
                                claude=args.claude, model=args.model, trust=args.trust_plugin)
    table, rate = pass_rate_table(fresh, args.threshold)
    meta = {"engine": args.engine,
            "recorded_at": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat(),
            "claude_version": _claude_version(args.claude) if runner is real_runner else "fake",
            "threshold": args.threshold, "pass_rate": round(rate, 4)}
    merge_into_replay(args.root, fresh, meta)
    print(table)
    print(f"\nrecorded {len(fresh)} decision(s) into {SE.REPLAY_FILE}; pass rate {rate:.1%} "
          f"vs threshold {args.threshold:.0%} -> " + ("PASS" if rate >= args.threshold else "FAIL"))
    return 0 if rate >= args.threshold else 1


if __name__ == "__main__":
    sys.exit(main())
