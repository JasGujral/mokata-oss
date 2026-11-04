"""`mokata govern` — the governed-state view (Stage 48): the always-on rules tier, memory grouped by
kind, the read/write ratio, pending self-healing proposals and the Stage 60 "what changed since last
session" diff. `build_governance_view` derives it (the MCP `govern` tool returns it as data);
`write_governance_dashboard` renders it as a SELF-CONTAINED local HTML file (inline CSS, no network,
no server, no assets) under gitignored `.mokata/temp_local/`. Read-only: it surfaces the gated
`mokata memory edit` manage path for each item, it never writes from the view.

0.0.27 WATCH-FOLD: this used to share a module with the `mokata watch` HTML dashboard, which
is gone (folded into the IDE's Sessions screen). The governance view is not part of that ruling and
moved here unchanged; the IDE absorbing it is ID.S7 (doc 109).
"""

from __future__ import annotations

import html
import os
from dataclasses import dataclass
from typing import Any, List, Optional

from . import TEMP_LOCAL_DIRNAME

GOVERN_DASHBOARD_FILENAME = "govern.html"


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def governance_dir(mokata_dir: str) -> str:
    return os.path.join(mokata_dir, TEMP_LOCAL_DIRNAME)


def governance_path(mokata_dir: str) -> str:
    return os.path.join(governance_dir(mokata_dir), GOVERN_DASHBOARD_FILENAME)


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
    health: Any = None               # Stage 59 — read-only MemoryHealth nudge (proposal-only)
    session_diff: Any = None         # Stage 60 — read-only "what changed since last session"
    memory_degraded: bool = False    # D5 — the store could NOT be read; this view is not memory
    memory_degraded_detail: str = ""


def build_governance_view(surface: Any) -> GovernanceView:
    """Derive the governed-state view from the manifest + rules + memory store. Read-only;
    degrade-clean (no/empty memory ⇒ empty groups, never raises).

    D5 — this is THE surface that answers "what is mokata remembering?". When the store failed to
    build or read, it used to answer "nothing": `memory_enabled=False`, no groups, `0 reads / 0
    writes` — pixel-identical to a fresh repo that genuinely has no memory. It still renders (the
    dashboard must not die on a broken store), but it now says the store could not be READ, so a
    zero can never be mistaken for a truth."""
    from collections import OrderedDict
    from sqlite3 import Error as SQLiteError

    from .govern.rules import always_on_rules_for
    # mokata's OWN typed memory error (`MokataError`), NOT the builtin `MemoryError` — the builtin
    # is an out-of-memory condition and must never be swallowed here. Bound BEFORE the try, so the
    # except clause can never be evaluated against an unbound name.
    from .memory.store import MemoryError as MemoryStoreError
    rs = always_on_rules_for(surface)
    m = surface.manifest
    enabled, reads, writes, ratio = False, 0, 0, 0.0
    groups: Any = OrderedDict()
    proposals: List[Any] = []
    memory_degraded, memory_detail = False, ""
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
    except (MemoryStoreError, OSError, ImportError, SQLiteError) as exc:
        from .degrade import FAILURE_UNREACHABLE, note_degraded
        memory_degraded, memory_detail = True, str(exc)
        note_degraded("memory", FAILURE_UNREACHABLE,
                      fallback="this view is NOT your memory — the store could not be read",
                      fix="run `mokata doctor`", detail=str(exc))
    # Stage 59 — derive the memory-health nudge from the proposals + C8 counters ALREADY in
    # hand (no extra store reads, no stat bumps). Proposal-only: it points at the gated review
    # path; it never edits/prunes memory.
    from .memory.intelligence import memory_health
    health = memory_health(proposals, reads, writes)
    # Stage 60 — the read-only "what changed since last session" diff (derived; never writes).
    # Degrade-clean: any problem (or no prior snapshot) yields a friendly first-session diff.
    try:
        from .visibility import compute_session_diff
        session_diff = compute_session_diff(surface)
    except Exception:
        # (iv) SUPPRESS-OK: `compute_session_diff` is itself degrade-clean and already announces its
        # OWN memory failure (visibility.py) — the notice for a broken store has been emitted by the
        # time this returns. This guard exists only so a COSMETIC block ("what changed since last
        # session") can never take the whole governance view down with it; the block simply doesn't
        # render, which is exactly what a first session looks like. Broad because the callee spans
        # memory + ledger + snapshot IO and re-listing its classes here would drift from it.
        session_diff = None
    return GovernanceView(
        version=m.mokata_version, profile=m.profile,
        rule_lines=list(rs.lines), rule_count=rs.line_count, rule_cap=rs.cap,
        rule_within_cap=rs.within_cap, groups=groups, memory_enabled=enabled,
        reads=reads, writes=writes, ratio=ratio, proposals=proposals, health=health,
        session_diff=session_diff, memory_degraded=memory_degraded,
        memory_degraded_detail=memory_detail)


_CSS = """
body{font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#0f172a;
color:#e2e8f0}
.wrap{max-width:920px;margin:0 auto;padding:24px}
h1{font-size:18px;margin:0 0 4px}h2{font-size:14px;text-transform:uppercase;letter-spacing:.05em;
color:#94a3b8;margin:24px 0 8px}
.muted{color:#94a3b8}.header{color:#cbd5e1;margin-bottom:16px}
.empty{background:#1e293b;border-radius:8px;padding:24px;text-align:center;color:#94a3b8}
.foot{margin-top:24px;color:#475569;font-size:11px}
"""


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
.nudge{font-size:13px;color:#fde68a;background:#3f2d0b;border-radius:8px;padding:8px 12px;
margin:6px 0}.nudge code{color:#fbbf24}
ul.since{margin:4px 0 0;padding-left:18px;color:#cbd5e1;font-size:12px}
ul.since li{font-family:ui-monospace,Menlo,monospace;margin:2px 0}
.degraded{background:#3f2d0b;border:1px solid #d97706;border-radius:8px;padding:10px 12px;
margin:6px 0;color:#fde68a}
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


def _since_block(view: GovernanceView) -> str:
    """Stage 60 — the read-only "what changed since last session" block. Pure/derived;
    deterministic (no wall-clock — the decision lines come from the timestamp-free
    why_timeline). Degrade-clean: first session / no changes → a friendly one-liner."""
    diff = view.session_diff
    if diff is None:
        return ""
    head = "<h2>what changed since last session</h2>"
    if getattr(diff, "degraded", False):
        # D5 — "no changes since last session" is the WORST thing to print when nothing was compared.
        return head + (f"<div class='degraded'><b>⚠ {_esc(diff.summary_line())}</b><br>"
                       "Nothing was compared — this is not a report that nothing changed.</div>")
    if diff.first_session:
        return head + ("<div class='empty'>first session — no prior snapshot to compare "
                       "yet. The baseline is captured at each session start.</div>")
    if not diff.has_changes:
        return head + "<div class='empty'>no changes since last session.</div>"
    rows = "".join(f"<li>{_esc(line)}</li>" for line in diff.detail_lines()[1:])
    return (head + f"<p class='ratio'>{_esc(diff.summary_line())}</p>"
            f"<ul class='since'>{rows}</ul>")


def render_governance_html(view: GovernanceView, refresh_secs: Optional[int] = None) -> str:
    """Render the self-contained governance dashboard. Pure + deterministic (no wall-clock);
    `refresh_secs` adds a self meta-refresh for live mode."""
    refresh = (f'<meta http-equiv="refresh" content="{int(refresh_secs)}">'
               if refresh_secs else "")
    head = ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"{refresh}<title>mokata · govern</title>"
            f"<style>{_CSS}{_GOVERN_CSS}</style></head><body>")
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
    if view.memory_degraded:
        # D5 — the ONE thing this view must never say when the store is unreadable is "nothing".
        # An empty memory panel and a broken memory panel used to be the same pixels.
        mem_block = (
            "<h2>memory by kind</h2>"
            "<div class='degraded'><b>⚠ DEGRADED — this is NOT your memory.</b><br>"
            "The memory store could not be read, so this view shows nothing captured and "
            "<b>0 reads / 0 writes</b>. That is the READ failing, not your memory being empty — "
            "do not read a zero here as a fact.<br>"
            f"<span class='prov'>{_esc(view.memory_degraded_detail)}</span><br>"
            "<code class='manage'>mokata doctor</code></div>")
    elif not view.memory_enabled:
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
        f"({view.reads} reads / {view.writes} writes)"
        + ("  <b style='color:#d97706'>— UNKNOWN: the store could not be read</b>"
           if view.memory_degraded else "") + "</p>")

    # Stage 59 — the memory-health nudge: actionable, proposal-only, SILENT when healthy.
    nudge = view.health.nudge() if view.health is not None else ""
    if nudge:
        h = view.health
        ratio_block += (
            "<p class='nudge'>"
            f"<b>memory health</b>: {h.stale} stale &middot; {h.contradictory} contradictory "
            f"&middot; {h.unused} unused — review with "
            "<code>mokata memory</code> / <code>mokata govern</code>. "
            "Proposal-only: nothing changes until you approve (human-gated)."
            "</p>")

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
        f"{_since_block(view)}{rules_block}{mem_block}{ratio_block}{prop_block}")
    return head + body + foot


def write_governance_dashboard(surface: Any, refresh_secs: Optional[int] = None) -> str:
    """Build + write the self-contained governance HTML under temp_local/. READ-ONLY on the
    store (derives the view; performs no write); `refresh_secs` adds the live self meta-refresh. Returns the written file path."""
    view = build_governance_view(surface)
    html_text = render_governance_html(view, refresh_secs=refresh_secs)
    out_dir = governance_dir(surface.mokata_dir)
    os.makedirs(out_dir, exist_ok=True)
    path = governance_path(surface.mokata_dir)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html_text)
    return path
