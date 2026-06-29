"""Stage 40 — the clickable local HTML dashboard (`mokata watch`, the rich tier).

A SELF-CONTAINED local HTML file (inline CSS, no external assets, no network, no server, no JS
framework — clickable via native `<details>`) that REFLECTS the active run: the parallel lanes,
the 7-phase pipeline, and a BOUNDED tail of the audit-ledger gate/decision feed. It is purely
derived and read-only — it never writes durable state, never gates, never mutates a run — and it
lives in gitignored `.mokata/temp_local/`, so it is never committed or auto-shared.

Deterministic: the rendered HTML is a pure function of run-state + the bounded ledger tail (no
wall-clock is injected), so a fixed run-state always renders the same file. Degrade-clean: no
active run → a friendly empty state; no ledger → lanes + phases only.
"""

from __future__ import annotations

import html
import os
from dataclasses import dataclass
from typing import Any, List, Optional

from . import TEMP_LOCAL_DIRNAME
from .progress import (
    DONE,
    CURRENT,
    RunLanes,
    build_run_lanes,
)

DASHBOARD_FILENAME = "watch.html"
GOVERN_DASHBOARD_FILENAME = "govern.html"
# Frugal/bounded (P11): the feed shows only the last N ledger rows, never the full history.
LEDGER_FEED_TAIL = 50

# UX-tier setting: terminal (no HTML) | dashboard | both.
UX_SETTINGS_KEY = "ux"
UX_TERMINAL, UX_DASHBOARD, UX_BOTH = "terminal", "dashboard", "both"
UX_PROGRESS_VALUES = (UX_TERMINAL, UX_DASHBOARD, UX_BOTH)

_STATE_COLORS = {
    "running": "#2563eb", "done": "#16a34a", "blocked": "#dc2626", "degraded": "#d97706",
}


def dashboard_dir(mokata_dir: str) -> str:
    return os.path.join(mokata_dir, TEMP_LOCAL_DIRNAME)


def dashboard_path(mokata_dir: str) -> str:
    return os.path.join(dashboard_dir(mokata_dir), DASHBOARD_FILENAME)


def ux_progress_setting(surface: Any) -> str:
    """The user's chosen UX tier (default `terminal`); unknown values fall back to terminal."""
    try:
        val = (surface.manifest.setting(UX_SETTINGS_KEY, {}) or {}).get("progress")
    except Exception:
        val = None
    return val if val in UX_PROGRESS_VALUES else UX_TERMINAL


def dashboard_enabled(surface: Any) -> bool:
    return ux_progress_setting(surface) in (UX_DASHBOARD, UX_BOTH)


# --------------------------------------------------------------------------- HTML rendering
def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _feed_row(entry: dict) -> str:
    seq = _esc(entry.get("seq", ""))
    kind = _esc(entry.get("kind", ""))
    at = _esc(entry.get("at", ""))
    # show the salient fields compactly, never the whole entry verbatim
    extra = {k: v for k, v in entry.items() if k not in ("seq", "kind", "at")}
    bits = ", ".join(f"{_esc(k)}={_esc(v)}" for k, v in list(extra.items())[:6])
    return (f"<tr><td class='seq'>{seq}</td><td class='kind'>{kind}</td>"
            f"<td class='at'>{at}</td><td class='det'>{bits}</td></tr>")


def _lane_card(lane, feed: List[dict]) -> str:
    color = _STATE_COLORS.get(lane.state, "#475569")
    rows = [e for e in feed
            if str(e.get("task", "")) == lane.name or e.get("kind") in (
                "exec_degrade", "exec_estimate")]
    inner = "".join(_feed_row(e) for e in rows) or \
        "<tr><td colspan='4' class='muted'>no ledger rows for this lane</td></tr>"
    return (
        f"<details class='lane'>"
        f"<summary><span class='dot' style='background:{color}'></span>"
        f"<b>{_esc(lane.name)}</b> — <span style='color:{color}'>{_esc(lane.state)}</span>"
        + (f" <span class='muted'>· {_esc(lane.note)}</span>" if lane.note else "")
        + (f" <span class='muted'>· {_esc(lane.at)}</span>" if lane.at else "")
        + "</summary>"
        f"<table class='feed'><tr><th>#</th><th>kind</th><th>at</th><th>details</th></tr>"
        f"{inner}</table></details>")


def _phase_pills(rl: RunLanes) -> str:
    p = rl.progress
    if p is None or not p.steps:
        return "<p class='muted'>no pipeline phases on record</p>"
    pills = []
    for s in p.steps:
        cls = {DONE: "done", CURRENT: "current"}.get(s.status, "pending")
        pills.append(f"<span class='pill {cls}'>{_esc(s.phase)}</span>")
    return "<div class='phases'>" + "".join(pills) + "</div>"


_CSS = """
body{font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#0f172a;
color:#e2e8f0}
.wrap{max-width:920px;margin:0 auto;padding:24px}
h1{font-size:18px;margin:0 0 4px}h2{font-size:14px;text-transform:uppercase;letter-spacing:.05em;
color:#94a3b8;margin:24px 0 8px}
.muted{color:#94a3b8}.header{color:#cbd5e1;margin-bottom:16px}
.phases{display:flex;flex-wrap:wrap;gap:6px}
.pill{padding:3px 10px;border-radius:999px;background:#1e293b;color:#94a3b8;font-size:12px}
.pill.done{background:#14532d;color:#bbf7d0}.pill.current{background:#1e3a8a;color:#bfdbfe;
font-weight:600}
.lane{background:#1e293b;border-radius:8px;margin:6px 0;padding:8px 12px}
.lane summary{cursor:pointer;list-style:none}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:7px}
table.feed{width:100%;border-collapse:collapse;margin-top:8px;font-size:12px}
table.feed th{text-align:left;color:#64748b;font-weight:500;border-bottom:1px solid #334155;
padding:3px 6px}
table.feed td{padding:3px 6px;border-bottom:1px solid #1e293b;vertical-align:top}
td.seq{color:#64748b}td.kind{color:#93c5fd}td.at{color:#64748b;white-space:nowrap}
.empty{background:#1e293b;border-radius:8px;padding:24px;text-align:center;color:#94a3b8}
.foot{margin-top:24px;color:#475569;font-size:11px}
"""


def render_dashboard_html(rl: RunLanes, feed: List[dict],
                          refresh_secs: Optional[int] = None) -> str:
    """Render the self-contained dashboard HTML for a lane view + a bounded ledger feed. Pure
    and deterministic (no wall-clock); `refresh_secs` adds a self meta-refresh for live mode."""
    refresh = (f'<meta http-equiv="refresh" content="{int(refresh_secs)}">'
               if refresh_secs else "")
    head = ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"{refresh}<title>mokata · watch</title><style>{_CSS}</style></head><body>")
    foot = ("<p class='foot'>mokata · local read-only run view — reflects run-state + a bounded "
            "ledger tail. Never writes, never gates, never leaves this machine.</p>"
            "</div></body></html>")

    if not rl.active:
        body = (f"<div class='wrap'><h1>mokata · watch</h1>"
                f"<div class='empty'>{_esc(rl.message)}<br><span class='muted'>"
                f"Start a run with /mokata:brainstorm or /mokata:refine.</span></div>")
        return head + body + foot

    lane_cards = "".join(_lane_card(ln, feed) for ln in rl.lanes) or \
        "<p class='muted'>no lanes — sequential run not started</p>"
    feed_rows = "".join(_feed_row(e) for e in feed) or \
        "<tr><td colspan='4' class='muted'>no audit ledger yet — lanes only</td></tr>"

    body = (
        f"<div class='wrap'>"
        f"<h1>mokata · watch</h1>"
        f"<div class='header'>{_esc(rl.header)} · mode: {_esc(rl.mode)}"
        + ("  <b style='color:#d97706'>· degraded</b>" if rl.degraded else "") + "</div>"
        f"<h2>pipeline</h2>{_phase_pills(rl)}"
        f"<h2>lanes ({len(rl.lanes)})</h2>{lane_cards}"
        f"<h2>gate &amp; decision feed (last {len(feed)})</h2>"
        f"<table class='feed'><tr><th>#</th><th>kind</th><th>at</th><th>details</th></tr>"
        f"{feed_rows}</table>")
    return head + body + foot


# --------------------------------------------------------------------------- write (read-only on state)
def build_feed(ledger: Any, tail: int = LEDGER_FEED_TAIL) -> List[dict]:
    """The bounded tail of ledger entries for the feed (frugal — never the full history)."""
    if ledger is None:
        return []
    try:
        return list(ledger.entries())[-tail:]
    except Exception:
        return []


def write_dashboard(surface: Any, *, run_id: Optional[str] = None,
                    refresh_secs: Optional[int] = None,
                    tail: int = LEDGER_FEED_TAIL) -> str:
    """Build the lane view + bounded feed and write the self-contained HTML under temp_local/.
    READ-ONLY on run-state and the ledger; returns the written file path."""
    from .govern import AuditLedger
    ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
    rl = build_run_lanes(surface.state, ledger=ledger, run_id=run_id)
    feed = build_feed(ledger, tail=tail)
    html_text = render_dashboard_html(rl, feed, refresh_secs=refresh_secs)
    out_dir = dashboard_dir(surface.mokata_dir)
    os.makedirs(out_dir, exist_ok=True)
    path = dashboard_path(surface.mokata_dir)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html_text)
    return path


# =========================================================================== Stage 48 —
# memory & governance dashboard: a consolidated, clickable, SELF-CONTAINED view of the
# GOVERNED STATE. Same engine + constraints as `mokata watch` (inline CSS, no network/
# server/assets, under gitignored temp_local/, read-only). Derived + bounded (P11); it
# surfaces the gated CLI manage path for each item — it never writes from the dashboard.

def governance_path(mokata_dir: str) -> str:
    return os.path.join(dashboard_dir(mokata_dir), GOVERN_DASHBOARD_FILENAME)


@dataclass
class GovernanceView:
    version: str
    profile: str
    rule_lines: List[str]            # the always-on tier (rules + guardrails), budget-capped
    rule_count: int
    rule_cap: Optional[int]
    rule_within_cap: bool
    groups: "Any"                    # OrderedDict[kind -> [MemoryItem]] (brain.group_by_kind)
    memory_enabled: bool
    reads: int
    writes: int
    ratio: float
    proposals: List[Any]             # pending self-healing HealingProposals


def build_governance_view(surface: Any) -> GovernanceView:
    """Derive the governed-state view from the manifest + rules + memory store. Read-only;
    degrade-clean (no/empty memory ⇒ empty groups, never raises)."""
    from collections import OrderedDict

    from .govern.rules import always_on_rules_for
    rs = always_on_rules_for(surface)
    m = surface.manifest
    enabled, reads, writes, ratio = False, 0, 0, 0.0
    groups: Any = OrderedDict()
    proposals: List[Any] = []
    try:
        from .memory import MemoryStore
        from .memory.brain import group_by_kind
        store = MemoryStore.from_surface(surface)
        enabled = bool(store.enabled_types)
        # Snapshot the adoption stats FIRST, then read via the NON-counting path: the
        # dashboard is read-only and must mutate no durable state (no _bump_read, so two
        # `mokata govern` runs are byte-identical). peek_active + detect_issues never bump.
        reads, writes, ratio = store.stats.reads, store.stats.writes, store.stats.ratio
        if enabled:
            groups = group_by_kind(store.peek_active())
            proposals = store.detect_issues()
    except Exception:
        pass
    return GovernanceView(
        version=m.mokata_version, profile=m.profile,
        rule_lines=list(rs.lines), rule_count=rs.line_count, rule_cap=rs.cap,
        rule_within_cap=rs.within_cap, groups=groups, memory_enabled=enabled,
        reads=reads, writes=writes, ratio=ratio, proposals=proposals)


_GOVERN_CSS = """
.rule{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;color:#cbd5e1;
white-space:pre-wrap;background:#1e293b;border-radius:8px;padding:10px 12px;margin:0}
.budget{color:#94a3b8;font-size:12px;margin:4px 0 0}
.item{background:#1e293b;border-radius:8px;margin:6px 0;padding:8px 12px}
.item .subj{color:#bfdbfe;font-weight:600}.item .val{color:#e2e8f0}
.item .prov{color:#64748b;font-size:11px}
.manage{display:inline-block;margin-top:4px;font-family:ui-monospace,Menlo,monospace;
font-size:11px;color:#fbbf24;background:#0b1220;border-radius:5px;padding:2px 6px}
.kindcount{color:#94a3b8;font-weight:400}
.prop{background:#3f1d1d;border-radius:8px;margin:6px 0;padding:8px 12px;color:#fecaca}
.ratio{font-size:13px;color:#cbd5e1}
"""


def _manage_cmd(subject: str) -> str:
    return f'mokata memory edit "{_esc(subject)}"'


def _item_card(item: Any) -> str:
    prov = getattr(item, "provenance", {}) or {}
    who = prov.get("author") or "unknown"
    src = prov.get("source") or "unknown"
    return (
        "<div class='item'>"
        f"<div><span class='subj'>{_esc(item.subject)}</span> "
        f"<span class='muted'>[{_esc(item.effective_kind)}]</span></div>"
        f"<div class='val'>{_esc(item.value)}</div>"
        f"<div class='prov'>by {_esc(who)} · source {_esc(src)}</div>"
        f"<code class='manage'>{_manage_cmd(item.subject)}</code>"
        "</div>")


def render_governance_html(view: GovernanceView) -> str:
    """Render the self-contained governance dashboard. Pure + deterministic (no wall-clock)."""
    head = ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>mokata · govern</title><style>{_CSS}{_GOVERN_CSS}</style></head><body>")
    foot = ("<p class='foot'>mokata · local read-only governance view — derived from your "
            "rules + memory store. The manage commands are SURFACED, not run; every write "
            "stays human-gated. Never writes durable state, never leaves this machine.</p>"
            "</div></body></html>")

    cap = "no cap" if view.rule_cap is None else f"{view.rule_count} / {view.rule_cap} lines"
    flag = "within budget" if view.rule_within_cap else "OVER the line budget"
    rules_block = (
        "<h2>rules &amp; guardrails (always-on)</h2>"
        f"<pre class='rule'>{_esc(chr(10).join(view.rule_lines))}</pre>"
        f"<p class='budget'>line budget: {_esc(cap)} — {_esc(flag)}. "
        "honoured on every run.</p>")

    # memory by kind
    if not view.memory_enabled:
        mem_block = ("<h2>memory by kind</h2><div class='empty'>memory is disabled for this "
                     "profile — nothing is captured or honoured.</div>")
    elif not view.groups:
        mem_block = ("<h2>memory by kind</h2><div class='empty'>no captured memory yet — run "
                     "<code>/mokata:onboard</code> to capture rules, guardrails, conventions, "
                     "and domain context.</div>")
    else:
        sections = []
        for kind, items in view.groups.items():
            cards = "".join(_item_card(i) for i in items)
            sections.append(
                f"<details open><summary><b>{_esc(kind)}</b> "
                f"<span class='kindcount'>({len(items)})</span></summary>{cards}</details>")
        mem_block = "<h2>memory by kind</h2>" + "".join(sections)

    ratio_block = (
        "<h2>adoption</h2>"
        f"<p class='ratio'>memory read/write ratio: <b>{view.ratio:.2f}</b> "
        f"({view.reads} reads / {view.writes} writes)</p>")

    if view.proposals:
        props = []
        for p in view.proposals:
            props.append(
                "<div class='prop'>"
                f"<b>{_esc(getattr(p, 'kind', ''))}</b> · {_esc(p.subject)}: "
                f"{_esc(p.diff())}<br><span class='prov'>{_esc(getattr(p, 'rationale', ''))}"
                "</span><br>"
                f"<code class='manage'>resolve: {_manage_cmd(p.subject)}</code></div>")
        prop_block = (f"<h2>pending self-healing proposals ({len(view.proposals)})</h2>"
                      "<p class='muted'>nothing changes until you act — each is human-gated."
                      "</p>" + "".join(props))
    else:
        prop_block = ("<h2>pending self-healing proposals</h2>"
                      "<div class='empty'>no pending proposals — memory is consistent.</div>")

    body = (
        f"<div class='wrap'><h1>mokata · govern</h1>"
        f"<div class='header'>mokata {_esc(view.version)} · profile: {_esc(view.profile)} "
        "— the state mokata will honour</div>"
        f"{rules_block}{mem_block}{ratio_block}{prop_block}")
    return head + body + foot


def write_governance_dashboard(surface: Any) -> str:
    """Build + write the self-contained governance HTML under temp_local/. READ-ONLY on the
    store (derives the view; performs no write); returns the written file path."""
    view = build_governance_view(surface)
    html_text = render_governance_html(view)
    out_dir = dashboard_dir(surface.mokata_dir)
    os.makedirs(out_dir, exist_ok=True)
    path = governance_path(surface.mokata_dir)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html_text)
    return path
