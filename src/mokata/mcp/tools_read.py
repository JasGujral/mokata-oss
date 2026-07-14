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
from ..knowledge import KnowledgeLayer
from ..memory import MemoryStore
from .registry import _mokata_dir, _surface, _tool

__all__ = [
    "query", "recall", "doctor", "coverage", "budget", "audit", "status", "preview",
    "progress", "lanes", "watch", "govern", "rules", "skills", "suggest", "lat_check",
    "index_status", "tour", "ci_check", "baseline", "sessions", "session_list",
    "session_windows", "session_save",
    "config_get", "export_preview", "decompose", "vault_list", "vault_search",
    "vault_pull", "stacks_list", "stacks_search", "stacks_show", "plan_list", "plan_show",
]


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
        from ..team_audit import render_team_timeline, team_audit_view
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
    return resp


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
    from ..progress import build_progress, render_progress
    surface = _surface(path)
    p = build_progress(surface.state, run_id=run)
    out = p.to_dict()
    # Stage 6d — MAX detail: the phase tracker + the 5 user-stage arc + 6c develop sub-counter
    # + what's pending this session, all derived from the same _badge_state single source.
    out["block"] = render_progress(p, surface=surface)
    return out


@_tool("lanes", "read")
def lanes(path: str = ".", run: Optional[str] = None,
          ascii_only: bool = False) -> Dict[str, Any]:
    """The PARALLEL-aware lane view: one lane per concurrent subagent
    (running/done/blocked/degraded) under the run's phase header, derived from run-state +
    the execmode records in a bounded ledger tail (Stage 40). Read-only; a sequential run
    shows a single lane; no run/ledger degrades to a friendly empty view (never an error).
    `run` selects a specific run id (default: the active/most-recent)."""
    from ..progress import build_run_lanes, render_lanes
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
def lat_check(path: str = ".") -> Dict[str, Any]:
    """Scan @lat anchors and flag concept drift (B5). Degrades cleanly when absent (no
    anchors → no drift). Read-only."""
    from ..knowledge import lat_check as _lat
    report = _lat(_surface(path).root)
    return {"has_drift": report.has_drift, "report": report.render()}


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
    fl = [f.strip() for f in files.split(",") if f.strip()]
    sy = [s.strip() for s in symbols.split(",") if s.strip()] or None
    res = CI.run_ci_check(path, fl, changed_symbols=sy)
    # D5 — `degraded` rides the structured response too: an agent reading this dict must be able to
    # tell "checked, and clean" from "could not check" without parsing the prose.
    return {"blocked": res.blocked, "degraded": res.degraded, "overall": res.overall,
            "initialized": res.initialized,
            "legs": [{"name": leg.name, "status": leg.status, "summary": leg.summary,
                      "degraded": leg.degraded, "unblock": leg.unblock} for leg in res.legs],
            "comment_body": res.comment_body()}


@_tool("baseline", "read")
def baseline(path: str = ".", cmd: str = "") -> Dict[str, Any]:
    """Report the test suite green/red at baseline (Stage 34B) so a later failure is
    attributable to your change. Degrades clean if no test command is known (mokata never
    guesses a framework). Read-only — runs the existing suite, writes nothing."""
    from ..baseline import baseline_command, baseline_status
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
    result = baseline_status(baseline_command(manifest, override=cmd or None), cwd=path)
    return {"ok": result.ok, "report": result.render()}


@_tool("sessions", "read")
def sessions(path: str = ".") -> Dict[str, Any]:
    """List past + active runs (id, phases passed, resume point) — read-only, bounded, with a
    friendly empty state. Continue one via the /mokata:resume slash; the gates still apply on
    resume (mokata never auto-runs the pipeline)."""
    from ..progress import list_sessions
    rows = list_sessions(_surface(path).state)
    return {"count": len(rows),
            "sessions": [{"run_id": s.run_id, "done": s.done, "total": s.total,
                          "complete": s.complete, "active": s.active,
                          "resume_phase": s.resume_phase, "last_passed": s.last_passed}
                         for s in rows]}


@_tool("session_windows", "read")
def session_windows(path: str = ".") -> Dict[str, Any]:
    """MS.S2 — list the LIVE Claude Code windows on this repo (each window is its own MCP process):
    short id, when it started, alive|stale, and its current pipeline phase. Read-only — the caller's
    own window self-registers, and stale (dead-pid) windows are pruned lazily. Distinct from
    `sessions` (which lists pipeline RUNS) and `session_list` (shareable session bundles)."""
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
    result: Dict[str, Any] = {
        "count": len(rows),
        "windows": [{"session_id": r.session_id, "short_id": r.short_id,
                     "started": r.started_at, "last_seen": r.last_seen,
                     "alive": r.alive, "phase": r.phase,
                     "worktree": worktree_label(r.repo_root) if r.repo_root else "main",
                     "scope": r.scope} for r in rows]}
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


@_tool("session_save", "read")
def session_save(path: str = ".", brainstorm: Optional[Dict[str, Any]] = None,
                 passed: Optional[list] = None, run_id: str = "",
                 turn: bool = False) -> Dict[str, Any]:
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
    brainstorm-only)."""
    from ..session_flow import SessionFlow
    flow = SessionFlow(_surface(path))
    if turn:
        res = flow.turn(brainstorm or {})
    else:
        res = flow.checkpoint(
            brainstorm=brainstorm, passed=passed, run_id=run_id or None, moment="session_save")
    if res is None:
        return {"ok": True, "empty": True, "degraded": True,
                "message": "local state persist degraded (see the warning) — your work is not "
                           "blocked; retry once the disk/permissions recover"}
    return res.to_dict()


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
def session_list(path: str = ".", transport: str = "") -> Dict[str, Any]:
    """Stage 55a/55b — list the tagged, shareable session bundles (tag, provenance, resume point,
    transport). Read-only; a friendly empty state when there are none. With no `transport` it
    spans LOCAL + the committed VAULT (+ shared Postgres when a DSN is configured); pass a single
    transport name to scope it. A missing/unavailable remote is skipped clean. Push/pull/rename
    are the human-gated `session_push`/`session_pull`/`session_name` write tools."""
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
def decompose(path: str = ".") -> Dict[str, Any]:
    """Propose an independent-subtask split of the emitted spec's acceptance criteria, with a
    dependency plan (Stage 54f): one subtask per AC, with `depends_on` edges where subtasks
    touch the same symbol/file (the code graph verifies independence when wired; the lexical
    floor otherwise — in which case the split stays UNVERIFIED and sequential is recommended).
    READ-ONLY: it only PROPOSES the split; nothing fans out (the confirm + execution stay the
    human-gated `mokata decompose --run` / exec flow). Degrades clean with no spec/ACs."""
    from ..engine import load_emitted_spec
    from ..execmode.decompose import decompose as _decompose
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
# Vault READ tools — list/search/pull are safe reads over the team design vault.
# --------------------------------------------------------------------------------------
@_tool("vault_list", "read")
def vault_list(path: str = ".") -> Dict[str, Any]:
    """List the team design vault's entries (brainstorm/spec artifacts) with name, kind,
    author, and date. Read-only."""
    from ..vault import vault_list as _list
    entries = _list(path)
    return {"count": len(entries),
            "entries": [{"name": e.name, "kind": e.kind, "title": e.title,
                         "author": e.author, "version": e.version,
                         "updated_at": e.updated_at} for e in entries]}


@_tool("vault_search", "read")
def vault_search(path: str = ".", query: str = "") -> Dict[str, Any]:
    """Search the design vault by name/title/body (lexical), ranked. Read-only."""
    from ..vault import vault_search as _search
    hits = _search(path, query)
    return {"count": len(hits),
            "hits": [{"name": h.entry.name, "kind": h.entry.kind, "title": h.entry.title,
                      "score": round(h.score, 4), "author": h.entry.author,
                      "updated_at": h.entry.updated_at} for h in hits]}


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
def stacks_list(path: str = ".", source: str = "") -> Dict[str, Any]:
    """List the curated community-stack catalog (per-framework governed stacks). `source` is an
    optional git-org/vault catalog dir or index.json; default is the bundled curated index.
    Read-only. There is NO hosted marketplace — this reads a versioned index.json."""
    from .. import stacks as ST
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
    from .. import stacks as ST
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
    from .. import stacks as ST
    try:
        index = ST.load_index(source or None)
    except ST.StackError as exc:
        return {"status": "error", "message": str(exc)}
    entry = ST.show_stack(name, index)
    if entry is None:
        return {"status": "not_found", "message": f"no stack named '{name}' in the catalog"}
    return {"status": "ok", "hosted": False, "stack": entry}
