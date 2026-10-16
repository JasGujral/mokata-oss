"""STUDY.md — the publishable credibility artifact, generated ONLY from a run archive.

Every number in it is computed from the archive by `metrics.aggregate`; nothing is typed. A fixture
archive renders with a FIXTURE banner on top (useful for reviewing the template), and the
validator's problems — contamination, missing runs, too few repeats — are printed IN the study
rather than filtered out of it.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from typing import Any, List, Optional

from .archive import RunArchive
from .metrics import aggregate

FIXTURE_BANNER = ("> ⚠ **FIXTURE RUN — NOT EVIDENCE.** This study was generated from a fixture "
                  "archive (fake agent and/or fake judge). No number below measures mokata.\n")


def _fmt(v: Any, pct: bool = False, digits: int = 0) -> str:
    if v is None:
        return "—"
    if pct:
        return f"{v * 100:.0f}%"
    if isinstance(v, float):
        return f"{v:,.{digits}f}"
    return f"{v:,}" if isinstance(v, int) else str(v)


def render_study(arch: RunArchive, extra_sections: Optional[List[str]] = None) -> str:
    m = arch.manifest
    agg = aggregate(arch)
    problems = arch.validate(require_real=True)
    out: List[str] = ["# G2 study — mokata vs bare Claude Code", ""]
    if not arch.is_real:
        out += [FIXTURE_BANNER, ""]
    out += [
        f"- **Run:** `{arch.path}` · kind **{arch.kind}** · started {m.get('started')} · "
        f"finished {m.get('finished')}",
        f"- **Agent:** {m['agent']['id']} `{m['agent'].get('version', '')}` (headless, the "
        f"operator's own login; API-key env scrubbed)",
        f"- **Judge (separate, full trajectory):** {m['judge']['id']} · model "
        f"`{m['judge'].get('model') or 'default'}`",
        f"- **mokata:** {m.get('mokata_version')} · harness {m.get('harness_version')} · "
        f"{m.get('platform')}",
        f"- **Matrix:** {len(arch.condition_names)} conditions × {len(arch.task_ids)} tasks × "
        f"{m.get('repeats')} repeats, fresh `{m.get('cloner')}` clone per run, repeats interleaved",
        "",
        "## Conditions", "",
        "| condition | surface | fingerprint | description |", "|---|---|---|---|",
    ]
    for c in m["conditions"]:
        out.append(f"| `{c['name']}` | {c['surface']} | `{c['fingerprint']}` | {c['description']} |")
    out += ["", "## Tasks", "", "| task | mechanism | repo | rubric |", "|---|---|---|---|"]
    for t in m["tasks"]:
        repo = t["repo"].get("url", "") + ("@" + t["repo"]["ref"] if t["repo"].get("ref") else "") \
            or "template:" + t["repo"].get("template", "")
        out.append(f"| `{t['id']}` | {t['mechanism']} | `{repo}` | "
                   + "; ".join(f"{c['id']} ({c['kind']})" for c in t["rubric"]) + " |")
    out += ["", "## Headline (per condition)", "",
            "| condition | graded | pass rate | ungradable | defects caught | secrets blocked | "
            "tokens saved vs bare |", "|---|---|---|---|---|---|---|"]
    for c, x in agg["per_condition"].items():
        out.append(f"| `{c}` | {x['graded']}/{x['runs']} | {_fmt(x['pass_rate'], pct=True)} | "
                   f"{x['ungradable']} | {x['defects_caught']} | {x['secrets_blocked']} | "
                   f"{_fmt(x['tokens_saved_vs_bare'])} |")
    out += ["", "## Per cell (medians over repeats)", "",
            "| condition | task | pass | turns | first-turn ctx | growth/turn | max ctx | "
            "total re-sent ctx | notional $ |", "|---|---|---|---|---|---|---|---|---|"]
    for x in agg["cells"]:
        out.append(f"| `{x['condition']}` | `{x['task']}` | {x['passed']}/{x['graded']} | "
                   f"{_fmt(x['median_turns'], digits=1)} | {_fmt(x['median_first_turn_context'])} | "
                   f"{_fmt(x['median_growth_per_turn'], digits=0)} | {_fmt(x['median_max_context'])} | "
                   f"{_fmt(x['median_total_context'])} | {_fmt(x['median_notional_cost_usd'], digits=2)} |")
    out += ["", "## Per mechanism (pass / graded)", ""]
    out.append("| mechanism | " + " | ".join(f"`{c}`" for c in arch.condition_names) + " |")
    out.append("|---|" + "---|" * len(arch.condition_names))
    for mech, row in agg["per_mechanism"].items():
        out.append(f"| {mech} | " + " | ".join(f"{row[c]['passed']}/{row[c]['graded']}"
                                                for c in arch.condition_names) + " |")
    out += ["", "## How the numbers are derived", "",
            "- **Turns** = distinct model calls in the raw stream. **Context per turn** = "
            "input + cache-read + cache-creation tokens of that call (what the turn re-sent). "
            "**Total re-sent context** = their sum.",
            "- **Notional $** is Claude Code's API-equivalent estimate; on a subscription login "
            "nothing is billed per token.",
            "- **Tokens saved** = Σ over tasks of median total re-sent context (bare − condition); "
            "signed, never clipped.",
            "- **Secrets blocked** is mechanical (mokata's `BLOCKED [layer/kind]` lines in tool "
            "results); **defects caught** is judged (rubric criteria of kind `defect`).",
            "- Ungradable verdicts are counted, never scored as pass or fail.",
            ""]
    from .continuity import continuity_report, render_markdown
    if any(t.get("decisions") for t in m["tasks"]):
        out += [render_markdown(continuity_report(arch)), ""]
    if "mokata-lean" in arch.condition_names:
        from .lean import lean_verdict, render_markdown as lean_md
        out += [lean_md(lean_verdict(arch)), ""]
    for section in extra_sections or []:
        out += [section, ""]
    out += ["## Validation", ""]
    out += ([f"- ❌ {p}" for p in problems] if problems else
            ["- ✅ complete real run: every cell has ≥5 runs with raw stream, parse, verdict; "
             "no contamination; no harness errors."])
    out += ["", "## Reproduce", "",
            "Every run directory holds the raw `stream.jsonl`, `trajectory.json`, `verdict.json`, "
            "`judge_raw.txt` and `meta.json` (`{condition}/{task}/run{N}/`). Regenerate this file "
            "with:", "", "```", f"python -m bench.g2 study --run {arch.path}", "```", ""]
    return "\n".join(out)
