"""PIPE-TOKEN-AUDIT — the closing instrument (doc 84 PIPE-TOKEN-AUDIT; plan 111 closing stage).

A report generator over a G2 run archive. It SPENDS G2's instrument (turns + per-turn context) on
two questions, and adds no new measurement mechanism:

1. **Is graph usage wired, not just built?** — the per-phase GRAPH-COMPLIANCE TABLE. Each model turn
   is assigned to the mokata phase in force (brainstorm · spec · test · develop · review · ship; the
   phase is entered by a Skill / slash-command / phase-specific MCP call). Per phase, navigation
   calls are classed as GRAPH (`mcp__mokata__query`/`decompose`/`ci_check`/`spec_check`,
   `mokata query` in Bash) or GREP FLOOR (Grep, Glob, `grep`/`rg`/`find` in Bash). A phase that
   navigated only on the grep floor is named — exit criterion 5: "no phase silently on the grep
   floor".
2. **Is context discipline real?** — per phase: turns, median context per turn, tool-result bytes,
   and mokata's own injected context (hook/system entries in the stream) against the documented
   budgets (`settings.context`: per-turn recall pack 300 tokens, SessionStart briefing 2000).
   Plus the SYSTEM-PROMPT-SIZE TRACKER: the first turn's context (system prompt + tool schemas +
   first prompt) and the number of tools offered, per condition — and, with `--history`, appended
   release over release so growth is visible (real runs only).

⚠ THE NAMED REPORT NEEDS A REAL RUN. Over a fixture archive this renders a FIXTURE-bannered
template and refuses to append to the history; the report that becomes G2's standing baseline can
only be produced from `python -m bench.g2 run … --yes` output.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import re
import statistics
from typing import Any, Dict, List, Optional

from .archive import ArchiveError, RunArchive
from .streamparse import Trajectory

PHASES = ("brainstorm", "spec", "test", "develop", "review", "ship")
UNPHASED = "(unphased)"
#: Documented budgets (src/mokata/context_budget.py LIMITS defaults, as shipped).
TURN_RECALL_BUDGET_TOKENS = 300
BRIEFING_BUDGET_TOKENS = 2000

GRAPH_MCP = {"mcp__mokata__query", "mcp__mokata__decompose", "mcp__mokata__ci_check",
             "mcp__mokata__spec_check"}
GREP_TOOLS = {"Grep", "Glob"}
_BASH_GREP = re.compile(r"(^|[\s|;&(])(grep|egrep|rg|ag|ack|find)\s")
_BASH_GRAPH = re.compile(r"\bmokata\s+query\b")
_PHASE_MCP = {"mcp__mokata__spec_emit": "spec", "mcp__mokata__spec_check": "spec",
              "mcp__mokata__spec_amend": "spec", "mcp__mokata__spec_show": "spec",
              "mcp__mokata__review_record": "review", "mcp__mokata__review_status": "review",
              "mcp__mokata__run_finish": "ship"}
_PHASE_WORD = re.compile(r"(?:^|[/:\s])(" + "|".join(PHASES) + r")\b")


def _tokens(text: str) -> int:
    return -(-len(text or "") // 4)        # mokata's estimate (~4 chars/token), kept dependency-free


def phase_signal(use: Dict[str, Any]) -> Optional[str]:
    name = use.get("name", "")
    if name in _PHASE_MCP:
        return _PHASE_MCP[name]
    if name in ("Skill", "SlashCommand"):
        blob = json.dumps(use.get("input") or {}).lower()
        m = _PHASE_WORD.search(blob.replace('"', " "))
        return m.group(1) if m else None
    return None


def nav_class(use: Dict[str, Any]) -> Optional[str]:
    name = use.get("name", "")
    if name in GRAPH_MCP:
        return "graph"
    if name in GREP_TOOLS:
        return "grep"
    if name == "Bash":
        cmd = str((use.get("input") or {}).get("command", ""))
        if _BASH_GRAPH.search(cmd):
            return "graph"
        if _BASH_GREP.search(cmd):
            return "grep"
    return None


def phases_of(tr: Trajectory) -> Dict[str, Dict[str, Any]]:
    """Per phase for one run: turns, contexts, graph/grep calls, tool-result bytes, injected tokens."""
    out: Dict[str, Dict[str, Any]] = {}
    current = UNPHASED
    turn_phase: Dict[int, str] = {}
    for t in tr.turns:
        turn_phase[t.index] = current
        row = out.setdefault(current, {"turns": 0, "contexts": [], "graph": 0, "grep": 0,
                                       "result_bytes": 0, "injected": []})
        row["turns"] += 1
        row["contexts"].append(t.context_tokens)
        for use in t.tool_uses:
            nc = nav_class(use)
            if nc:
                row[nc] += 1
        for use in t.tool_uses:          # a phase signal takes effect from the NEXT turn
            sig = phase_signal(use)
            if sig:
                current = sig
    for r in tr.tool_results:
        ph = turn_phase.get(r["turn"], UNPHASED)
        out.setdefault(ph, {"turns": 0, "contexts": [], "graph": 0, "grep": 0, "result_bytes": 0,
                            "injected": []})["result_bytes"] += len((r.get("text") or "").encode("utf-8"))
    for e in tr.timeline:
        if e["role"].startswith("system:"):
            ph = turn_phase.get(e["turn"] + 1, UNPHASED)
            out.setdefault(ph, {"turns": 0, "contexts": [], "graph": 0, "grep": 0,
                                "result_bytes": 0, "injected": []})["injected"].append(_tokens(e["text"]))
    return out


def status_of(graph: int, grep: int) -> str:
    if grep and not graph:
        return "GREP FLOOR"
    if grep and graph:
        return "mixed"
    if graph:
        return "graph"
    return "no navigation"


def _med(xs: List[float]) -> Optional[float]:
    return statistics.median(xs) if xs else None


def audit(arch: RunArchive) -> Dict[str, Any]:
    by_cond: Dict[str, Any] = {}
    for c in arch.condition_names:
        agg: Dict[str, Dict[str, Any]] = {}
        first_turns, tools_offered, briefings = [], [], []
        for rec in arch.usable([r for r in arch.records if r.condition == c]):
            tr = rec.trajectory
            first_turns.append(tr.first_turn_context_tokens)
            tools_offered.append(len(tr.tools_offered))
            first_inject = [e for e in tr.timeline if e["role"].startswith("system:") and e["turn"] == 0]
            if first_inject:
                briefings.append(sum(_tokens(e["text"]) for e in first_inject))
            for ph, row in phases_of(tr).items():
                a = agg.setdefault(ph, {"runs": 0, "grep_floor_runs": 0, "graph": 0, "grep": 0,
                                        "turns": [], "ctx": [], "result_bytes": [],
                                        "injected_max": [], "over_turn_budget": 0})
                a["runs"] += 1
                a["graph"] += row["graph"]
                a["grep"] += row["grep"]
                a["grep_floor_runs"] += 1 if status_of(row["graph"], row["grep"]) == "GREP FLOOR" else 0
                a["turns"].append(row["turns"])
                a["ctx"].extend(row["contexts"])
                a["result_bytes"].append(row["result_bytes"])
                inj = row["injected"]
                a["injected_max"].append(max(inj) if inj else 0)
                a["over_turn_budget"] += sum(1 for x in inj if x > TURN_RECALL_BUDGET_TOKENS)
        table = []
        order = [UNPHASED] + list(PHASES)
        for ph in sorted(agg, key=lambda p: order.index(p) if p in order else 99):
            a = agg[ph]
            table.append({"phase": ph, "runs": a["runs"], "graph_calls": a["graph"],
                          "grep_calls": a["grep"], "status": status_of(a["graph"], a["grep"]),
                          "grep_floor_runs": a["grep_floor_runs"],
                          "median_turns": _med(a["turns"]), "median_ctx_per_turn": _med(a["ctx"]),
                          "median_result_bytes": _med(a["result_bytes"]),
                          "max_injected_tokens": max(a["injected_max"]) if a["injected_max"] else 0,
                          "injections_over_turn_budget": a["over_turn_budget"]})
        by_cond[c] = {"phases": table,
                      "system_prompt": {"median_first_turn_context": _med(first_turns),
                                        "median_tools_offered": _med(tools_offered),
                                        "median_briefing_tokens": _med(briefings)}}
    silent = [f"{c}:{row['phase']}" for c, v in by_cond.items() if c != "bare"
              for row in v["phases"] if row["phase"] != UNPHASED and row["grep_floor_runs"]]
    return {"by_condition": by_cond, "phases_on_grep_floor": silent, "kind": arch.kind,
            "mokata_version": arch.manifest.get("mokata_version")}


def render_report(arch: RunArchive, history: Optional[List[Dict[str, Any]]] = None) -> str:
    rep = audit(arch)
    out = ["# PIPE-TOKEN-AUDIT", ""]
    if not arch.is_real:
        out += ["> ⚠ **FIXTURE RUN — NOT THE AUDIT.** This is the report TEMPLATE rendered over a "
                "fixture archive. The named PIPE-TOKEN-AUDIT report (G2's standing baseline) "
                "requires a real G2 run.", ""]
    out += [f"Run `{arch.path}` · mokata {rep['mokata_version']} · kind {rep['kind']}", "",
            "## 1 · Graph-compliance table (per phase)", ""]
    for c, v in rep["by_condition"].items():
        out += [f"### `{c}`", "", "| phase | runs | graph calls | grep-floor calls | status | "
                "runs on grep floor | median turns | median ctx/turn | median tool-result bytes | "
                "max injected tokens | injections > 300 |", "|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in v["phases"]:
            st = f"**{r['status']}**" if r["status"] == "GREP FLOOR" else r["status"]
            out.append(f"| {r['phase']} | {r['runs']} | {r['graph_calls']} | {r['grep_calls']} | {st} | "
                       f"{r['grep_floor_runs']} | {r['median_turns']} | {r['median_ctx_per_turn']} | "
                       f"{r['median_result_bytes']} | {r['max_injected_tokens']} | "
                       f"{r['injections_over_turn_budget']} |")
        out.append("")
    silent = rep["phases_on_grep_floor"]
    out += ["**Verdict:** " + ("❌ phases on the grep floor in at least one run: "
                               + ", ".join(f"`{s}`" for s in silent) if silent else
                               "✅ no mokata phase navigated on the grep floor alone."), "",
            "## 2 · System-prompt size tracker", "",
            "| condition | median first-turn context | median tools offered | median briefing tokens "
            f"(budget {BRIEFING_BUDGET_TOKENS}) |", "|---|---|---|---|"]
    for c, v in rep["by_condition"].items():
        s = v["system_prompt"]
        out.append(f"| `{c}` | {s['median_first_turn_context']} | {s['median_tools_offered']} | "
                   f"{s['median_briefing_tokens']} |")
    if history:
        out += ["", "### Release over release", "", "| mokata | date | condition | first-turn context | "
                "tools offered |", "|---|---|---|---|---|"]
        for h in history:
            for c, s in h["system_prompt"].items():
                out.append(f"| {h['mokata_version']} | {h['date']} | `{c}` | "
                           f"{s['median_first_turn_context']} | {s['median_tools_offered']} |")
    out += ["", "Injected context is what `stream-json` shows of mokata's hook output (a lower "
            "bound). Phases are entered by Skill / slash-command / phase-specific MCP calls; turns "
            "before the first are `(unphased)`.", ""]
    return "\n".join(out)


def append_history(arch: RunArchive, history_path: str) -> List[Dict[str, Any]]:
    if not arch.is_real or arch.validate(require_real=True):
        raise ArchiveError("REFUSING to append a fixture or incomplete run to the system-prompt "
                           "history — the tracker is release-over-release EVIDENCE.")
    rows: List[Dict[str, Any]] = []
    if os.path.exists(history_path):
        with open(history_path, encoding="utf-8") as fh:
            rows = json.load(fh)
    rep = audit(arch)
    rows.append({"mokata_version": rep["mokata_version"], "date": arch.manifest.get("finished"),
                 "run": os.path.basename(arch.path),
                 "system_prompt": {c: v["system_prompt"] for c, v in rep["by_condition"].items()}})
    with open(history_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(rows, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return rows
