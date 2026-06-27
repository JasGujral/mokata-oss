"""H4+ — the plugin-first MCP surface.

A thin Model Context Protocol server that exposes mokata operations as native tools for
Claude Code, so the framework is driven from inside the harness — not only the CLI. Every
tool delegates to the existing package; this module adds NO engine logic.

Two safety rules are non-negotiable:

  * READ tools (query, recall, doctor, coverage, budget, audit, status, preview) are safe
    and expose their data directly.
  * WRITE / durable tools (remember, import_stack, reset, apply_proposal) are ALWAYS
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
def recall(path: str = ".", subject: str = "",
           memory_type: Optional[str] = None, mtype: Optional[str] = None) -> Dict[str, Any]:
    """Recall active memory. With a `subject`, return matching items; otherwise return all
    active items. `memory_type` filters by storage tier (persistent/decision/episodic); `mtype`
    is a DEPRECATED alias. Read-only — surfaces nothing disabled, writes nothing."""
    store = MemoryStore.from_surface(_surface(path))
    if not store.enabled_types:
        return {"enabled": False, "items": []}
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
def audit(path: str = ".", limit: int = 0) -> Dict[str, Any]:
    """Show the append-only audit ledger (every gate decision + tool call). `limit` keeps
    the most recent N entries (0 = all). Read-only."""
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


# --------------------------------------------------------------------------------------
# MCP wiring — lazily imports the optional SDK; never imported by the core/CLI.
# --------------------------------------------------------------------------------------
def mcp_available() -> bool:
    """True when the optional MCP SDK is importable."""
    import importlib.util
    return importlib.util.find_spec("mcp") is not None


def build_server(default_path: str = ".") -> Any:
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
    args = parser.parse_args(argv)
    build_server(default_path=args.path).run()   # stdio transport (plugin-launched)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
