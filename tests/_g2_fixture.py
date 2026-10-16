"""Shared FIXTURE for the G2 eval-harness tests (D7/D9/D10/D11/D14/pipe-token-audit).

Builds a G2 run archive with a FAKE agent, a FAKE judge and a copy-cloner — no network, no
`claude` binary, no API key. Every archive built here derives `kind: fixture`, which is the point:
the proof page and the audit must refuse it unless explicitly told to render a fixture.

The fake stream mimics Claude Code's `stream-json --verbose` shape: an `init` event (whose MCP
servers match the condition, so the contamination check passes), assistant turns whose usage grows,
tool uses and results, and a `result` event.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from typing import Any, Callable, Dict, List, Optional

import _support  # noqa: F401  (src/ on the path + provisioning refusal)

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from bench.g2 import conditions as C  # noqa: E402
from bench.g2.agent import FakeAgent  # noqa: E402
from bench.g2.judge import FakeJudge  # noqa: E402
from bench.g2.runner import run_matrix  # noqa: E402
from bench.g2.tasks import load_tasks  # noqa: E402
from bench.g2.workspace import CopyCloner  # noqa: E402


def init_event(condition: str, tools: Optional[List[str]] = None) -> Dict[str, Any]:
    mokata = condition != "bare"
    base = ["Bash", "Read", "Edit", "Write", "Grep", "Glob"]
    if tools is None:
        tools = base + (["mcp__mokata__query", "mcp__mokata__recall", "mcp__mokata__remember",
                         "mcp__mokata__approve"] if mokata else [])
    return {"type": "system", "subtype": "init", "session_id": "s", "model": "fake-model",
            "tools": tools,
            "mcp_servers": [{"name": "mokata", "status": "connected"}] if mokata else []}


def assistant(mid: str, ctx: int, *, text: str = "", tools: Optional[List[Dict[str, Any]]] = None,
              out: int = 50, cache_read: int = 0) -> Dict[str, Any]:
    content: List[Dict[str, Any]] = []
    if text:
        content.append({"type": "text", "text": text})
    for i, t in enumerate(tools or []):
        content.append({"type": "tool_use", "id": f"{mid}-t{i}", "name": t["name"],
                        "input": t.get("input", {})})
    return {"type": "assistant", "message": {
        "id": mid, "role": "assistant", "content": content,
        "usage": {"input_tokens": ctx - cache_read, "cache_read_input_tokens": cache_read,
                  "cache_creation_input_tokens": 0, "output_tokens": out}}}


def tool_result(tid: str, text: str, is_error: bool = False) -> Dict[str, Any]:
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": is_error}]}}


def result_event(turns: int, cost: float = 0.1) -> Dict[str, Any]:
    return {"type": "result", "subtype": "success", "is_error": False, "num_turns": turns,
            "duration_ms": 1000 * turns, "total_cost_usd": cost}


def default_script(workdir: str, prompt: str, condition: str, repeat: int) -> List[Dict[str, Any]]:
    """A plausible trajectory: mokata-on starts with a bigger first turn (tool schemas) but grows
    slower; it uses the graph where bare greps; one mokata run sees a secret block."""
    mokata = condition != "bare"
    first = 12000 if mokata else 9000
    step = 900 if mokata else 2000
    nav = ({"name": "mcp__mokata__query", "input": {"q": "callers of line_total"}} if mokata
           else {"name": "Grep", "input": {"pattern": "line_total"}})
    ev = [init_event(condition),
          {"type": "user", "message": {"role": "user", "content": prompt}},
          assistant("m1", first, text="Plan: find callers, then edit.", tools=[nav]),
          tool_result("m1-t0", "shop/cart.py\nshop/report.py"),
          assistant("m2", first + step, tools=[{"name": "Edit", "input": {
              "file_path": "shop/pricing.py", "new_string": "from decimal import Decimal"}}]),
          tool_result("m2-t0", "ok"),
          assistant("m3", first + 2 * step, text="Done; tests pass.")]
    if mokata and repeat == 1:
        ev.insert(5, tool_result("m2-t1", "BLOCKED [pattern/aws-key] AKIA… in shop/pricing.py\n"
                                          "secret blocked"))
    ev.append(result_event(3))
    return ev


def passing_judge(task, transcript: str) -> str:
    return json.dumps({"criteria": {c.id: {"pass": True, "why": "fixture"} for c in task.rubric},
                       "overall_pass": True})


def build_fixture_archive(out_dir: Optional[str] = None, *, repeats: int = 5,
                          conditions: Optional[List[Any]] = None, task_ids: Optional[List[str]] = None,
                          script: Callable = default_script, decide: Callable = passing_judge) -> str:
    out_dir = out_dir or os.path.join(tempfile.mkdtemp(prefix="g2-fixture-"), "run")
    conds = conditions or [C.bare(), C.mokata_on()]
    tasks = load_tasks(only=task_ids)
    run_matrix(conditions=conds, tasks=tasks, repeats=repeats, out_dir=out_dir,
               agent=FakeAgent(script), judge=FakeJudge(decide), cloner=CopyCloner(),
               setup_runner=lambda argv, cwd: 0)
    return out_dir
