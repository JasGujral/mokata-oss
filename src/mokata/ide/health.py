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
  * UPDATE mokata itself — the row is `mokata update`'s own plan and executor (`update.py`, one
    source of truth): the installer, the ONE command that upgrades and keeps the extras, whether
    the vendored commands/skills match. "Update now" (the click is the consent; the page sends
    back the command it showed) runs it: on POSIX in a job whose output streams into the panel,
    then `mokata update --finish` with the new mokata, then "restart the IDE"; on Windows (the
    running interpreter is locked) through the detached updater, after which this server exits —
    the next `mokata ide` reports the result. The version CHECK is the one outbound call
    (opt-in, a button, ledger-accounted) and adds the release notes in between.

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
from ..errors import MokataError
from ..knowledge.cancel import checkpoint, kill_on_cancel, report

ACTIONS = ("rebuild-graph", "rebuild-ast", "rebuild-search", "update")
OK, MISSING, DEGRADED, STALE, BROKEN = "ok", "missing", "degraded", "out of sync", "broken"
NOT_SET_UP = "not set up"
#: INT-2a — a row whose check has not answered yet (the checks are still running as a job).
CHECKING = "checking"
_SEVERITY = {"error": BROKEN, "warning": DEGRADED}
# Doctor warnings that describe a configuration choice, not something missing or degraded: a
# capability claimed by several providers is RESOLVED by precedence (the default manifest has it).
_NOT_A_PROBLEM = frozenset({"role-conflict",
                            # P37 — Health lists background jobs itself (`_background_rows`)
                            "background-job-failed", "background-jobs"})


#: 0.0.26 P32 — each action runs as a JOB of the IDE's registry (`ide/jobs.py`): progress n/N in
#: the Activity drawer, Cancel, no time limit. They share the ONE maintenance lane, so two never run
#: at once (the second waits, visibly queued) — they hit the same disk and the same graph.
ACTION_KINDS = {"rebuild-graph": "index.crg", "rebuild-ast": "index.ast",
                "rebuild-search": "index.meaning", "update": "mokata.update"}
ACTION_LABELS = {"rebuild-graph": "rebuild the code graph", "rebuild-ast": "re-parse the AST index",
                 "rebuild-search": "rebuild the meaning index", "update": "update mokata"}
#: What the code-graph row says when graph views keep FAILING at use (R35).
FAILING_AT_USE = "graph views failing"
#: Set when this server must exit so the detached updater can replace mokata (Windows); the
#: server's exit watcher (`server.serve`) stops uvicorn when it is set.
EXIT = threading.Event()


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


def _mokata_row(root: str = ".") -> Dict[str, Any]:
    from ..update import plan_update, render_result, take_result
    inst = _installed_version()
    plan = plan_update(root, check=False)               # file reads only — no network, no spawn
    upd = plan.to_dict()
    drift = [v for v in plan.vendored if v.get("drift")]
    done = take_result()                                # the detached updater's report, once
    if done:
        r = _row("mokata", "mokata", OK if done.get("ok") else BROKEN, render_result(done),
                 fix="" if done.get("ok") else (done.get("manual") or ""))
    elif inst and inst != __version__:
        r = _row("mokata", "mokata", STALE,
                 f"this IDE is running {__version__}, but {inst} is installed — restart the IDE "
                 "to use it (close it, then `mokata ide`)", fix="mokata ide")
    elif drift:
        r = _row("mokata", "mokata", STALE,
                 f"{__version__} · vendored commands/skills ({drift[0]['scope']}): "
                 f"{drift[0]['summary']}", fix=plan.follow_up[0] if plan.follow_up else
                 "mokata setup claude --skills-only")
    else:
        r = _row("mokata", "mokata", OK, f"{__version__} · installed by {plan.installer}"
                 + (f" · keeps {plan.keeps}" if plan.keeps else ""))
    r["update"] = upd
    return r


def _graph_rows(root: str, layer: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    primary = getattr(layer, "primary", None)
    name = getattr(layer, "backend_name", "") or "none"
    client = getattr(primary, "client", None)
    status = None
    if client is not None and callable(getattr(primary, "index_status", None)):
        try:
            status = primary.index_status()
        except Exception:  # noqa: BLE001 — unknown is "no extra row", never a broken panel
            status = None
    from ..knowledge.crg_build import BUILDING, FAILED, UNBUILT
    if status is not None and status.state in (BUILDING, UNBUILT, FAILED):
        # 0.0.26 O13 — installed is not answering: three states, each with its own words, and only
        # a failed build is BROKEN (with its error and the rebuild action).
        floor = getattr(getattr(layer, "fallback", None), "name", "") or "the floor"
        if status.state == BUILDING:
            rows.append(_row("graph", f"code graph ({name})", STALE,
                             f"{status.line(name)} — the '{floor}' floor answers until it finishes"))
        elif status.state == UNBUILT:
            rows.append(_row("graph", f"code graph ({name})", STALE,
                             f"detected, unbuilt — no index for this repo yet; the first structural "
                             f"query builds it in the background (the '{floor}' floor answers "
                             f"meanwhile)", fix=fix_line("graph", "unbuilt"),
                             action="rebuild-graph", action_label="Build index"))
        else:
            rows.append(_row("graph", f"code graph ({name})", BROKEN,
                             f"{status.line(name)} — the '{floor}' floor answers meanwhile",
                             fix=fix_line("graph", "failed"),
                             action="rebuild-graph", action_label="Rebuild index"))
    elif client is not None and callable(getattr(client, "refresh", None)):
        db = os.path.join(root, ".code-review-graph", "graph.db")
        try:
            age = time.time() - os.path.getmtime(db)
            when = f"index last built {int(age // 60)} min ago" if age < 86400 * 2 \
                else f"index last built {int(age // 86400)} days ago"
        except OSError:
            when = "no index on disk yet"
        from . import nav
        from ..knowledge.layer import impact_backend_line
        impact = impact_backend_line(layer)          # 0.0.26 O2 — which backend answers impact
        h = nav.graph_health(root)
        if h:
            rows.append(_row("graph", f"code graph ({name})", DEGRADED, nav.degraded_line(h)
                             + f" · {when} · {impact}", fix="mokata doctor",
                             action="rebuild-graph", action_label="Force rebuild"))
        else:
            rows.append(_row("graph", f"code graph ({name})", OK, f"{when} · {impact}",
                             action="rebuild-graph", action_label="Force rebuild"))
    else:
        rows.append(_row("graph", f"code graph ({name})", OK if name == "ast" else DEGRADED,
                         "mokata's built-in Python reader" if name == "ast"
                         else f"answering from {name}",
                         fix="" if name == "ast" else fix_line("graph", "not-wired")))
    from ..knowledge.freshness import _ast_backends
    if _ast_backends(layer):
        rows.append(_row("ast", "built-in AST index", OK,
                         "re-parses only changed files; force a full re-parse if answers look stale",
                         action="rebuild-ast", action_label="Force re-parse"))
    return rows


def fix_line(capability: str, state: str) -> str:
    """0.0.26 P39 (R36) — the ONE fix line, doctor's (`govern.doctor.fix_line`): the search card,
    this panel and the Welcome screen print the same string for the same state."""
    from ..govern.doctor import fix_line as one_line
    return one_line(capability, state)


def _search_row(root: str) -> Dict[str, Any]:
    from . import search as SR
    st = SR.status()
    if not st.get("available"):
        return _row("search", "search by meaning", MISSING,
                    st.get("note") or "the local model is not installed",
                    fix=st.get("install") or "")          # P39 R36: the search card's own line
    # 0.0.26 O13 — built / building (with progress) / partial (resumes) / failed / none yet.
    bg = SR.build_state(root) or {}
    meta = SR._read_meta(root)
    if bg.get("state") == "building":
        p = bg.get("progress") or {}
        where = (f"{p.get('done', 0)}/{p.get('total', '?')} files, {p.get('chunks', 0)} chunks"
                 if p else "starting")
        return _row("search", "search by meaning", STALE,
                    f"building its index in the background ({where}) — searches answer from "
                    "what is indexed so far")
    if bg.get("state") == "failed":
        return _row("search", "search by meaning", BROKEN,
                    f"the index build failed — {bg.get('error')}", fix=fix_line("meaning", "failed"),
                    action="rebuild-search", action_label="Rebuild index")
    if meta.get("complete"):
        return _row("search", "search by meaning", OK,
                    f"index built — {meta.get('rows', 0)} chunks from "
                    f"{len(meta.get('files', {}))} files",
                    action="rebuild-search", action_label="Force rebuild")
    if meta.get("files"):
        return _row("search", "search by meaning", STALE,
                    f"index partly built ({len(meta.get('files', {}))} files) — the next search or "
                    "`mokata setup claude --mode full` resumes it",
                    action="rebuild-search", action_label="Force rebuild")
    return _row("search", "search by meaning", STALE, "no index yet — built on first search "
                "(in the background on a big repo) or by `mokata setup claude --mode full`",
                action="rebuild-search", action_label="Build index")


def _setup_row() -> Dict[str, Any]:
    from .statedir import SETUP_COMMAND, lights_up
    r = _row("setup", "mokata setup", NOT_SET_UP,
             f"mokata is not set up in this repo — run `{SETUP_COMMAND}`. Everything below still "
             "works; setup adds the governance rows and panels listed here.",
             fix=fix_line("setup", "not-set-up"))
    r["lights_up"] = lights_up()
    return r


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


#: A job handed to a background worker when the IDE closed is listed in Health this long after
#: it finished (P37); Activity lists it for `handoff.LIST_FOR_S`.
BACKGROUND_ROW_S = 24 * 3600.0
_RETRY = {"index.crg": ("rebuild-graph", "Rebuild index"),
          "index.meaning": ("rebuild-search", "Rebuild index"),
          "index.ast": ("rebuild-ast", "Re-parse"), "index.symbols": ("rebuild-ast", "Re-parse")}


def _background_rows(root: str, now: Optional[float] = None) -> List[Dict[str, Any]]:
    """P37 — the work the last close handed to a background worker: still running, or how it
    ended (done / failed, with the worker's own words)."""
    from . import handoff
    now = time.time() if now is None else now
    out = []
    for j in handoff.snapshots(root, now):
        when = hhmm_of(j.get("finished"))
        if j["state"] == "running":
            out.append(_row(f"background:{j['id']}", j["label"], STALE,
                            f"{j['note']}" + (f" · {j['done']}/{j['total']} {j['unit']}"
                                              if j.get("total") else "")))
        elif j["state"] == "failed":
            act, lab = _RETRY.get(str(j.get("kind")), ("", ""))
            out.append(_row(f"background:{j['id']}", j["label"], BROKEN,
                            f"failed after the IDE closed{when}: {j['error']}", action=act,
                            action_label=lab))
        elif now - float(j.get("finished") or 0) <= BACKGROUND_ROW_S:
            word = "finished" if j["state"] == "done" else j["state"]
            out.append(_row(f"background:{j['id']}", j["label"], OK,
                            f"{word} after the IDE closed{when}" +
                            (f" — {j['note'] or j['error']}" if (j['note'] or j['error']) else "")))
    return out


def hhmm_of(ts: Any) -> str:
    try:
        return " at " + time.strftime("%H:%M", time.localtime(float(ts))) if ts else ""
    except (TypeError, ValueError):
        return ""


def _maintenance(jobs: Any) -> List[Dict[str, Any]]:
    if jobs is None:
        return []
    listing = jobs.listing()
    return [j for j in listing["active"] + listing["recent"][:5]
            if j["kind"] in ACTION_KINDS.values()]


#: 0.0.26 P39 — what a failing job kind says on its capability's row (R35 "failing at use").
FAILING_WORDS = {"graph.view": FAILING_AT_USE, "graph.query": FAILING_AT_USE,
                 "impact": FAILING_AT_USE, "index.crg": "code graph build failing",
                 "index.ast": "AST index failing", "index.meaning": "meaning index build failing",
                 "search.meaning": "meaning search failing", "search.text": "word search failing",
                 "mokata.init": "mokata init failed"}
_ROW_LABEL = {"lexical": "word search", "graph": "code graph", "search": "search by meaning",
              "ast": "built-in AST index", "setup": "mokata setup"}


def _failing_at_use(rows: List[Dict[str, Any]], jobs: Any) -> None:
    """R35 — a capability is judged by what it DOES: a job of its kind that failed (and no later
    one of that kind succeeded) marks its row degraded, with the job's error and a Retry that
    re-runs that job — whatever the presence probe says. (0.0.26 P32 did this for graph views;
    P39 feeds every capability's jobs: index builds, meaning and word search, init.)"""
    failing = jobs.failing() if jobs is not None else {}
    by_cap: Dict[str, List[Dict[str, Any]]] = {}
    for f in failing.values():
        by_cap.setdefault(f.get("capability") or "graph", []).append(f)
    for cap, fs in by_cap.items():
        last = max(fs, key=lambda f: f["at"])
        why = f"{FAILING_WORDS.get(last['kind'], 'failing')}: {last['label']} failed — " \
              f"{last['error']}"[:400]
        row = next((r for r in rows if r["key"] == cap), None)
        if row is None:
            row = _row(cap, _ROW_LABEL.get(cap, cap), DEGRADED, "")
            rows.append(row)
        if row["state"] in (OK, STALE):
            row["state"] = DEGRADED
        row["detail"] = f"{why} · {row['detail']}" if row["detail"] else why
        row["failing"] = sorted(f["kind"] for f in fs)
        row["retry"] = {"kind": last["kind"], "args": last.get("args") or {}}
        if cap == "graph" and not row.get("action"):
            row["action"], row["action_label"] = "rebuild-graph", "Rebuild index"


def _follow_external_build(root: str, layer: Any, jobs: Any) -> None:
    """A build another process runs (first use, a terminal) is shown in Activity as a job that
    follows its progress — never started twice, never stopped from here."""
    if jobs is None or not getattr(getattr(layer, "primary", None), "client", None):
        return
    from ..knowledge import crg_build as CB
    st = CB.build_status(root)
    if st.state != CB.BUILDING:
        return
    lock = CB._read_json(CB._lock_path(root))
    if lock.get("pid") == os.getpid():
        return                                   # ours: already a job
    job = jobs.start("index.crg", {"follow": True}, lambda: CB.watch_running(root).line(),
                     label="code graph index (building elsewhere)",
                     scope=os.path.basename(os.path.abspath(root)), keep=True)
    job.cancellable = False


def health(root: str, layer: Any, *, demo: bool = False, full: bool = True,
           jobs: Any = None) -> Dict[str, Any]:
    """Every row, worst first. `full=False` is the page-load pass: version, graph, search index
    and the wiring — file reads only, no spawn. `full=True` (the panel opened) adds every
    `mokata doctor` verdict."""
    if demo:
        return {"sample": True, "rows": [_row("mokata", "mokata", OK, "SAMPLE")],
                "problems": 0, "full": full, "jobs": _maintenance(jobs)}
    from .statedir import initialized
    setup = initialized(root)
    rows = [_mokata_row(root)] + _graph_rows(root, layer) + [_search_row(root)] \
        + _background_rows(root)
    _failing_at_use(rows, jobs)
    _follow_external_build(root, layer, jobs)
    if setup:
        rows += _doctor_rows(root) if full else _wiring_rows(root)
    order = {NOT_SET_UP: -1, BROKEN: 0, MISSING: 1, DEGRADED: 2, STALE: 3, OK: 4}
    rows.sort(key=lambda r: order.get(r["state"], 9))
    if not setup:
        # 0.0.26 P29 — a repo mokata has not set up: ONE top row that says so and what setup
        # lights up; every row below it is a check that still runs (version, graph floor, search).
        rows.insert(0, _setup_row())
    bad = sum(1 for r in rows if r["state"] != OK)
    return {"sample": False, "rows": rows, "problems": bad, "full": full, "jobs": _maintenance(jobs),
            "initialized": setup}


def checking(root: str, *, demo: bool = False, full: bool = True,
             jobs: Any = None) -> Dict[str, Any]:
    """INT-2a — the answer while the checks are still running: the SAME shape as `health()`, with
    every row group in state `checking` and `checking: true`. No spawn, no index read, no graph —
    only a stat for `initialized` — so it answers at once on a box under any load. `problems` is 0
    because nothing has been FOUND yet; `checking` is what says the count is not final."""
    if demo:
        return health(root, None, demo=True, full=full, jobs=jobs)
    from .statedir import initialized
    setup = initialized(root)
    groups = [("mokata", "mokata"), ("graph", "code graph"), ("search", "search by meaning")]
    if setup:                       # doctor/wiring need a manifest — the same rule as `health()`
        groups.append(("doctor", "mokata doctor") if full else ("wiring", "wiring"))
    rows = [_row(k, label, CHECKING, "checking…") for k, label in groups]
    if not setup:
        rows.insert(0, _setup_row())
    return {"sample": False, "rows": rows, "problems": 0, "full": full,
            "jobs": _maintenance(jobs), "initialized": setup, "checking": True}


# ----------------------------------------------------------------------------- the actions
def _rebuild_graph(root: str, layer: Any) -> Dict[str, Any]:
    client = getattr(getattr(layer, "primary", None), "client", None)
    if client is None or not callable(getattr(client, "refresh", None)):
        return {"ok": False, "message": "no adopted code graph to rebuild"}
    t0 = time.time()
    if getattr(client, "can_build_index", False):
        # 0.0.26 O13 — the real tool: under the repo's build lock (never beside a first-use build)
        # with no time limit, and a failure says WHY.
        # 0.0.26 P32 (R39): a rebuild of a BUILT index is written beside it and swapped in only
        # when complete — Cancel (or a failure) leaves the graph in use COMPLETE.
        from ..knowledge import crg_build
        from . import nav
        res = crg_build.build_now(root, full=True, swap=True, before_swap=nav.close_layers)
        if res.busy is not None:
            return {"ok": False, "message": f"not started: {res.busy.line()}"}
        checkpoint()                    # cancelled: the job says so (the old index is intact)
        if res.ok:
            client._index_ok = True
            return {"ok": True, "message": f"code graph index built: {res.detail}"}
        return {"ok": False, "message": f"the code graph's index build failed — {res.error}"}
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


def _stream(argv: List[str], label: str, buf: List[str]) -> int:
    """Run one command, its output streamed into the running job for the page to poll."""
    import subprocess
    from ..quiet_spawn import hidden
    buf.append(f"$ {label}")
    report(note=label)
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                                errors="replace", **hidden())
    except OSError as exc:
        buf.append(f"could not start: {exc}")
        return 127
    with kill_on_cancel(proc):
        for line in proc.stdout or ():
            buf.append(line.rstrip("\n"))
            report(note=buf[-1][-300:], partial={"output": "\n".join(buf)[-8000:]})
    checkpoint()
    return proc.wait()


def _exit_soon(delay: float = 2.0) -> None:
    """Leave the page time to read the job's last state, then stop this server."""
    t = threading.Timer(delay, EXIT.set)
    t.daemon = True
    t.start()


def _update(root: str, confirm: str, *, launch: Optional[Callable[..., Any]] = None,
            stream: Optional[Callable[[List[str], str, List[str]], int]] = None) -> Dict[str, Any]:
    from ..update import finish_argv, launch_detached, plan_update
    plan = plan_update(root, check=False)
    if plan.argv is None:
        return {"ok": False, "message": f"run it yourself: {plan.command}"
                + (f" ({plan.reason})" if plan.reason else "")}
    if (confirm or "").strip() != plan.command:
        # The click carries the command the human was shown. A stale page (the plan changed) or
        # a forged call cannot run something the human did not read.
        return {"ok": False, "message": "the command changed since it was shown — reopen the panel"}
    if plan.deferred:
        (launch or launch_detached)(plan, root, wait_pids=[os.getpid()])
        _exit_soon()
        return {"ok": True, "message": "Updating: this IDE closes now so Windows can replace "
                "mokata (it locks the running files). In a minute, open it again with `mokata ide` "
                "— it shows how the update went."}
    run = stream or _stream
    buf: List[str] = []
    if run(list(plan.argv), plan.command, buf) != 0:
        return {"ok": False, "message": f"update failed — run it yourself: {plan.command}",
                "output": "\n".join(buf)[-8000:]}
    run(finish_argv(root), "mokata update --finish", buf)
    now = next((ln.split()[1] for ln in buf if ln.startswith("mokata ") and len(ln.split()) == 2),
               "the latest")
    return {"ok": True, "message": f"updated: {__version__} → {now}. Restart the IDE to use it "
            "(close it, then `mokata ide`).", "output": "\n".join(buf)[-8000:]}


def start(root: str, layer: Any, action: str, confirm: str, jobs: Any) -> Dict[str, Any]:
    """Start (or join) the action's job. Answers `{"job": <snapshot>}`; the outcome is the job's:
    `done` with the result's message, or `failed` with why (an action that reports `ok: False`
    is a failure, said in its own words)."""
    if action not in ACTIONS:
        from .server import BadRequest
        raise BadRequest(f"action must be one of {', '.join(ACTIONS)}")
    fn: Callable[[], Dict[str, Any]] = {
        "rebuild-graph": lambda: _rebuild_graph(root, layer),
        "rebuild-ast": lambda: _rebuild_ast(root, layer),
        "rebuild-search": lambda: _rebuild_search(root),
        "update": lambda: _update(root, confirm),
    }[action]

    def run() -> Dict[str, Any]:
        out = fn()
        if not out.get("ok"):
            raise ActionFailed(out.get("message") or f"{action} failed")
        report(note=out.get("message") or "")
        return out
    job = jobs.start(ACTION_KINDS[action], {"action": action}, run, label=ACTION_LABELS[action],
                     scope=os.path.basename(os.path.abspath(root)), keep=True)
    return {"job": job.snapshot(), "action": action}


class ActionFailed(MokataError):
    """A health action ran and reported failure (a build that failed, an update that did not
    apply). HARD: the job is FAILED with the action's own message; nothing is retried here."""


def check_update(root: str) -> Dict[str, Any]:
    """The ONE outbound call, on a button: `mokata update`'s plan with the release feed asked —
    latest version, the release notes in between, the line, the follow-up."""
    from ..govern.ledger import AuditLedger
    from ..update import plan_update
    from .statedir import initialized
    led = None
    if initialized(root):            # P29 — a repo mokata has not set up gets no ledger written
        try:
            led = AuditLedger.from_mokata_dir(os.path.join(root, ".mokata"))
        except (OSError, ValueError):
            led = None
    plan = plan_update(root, check=True, ledger=led)
    if not plan.online:
        msg = f"couldn't check — {plan.offline_note}"
    elif plan.newer:
        msg = f"a newer mokata is available: {plan.latest} (you have {plan.current})."
    else:
        msg = f"up to date — mokata {plan.current} is the latest release."
    return {"ok": plan.online, "current": plan.current, "latest": plan.latest,
            "up_to_date": plan.online and not plan.newer, "message": msg,
            "update": plan.to_dict()}


__all__ = ["ACTIONS", "ACTION_KINDS", "EXIT", "health", "start", "check_update"]
