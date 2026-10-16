"""CTX-CONT (0.0.23 D10) — "context continuity %": does a decision made EARLY survive into the
context develop works in? Computed from the run archive only.

Seeded scenario: a task declares `decisions`, each stated in the task prompt (turn 0) with `probe`
terms. For each run:

  develop turn   the first model turn that WRITES (Edit / Write / MultiEdit / NotebookEdit, or the
                 task's `develop_marker` tool) — the turn whose context the decision must reach.
  at risk        a compaction boundary fell between the decision (turn 0) and the develop turn. With
                 no compaction the prompt is still verbatim in context, so survival is trivially
                 true — counted, but reported separately so a 100% made of trivial cases cannot
                 pass for a tested one.
  present        not at risk, OR — after the last boundary before the develop turn and up to it —
                 something in the stream (compaction summary, hook injection, tool result, the
                 agent's own text) carries every probe term. This is CONTINUITY.
  applied        develop's write inputs (from the develop turn on) carry the probe terms: the
                 decision was not only remembered but acted on. Reported beside continuity, never
                 folded into it.

⚠ LOWER BOUND, stated: `present` sees only what the stream shows. Context that reaches the model
without appearing in `stream-json` (if any) is invisible here, so a reported loss may be a loss of
VISIBILITY. The raw stream is archived; the measurement can be redone if that changes.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Sequence

from .archive import RunArchive
from .streamparse import Trajectory

WRITE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


@dataclass
class DecisionResult:
    decision: str
    develop_turn: Optional[int]
    at_risk: bool
    present: Optional[bool]          # None when the run never reached develop
    applied: Optional[bool]


def _has_all(text: str, probe: Sequence[str]) -> bool:
    low = text.lower()
    return all(p in low for p in probe)


def _is_write(name: str, marker: Optional[str]) -> bool:
    return name in WRITE_TOOLS or (marker is not None and name == marker)


def develop_turn(tr: Trajectory, marker: Optional[str] = None) -> Optional[int]:
    for t in tr.turns:
        if any(_is_write(u["name"], marker) for u in t.tool_uses):
            return t.index
    return None


def measure_run(tr: Trajectory, decisions: List[Dict[str, Any]],
                marker: Optional[str] = None) -> List[DecisionResult]:
    dev = develop_turn(tr, marker)
    out = []
    for d in decisions:
        probe = [p.lower() for p in d["probe"]]
        if dev is None:
            out.append(DecisionResult(d["id"], None, False, None, None))
            continue
        # a boundary recorded at `turn` k means k turns completed before it; it precedes turn k+1
        boundaries = [b for b in tr.compactions if b < dev]
        at_risk = bool(boundaries)
        if not at_risk:
            present = True
        else:
            last = max(boundaries)
            window = [e for e in tr.timeline if last <= e["turn"] < dev]
            present = any(_has_all(e["text"], probe) for e in window)
        writes = [json.dumps(u["input"], sort_keys=True) for t in tr.turns if t.index >= dev
                  for u in t.tool_uses if _is_write(u["name"], marker)]
        applied = any(_has_all(w, probe) for w in writes)
        out.append(DecisionResult(d["id"], dev, at_risk, present, applied))
    return out


def _rate(xs: List[bool]) -> Optional[float]:
    return sum(1 for x in xs if x) / len(xs) if xs else None


def continuity_report(arch: RunArchive) -> Dict[str, Any]:
    tasks = {t["id"]: t for t in arch.manifest["tasks"] if t.get("decisions")}
    per_condition: Dict[str, Dict[str, Any]] = {}
    rows = []
    for c in arch.condition_names:
        results: List[DecisionResult] = []
        for tid, t in tasks.items():
            for rec in arch.usable(arch.cell(c, tid)):
                for r in measure_run(rec.trajectory, t["decisions"], t.get("develop_marker")):
                    results.append(r)
                    rows.append(dict(asdict(r), condition=c, task=tid, repeat=rec.repeat))
        reached = [r for r in results if r.present is not None]
        risky = [r for r in reached if r.at_risk]
        per_condition[c] = {
            "decisions_measured": len(results), "reached_develop": len(reached),
            "continuity_pct": _rate([r.present for r in reached]),
            "at_risk": len(risky), "continuity_pct_at_risk": _rate([r.present for r in risky]),
            "applied_pct": _rate([r.applied for r in reached]),
        }
    return {"per_condition": per_condition, "rows": rows, "tasks": sorted(tasks)}


def render_markdown(rep: Dict[str, Any]) -> str:
    def pct(v):
        return "—" if v is None else f"{v * 100:.0f}%"
    lines = ["## Context continuity (CTX-CONT)", ""]
    if not rep["tasks"]:
        return "\n".join(lines + ["No task in this run declares `decisions`; continuity not measured."])
    lines += [f"Seeded tasks: {', '.join('`' + t + '`' for t in rep['tasks'])}. A decision stated at "
              "turn 0 must reach the first develop (write) turn's context.", "",
              "| condition | measured | reached develop | **continuity %** | at risk (compacted) | "
              "continuity % when at risk | applied % |", "|---|---|---|---|---|---|---|"]
    for c, x in rep["per_condition"].items():
        lines.append(f"| `{c}` | {x['decisions_measured']} | {x['reached_develop']} | "
                     f"**{pct(x['continuity_pct'])}** | {x['at_risk']} | "
                     f"{pct(x['continuity_pct_at_risk'])} | {pct(x['applied_pct'])} |")
    if all(x["at_risk"] == 0 for x in rep["per_condition"].values()):
        lines += ["", "⚠ No run compacted before develop, so every survival above is the trivial "
                  "case (the prompt was still in context). Continuity is UNTESTED by this run; it "
                  "needs a seeded task long enough to compact."]
    lines += ["", "`present` is a lower bound: it sees only what `stream-json` shows."]
    return "\n".join(lines)
