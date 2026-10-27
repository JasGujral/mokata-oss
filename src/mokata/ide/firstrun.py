"""S0 Welcome — what a stranger's repo can do in the IDE right now, what it cannot yet, and what
each missing piece costs (0.0.26 P39 `FIRST-RUN`; PRD F-1, §5 S0, G27/G24, R34/R36).

`mokata ide` on a repo nobody set up must GUIDE, not confuse. One answer lists every capability:

    lexical  word / regex search and the file finder      — always ready (ripgrep or the floor)
    graph    the code graph behind Impact                  — floor now; code-review-graph when built
    meaning  search by meaning                             — needs the extra, the model, an index
    sessions what each Claude Code session wrote           — needs `mokata init` here
    gates    the human gate on durable writes              — needs `mokata init` (+ the wiring)

Every not-ready row carries EITHER the one fix line (doctor's, `govern.doctor.fix_line` — the same
string the search card and the Health row print, R36) when a dependency is missing — the IDE never
installs and never goes online — OR a Build/Set-up action whose consent names what it writes,
where, and an honest estimate of the time and disk it takes, scaled from the repo's file count by
numbers measured on real repos (O13). An action runs only as a P32 job, and only with the token
of the consent the human was shown (`consent()` / `start()`): a stale or forged click is refused.

Read-only. The walk is the explorer's (`nav.walk_files`: the graph's own pruning), on request.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from typing import Any, Dict, List, Optional

from ..errors import MokataError

READY, LIMITED, NOT_READY, BUILDING, FAILED, NEEDS_SETUP = (
    "ready", "limited", "not-ready", "building", "failed", "needs-setup")
ACTIONS = ("build-graph", "build-meaning", "init")
#: action -> the P32 job kind it runs as
ACTION_KINDS = {"build-graph": "index.crg", "build-meaning": "index.meaning", "init": "mokata.init"}

# Measured (0.0.26 O13, 2-CPU box): code-review-graph full build — django 3,010 files 74 s 542 MB;
# kubernetes 20,427 files 593 s 2.6 GB; TypeScript 31,991 files 2,137 s 1.3 GB. Meaning index —
# django 3,047 files 24 s 48 MB; kubernetes 17,955 files 106 s 217 MB; TypeScript 26,801 files
# 134 s 83 MB. Per file: graph 0.03 s (0.07 s TS) and ~150 KB (40 KB TS); meaning 0.007 s, 12 KB.
GRAPH_S_PER_FILE, GRAPH_S_PER_TS_FILE = 0.03, 0.07
GRAPH_B_PER_FILE, GRAPH_B_PER_TS_FILE = 150_000, 40_000
MEANING_S_PER_FILE, MEANING_B_PER_FILE = 0.007, 12_000
COST_BASIS = ("estimated from this repo's file count with rates measured on django (3k files), "
              "kubernetes (20k) and TypeScript (32k) on a 2-CPU machine")
_CODE_EXTS = (".py", ".pyi", ".go", ".ts", ".tsx", ".js", ".jsx", ".mts", ".cts", ".mjs", ".cjs",
              ".java", ".kt", ".rs", ".rb", ".php", ".cs", ".c", ".h", ".cc", ".cpp", ".hpp",
              ".swift", ".scala", ".sol", ".vue", ".dart", ".lua", ".r", ".jl")
_TS_EXTS = (".ts", ".tsx", ".mts", ".cts")


def _fix(capability: str, state: str) -> str:
    from ..govern.doctor import fix_line
    return fix_line(capability, state)


# ----------------------------------------------------------------------------- the repo
def facts(root: str) -> Dict[str, Any]:
    """Files counted (the explorer's walk), bytes, and the languages by file count."""
    from . import nav
    files = nav.walk_files(root)
    exts: Dict[str, int] = {}
    size = 0
    for rel in files:
        ext = os.path.splitext(rel)[1].lower()
        if ext:
            exts[ext] = exts.get(ext, 0) + 1
        try:
            size += os.path.getsize(os.path.join(root, rel))
        except OSError:
            pass
    code = sum(n for e, n in exts.items() if e in _CODE_EXTS)
    ts = sum(n for e, n in exts.items() if e in _TS_EXTS)
    from .search import _SEMANTIC_EXTS
    sem = sum(n for e, n in exts.items() if e in _SEMANTIC_EXTS)
    langs = sorted(((e, n) for e, n in exts.items() if e in _CODE_EXTS), key=lambda x: -x[1])[:6]
    return {"name": os.path.basename(os.path.abspath(root)), "path": os.path.abspath(root),
            "files": len(files), "bytes": size, "code_files": code, "ts_files": ts,
            "meaning_files": sem, "languages": [{"ext": e, "files": n} for e, n in langs]}


def _mb(b: float) -> str:
    return f"{b / 1e9:.1f} GB" if b >= 1e9 else f"{max(1, round(b / 1e6))} MB"


def _dur(s: float) -> str:
    s = max(1, int(round(s)))
    if s < 90:
        return f"{s} s"
    if s < 5400:
        return f"{round(s / 60)} min"
    return f"{s / 3600:.1f} h"


def cost(kind: str, f: Dict[str, Any]) -> Dict[str, Any]:
    """An honest ESTIMATE (it says so) of one build's time and disk for THIS repo."""
    if kind == "graph":
        ts, other = f["ts_files"], max(0, f["code_files"] - f["ts_files"])
        secs = other * GRAPH_S_PER_FILE + ts * GRAPH_S_PER_TS_FILE
        size = other * GRAPH_B_PER_FILE + ts * GRAPH_B_PER_TS_FILE
        n = f["code_files"]
    else:
        n = f["meaning_files"]
        secs, size = n * MEANING_S_PER_FILE, n * MEANING_B_PER_FILE
    return {"seconds": round(secs, 1), "bytes": int(size), "files": n,
            "text": f"about {_dur(secs)} and {_mb(size)} for {n:,} files (estimate)",
            "basis": COST_BASIS}


# ----------------------------------------------------------------------------- each capability
def _row(key: str, label: str, state: str, detail: str, *, fix: str = "",  # noqa: PLR0913
         action: str = "", action_label: str = "", cost_: Optional[Dict[str, Any]] = None,
         enables: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
    return {"key": key, "label": label, "state": state, "detail": detail, "fix": fix,
            "action": action, "action_label": action_label, "cost": cost_,
            "enables": enables or [], "ready": state in (READY, LIMITED)}


def _lexical() -> Dict[str, Any]:
    from .search import engine_name
    import platform
    enables = [{"screen": "search", "label": "Search", "now": True}]
    if engine_name() == "rg":
        return _row("lexical", "Word & regex search, file finder", READY,
                    "ripgrep — works now on every file", enables=enables)
    wheel = platform.machine().lower() in ("x86_64", "amd64", "aarch64", "arm64")
    return _row("lexical", "Word & regex search, file finder", LIMITED if wheel else READY,
                "works now on the pure-Python floor (slower on a big repo)",
                fix=_fix("lexical", "not-installed") if wheel else "", enables=enables)


def _graph(root: str, governed: bool, f: Dict[str, Any], jobs: Any) -> Dict[str, Any]:
    from . import nav
    from ..detect import find_command
    from ..knowledge.crg_build import BUILDING as CB_BUILDING, BUILT, FAILED as CB_FAILED, UNBUILT
    label = "Code graph (Impact)"
    # Impact answers NOW from the floor (P29); a built code graph makes it structural (F-1 step 5:
    # "the badge flips to structural"). Search by meaning / Sessions only work once ready.
    impact = [{"screen": "graph", "label": "Impact (floor)", "now": True}]
    structural = [{"screen": "graph", "label": "Impact (structural)", "now": True}]
    layer = nav._layer(root)
    primary = getattr(layer, "primary", None)
    floor = getattr(getattr(layer, "fallback", None), "name", "") \
        or getattr(layer, "backend_name", "") or "the floor"
    client = getattr(primary, "client", None)
    status = None
    if client is not None and callable(getattr(primary, "index_status", None)):
        try:
            status = primary.index_status()
        except Exception:  # noqa: BLE001 — an unreadable probe is "unknown", said as not ready
            status = None
    c = cost("graph", f)
    floor_line = (f"the '{floor}' floor answers meanwhile (Python: real call edges; other "
                  "languages: name matches)")
    if status is not None:
        if status.state == BUILT:
            return _row("graph", label, READY, "code-review-graph — index built; Impact is "
                        "structural", enables=structural)
        if status.state == CB_BUILDING:
            return _row("graph", label, BUILDING, f"{status.line()} — {floor_line}",
                        enables=impact)
        if status.state == CB_FAILED:
            return _row("graph", label, FAILED, f"{status.line()} — {floor_line}",
                        fix=_fix("graph", "failed"), action="build-graph",
                        action_label="Retry build", cost_=c, enables=impact)
        if status.state == UNBUILT:
            return _row("graph", label, NOT_READY, f"code-review-graph is installed; this repo "
                        f"has no index yet — {floor_line}", fix=_fix("graph", "unbuilt"),
                        action="build-graph", action_label="Build code graph", cost_=c,
                        enables=impact)
    present = bool(find_command("code-review-graph"))
    if present and not governed:
        return _row("graph", label, NEEDS_SETUP, "code-review-graph is installed; mokata wires it "
                    f"once set up here (Set up mokata, then Build) — {floor_line}",
                    fix=_fix("setup", "not-set-up"), action="init",
                    action_label="Set up mokata first", cost_=c, enables=impact)
    if present:
        return _row("graph", label, LIMITED, f"code-review-graph is installed but not in this "
                    f"repo's chain — {floor_line}", fix=_fix("graph", "not-wired"),
                    enables=impact)
    return _row("graph", label, LIMITED, f"Impact works now from the built-in floor; the full "
                f"code graph (every language, faster blast radius) needs code-review-graph — "
                f"{floor_line}", fix=_fix("graph", "not-installed"), enables=impact)


def _meaning(root: str, governed: bool, f: Dict[str, Any], jobs: Any) -> Dict[str, Any]:
    from . import search as SR
    label = "Search by meaning"
    enables = [{"screen": "search", "label": "Search by meaning", "now": True}]
    later = [{"screen": "search", "label": "Search by meaning", "now": False}]
    st = SR.status()
    if not st.get("available"):
        return _row("meaning", label, NOT_READY, st.get("note") or "not available here",
                    fix=st.get("install") or "", enables=later)  # == the search card (R36)
    c = cost("meaning", f)
    running = jobs.find("index.meaning", {"root": os.path.abspath(root)}) if jobs else None
    if running is None and jobs is not None:
        running = next((j for j in _active(jobs) if j["kind"] == "index.meaning"), None)
    if running is not None:
        return _row("meaning", label, BUILDING, "building its index — searches answer from what "
                    "is indexed so far", enables=later)
    bg = SR.build_state(root) or {}
    meta = SR._read_meta(root)
    if bg.get("state") == "failed":
        return _row("meaning", label, FAILED, f"the index build failed — {bg.get('error')}",
                    fix=_fix("meaning", "failed"), action="build-meaning",
                    action_label="Retry build", cost_=c, enables=later)
    if meta.get("complete"):
        return _row("meaning", label, READY, f"index built — {meta.get('rows', 0)} chunks from "
                    f"{len(meta.get('files', {}))} files", enables=enables)
    where = "your profile" if not governed else ".mokata/temp_local/ide/semantic/"
    return _row("meaning", label, NOT_READY,
                ("index partly built — Build resumes it" if meta.get("files")
                 else f"no index yet — Build writes it to {where}"),
                fix=_fix("meaning", "unbuilt"), action="build-meaning",
                action_label="Build meaning index", cost_=c, enables=later)


def _active(jobs: Any) -> List[Dict[str, Any]]:
    try:
        return list(jobs.listing().get("active") or [])
    except Exception:  # noqa: BLE001 — no registry answer is "nothing running"
        return []


def _governance(root: str, initialized: bool, jobs: Any) -> List[Dict[str, Any]]:
    init_running = any(j["kind"] == "mokata.init" for j in _active(jobs)) if jobs else False
    setup_fix = _fix("setup", "not-set-up")
    if not initialized:
        state = BUILDING if init_running else NEEDS_SETUP
        common = {"fix": setup_fix, "action": "" if init_running else "init",
                  "action_label": "Set up mokata…"}
        return [
            _row("sessions", "Sessions", state, "what each Claude Code session writes here, "
                 "snapshots, replay — recorded once mokata is set up",
                 enables=[{"screen": "sessions", "label": "Sessions", "now": False}], **common),
            _row("gates", "Gates & approvals", state, "the human gate on durable writes and the "
                 "run-state gate — wired by mokata init", **common),
        ]
    from ..hook_wiring import GATE_ENFORCING, gate_enforcement_state
    gate = gate_enforcement_state(root)
    rows = [_row("sessions", "Sessions", READY, "mokata records every session here",
                 enables=[{"screen": "sessions", "label": "Sessions", "now": True}])]
    if gate == GATE_ENFORCING:
        rows.append(_row("gates", "Gates & approvals", READY, "the run-state gate is wired"))
    else:
        rows.append(_row("gates", "Gates & approvals", NOT_READY,
                         f"mokata is set up but its gate is not firing ({gate})",
                         fix=setup_fix))
    return rows


def welcome(root: str, jobs: Any = None, *, demo: bool = False) -> Dict[str, Any]:
    """The Welcome answer: the repo, every capability's row, and whether to show it first."""
    if demo:
        return {"sample": True, "repo": {"name": "sample", "path": "/sample/repo", "files": 0,
                                         "bytes": 0, "code_files": 0, "ts_files": 0,
                                         "meaning_files": 0, "languages": []},
                "initialized": True, "capabilities": [], "first_run": False}
    from .statedir import governed as is_governed, initialized as is_init
    gov, init = is_governed(root), is_init(root)
    f = facts(root)
    rows = [_lexical(), _graph(root, gov, f, jobs), _meaning(root, gov, f, jobs)] \
        + _governance(root, init, jobs)
    missing = [r["key"] for r in rows if not r["ready"]]
    return {"sample": False, "repo": f, "initialized": init, "capabilities": rows,
            "missing": missing,
            # launch(first run) → S0: an uninitialised repo, or one whose indexes are missing
            "first_run": (not init) or any(k in ("graph", "meaning") for k in missing)}


# ----------------------------------------------------------------------------- consent + start
def _init_preview(root: str) -> str:
    from ..cli import build_parser
    from ..cli_commands.setup import render_init_preview
    a = build_parser().parse_args(["init", "--preview", "--yes", "--path", root])
    return render_init_preview(a, a.profile)


class Unavailable(MokataError):
    """The action exists but cannot run here now (already set up, a missing dependency, a build
    already running): said, never started."""


def consent(root: str, action: str, jobs: Any = None) -> Dict[str, Any]:
    """What `action` will write, where, and what it costs — and the token the click must carry
    back. ValueError: no such action. `Unavailable`: it cannot run here now (with why)."""
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {', '.join(ACTIONS)}")
    from .statedir import initialized
    f = facts(root)
    if action == "init":
        if initialized(root):
            raise Unavailable("mokata is already set up in this repo")
        text = _init_preview(root)
        writes = [".mokata/ (manifest, constitution, audit ledger)",
                  ".claude/ (commands, skills, hooks, permissions)", ".mcp.json",
                  ".gitignore / .worktreeinclude entries"]
        title = "Set up mokata in this repo (mokata init --yes)"
        est = {"text": "a few seconds; a few hundred KB", "seconds": 5, "bytes": 500_000,
               "basis": "measured: 3 s on a small repo"}
    elif action == "build-graph":
        row = _graph(root, True, f, jobs)
        if row["action"] != "build-graph":
            raise Unavailable(f"the code graph cannot be built here now: {row['detail']}")
        est = cost("graph", f)
        writes = [".code-review-graph/ in this repo (it ignores itself in git)"]
        title = "Build the code graph (code-review-graph)"
        text = (f"code-review-graph parses {est['files']:,} source files and writes its index to "
                f"{os.path.join(root, '.code-review-graph')}{os.sep} — {est['text']}. It runs as a "
                "job with progress and Cancel; a cancelled first build leaves no partial index, "
                "and Impact answers from the floor meanwhile. Same as `mokata graph build`.")
    else:
        row = _meaning(root, True, f, jobs)
        if row["action"] != "build-meaning":
            raise Unavailable(f"the meaning index cannot be built here now: {row['detail']}")
        from .search import index_dir
        est = cost("meaning", f)
        writes = [index_dir(root)]
        title = "Build the meaning index"
        text = (f"the local model embeds {est['files']:,} source files (offline — nothing leaves "
                f"this machine) into {index_dir(root)}{os.sep} — {est['text']}. It runs as a job "
                "with progress and Cancel; Cancel keeps what is embedded and the next build "
                "resumes it.")
    token = hashlib.sha256(json.dumps([action, os.path.abspath(root), text],
                                      sort_keys=True).encode("utf-8")).hexdigest()[:24]
    return {"action": action, "title": title, "text": text, "writes": writes, "cost": est,
            "token": token, "kind": ACTION_KINDS[action]}


class ConsentStale(MokataError):
    """The click carried a token for a plan that is not the one in force now (it changed since
    it was shown, or it was never shown): nothing runs."""


def _init_job(root: str) -> Dict[str, Any]:
    """`mokata init --yes` in a subprocess (its output streams into the job), then the IDE's own
    server record moves into the now-governed repo, where the CLI looks for it."""
    from .health import ActionFailed, _stream
    argv = [sys.executable, "-m", "mokata", "init", "--yes", "--path", root]
    buf: List[str] = []
    if _stream(argv, "mokata init --yes", buf) != 0:
        raise ActionFailed("mokata init failed — " + " | ".join(buf[-6:])[-400:])
    _move_record(root)
    return {"ok": True, "message": "mokata is set up here — sessions, gates and runs are on",
            "output": "\n".join(buf)[-8000:]}


def _move_record(root: str) -> None:
    from .server import _write_record
    from .statedir import user_repo_dir
    old = os.path.join(user_repo_dir(root), "ide", "server.json")
    try:
        with open(old, encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        return
    if not isinstance(rec, dict) or rec.get("pid") != os.getpid():
        return
    try:
        _write_record(root, rec)
        os.remove(old)
    except OSError:
        pass                     # the old record is still found by the user-profile rule


def start(root: str, action: str, confirm: str, jobs: Any) -> Dict[str, Any]:
    """Run `action` as its job — only when `confirm` is the token of the consent in force NOW."""
    try:
        want = consent(root, action, jobs)
    except Unavailable as exc:
        raise ConsentStale(str(exc)) from exc
    if not confirm or confirm != want["token"]:
        raise ConsentStale("the plan changed since it was shown (or was never shown) — reopen "
                           "it and confirm again")
    if action == "init":
        job = jobs.start("mokata.init", {"action": "init"}, lambda: _init_job(root),
                         label="mokata init", scope=os.path.basename(os.path.abspath(root)),
                         keep=True)
        return {"id": job.id, "job": job.snapshot()}
    if action == "build-meaning":
        # NOT Health's "Force rebuild" (that drops the index first): a first build RESUMES what a
        # cancelled one embedded — the background build the meaning search itself uses (O13/P32).
        from . import search as SR
        st = SR._start_background(root, SR._load_embedder())
        job = jobs.get(st.get("job_id") or "")
        if job is None:
            raise ConsentStale("the meaning index build did not start — reopen Welcome")
        return {"id": job.id, "job": job.snapshot()}
    from . import health, nav
    snap = health.start(root, nav._layer(root), "rebuild-graph", "", jobs)["job"]
    return {"id": snap["id"], "job": snap}


__all__ = ["ACTIONS", "ACTION_KINDS", "ConsentStale", "Unavailable", "consent", "cost", "facts", "start",
           "welcome"]
