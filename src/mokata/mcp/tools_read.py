"""READ tools — safe, expose their data directly, write nothing.

Every function is pure and SDK-free (fully usable/testable without the `mcp` SDK). Each is
registered into the shared `TOOLS` registry via `@_tool(name, "read")`; heavier engine imports
stay lazy inside the functions so importing this module is cheap and dependency-light.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
from typing import Annotated, Any, Dict, List, Literal, Optional

from .. import MOKATA_DIR
from ..adapters import AdapterContract, negotiate, overlapping_capabilities
from ..config import Surface
from ..deprecation import RemovedChannelError
from ..engine import preview_pipeline
from ..govern import AuditLedger, BudgetReport, diagnose
from ..knowledge import QUERY_KINDS, KnowledgeLayer
from ..memory import MemoryStore
from ..axi_out import with_count
from .pagination import DEFAULT_PAGE_LIMIT, paginate
from .registry import _mokata_dir, _surface, _tool
from .response_format import LazyRender, apply_response_format
from .tool_annotations import Field
from .validation import ValidationError, validate_comma_list, validate_enum

# MCP-R.D1d — the allowed `kind` values of the `query` tool, grounded on the router it feeds:
# `knowledge.layer.run_query` handles `semantic` itself and delegates every other kind to the
# backends, all three of which reject anything outside `QUERY_KINDS`. Composed here (rather than
# hard-coded) so a new structural kind added to the graph layer is accepted by the tool for free.
QUERY_TOOL_KINDS = tuple(QUERY_KINDS) + ("semantic",)

# 0.0.28 I2 — the shared parameter types. Every parameter a model sees is `Annotated[T, Field(...)]`
# (its description reaches `tools/list`); a closed set is a `Literal` (FastMCP emits an `enum`).
# The set a Literal names is the set the body validates against — `_QueryKind` is DERIVED from
# `QUERY_TOOL_KINDS` so a new structural kind reaches the schema for free.
_Path = Annotated[str, Field(description="Repo root the tool operates on (default: the server's "
                                         "working directory).")]
_Format = Annotated[Literal["concise", "detailed"], Field(
    description="`concise` (default) returns the structured answer; `detailed` adds the rendered "
                "text view.")]
_Run = Annotated[str, Field(description="Run id to act on (default: the active or most recent "
                                        "run).")]
_Limit = Annotated[int, Field(description="Page size; 0 returns everything (default 50).")]
_Offset = Annotated[int, Field(description="How many entries to skip before this page (default 0).")]
_QueryKind = Literal[QUERY_TOOL_KINDS]  # type: ignore[valid-type]
_MemoryTier = Literal["persistent", "decision", "episodic"]
_Stage = Literal["brainstorm", "spec", "develop", "review", "ship"]   # == progress.STAGE_BADGE_STAGES
_Landing = Literal["merged", "pr", "kept", "discarded"]               # == progress_events.LANDINGS

__all__ = [
    "query", "recall", "consolidate_proposals", "doctor", "coverage", "budget", "audit",
    "status", "preview",
    "progress", "lanes", "govern", "rules", "skills", "suggest", "lat_check",
    "index_status", "tour", "ci_check", "baseline", "sessions", "session_list",
    "session_windows", "worktree_list", "session_save",
    "config_get", "export_preview", "decompose", "spec_show",
    "review_status", "review_record",       # REVIEW-FIX.R3 — the 6r loop, in-harness
    "progress_mark", "run_finish",          # 0.0.22 C1 — stage entry + ship completion, in-harness
    "working_set",                          # 0.0.22 D2 — the run's one keyed working-memory object
    "vault_list", "vault_search",
    "vault_pull", "stacks_list", "stacks_search", "stacks_show", "plan_list", "plan_show",
]


# MCP-R.D1d — the allowed `kind` values are grounded on the router it feeds: `run_query` handles
# `semantic` itself and delegates every other kind to backends that reject anything outside
# `QUERY_KINDS`. Validated BEFORE the layer is built so a caller typo costs nothing. GR.S2 semantic.
# GRAPH-FIRST-IMPACT (d) (0.0.23 D2): `impact_degraded` carries the SAME degraded wording + remedy the
# CLI prints; absent on a structural answer. Every kind needs a target, so an empty one is refused here
# (0.0.28 I2) rather than travelling to the backend.
@_tool("query", "read")
def query(path: _Path = ".", *,
          target: Annotated[str, Field(description="Symbol to ask about (or the search text for "
                                       "`semantic`). Every kind needs one.")],
          kind: Annotated[_QueryKind, Field(description="Query kind: defs, refs, callers, callees, "
                                            "implementers, imports, blast_radius or semantic "
                                            "(default callers).")] = "callers",
          depth: Annotated[int, Field(description="How many call levels to walk for transitive "
                                      "kinds (default 2).")] = 2) -> Dict[str, Any]:
    """Look up code structure: where a symbol is defined, referenced, called, or what a change would affect.

Prefer this over grep or Read to navigate. Uses the code graph when one is wired and degrades to an
AST then grep floor; every answer names the backend that produced it, so a lexical answer is never
mistaken for a structural one.

Returns: the matches, the `backend` used, and `impact_degraded` when a blast radius was answered
by a weaker backend.
Errors: refused (invalid_input) for an unknown `kind` or an empty `target`.
    """
    from ..knowledge.layer import run_query
    # D1d — validate BEFORE the layer is built: an unknown kind used to travel all the way to the
    # backend and raise `ValueError` (reclaimed as a server `error`), after paying for the surface
    # load and backend selection. It is a caller typo, so it refuses here, up front, for free.
    validate_enum(kind, QUERY_TOOL_KINDS, "kind")
    if not (target or "").strip():
        raise ValidationError("target", "is required: name the symbol (or search text) to look up")
    layer = KnowledgeLayer.from_surface(_surface(path))
    result = run_query(layer, kind, target, depth=depth)
    out = result.to_dict()
    # GRAPH-FIRST-IMPACT (d) (0.0.23 D2) — the SAME degraded wording + remedy the CLI prints; absent
    # on a structural answer, so a graph-backed response is byte-identical.
    from ..knowledge.layer import impact_degrade
    deg = impact_degrade(layer, result)
    if deg:
        out["impact_degraded"] = deg
    return out


# Stage 59 — explainable retrieval: each hit names WHY it surfaced (frugal, top-k).
# 0.0.28 I2: the deprecated `mtype` alias parameter (and its response key) is deleted, not demoted.
@_tool("recall", "read")
def recall(path: _Path = ".",
           subject: Annotated[str, Field(description="Return only items with exactly this subject "
                                         "(default: no subject filter).")] = "",
           query: Annotated[str, Field(description="Free text; returns the most relevant items, "
                                       "each with a `why` (default: none).")] = "",
           memory_type: Annotated[Optional[_MemoryTier], Field(
               description="Only this storage tier: persistent, decision or episodic "
                           "(default: all).")] = None) -> Dict[str, Any]:
    """Read the project's active memory: by relevance to a `query`, by exact `subject`, or everything.

With `query`, returns the top matches and a short reason each one surfaced. With `subject`, exact
matches only. With neither, every active item. Surfaces nothing disabled and writes nothing.

Returns: `items` (memory_type, kind, subject, value), `count`, `backend`, and a `message` when empty.
Errors: refused (invalid_input) for a `memory_type` outside persistent, decision, episodic.
    """
    store = MemoryStore.from_surface(_surface(path))
    if not store.enabled_types:
        return {"enabled": False, "items": [], "count": 0,
                "message": "memory is disabled in this repo's profile — nothing to recall."}
    if query:
        from ..memory.intelligence import explain_recall
        hits = store.recall_relevant(query)
        return _with_degrade(store, _empty_says(with_count({
            "enabled": True, "backend": store.backend.name, "query": query,
            "items": [{"memory_type": e.item.mtype, "kind": e.item.effective_kind,
                       "subject": e.item.subject, "value": e.item.value, "why": e.why}
                      for e in explain_recall(query, hits)]}, "items"),
            "items", f"no active memory is relevant to '{query}'."))
    items = (store.recall(subject, mtype=memory_type) if subject
             else store.all_active(mtype=memory_type))
    return _with_degrade(store, _empty_says(with_count({
        "enabled": True, "backend": store.backend.name,
        "items": [{"memory_type": i.mtype, "kind": i.effective_kind,
                   "subject": i.subject, "value": i.value}
                  for i in items]}, "items"),
        "items", (f"no active memory with subject '{subject}'." if subject
                  else "no active memory yet — capture some with `/mokata:onboard` or the "
                  "`remember` tool.")))


def _empty_says(resp: Dict[str, Any], list_key: str, message: str) -> Dict[str, Any]:
    """AXI-OUT (3) — an EMPTY list says why, so "0 results" never reads like a broken read.
    Non-empty results are untouched."""
    if not resp.get(list_key) and "message" not in resp:
        resp["message"] = message
    return resp


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


# P22: `concise` answers with `ok`; `detailed` adds the rendered `report`.
@_tool("doctor", "read")
def doctor(path: _Path = ".", response_format: _Format = "concise") -> Dict[str, Any]:
    """Check the project's mokata config for missing providers, broken adapters, role conflicts and bad trust dials.

Call it when something seems miswired. Read-only. `concise` answers with `ok`; `detailed` adds the
full rendered `report`.

Returns: `ok` (true when nothing is wrong) and, if detailed, `report`.
Errors: refused (invalid_input) for an unknown `response_format`; fails if `path` is not a mokata repo.
    """
    report = diagnose(_surface(path))
    return apply_response_format(response_format,
                                 {"ok": report.ok, "report": LazyRender(report.render)})


# P22: `concise` answers with the structured `overlaps`; `detailed` adds `report`.
@_tool("coverage", "read")
def coverage(path: _Path = ".", response_format: _Format = "concise") -> Dict[str, Any]:
    """Show which capabilities the current stack covers, which are unmet, and where providers overlap.

Read-only; reads the manifest only. `concise` answers with `overlaps`; `detailed` adds the rendered
coverage `report`.

Returns: `overlaps` (capability to its providers) and, if detailed, `report`.
Errors: refused (invalid_input) for an unknown `response_format`; fails if `path` is not a mokata repo.
    """
    m = _surface(path).manifest
    adapters = [AdapterContract(name=tid, provides=[t.get("provides")],
                                kind=t.get("kind", "external"))
                for tid, t in m.tools.items() if t.get("provides")]
    report = negotiate(list(m.capabilities), adapters)
    overlaps = overlapping_capabilities(m)
    return apply_response_format(response_format, {
        "report": LazyRender(report.render),
        "overlaps": {need: providers for need, providers in overlaps.items()}})


# P22: `concise` answers with the `events` count; `detailed` adds `report`.
@_tool("budget", "read")
def budget(path: _Path = ".", response_format: _Format = "concise") -> Dict[str, Any]:
    """Show the token savings mokata has recorded in the audit ledger.

Read-only. `concise` answers with the number of recorded `events`; `detailed` adds the rendered
`report`.

Returns: `events` and, if detailed, `report` ("no savings recorded yet" when empty).
Errors: refused (invalid_input) for an unknown `response_format`.
    """
    ledger = AuditLedger.from_mokata_dir(_mokata_dir(path))
    report = BudgetReport.from_ledger(ledger)
    if not report.events:
        return apply_response_format(response_format, {
            "events": 0, "report": LazyRender(lambda: "budget: no savings recorded yet.")})
    return apply_response_format(response_format, {
        "events": len(report.events), "report": LazyRender(report.render)})


# MCP-R.D1c — the whole ledger is an unbounded payload, so `limit` defaults to 50 and `limit=0` opts
# out. Stage 71: `team=true` shows the TEAM-WIDE who-did-what over the SHARED log on the team's OWN
# storage (no telemetry, nothing phoned home); degrade-clean when sharing is off / backend absent.
@_tool("audit", "read")
def audit(path: _Path = ".", limit: _Limit = DEFAULT_PAGE_LIMIT, offset: _Offset = 0,
          team: Annotated[bool, Field(description="Show the team-wide shared log instead of this "
                                      "repo's own (default false).")] = False) -> Dict[str, Any]:
    """Show the append-only audit ledger of gate decisions and tool calls, newest first, in pages.

`offset=0` is the most recent `limit` entries; paging forward walks back in time. With `team=true`,
shows the shared team log (all actors, on the team's own storage); reports `available: false` with a
message when sharing is off.

Returns: `entries`, `count` (this page), `total`, `has_more`, `next_offset` (null at the end).
Errors: none expected; a negative `limit` or `offset` is clamped.
    """
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
def status(path: _Path = ".") -> Dict[str, Any]:
    """One-line stack summary: mokata version, profile, and what each capability resolves to right now.

Read-only. Also reports a stale hook wiring and any unflushed team writes when they exist.

Returns: `version`, `profile`, `capabilities`, and optional `pending` and `wiring`.
Errors: fails if `path` is not a mokata repo (run `mokata init`).
    """
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
def preview(path: _Path = ".",
            start: Annotated[Optional[str], Field(description="Phase to start the dry run at "
                                                  "(default: the first).")] = None,
            stop: Annotated[Optional[str], Field(description="Phase to stop the dry run after "
                                                 "(default: the last).")] = None
            ) -> Dict[str, Any]:
    """Dry-run the pipeline: the planned phases, gates and file touches, with no side effects.

Use it to see what a run would do before starting one.

Returns: `preview`, the rendered plan text.
Errors: fails if `start` or `stop` names a phase mokata does not have.
    """
    pv = preview_pipeline(start=start, stop=stop, mokata_dir=_surface(path).mokata_dir)
    return {"preview": pv.render()}


# P22 — no ASCII table you did not ask for. MCP-R.D1b — the rendered `block` is a LazyRender: dropped
# under concise, built only under detailed. Stage 6d — MAX detail: the phase tracker + the 5 user-stage
# arc + develop sub-counter + what is pending, all from one `_badge_state` source.
@_tool("progress", "read")
def progress(path: _Path = ".", run: _Run = "",
             response_format: _Format = "concise") -> Dict[str, Any]:
    """Show where the run is: done, current and pending phases with counts, from the saved run state.

Read-only. With no active run it returns a clean inactive view, not an error. `detailed` adds the
rendered `block`.

Returns: the structured tracker and, if detailed, `block`.
Errors: refused (invalid_input) for an unknown `response_format`.
    """
    from ..progress import build_progress, render_progress
    surface = _surface(path)
    p = build_progress(surface.state, run_id=run or None)
    out = p.to_dict()
    out["block"] = LazyRender(lambda: render_progress(p, surface=surface))
    return apply_response_format(response_format, out)


# Stage 40 — one lane per concurrent subagent, from run-state + the execmode ledger tail.
@_tool("lanes", "read")
def lanes(path: _Path = ".", run: _Run = "",
          ascii_only: Annotated[bool, Field(description="Render the `block` with ASCII only "
                                            "(default false).")] = False,
          response_format: _Format = "concise") -> Dict[str, Any]:
    """Show the parallel lanes: one per concurrent subagent (running, done, blocked, degraded) under the run's phase.

Read-only. A sequential run shows one lane; no run degrades to an empty view. `detailed` adds the
rendered `block`.

Returns: the structured lanes and, if detailed, `block`.
Errors: refused (invalid_input) for an unknown `response_format`.
    """
    from ..progress import build_run_lanes, render_lanes
    surface = _surface(path)
    ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
    rl = build_run_lanes(surface.state, ledger=ledger, run_id=run or None)
    out = rl.to_dict()
    out["block"] = LazyRender(lambda: render_lanes(rl, ascii_only=ascii_only))
    return apply_response_format(response_format, out)


# Stage 48 governed-state view; Stage 59 memory-health nudge (proposal-only); Stage 60 since-last-session diff.
@_tool("govern", "read")
def govern(path: _Path = ".",
           live: Annotated[bool, Field(description="Make the written HTML dashboard refresh itself "
                                       "(default false).")] = False) -> Dict[str, Any]:
    """Show the governed state: always-on rules, memory by kind, read/write ratio, pending proposals and what changed since last session.

Writes a self-contained HTML dashboard under the git-ignored `temp_local/` and returns its path
with a summary. Changes no project state; edits go through the gated `mokata memory edit`.

Returns: `path` (the HTML), `version`, `profile`, `rules`, `reads`, `writes`, `ratio`, `proposals`,
`health`, `since_last_session`.
Errors: fails if `path` is not a mokata repo.
    """
    from ..governance_view import build_governance_view, write_governance_dashboard
    surface = _surface(path)
    view = build_governance_view(surface)
    refresh = 2 if live else None
    written = write_governance_dashboard(surface, refresh_secs=refresh)
    h = view.health
    health = {"stale": h.stale, "contradictory": h.contradictory, "unused": h.unused,
              "healthy": h.healthy, "nudge": h.nudge()} if h is not None else {}
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
# Stage 54e command-parity read; G5 rule proposals are distilled from recurring ledger corrections, never auto-added.
@_tool("rules", "read")
def rules(path: _Path = ".") -> Dict[str, Any]:
    """List the always-on rules and guardrails by tier, with each tier's line count and budget cap.

Also lists rule proposals distilled from recurring corrections in the ledger; they are proposals
only and are never added automatically. Read-only.

Returns: `tiers` (lines, cap, within_cap), `cap_errors`, `proposals`, `proposal_count`.
Errors: fails if `path` is not a mokata repo.
    """
    from ..govern import learn_from_ledger, load_rules, validate_caps
    surface = _surface(path)
    by_tier = load_rules(surface)
    proposals = learn_from_ledger(AuditLedger.from_mokata_dir(surface.mokata_dir))
    return {"tiers": {tier: {"lines": rs.line_count, "cap": rs.cap,
                             "within_cap": rs.within_cap}
                      for tier, rs in by_tier.items()},
            "cap_errors": validate_caps(by_tier),
            "proposals": [p.proposed_rule for p in proposals],
            "proposal_count": len(proposals)}


# L4 progressive disclosure; Stage 70 discoverable keyword filter. An unknown `name` is a caller fault (refused).
@_tool("skills", "read")
def skills(path: _Path = ".",
           name: Annotated[str, Field(description="Return this one skill's gate, phase and full "
                                      "prompt (default: list skills).")] = "",
           query: Annotated[str, Field(description="Keep only skills whose name or summary contains "
                                       "this text (default: no filter).")] = "") -> Dict[str, Any]:
    """Browse the skill catalog: all skills, a keyword-filtered list, or one skill in full.

With no arguments, the cheap (name, summary) list; with `query`, a filtered list; with `name`, that
skill's gate, phase and prompt. Read-only; never runs a skill.

Returns: `skills` with `count`, or one skill's `name`, `summary`, `phase`, `gate`, `prompt`.
Errors: refused (invalid_input) when `name` is not a known skill.
    """
    from ..skills import SkillNotFound, get_skill, list_skills
    if name:
        try:
            s = get_skill(name)
        except SkillNotFound:
            raise ValidationError("name", "is not a known skill",
                                  allowed=[n for n, _ in list_skills()]) from None
        return {"name": s.name, "summary": s.summary, "phase": s.phase,
                "gate": {"id": s.gate.id, "kind": s.gate.kind,
                         "description": s.gate.description},
                "prompt": s.prompt}
    catalog = list_skills()
    if query:
        q = query.lower()
        catalog = [(n, sm) for n, sm in catalog if q in n.lower() or q in sm.lower()]
    return _empty_says(with_count({"skills": [{"name": n, "summary": sm} for n, sm in catalog]},
                                  "skills"),
                       "skills", f"no skill matches '{query}' — call `skills` with no query "
                                 f"for the full catalog.")


# L6 suggestion; G-3 (deterministic keyword match, each hit naming the words that produced it).
# 0.0.27 P14 — with NONE of the state flags or `intent`, the next step is DERIVED from the run's
# recorded state via `hints.derive`: `{status: hints|none|unknown, hints[...], reason, basis}`; each
# hint cites the fact it came from; `unknown` names the store that could not be read.
@_tool("suggest", "read")
def suggest(path: _Path = ".",
            fresh: Annotated[bool, Field(description="Starting fresh, nothing built yet.")] = False,
            spec: Annotated[bool, Field(description="A spec already exists.")] = False,
            failing_test: Annotated[bool, Field(description="A failing test is on record.")] = False,
            implementation: Annotated[bool, Field(description="An implementation exists.")] = False,
            diff: Annotated[bool, Field(description="There is a diff to review.")] = False,
            bug: Annotated[bool, Field(description="A bug report is in hand.")] = False,
            stacktrace: Annotated[bool, Field(description="A stack trace is in hand.")] = False,
            perf: Annotated[bool, Field(description="A performance concern is in hand.")] = False,
            intent: Annotated[str, Field(description="Free text such as \"flaky bug\" matched by "
                                         "keyword (default: none).")] = "",
            run: _Run = "",
            session: Annotated[str, Field(description="Session id for resolving the run "
                                          "(default: resolved).")] = "") -> Dict[str, Any]:
    """Suggest the next /mokata command for the current context. It only suggests and never runs anything.

Pass the state flags that describe where you are, and/or `intent` free text (a keyword match that
names the words behind each hit; an unmatched intent returns nothing rather than a guess). With none
of these, the next step is derived from the run's recorded state, each hint citing its source.

Returns: `suggestions` (skill, reason) or, with no input, `status` (hints, none or unknown) and `hints`.
Errors: none for a caller fault; `unknown` names a store that could not be read.
    """
    if not (intent and intent.strip()) and not any(
            (fresh, spec, failing_test, implementation, diff, bug, stacktrace, perf)):
        from ..hints import derive
        return derive(os.path.abspath(path), run=run or "", session=session or "")
    from ..compose import SuggestionContext, recommend, suggest as _suggest
    ctx = SuggestionContext(starting_fresh=fresh, has_spec=spec,
                            has_failing_test=failing_test, has_implementation=implementation,
                            has_diff=diff, has_bug_report=bug, has_stacktrace=stacktrace,
                            has_perf_issue=perf)
    if intent and intent.strip():
        return {"intent": intent, "basis": "as you described it",
                "suggestions": [{"skill": r.skill, "reason": r.reason, "score": r.score,
                                 "matched": r.matched} for r in recommend(intent, ctx)]}
    return _empty_says(with_count(
        {"basis": "as you described it",
         "suggestions": [{"skill": s.skill, "reason": s.reason} for s in _suggest(ctx)]},
        "suggestions"), "suggestions", "no command fits the state you described — pass the "
                                       "booleans that describe it (fresh/spec/diff/bug/…).")


# B5 concept-drift scan over @lat anchors; P22 response_format; P41 scans the session's worktree files.
@_tool("lat_check", "read")
def lat_check(path: _Path = ".", response_format: _Format = "concise") -> Dict[str, Any]:
    """Scan `@lat` anchors in the code and flag concept drift.

Read-only. With no anchors there is no drift. `concise` answers with `has_drift`; `detailed` adds
the rendered `report`.

Returns: `has_drift` and, if detailed, `report`.
Errors: refused (invalid_input) for an unknown `response_format`.
    """
    from ..knowledge import lat_check as _lat
    from ..tree_scope import code_root
    report = _lat(code_root(_surface(path).root))     # P41: the session's worktree's files
    return apply_response_format(response_format,
                                 {"has_drift": report.has_drift, "report": LazyRender(report.render)})


# B4 freshness index: a read-only diff; the durable rebuild stays `mokata index` on the CLI.
@_tool("index_status", "read")
def index_status(path: _Path = ".") -> Dict[str, Any]:
    """Report the code index's freshness: files tracked and what changed since the last build.

Read-only: nothing is rebuilt or written (`mokata index` rebuilds). Names the graph backend, the
languages indexed, and how many source files were skipped as unsupported (Python only).

Returns: `built`, `tracked`, `added`, `removed`, `changed`, `coverage`, `backend`; before a first
build, `built: false` with a `note`.
Errors: fails if `path` is not a mokata repo.
    """
    from ..knowledge import KnowledgeIndex, KnowledgeLayer
    from ..knowledge.index import coverage_line
    surface = _surface(path)
    layer = KnowledgeLayer.from_surface(surface)
    backend = {"uses_graph": layer.uses_graph, "backend": layer.backend_name}
    data = surface.state.read("knowledge_index")
    if data is None:
        return {"built": False, "tracked": 0,
                "note": "no index yet — run `mokata index` to build it (a durable write). "
                        "The index covers Python files only; other languages are not indexed.",
                **backend}
    idx = KnowledgeIndex.from_dict(data)
    d = idx.diff(surface.root)
    cov = idx.coverage()
    return {"built": True, "tracked": len(idx.entries), "added": len(d["added"]),
            "removed": len(d["removed"]), "changed": len(d["changed"]),
            "languages_indexed": sorted(cov["indexed"]),
            "unsupported_files": cov["unsupported_total"],
            "unsupported_by_language": cov["unsupported"],
            "unsupported_counted_languages": cov["counted"],
            "coverage": coverage_line(cov), **backend}


# Stage 56 self-contained demo.
@_tool("tour", "read")
def tour(path: _Path = ".") -> Dict[str, Any]:
    """Give a short, self-contained demo of mokata: a graph query, a memory recall and a secret gate-catch.

Writes nothing and is safe to call any time. Show the returned text to the user.

Returns: `tour`, the demo text.
Errors: none expected.
    """
    from ..onboarding import build_tour
    return {"tour": build_tour()}


# Stage 58 — mokata as a PR check. D1d: a non-empty list with no usable entries now REFUSES instead of
# degrading to the unscoped default (which returned PASS over an unchecked change). D5: `degraded`
# rides the structure so "checked, clean" differs from "could not check". SPEC-TRISTATE: `checked` and
# `absent` ride too; `exit_code` is the CLI's. P41: runs against the session's tree.
@_tool("ci_check", "read")
def ci_check(path: _Path = ".",
             files: Annotated[str, Field(description="Comma-separated changed files to check "
                                         "(default: no file scope).")] = "",
             symbols: Annotated[str, Field(description="Comma-separated changed symbols (default: "
                                           "those defined in `files`).")] = "") -> Dict[str, Any]:
    """Run mokata as a PR check: the completeness gate and spec regression guard over a change's files.

Read-only: it reports blocks and produces the review-comment text but never posts to GitHub. A leg
with nothing to check (no saved spec, no corpus, repo not initialized) reports `absent`, never a pass.

Returns: `blocked`, `overall`, `degraded`, `checked`, `absent`, `exit_code`, `legs`, `comment_body`.
Errors: refused (invalid_input) when `files` or `symbols` has no usable entries or a control character.
    """
    from .. import ci_check as CI
    # D1d — same parse as before, but a non-empty list with no usable entries now REFUSES instead of
    # silently degrading to the unscoped default (which returned PASS over an unchecked change).
    fl = validate_comma_list(files, "files")
    sy = validate_comma_list(symbols, "symbols") or None
    from ..tree_scope import code_root
    res = CI.run_ci_check(code_root(path), fl, changed_symbols=sy)   # P41: the session's tree
    # D5 — `degraded` rides the structured response too: an agent reading this dict must be able to
    # tell "checked, and clean" from "could not check" without parsing the prose.
    # SPEC-TRISTATE — `checked` and `absent` ride the structure too: a leg with status "absent"
    # had nothing to check, and `blocked: false` alone must not read as "clean". `exit_code` is the
    # CLI's (1 only on a block), so an agent can see a 0 that is not a pass.
    return {"blocked": res.blocked, "degraded": res.degraded, "overall": res.overall,
            "checked": res.checked, "absent": res.absent_legs, "exit_code": res.exit_code,
            "initialized": res.initialized,
            "legs": [{"name": leg.name, "status": leg.status, "summary": leg.summary,
                      "degraded": leg.degraded, "unblock": leg.unblock} for leg in res.legs],
            "comment_body": res.comment_body()}


# Stage 34B. MCP-R.D0 R7 — the subprocess is capped far below its 600s terminal bound so a read tool
# never blocks stdio for ten minutes; the CLI path keeps the longer default. P22 response_format.
@_tool("baseline", "read")
def baseline(path: _Path = ".",
             cmd: Annotated[str, Field(description="Test command to run instead of the configured "
                                       "one (default: the project's own).")] = "",
             response_format: _Format = "concise") -> Dict[str, Any]:
    """Run the test suite once and report green or red, so a later failure is attributable to your change.

Runs the existing suite and writes nothing. If no test command is known it says so; mokata never
guesses a framework. `concise` answers with `ok`; `detailed` adds the rendered `report`.

Returns: `ok` and, if detailed, `report`.
Errors: refused (invalid_input) for an unknown `response_format`.
    """
    from ..baseline import baseline_command, baseline_status, mcp_test_timeout
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
    from ..project_env import settings_python
    result = baseline_status(baseline_command(manifest, override=cmd or None), cwd=path,
                             timeout=mcp_test_timeout(manifest),
                             settings_python=settings_python(manifest))
    return apply_response_format(response_format,
                                 {"ok": result.ok, "report": LazyRender(result.render)})


@_tool("sessions", "read")
def sessions(path: _Path = ".", limit: _Limit = DEFAULT_PAGE_LIMIT,
             offset: _Offset = 0) -> Dict[str, Any]:
    """List past and active pipeline runs: id, phases passed and the resume point.

Read-only, paged from the start. Continue a run with the /mokata:resume slash; the gates still apply
on resume.

Returns: `sessions`, `count` (this page), `total`, `has_more`, `next_offset`; `message` when empty.
Errors: none expected; a negative `limit` or `offset` is clamped.
    """
    from ..progress import list_sessions
    rows = list_sessions(_surface(path).state)
    return _empty_says(paginate([{"run_id": s.run_id, "done": s.done, "total": s.total,
                                  "complete": s.complete, "active": s.active,
                                  "resume_phase": s.resume_phase, "last_passed": s.last_passed}
                                 for s in rows], key="sessions", limit=limit, offset=offset),
                       "sessions", "no runs in this repo yet — `/mokata:brainstorm` starts one.")


# MS.S2 — each Claude Code window is its own MCP process; the caller's own window self-registers
# (transient registry upkeep, ungated) and stale (dead-pid) windows are pruned lazily. WT.S1 — a
# one-time human-gated worktree offer when a live sibling is on this repo (data, never an action).
@_tool("session_windows", "read")
def session_windows(path: _Path = ".", limit: _Limit = DEFAULT_PAGE_LIMIT,
                    offset: _Offset = 0) -> Dict[str, Any]:
    """List the live Claude Code windows on this repo: short id, start time, alive or stale, and pipeline phase.

Read-only apart from registering the calling window. Different from `sessions` (pipeline runs) and
`session_list` (shareable bundles).

Returns: `windows`, `count`, `total`, `has_more`, `next_offset`, and an optional `offer`.
Errors: none expected; a negative `limit` or `offset` is clamped.
    """
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


# WT-LIST (FR-WT-1). Read-only END TO END: creates/removes nothing, does NOT `git worktree prune`, and
# registers no session (unlike `session_windows`). `unknown` is never reported as `no-session`.
@_tool("worktree_list", "read")
def worktree_list(path: _Path = ".") -> Dict[str, Any]:
    """List this repo's git worktrees, each joined to the mokata session that owns its branch, with a staleness verdict.

Verdicts: main, active, merged, idle, no-session, unknown (session registry unreadable). Read-only;
creates and removes nothing.

Returns: the worktree rows, `empty: true` when only the main checkout exists, and a rendered `message`.
Errors: none expected.
    """
    from ..worktree_list import build_worktree_report
    return build_worktree_report(_surface(path)).to_dict()


# SS.S0 — UNGATED by design (a local save is the user's own transient state, P2-exempt; the human gate
# sits at the SHARE boundary, `session_push`). Writes what the resume stack reads: `brainstorm_progress`
# (+ `approved_approach`) + the run checkpoint, atomically under this window's MS.S2 scope.
# SS.S1 — routes through `SessionFlow` (the one production seam); degrade-clean, a persist failure warns
# once and returns a degraded marker. SS.S2 — `turn=True` is a per-turn brainstorm-only autosave via
# `SessionFlow.turn()`; `passed`/`run_id` are ignored on that path. RUN-REG — `register=True` registers
# the run (writes `pipeline_run__<rid>` if absent) as the brainstorm protocol's first step; idempotent.
# 0.0.24 (OSS #82/#83) — the degraded result names the exception TYPE and errno, never its text.
@_tool("session_save", "read")
def session_save(path: _Path = ".",
                 brainstorm: Annotated[Optional[Dict[str, Any]], Field(
                     description="The brainstorm session as a dict (`BrainstormSession.to_dict()`); "
                                 "default: none.")] = None,
                 passed: Annotated[Optional[List[str]], Field(
                     description="Pipeline phases the run has passed (default: none).")] = None,
                 run_id: Annotated[str, Field(description="Run id to save under (default: the "
                                              "current run).")] = "",
                 turn: Annotated[bool, Field(description="Autosave one brainstorm turn only: a "
                                             "single atomic write (default false).")] = False,
                 register: Annotated[bool, Field(description="Register this run first if it is not "
                                                 "yet tracked (default false).")] = False
                 ) -> Dict[str, Any]:
    """Snapshot this session's in-flight brainstorm and run state so an interrupted session can be resumed.

Saves locally with no approval: it is your own transient state; sharing is the gated `session_push`.
Call it at each milestone, and with `turn=true` after each answered question. `register=true` starts
tracking the run. A persist failure returns a degraded marker instead of raising.

Returns: the keys and counts saved (never the content), `registered` when set, or `degraded: true`.
Errors: refused (invalid_input) for arguments of the wrong type.
    """
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
        # 0.0.24 (OSS #82/#83) — an MCP client never sees the stderr warning, so the result names
        # the failure itself: the exception TYPE and errno/winerror, never its text (secret-safe,
        # as `note_persist_failure`).
        err = getattr(flow, "last_error", None)
        if err is not None:
            out["error"] = type(err).__name__
            for attr in ("winerror", "errno"):
                code = getattr(err, attr, None)
                if isinstance(code, int):
                    out["error"] += f" [{attr} {code}]"
                    break
            if isinstance(err, PermissionError):
                out["message"] += (". On Windows a PermissionError here was another mokata process "
                                   "holding the state file; current mokata retries that — upgrade "
                                   "(`uv tool install --upgrade mokata`) if this persists")
        if registered:
            out["registered"] = registered
        return out
    out = res.to_dict()
    if registered:
        out["registered"] = registered
    return out


# Stage 6p — plan files written at approach approval; `mokata plan export` makes an editable copy.
@_tool("plan_list", "read")
def plan_list(path: _Path = ".") -> Dict[str, Any]:
    """List the saved brainstorm plan files in `.mokata/plans/`, written when an approach is approved.

Read-only. Use `mokata plan export` to keep an editable, committable copy.

Returns: `plans` (slugs), `count`, and a `message` when empty.
Errors: none expected.
    """
    from .. import plans as P
    slugs = P.list_plans(_surface(path).plans_dir)
    return _empty_says({"count": len(slugs), "plans": slugs}, "plans",
                       "no saved plans — a plan file is written when a brainstorm approach is "
                       "approved.")


# Stage 6p. An unresolvable slug is reported as `found: false`, not refused: the message names the choices.
@_tool("plan_show", "read")
def plan_show(path: _Path = ".",
              slug: Annotated[str, Field(description="Plan slug from `plan_list` (default: the "
                                         "only saved plan).")] = "") -> Dict[str, Any]:
    """Print one saved brainstorm plan, the design write-up.

With no `slug` it picks the sole saved plan; when several exist it lists them so you can choose.
Read-only.

Returns: `found`, `slug`, `content`; or `found: false` with a `message`.
Errors: none; an unknown or ambiguous plan comes back as `found: false`.
    """
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


# Stage 55a/55b. `transport` is deliberately a free string, not a Literal: besides local/postgres the
# body answers a REMOVED kind with `status: "removed"` and its record (never "unavailable", which would
# invite a retry that cannot work, §7g), and an unknown one with `status: "unavailable"`. Bundles
# stranded in a removed channel's store are reported in `removed_channel`, whether or not the page is
# empty.
@_tool("session_list", "read")
def session_list(path: _Path = ".",
                 transport: Annotated[str, Field(description="Scope to one transport: `local` or "
                                                 "`postgres` (default: local, plus shared when "
                                                 "configured).")] = "",
                 limit: _Limit = DEFAULT_PAGE_LIMIT, offset: _Offset = 0) -> Dict[str, Any]:
    """List the tagged, shareable session bundles: tag, author, resume point and transport.

Read-only, paged. With no `transport` it spans local plus shared Postgres when configured. An
unavailable remote is skipped cleanly; a removed one answers `status: "removed"`. Pushing, pulling
and renaming are the gated `session_push`, `session_pull`, `session_name`.

Returns: `bundles`, `count`, `total`, `has_more`, `next_offset`, optional `removed_channel`.
Errors: none expected; a negative `limit` or `offset` is clamped.
    """
    from .. import session_bundle as SB
    from .. import session_transport as STX
    if transport:
        try:
            transports = [STX.make_transport(transport, path)]
        except STX.SessionTransportUnavailable as exc:
            return {"count": 0, "bundles": [], "transport": transport,
                    "status": "unavailable", "message": str(exc)}
        except RemovedChannelError as exc:
            # A REMOVED kind is not an unavailable one, and the two must not share a status:
            # "unavailable" invites a retry with a DSN exported, which will never work for a
            # transport that no longer exists (§7g).
            return {"count": 0, "bundles": [], "transport": transport,
                    "status": "removed", "message": str(exc)}
    else:
        # The removed vault leg is gone; a repo that still holds bundles there is TOLD so rather
        # than silently listed short (see `cli_commands/collab.py`'s note).
        transports = [STX.LocalTransport(path)]
        if STX.resolve_pg_dsn(root=path):
            try:
                transports.append(STX.make_transport("postgres", path))
            except STX.SessionTransportUnavailable:
                pass
    infos = SB.list_session_bundles_across(path, transports)
    result = paginate([{"tag": i.tag, "author": i.author, "created": i.created,
                        "source": i.source, "run_id": i.run_id,
                        "resume_phase": i.resume_phase, "done": i.done, "total": i.total,
                        "transport": i.transport}
                       for i in infos], key="bundles", limit=limit, offset=offset)
    # Same fact the CLI prints, in the shape this surface speaks. A model that reads a short list
    # and reports "you have no sessions" is the empty-store defect with a narrator — and it is
    # attached whether or not the page is empty, because a repo with one local bundle and nine
    # stranded ones is exactly the case an is-it-empty guard would miss.
    stranded = STX.removed_bundle_tags("vault", path)
    if stranded:
        from .. import deprecation
        result["removed_channel"] = {
            "channel": "vault", "stranded": len(stranded),
            "message": deprecation.removal_answer("vault", path, True)}
    return _empty_says(result, "bundles", "no session bundles on this transport — "
                                          "`mokata session save` writes one.")


# Stage 24A. The write counterpart is the gated `config_set`. An unreadable manifest propagates to the front door as a mokata-side error.
@_tool("config_get", "read")
def config_get(path: _Path = ".", *,
               key: Annotated[str, Field(description="Dotted key in the manifest, e.g. "
                                         "`tools.sqlite.config.path`.")]) -> Dict[str, Any]:
    """Read one dotted key from the committed manifest, e.g. `tools.sqlite.config.path`.

Read-only. The gated counterpart that changes a value is `config_set`.

Returns: `found`, `key`, `value` (null when not found).
Errors: refused (invalid_input) for an empty `key`; fails if the repo is not initialized (`mokata init`).
    """
    from .. import config_cmd
    if not [p for p in (key or "").split(".") if p]:
        raise ValidationError("key", "is required: a non-empty dotted path")
    _surface(path)      # an uninitialized repo is refused with `mokata init`, never an error
    found, val = config_cmd.config_get(path, key)
    return {"found": found, "key": key, "value": val if found else None}


# J3 shareable stack export; `export_stack` is the gated write.
@_tool("export_preview", "read")
def export_preview(path: _Path = ".",
                   file: Annotated[str, Field(description="Destination file to preview "
                                              "(default: the standard share file in `.mokata`)."
                                              )] = "") -> Dict[str, Any]:
    """Preview the shareable stack export: the profile, capabilities and tools that would be written, and where.

Writes nothing. Use the gated `export_stack` to write it.

Returns: `dest`, `profile`, `capabilities` (count), `tools` (count), `preview` (the JSON).
Errors: fails if `path` is not a mokata repo.
    """
    from ..manifest import Manifest
    from ..share import SHARE_FILENAME, export_manifest
    data = export_manifest(_surface(path))      # dest=None → returns data, writes nothing
    dest = file or os.path.join(path, MOKATA_DIR, SHARE_FILENAME)
    return {"dest": dest, "profile": data.get("profile"),
            "capabilities": len(data.get("capabilities", {}) or {}),
            "tools": len(data.get("tools", {}) or {}),
            "preview": Manifest.from_dict(data).to_json()}


# Stage 54f. STATE-SCOPE — the RUN's spec, not the process's: reading it through this session's own
# scope handed a re-entered window "no emitted spec" for a run that plainly has one. Only PROPOSES the
# split; fan-out stays the human-gated `mokata decompose --run`. P22 response_format.
@_tool("decompose", "read")
def decompose(path: _Path = ".", response_format: _Format = "concise") -> Dict[str, Any]:
    """Propose an independent-subtask split of the emitted spec: one subtask per acceptance criterion, with dependency edges.

Read-only; nothing fans out (`mokata decompose --run` does that, with approval). Without a code
graph the split stays unverified and sequential is recommended. `detailed` adds the rendered `block`.

Returns: `subtasks`, `available`, and if detailed `block`; `available: false` with a `note` if no spec.
Errors: refused (invalid_input) for an unknown `response_format`.
    """
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


# HANDOFF.G1 — resolves the run through the CLI's own `_run_scoped_store`, NOT a second path (a foreign
# spec handed to the reviewer is the failure G1 closes). SESSION-BIND-BY-ANCESTOR (0.0.23 F1) names the
# process's session run when `run` is omitted. D5: "no spec" and "unreadable spec" are different facts
# with different remedies. SPEC-HISTORY: every answer names `version`; `version=N` reads a superseded
# spec from its archive; `list_versions=true` returns the history instead. 0.0.25 Lane D adds
# `domain_skills`. Not `spec_check` (that is the SHARED-corpus regression guard).
@_tool("spec_show", "read")
def spec_show(path: _Path = ".", run: _Run = "",
              version: Annotated[int, Field(description="Read this superseded spec version "
                                            "(default 0: the current one).")] = 0,
              list_versions: Annotated[bool, Field(description="List the run's spec versions "
                                                   "instead of a spec (default false).")] = False,
              response_format: _Format = "concise") -> Dict[str, Any]:
    """Fetch the run's approved spec: title, approach, domains, every acceptance criterion and scope.

Read it here, verbatim, instead of re-deriving it from conversation memory. One keyed read of the
run's state, not a corpus scan (that is `spec_check`). With `version=N` reads a superseded spec;
`list_versions=true` lists the history. No run, no spec or an unreadable spec each answer
`available: false` with the recovery.

Returns: `available`, `run`, `version`, `title`, `approach`, `criteria`, `domain_skills`, `scope`.
Errors: refused (invalid_input) for an unknown `response_format`.
    """
    from ..cli_commands.spec import (NO_SPEC_RECOVERY, NO_TRACKED_RUN_RECOVERY,
                                     _run_scoped_store)
    from ..engine.spec_gate import SPEC_MALFORMED_MESSAGE, read_emitted_spec

    surface = _surface(path)
    # Resolve the run the way `mokata spec show` does — the SAME `_run_scoped_store`, not a second
    # resolution path. A tool that answered "the spec" from a run other than the one the gates
    # enforce would hand the reviewer a foreign spec, which is the very failure G1 exists to close.
    # SESSION-BIND-BY-ANCESTOR (0.0.23 F1) — with no `run`, the run bound to the Claude session
    # this MCP serves (process ancestry) is named EXPLICITLY into the same seam; no ancestry match
    # leaves the argument None and the resolution byte-identical.
    from ..run_resolver import process_session_run
    named = (run or "").strip() or process_session_run(surface.root)
    store, run_id, err = _run_scoped_store(surface, named or None)
    if err:
        return {"available": False, "reason": "ambiguous-run", "run": None,
                "criteria": [], "note": err}
    if run_id is None:
        return {"available": False, "reason": "no-run", "run": None,
                "criteria": [], "note": NO_TRACKED_RUN_RECOVERY}
    # D5's distinction, carried onto this surface: "no spec" and "a spec that cannot be read" are
    # different facts with different remedies. Collapsing them would send a caller to rewrite a spec
    # they already have while the real fault (a torn write, a hand-edit) goes uninvestigated.
    from ..engine import spec_history as SH
    current = SH.current_version(store)
    if list_versions:
        rows = SH.spec_versions(store, run_id)
        return {"available": bool(rows), "run": run_id, "current_version": current,
                "versions": rows, "criteria": [],
                **({} if rows else {"reason": "no-spec", "note": NO_SPEC_RECOVERY})}
    if version and version != current:
        row = SH.spec_at_version(store, run_id, int(version))
        spec, malformed = row["spec"], row["malformed"]
        if spec is None and not malformed:
            return {"available": False, "reason": "no-such-version", "run": run_id,
                    "version": int(version), "current_version": current, "criteria": [],
                    "note": (f"this run has no spec v{version} (current is v{current}); call "
                             f"with list_versions=true to see what exists")}
        shown = int(version)
    else:
        spec, malformed = read_emitted_spec(store)
        shown = current
    if malformed:
        return {"available": False, "reason": "malformed", "run": run_id,
                "criteria": [], "note": SPEC_MALFORMED_MESSAGE}
    if spec is None:
        return {"available": False, "reason": "no-spec", "run": run_id,
                "criteria": [], "note": NO_SPEC_RECOVERY}
    out: Dict[str, Any] = {
        "available": True, "run": run_id, "version": shown or 1, "current_version": current,
        "superseded": bool(shown and shown != current), "title": spec.title,
        "approach": spec.approach, "domains": list(spec.domains),
        "criteria": [c.to_dict() for c in spec.criteria],     # SPEC-ANCHOR: anchors when set
    }
    # 0.0.25 Lane D — the domain skills each stage LOADS for this spec (the neutral twin of the
    # per-turn routing lines): a harness without hooks reads the same answer here.
    from ..domains import LOADING_STAGES, skills_for_stage
    out["domain_skills"] = {st: list(skills_for_stage(spec.domains, st)) for st in LOADING_STAGES}
    if spec.scope is not None:
        out["scope"] = spec.scope.to_dict()
    out["block"] = LazyRender(lambda: _render_spec_show(spec, shown, current))
    return apply_response_format(response_format, out)


def _render_spec_show(spec: Any, version: int = 0, current: int = 0) -> str:
    """The human view — the SAME lines `cmd_spec_show` prints, in the same order (title, version,
    approach, domains, then each AC). Built lazily, so `concise` never pays for the string it drops."""
    from ..cli_commands.spec import _version_line
    lines = [f"spec: {spec.title}", _version_line(version, current)]
    if spec.approach:
        lines.append(f"approach: {spec.approach}")
    if spec.domains:
        lines.append(f"domains: {', '.join(spec.domains)}")
        from ..domains import domain_routing_line
        routing = domain_routing_line(spec.domains)
        if routing:
            lines.append(routing)
    for c in spec.criteria:
        lines.append(f"  {c.id}: {c.text}")
    from ..engine.spec_anchor import ac_anchor_lines
    lines.extend(f"  → {ln}" for ln in ac_anchor_lines(spec))       # SPEC-ANCHOR S2
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
# SHIP'S REVIEW GATE. The four blocking answers are the CLI's, verbatim, from the same
# `ship_review_gate`: no run resolved, STALE verdict (older than `settings.review.verdict_max_age_hours`,
# default 24), UNREADABLE log (`readable:false`), or no verdict (`readable:true, present:false`).
# REVIEW-FIX.R4: a read that RAISES is a different fact from "review hasn't run" and says so. The tool
# NEVER returns the verdict's `findings` text (it can quote project content). SL.S3 `caught`, D4 `observed`.
@_tool("review_status", "read")
def review_status(path: _Path = ".", run: _Run = "",
                  response_format: _Format = "concise") -> Dict[str, Any]:
    """Read the persisted review verdict ship will check: did the closing review run, pass, and block shipping?

Evidence on disk, not conversation memory. Blocks when no run resolves, the verdict is stale (older
than `settings.review.verdict_max_age_hours`, default 24) or unreadable, or none was recorded. An
inline pass does not block but is flagged. Never returns the findings text. Read-only.

Returns: `run`, `present`, `readable`, `passed`, `independent`, `blocks`, `recorded_at`, `message`,
`unblock`, `caught`; `detailed` adds `block`.
Errors: refused (invalid_input) for an unknown `response_format`.
    """
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
    # D4 — for an independent pass: was a subagent of this run SEEN to finish (True), only claimed
    # (False), or not recorded (None)? Informational: the gate's decision above does not read it.
    out["observed"] = gate.observed
    out["caught"] = _caught_figures(surface, run_id)
    out["block"] = LazyRender(lambda: _render_review_status(gate))
    return apply_response_format(response_format, out)


def _caught_figures(surface: Any, run_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """SL.S3 (0.0.22 C2) — what review caught vs what the loop caught: for this run, and summed over
    every MEASURED run. `rate` is None when unmeasured or when nothing was caught — never a 0 that
    nobody counted. None when the figures cannot be read (the verdict above still stands)."""
    try:
        from ..progress_events import review_yield, review_yield_all

        def _d(y: Any) -> Dict[str, Any]:
            return {"in_loop": y.in_loop, "at_review": y.at_review, "measured": y.measured,
                    "rate": y.rate, "line": y.line()}
        return {"run": _d(review_yield(surface, run_id)), "all_runs": _d(review_yield_all(surface))}
    except Exception:  # noqa: BLE001 — informational; never turns a status read into a failure
        return None


def _render_review_status(gate: Any) -> str:
    """The human view — the SAME single line `mokata progress review-status` prints (the gate
    message, plus its unblock remedy when it blocks). Built lazily, so concise never pays for it."""
    return _render_review_line(gate.message, gate.unblock)


def _render_review_line(message: str, unblock: str) -> str:
    """The CLI's one-line composition, from message + remedy. Split out at REVIEW-FIX.R4 so the
    DEGRADE answer — which has no `ReviewGate` to render, because building one is what failed —
    renders identically to every other answer instead of losing its `detailed` view."""
    return message + (f"  → to unblock: {unblock}" if unblock else "")


# TRUST POSTURE (grounded, unchanged from the CLI): recording a verdict is observability tier —
# append-only and UNGATED like `mokata progress mark` and the audit ledger — so this is a READ-kind tool
# with no proposal and no WriteGate (the `session_save` precedent). It stays safe because the record is
# not what a gate TRUSTS: `ship_review_gate` re-derives whose run, how old, passed or not.
# 0.0.28 I2: the CLI's mutually-exclusive passed/failed pair is ONE required `outcome`. A caller fault
# (bad `blocking`, or a verdict contradicting its Blocking count) raises `ValidationError`; the
# `status:"error"` below is the genuine record-write failure. REVIEW-FIX.R4: a failed record is LOUD
# (`recorded:false`, `satisfies_gate:false`, the CLI's sentence); a run-less record satisfies nothing.
# SL.S3: omitted `blocking` keeps the caught-at-review rate "not measured", never zero.
@_tool("review_record", "read")
def review_record(path: _Path = ".", *,
                  outcome: Annotated[Literal["passed", "failed"], Field(
                      description="The review's conclusion, e.g. `outcome=\"passed\"`.")],
                  independent: Annotated[bool, Field(description="True when the review ran as a "
                                                     "fresh-context subagent (default false: inline)."
                                                     )] = False,
                  findings: Annotated[str, Field(description="Optional count or summary stored with "
                                                 "the verdict (default: none).")] = "",
                  run: _Run = "",
                  blocking: Annotated[Optional[int], Field(
                      description="How many Blocking findings the review raised (0 if none); "
                                  "omit if not counted.")] = None) -> Dict[str, Any]:
    """Record the closing review's verdict; ship accepts only this as evidence the review ran.

Call once, at the end of /mokata:review: `review_record(outcome="passed")` or `outcome="failed"`.
Append-only and ungated. `independent` marks a fresh-context subagent review; `blocking` is the
count of Blocking findings and must agree with the outcome. A verdict with no resolvable run
satisfies nothing, and says so.

Returns: `recorded`, `run`, `passed`, `independent`, `recorded_at`, `message`; `satisfies_gate: false` if run-less.
Errors: refused (invalid_input) for a bad `outcome` or a `blocking` that contradicts it; `error` if the write failed.
    """
    from ..cli_commands.runviews import (review_record_failed_line, review_recorded_line,
                                         review_runless_line)
    from ..progress_events import _CURRENT_RUN, record_review_verdict
    validate_enum(outcome, ("passed", "failed"), "outcome")
    passed = outcome == "passed"
    if blocking is not None and (isinstance(blocking, bool) or not isinstance(blocking, int)
                                 or blocking < 0):
        raise ValidationError("blocking", "must be a whole number of Blocking findings (0 or more)")
    from ..progress_events import ReviewLoopCapped, verdict_contradiction
    if verdict_contradiction(passed, blocking):
        raise ValidationError(
            "blocking", "contradicts `outcome`: a passed review cannot have Blocking findings, "
                        "and a failed one must name at least one; nothing was recorded")
    surface = _surface(path)
    try:
        event = record_review_verdict(surface, passed=passed, independent=independent,
                                      findings=findings or None, blocking=blocking,
                                      run_id=(run or "").strip() or _CURRENT_RUN,
                                      tree_root=path)
    except ReviewLoopCapped as exc:
        # R4 — not a recording FAULT: the loop is over its cap and round six escalates. Nothing was
        # recorded, on purpose; the message is the escalation the review skill relays to the human.
        return {"recorded": False, "status": "escalate", "run": None, "satisfies_gate": False,
                "reason": str(exc), "message": str(exc),
                "hint": "stop the review/fix loop and hand it to the human."}
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
    verdict = outcome
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
def vault_list(path: _Path = ".", limit: _Limit = DEFAULT_PAGE_LIMIT,
               offset: _Offset = 0) -> Dict[str, Any]:
    """List the team design vault's entries (brainstorm and spec artifacts): name, kind, author, date.

Read-only, paged from the start of the name-sorted listing.

Returns: `entries`, `count` (this page), `total`, `has_more`, `next_offset`.
Errors: none expected; a negative `limit` or `offset` is clamped.
    """
    from ..vault import vault_list as _list
    entries = _list(path)
    return paginate([{"name": e.name, "kind": e.kind, "title": e.title,
                      "author": e.author, "version": e.version,
                      "updated_at": e.updated_at} for e in entries],
                    key="entries", limit=limit, offset=offset)


@_tool("vault_search", "read")
def vault_search(path: _Path = ".", *,
                 query: Annotated[str, Field(description="Text to look for in names, titles and "
                                             "bodies.")],
                 limit: _Limit = DEFAULT_PAGE_LIMIT, offset: _Offset = 0) -> Dict[str, Any]:
    """Search the design vault by name, title or body (lexical), best match first.

Read-only, paged down the ranking.

Returns: `hits` (name, kind, title, score, author), `count`, `total`, `has_more`, `next_offset`.
Errors: refused (invalid_input) for an empty `query`.
    """
    from ..vault import vault_search as _search
    if not (query or "").strip():
        raise ValidationError("query", "is required: the text to search for")
    hits = _search(path, query)
    return paginate([{"name": h.entry.name, "kind": h.entry.kind, "title": h.entry.title,
                      "score": round(h.score, 4), "author": h.entry.author,
                      "updated_at": h.entry.updated_at} for h in hits],
                    key="hits", limit=limit, offset=offset)


# A VaultError (no such entry, corrupt artifact) propagates to the front door; an empty `name` is the caller's fault.
@_tool("vault_pull", "read")
def vault_pull(path: _Path = ".", *,
               name: Annotated[str, Field(description="Artifact name, as listed by `vault_list`.")],
               dest: Annotated[str, Field(description="File to also write the markdown to "
                                          "(default: return it only).")] = "") -> Dict[str, Any]:
    """Fetch one named design artifact from the vault for review, returning its markdown.

Read-only on the vault; with `dest` it also writes a copy there. The stored content hash is verified,
so a corrupted artifact is reported rather than served.

Returns: `status`, `name`, `kind`, `version`, `author`, `updated_at`, `dest`, `content`.
Errors: refused (invalid_input) for an empty `name` or one the vault cannot serve.
    """
    from ..vault import VaultError, vault_pull as _pull
    if not (name or "").strip():
        raise ValidationError("name", "is required: the artifact name from `vault_list`")
    _surface(path)      # an uninitialized repo is refused with `mokata init`, never an error
    try:
        content, entry = _pull(path, name, dest=dest or None)
    except VaultError as exc:
        # The caller named an entry the vault cannot serve (absent, or its file gone): their
        # argument, not a mokata fault. Fixed text — the error's own message quotes the name.
        raise ValidationError("name", "names no entry this vault can serve; `vault_list` shows "
                              "the names it has") from exc
    return {"status": "ok", "name": entry.name, "kind": entry.kind,
            "version": entry.version, "author": entry.author,
            "updated_at": entry.updated_at, "dest": dest or None, "content": content}


# --------------------------------------------------------------------------------------
# Stage 70 — community stacks READ tools (list/search/show). install = the gated adopt path.
# NO hosted marketplace: publish is over git/the vault; the index is a reviewable index.json.
# --------------------------------------------------------------------------------------
# Stage 70 — NO hosted marketplace: reads a versioned index.json. 0.0.25 Lane D `detected` is suggest-only. A StackError propagates as a mokata-side error.
@_tool("stacks_list", "read")
def stacks_list(path: _Path = ".",
                source: Annotated[str, Field(description="Catalog to read: a git-org or vault directory, or an "
                                         "index.json (default: the bundled curated index).")] = "",
                limit: _Limit = DEFAULT_PAGE_LIMIT, offset: _Offset = 0) -> Dict[str, Any]:
    """List the curated community stack catalog (per-framework governed stacks).

Read-only, paged from the start of the name-sorted catalog. There is no hosted marketplace: it reads
a versioned `index.json`. `detected` lists starter stacks this repo looks like; nothing is installed.

Returns: `stacks`, `count`, `total`, `has_more`, `next_offset`, `detected`, `note`.
Errors: `error` if the catalog at `source` cannot be read.
    """
    from .. import stacks as ST
    index = ST.load_index(source or None)
    entries = ST.list_stacks(index)
    # 0.0.25 Lane D — the starter stacks the repo at `path` looks like, with each package's test
    # runner / linters / type checker. Suggest-only: nothing is installed or written.
    detected = [d.to_dict() for d in ST.detect_stacks(path or ".")]
    return {"status": "ok", "hosted": False,
            **paginate(entries, key="stacks", limit=limit, offset=offset),
            "detected": detected, "note": ST.HONEST_NOTE}


# Stage 70. A StackError propagates as a mokata-side error.
@_tool("stacks_search", "read")
def stacks_search(path: _Path = ".", *,
                  query: Annotated[str, Field(description="Text to match against stack name, "
                                              "framework, summary and tags.")],
                  source: Annotated[str, Field(description="Catalog to read: a git-org or vault directory, or an "
                                         "index.json (default: the bundled curated index).")] = "",
                  limit: _Limit = DEFAULT_PAGE_LIMIT, offset: _Offset = 0) -> Dict[str, Any]:
    """Search the community stack catalog by name, framework, summary or tags (lexical), best match first.

Read-only, paged down the ranking.

Returns: `hits` (the catalog entry plus `score`), `count`, `total`, `has_more`, `next_offset`.
Errors: refused (invalid_input) for an empty `query`; `error` if the catalog cannot be read.
    """
    from .. import stacks as ST
    if not (query or "").strip():
        raise ValidationError("query", "is required: the text to search the catalog for")
    index = ST.load_index(source or None)
    hits = ST.search_stacks(query, index)
    return {"status": "ok",
            **paginate([dict(h.entry, score=round(h.score, 4)) for h in hits],
                       key="hits", limit=limit, offset=offset)}


# Stage 70. A StackError propagates as a mokata-side error; an absent name is `not_found`.
@_tool("stacks_show", "read")
def stacks_show(path: _Path = ".", *,
                name: Annotated[str, Field(description="Stack name, as listed by `stacks_list`.")],
                source: Annotated[str, Field(description="Catalog to read: a git-org or vault directory, or an "
                                         "index.json (default: the bundled curated index).")] = "") -> Dict[str, Any]:
    """Show one community stack's catalog entry: framework, guardrail count, recommended skills and tags.

Read-only.

Returns: `stack` and `status: "ok"`, or `status: "not_found"` with a `message`.
Errors: refused (invalid_input) for an empty `name`; `error` if the catalog cannot be read.
    """
    from .. import stacks as ST
    if not (name or "").strip():
        raise ValidationError("name", "is required: the stack name from `stacks_list`")
    index = ST.load_index(source or None)
    entry = ST.show_stack(name, index)
    if entry is None:
        return {"status": "not_found", "message": f"no stack named '{name}' in the catalog"}
    return {"status": "ok", "hosted": False, "stack": entry}


# M-4/R5 (0.0.16) — registered LAST, not beside `recall` where it reads more naturally. The
# registry snapshot pins tool ORDER, and its own rule is that a new tool is APPENDED so every
# pre-existing entry keeps its position; slotting this next to `recall` would have shifted ~35
# entries and broken exactly what that guard protects.
# M-4/R5 PHASE 1 — mokata never drafts a summary (it calls no model); the agent drafts, the human gates via `consolidate`. Registered LAST on purpose (registry order is append-only).
@_tool("consolidate_proposals", "read")
def consolidate_proposals(path: _Path = ".") -> Dict[str, Any]:
    """Get the drafting request for memory consolidation: clusters of past turns that want a summary.

Read-only; writes and applies nothing. mokata calls no model: you write each summary from the turns
returned, then submit it with the human-gated `consolidate` tool.

Returns: `enabled`, `instruction`, `drafting_requests`.
Errors: none expected; `enabled: false` when memory is off in this profile.
    """
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


# --------------------------------------------------------------------------------------
# 0.0.22 C1 (`B-LIFE-FU`) — the stage mark and the ship-completion event, in-harness. APPENDED to
# the tail of the READ block (registration order is the server's order, and the registry snapshot
# protects every pre-existing position), not slotted beside `review_record` where they belong.
# --------------------------------------------------------------------------------------
# 0.0.22 C1 (`B-LIFE-FU`), appended at the READ tail (registry order is append-only). UNGATED, like its
# CLI twin: a progress-event append is observability. REFUSES when several runs exist and none is
# identifiably yours (a stage stamped into the wrong run is a false green). SESSION-BIND-BY-ANCESTOR
# (0.0.23 F1). R2 (0.0.26) — the stage attests the approved plan hash or is refused. The broad
# `except` below is the genuine mokata-side fault path (`status:"error"`, never a caller fault).
@_tool("progress_mark", "read")
def progress_mark(path: _Path = ".", *,
                  stage: Annotated[_Stage, Field(description="The stage being entered: brainstorm, "
                                                 "spec, develop, review or ship.")],
                  run: _Run = "") -> Dict[str, Any]:
    """Record entering a pipeline stage so the badge shows the true current stage.

Call it on entry to develop, review or ship: `progress_mark(stage="develop")`. Append-only and ungated.
Refuses when several runs exist and none is yours (naming them) or the approved plan is not attested;
a failure is reported, never raised, and never blocks the phase.

Returns: `recorded`, `status`, `run`, `stage`, `message`.
Errors: refused (invalid_input) for an unknown `stage`; `refused` result when the run is ambiguous; `error` if the write failed.
    """
    from ..progress import STAGE_BADGE_STAGES
    from ..progress_events import ProgressLog, STAGE_ENTER
    from ..run_resolver import resolve_run_for_process, unresolved_reason
    stage = (stage or "").strip()
    validate_enum(stage, STAGE_BADGE_STAGES, "stage")
    surface = _surface(path)    # uninitialized: refused with `mokata init`, not a write failure
    try:
        # SESSION-BIND-BY-ANCESTOR (0.0.23 F1): the session this MCP serves, via process ancestry.
        res = resolve_run_for_process(surface.root, run_id=(run or "").strip() or None)
        if res.ambiguous:
            return {"recorded": False, "status": "refused", "run": None,
                    "message": f"mokata progress: not recording '{stage}' — "
                               + unresolved_reason(res)}
        # R2 (0.0.26) — the stage ATTESTS the approved plan hash, or is refused (nothing recorded).
        from ..plan_tasks import stage_attestation
        attested, refusal = stage_attestation(surface.root, res.run_id, stage)
        if refusal:
            return {"recorded": False, "status": "refused", "run": res.run_id,
                    "message": f"mokata progress: {refusal}"}
        ProgressLog.from_surface(surface).append_event(STAGE_ENTER, stage, run_id=res.run_id,
                                                       data=attested)
        if stage == "ship" and res.run_id:
            try:
                from ..govern.resume import PipelineCheckpoint
                PipelineCheckpoint(surface.state, res.run_id).mark_completed()
            except Exception:          # noqa: BLE001 — the CLI twin's best-effort stamp
                pass
    except Exception as exc:             # noqa: BLE001
        return {"recorded": False, "status": "error", "run": None,
                "reason": f"could not record '{stage}' ({exc})",
                "hint": f"keep going — a stage mark never blocks the phase; or run "
                        f"`mokata progress mark {stage}` from the terminal."}
    where = f" (run {res.run_id})" if res.run_id else ""
    return {"recorded": True, "status": "recorded", "run": res.run_id or None, "stage": stage,
            "message": f"mokata progress: entered '{stage}'{where}."}


# 0.0.22 C1. UNGATED (the tier of `progress_mark` / `review_record`). Appends `run_finished` and stamps
# `completed_at` so the run reads finished-THEN on every surface; NEVER recorded run-less (refuses and
# names `run`). `landing` is a Literal over `progress_events.LANDINGS`; the body still refuses through
# `FinishRefused` for a direct caller. The broad `except` is the genuine mokata-side write failure.
@_tool("run_finish", "read")
def run_finish(path: _Path = ".", *,
               landing: Annotated[_Landing, Field(description="How the human landed the run: "
                                                  "merged, pr, kept or discarded.")],
               run: _Run = "") -> Dict[str, Any]:
    """Record that the run is over and how the human landed it.

Call once at the end of /mokata:ship, after the human chose: `run_finish(landing="merged")`. Appends
the completion event and stamps the run's `completed_at`. Ungated. Never recorded run-less: with no
run, or several and none yours, it refuses and names `run` as the fix.

Returns: `recorded`, `status`, `run`, `landing`, `recorded_at`, `message`.
Errors: refused (invalid_input) for an unknown `landing`; `refused` result with no resolvable run; `error` if the write failed.
    """
    from ..cli_commands.runviews import run_finish_refused_line, run_finished_line
    from ..progress_events import (LANDINGS, FinishRefused, _CURRENT_RUN,
                                   record_run_finished)
    landing = (landing or "").strip()
    validate_enum(landing, LANDINGS, "landing")
    surface = _surface(path)    # uninitialized: refused with `mokata init`, not a write failure
    try:
        event = record_run_finished(surface, landing=landing,
                                    run_id=(run or "").strip() or _CURRENT_RUN)
    except FinishRefused as exc:
        return {"recorded": False, "status": "refused", "run": None,
                "message": run_finish_refused_line(str(exc))}
    except Exception as exc:             # noqa: BLE001
        return {"recorded": False, "status": "error", "run": None,
                "message": run_finish_refused_line(f"could not write it ({exc})")}
    return {"recorded": True, "status": "recorded", "run": event["run_id"], "landing": landing,
            "recorded_at": event.get("ts"),
            "message": run_finished_line(event["run_id"], landing)}


# HP.S2 — ONE keyed object instead of N lookups. `note` APPENDS first (observability tier, like `progress_mark`). The `status:"error"` is a genuine read failure.
@_tool("working_set", "read")
def working_set(path: _Path = ".", run: _Run = "",
                note: Annotated[str, Field(description="One short note to append for the next phase "
                                           "(default: none).")] = "") -> Dict[str, Any]:
    """Get the run's working set in one call: next phase, last stage, spec and approach keys, touched files and notes.

`note` appends one short note for the next phase before reading (run state, ungated). Failures are
reported, never raised.

Returns: `run`, `status`, `phase`, `last_passed`, `stage`, `approach_key`, `spec_key`, `plan_slug`,
`touched_files`, `notes`; `status: "unresolved"` when no run can be resolved.
Errors: `error` result if the working set cannot be read.
    """
    from ..progress_events import _resolve_verdict_run
    from ..working_set import add_note, working_set as _ws
    surface = _surface(path)
    rid = (run or "").strip() or _resolve_verdict_run(surface)
    if not rid:
        return {"run": None, "status": "unresolved",
                "message": "no run could be resolved here — name it with `run` "
                           "(`mokata sessions` lists them)."}
    try:
        if note.strip():
            add_note(surface.root, rid, note)
        out = _ws(surface.root, rid).to_dict()
    except Exception as exc:                 # noqa: BLE001
        return {"run": rid, "status": "error", "message": f"could not read the working set ({exc})"}
    out["run"] = out.pop("run_id")
    out["status"] = "ok"
    return out



# 0.0.25 ID3 — ID.S3's `ide_*` tools (`ide_status`, `ide_open`, `ide_sessions`), all READ-kind.
# Imported HERE, at the very end, so they register at the READ tail: the registry order is an
# append-only public surface (`test_pre_simp_tool_registry_parity`).
from . import tools_ide  # noqa: E402,F401
# 0.0.27 P46 — `explain_bundle` + `explain_save` (the change explainer), APPENDED to the READ tail
# after the ide_* tools (registry order is append-only).
from . import tools_explain  # noqa: E402,F401
