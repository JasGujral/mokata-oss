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
from typing import Any, List, Optional

from . import TEMP_LOCAL_DIRNAME
from .progress import (
    DONE,
    CURRENT,
    RunLanes,
    build_run_lanes,
)

DASHBOARD_FILENAME = "watch.html"
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
