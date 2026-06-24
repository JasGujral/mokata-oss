"""H4+ — the plugin-first MCP surface.

A thin Model Context Protocol server that exposes mokata operations as native tools for
Claude Code, so the framework is driven from inside the harness — not only the CLI. Every
tool delegates to the existing package; this module adds NO engine logic.

Two safety rules are non-negotiable:

  * READ tools (query, recall, doctor, coverage, budget, audit, status, preview) are safe
    and expose their data directly.
  * WRITE / durable tools (remember, import_stack, reset, apply_proposal) are ALWAYS
    human-gated. With no `confirm`, they are PROPOSE-ONLY: they return the staged change
    and write nothing. Only an explicit `confirm=true` — a human decision — performs the
    write, and even then it goes through the universal WriteGate (secrets are a hard block
    that approval cannot override) and is recorded in the audit ledger. An MCP call NEVER
    writes silently.

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
           mtype: Optional[str] = None) -> Dict[str, Any]:
    """Recall active memory. With a `subject`, return matching items; otherwise return all
    active items. Read-only — surfaces nothing disabled, writes nothing."""
    store = MemoryStore.from_surface(_surface(path))
    if not store.enabled_types:
        return {"enabled": False, "items": []}
    items = store.recall(subject, mtype=mtype) if subject else store.all_active(mtype=mtype)
    return {"enabled": True, "backend": store.backend.name,
            "items": [{"mtype": i.mtype, "subject": i.subject, "value": i.value}
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


# --------------------------------------------------------------------------------------
# WRITE tools — propose-only by default; explicit `confirm` performs the gated write.
# --------------------------------------------------------------------------------------
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
             mtype: str = DECISION, confirm: bool = False) -> Dict[str, Any]:
    """Remember a fact/decision in memory. HUMAN-GATED: without `confirm=true` this is
    propose-only and writes nothing. With `confirm=true` it commits through the WriteGate
    (a secret in `value` is blocked even when confirmed)."""
    surface = _surface(path)
    store = MemoryStore.from_surface(surface)
    if not store.enabled_types:
        return {"status": "unavailable", "message": "memory is disabled for this profile"}
    item = MemoryItem.create(subject, value, mtype=mtype)
    if not confirm:
        return {"status": "proposed", "action": "remember",
                "preview": store.render_write(item),
                "hint": "re-call with confirm=true to commit (your explicit approval)"}
    res = _gated_write(surface.mokata_dir, "memory", f"memory:{subject}", value,
                       lambda: store.remember(item, assume_yes=True).committed)
    return res


@_tool("import_stack", "write")
def import_stack(path: str = ".", file: str = "", confirm: bool = False,
                 force: bool = False) -> Dict[str, Any]:
    """Validate and apply a shared stack manifest. HUMAN-GATED: without `confirm=true` this
    only validates and reports what WOULD apply (no write). With `confirm=true` it applies
    (use `force=true` to overwrite an existing config)."""
    from .share import apply_manifest, load_shared, validate_shared
    try:
        data = load_shared(file)
    except (OSError, ValueError) as exc:
        return {"status": "error", "message": f"cannot read {file}: {exc}"}
    errors = validate_shared(data)
    if not confirm:
        return {"status": "proposed", "action": "import",
                "valid": not errors, "errors": errors,
                "would_apply_profile": data.get("profile"),
                "hint": "re-call with confirm=true to apply (your explicit approval)"}
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
          confirm: bool = False) -> Dict[str, Any]:
    """Remove mokata state (.mokata/). HUMAN-GATED: without `confirm=true` this lists what
    WOULD be removed (no deletion). With `confirm=true` it removes them. `keep_config`
    keeps the manifest + constitution."""
    plan = plan_reset(path, keep_config=keep_config)
    if not plan.targets:
        return {"status": "noop", "message": "reset: nothing to remove."}
    if not confirm:
        return {"status": "proposed", "action": "reset", "targets": plan.targets,
                "hint": "re-call with confirm=true to remove (your explicit approval)"}
    box: Dict[str, Any] = {}

    def _do_reset() -> Any:
        result = reset_state(path, keep_config=keep_config, assume_yes=True)
        box["reset"] = result
        return {"removed": result.removed, "aborted": result.aborted}

    res = _gated_write(_mokata_dir(path), "config", _mokata_dir(path), "", _do_reset)
    return res


@_tool("apply_proposal", "write")
def apply_proposal(path: str = ".", subject: str = "", decision: str = "approve",
                   confirm: bool = False) -> Dict[str, Any]:
    """Resolve a surfaced self-healing memory proposal (contradiction/staleness).
    HUMAN-GATED: without `confirm=true` it shows the staged old->new change (no write).
    With `confirm=true` it applies your `decision` (approve/reject/defer)."""
    if decision not in ("approve", "reject", "defer"):
        return {"status": "error",
                "message": "decision must be one of approve/reject/defer"}
    surface = _surface(path)
    store = MemoryStore.from_surface(surface)
    match = next((p for p in store.detect_issues() if p.subject == subject), None)
    if match is None:
        return {"status": "error",
                "message": f"no pending proposal for subject '{subject}'"}
    if not confirm:
        return {"status": "proposed", "action": "apply_proposal", "subject": subject,
                "kind": match.kind, "diff": match.diff(), "decision": decision,
                "hint": "re-call with confirm=true to apply (your explicit approval)"}
    res = _gated_write(
        surface.mokata_dir, "memory", f"memory:{subject}", match.diff(),
        lambda: store.apply_proposal(match, decision, assume_yes=True).message)
    return res


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
