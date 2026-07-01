"""H4+ — the plugin-first MCP surface.

A thin Model Context Protocol server that exposes mokata operations as native tools for
Claude Code, so the framework is driven from inside the harness — not only the CLI. Every
tool delegates to the existing package; this module adds NO engine logic.

Two safety rules are non-negotiable:

  * READ tools (query, recall, doctor, coverage, budget, audit, status, preview, progress,
    lanes, watch, govern, vault_list/search/pull, and the Stage 54e parity reads — rules,
    skills, suggest, lat_check, index_status, baseline, sessions, config_get, export_preview,
    the Stage 55a session_list, and the Stage 70 stacks_list/stacks_search/stacks_show) are safe
    and expose their data directly.
  * WRITE / durable tools (remember, import_stack, reset, apply_proposal, memory_export/import,
    vault_push, spec_check, init, the Stage 54e config_set + export_stack, the Stage 70 gated
    stacks_install, and the Stage 55a/55b
    human-gated session_push/session_pull/session_name — gated on EVERY transport) are ALWAYS
    human-gated. With no `approve`, they are PROPOSE-ONLY: they return the staged change
    and write nothing. Only an explicit `approve=true` — a human decision — performs the
    write, and even then it goes through the universal WriteGate (secrets are a hard block
    that approval cannot override) and is recorded in the audit ledger. An MCP call NEVER
    writes silently. (`confirm=true` is accepted as a deprecated alias for `approve=true`.)

The MCP SDK is an OPTIONAL, plugin-side dependency (`pip install "mokata[mcp]"`). This
module imports it LAZILY inside `build_server`, so the core package and CLI import and run
with the SDK absent. The tool functions themselves are pure and SDK-free — fully usable and
testable without `mcp` installed.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from . import MOKATA_DIR
from .adapters import AdapterContract, negotiate, overlapping_capabilities
from .config import Surface
from .engine import preview_pipeline
from .govern import (AuditLedger, BudgetReport, WriteGate, WriteRequest,
                     diagnose, plan_reset, reset_state)
from .knowledge import KnowledgeLayer
from .memory import DECISION, MemoryItem, MemoryStore

SERVER_NAME = "mokata"


# --------------------------------------------------------------------------------------
# Tool registry — pure, SDK-free functions. `build_server` registers these with FastMCP.
# --------------------------------------------------------------------------------------
@dataclass
class ToolSpec:
    name: str
    kind: str          # "read" | "write"
    fn: Callable[..., Any]


TOOLS: List[ToolSpec] = []


def _tool(name: str, kind: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def register(fn: Callable[..., Any]) -> Callable[..., Any]:
        TOOLS.append(ToolSpec(name=name, kind=kind, fn=fn))
        return fn
    return register


def tool_names() -> List[str]:
    """Every tool the server exposes (works with the MCP SDK absent)."""
    return [t.name for t in TOOLS]


def read_tool_names() -> List[str]:
    return [t.name for t in TOOLS if t.kind == "read"]


def write_tool_names() -> List[str]:
    return [t.name for t in TOOLS if t.kind == "write"]


def _surface(path: str) -> Surface:
    return Surface.load(path)


def _mokata_dir(path: str) -> str:
    return os.path.join(path, MOKATA_DIR)


# --------------------------------------------------------------------------------------
# READ tools — safe, expose data directly.
# --------------------------------------------------------------------------------------
@_tool("query", "read")
def query(path: str = ".", kind: str = "callers", target: str = "",
          depth: int = 2) -> Dict[str, Any]:
    """Run a structural code query (graph backend if present, else the grep floor). `kind`
    is one of callers/callees/implementers/imports/blast_radius. Read-only."""
    layer = KnowledgeLayer.from_surface(_surface(path))
    return layer._run(kind, target, depth=depth).to_dict()


@_tool("recall", "read")
def recall(path: str = ".", subject: str = "", query: str = "",
           memory_type: Optional[str] = None, mtype: Optional[str] = None) -> Dict[str, Any]:
    """Recall active memory. With a `query`, do a by-RELEVANCE recall (top-k, frugal) where
    each hit carries a short EXPLAINABLE "why it surfaced" phrase (which query token / graph
    anchor / semantic neighbour / kind). With a `subject`, return exact matches; otherwise all
    active items. `memory_type` filters by storage tier (persistent/decision/episodic); `mtype`
    is a DEPRECATED alias. Read-only — surfaces nothing disabled, writes nothing."""
    store = MemoryStore.from_surface(_surface(path))
    if not store.enabled_types:
        return {"enabled": False, "items": []}
    if query:
        # Stage 59 — explainable retrieval: each hit names WHY it surfaced (frugal, top-k).
        from .memory.intelligence import explain_recall
        hits = store.recall_relevant(query)
        return {"enabled": True, "backend": store.backend.name, "query": query,
                "items": [{"memory_type": e.item.mtype, "kind": e.item.effective_kind,
                           "subject": e.item.subject, "value": e.item.value, "why": e.why}
                          for e in explain_recall(query, hits)]}
    mt = memory_type or mtype
    items = store.recall(subject, mtype=mt) if subject else store.all_active(mtype=mt)
    return {"enabled": True, "backend": store.backend.name,
            "items": [{"memory_type": i.mtype, "kind": i.effective_kind,
                       "mtype": i.mtype,  # deprecated alias, kept for back-compat
                       "subject": i.subject, "value": i.value}
                      for i in items]}


@_tool("doctor", "read")
def doctor(path: str = ".") -> Dict[str, Any]:
    """Diagnose the manifest/config: missing providers, broken adapters, role conflicts,
    bad trust dials. Read-only."""
    report = diagnose(_surface(path))
    return {"ok": report.ok, "report": report.render()}


@_tool("coverage", "read")
def coverage(path: str = ".") -> Dict[str, Any]:
    """Report capability coverage, unmet gaps, and overlaps for the current stack.
    Read-only."""
    m = _surface(path).manifest
    adapters = [AdapterContract(name=tid, provides=[t.get("provides")],
                                kind=t.get("kind", "external"))
                for tid, t in m.tools.items() if t.get("provides")]
    report = negotiate(list(m.capabilities), adapters)
    overlaps = overlapping_capabilities(m)
    return {"report": report.render(),
            "overlaps": {need: providers for need, providers in overlaps.items()}}


@_tool("budget", "read")
def budget(path: str = ".") -> Dict[str, Any]:
    """Show token savings recorded in the audit ledger (live budget readout). Read-only."""
    ledger = AuditLedger.from_mokata_dir(_mokata_dir(path))
    report = BudgetReport.from_ledger(ledger)
    if not report.events:
        return {"events": 0, "report": "budget: no savings recorded yet."}
    return {"events": len(report.events), "report": report.render()}


@_tool("audit", "read")
def audit(path: str = ".", limit: int = 0, team: bool = False) -> Dict[str, Any]:
    """Show the append-only audit ledger (every gate decision + tool call). `limit` keeps
    the most recent N entries (0 = all). With `team=true` (Stage 71) show the TEAM-WIDE
    who-did-what over the SHARED log instead — spanning all actors, on the team's OWN storage
    (NO telemetry, nothing phoned home). Degrade-clean: sharing off / backend absent → available
    false with a clear message. Read-only."""
    if team:
        from .team_audit import render_team_timeline, team_audit_view
        view = team_audit_view(path, _surface(path))
        if not view.available:
            return {"team": True, "available": False, "message": view.message,
                    "count": 0, "entries": []}
        entries = view.entries[-limit:] if limit and limit > 0 else view.entries
        return {"team": True, "available": True, "actors": view.actors,
                "count": len(view.entries), "entries": entries,
                "who_did_what": render_team_timeline(view)}
    entries = AuditLedger.from_mokata_dir(_mokata_dir(path)).entries()
    if limit and limit > 0:
        entries = entries[-limit:]
    return {"count": len(entries), "entries": entries}


@_tool("status", "read")
def status(path: str = ".") -> Dict[str, Any]:
    """One-line stack summary: version, profile, and what each capability resolves to right
    now. Read-only."""
    surface = _surface(path)
    m = surface.manifest
    return {"version": m.mokata_version, "profile": m.profile,
            "capabilities": [r.summary() for r in surface.router.resolve_all()]}


@_tool("preview", "read")
def preview(path: str = ".", start: Optional[str] = None,
            stop: Optional[str] = None) -> Dict[str, Any]:
    """Dry-run the pipeline: planned phases, gates, and file touches. No side effects."""
    pv = preview_pipeline(start=start, stop=stop, mokata_dir=_surface(path).mokata_dir)
    return {"preview": pv.render()}


@_tool("progress", "read")
def progress(path: str = ".", run: Optional[str] = None) -> Dict[str, Any]:
    """Where are we? The run-progress tracker (done/current/pending + counts) derived from
    the persisted run-state. Read-only; with no active run it returns a clean, inactive view
    (never an error). `run` selects a specific run id (default: the active/most-recent)."""
    from .progress import build_progress, render_progress
    p = build_progress(_surface(path).state, run_id=run)
    out = p.to_dict()
    out["block"] = render_progress(p)
    return out


@_tool("lanes", "read")
def lanes(path: str = ".", run: Optional[str] = None,
          ascii_only: bool = False) -> Dict[str, Any]:
    """The PARALLEL-aware lane view: one lane per concurrent subagent
    (running/done/blocked/degraded) under the run's phase header, derived from run-state +
    the execmode records in a bounded ledger tail (Stage 40). Read-only; a sequential run
    shows a single lane; no run/ledger degrades to a friendly empty view (never an error).
    `run` selects a specific run id (default: the active/most-recent)."""
    from .progress import build_run_lanes, render_lanes
    surface = _surface(path)
    ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
    rl = build_run_lanes(surface.state, ledger=ledger, run_id=run)
    out = rl.to_dict()
    out["block"] = render_lanes(rl, ascii_only=ascii_only)
    return out


@_tool("watch", "read")
def watch(path: str = ".", run: Optional[str] = None) -> Dict[str, Any]:
    """Write the self-contained, clickable local HTML dashboard of the active run (parallel
    lanes + 7-phase pipeline + a bounded gate/decision feed) under gitignored temp_local/ and
    return its path. The artifact has no network/server/assets. Honors settings.ux.progress:
    the default `terminal` writes NO HTML (returns a note on how to enable it). Read-only over
    run-state + the ledger — it never mutates a run or gates."""
    from .dashboard import (dashboard_enabled, ux_progress_setting,
                            write_dashboard)
    surface = _surface(path)
    if not dashboard_enabled(surface):
        return {"enabled": False, "tier": ux_progress_setting(surface),
                "note": "dashboard off (settings.ux.progress=%s). Enable with "
                        "`mokata config set settings.ux.progress dashboard` (or `both`)."
                        % ux_progress_setting(surface)}
    written = write_dashboard(surface, run_id=run)
    return {"enabled": True, "path": written,
            "note": "self-contained dashboard written under temp_local/ (read-only, "
                    "no network); open it in a browser."}


@_tool("govern", "read")
def govern(path: str = ".", live: bool = False) -> Dict[str, Any]:
    """The governed-state view (Stage 48): the always-on rules tier, memory grouped by kind,
    the read/write ratio, pending self-healing proposals, and the Stage 60 "what changed since
    last session" diff. Writes the self-contained governance HTML under gitignored temp_local/
    and returns its path + a structured summary. With `live=True` the HTML self-refreshes
    (honours settings.ux.progress; degrades to a static snapshot off the dashboard tier).
    Read-only — it surfaces the gated `mokata memory edit` manage path, never writes state."""
    from .dashboard import (build_governance_view, dashboard_enabled,
                            write_governance_dashboard)
    surface = _surface(path)
    view = build_governance_view(surface)
    refresh = 2 if (live and dashboard_enabled(surface)) else None
    written = write_governance_dashboard(surface, refresh_secs=refresh)
    # Stage 59 — the read-only memory-health nudge (proposal-only; empty string when healthy).
    h = view.health
    health = {"stale": h.stale, "contradictory": h.contradictory, "unused": h.unused,
              "healthy": h.healthy, "nudge": h.nudge()} if h is not None else {}
    # Stage 60 — the read-only "since last session" diff (derived; never writes).
    d = view.session_diff
    since = {"first_session": d.first_session, "has_changes": d.has_changes,
             "summary": d.summary_line(), "new_memory": d.new_memory,
             "changed_memory": d.changed_memory, "new_rules": d.new_rules,
             "decisions": d.decision_count} if d is not None else {}
    return {"path": written, "version": view.version, "profile": view.profile,
            "rules": view.rule_count, "memory_enabled": view.memory_enabled,
            "reads": view.reads, "writes": view.writes, "ratio": view.ratio,
            "proposals": len(view.proposals), "health": health, "live": bool(refresh),
            "since_last_session": since,
            "note": "read-only view of the governed state under temp_local/; manage via the "
                    "gated `mokata memory edit` commands."}


# --------------------------------------------------------------------------------------
# Stage 54e — command-parity READ tools. Each REUSES an existing engine; read-only.
# --------------------------------------------------------------------------------------
@_tool("rules", "read")
def rules(path: str = ".") -> Dict[str, Any]:
    """The always-on 4-tier rules (rules/guardrails) with each tier's line count + budget
    cap, plus any HUMAN-GATED rule PROPOSALS distilled from recurring ledger corrections
    (G5; proposal-only — never auto-added). Read-only."""
    from .govern import learn_from_ledger, load_rules, validate_caps
    surface = _surface(path)
    by_tier = load_rules(surface)
    proposals = learn_from_ledger(AuditLedger.from_mokata_dir(surface.mokata_dir))
    return {"tiers": {tier: {"lines": rs.line_count, "cap": rs.cap,
                             "within_cap": rs.within_cap}
                      for tier, rs in by_tier.items()},
            "cap_errors": validate_caps(by_tier),
            "proposals": [p.proposed_rule for p in proposals]}


@_tool("skills", "read")
def skills(path: str = ".", name: str = "", query: str = "") -> Dict[str, Any]:
    """The skill/command catalog (L4, progressive disclosure). With no args, the cheap
    (name, summary) list; with a `query`, a discoverable keyword-filtered catalog (Stage 70);
    with a `name`, that skill's gate + phase + full prompt. Read-only — surfaces the catalog,
    never runs a skill."""
    from .skills import SkillNotFound, get_skill, list_skills
    if name:
        try:
            s = get_skill(name)
        except SkillNotFound as exc:
            return {"status": "error", "message": str(exc)}
        return {"name": s.name, "summary": s.summary, "phase": s.phase,
                "gate": {"id": s.gate.id, "kind": s.gate.kind,
                         "description": s.gate.description},
                "prompt": s.prompt}
    catalog = list_skills()
    if query:
        q = query.lower()
        catalog = [(n, sm) for n, sm in catalog if q in n.lower() or q in sm.lower()]
    return {"skills": [{"name": n, "summary": sm} for n, sm in catalog]}


@_tool("suggest", "read")
def suggest(path: str = ".", fresh: bool = False, spec: bool = False,
            failing_test: bool = False, implementation: bool = False, diff: bool = False,
            bug: bool = False, stacktrace: bool = False, perf: bool = False) -> Dict[str, Any]:
    """Suggest a relevant /mokata command for the current context (L6) — SUGGEST ONLY, it
    never runs anything. Pass the booleans that describe your state (fresh/spec/failing_test/
    implementation/diff/bug/stacktrace/perf). Read-only."""
    from .compose import SuggestionContext, suggest as _suggest
    ctx = SuggestionContext(starting_fresh=fresh, has_spec=spec,
                            has_failing_test=failing_test, has_implementation=implementation,
                            has_diff=diff, has_bug_report=bug, has_stacktrace=stacktrace,
                            has_perf_issue=perf)
    return {"suggestions": [{"skill": s.skill, "reason": s.reason} for s in _suggest(ctx)]}


@_tool("lat_check", "read")
def lat_check(path: str = ".") -> Dict[str, Any]:
    """Scan @lat anchors and flag concept drift (B5). Degrades cleanly when absent (no
    anchors → no drift). Read-only."""
    from .knowledge import lat_check as _lat
    report = _lat(_surface(path).root)
    return {"has_drift": report.has_drift, "report": report.render()}


@_tool("index_status", "read")
def index_status(path: str = ".") -> Dict[str, Any]:
    """The freshness-index STATUS (B4): how many files are tracked and what changed
    (added/removed/changed) since the last build — computed via a read-only DIFF; nothing is
    rebuilt or written (the durable rebuild stays `mokata index` on the CLI). Names the
    code-graph backend the refresh would run against. Read-only."""
    from .knowledge import KnowledgeIndex, KnowledgeLayer
    surface = _surface(path)
    layer = KnowledgeLayer.from_surface(surface)
    backend = {"uses_graph": layer.uses_graph, "backend": layer.backend_name}
    data = surface.state.read("knowledge_index")
    if data is None:
        return {"built": False, "tracked": 0,
                "note": "no index yet — run `mokata index` to build it (a durable write).",
                **backend}
    idx = KnowledgeIndex.from_dict(data)
    d = idx.diff(surface.root)
    return {"built": True, "tracked": len(idx.entries), "added": len(d["added"]),
            "removed": len(d["removed"]), "changed": len(d["changed"]), **backend}


@_tool("tour", "read")
def tour(path: str = ".") -> Dict[str, Any]:
    """Stage 56 — a short, SELF-CONTAINED, READ-ONLY demo of mokata (a graph query, a live
    memory recall in an in-memory store, a real secret gate-catch). Writes nothing; safe to call
    anytime. Returns the demo text for the model to show the user."""
    from .onboarding import build_tour
    return {"tour": build_tour()}


@_tool("ci_check", "read")
def ci_check(path: str = ".", files: str = "", symbols: str = "") -> Dict[str, Any]:
    """Stage 58 — mokata as a PR check: run the completeness gate + spec-awareness regression
    guard over a change's `files` (comma-separated; `symbols` default to the ones defined in those
    files) and return PASS/BLOCK + the review-comment body. READ-ONLY — it SURFACES blocks for a
    reviewer and PRODUCES the comment; it never posts to GitHub. DEGRADE-CLEAN: no saved spec /
    no corpus / uninitialized repo → nothing to check → PASS (never a false block)."""
    from . import ci_check as CI
    fl = [f.strip() for f in files.split(",") if f.strip()]
    sy = [s.strip() for s in symbols.split(",") if s.strip()] or None
    res = CI.run_ci_check(path, fl, changed_symbols=sy)
    return {"blocked": res.blocked, "overall": res.overall, "initialized": res.initialized,
            "legs": [{"name": leg.name, "status": leg.status, "summary": leg.summary,
                      "unblock": leg.unblock} for leg in res.legs],
            "comment_body": res.comment_body()}


@_tool("baseline", "read")
def baseline(path: str = ".", cmd: str = "") -> Dict[str, Any]:
    """Report the test suite green/red at baseline (Stage 34B) so a later failure is
    attributable to your change. Degrades clean if no test command is known (mokata never
    guesses a framework). Read-only — runs the existing suite, writes nothing."""
    from .baseline import baseline_command, baseline_status
    manifest = None
    if Surface.is_initialized(path):
        try:
            manifest = Surface.load(path).manifest
        except Exception:
            manifest = None
    result = baseline_status(baseline_command(manifest, override=cmd or None), cwd=path)
    return {"ok": result.ok, "report": result.render()}


@_tool("sessions", "read")
def sessions(path: str = ".") -> Dict[str, Any]:
    """List past + active runs (id, phases passed, resume point) — read-only, bounded, with a
    friendly empty state. Continue one via the /mokata:resume slash; the gates still apply on
    resume (mokata never auto-runs the pipeline)."""
    from .progress import list_sessions
    rows = list_sessions(_surface(path).state)
    return {"count": len(rows),
            "sessions": [{"run_id": s.run_id, "done": s.done, "total": s.total,
                          "complete": s.complete, "active": s.active,
                          "resume_phase": s.resume_phase, "last_passed": s.last_passed}
                         for s in rows]}


@_tool("session_list", "read")
def session_list(path: str = ".", transport: str = "") -> Dict[str, Any]:
    """Stage 55a/55b — list the tagged, shareable session bundles (tag, provenance, resume point,
    transport). Read-only; a friendly empty state when there are none. With no `transport` it
    spans LOCAL + the committed VAULT (+ shared Postgres when a DSN is configured); pass a single
    transport name to scope it. A missing/unavailable remote is skipped clean. Push/pull/rename
    are the human-gated `session_push`/`session_pull`/`session_name` write tools."""
    from . import session_bundle as SB
    from . import session_transport as STX
    if transport:
        try:
            transports = [STX.make_transport(transport, path)]
        except STX.SessionTransportUnavailable as exc:
            return {"count": 0, "bundles": [], "transport": transport,
                    "status": "unavailable", "message": str(exc)}
    else:
        transports = [STX.LocalTransport(path), STX.VaultTransport(path)]
        if STX.resolve_pg_dsn():
            try:
                transports.append(STX.make_transport("postgres", path))
            except STX.SessionTransportUnavailable:
                pass
    infos = SB.list_session_bundles_across(path, transports)
    return {"count": len(infos),
            "bundles": [{"tag": i.tag, "author": i.author, "created": i.created,
                         "source": i.source, "run_id": i.run_id,
                         "resume_phase": i.resume_phase, "done": i.done, "total": i.total,
                         "transport": i.transport}
                        for i in infos]}


@_tool("config_get", "read")
def config_get(path: str = ".", key: str = "") -> Dict[str, Any]:
    """Read a dotted backend-config key from the committed manifest (Stage 24A), e.g.
    `tools.sqlite.config.path`. Read-only; returns {found, value}. The write counterpart is
    the human-gated `config_set`."""
    from . import config_cmd
    try:
        found, val = config_cmd.config_get(path, key)
    except config_cmd.ConfigCommandError as exc:
        return {"status": "error", "message": str(exc)}
    return {"found": found, "key": key, "value": val if found else None}


@_tool("export_preview", "read")
def export_preview(path: str = ".", file: str = "") -> Dict[str, Any]:
    """Preview the shareable stack export (J3): the manifest profile/capabilities/tools that
    WOULD be written and where, WITHOUT writing anything. Use the gated `export_stack` to
    actually write it. Read-only."""
    from .manifest import Manifest
    from .share import SHARE_FILENAME, export_manifest
    data = export_manifest(_surface(path))      # dest=None → returns data, writes nothing
    dest = file or os.path.join(path, MOKATA_DIR, SHARE_FILENAME)
    return {"dest": dest, "profile": data.get("profile"),
            "capabilities": len(data.get("capabilities", {}) or {}),
            "tools": len(data.get("tools", {}) or {}),
            "preview": Manifest.from_dict(data).to_json()}


@_tool("decompose", "read")
def decompose(path: str = ".") -> Dict[str, Any]:
    """Propose an independent-subtask split of the emitted spec's acceptance criteria, with a
    dependency plan (Stage 54f): one subtask per AC, with `depends_on` edges where subtasks
    touch the same symbol/file (the code graph verifies independence when wired; the lexical
    floor otherwise — in which case the split stays UNVERIFIED and sequential is recommended).
    READ-ONLY: it only PROPOSES the split; nothing fans out (the confirm + execution stay the
    human-gated `mokata decompose --run` / exec flow). Degrades clean with no spec/ACs."""
    from .engine import load_emitted_spec
    from .execmode.decompose import decompose as _decompose
    surface = _surface(path)
    spec = load_emitted_spec(surface.state)
    if spec is None or not spec.criteria:
        return {"available": False, "subtasks": [],
                "note": "no emitted spec with acceptance criteria — run /mokata:spec first; "
                        "the split is derived from the approved ACs."}
    plan = _decompose(spec, layer=KnowledgeLayer.from_surface(surface))
    out = plan.to_dict()
    out["available"] = True
    out["block"] = plan.render()
    return out


# --------------------------------------------------------------------------------------
# WRITE tools — propose-only by default; explicit `approve=true` performs the gated write.
# --------------------------------------------------------------------------------------
def _approved(approve: bool, confirm: Optional[bool]) -> bool:
    """The gate boolean for a write tool. `approve` is the convention (a bool, matching
    `assume_yes` elsewhere — the project keeps `confirm` for the Callable gate); `confirm` is a
    DEPRECATED alias kept so earlier MCP callers that passed `confirm=true` still work."""
    return bool(approve) or bool(confirm)


def _gated_write(mokata_dir: str, kind: str, target: str, content: str,
                 commit_fn: Callable[[], Any]) -> Dict[str, Any]:
    """Run a durable write through the universal WriteGate. Secrets are a hard block that
    `confirm` cannot override; the decision is recorded in the audit ledger; the actual
    work happens in `commit_fn`, only if the gate approves."""
    ledger = AuditLedger.from_mokata_dir(mokata_dir)
    gate = WriteGate(ledger=ledger)
    box: Dict[str, Any] = {}
    outcome = gate.submit(
        WriteRequest(kind, target, content=content, actor="mcp"),
        commit=lambda: box.update(result=commit_fn()),
        assume_yes=True)            # the explicit `confirm` IS the human approval
    return {"status": "committed" if outcome.committed else "blocked",
            "committed": outcome.committed,
            "reason": outcome.reason,
            "findings": [f.kind for f in outcome.findings],
            "result": box.get("result")}


@_tool("remember", "write")
def remember(path: str = ".", subject: str = "", value: str = "",
             memory_type: str = DECISION, kind: str = "", approve: bool = False,
             confirm: Optional[bool] = None, mtype: Optional[str] = None) -> Dict[str, Any]:
    """Remember a fact/decision in memory. `memory_type` is the storage tier
    (persistent/decision/episodic); `kind` is the typed project part (rule/guardrail/
    best-practice/context/reference) captured by /mokata:onboard; `mtype` is a DEPRECATED alias
    for `memory_type`. HUMAN-GATED: without `approve=true` this is propose-only and writes
    nothing. With `approve=true` it commits through the WriteGate (a secret in `subject` OR
    `value` is blocked even when approved). A part kind is stored as persistent project knowledge."""
    from .memory import PART_KINDS, PERSISTENT, normalize_kind
    surface = _surface(path)
    store = MemoryStore.from_surface(surface)
    if not store.enabled_types:
        return {"status": "unavailable", "message": "memory is disabled for this profile"}
    mtype = mtype or memory_type      # `mtype` (deprecated) overrides only if explicitly passed
    norm = normalize_kind(kind)
    if norm in PART_KINDS:
        mtype = PERSISTENT          # the captured "parts" are persistent project knowledge
    item = MemoryItem.create(subject, value, mtype=mtype, kind=norm or kind)
    if not _approved(approve, confirm):
        return {"status": "proposed", "action": "remember",
                "preview": store.render_write(item),
                "hint": "re-call with approve=true to commit (your explicit approval)"}
    # H4: scan subject AND value so a secret pasted into the subject can't slip the gate.
    res = _gated_write(surface.mokata_dir, "memory", f"memory:{subject}",
                       f"{subject}\n{value}",
                       lambda: store.remember(item, assume_yes=True).committed)
    return res


@_tool("import_stack", "write")
def import_stack(path: str = ".", file: str = "", approve: bool = False,
                 force: bool = False, confirm: Optional[bool] = None) -> Dict[str, Any]:
    """Validate and apply a shared stack manifest. HUMAN-GATED: without `approve=true` this
    only validates and reports what WOULD apply (no write). With `approve=true` it applies
    (use `force=true` to overwrite an existing config)."""
    from .share import apply_manifest, load_shared, validate_shared
    try:
        data = load_shared(file)
    except (OSError, ValueError) as exc:
        return {"status": "error", "message": f"cannot read {file}: {exc}"}
    errors = validate_shared(data)
    if not _approved(approve, confirm):
        return {"status": "proposed", "action": "import",
                "valid": not errors, "errors": errors,
                "would_apply_profile": data.get("profile"),
                "hint": "re-call with approve=true to apply (your explicit approval)"}
    if errors:
        return {"status": "blocked", "committed": False,
                "reason": "rejected: shared manifest is invalid", "errors": errors}
    surface_dir = _mokata_dir(path)
    box: Dict[str, Any] = {}

    def _apply() -> Any:
        result = apply_manifest(path, data, assume_yes=True, force=force)
        box["apply"] = result
        return {"applied": result.applied, "path": result.path,
                "message": result.message}

    res = _gated_write(surface_dir, "config", surface_dir, "", _apply)
    return res


@_tool("reset", "write")
def reset(path: str = ".", keep_config: bool = False,
          approve: bool = False, confirm: Optional[bool] = None) -> Dict[str, Any]:
    """Remove mokata state (.mokata/). HUMAN-GATED: without `approve=true` this lists what
    WOULD be removed (no deletion). With `approve=true` it removes them. `keep_config`
    keeps the manifest + constitution."""
    plan = plan_reset(path, keep_config=keep_config)
    if not plan.targets:
        return {"status": "noop", "message": "reset: nothing to remove."}
    if not _approved(approve, confirm):
        return {"status": "proposed", "action": "reset", "targets": plan.targets,
                "hint": "re-call with approve=true to remove (your explicit approval)"}
    box: Dict[str, Any] = {}

    def _do_reset() -> Any:
        result = reset_state(path, keep_config=keep_config, assume_yes=True)
        box["reset"] = result
        return {"removed": result.removed, "aborted": result.aborted}

    res = _gated_write(_mokata_dir(path), "config", _mokata_dir(path), "", _do_reset)
    return res


@_tool("apply_proposal", "write")
def apply_proposal(path: str = ".", subject: str = "", decision: str = "approve",
                   approve: bool = False, confirm: Optional[bool] = None) -> Dict[str, Any]:
    """Resolve a surfaced self-healing memory proposal (contradiction/staleness).
    HUMAN-GATED: without `approve=true` it shows the staged old->new change (no write).
    With `approve=true` it applies your `decision` (approve/reject/defer)."""
    if decision not in ("approve", "reject", "defer"):
        return {"status": "error",
                "message": "decision must be one of approve/reject/defer"}
    surface = _surface(path)
    store = MemoryStore.from_surface(surface)
    match = next((p for p in store.detect_issues() if p.subject == subject), None)
    if match is None:
        return {"status": "error",
                "message": f"no pending proposal for subject '{subject}'"}
    if not _approved(approve, confirm):
        return {"status": "proposed", "action": "apply_proposal", "subject": subject,
                "kind": match.kind, "diff": match.diff(), "decision": decision,
                "hint": "re-call with approve=true to apply (your explicit approval)"}
    res = _gated_write(
        surface.mokata_dir, "memory", f"memory:{subject}", match.diff(),
        lambda: store.apply_proposal(match, decision, assume_yes=True).message)
    return res


@_tool("memory_export", "write")
def memory_export(path: str = ".", file: str = "", approve: bool = False,
                  confirm: Optional[bool] = None) -> Dict[str, Any]:
    """Export local memory (active items + provenance) to a committable share file. READ-ONLY
    on the store. HUMAN-GATED: without `approve=true` it reports how many items WOULD be
    written (no file); with `approve=true` it writes the share file."""
    from .memory import MEMORY_SHARE_FILENAME, export_memory
    surface = _surface(path)
    store = MemoryStore.from_surface(surface)
    data = export_memory(store)             # read-only; computes the items without writing
    dest = file or os.path.join(path, MOKATA_DIR, MEMORY_SHARE_FILENAME)
    if not _approved(approve, confirm):
        return {"status": "proposed", "action": "memory_export",
                "items": len(data["items"]), "dest": dest,
                "hint": "re-call with approve=true to write the share file"}
    export_memory(store, dest=dest)
    return {"status": "committed", "committed": True, "dest": dest,
            "items": len(data["items"])}


@_tool("memory_import", "write")
def memory_import(path: str = ".", file: str = "", approve: bool = False,
                  confirm: Optional[bool] = None) -> Dict[str, Any]:
    """Merge a memory share file into local memory. HUMAN-GATED: without `approve=true` it
    validates + reports how many items WOULD merge (no write); with `approve=true` it dedups,
    gate-adds new items, and routes conflicts through the self-healing surface (never a silent
    overwrite). The imported content is UNTRUSTED, so each item is secret-scanned through the
    WriteGate and audit-logged — a secret is HARD-BLOCKED, not imported. Provenance is preserved."""
    from .memory import import_memory, load_memory_share
    surface = _surface(path)
    try:
        data = load_memory_share(file)
    except (OSError, ValueError) as exc:
        return {"status": "error", "message": f"cannot read {file}: {exc}"}
    if not _approved(approve, confirm):
        n = len(data["items"]) if isinstance(data.get("items"), list) else 0
        return {"status": "proposed", "action": "memory_import", "incoming": n,
                "hint": "re-call with approve=true to merge (dedup + gated healing)"}
    ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
    res = import_memory(MemoryStore.from_surface(surface), data, assume_yes=True,
                        ledger=ledger)
    return {"status": "aborted" if res.aborted else "committed",
            "committed": not res.aborted, "added": res.added, "skipped": res.skipped,
            "resolved": res.resolved, "declined": res.declined,
            "blocked": res.blocked, "errors": res.errors}


@_tool("vault_list", "read")
def vault_list(path: str = ".") -> Dict[str, Any]:
    """List the team design vault's entries (brainstorm/spec artifacts) with name, kind,
    author, and date. Read-only."""
    from .vault import vault_list as _list
    entries = _list(path)
    return {"count": len(entries),
            "entries": [{"name": e.name, "kind": e.kind, "title": e.title,
                         "author": e.author, "version": e.version,
                         "updated_at": e.updated_at} for e in entries]}


@_tool("vault_search", "read")
def vault_search(path: str = ".", query: str = "") -> Dict[str, Any]:
    """Search the design vault by name/title/body (lexical), ranked. Read-only."""
    from .vault import vault_search as _search
    hits = _search(path, query)
    return {"count": len(hits),
            "hits": [{"name": h.entry.name, "kind": h.entry.kind, "title": h.entry.title,
                      "score": round(h.score, 4), "author": h.entry.author,
                      "updated_at": h.entry.updated_at} for h in hits]}


@_tool("vault_pull", "read")
def vault_pull(path: str = ".", name: str = "", dest: str = "") -> Dict[str, Any]:
    """Pull a named design artifact for review (returns the markdown; optionally writes it to
    `dest`). Read-only on the vault."""
    from .vault import VaultError, vault_pull as _pull
    try:
        content, entry = _pull(path, name, dest=dest or None)
    except VaultError as exc:
        return {"status": "error", "message": str(exc)}
    return {"status": "ok", "name": entry.name, "kind": entry.kind,
            "version": entry.version, "author": entry.author,
            "updated_at": entry.updated_at, "dest": dest or None, "content": content}


@_tool("vault_push", "write")
def vault_push(path: str = ".", name: str = "", file: str = "", kind: str = "",
               author: str = "", force: bool = False,
               approve: bool = False, confirm: Optional[bool] = None) -> Dict[str, Any]:
    """Push a brainstorm/spec markdown artifact into the team vault under `name`. HUMAN-GATED:
    without `approve=true` this reports what WOULD happen (no write). With `approve=true` it
    writes through the WriteGate (a secret in the artifact is blocked even when approved). A
    changed re-push needs `force=true` (it versions, keeping prior metadata — never a silent
    clobber)."""
    from .vault import VaultError, commit_push, plan_push, _artifact_path
    try:
        plan = plan_push(path, name, file, kind=kind or None, force=force)
    except VaultError as exc:
        return {"status": "error", "message": str(exc)}
    if plan.status == "unchanged":
        return {"status": "unchanged", "committed": False, "name": plan.name,
                "reason": plan.reason()}
    if plan.blocked:
        return {"status": "conflict", "committed": False, "name": plan.name,
                "reason": plan.reason(),
                "hint": "re-call with force=true to version it (nothing is clobbered)"}
    if not _approved(approve, confirm):
        return {"status": "proposed", "action": "vault_push", "name": plan.name,
                "kind": plan.kind, "next_version": plan.next_version, "reason": plan.reason(),
                "hint": "re-call with approve=true to write (your explicit approval)"}
    res = _gated_write(
        _mokata_dir(path), "config", _artifact_path(path, plan.name), plan.content,
        lambda: commit_push(path, plan, author=author).version)
    res["name"] = plan.name
    res["version"] = res.get("result")
    return res


@_tool("session_push", "write")
def session_push(path: str = ".", tag: str = "", run_id: str = "", author: str = "",
                 force: bool = False, transport: str = "local", approve: bool = False,
                 confirm: Optional[bool] = None) -> Dict[str, Any]:
    """Stage 55a/55b — package the CURRENT session (run checkpoint(s) + approved approach +
    emitted spec + in-progress brainstorm) into a MACHINE-PATH-FREE, versioned, secret-scanned
    bundle and share it over `transport` (local | vault | postgres). HUMAN-GATED: without
    `approve=true` this reports what WOULD happen (no write). With `approve=true` it writes through
    the WriteGate (a secret in the session is hard-blocked even when approved) — on EVERY
    transport. A changed re-push needs `force=true` (never a silent clobber); no session in
    progress → a friendly no-op. An unreachable remote (no psycopg/DSN) degrades clean
    (status 'unavailable') and NEVER silently falls back to a less-secure store."""
    from . import session_bundle as SB
    from . import session_transport as STX
    try:
        t = STX.make_transport(transport, path)
    except STX.SessionTransportUnavailable as exc:
        return {"status": "unavailable", "committed": False, "transport": transport,
                "message": str(exc)}
    try:
        plan = SB.plan_session_push(path, _surface(path), tag, run_id=run_id or None,
                                    force=force, author=author, transport=t)
    except SB.SessionBundleError as exc:
        return {"status": "error", "message": str(exc)}
    if plan.status == "empty":
        return {"status": "empty", "committed": False, "reason": plan.reason()}
    if plan.status == "unchanged":
        return {"status": "unchanged", "committed": False, "tag": plan.tag,
                "reason": plan.reason()}
    if plan.blocked:
        return {"status": "conflict", "committed": False, "tag": plan.tag,
                "reason": plan.reason(),
                "hint": "re-call with force=true to overwrite (the prior bundle is replaced)"}
    if not _approved(approve, confirm):
        return {"status": "proposed", "action": "session_push", "tag": plan.tag,
                "resume": plan.bundle.get("resume"), "reason": plan.reason(),
                "hint": "re-call with approve=true to write (your explicit approval)"}
    ledger = AuditLedger.from_mokata_dir(_mokata_dir(path))
    res = SB.commit_session_push_gated(plan, ledger=ledger, assume_yes=True)
    return {"status": "committed" if res.committed else "blocked",
            "committed": res.committed, "tag": plan.tag, "reason": res.reason,
            "findings": [f.kind for f in res.findings]}


@_tool("session_pull", "write")
def session_pull(path: str = ".", tag: str = "", into: str = "", force: bool = False,
                 transport: str = "local", approve: bool = False,
                 confirm: Optional[bool] = None) -> Dict[str, Any]:
    """Stage 55a/55b — pull a tagged session bundle over `transport` (local | vault | postgres)
    and re-hydrate it into a repo so `mokata resume` continues the work. The bundle is UNTRUSTED,
    so this is HUMAN-GATED and SECRET-SCANNED on pull — on EVERY transport: without `approve=true`
    it reports what WOULD hydrate (no write); with `approve=true` it hydrates through the WriteGate
    (a secret is hard-blocked). The content-hash is verified (corruption caught from any source),
    and a CROSS-CODEBASE fingerprint mismatch is surfaced (status 'mismatch') and NOT applied
    unless `force=true`. `into` is the target repo (default: this repo). An unreachable remote
    degrades clean (status 'unavailable'). The HARD-GATE survives: a not-yet-approved brainstorm
    stays not approved."""
    from . import session_bundle as SB
    from . import session_transport as STX
    try:
        t = STX.make_transport(transport, path)
    except STX.SessionTransportUnavailable as exc:
        return {"status": "unavailable", "committed": False, "transport": transport,
                "message": str(exc)}
    target = into or path
    try:
        plan = SB.plan_session_pull(path, tag, target, force=force, transport=t)
    except SB.SessionBundleError as exc:
        return {"status": "error", "message": str(exc)}
    if plan.status == "missing":
        return {"status": "missing", "committed": False, "tag": plan.tag,
                "reason": plan.reason()}
    if plan.status == "mismatch":
        return {"status": "mismatch", "committed": False, "tag": plan.tag,
                "reason": plan.reason(), "bundle_fingerprint": plan.bundle_fingerprint,
                "target_fingerprint": plan.target_fingerprint,
                "hint": "re-call with force=true to apply it here anyway (explicit override)"}
    if not _approved(approve, confirm):
        return {"status": "proposed", "action": "session_pull", "tag": plan.tag,
                "into": target, "resume": plan.bundle.get("resume"), "reason": plan.reason(),
                "hint": "re-call with approve=true to hydrate (your explicit approval)"}
    target_surface = _surface(target)
    ledger = AuditLedger.from_mokata_dir(_mokata_dir(target))
    res = SB.hydrate_bundle(target_surface, plan.bundle, ledger=ledger, assume_yes=True)
    return {"status": "committed" if res.committed else "blocked",
            "committed": res.committed, "tag": plan.tag, "into": target,
            "resume": plan.bundle.get("resume"), "reason": res.reason,
            "findings": [f.kind for f in res.findings]}


@_tool("session_name", "write")
def session_name(path: str = ".", tag: str = "", new: str = "", force: bool = False,
                 transport: str = "local", approve: bool = False,
                 confirm: Optional[bool] = None) -> Dict[str, Any]:
    """Stage 55b — give a tagged session a human-friendly name (rename) over `transport`
    (local | vault | postgres). HUMAN-GATED where it writes durable: without `approve=true` it
    reports what WOULD change (no write); with `approve=true` it moves the bundle through the
    WriteGate. Idempotent (renaming to the current name is a no-op); a name collision is REFUSED
    unless `force=true` (NEVER a silent clobber); provenance is preserved and the content-hash is
    untouched. An unreachable remote degrades clean (status 'unavailable')."""
    from . import session_bundle as SB
    from . import session_transport as STX
    try:
        t = STX.make_transport(transport, path)
    except STX.SessionTransportUnavailable as exc:
        return {"status": "unavailable", "committed": False, "transport": transport,
                "message": str(exc)}
    try:
        plan = SB.plan_session_rename(path, tag, new, transport=t, force=force)
    except SB.SessionBundleError as exc:
        return {"status": "error", "message": str(exc)}
    if plan.status == "noop":
        return {"status": "noop", "committed": False, "old": plan.old, "new": plan.new,
                "reason": plan.reason()}
    if plan.status == "missing":
        return {"status": "missing", "committed": False, "old": plan.old, "new": plan.new,
                "reason": plan.reason()}
    if plan.status == "collision":
        return {"status": "conflict", "committed": False, "old": plan.old, "new": plan.new,
                "reason": plan.reason(),
                "hint": "re-call with force=true to overwrite the colliding name"}
    if not _approved(approve, confirm):
        return {"status": "proposed", "action": "session_name", "old": plan.old,
                "new": plan.new, "reason": plan.reason(),
                "hint": "re-call with approve=true to rename (your explicit approval)"}
    ledger = AuditLedger.from_mokata_dir(_mokata_dir(path))
    res = SB.commit_session_rename_gated(plan, ledger=ledger, assume_yes=True)
    return {"status": "committed" if res.committed else "blocked",
            "committed": res.committed, "old": plan.old, "new": plan.new, "reason": res.reason,
            "findings": [f.kind for f in res.findings]}


@_tool("audit_share", "write")
def audit_share(path: str = ".", approve: bool = False,
                confirm: Optional[bool] = None) -> Dict[str, Any]:
    """Stage 71 — publish this dev's NEW local audit entries to the team's SHARED log (the team's
    OWN managed Postgres — NO telemetry, nothing phoned home to mokata/Anthropic). OPT-IN
    (`settings.audit.shared`) + LOCAL-FIRST. HUMAN-GATED: without `approve=true` this is
    propose-only — it reports how many entries WOULD publish and writes nothing. With
    `approve=true` it publishes through the universal WriteGate (kind `send`: a secret is
    hard-blocked even when approved), APPEND-ONLY + per-actor + namespaced so concurrent
    teammates never clobber each other. Degrade-clean: sharing off / no driver-or-DSN → a clear
    message, the log stays LOCAL, no crash. The DSN secret is never stored."""
    from .team_audit import pending_share, share_audit, shared_enabled
    surface = _surface(path)
    if not shared_enabled(surface.manifest.data):
        return {"status": "disabled", "committed": False,
                "message": ("team audit sharing is OFF (local-first). Opt in with "
                            "`mokata config set settings.audit.shared true`.")}
    if not _approved(approve, confirm):
        available, pending, dsn_env, message = pending_share(path, surface)
        return {"status": "proposed", "action": "audit_share", "available": available,
                "pending": pending, "dsn_env": dsn_env, "message": message,
                "hint": "re-call with approve=true to publish (your explicit approval)"}
    msgs: List[str] = []
    res = share_audit(path, surface, assume_yes=True, out=msgs.append,
                      ledger=AuditLedger.from_mokata_dir(surface.mokata_dir))
    status = ("committed" if res.committed and res.published else
              "unavailable" if res.reason == "unavailable" else
              "in_sync" if res.reason == "in sync" else
              "blocked")
    return {"status": status, "committed": res.committed, "published": res.published,
            "reason": res.reason, "findings": [f.kind for f in res.findings],
            "message": res.message, "log": msgs}


@_tool("spec_check", "write")
def spec_check(path: str = ".", symbols: str = "", files: str = "", text: str = "",
               phase: str = "develop", approve: bool = False,
               confirm: Optional[bool] = None) -> Dict[str, Any]:
    """Stage 37 regression guard: check a change's touch-set (comma-separated `symbols`/`files`)
    against the SAVED specs + decision memory. If it would affect one, this is HUMAN-GATED:
    without `approve=true` it SURFACES the conflict and writes nothing but the surfaced-deviation
    record (status 'blocked'); with `approve=true` it records your confirmation (amend/supersede)
    through the deviation gate. No saved corpus → 'skipped'; no graph → lexical/file overlap (the
    result says so). Frugal: only the touch-set is checked."""
    from .engine import ChangeSet, check_change, load_decisions, load_spec_corpus
    from .govern.deviation import ACCEPTANCE_CRITERIA, DeviationGate, DeviationRequest
    surface = _surface(path)
    change = ChangeSet(
        symbols=[s.strip() for s in symbols.split(",") if s.strip()],
        files=[f.strip() for f in files.split(",") if f.strip()], text=text)
    specs = load_spec_corpus(surface.state)
    decisions = load_decisions(MemoryStore.from_surface(surface))
    layer = KnowledgeLayer.from_surface(surface)
    report = check_change(change, specs, decisions, layer=layer)
    if not report.checked:
        return {"status": "skipped", "message": report.note, "conflicts": []}
    if not report.has_conflicts:
        return {"status": "ok", "conflicts": [], "degraded": report.degraded,
                "note": report.note}

    ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
    ledger.record("spec_conflict", phase=phase, degraded=report.degraded,
                  conflicts=[c.to_dict() for c in report.conflicts],
                  touch_set=report.touch_set)
    refs = ", ".join(f"{c.source_kind} '{c.ref}'" for c in report.conflicts)
    req = DeviationRequest(
        what=f"this change affects saved {refs}",
        why="the touched surface is already specified/decided",
        options=["confirm + amend/supersede the affected spec(s)/decision(s)",
                 "re-plan so the change does not break them"],
        target=ACCEPTANCE_CRITERIA, phase=phase)
    gate = DeviationGate(ledger)
    if not _approved(approve, confirm):
        gate.request(req)       # log that it was surfaced (proposed); resolve nothing yet
        return {"status": "blocked", "committed": False,
                "conflicts": [c.to_dict() for c in report.conflicts],
                "degraded": report.degraded, "render": report.render(),
                "hint": "re-call with approve=true to confirm the change (amend/supersede), "
                        "or re-plan so it doesn't break the saved spec/decision"}
    outcome = gate.submit(req, assume_yes=True)
    return {"status": "confirmed", "committed": True, "reason": outcome.reason,
            "conflicts": [c.to_dict() for c in report.conflicts]}


@_tool("init", "write")
def init(path: str = ".", profile: str = "standard", approve: bool = False,
         force: bool = False, confirm: Optional[bool] = None) -> Dict[str, Any]:
    """Initialize mokata in a repo (write .mokata/manifest.json + constitution) so a new
    project can be set up from inside Claude Code — no terminal trip. HUMAN-GATED: without
    `approve=true` this PREVIEWS the plan (detected tools, profile, files it would write)
    and writes nothing. With `approve=true` it applies; an existing manifest needs
    `force=true` to overwrite (a profile switch is never silent)."""
    from .init import init_repo, plan_init, render_plan
    from .profiles import profile_names
    if profile not in profile_names():
        return {"status": "error",
                "message": f"unknown profile '{profile}'; choose one of {profile_names()}"}
    already = Surface.is_initialized(path)
    if not _approved(approve, confirm):
        return {"status": "proposed", "action": "init", "profile": profile,
                "already_initialized": already,
                "preview": render_plan(plan_init(path, profile)),
                "hint": ("re-call with approve=true to apply"
                         + (" plus force=true to overwrite the existing manifest"
                            if already else ""))}
    if already and not force:
        return {"status": "blocked", "committed": False,
                "reason": "a manifest already exists — re-call with force=true to "
                          "overwrite (a profile switch is never silent)"}
    box: Dict[str, Any] = {}

    def _do_init() -> Any:
        res = init_repo(root=path, profile=profile, assume_yes=True, force=force,
                        out=lambda *_a: None)
        box["init"] = res
        return {"written": res.written, "aborted": res.aborted, "profile": profile}

    return _gated_write(_mokata_dir(path), "config", _mokata_dir(path), "", _do_init)


@_tool("reconfigure", "write")
def reconfigure(path: str = ".", profile: str = "", add: str = "", remove: str = "",
                approve: bool = False, confirm: Optional[bool] = None) -> Dict[str, Any]:
    """Stage 56b — re-runnable reconfigure: change what's wired on an ALREADY-INITIALIZED repo
    (switch `profile`, `add`/`remove` integrations — comma-separated tool ids) WITHOUT a terminal
    trip. HUMAN-GATED: without `approve=true` it returns the current→proposed DIFF and writes
    nothing; with `approve=true` it applies (gated, idempotent, reversible — a removed integration
    leaves no residue; an ABSENT add is recommended, never installed). No changes → a friendly
    no-op; an uninitialized repo degrades clean (run `init`/`setup` first)."""
    from . import onboarding
    if not Surface.is_initialized(path):
        return {"status": "uninitialized", "committed": False,
                "message": "this repo isn't initialized — run init/setup first"}
    add_l = [t.strip() for t in add.split(",") if t.strip()]
    rem_l = [t.strip() for t in remove.split(",") if t.strip()]
    plan = onboarding.plan_reconfigure(path, profile=(profile or None),
                                       add=(add_l or None), remove=(rem_l or None))
    if not plan.changed:
        return {"status": "unchanged", "committed": False,
                "reason": "no changes — your setup already matches",
                "recommended": plan.recommended}
    if not _approved(approve, confirm):
        return {"status": "proposed", "action": "reconfigure",
                "diff": onboarding.render_reconfigure_diff(plan),
                "added": plan.added, "removed": plan.removed,
                "profile": plan.target_profile if plan.profile_changed else None,
                "recommended": plan.recommended,
                "hint": "re-call with approve=true to apply (your explicit approval)"}
    ledger = AuditLedger.from_mokata_dir(_mokata_dir(path))
    res = onboarding.run_reconfigure(path, profile=(profile or None), add=(add_l or None),
                                     remove=(rem_l or None), assume_yes=True, ledger=ledger,
                                     out=lambda *_a: None)
    return {"status": "committed" if res.changed else "blocked", "committed": res.changed,
            "added": res.added, "removed": res.removed, "profile": res.profile,
            "recommended": res.recommended}


@_tool("config_set", "write")
def config_set(path: str = ".", key: str = "", value: str = "", approve: bool = False,
               confirm: Optional[bool] = None) -> Dict[str, Any]:
    """Set a dotted backend-config key in the committed manifest (Stage 24A), e.g.
    `tools.sqlite.config.path`. HUMAN-GATED: without `approve=true` it PREVIEWS the old->new
    change and writes nothing; with `approve=true` it writes through the WriteGate. A secret
    in the resulting manifest (an inline DSN/credential) is a HARD BLOCK even when approved —
    reference an env var (e.g. config.dsn_env) instead. A structurally-invalid edit is refused,
    not committed."""
    from . import config_cmd
    ledger = AuditLedger.from_mokata_dir(_mokata_dir(path))
    msgs: List[str] = []
    try:
        if not _approved(approve, confirm):
            # Propose: run the full secret-scan + schema-validate, but NEVER commit.
            res = config_cmd.config_set(path, key, value, assume_yes=False,
                                        confirm=lambda _q: False, out=msgs.append,
                                        ledger=ledger)
            if res.findings:
                return {"status": "blocked", "committed": False,
                        "reason": "secret detected in the manifest — reference an env var "
                                  "instead (e.g. config.dsn_env)",
                        "findings": [f.kind for f in res.findings], "detail": msgs}
            return {"status": "proposed", "action": "config_set", "key": key,
                    "old": res.old, "new": res.new, "detail": msgs,
                    "hint": "re-call with approve=true to write (your explicit approval)"}
        res = config_cmd.config_set(path, key, value, assume_yes=True, out=msgs.append,
                                    ledger=ledger)
    except config_cmd.ConfigCommandError as exc:
        return {"status": "error", "message": str(exc)}
    return {"status": "committed" if res.committed else "blocked",
            "committed": res.committed, "key": key, "old": res.old, "new": res.new,
            "findings": [f.kind for f in res.findings], "reason": res.message, "detail": msgs}


@_tool("export_stack", "write")
def export_stack(path: str = ".", file: str = "", approve: bool = False,
                 confirm: Optional[bool] = None) -> Dict[str, Any]:
    """Export the current manifest as a shareable stack file (J3). HUMAN-GATED: without
    `approve=true` it reports what WOULD be written (no file); with `approve=true` it writes
    through the WriteGate — the exported content is secret-scanned, so a secret is hard-blocked
    even when approved. Default destination is .mokata/mokata-stack.json. The read-only
    counterpart is `export_preview`."""
    from .manifest import Manifest
    from .share import SHARE_FILENAME, export_manifest
    surface = _surface(path)
    data = export_manifest(surface)             # dest=None → no write
    dest = file or os.path.join(path, MOKATA_DIR, SHARE_FILENAME)
    content = Manifest.from_dict(data).to_json()
    if not _approved(approve, confirm):
        return {"status": "proposed", "action": "export_stack", "dest": dest,
                "profile": data.get("profile"),
                "hint": "re-call with approve=true to write the stack file"}
    return _gated_write(surface.mokata_dir, "config", dest, content,
                        lambda: (export_manifest(surface, dest=dest), dest)[1])


# --------------------------------------------------------------------------------------
# Stage 70 — community stacks (list/search/show read; install = the gated adopt path).
# NO hosted marketplace: publish is over git/the vault; the index is a reviewable index.json.
# --------------------------------------------------------------------------------------
@_tool("stacks_list", "read")
def stacks_list(path: str = ".", source: str = "") -> Dict[str, Any]:
    """List the curated community-stack catalog (per-framework governed stacks). `source` is an
    optional git-org/vault catalog dir or index.json; default is the bundled curated index.
    Read-only. There is NO hosted marketplace — this reads a versioned index.json."""
    from . import stacks as ST
    try:
        index = ST.load_index(source or None)
    except ST.StackError as exc:
        return {"status": "error", "message": str(exc)}
    entries = ST.list_stacks(index)
    return {"status": "ok", "hosted": False, "count": len(entries), "stacks": entries,
            "note": ST.HONEST_NOTE}


@_tool("stacks_search", "read")
def stacks_search(path: str = ".", query: str = "", source: str = "") -> Dict[str, Any]:
    """Search the community-stack catalog by name/framework/summary/tags (lexical), ranked.
    Read-only. `source` optionally points at a git-org/vault index.json instead of the bundled one."""
    from . import stacks as ST
    try:
        index = ST.load_index(source or None)
    except ST.StackError as exc:
        return {"status": "error", "message": str(exc)}
    hits = ST.search_stacks(query, index)
    return {"status": "ok", "count": len(hits),
            "hits": [dict(h.entry, score=round(h.score, 4)) for h in hits]}


@_tool("stacks_show", "read")
def stacks_show(path: str = ".", name: str = "", source: str = "") -> Dict[str, Any]:
    """Show one community stack's catalog entry (framework, curated-guardrail count, recommended
    skills, tags). Read-only. `source` optionally points at a git-org/vault index.json."""
    from . import stacks as ST
    try:
        index = ST.load_index(source or None)
    except ST.StackError as exc:
        return {"status": "error", "message": str(exc)}
    entry = ST.show_stack(name, index)
    if entry is None:
        return {"status": "not_found", "message": f"no stack named '{name}' in the catalog"}
    return {"status": "ok", "hosted": False, "stack": entry}


@_tool("stacks_install", "write")
def stacks_install(path: str = ".", name: str = "", source: str = "", force: bool = False,
                   approve: bool = False, confirm: Optional[bool] = None) -> Dict[str, Any]:
    """Install a catalog stack as this repo's config — the human-gated, secret-scanned ADOPT
    path (reuses `apply_manifest`). HUMAN-GATED: without `approve=true` it reports what WOULD
    apply (no write); with `approve=true` it applies through the WriteGate, where the stack
    manifest is secret-scanned so a secret is hard-blocked EVEN when approved (community content
    is untrusted). `force` overwrites an existing config. No hosted marketplace is involved."""
    from . import stacks as ST
    try:
        raw, data = ST.resolve_stack_manifest(name, source=source or None)
    except ST.StackError as exc:
        return {"status": "error", "message": str(exc)}
    stack_meta = (data.get("settings") or {}).get("stack") or {}
    if not _approved(approve, confirm):
        return {"status": "proposed", "action": "stacks_install", "name": name,
                "profile": data.get("profile"), "framework": stack_meta.get("framework"),
                "hint": "re-call with approve=true to install (the gated, secret-scanned adopt)"}
    surface_dir = _mokata_dir(path)
    box: Dict[str, Any] = {}

    def _apply() -> Any:
        from .share import apply_manifest
        result = apply_manifest(path, data, assume_yes=True, force=force)
        box["apply"] = result
        return {"applied": result.applied, "path": result.path, "message": result.message}

    # Feed the RAW manifest text so the WriteGate secret-scan is the absolute hard block.
    return _gated_write(surface_dir, "config", surface_dir, raw, _apply)


# --------------------------------------------------------------------------------------
# MCP wiring — lazily imports the optional SDK; never imported by the core/CLI.
# --------------------------------------------------------------------------------------
def mcp_available() -> bool:
    """True when the optional MCP SDK is importable."""
    import importlib.util
    return importlib.util.find_spec("mcp") is not None


def build_server() -> Any:
    """Construct the FastMCP server with every tool registered. Requires the optional
    `mcp` SDK (`pip install "mokata[mcp]"`)."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - exercised only with the SDK absent
        raise RuntimeError(
            "the MCP SDK is not installed; run `pip install \"mokata[mcp]\"` to enable "
            "the mokata MCP server"
        ) from exc

    server = FastMCP(SERVER_NAME)
    for spec in TOOLS:
        server.add_tool(spec.fn, name=spec.name,
                        description=(spec.fn.__doc__ or "").strip())
    return server


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="mokata-mcp",
        description="mokata MCP server (stdio) — mokata operations as native MCP tools.")
    parser.add_argument("--path", default=".",
                        help="repo root the tools operate on (default: current dir)")
    parser.parse_args(argv)
    build_server().run()   # stdio transport (plugin-launched); each tool takes its own `path`
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
