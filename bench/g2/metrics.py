"""G2 aggregation: per-cell results and the PUBLIC COUNTERS, all derived from the archive.

Public counters (doc 84 G2 row: "defects caught, secrets blocked, tokens saved"), each with its
derivation stated so a page that prints it can print the derivation too:
  * defects_caught[c]  — graded runs in condition c where the judge passed a rubric criterion of
                         kind `defect` (a planted defect the agent found and fixed).
  * secrets_blocked[c] — tool results in condition c carrying mokata's secret-guard block line
                         (`BLOCKED [layer/kind] …`, `govern/secret_ignore.render_block`):
                         MECHANICAL, read from the raw trajectory, not judged.
  * tokens_saved[c]    — Σ over tasks of (median total re-sent context, bare) − (median, c).
                         SIGNED: a negative number is reported as negative, never clipped.

Medians, not means: a single runaway run (a loop to max-turns) should not move a cell.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import re
import statistics
from typing import Any, Dict, List, Optional

from .archive import RunArchive, RunRecord

SECRET_BLOCK = re.compile(r"^BLOCKED \[[^\]/]+/[^\]]+\]", re.M)


def _median(xs: List[float]) -> Optional[float]:
    return statistics.median(xs) if xs else None


def secrets_blocked_in(rec: RunRecord) -> int:
    return sum(len(SECRET_BLOCK.findall(r.get("text") or "")) for r in rec.trajectory.tool_results)


def defects_caught_in(rec: RunRecord, task_rubric: List[Dict[str, Any]]) -> int:
    if not rec.graded:
        return 0
    crit = rec.verdict.get("criteria") or {}
    return sum(1 for c in task_rubric if c["kind"] == "defect" and crit.get(c["id"]) is True)


def cell_stats(arch: RunArchive, condition: str, task: str) -> Dict[str, Any]:
    recs = arch.usable(arch.cell(condition, task))
    graded = [r for r in recs if r.graded]
    trs = [r.trajectory for r in recs]
    rubric = next(t["rubric"] for t in arch.manifest["tasks"] if t["id"] == task)
    return {
        "condition": condition, "task": task, "runs": len(recs),
        "graded": len(graded), "ungradable": len(recs) - len(graded),
        "passed": sum(1 for r in graded if r.passed),
        "pass_rate": (sum(1 for r in graded if r.passed) / len(graded)) if graded else None,
        "median_turns": _median([t.num_turns for t in trs]),
        "median_total_context": _median([t.total_context_tokens for t in trs]),
        "median_max_context": _median([t.max_context_tokens for t in trs]),
        "median_first_turn_context": _median([t.first_turn_context_tokens for t in trs]),
        "median_growth_per_turn": _median([t.growth_per_turn for t in trs]),
        "median_output_tokens": _median([t.output_tokens for t in trs]),
        "median_notional_cost_usd": _median([t.result.get("total_cost_usd") for t in trs
                                             if isinstance(t.result.get("total_cost_usd"), (int, float))]),
        "median_duration_ms": _median([t.result.get("duration_ms") for t in trs
                                       if isinstance(t.result.get("duration_ms"), (int, float))]),
        "secrets_blocked": sum(secrets_blocked_in(r) for r in recs),
        "defects_caught": sum(defects_caught_in(r, rubric) for r in recs),
    }


def aggregate(arch: RunArchive) -> Dict[str, Any]:
    cells = [cell_stats(arch, c, t) for c, t, _ in arch.cells()]
    by = {(x["condition"], x["task"]): x for x in cells}
    per_condition: Dict[str, Dict[str, Any]] = {}
    for c in arch.condition_names:
        mine = [x for x in cells if x["condition"] == c]
        graded = sum(x["graded"] for x in mine)
        passed = sum(x["passed"] for x in mine)
        saved = None
        if c != "bare":
            deltas = [by[("bare", t)]["median_total_context"] - by[(c, t)]["median_total_context"]
                      for t in arch.task_ids
                      if by[("bare", t)]["median_total_context"] is not None
                      and by[(c, t)]["median_total_context"] is not None]
            saved = sum(deltas) if deltas else None
        per_condition[c] = {
            "runs": sum(x["runs"] for x in mine), "graded": graded,
            "ungradable": sum(x["ungradable"] for x in mine), "passed": passed,
            "pass_rate": passed / graded if graded else None,
            "defects_caught": sum(x["defects_caught"] for x in mine),
            "secrets_blocked": sum(x["secrets_blocked"] for x in mine),
            "tokens_saved_vs_bare": saved,
        }
    mechanisms: Dict[str, Dict[str, Any]] = {}
    for t in arch.manifest["tasks"]:
        for c in arch.condition_names:
            x = by[(c, t["id"])]
            m = mechanisms.setdefault(t["mechanism"], {}).setdefault(c, {"graded": 0, "passed": 0})
            m["graded"] += x["graded"]
            m["passed"] += x["passed"]
    return {"cells": cells, "per_condition": per_condition, "per_mechanism": mechanisms,
            "kind": arch.kind, "repeats": arch.manifest.get("repeats")}
