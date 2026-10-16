"""The PUBLIC PROOF PAGE (0.0.23 D9) — G2's real counters, rendered from a real run archive.

⛔ It REFUSES to render without a complete real run (`RunArchive.require_real`): no fixture, no
partial matrix, no contaminated run, fewer than 5 repeats → no page. There is no flag that turns a
fixture into a real run; `allow_fixture=True` exists for tests and template review only, and stamps
FIXTURE over the page and every counter so a screenshot of it cannot pass as evidence.

Every number comes from `metrics.aggregate` over the archive, and the page prints the archive's
content DIGEST so a reader can check that the page and a published archive are the same run.

Self-contained static HTML: no scripts, no external assets, light + dark via `prefers-color-scheme`.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import hashlib
import html
import os
from typing import Any, List

from .archive import RunArchive
from .metrics import aggregate


def archive_digest(arch: RunArchive) -> str:
    """sha256 over run.json and every run's verdict/trajectory/meta, in a fixed order."""
    h = hashlib.sha256()
    paths = [os.path.join(arch.path, "run.json")]
    for rec in arch.records:
        for f in ("meta.json", "trajectory.json", "verdict.json"):
            paths.append(os.path.join(rec.path, f))
    for p in paths:
        h.update(os.path.relpath(p, arch.path).replace(os.sep, "/").encode("utf-8"))
        if os.path.exists(p):
            with open(p, "rb") as fh:
                h.update(fh.read().replace(b"\r\n", b"\n"))
    return h.hexdigest()


def _e(v: Any) -> str:
    return html.escape("—" if v is None else str(v))


def _pct(v: Any) -> str:
    return "—" if v is None else f"{v * 100:.0f}%"


def _num(v: Any) -> str:
    if v is None:
        return "—"
    return f"{v:,.0f}" if isinstance(v, float) else f"{v:,}"


CSS = """
:root{--bg:#fbfbf8;--fg:#1d1d1b;--muted:#6b6b66;--card:#fff;--line:#e3e2dc;--accent:#1f7a4d;--warn:#b3261e}
@media (prefers-color-scheme:dark){:root{--bg:#121412;--fg:#e8e8e3;--muted:#9a9a93;--card:#1b1e1b;--line:#2c302c;--accent:#4cc38a;--warn:#ff8a80}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:980px;margin:0 auto;padding:32px 16px}h1{font-size:1.7rem;margin:0 0 4px}h2{font-size:1.15rem;margin:32px 0 8px}
.muted{color:var(--muted)}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.k{font-size:.8rem;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}.v{font-size:1.7rem;font-variant-numeric:tabular-nums}
.how{font-size:.82rem;color:var(--muted)}table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:.92rem}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line)}.scroll{overflow-x:auto}
.fixture{border:3px solid var(--warn);color:var(--warn);padding:12px;border-radius:10px;font-weight:700;margin:12px 0}
code{font-size:.85em;overflow-wrap:anywhere}
"""


def render_page(arch: RunArchive, *, allow_fixture: bool = False) -> str:
    if not (allow_fixture and not arch.is_real):
        arch.require_real()                       # raises ArchiveError — the refusal
    fixture = not arch.is_real
    m = arch.manifest
    agg = aggregate(arch)
    tag = " [FIXTURE]" if fixture else ""
    out: List[str] = [
        "<!doctype html>", '<html lang="en">', "<head>", '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>mokata proof{' (FIXTURE)' if fixture else ''}</title>",
        f"<style>{CSS}</style>", "</head>",
        f'<body data-kind="{_e(arch.kind)}"><main>',
        "<h1>mokata, measured</h1>",
        '<p class="muted">G2: the same tasks, run by Claude Code with and without mokata, '
        f"{_e(m.get('repeats'))} repeats each, graded by a separate judge on the full trajectory.</p>",
    ]
    if fixture:
        out.append('<div class="fixture">FIXTURE RUN — NOT EVIDENCE. Rendered from a fixture '
                   "archive (fake agent and/or judge). No number on this page measures mokata.</div>")
    out.append("<h2>Counters</h2>")
    for c, x in agg["per_condition"].items():
        if c == "bare":
            continue
        cards = [
            ("pass rate", f"{_pct(x['pass_rate'])} vs bare {_pct(agg['per_condition']['bare']['pass_rate'])}",
             "judge-graded runs whose every outcome criterion passed"),
            ("defects caught", f"{x['defects_caught']}",
             "runs where the judge passed a planted-defect criterion"),
            ("secrets blocked", f"{x['secrets_blocked']}",
             "mokata secret-guard block lines in tool results (mechanical)"),
            ("tokens saved vs bare", _num(x["tokens_saved_vs_bare"]),
             "Σ tasks: median re-sent context, bare − mokata (signed)"),
        ]
        out.append(f"<h2>Condition <code>{_e(c)}</code>{_e(tag)}</h2><div class=\"grid\">")
        for k, v, how in cards:
            out.append(f'<div class="card"><div class="k">{_e(k)}{_e(tag)}</div>'
                       f'<div class="v">{_e(v)}</div><div class="how">{_e(how)}</div></div>')
        out.append("</div>")
    out += ["<h2>Per task (medians)</h2>", '<div class="scroll"><table>',
            "<tr><th>condition</th><th>task</th><th>pass</th><th>turns</th>"
            "<th>growth / turn</th><th>total re-sent context</th></tr>"]
    for x in agg["cells"]:
        out.append(f"<tr><td><code>{_e(x['condition'])}</code></td><td><code>{_e(x['task'])}</code></td>"
                   f"<td>{x['passed']}/{x['graded']}</td><td>{_num(x['median_turns'])}</td>"
                   f"<td>{_num(x['median_growth_per_turn'])}</td><td>{_num(x['median_total_context'])}</td></tr>")
    out += ["</table></div>", "<h2>Provenance</h2>", "<ul>",
            f"<li>Run {_e(m.get('started'))} → {_e(m.get('finished'))}; mokata "
            f"{_e(m.get('mokata_version'))}; harness {_e(m.get('harness_version'))}</li>",
            f"<li>Agent: {_e(m['agent']['id'])} <code>{_e(m['agent'].get('version'))}</code> "
            "(the operator's own Claude login — no API key)</li>",
            f"<li>Judge: {_e(m['judge']['id'])} <code>{_e(m['judge'].get('model') or 'default')}</code></li>",
            f"<li>Archive digest (sha256): <code>{archive_digest(arch)}</code></li>",
            "<li>Every number here is regenerated by <code>python -m bench.g2 page --run &lt;archive&gt;</code>"
            "; the archive holds the raw streams and the judge's raw output.</li>",
            "</ul>", "</main></body></html>", ""]
    return "\n".join(out)
