#!/usr/bin/env python3
"""Live Claude Code check of the mokata MCP server — MANUAL, run by a human, never in CI.

It drives the real `claude` CLI (`claude -p`, your own login) against two throwaway fixture repos,
one `mokata init`-ed and one not, with ONLY the mokata server attached, and grades what a model
actually experiences on its first try:

  * how many mokata tool calls the model made, per prompt and in total;
  * how many came back `isError`, `invalid_input` or `not-initialized`;
  * the FIRST-CALL SUCCESS RATE: the share of prompts whose first mokata call was not refused
    for a bad argument and was not an error;
  * Claude Code's own MCP log for the session: tool descriptions it TRUNCATED, and DEGRADED lines.

It spends your subscription and its numbers depend on the model, which is why a human runs it and
reads it: run it on the old tree and on the new one (`--src`) and compare.

It refuses to run when ANTHROPIC_API_KEY is set, so it can never bill an API key, and it reads
nothing outside the source tree you point it at and the fixtures it creates.

usage:
    python scripts/mcp_live_check.py [--src PATH/TO/src] [--claude claude] [--model M] [--keep]

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SRC = os.path.abspath(os.path.join(HERE, "..", "src"))

#: (fixture, prompt). `init` runs in the initialized repo, `bare` in the uninitialized one.
PROMPTS: List[Tuple[str, str]] = [
    ("init", "Using the mokata tools, show me the status of this repo."),
    ("init", "Using mokata, who calls the function f1? Use the code graph query tool."),
    ("init", "Using mokata, remember that our team prefers pytest over unittest."),
    ("init", "Using mokata, recall anything remembered about testing."),
    ("init", "Using mokata, record a passed review verdict for this run with no blocking findings."),
    ("init", "Using mokata, mark that the run is entering the develop stage."),
    ("init", "Using mokata, emit a spec titled 'adders add' with one acceptance criterion AC1 "
             "'f1 adds 1', a test test_f1_adds for AC1, and a task T1 implementing f1 in src/m1.py."),
    ("init", "Using mokata, what is the value of the config key profile?"),
    ("init", "Using mokata, list the worktrees of this repo."),
    ("init", "Using mokata, run the doctor and summarise it in one line."),
    ("bare", "Using the mokata tools, show me the status of this repo."),
    ("bare", "Using mokata, remember that this repo uses tabs."),
]

FIXTURE_FILES = {f"src/m{i}.py": f"def f{i}(x):\n    return x + {i}\n" for i in range(4)}


def _refuse_api_key() -> None:
    if os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("mcp_live_check: refusing — ANTHROPIC_API_KEY is set. This check runs on your "
                 "own Claude login only; unset it and re-run.")


def _fixture(src: str, init: bool) -> str:
    d = tempfile.mkdtemp(prefix="mokata-live-" + ("init-" if init else "bare-"))
    for rel, text in FIXTURE_FILES.items():
        os.makedirs(os.path.join(d, os.path.dirname(rel)), exist_ok=True)
        with open(os.path.join(d, rel), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    subprocess.run(["git", "init", "-q", d], check=True, stdin=subprocess.DEVNULL)
    if init:
        code = ("import sys; sys.path.insert(0, sys.argv[1]); from mokata.init import init_repo; "
                "init_repo(root=sys.argv[2], profile='standard', assume_yes=True, "
                "out=lambda *a: None)")
        subprocess.run([sys.executable, "-c", code, src, d], check=True, stdin=subprocess.DEVNULL)
    return d


def _mcp_config(src: str, repo: str) -> str:
    launch = ("import sys; sys.path.insert(0, %r); from mokata.mcp.server import main; "
              "sys.exit(main())" % src)
    cfg = {"mcpServers": {"mokata": {"command": sys.executable, "args": ["-c", launch],
                                     "cwd": repo}}}
    path = os.path.join(repo, ".live-check-mcp.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    return path


def _result_text(block: Dict[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, list):
        return "".join(c.get("text", "") for c in content if isinstance(c, dict))
    return str(content or "")


def _grade_stream(lines: List[str]) -> Dict[str, Any]:
    """Count mokata tool_use / results from a `--output-format stream-json` transcript."""
    names: Dict[str, str] = {}
    calls: List[Dict[str, Any]] = []
    session_error: Optional[str] = None
    for raw in lines:
        try:
            ev = json.loads(raw)
        except ValueError:
            continue
        # The session's own failure (an expired login, a refused request) arrives as a `result`
        # event with is_error set. It is not a mokata answer and must never grade as "0 calls".
        if isinstance(ev, dict) and ev.get("type") == "result" and ev.get("is_error"):
            session_error = str(ev.get("result") or ev.get("subtype") or "unknown session error")
        # stream-json carries events whose `message` is a plain string (system and error
        # events) or that are not objects at all; only an object message has content blocks.
        msg = ev.get("message") if isinstance(ev, dict) else None
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and str(block.get("name", "")).startswith(
                    "mcp__mokata__"):
                names[block.get("id")] = block["name"][len("mcp__mokata__"):]
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in names:
                text = _result_text(block)
                calls.append({"tool": names[block["tool_use_id"]],
                              "is_error": bool(block.get("is_error")),
                              "invalid_input": '"invalid_input"' in text,
                              "not_initialized": '"not-initialized"' in text})
    first = calls[0] if calls else None
    return {"calls": calls, "session_error": session_error,
            "first_ok": bool(first) and not (first["is_error"] or first["invalid_input"])}


def _mcp_logs_since(started: float) -> Tuple[int, int, List[str]]:
    """Claude Code's MCP logs for the mokata server touched since `started`: (truncated, degraded)."""
    roots = [os.path.expanduser("~/Library/Caches/claude-cli-nodejs"),
             os.path.expanduser("~/.cache/claude-cli-nodejs"),
             os.path.join(os.environ.get("LOCALAPPDATA", ""), "claude-cli-nodejs")]
    truncated = degraded = 0
    files: List[str] = []
    for root in roots:
        for path in glob.glob(os.path.join(root, "*", "mcp-logs-mokata", "*.jsonl")):
            if os.path.getmtime(path) < started:
                continue
            files.append(path)
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    truncated += "truncated" in line
                    degraded += "DEGRADED" in line
    return truncated, degraded, files


def _run(claude: str, model: Optional[str], repo: str, cfg: str, prompt: str,
         timeout: float) -> List[str]:
    argv = [claude, "-p", prompt, "--mcp-config", cfg, "--strict-mcp-config",
            "--output-format", "stream-json", "--verbose", "--allowedTools", "mcp__mokata__*"]
    if model:
        argv += ["--model", model]
    try:
        proc = subprocess.run(argv, cwd=repo, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", stdin=subprocess.DEVNULL, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        out = out.decode("utf-8", "replace") if isinstance(out, bytes) else out
        return out.splitlines() + ['{"type": "live-check", "timeout": true}']
    return proc.stdout.splitlines()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--src", default=DEFAULT_SRC, help="the mokata src/ tree the server runs from")
    ap.add_argument("--claude", default="claude", help="the Claude Code CLI to drive")
    ap.add_argument("--model", default=None, help="pass --model to claude (default: yours)")
    ap.add_argument("--timeout", type=float, default=600.0,
                    help="seconds one prompt may take before it is recorded as a timeout")
    ap.add_argument("--keep", action="store_true", help="keep the fixture repos")
    args = ap.parse_args(argv)
    _refuse_api_key()
    if not shutil.which(args.claude):
        sys.exit(f"mcp_live_check: `{args.claude}` is not on PATH")
    src = os.path.abspath(args.src)
    repos = {"init": _fixture(src, True), "bare": _fixture(src, False)}
    cfgs = {k: _mcp_config(src, d) for k, d in repos.items()}
    started = time.time()
    rows = []
    try:
        for i, (kind, prompt) in enumerate(PROMPTS, 1):
            lines = _run(args.claude, args.model, repos[kind], cfgs[kind], prompt, args.timeout)
            g = _grade_stream(lines)
            if g["session_error"]:
                print(f"P{i:02d} [{kind}] the claude session itself failed, before mokata was "
                      f"asked anything: {g['session_error']}")
                print("mcp_live_check: NOTHING GRADED — fix the session (an expired login: run "
                      "`claude` and /login) and re-run.")
                return 2
            timed_out = any('"live-check"' in ln for ln in lines)
            rows.append((i, kind, g, timed_out))
            c = g["calls"]
            print(f"P{i:02d} [{kind}] calls={len(c)} error={sum(x['is_error'] for x in c)} "
                  f"invalid_input={sum(x['invalid_input'] for x in c)} "
                  f"not_initialized={sum(x['not_initialized'] for x in c)} "
                  f"first_ok={'yes' if g['first_ok'] else 'no'}"
                  + (" TIMEOUT" if timed_out else "")
                  + (f"  tools={','.join(x['tool'] for x in c)}" if c else ""))
    finally:
        if not args.keep:
            for d in repos.values():
                shutil.rmtree(d, ignore_errors=True)
    truncated, degraded, files = _mcp_logs_since(started)
    calls = [x for _i, _k, g, _t in rows for x in g["calls"]]
    with_calls = [r for r in rows if r[2]["calls"]]
    ok = sum(1 for r in with_calls if r[2]["first_ok"])
    print("")
    print(f"prompts={len(rows)} with_a_mokata_call={len(with_calls)} total_calls={len(calls)}")
    print(f"isError={sum(x['is_error'] for x in calls)} "
          f"invalid_input={sum(x['invalid_input'] for x in calls)} "
          f"not_initialized={sum(x['not_initialized'] for x in calls)} "
          f"timeouts={sum(1 for r in rows if r[3])}")
    rate = (100.0 * ok / len(with_calls)) if with_calls else 0.0
    print(f"first_call_success={ok}/{len(with_calls)} ({rate:.0f}%)")
    print(f"mcp_log: truncated_descriptions={truncated} degraded_lines={degraded} "
          f"files={len(files)}")
    if not with_calls:
        print("mcp_live_check: NOTHING GRADED — no prompt reached a mokata tool call.")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
