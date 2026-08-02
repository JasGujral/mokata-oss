"""READ tools — safe, expose their data directly, write nothing.

Every function is pure and SDK-free (fully usable/testable without the `mcp` SDK). Each is
registered into the shared `TOOLS` registry via `@_tool(name, "read")`; heavier engine imports
stay lazy inside the functions so importing this module is cheap and dependency-light.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from .. import MOKATA_DIR
from ..adapters import AdapterContract, negotiate, overlapping_capabilities
from ..config import Surface
from ..engine import preview_pipeline
from ..govern import AuditLedger, BudgetReport, diagnose
from ..knowledge import QUERY_KINDS, KnowledgeLayer
from ..memory import MemoryStore
from .pagination import DEFAULT_PAGE_LIMIT, paginate
from .registry import _mokata_dir, _surface, _tool
from .response_format import LazyRender, apply_response_format
from .validation import validate_comma_list, validate_enum

# MCP-R.D1d — the allowed `kind` values of the `query` tool, grounded on the router it feeds:
# `knowledge.layer.run_query` handles `semantic` itself and delegates every other kind to the
# backends, all three of which reject anything outside `QUERY_KINDS`. Composed here (rather than
# hard-coded) so a new structural kind added to the graph layer is accepted by the tool for free.
QUERY_TOOL_KINDS = tuple(QUERY_KINDS) + ("semantic",)

__all__ = [
    "query", "recall", "consolidate_proposals", "doctor", "coverage", "budget", "audit",
    "status", "preview",
    "progress", "lanes", "watch", "govern", "rules", "skills", "suggest", "lat_check",
    "index_status", "tour", "ci_check", "baseline", "sessions", "session_list",
    "session_windows", "worktree_list", "session_save",
    "config_get", "export_preview", "decompose", "spec_show",
    "review_status", "review_record",       # REVIEW-FIX.R3 — the 6r loop, in-harness
    "vault_list", "vault_search",
    "vault_pull", "stacks_list", "stacks_search", "stacks_show", "plan_list", "plan_show",
]


@_tool("query", "read")
def query(path: str = ".", kind: str = "callers", target: str = "",
          depth: int = 2) -> Dict[str, Any]:
    """Run a structural code query (graph backend if present, else the grep floor). This is the
    NAVIGATION instrument: prefer it over Read/grep to find a symbol's definition, its callers,
    or everywhere it is referenced. `kind` is one of defs (where a symbol is defined) / refs
    (everywhere it is referenced) / callers / callees / implementers / imports / blast_radius, or
    `semantic` when an adopted graph exposes a semantic index (GR.S2). Degrades down the chain
    (code-review-graph -> serena -> AST floor -> grep) and every answer names the backend that
    produced it, so a lexical answer is never mistaken for a structural one. Read-only."""
    from ..knowledge.layer import run_query
    # D1d — validate BEFORE the layer is built: an unknown kind used to travel all the way to the
    # backend and raise `ValueError` (reclaimed as a server `error`), after paying for the surface
    # load and backend selection. It is a caller typo, so it refuses here, up front, for free.
    validate_enum(kind, QUERY_TOOL_KINDS, "kind")
    layer = KnowledgeLayer.from_surface(_surface(path))
    return run_query(layer, kind, target, depth=depth).to_dict()


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
        from ..memory.intelligence import explain_recall
        hits = store.recall_relevant(query)
        return _with_degrade(store, {
            "enabled": True, "backend": store.backend.name, "query": query,
            "items": [{"memory_type": e.item.mtype, "kind": e.item.effective_kind,
                       "subject": e.item.subject, "value": e.item.value, "why": e.why}
                      for e in explain_recall(query, hits)]})
    mt = memory_type or mtype
    items = store.recall(subject, mtype=mt) if subject else store.all_active(mtype=mt)
    return _with_degrade(store, {
        "enabled": True, "backend": store.backend.name,
        "items": [{"memory_type": i.mtype, "kind": i.effective_kind,
                   "mtype": i.mtype,  # deprecated alias, kept for back-compat
                   "subject": i.subject, "value": i.value}
                  for i in items]})


def _with_degrade(store: Any, resp: Dict[str, Any]) -> Dict[str, Any]:
    """CM.S2 (C-2) — attach the explicit degraded marker when a team-mode read was served from
    the LOCAL fallback, so an MCP consumer is never handed local state dressed as team state.
    The marker names the resolved env-var NAME + failure class, NEVER the DSN value. A healthy /
    local read carries no marker (byte-identical response)."""
    notice = store.degrade_notice
    if notice is not None:
        resp["degraded"] = notice.to_dict()
    # CM.S4 (C-4) — the structured local-only backlog field: the count of approved-but-unflushed
    # team writes (+ oldest age + last-failure class). Computed from the SAME routing verdict as
    # the degrade marker (store.read_routing) so the two always agree. Absent when nothing is
    # pending / in local mode (byte-identical response). Never carries a DSN value / memory content.
    ps = getattr(store, "pending_status", None)
    if ps is not None:
        resp["pending"] = ps.to_dict()
    return resp


@_tool("doctor", "read")
def doctor(path: str = ".", response_format: str = "concise") -> Dict[str, Any]:
    """Diagnose the manifest/config: missing providers, broken adapters, role conflicts,
    bad trust dials. Read-only. `response_format` {concise (default), detailed}: concise
    answers with `ok`; detailed adds the rendered `report` (P22)."""
    report = diagnose(_surface(path))
    return apply_response_format(response_format,
                                 {"ok": report.ok, "report": LazyRender(report.render)})


@_tool("coverage", "read")
def coverage(path: str = ".", response_format: str = "concise") -> Dict[str, Any]:
    """Report capability coverage, unmet gaps, and overlaps for the current stack.
    Read-only. `response_format` {concise (default), detailed}: concise answers with the
    structured `overlaps`; detailed adds the rendered coverage `report` (P22)."""
    m = _surface(path).manifest
    adapters = [AdapterContract(name=tid, provides=[t.get("provides")],
                                kind=t.get("kind", "external"))
                for tid, t in m.tools.items() if t.get("provides")]
    report = negotiate(list(m.capabilities), adapters)
    overlaps = overlapping_capabilities(m)
    return apply_response_format(response_format, {
        "report": LazyRender(report.render),
        "overlaps": {need: providers for need, providers in overlaps.items()}})


@_tool("budget", "read")
def budget(path: str = ".", response_format: str = "concise") -> Dict[str, Any]:
    """Show token savings recorded in the audit ledger (live budget readout). Read-only.
    `response_format` {concise (default), detailed}: concise answers with the `events` count;
    detailed adds the rendered `report` (P22)."""
    ledger = AuditLedger.from_mokata_dir(_mokata_dir(path))
    report = BudgetReport.from_ledger(ledger)
    if not report.events:
        return apply_response_format(response_format, {
            "events": 0, "report": LazyRender(lambda: "budget: no savings recorded yet.")})
    return apply_response_format(response_format, {
        "events": len(report.events), "report": LazyRender(report.render)})


@_tool("audit", "read")
def audit(path: str = ".", limit: int = DEFAULT_PAGE_LIMIT, offset: int = 0,
          team: bool = False) -> Dict[str, Any]:
    """Show the append-only audit ledger (every gate decision + tool call), PAGED newest-first:
    `offset=0` is the most recent `limit` entries and paging forward walks BACK in time, each page
    chronological inside itself. `limit` defaults to 50 (MCP-R.D1c — the whole ledger is an
    unbounded payload); pass `limit=0` to explicitly opt out and get everything. The result carries
    `count` (this page), `total` (the whole ledger), `has_more`, and `next_offset` (null at the end)
    so you know when there is more. With `team=true` (Stage 71) show the TEAM-WIDE who-did-what over
    the SHARED log instead — spanning all actors, on the team's OWN storage (NO telemetry, nothing
    phoned home), paged the same way. Degrade-clean: sharing off / backend absent → available false
    with a clear message. Read-only."""
    if team:
        from ..team_audit import render_team_timeline, team_audit_view
        view = team_audit_view(path, _surface(path))
        if not view.available:
            return {"team": True, "available": False, "message": view.message,
                    "count": 0, "entries": []}
        return {"team": True, "available": True, "actors": view.actors,
                **paginate(view.entries, key="entries", limit=limit, offset=offset,
                           from_end=True),
                "who_did_what": render_team_timeline(view)}
    entries = AuditLedger.from_mokata_dir(_mokata_dir(path)).entries()
    return paginate(entries, key="entries", limit=limit, offset=offset, from_end=True)


@_tool("status", "read")
def status(path: str = ".") -> Dict[str, Any]:
    """One-line stack summary: version, profile, and what each capability resolves to right
    now. Read-only."""
    surface = _surface(path)
    m = surface.manifest
    resp = {"version": m.mokata_version, "profile": m.profile,
            "capabilities": [r.summary() for r in surface.router.resolve_all()]}
    # CM.S4 (C-4) — surface the local-only team backlog here too (structured), reusing the ONE
    # CM.S2 routing verdict so it agrees with the degraded read notice. Absent in local mode / when
    # nothing is pending. Degrade-clean — a hiccup never breaks the status readout.
    try:
        from ..degrade import resolve_read_routing
        from ..flush_liveness import pending_status
        ps = pending_status(surface, routing=resolve_read_routing(surface))
        if ps is not None:
            resp["pending"] = ps.to_dict()
    except Exception:  # pragma: no cover - surfacing is best-effort
        # (iv) SUPPRESS-OK: an ADDITIVE, optional key on an otherwise-complete status response. Both
        # callees are themselves degrade-clean and loud — `resolve_read_routing` never raises on the
        # read path and carries its own DegradeNotice — so nothing is hidden here; the only thing
        # this guard can lose is the pending COUNT, and the degrade itself is reported elsewhere.
        # Broad because it spans routing + journal + team-health, three subsystems' classes.
        pass
    # DOC-ONBOARD — the channel that survives dead hooks. When the wiring is a dead bare name,
    # mokata's hooks never launch, so the SessionStart briefing cannot say a word — but the MCP
    # server is registered separately and still answers, which makes THIS the only in-session
    # surface left. Same verdict (`hook_wiring.wiring_drift`) and same wording
    # (`wiring_drift_line`) the doctor finding and the briefing use — one source, three surfaces.
    # ABSENT when the wiring is current: a key that is always there is a key nobody reads.
    try:
        from ..hook_wiring import wiring_drift, wiring_drift_line
        drift = wiring_drift(path)
        if drift.drifted:
            resp["wiring"] = {"stale": True, "codes": drift.codes, "reasons": drift.reasons,
                              "where": drift.where, "fix": "mokata setup claude",
                              "note": wiring_drift_line(drift)}
    except Exception:  # pragma: no cover - surfacing is best-effort
        # (iv) SUPPRESS-OK: an ADDITIVE, optional key, and `wiring_drift` is itself never-raise
        # (it returns `checked=False` rather than throwing). What could still reach here is a
        # half-installed package; losing an advisory key must not break a status readout.
        pass
    return resp


@_tool("preview", "read")
def preview(path: str = ".", start: Optional[str] = None,
            stop: Optional[str] = None) -> Dict[str, Any]:
    """Dry-run the pipeline: planned phases, gates, and file touches. No side effects."""
    pv = preview_pipeline(start=start, stop=stop, mokata_dir=_surface(path).mokata_dir)
    return {"preview": pv.render()}


@_tool("progress", "read")
def progress(path: str = ".", run: Optional[str] = None,
             response_format: str = "concise") -> Dict[str, Any]:
    """Where are we? The run-progress tracker (done/current/pending + counts) derived from
    the persisted run-state. Read-only; with no active run it returns a clean, inactive view
    (never an error). `run` selects a specific run id (default: the active/most-recent).
    `response_format` {concise (default), detailed}: detailed adds the rendered `block` view;
    concise returns the structured tracker only (P22 — no ASCII table you didn't ask for)."""
    from ..progress import build_progress, render_progress
    surface = _surface(path)
    p = build_progress(surface.state, run_id=run)
    out = p.to_dict()
    # Stage 6d — MAX detail: the phase tracker + the 5 user-stage arc + 6c develop sub-counter
    # + what's pending this session, all derived from the same _badge_state single source.
    # MCP-R.D1b — the rendered `block` is a LazyRender: dropped under concise (the default), built
    # only under `response_format:detailed` (byte-identical to today). Never computed under concise.
    out["block"] = LazyRender(lambda: render_progress(p, surface=surface))
    return apply_response_format(response_format, out)


@_tool("lanes", "read")
def lanes(path: str = ".", run: Optional[str] = None,
          ascii_only: bool = False, response_format: str = "concise") -> Dict[str, Any]:
    """The PARALLEL-aware lane view: one lane per concurrent subagent
    (running/done/blocked/degraded) under the run's phase header, derived from run-state +
    the execmode records in a bounded ledger tail (Stage 40). Read-only; a sequential run
    shows a single lane; no run/ledger degrades to a friendly empty view (never an error).
    `run` selects a specific run id (default: the active/most-recent). `response_format`
    {concise (default), detailed}: detailed adds the rendered `block`; concise returns the
    structured lanes only (P22)."""
    from ..progress import build_run_lanes, render_lanes
    surface = _surface(path)
    ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
    rl = build_run_lanes(surface.state, ledger=ledger, run_id=run)
    out = rl.to_dict()
    out["block"] = LazyRender(lambda: render_lanes(rl, ascii_only=ascii_only))
    return apply_response_format(response_format, out)


@_tool("watch", "read")
def watch(path: str = ".", run: Optional[str] = None) -> Dict[str, Any]:
    """Write the self-contained, clickable local HTML dashboard of the active run (parallel
    lanes + 7-phase pipeline + a bounded gate/decision feed) under gitignored temp_local/ and
    return its path. The artifact has no network/server/assets. Honors settings.ux.progress:
    the default `terminal` writes NO HTML (returns a note on how to enable it). Read-only over
    run-state + the ledger — it never mutates a run or gates."""
    from ..dashboard import (dashboard_enabled, ux_progress_setting,
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
    from ..dashboard import (build_governance_view, dashboard_enabled,
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
    from ..govern import learn_from_ledger, load_rules, validate_caps
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
    from ..skills import SkillNotFound, get_skill, list_skills
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
    from ..compose import SuggestionContext, suggest as _suggest
    ctx = SuggestionContext(starting_fresh=fresh, has_spec=spec,
                            has_failing_test=failing_test, has_implementation=implementation,
                            has_diff=diff, has_bug_report=bug, has_stacktrace=stacktrace,
                            has_perf_issue=perf)
    return {"suggestions": [{"skill": s.skill, "reason": s.reason} for s in _suggest(ctx)]}


@_tool("lat_check", "read")
def lat_check(path: str = ".", response_format: str = "concise") -> Dict[str, Any]:
    """Scan @lat anchors and flag concept drift (B5). Degrades cleanly when absent (no
    anchors → no drift). Read-only. `response_format` {concise (default), detailed}: concise
    answers with `has_drift`; detailed adds the rendered `report` (P22)."""
    from ..knowledge import lat_check as _lat
    report = _lat(_surface(path).root)
    return apply_response_format(response_format,
                                 {"has_drift": report.has_drift, "report": LazyRender(report.render)})


@_tool("index_status", "read")
def index_status(path: str = ".") -> Dict[str, Any]:
    """The freshness-index STATUS (B4): how many files are tracked and what changed
    (added/removed/changed) since the last build — computed via a read-only DIFF; nothing is
    rebuilt or written (the durable rebuild stays `mokata index` on the CLI). Names the
    code-graph backend the refresh would run against. Read-only."""
    from ..knowledge import KnowledgeIndex, KnowledgeLayer
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
    from ..onboarding import build_tour
    return {"tour": build_tour()}


@_tool("ci_check", "read")
def ci_check(path: str = ".", files: str = "", symbols: str = "") -> Dict[str, Any]:
    """Stage 58 — mokata as a PR check: run the completeness gate + spec-awareness regression
    guard over a change's `files` (comma-separated; `symbols` default to the ones defined in those
    files) and return PASS/BLOCK + the review-comment body. READ-ONLY — it SURFACES blocks for a
    reviewer and PRODUCES the comment; it never posts to GitHub. DEGRADE-CLEAN: no saved spec /
    no corpus / uninitialized repo → nothing to check → PASS (never a false block)."""
    from .. import ci_check as CI
    # D1d — same parse as before, but a non-empty list with no usable entries now REFUSES instead of
    # silently degrading to the unscoped default (which returned PASS over an unchecked change).
    fl = validate_comma_list(files, "files")
    sy = validate_comma_list(symbols, "symbols") or None
    res = CI.run_ci_check(path, fl, changed_symbols=sy)
    # D5 — `degraded` rides the structured response too: an agent reading this dict must be able to
    # tell "checked, and clean" from "could not check" without parsing the prose.
    return {"blocked": res.blocked, "degraded": res.degraded, "overall": res.overall,
            "initialized": res.initialized,
            "legs": [{"name": leg.name, "status": leg.status, "summary": leg.summary,
                      "degraded": leg.degraded, "unblock": leg.unblock} for leg in res.legs],
            "comment_body": res.comment_body()}


@_tool("baseline", "read")
def baseline(path: str = ".", cmd: str = "", response_format: str = "concise") -> Dict[str, Any]:
    """Report the test suite green/red at baseline (Stage 34B) so a later failure is
    attributable to your change. Degrades clean if no test command is known (mokata never
    guesses a framework). Read-only — runs the existing suite, writes nothing. `response_format`
    {concise (default), detailed}: concise answers with `ok`; detailed adds the rendered
    `report` (P22)."""
    from ..baseline import BASELINE_MCP_TIMEOUT_SECONDS, baseline_command, baseline_status
    from ..config import ConfigError
    manifest = None
    if Surface.is_initialized(path):
        try:
            manifest = Surface.load(path).manifest
        except (ConfigError, OSError):
            # ConfigError: an absent/invalid manifest (Surface.load re-wraps ManifestError as one).
            # OSError: an unreadable constitution file. Without a manifest, `baseline_command` falls
            # back to the explicit `cmd` override or reports that no test command is known — mokata
            # never guesses a framework, so the degrade is visible in the baseline report itself.
            manifest = None
    # MCP-R.D0 · R7 — cap the subprocess far below its 600s terminal bound: on the MCP surface a
    # read tool must never block stdio for 10 minutes of silence (the `_serve` wall-clock backstops
    # it too). The CLI/`mokata baseline` path keeps the longer default.
    result = baseline_status(baseline_command(manifest, override=cmd or None), cwd=path,
                             timeout=BASELINE_MCP_TIMEOUT_SECONDS)
    return apply_response_format(response_format,
                                 {"ok": result.ok, "report": LazyRender(result.render)})


@_tool("sessions", "read")
def sessions(path: str = ".", limit: int = DEFAULT_PAGE_LIMIT, offset: int = 0) -> Dict[str, Any]:
    """List past + active runs (id, phases passed, resume point) — read-only, with a friendly
    empty state. PAGED from the start of the run listing (`limit` defaults to 50, `limit=0` opts
    out); the result carries `count` (this page), `total`, `has_more`, and `next_offset`. Continue
    one via the /mokata:resume slash; the gates still apply on resume (mokata never auto-runs the
    pipeline)."""
    from ..progress import list_sessions
    rows = list_sessions(_surface(path).state)
    return paginate([{"run_id": s.run_id, "done": s.done, "total": s.total,
                      "complete": s.complete, "active": s.active,
                      "resume_phase": s.resume_phase, "last_passed": s.last_passed}
                     for s in rows], key="sessions", limit=limit, offset=offset)


@_tool("session_windows", "read")
def session_windows(path: str = ".", limit: int = DEFAULT_PAGE_LIMIT,
                    offset: int = 0) -> Dict[str, Any]:
    """MS.S2 — list the LIVE Claude Code windows on this repo (each window is its own MCP process):
    short id, when it started, alive|stale, and its current pipeline phase. Read-only — the caller's
    own window self-registers, and stale (dead-pid) windows are pruned lazily. PAGED from the start
    of the registry listing (`limit` defaults to 50, `limit=0` opts out); the result carries `count`
    (this page), `total`, `has_more`, and `next_offset`. Distinct from `sessions` (which lists
    pipeline RUNS) and `session_list` (shareable session bundles)."""
    from .. import session_registry as SR
    from ..repo_identity import worktree_label
    surface = _surface(path)
    try:
        SR.touch(surface)                    # self-register (transient registry upkeep, ungated)
    except Exception:
        # (iv) SUPPRESS-OK: `touch` is WRITE-side upkeep (self-registering this window) inside a READ
        # tool. Failing to register affects only whether THIS window appears in a list the caller is
        # about to be shown — it cannot corrupt or hide another window's row, and the listing below
        # is unaffected. Broad because the registry spans PID probing + transient-file IO across
        # three OSes, and a listing must never die because a registry write did.
        pass                                 # degrade-clean: listing must never fail on upkeep
    rows = SR.list_sessions(surface)
    result: Dict[str, Any] = paginate(
        [{"session_id": r.session_id, "short_id": r.short_id,
          "started": r.started_at, "last_seen": r.last_seen,
          "alive": r.alive, "phase": r.phase,
          "worktree": worktree_label(r.repo_root) if r.repo_root else "main",
          "branch": r.branch,                # WT.S4 — the run↔worktree binding, same as the CLI
          "scope": r.scope} for r in rows],
        key="windows", limit=limit, offset=offset)
    # WT.S1 — a ONE-TIME human-gated worktree offer when a live sibling is on this repo (data, not
    # an action; never creates anything). Degrade-clean.
    try:
        from .. import session_worktree as SW
        offer = SW.offer_text_once(surface, rows=rows)
        if offer:
            result["offer"] = offer
    except Exception:
        # (iv) SUPPRESS-OK: a ONE-TIME, purely ADDITIVE offer (data, never an action — it creates
        # nothing). Its absence removes a suggestion, not a capability or a warning: the window
        # listing above is complete either way. Broad because the callee spans git-worktree probing
        # + a once-only marker file, and a suggestion must never break a listing.
        pass
    return result


@_tool("worktree_list", "read")
def worktree_list(path: str = ".") -> Dict[str, Any]:
    """WT-LIST (FR-WT-1) — list this repo's git worktrees, each JOINED to the mokata session that
    owns its branch, with a STALENESS VERDICT: `main` (the main checkout, never judged) ·
    `active` (a live session is bound here) · `merged` (the branch is already merged into the
    default branch) · `idle` (a session was bound but its window exited) · `no-session` (on disk,
    nothing bound) · `unknown` (the session registry could not be read — never reported as
    "no-session"). Read-only END TO END: it creates nothing, removes nothing, does NOT `git
    worktree prune`, and registers no session (unlike `session_windows`, it does not
    self-register). `empty: true` is the definitive "this repo has only its main checkout" answer;
    `message` is the same rendered text `mokata worktree list` prints. Distinct from
    `session_windows` (live WINDOWS) and `sessions` (pipeline RUNS)."""
    from ..worktree_list import build_worktree_report
    return build_worktree_report(_surface(path)).to_dict()


@_tool("session_save", "read")
def session_save(path: str = ".", brainstorm: Optional[Dict[str, Any]] = None,
                 passed: Optional[list] = None, run_id: str = "",
                 turn: bool = False, register: bool = False) -> Dict[str, Any]:
    """SS.S0 — snapshot THIS session's full in-flight state so an interrupted brainstorm/pipeline
    is recoverable. UNGATED by design: a local save is the user's own transient state (P2-exempt);
    the human gate sits at the SHARE boundary (`session_push`), NOT here — so there is no
    approve/confirm param and no WriteGate. Writes EXACTLY what the existing resume stack reads:
    `brainstorm_progress` (+ `approved_approach` when approved) + the run checkpoint, atomically
    under this window's MS.S2 session scope. `brainstorm` is a `BrainstormSession.to_dict()`;
    `passed` is the run's passed pipeline phases. Read-your-own: returns the keys + COUNTS saved,
    never the state content. A session with nothing to save returns an honest empty result. This is
    a REGISTERED READ TOOL (ungated + auto-safe, like the rest of the session family) — never a
    gated write.

    SS.S1 — this routes THROUGH the `SessionFlow` orchestrator (the ONE production seam), so the
    agent-facing save is degrade-clean: a persist failure warns once and returns a degraded marker
    rather than raising. It is the same seam the brainstorm skill instructs the agent to hit at
    each coarse milestone — making crash-safety automatic (P17), never a manual chore.

    SS.S2 — `turn=True` marks a per-turn autosave: a `brainstorm`-only snapshot routed through
    `SessionFlow.turn()` (one atomic write, no gate/checkpoint), fired after each answered Q&A turn
    so a kill −9 loses at most the single in-flight turn. The default (`turn=False`) is byte-for-byte
    the SS.S1 coarse checkpoint; `passed`/`run_id` are ignored on the turn path (a turn is
    brainstorm-only).

    RUN-REG — `register=True` REGISTERS this run (writes its `pipeline_run__<rid>` checkpoint if
    absent) as the brainstorm protocol's FIRST step, so a run driven conversationally is tracked
    from the start: `progress` reports it, `spec` can attach, and the phase gate has state to bind
    on. Idempotent and non-destructive; the result carries `registered: <run_id>`."""
    from ..session_flow import SessionFlow
    surface = _surface(path)
    flow = SessionFlow(surface)
    # RUN-REG — `register=True` is the brainstorm protocol's FIRST step: it REGISTERS this run (a
    # `pipeline_run__<rid>` checkpoint) so conversational execution cannot silently bypass tracking.
    # Rides the existing write path, idempotent, and never resets a run already past its first gate.
    registered = None
    if register:
        from ..session_save import register_run
        registered = register_run(surface, run_id=run_id or None)
    if turn:
        res = flow.turn(brainstorm or {})
    else:
        res = flow.checkpoint(
            brainstorm=brainstorm, passed=passed, run_id=run_id or None, moment="session_save")
    if res is None:
        out = {"ok": True, "empty": True, "degraded": True,
               "message": "local state persist degraded (see the warning) — your work is not "
                          "blocked; retry once the disk/permissions recover"}
        if registered:
            out["registered"] = registered
        return out
    out = res.to_dict()
    if registered:
        out["registered"] = registered
    return out


@_tool("plan_list", "read")
def plan_list(path: str = ".") -> Dict[str, Any]:
    """Stage 6p — list the saved brainstorm PLAN FILES (`.mokata/plans/<slug>.md`), written at
    approach approval. Read-only, with a friendly empty state. Keep an editable, committable copy
    with the user-initiated `mokata plan export`."""
    from .. import plans as P
    slugs = P.list_plans(_surface(path).plans_dir)
    return {"count": len(slugs), "plans": slugs}


@_tool("plan_show", "read")
def plan_show(path: str = ".", slug: str = "") -> Dict[str, Any]:
    """Stage 6p — print one saved brainstorm plan (the design write-up). With no `slug`, resolves
    to the sole saved plan; when several exist it asks which. Read-only."""
    from .. import plans as P
    plans_dir = _surface(path).plans_dir
    try:
        resolved = P.resolve_slug(plans_dir, slug or None)
    except P.PlanError as exc:
        return {"found": False, "message": str(exc)}
    content = P.read_plan(plans_dir, resolved)
    if content is None:
        return {"found": False, "message": f"no saved plan '{resolved}'"}
    return {"found": True, "slug": resolved, "content": content}


@_tool("session_list", "read")
def session_list(path: str = ".", transport: str = "", limit: int = DEFAULT_PAGE_LIMIT,
                 offset: int = 0) -> Dict[str, Any]:
    """Stage 55a/55b — list the tagged, shareable session bundles (tag, provenance, resume point,
    transport). Read-only; a friendly empty state when there are none. With no `transport` it
    spans LOCAL + the committed VAULT (+ shared Postgres when a DSN is configured); pass a single
    transport name to scope it. A missing/unavailable remote is skipped clean. PAGED from the start
    of the listing (`limit` defaults to 50, `limit=0` opts out); the result carries `count` (this
    page), `total`, `has_more`, and `next_offset`. Push/pull/rename are the human-gated
    `session_push`/`session_pull`/`session_name` write tools."""
    from .. import session_bundle as SB
    from .. import session_transport as STX
    if transport:
        try:
            transports = [STX.make_transport(transport, path)]
        except STX.SessionTransportUnavailable as exc:
            return {"count": 0, "bundles": [], "transport": transport,
                    "status": "unavailable", "message": str(exc)}
    else:
        transports = [STX.LocalTransport(path), STX.VaultTransport(path)]
        if STX.resolve_pg_dsn(root=path):
            try:
                transports.append(STX.make_transport("postgres", path))
            except STX.SessionTransportUnavailable:
                pass
    infos = SB.list_session_bundles_across(path, transports)
    return paginate([{"tag": i.tag, "author": i.author, "created": i.created,
                      "source": i.source, "run_id": i.run_id,
                      "resume_phase": i.resume_phase, "done": i.done, "total": i.total,
                      "transport": i.transport}
                     for i in infos], key="bundles", limit=limit, offset=offset)


@_tool("config_get", "read")
def config_get(path: str = ".", key: str = "") -> Dict[str, Any]:
    """Read a dotted backend-config key from the committed manifest (Stage 24A), e.g.
    `tools.sqlite.config.path`. Read-only; returns {found, value}. The write counterpart is
    the human-gated `config_set`."""
    from .. import config_cmd
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
    from ..manifest import Manifest
    from ..share import SHARE_FILENAME, export_manifest
    data = export_manifest(_surface(path))      # dest=None → returns data, writes nothing
    dest = file or os.path.join(path, MOKATA_DIR, SHARE_FILENAME)
    return {"dest": dest, "profile": data.get("profile"),
            "capabilities": len(data.get("capabilities", {}) or {}),
            "tools": len(data.get("tools", {}) or {}),
            "preview": Manifest.from_dict(data).to_json()}


@_tool("decompose", "read")
def decompose(path: str = ".", response_format: str = "concise") -> Dict[str, Any]:
    """Propose an independent-subtask split of the emitted spec's acceptance criteria, with a
    dependency plan (Stage 54f): one subtask per AC, with `depends_on` edges where subtasks
    touch the same symbol/file (the code graph verifies independence when wired; the lexical
    floor otherwise — in which case the split stays UNVERIFIED and sequential is recommended).
    READ-ONLY: it only PROPOSES the split; nothing fans out (the confirm + execution stay the
    human-gated `mokata decompose --run` / exec flow). Degrades clean with no spec/ACs.
    `response_format` {concise (default), detailed}: detailed adds the rendered `block`; concise
    returns the structured split only (P22)."""
    from ..engine import load_emitted_spec
    from ..execmode.decompose import decompose as _decompose
    from .consent import _evidence_store
    surface = _surface(path)
    # STATE-SCOPE — the RUN's spec, not the process's. The split is derived from the approved ACs,
    # so reading it through this session's own scope handed a re-entered window "no emitted spec"
    # for a run that plainly has one. Same resolved run `spec_show` and `spec_emit` answer about.
    spec = load_emitted_spec(_evidence_store(surface, path)[0])
    if spec is None or not spec.criteria:
        return {"available": False, "subtasks": [],
                "note": "no emitted spec with acceptance criteria — run /mokata:spec first; "
                        "the split is derived from the approved ACs."}
    plan = _decompose(spec, layer=KnowledgeLayer.from_surface(surface))
    out = plan.to_dict()
    out["available"] = True
    out["block"] = LazyRender(plan.render)
    return apply_response_format(response_format, out)


@_tool("spec_show", "read")
def spec_show(path: str = ".", run: str = "",
              response_format: str = "concise") -> Dict[str, Any]:
    """The RUN'S OWN persisted, gate-passed spec — title, approach, domains, every acceptance
    criterion, and the declared scope when one is set. This is how a phase FETCHES the approved
    spec (the reviewer's brief, develop's/test's precondition): read it here, verbatim, instead of
    re-deriving it from conversation memory or re-searching the repo for it. ONE keyed read of the
    run's state — NOT a corpus scan, and NOT `spec_check` (that is the regression guard over the
    SHARED spec corpus, a different question). `run` names the run explicitly; omitted, it resolves
    the same run the gates enforce and REFUSES rather than guess when two runs are undecidable.
    Read-only. Degrades clean: no tracked run / no spec / a spec that is present but unreadable each
    come back as an `available:false` answer naming the recovery — never an exception, and never a
    byte of spec content. `response_format` {concise (default), detailed}: detailed adds the
    rendered `block`."""
    from ..cli_commands.spec import (NO_SPEC_RECOVERY, NO_TRACKED_RUN_RECOVERY,
                                     _run_scoped_store)
    from ..engine.spec_gate import SPEC_MALFORMED_MESSAGE, read_emitted_spec

    surface = _surface(path)
    # Resolve the run the way `mokata spec show` does — the SAME `_run_scoped_store`, not a second
    # resolution path. A tool that answered "the spec" from a run other than the one the gates
    # enforce would hand the reviewer a foreign spec, which is the very failure G1 exists to close.
    store, run_id, err = _run_scoped_store(surface, (run or "").strip() or None)
    if err:
        return {"available": False, "reason": "ambiguous-run", "run": None,
                "criteria": [], "note": err}
    if run_id is None:
        return {"available": False, "reason": "no-run", "run": None,
                "criteria": [], "note": NO_TRACKED_RUN_RECOVERY}
    # D5's distinction, carried onto this surface: "no spec" and "a spec that cannot be read" are
    # different facts with different remedies. Collapsing them would send a caller to rewrite a spec
    # they already have while the real fault (a torn write, a hand-edit) goes uninvestigated.
    spec, malformed = read_emitted_spec(store)
    if malformed:
        return {"available": False, "reason": "malformed", "run": run_id,
                "criteria": [], "note": SPEC_MALFORMED_MESSAGE}
    if spec is None:
        return {"available": False, "reason": "no-spec", "run": run_id,
                "criteria": [], "note": NO_SPEC_RECOVERY}
    out: Dict[str, Any] = {
        "available": True, "run": run_id, "title": spec.title,
        "approach": spec.approach, "domains": list(spec.domains),
        "criteria": [{"id": c.id, "text": c.text} for c in spec.criteria],
    }
    if spec.scope is not None:
        out["scope"] = spec.scope.to_dict()
    out["block"] = LazyRender(lambda: _render_spec_show(spec))
    return apply_response_format(response_format, out)


def _render_spec_show(spec: Any) -> str:
    """The human view — the SAME lines `cmd_spec_show` prints, in the same order (title, approach,
    domains, then each AC). Built lazily, so `concise` never pays for the string it drops."""
    lines = [f"spec: {spec.title}"]
    if spec.approach:
        lines.append(f"approach: {spec.approach}")
    if spec.domains:
        lines.append(f"domains: {', '.join(spec.domains)}")
    for c in spec.criteria:
        lines.append(f"  {c.id}: {c.text}")
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# The 6r REVIEW LOOP, in-harness (REVIEW-FIX.R3) — the read AND the record halves.
#
# The loop's persisted truth is ONE record: the `review_verdict` progress event, written by
# `progress_events.record_review_verdict` and read by `latest_review_verdict` /
# `ship_review_gate` (run-keyed since R1, freshness-bounded since R2). Until this stage the ONLY
# way to reach either half was `mokata progress record-review` / `review-status` — so the harness
# had to shell out for the one piece of evidence that decides whether a change may ship. These
# two tools are that same seam, exposed in-harness: SAME resolution, SAME event on disk, SAME
# gate verdicts. Neither adds a store, and neither adds a second answer.
#
# TRUST POSTURE (grounded, deliberately UNCHANGED from the CLI): recording a verdict is
# OBSERVABILITY tier — append-only + UNGATED, the same tier as `mokata progress mark` and the
# audit ledger — so `review_record` is a registered READ-kind tool with NO `approve`/`proposal_id`
# and no WriteGate, exactly as `mokata progress record-review` has no TTY confirm. That is the
# `session_save` (SS.S0) precedent, and it is the honest one: the CLI half writes this event
# ungated today, and giving the harness half a stricter gate would make the SAME truth source
# reachable under two different postures — the very inconsistency R3 exists to kill. It stays
# safe because the record is not what a gate TRUSTS: `ship_review_gate` re-derives everything
# that matters (whose run, how old, passed or not), and a verdict it cannot key to a run
# satisfies nothing at all.
# --------------------------------------------------------------------------------------
@_tool("review_status", "read")
def review_status(path: str = ".", run: str = "",
                  response_format: str = "concise") -> Dict[str, Any]:
    """SHIP'S REVIEW GATE, read from the persisted verdict — the in-harness twin of `mokata
    progress review-status`. This is how ship VERIFIES that the closing review actually ran:
    evidence on disk, not conversation context (a `/clear`ed session remembers nothing, and a
    model's recollection that "review passed" is not a record).

    Returns the resolved `run`, whether a verdict is `present`, whether it was `readable`, whether
    it `passed`, whether it was `independent` (a fresh-context subagent vs the inline two-pass),
    `recorded_at`, and `blocks` — True when ship must STOP — with the gate's own `message` +
    `unblock` line. The four blocking answers are the CLI's, verbatim, because they come from the
    same `ship_review_gate`: no run could be resolved (mokata refuses to read another run's
    verdict), the verdict is STALE (older than `settings.review.verdict_max_age_hours`, default
    24), the verdict could not be READ (`readable: false` — an unreadable or damaged progress-event
    log, whose remedy is the LOG, not re-running review), or no verdict was recorded at all
    (`readable: true, present: false` — the remedy IS /mokata:review). An inline PASS does not
    block, but says so.

    `run` names the run explicitly; omitted, it resolves the SAME session-aware way the record
    does, so the read-key is the record-key. Read-only. Degrades clean: an unreadable gate reports
    a block rather than raising. It NEVER returns the verdict's `findings` text — findings can
    quote project content, and a status read has no need of it (`response_format` {concise
    (default), detailed}: detailed adds the rendered `block`, the same one line the CLI prints)."""
    from ..progress_events import (FAULT_UNKNOWN, _resolve_verdict_run,
                                   latest_review_verdict_event, review_log_path,
                                   review_read_error_message, review_read_error_unblock,
                                   ship_review_gate)
    surface = _surface(path)
    try:
        # Resolution through the CLI's own seam — `_resolve_verdict_run` IS what
        # `ship_review_gate(run_id=None)` calls for itself, so this is not a second resolution
        # path, it is the same one made visible: the gate returns a verdict, not the key it used,
        # and this answer must be able to NAME the run it judged. (When nothing resolves, the gate
        # asks again and gets the same None — a deterministic, local repeat on the block path
        # only, which is cheaper than teaching the gate a second return shape.)
        run_id = (run or "").strip() or _resolve_verdict_run(surface)
        gate = ship_review_gate(surface, run_id=run_id)
        # The stamp, read only when there IS a verdict — a second bounded backward scan on the
        # present path only, never on the degrade paths (P11).
        event = latest_review_verdict_event(surface, run_id=run_id) if gate.present else None
    except Exception:                        # noqa: BLE001
        # Degrade-clean, and FAIL-CLOSED in the same direction the CLI degrades. REVIEW-FIX.R4 —
        # this no longer claims "review hasn't run": the read RAISED, which is a different fact
        # with a different remedy, and it now says so (`readable: false`) in the same sentence the
        # CLI's twin handler prints, from the same builder. The two surfaces moved together, which
        # is what R3 left this blurred FOR. `{exc}` stays out of the message — R3's secret-safety
        # bar, now the CLI's too.
        err_path = review_log_path(surface)
        degraded: Dict[str, Any] = {
            "run": None, "present": False, "readable": False, "passed": False,
            "independent": False, "blocks": True, "recorded_at": None,
            "message": review_read_error_message(FAULT_UNKNOWN, err_path),
            "unblock": review_read_error_unblock(err_path),
        }
        degraded["block"] = LazyRender(
            lambda: _render_review_line(degraded["message"], degraded["unblock"]))
        return apply_response_format(response_format, degraded)
    ts = event.get("ts") if isinstance(event, dict) else None
    out: Dict[str, Any] = {
        "run": run_id, "present": gate.present, "readable": gate.readable,
        "passed": gate.passed, "independent": gate.independent, "blocks": gate.blocks,
        "recorded_at": ts if isinstance(ts, str) and ts else None,
        "message": gate.message, "unblock": gate.unblock,
    }
    out["block"] = LazyRender(lambda: _render_review_status(gate))
    return apply_response_format(response_format, out)


def _render_review_status(gate: Any) -> str:
    """The human view — the SAME single line `mokata progress review-status` prints (the gate
    message, plus its unblock remedy when it blocks). Built lazily, so concise never pays for it."""
    return _render_review_line(gate.message, gate.unblock)


def _render_review_line(message: str, unblock: str) -> str:
    """The CLI's one-line composition, from message + remedy. Split out at REVIEW-FIX.R4 so the
    DEGRADE answer — which has no `ReviewGate` to render, because building one is what failed —
    renders identically to every other answer instead of losing its `detailed` view."""
    return message + (f"  → to unblock: {unblock}" if unblock else "")


@_tool("review_record", "read")
def review_record(path: str = ".", passed: bool = False, failed: bool = False,
                  independent: bool = False, findings: str = "",
                  run: str = "") -> Dict[str, Any]:
    """RECORD the closing review's verdict — the in-harness twin of `mokata progress
    record-review`, and the ONLY thing `review_status` / `/mokata:ship` will accept as evidence
    that review ran. Call it once, at the end of /mokata:review, with the outcome you actually
    reached.

    Exactly one of `passed` / `failed` must be true (the CLI's mutually-exclusive, required
    outcome). `independent` records that the review ran as a fresh-context subagent — omit it when
    it degraded to the inline two-pass; ship does not block on inline, it surfaces the weaker
    signal, so recording it honestly costs nothing and hiding it corrupts the record. `findings`
    is an optional count/summary stored with the verdict. `run` names the run this verdict belongs
    to; omitted, it resolves the SAME session-aware way `review_status` reads, so the record-key
    and the read-key are the same run by construction (REVIEW-FIX.R1).

    UNGATED, exactly like its CLI twin: this is observability-tier, append-only evidence (the tier
    of `mokata progress mark` and the audit ledger), not a durable code/memory/config write — so
    there is no `approve`, no proposal, and nothing to confirm. Recording is not the same as being
    believed: `ship_review_gate` still decides, and it refuses a verdict it cannot key to a run or
    one older than the freshness bound.

    A verdict that lands run-less SATISFIES NOTHING — the result says so plainly (`run: null`)
    rather than letting ship discover it later. A verdict that could not be recorded AT ALL is
    LOUDER still (REVIEW-FIX.R4): `recorded: false`, `satisfies_gate: false`, and a `message`
    stating that ship will now block as if review never ran — the same sentence the CLI prints as
    it exits non-zero. Degrade-clean: a failure to record is reported, never raised."""
    from ..cli_commands.runviews import (review_record_failed_line, review_recorded_line,
                                         review_runless_line)
    from ..progress_events import _CURRENT_RUN, record_review_verdict
    if passed == failed:
        # The CLI's `add_mutually_exclusive_group(required=True)`, as a result rather than an
        # argparse exit: neither named (nothing to record) or both named (no verdict to believe).
        return {"recorded": False, "status": "error", "run": None,
                "reason": "name the outcome: exactly one of passed / failed",
                "hint": ("call `review_record` with passed=true or failed=true — mokata records "
                         "what the review CONCLUDED and will not guess it.")}
    surface = _surface(path)
    try:
        event = record_review_verdict(surface, passed=passed, independent=independent,
                                      findings=findings or None,
                                      run_id=(run or "").strip() or _CURRENT_RUN)
    except Exception as exc:                 # noqa: BLE001
        # Same posture as the CLI's record path: a recording failure is REPORTED, not raised — the
        # review skill must never break on the act of writing down its own verdict. REVIEW-FIX.R4
        # made that report LOUD on BOTH surfaces: the CLI now exits non-zero, and this answer now
        # carries the same sentence (one builder) plus the CONSEQUENCE the caller must act on —
        # `satisfies_gate: false`, the same key a run-less record uses to say "this proves nothing".
        return {"recorded": False, "status": "error", "run": None, "satisfies_gate": False,
                "reason": f"could not record the verdict ({exc})",
                "message": review_record_failed_line(exc),
                "hint": "retry, or record it from the terminal with `mokata progress "
                        "record-review --passed|--failed --run <run id>`."}
    verdict = "passed" if passed else "failed"
    kind = "independent" if independent else "inline"
    rid = event.get("run_id")
    out: Dict[str, Any] = {
        "recorded": True, "status": "recorded", "run": rid or None,
        "passed": bool(passed), "independent": bool(independent),
        "recorded_at": event.get("ts"),
        # The SAME sentence the CLI prints, from the same builder — one truth source, one wording.
        "message": (review_recorded_line(verdict, kind, rid) if rid
                    else review_runless_line(verdict, kind)),
    }
    if not rid:
        out["satisfies_gate"] = False
    return out


# --------------------------------------------------------------------------------------
# Vault READ tools — list/search/pull are safe reads over the team design vault.
# --------------------------------------------------------------------------------------
@_tool("vault_list", "read")
def vault_list(path: str = ".", limit: int = DEFAULT_PAGE_LIMIT,
               offset: int = 0) -> Dict[str, Any]:
    """List the team design vault's entries (brainstorm/spec artifacts) with name, kind,
    author, and date. Read-only. PAGED from the start of the name-sorted listing (`limit` defaults
    to 50, `limit=0` opts out); the result carries `count` (this page), `total`, `has_more`, and
    `next_offset`."""
    from ..vault import vault_list as _list
    entries = _list(path)
    return paginate([{"name": e.name, "kind": e.kind, "title": e.title,
                      "author": e.author, "version": e.version,
                      "updated_at": e.updated_at} for e in entries],
                    key="entries", limit=limit, offset=offset)


@_tool("vault_search", "read")
def vault_search(path: str = ".", query: str = "", limit: int = DEFAULT_PAGE_LIMIT,
                 offset: int = 0) -> Dict[str, Any]:
    """Search the design vault by name/title/body (lexical), ranked. Read-only. PAGED down the
    ranking, best hit first (`limit` defaults to 50, `limit=0` opts out); the result carries `count`
    (this page), `total`, `has_more`, and `next_offset`."""
    from ..vault import vault_search as _search
    hits = _search(path, query)
    return paginate([{"name": h.entry.name, "kind": h.entry.kind, "title": h.entry.title,
                      "score": round(h.score, 4), "author": h.entry.author,
                      "updated_at": h.entry.updated_at} for h in hits],
                    key="hits", limit=limit, offset=offset)


@_tool("vault_pull", "read")
def vault_pull(path: str = ".", name: str = "", dest: str = "") -> Dict[str, Any]:
    """Pull a named design artifact for review (returns the markdown; optionally writes it to
    `dest`). Read-only on the vault."""
    from ..vault import VaultError, vault_pull as _pull
    try:
        content, entry = _pull(path, name, dest=dest or None)
    except VaultError as exc:
        return {"status": "error", "message": str(exc)}
    return {"status": "ok", "name": entry.name, "kind": entry.kind,
            "version": entry.version, "author": entry.author,
            "updated_at": entry.updated_at, "dest": dest or None, "content": content}


# --------------------------------------------------------------------------------------
# Stage 70 — community stacks READ tools (list/search/show). install = the gated adopt path.
# NO hosted marketplace: publish is over git/the vault; the index is a reviewable index.json.
# --------------------------------------------------------------------------------------
@_tool("stacks_list", "read")
def stacks_list(path: str = ".", source: str = "", limit: int = DEFAULT_PAGE_LIMIT,
                offset: int = 0) -> Dict[str, Any]:
    """List the curated community-stack catalog (per-framework governed stacks). `source` is an
    optional git-org/vault catalog dir or index.json; default is the bundled curated index.
    Read-only. There is NO hosted marketplace — this reads a versioned index.json. PAGED from the
    start of the name-sorted catalog (`limit` defaults to 50, `limit=0` opts out); the result
    carries `count` (this page), `total`, `has_more`, and `next_offset`."""
    from .. import stacks as ST
    try:
        index = ST.load_index(source or None)
    except ST.StackError as exc:
        return {"status": "error", "message": str(exc)}
    entries = ST.list_stacks(index)
    return {"status": "ok", "hosted": False,
            **paginate(entries, key="stacks", limit=limit, offset=offset),
            "note": ST.HONEST_NOTE}


@_tool("stacks_search", "read")
def stacks_search(path: str = ".", query: str = "", source: str = "",
                  limit: int = DEFAULT_PAGE_LIMIT, offset: int = 0) -> Dict[str, Any]:
    """Search the community-stack catalog by name/framework/summary/tags (lexical), ranked.
    Read-only. `source` optionally points at a git-org/vault index.json instead of the bundled one.
    PAGED down the ranking, best hit first (`limit` defaults to 50, `limit=0` opts out); the result
    carries `count` (this page), `total`, `has_more`, and `next_offset`."""
    from .. import stacks as ST
    try:
        index = ST.load_index(source or None)
    except ST.StackError as exc:
        return {"status": "error", "message": str(exc)}
    hits = ST.search_stacks(query, index)
    return {"status": "ok",
            **paginate([dict(h.entry, score=round(h.score, 4)) for h in hits],
                       key="hits", limit=limit, offset=offset)}


@_tool("stacks_show", "read")
def stacks_show(path: str = ".", name: str = "", source: str = "") -> Dict[str, Any]:
    """Show one community stack's catalog entry (framework, curated-guardrail count, recommended
    skills, tags). Read-only. `source` optionally points at a git-org/vault index.json."""
    from .. import stacks as ST
    try:
        index = ST.load_index(source or None)
    except ST.StackError as exc:
        return {"status": "error", "message": str(exc)}
    entry = ST.show_stack(name, index)
    if entry is None:
        return {"status": "not_found", "message": f"no stack named '{name}' in the catalog"}
    return {"status": "ok", "hosted": False, "stack": entry}


# M-4/R5 (0.0.16) — registered LAST, not beside `recall` where it reads more naturally. The
# registry snapshot pins tool ORDER, and its own rule is that a new tool is APPENDED so every
# pre-existing entry keeps its position; slotting this next to `recall` would have shifted ~35
# entries and broken exactly what that guard protects.
@_tool("consolidate_proposals", "read")
def consolidate_proposals(path: str = ".") -> Dict[str, Any]:
    """M-4/R5 PHASE 1 — the DRAFTING REQUEST: episodic clusters that want a summary, WITH the turns
    to draft from. Read-only; it writes nothing and applies nothing.

    mokata never drafts a summary itself — it calls no model. YOU write each summary from the turns
    returned here, then submit it with the `consolidate` write tool, which is human-gated: your
    draft is reviewed by the human before it is ever stored."""
    from ..memory.consolidation import DRAFTING_INSTRUCTION, drafting_request
    surface = _surface(path)
    store = MemoryStore.from_surface(surface)
    if not store.enabled_types:
        return {"enabled": False, "drafting_requests": []}
    # LEDGER PARITY — the agent's drafting request is recorded exactly like the human's (I3). The
    # ledger is passed EXPLICITLY rather than left to `MemoryStore.from_surface`'s attached one:
    # both resolve to the same `AuditLedger.from_mokata_dir`, so this changes no behaviour today,
    # but it states the requirement at the call site instead of inheriting it — the provenance of
    # a drafting request should not quietly depend on how the store happened to be constructed.
    ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
    return _with_degrade(store, {
        "enabled": True,
        "instruction": DRAFTING_INSTRUCTION,
        "drafting_requests": drafting_request(store.propose_consolidations(ledger=ledger))})
