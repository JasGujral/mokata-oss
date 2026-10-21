"""The IDE's health panel (0.0.25): what is missing, degraded or out of sync — and the few things
that can be FORCED back into sync from the page.

Jas, 2026-10-19: "in IDE no need to add dependency install — just highlight if anything is missing
or degraded — add option to force update code-review-graph or any such thing that can go out of
sync", and "at least easy update from UI".

So, by design:

  * HIGHLIGHT, never install. Missing / degraded rows come from `mokata doctor`'s own verdicts
    (`govern.doctor.diagnose`) — the same text, the same fix line — plus three rows only a running
    IDE can see (the IDE's own version vs the installed one, the code graph actually answering,
    the semantic-search index). A missing dependency names its command; nothing here installs one.
  * FORCE A RE-SYNC of what is derived and therefore can drift: the code graph's index (CRG
    `build`, a full rebuild), mokata's built-in AST index (drop + re-parse), the semantic-search
    index (rebuild). All three are caches under the repo; none is governed state.
  * UPDATE mokata itself — the installer-aware plan `mokata upgrade` runs (`install_route.
    plan_upgrade`). The page shows the exact command and the human confirms it; the server never
    runs it unasked. The version CHECK is the one outbound call (opt-in, a button, ledger-accounted
    by `version.check_for_update`).

Long work (a CRG build on a 1M-line repo is ~6 min) runs as ONE background job at a time; the page
asks for its state while it runs and stops asking when it ends. Nothing here runs unless the page
asks — opening the IDE costs nothing.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from .. import __version__

ACTIONS = ("rebuild-graph", "rebuild-ast", "rebuild-search", "update")
OK, MISSING, DEGRADED, STALE, BROKEN = "ok", "missing", "degraded", "out of sync", "broken"
_SEVERITY = {"error": BROKEN, "warning": DEGRADED}
# Doctor warnings that describe a configuration choice, not something missing or degraded: a
# capability claimed by several providers is RESOLVED by precedence (the default manifest has it).
_NOT_A_PROBLEM = frozenset({"role-conflict"})


# ----------------------------------------------------------------------------- the one job
class _Job:
    """At most one maintenance job at a time (they all hit the same disk and the same graph)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.state: Dict[str, Any] = {"action": None, "state": "idle"}

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self.state)

    def start(self, action: str, fn: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
        with self._lock:
            if self.state.get("state") == "running":
                return {**self.state, "refused": f"{self.state['action']} is still running"}
            self.state = {"action": action, "state": "running", "started": time.time()}

        def run() -> None:
            try:
                out = fn()
                ok = bool(out.get("ok"))
            except Exception as exc:  # noqa: BLE001 — a job's failure is REPORTED, never lost
                out, ok = {"message": f"{type(exc).__name__}: {exc}"}, False
            with self._lock:
                self.state = {**self.state, **out, "state": "done" if ok else "failed",
                              "finished": time.time()}
        threading.Thread(target=run, name=f"mokata-ide-{action}", daemon=True).start()
        return self.snapshot()


JOB = _Job()


# ----------------------------------------------------------------------------- rows
def _row(key: str, label: str, state: str, detail: str, fix: str = "",
         action: str = "", action_label: str = "") -> Dict[str, Any]:
    return {"key": key, "label": label, "state": state, "detail": detail, "fix": fix,
            "action": action, "action_label": action_label}


def _installed_version() -> Optional[str]:
    from importlib import metadata
    try:
        return metadata.version("mokata")
    except metadata.PackageNotFoundError:
        return None


def _mokata_row() -> Dict[str, Any]:
    from ..version import upgrade_plan
    inst = _installed_version()
    plan = upgrade_plan("auto")
    upd = {"command": plan.display, "runnable": plan.runnable, "deferred": plan.deferred,
           "reason": plan.reason, "installer": plan.installer}
    if inst and inst != __version__:
        r = _row("mokata", "mokata", STALE,
                 f"this IDE is running {__version__}, but {inst} is installed — restart the IDE "
                 "to use it (close it, then `mokata ide`)", fix="mokata ide")
    else:
        r = _row("mokata", "mokata", OK, f"{__version__}" + (f" · installed by {plan.installer}"
                                                             if plan.installer else ""))
    r["update"] = upd
    return r


def _graph_rows(root: str, layer: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    primary = getattr(layer, "primary", None)
    name = getattr(layer, "backend_name", "") or "none"
    client = getattr(primary, "client", None)
    if client is not None and callable(getattr(client, "refresh", None)):
        db = os.path.join(root, ".code-review-graph", "graph.db")
        try:
            age = time.time() - os.path.getmtime(db)
            when = f"index last built {int(age // 60)} min ago" if age < 86400 * 2 \
                else f"index last built {int(age // 86400)} days ago"
        except OSError:
            when = "no index on disk yet"
        from . import nav
        h = nav.graph_health(root)
        if h:
            rows.append(_row("graph", f"code graph ({name})", DEGRADED, nav.degraded_line(h)
                             + f" · {when}", fix="mokata doctor",
                             action="rebuild-graph", action_label="Force rebuild"))
        else:
            rows.append(_row("graph", f"code graph ({name})", OK, when,
                             action="rebuild-graph", action_label="Force rebuild"))
    else:
        rows.append(_row("graph", f"code graph ({name})", OK if name == "ast" else DEGRADED,
                         "mokata's built-in Python reader" if name == "ast"
                         else f"answering from {name}",
                         fix="" if name == "ast" else "mokata setup claude --mode full"))
    from ..knowledge.freshness import _ast_backends
    if _ast_backends(layer):
        rows.append(_row("ast", "built-in AST index", OK,
                         "re-parses only changed files; force a full re-parse if answers look stale",
                         action="rebuild-ast", action_label="Force re-parse"))
    return rows


def _search_row(root: str) -> Dict[str, Any]:
    from . import search as SR
    st = SR.status()
    if not st.get("available"):
        return _row("search", "search by meaning", MISSING,
                    st.get("note") or "the local model is not installed",
                    fix="mokata setup claude --mode memory")
    built = os.path.isdir(SR.index_dir(root))
    return _row("search", "search by meaning", OK if built else STALE,
                "index built" if built else "no index yet — built on first search",
                action="rebuild-search", action_label="Force rebuild")


def _wiring_rows(root: str) -> List[Dict[str, Any]]:
    """The cheap doctor check worth running at page load: is the Claude Code wiring the wiring
    THIS mokata writes? (File reads only — no spawn.)"""
    from ..config import Surface
    from ..govern.doctor import wiring_drift_findings
    try:
        found = wiring_drift_findings(Surface.load(root))
    except Exception as exc:  # noqa: BLE001 — "could not check" is itself a row
        return [_row("wiring", "Claude Code wiring", BROKEN,
                     f"could not check ({type(exc).__name__}: {exc})", fix="mokata doctor --wiring")]
    return [_row("wiring", "Claude Code wiring", STALE if f.severity == "warning" else BROKEN,
                 f.detail, fix="mokata setup claude") for f in found
            if f.severity in ("warning", "error")]


def _doctor_rows(root: str) -> List[Dict[str, Any]]:
    """`mokata doctor`'s verdicts — errors and warnings only; info lines are not problems."""
    from ..config import Surface
    from ..govern.doctor import diagnose
    try:
        report = diagnose(Surface.load(root))
    except Exception as exc:  # noqa: BLE001 — "could not check" is itself a row, never a blank
        return [_row("doctor", "mokata doctor", BROKEN,
                     f"the checks could not run ({type(exc).__name__}: {exc})",
                     fix="mokata doctor")]
    out = []
    for f in report.findings:
        state = _SEVERITY.get(f.severity)
        if state and f.code not in _NOT_A_PROBLEM:
            if f.code == "hooks-wiring-stale":
                out.append(_row("wiring", "Claude Code wiring", STALE, f.detail,
                                fix="mokata setup claude"))
            else:
                out.append(_row(f"doctor:{f.code}", f.code, state, f.detail, fix="mokata doctor"))
    return out


def health(root: str, layer: Any, *, demo: bool = False, full: bool = True) -> Dict[str, Any]:
    """Every row, worst first. `full=False` is the page-load pass: version, graph, search index
    and the wiring — file reads only, no spawn. `full=True` (the panel opened) adds every
    `mokata doctor` verdict."""
    if demo:
        return {"sample": True, "rows": [_row("mokata", "mokata", OK, "SAMPLE")],
                "problems": 0, "full": full, "job": JOB.snapshot()}
    rows = [_mokata_row()] + _graph_rows(root, layer) + [_search_row(root)]
    rows += _doctor_rows(root) if full else _wiring_rows(root)
    order = {BROKEN: 0, MISSING: 1, DEGRADED: 2, STALE: 3, OK: 4}
    rows.sort(key=lambda r: order.get(r["state"], 9))
    bad = sum(1 for r in rows if r["state"] != OK)
    return {"sample": False, "rows": rows, "problems": bad, "full": full, "job": JOB.snapshot()}


# ----------------------------------------------------------------------------- the actions
def _rebuild_graph(root: str, layer: Any) -> Dict[str, Any]:
    client = getattr(getattr(layer, "primary", None), "client", None)
    if client is None or not callable(getattr(client, "refresh", None)):
        return {"ok": False, "message": "no adopted code graph to rebuild"}
    t0 = time.time()
    ok = bool(client.refresh(root, full=True))
    return {"ok": ok, "message": (f"code graph rebuilt in {time.time() - t0:.0f}s" if ok else
                                  "the code graph's rebuild failed — `mokata doctor` names why")}


def _rebuild_ast(root: str, layer: Any) -> Dict[str, Any]:
    from ..knowledge.freshness import _ast_backends
    backends = _ast_backends(layer)
    if not backends:
        return {"ok": False, "message": "no built-in AST index in use"}
    t0 = time.time()
    for b in backends:
        cache = getattr(b, "cache_dir", None)
        if cache and os.path.isdir(cache):
            shutil.rmtree(cache, ignore_errors=True)
        b.invalidate()
        ensure = getattr(b, "_ensure_index", None)
        if callable(ensure):
            ensure()
    return {"ok": True, "message": f"AST index re-parsed in {time.time() - t0:.0f}s"}


def _rebuild_search(root: str) -> Dict[str, Any]:
    from ..memory.embed import ModelUnavailable
    from . import search as SR
    try:
        emb = SR._load_embedder(allow_network=False)
    except ModelUnavailable as exc:
        return {"ok": False, "message": f"the local model is not available — {exc}"}
    shutil.rmtree(SR.index_dir(root), ignore_errors=True)
    t0 = time.time()
    info = SR.build_index(root, emb) or {}
    n = info.get("embedded", "?")
    return {"ok": True, "message": f"search index rebuilt ({n} chunks) in {time.time() - t0:.0f}s"}


def _update(confirm: str) -> Dict[str, Any]:
    from ..version import installed_version, run_upgrade, upgrade_plan
    plan = upgrade_plan("auto")
    if not plan.runnable:
        return {"ok": False, "message": f"run it yourself: {plan.display}"
                + (f" ({plan.reason})" if plan.reason else "")}
    if (confirm or "").strip() != plan.display:
        # The confirmation is the exact command the human was shown. A stale page (the plan
        # changed) or a forged call cannot run something the human did not read.
        return {"ok": False, "message": "the command changed since it was shown — reopen the panel"}
    res = run_upgrade(plan)
    if not res.ok:
        return {"ok": False, "message": f"update failed (exit {res.returncode})",
                "output": res.output[-4000:]}
    now = installed_version() or "?"
    return {"ok": True, "message": f"updated: {__version__} → {now}. Restart the IDE to use it, "
            "then run `mokata setup claude` to refresh the Claude Code wiring.",
            "output": res.output[-4000:]}


def start(root: str, layer: Any, action: str, confirm: str = "") -> Dict[str, Any]:
    if action not in ACTIONS:
        from .server import BadRequest
        raise BadRequest(f"action must be one of {', '.join(ACTIONS)}")
    fn: Callable[[], Dict[str, Any]] = {
        "rebuild-graph": lambda: _rebuild_graph(root, layer),
        "rebuild-ast": lambda: _rebuild_ast(root, layer),
        "rebuild-search": lambda: _rebuild_search(root),
        "update": lambda: _update(confirm),
    }[action]
    return JOB.start(action, fn)


def check_update(root: str) -> Dict[str, Any]:
    """The ONE outbound call, on a button: compare with the latest published release."""
    from ..govern.ledger import AuditLedger
    from ..version import check_for_update
    try:
        led = AuditLedger.from_mokata_dir(os.path.join(root, ".mokata"))
    except (OSError, ValueError):
        led = None
    chk = check_for_update(ledger=led)
    return {"ok": chk.ok, "current": chk.current, "latest": chk.latest,
            "up_to_date": chk.up_to_date, "message": chk.message}


__all__ = ["ACTIONS", "JOB", "health", "start", "check_update"]
