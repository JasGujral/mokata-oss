"""The `mokata ide` server — localhost-only, paired, read-only (0.0.22 E2; ADR doc 85 §1b).

GUARDS, every one tested (doc 109 §6 — designed from px0's failures):
  * LOOPBACK ONLY. It binds 127.0.0.1; there is no option to bind anything else.
  * PAIRED. A per-launch random token. `/pair?t=<token>` swaps it for an HttpOnly, SameSite=Strict
    cookie; every other route refuses without that cookie (or, for the launcher's own calls from
    the terminal, the `X-Mokata-Token` header). Compared in constant time.
  * HOST CHECKED on every request (DNS rebinding): only `127.0.0.1:<port>` / `localhost:<port>`.
  * ORIGIN CHECKED on every non-GET: a page on another origin cannot drive it.
  * CONTAINED. Every path it reads goes through `snapshots.rel_path` (realpath inside the repo).
  * READ-ONLY. No route writes the repo (the semantic index lives under `.mokata/`). The network
    calls are the ones the user asks for: a PR fetch, and (0.0.23 C2) the one-time embeddings-model
    fetch behind the Meaning column's fetch button — meaning search otherwise loads its model offline.

LIFETIME. Started from a terminal it lives as long as its command (`--foreground`). Started from a
Claude session (`/mokata:review-window`) it is a child that exits after `IDLE_SECS` with no page attached —
never a daemon that outlives its use. Its record (port, pid, token) is `temp_local/ide/server.json`,
removed on exit, so a second `/mokata:review-window` finds it and RE-FOCUSES it instead of starting another.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hmac
import json
import os
import queue
import re
import secrets
import socket
import sys
import threading
import time
import traceback
from typing import Any, Callable, Dict, List, Optional

from ..errors import MokataError
from ..knowledge.query import BUDGET_DEFAULT, BUDGET_FULL
from .bus import HOOK_HEADER
from .gate_door import GATE_PATH as _GATE_PATH

IDLE_SECS = 30 * 60
POLL_SECS = 1.0            # SSE keep-alive tick: focus / idle bookkeeping (events arrive pushed)
COOKIE = "mokata_ide"
TOKEN_HEADER = "x-mokata-token"
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
STATIC_FILES = ("index.html", "registry.js", "app.js", "combo.js", "nav.js", "search.js", "graph.js",
                "plan.js", "terminal.js", "bridge.js", "sessions.js", "health.js", "boot.js", "app.css",
                "mokata-wordmark-dark.svg", "mokata-wordmark-light.svg", "mokata-icon.svg")
# 0.0.23 C3 — the vendored graph libraries (MIT; pins + sha256 in static/vendor/VERSIONS.json).
VENDOR_FILES = ("cytoscape.min.js", "layout-base.js", "cose-base.js", "cytoscape-fcose.js",
                "cytoscape-dagre.min.js")
# 0.0.26 P0 — a FEATURE's frontend: any `static/features/<name>.js|.css` is served and loaded by the
# page (`index` writes its <script>/<link> tags at the markers below) — no edit to index.html.
FEATURES_DIR = os.path.join(STATIC_DIR, "features")
SCRIPTS_MARK = "<!-- mokata:feature-scripts -->"
STYLES_MARK = "<!-- mokata:feature-styles -->"
_FEATURE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}\.(js|css)$")
_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
          ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}

INSTALL_LINE = ('mokata ide needs its web dependencies: pip install "mokata[ide]" '
                "(starlette, uvicorn, sse-starlette — normally already present with mokata's MCP SDK)")


class BadRequest(MokataError, ValueError):
    """A request the page should not have made (a missing or unknown parameter) — a HARD refusal
    answered 400 (D5); nothing is attempted."""


def refused(root: str, exc: MokataError) -> Any:
    """0.0.26 P29 — a failure mokata DEFINED (D5: a `MokataError` carries its own remedy in its
    message) is an answer about the repo's state, not a server fault: 409 with the error's class
    and text. A repo mokata has not set up says so as a state, with the one command."""
    from starlette.responses import JSONResponse
    from ..config import ConfigError
    from .statedir import NOT_INITIALIZED, SETUP_COMMAND, initialized
    body: Dict[str, Any] = {"error": type(exc).__name__, "note": str(exc)[:300]}
    if isinstance(exc, ConfigError) and not initialized(root):
        body.update(state=NOT_INITIALIZED, command=SETUP_COMMAND)
    return JSONResponse(body, status_code=409)


def crashed(where: str, exc: BaseException) -> Any:
    """Anything ELSE is a bug: still a 500, and its traceback goes to stderr (never swallowed) so
    the terminal running `mokata ide` shows where it broke. (stderr, not `logging`: the tree has
    no logging setup — test_a39 holds that.)"""
    from starlette.responses import JSONResponse
    try:
        sys.stderr.write(f"mokata ide: {where} failed — this is a bug, please report it:\n"
                         + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    except (OSError, ValueError):                              # pragma: no cover — no stderr
        pass
    return JSONResponse({"error": type(exc).__name__, "note": str(exc)[:300]}, status_code=500)


# ------------------------------------------------------------------------------ jobs
#
# 0.0.26 P32 `IDE-JOBS` (Jas 2026-10-21/22, overruling U10's 20 s cap). The heavy questions —
# graph views, impact, `query`, word and meaning search, the tree walk, health — used to run on a
# lane with a DEADLINE: past 20 s the page got a 503 while the work kept burning CPU for nobody
# (audit A #1, #3). Each is now a JOB (`ide/jobs.py`): the route waits a short sync window for it
# and answers the result, or `202 {job, state, partial}`; the page follows the job's `job` events
# on the event stream (progress, partial answers) and re-asks when it is done — the kept answer
# comes back at once. Cancel really stops the work (checkpoints in the walks, subprocess kill);
# a query job nobody watches any more is cancelled; index builds keep running. No deadline.
CLIENT_HEADER = "x-mokata-client"   # the page's id: its SSE stream is what keeps its jobs wanted
SHUTDOWN_GRACE_SECS = 2.0       # how long a SIGTERM/SIGINT waits for open requests before closing
SWITCH_INTERVAL_SECS = 0.001   # the GIL hand-off interval while serving (see `serve`)
MAX_SYNC_MS = 2000              # the longest sync window a page may ask for (`?sync_ms=`)


def record_path(root: str) -> str:
    from .statedir import ide_dir
    return os.path.join(ide_dir(root), "server.json")


def read_record(root: str) -> Optional[Dict[str, Any]]:
    try:
        with open(record_path(root), encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _write_record(root: str, data: Dict[str, Any]) -> None:
    from .statedir import note_repo
    path = record_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    note_repo(root)                  # P29 — a user-level state dir names the repo it belongs to
    tmp = path + f".tmp-{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def _remove_record(root: str, pid: int) -> None:
    rec = read_record(root)
    if rec and rec.get("pid") == pid:
        try:
            os.remove(record_path(root))
        except OSError:
            pass


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


TERMINAL_NEEDS_TTY = ("the embedded terminal is enabled only when you start `mokata ide` from your own "
                      "terminal (a TTY) — a window opened from inside a session "
                      "(/mokata:review-window) or without a TTY cannot start processes "
                      "(doc 109 §6: the same bar as `mokata approve`)")
TERMINAL_NOT_SAMPLE = "no terminal on SAMPLE data (`mokata ide --demo`)"


class AppState:
    def __init__(self, root: str, token: str, port: int, focus: Optional[str], demo: bool,
                 terminal: bool = False, approve_here: bool = False):
        from .source import DemoSource, RealSource
        self.root = root
        self.token = token
        self.port = port
        self.focus = focus
        self.demo = demo
        self.source = DemoSource() if demo else RealSource(root, focus_of=lambda: self.focus)
        if demo and not focus:
            # The SAMPLE opens on its first session, and the server — not the page — owns focus,
            # so the live channel and the state read can never disagree about it.
            self.focus = self.source.state(None)["focus"]
        self.last_seen = time.time()
        self.clients = 0
        self.focus_version = 0
        # ID.S3 — the event bus (pushed events + the stat-gated tails), and the hook token from the
        # bridge record: it authorises `POST /api/bus` and nothing else.
        from .bus import EventBus, read_bridge
        self.bus = EventBus()
        bridge = None if demo else read_bridge(root)
        self.hook_token = bridge["token"] if bridge else ""
        self.watcher: Optional[Any] = None
        # ID.S3 (Lane ID2) — the embedded terminal (enabled only for a TTY-launched, real-repo
        # window) and the joined session (focus + follow + the default context target).
        from .terminal import TerminalHost, available
        gate = ((False, TERMINAL_NOT_SAMPLE) if demo else
                (False, TERMINAL_NEEDS_TTY) if not terminal else available())
        self.terminals = TerminalHost(root, enabled=gate)
        self.joined: Optional[str] = None
        # 0.0.26 P38 — Approve/Reject in the window needs a code printed to THIS process's own
        # terminal (`ide/approve_codes.py`): offered only to `mokata ide --foreground` at a human
        # TTY, re-checked here (fail closed). The codes live in memory only.
        from .approve_codes import NO_TTY, SAMPLE, CodeBook, human_terminal
        self.approve_tty = ((False, SAMPLE) if demo else human_terminal() if approve_here
                            else (False, NO_TTY))
        self.codes = CodeBook()
        # 0.0.26 P32 — every long question is a job of this registry (lanes of its own, never the
        # pool the light routes answer from); background index builds started deep in a module
        # (⌘P symbols, the meaning index) become its jobs too (`jobs.install`).
        from . import jobs as J
        self.jobs = J.JobRegistry()
        J.install(self.jobs)
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    def attach_loop(self) -> None:
        """Bind job events to the event bus, from the server's loop (first request / stream)."""
        if self.loop is not None:
            return
        loop = self.loop = asyncio.get_running_loop()
        bus = self.bus

        def publish(evt: Dict[str, Any]) -> None:
            loop.call_soon_threadsafe(lambda: asyncio.ensure_future(bus.publish(evt)))
        self.jobs.attach(publish)

    def close(self, wait: float = 0.0) -> None:
        from . import jobs as J
        self.jobs.close(wait)
        if J.installed() is self.jobs:
            J.install(None)

    def hosts(self):
        return {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

    def origins(self):
        return {f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"}


def _fingerprint(root: str) -> str:
    """A cheap "did anything change" token: the snapshot timeline and git's index/HEAD mtimes."""
    from .snapshots import store_dir
    parts = []
    for p in (os.path.join(store_dir(root), "timeline.jsonl"), os.path.join(root, ".git", "index"),
              os.path.join(root, ".git", "HEAD")):
        try:
            st = os.stat(p)
            parts.append(f"{st.st_size}:{st.st_mtime_ns}")
        except OSError:
            parts.append("-")
    return "|".join(parts)


def _approvals_fp(root: str) -> str:
    """P38 — the proposal records' names, sizes and mtimes: changes when one is proposed, decided or
    used. Bounded by approval.MAX_PROPOSALS (plus whatever else sits in the state dir: names only)."""
    from ..approval import PROPOSAL_PREFIX
    from ..tdd_state import state_dir
    try:
        with os.scandir(state_dir(root)) as it:
            parts = sorted(f"{e.name}:{e.stat().st_size}:{e.stat().st_mtime_ns}" for e in it
                           if e.name.startswith(PROPOSAL_PREFIX))
    except OSError:
        return "-"
    return "|".join(parts)


def feature_assets() -> Dict[str, List[str]]:
    """The feature scripts and styles under `static/features/` (sorted: a stable load order). Read
    once per app build — never at idle."""
    try:
        names = sorted(n for n in os.listdir(FEATURES_DIR) if _FEATURE_NAME.match(n))
    except FileNotFoundError:
        names = []
    return {"scripts": [n for n in names if n.endswith(".js")],
            "styles": [n for n in names if n.endswith(".css")]}


def render_index(html: str, assets: Dict[str, List[str]]) -> str:
    """index.html with the feature tags written at its two markers (after the core views, before
    boot.js — so a feature registers before boot wires and refreshes)."""
    if SCRIPTS_MARK not in html or STYLES_MARK not in html:
        raise MokataError("index.html lost a feature marker — feature modules would not load")
    tags = "\n".join(f'<script src="/static/features/{n}"></script>' for n in assets["scripts"])
    links = "\n".join(f'<link rel="stylesheet" href="/static/features/{n}">'
                      for n in assets["styles"])
    return html.replace(SCRIPTS_MARK, tags).replace(STYLES_MARK, links)


def build_app(state: AppState, features: Optional[List[Any]] = None):
    """The Starlette app. Imported lazily so the core never needs the `[ide]` deps. `features`
    defaults to every module under `ide/features/` (a test passes its own)."""
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import (HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse,
                                     Response)
    from starlette.routing import Route
    from sse_starlette.sse import EventSourceResponse

    def _authed(request: Request) -> bool:
        got = request.cookies.get(COOKIE) or request.headers.get(TOKEN_HEADER) or ""
        return hmac.compare_digest(got.encode(), state.token.encode())

    def _hook_authed(request: Request) -> bool:
        """ID.S3 — an observe-only hook: its own token, and NO Origin (a browser always sends
        one on a POST, so no page — ours or another site's — can use this door)."""
        got = request.headers.get(HOOK_HEADER, "")
        return bool(state.hook_token and got and request.headers.get("origin") is None
                    and hmac.compare_digest(got.encode(), state.hook_token.encode()))

    def guard(handler, *, auth: bool = True, hook: bool = False):
        async def wrapped(request: Request):
            if request.headers.get("host", "") not in state.hosts():
                return PlainTextResponse("refused: unexpected Host (mokata ide is localhost-only)",
                                         status_code=403)
            if hook and request.method == "POST" and _hook_authed(request):
                state.last_seen = time.time()
                return await handler(request)
            if request.method != "GET":
                origin = request.headers.get("origin")
                if origin is not None and origin not in state.origins():
                    return PlainTextResponse("refused: cross-origin request", status_code=403)
                if origin is None and not hmac.compare_digest(
                        request.headers.get(TOKEN_HEADER, "").encode(), state.token.encode()):
                    return PlainTextResponse("refused: no Origin", status_code=403)
            if auth and not _authed(request):
                return PlainTextResponse("not paired — open the link `mokata ide` printed",
                                         status_code=401)
            state.last_seen = time.time()
            return await handler(request)
        return wrapped

    async def pair(request: Request):
        t = request.query_params.get("t", "")
        if not hmac.compare_digest(t.encode(), state.token.encode()):
            return PlainTextResponse("pairing link not valid for this server", status_code=401)
        focus = request.query_params.get("focus")
        if focus:
            state.focus, state.focus_version = focus, state.focus_version + 1
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(COOKIE, state.token, httponly=True, samesite="strict", path="/")
        return resp

    assets = feature_assets()

    async def index(request: Request):
        with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8", newline="") as fh:
            html = fh.read()
        return _served(render_index(html, assets).encode("utf-8"), "index.html")

    async def feature_static(request: Request):
        name = request.path_params["name"]
        if name not in assets["scripts"] and name not in assets["styles"]:
            return PlainTextResponse("not found", status_code=404)
        with open(os.path.join(FEATURES_DIR, name), "rb") as fh:
            return _served(fh.read(), name)

    async def static(request: Request):
        name = request.path_params["name"]
        if name not in STATIC_FILES:
            return PlainTextResponse("not found", status_code=404)
        return _static(name)

    async def vendor(request: Request):
        name = request.path_params["name"]
        if name not in VENDOR_FILES:
            return PlainTextResponse("not found", status_code=404)
        return _static("vendor/" + name)

    def _static(name: str) -> Response:
        with open(os.path.join(STATIC_DIR, *name.split("/")), "rb") as fh:
            return _served(fh.read(), name)

    def _served(body: bytes, name: str) -> Response:
        return Response(body, media_type=_TYPES.get(os.path.splitext(name)[1], "text/plain"),
                        headers={"Cache-Control": "no-store",
                                 "Content-Security-Policy": "default-src 'self'; "
                                 "img-src 'self' data:; style-src 'self'; script-src 'self'; "
                                 "frame-ancestors 'none'",
                                 "X-Frame-Options": "DENY"})

    def _json(fn, job: Optional[str] = None,
              job_label: Optional[Callable[[Request], str]] = None):
        """A JSON read route. LIGHT by default (asyncio's pool). A `job` route's handler VALIDATES
        the request (a bad one is a 400 at once) and returns the work, which runs as a job of that
        kind (see the section note): the result, or 202 with the job's handle."""
        async def handler(request: Request):
            from .nav import PathRefused
            try:
                if job is None:
                    return JSONResponse(await asyncio.to_thread(fn, request))
                work = fn(request)
                done, out = await run_job(request, job, _args(request), work,
                                          job_label(request) if job_label else job)
                return JSONResponse(out) if done else out
            except (PathRefused, BadRequest) as exc:
                return JSONResponse({"error": type(exc).__name__, "note": str(exc)},
                                    status_code=400)
            except MokataError as exc:
                return refused(state.root, exc)
            except Exception as exc:  # noqa: BLE001 — a bug: 500, traceback logged (`crashed`)
                return crashed(request.url.path, exc)
        return handler

    def _args(request: Request) -> Dict[str, Any]:
        """A job's question: the route's parameters (identical questions are one job)."""
        return {k: v for k, v in sorted(request.query_params.multi_items())
                if k not in ("sync_ms", "job")}

    async def run_job(request: Request, kind: str, args: Dict[str, Any],
                      work: Callable[[], Any], label: str, scope: str = "",
                      sync_ms: Any = None, job_id: Any = None, as_handle: bool = False) -> Any:
        """(True, result) when the job is done inside the sync window, else (False, a 202
        Response with the job's handle and any partial answer). A failed job re-raises its error
        here, so the route answers it as it always did (400 / 409 / 500). `sync_ms` (the query
        parameter, or a POST body's field) sets the window; 0 = answer the handle at once. `job`
        (likewise) names the job a page waited for: its finished answer comes back at once."""
        from . import jobs as J
        state.attach_loop()
        client = request.headers.get(CLIENT_HEADER) or None
        question = {"route": request.url.path, **args}
        want = request.query_params.get("job") if job_id is None else job_id
        kept = state.jobs.answer(want, kind, question) if isinstance(want, str) and want else None
        if kept is not None:                 # the page that waited for it fetches its answer
            return True, kept.result
        j = state.jobs.start(kind, question, work, label=label,
                             scope=scope or os.path.basename(state.root), client=client)
        raw = request.query_params.get("sync_ms", "") if sync_ms is None else sync_ms
        try:
            wait = max(0, min(MAX_SYNC_MS, int(raw))) / 1000
        except (TypeError, ValueError):
            wait = J.SYNC_WINDOW_S
        if wait == 0:
            return False, (handle(j) if as_handle else JSONResponse(handle(j), status_code=202))
        if not j.finished.is_set() and wait > 0:
            loop = asyncio.get_running_loop()
            woke = loop.create_future()
            def _wake() -> None:
                try:
                    loop.call_soon_threadsafe(lambda: woke.done() or woke.set_result(None))
                except RuntimeError:
                    pass        # INT-2a: the waiter's loop already closed (it answered and left)
            state.jobs.on_finish(j, _wake)
            await asyncio.wait({woke}, timeout=wait)
        if j.state == J.DONE:
            return True, j.result
        if j.state == J.FAILED and j.exc is not None:
            raise j.exc
        return False, (handle(j) if as_handle else JSONResponse(handle(j), status_code=202))

    def handle(j: Any) -> Dict[str, Any]:
        """What a page needs to follow a job it asked for."""
        snap = j.snapshot()
        return {"job": j.id, "kind": j.kind, "label": j.label, "state": j.state,
                "done": snap["done"], "total": snap["total"], "unit": snap["unit"],
                "note": snap["note"], "error": snap["error"], "elapsed": snap["elapsed"],
                "partial_ref": snap["partial_ref"], "partial": j.partial}

    def impact(request: Request) -> Dict[str, Any]:
        """0.0.26 O7 — `budget=full` (one named symbol) re-walks a blast radius the default bound
        cut, under its own hard ceiling; anything else about `budget` is refused up front."""
        budget = q(request, "budget") or BUDGET_DEFAULT
        names = [n for n in (q(request, "symbols") or "").split(",")[:40] if n.strip()] or None
        if budget not in (BUDGET_DEFAULT, BUDGET_FULL):
            raise BadRequest(f"budget must be '{BUDGET_DEFAULT}' or '{BUDGET_FULL}'")
        if budget == BUDGET_FULL and (not names or len(names) != 1):
            raise BadRequest("the full blast-radius budget is asked for one named symbol at a time")
        path, lines = q(request, "path"), q(request, "lines")
        return lambda: state.source.impact(path, lines, names, budget)

    def _impact_label(request: Request) -> str:
        if q(request, "budget") == BUDGET_FULL:
            return f"full blast radius · {q(request, 'symbols')[:80]}"
        return f"impact · {q(request, 'path')[:120]}"

    def _graph_label(request: Request) -> str:
        to = f" → {q(request, 'to')[:60]}" if q(request, "view") == "path" else ""
        pg = q(request, "page")
        more = f" · page {pg[:6]}" if pg and pg != "1" else ""
        return f"graph {q(request, 'view') or 'ripple'} · {q(request, 'symbol')[:80]}{to}{more}"

    q = lambda r, k: r.query_params.get(k, "")  # noqa: E731

    async def focus(request: Request):
        body = await request.json()
        sid = body.get("session") if isinstance(body, dict) else None
        state.focus = sid if isinstance(sid, str) and sid else None
        state.focus_version += 1
        await state.bus.publish({"kind": "focus", "focus": state.focus})
        return JSONResponse({"focus": state.focus})

    async def bus_post(request: Request):
        """ID.S3 — events IN. A neutral event or a Claude Code hook payload (the adapter maps it).
        Always answers 2xx with an EMPTY body to a well-formed post — for a Claude Code http hook
        that is "success, no decision" — so an observe-only hook can never steer the session."""
        from .bus import normalise, read_body
        raw = await read_body(request)
        if raw is None:
            return Response(status_code=204)      # oversized: dropped (the timeline tail has it)
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else None
        except (ValueError, UnicodeDecodeError):
            return PlainTextResponse("not JSON", status_code=400)
        evt = await asyncio.to_thread(normalise, payload, state.root) if not state.demo else None
        if evt:
            if evt.get("path"):
                from .nav import note_change
                note_change(state.root)      # lane S: ⌘P re-reads what moved, at its next ask
            await state.bus.publish(evt)
        return Response(status_code=204)

    # 0.0.25 ID3 — the session browser / needs-you (the bus ring is copied ON the loop, then read
    # in a thread), and `ide_open`'s door: open a file:line and/or focus a session in the page.
    async def sessions_browser(request: Request):
        ring = state.bus.recent()
        return await _json(lambda r: state.source.browser(ring))(request)

    def timelapse(request: Request) -> Dict[str, Any]:
        sid = q(request, "session")
        if not sid:
            raise BadRequest("a session is required")
        return state.source.timelapse(sid[:200])

    def treemap(request: Request) -> Dict[str, Any]:
        size = q(request, "size") or "loc"
        if size not in ("loc", "change"):
            raise BadRequest("size must be loc or change")
        return state.source.treemap(q(request, "session")[:200] or None, size)

    async def open_in_page(request: Request):
        """ID3 `ide_open` — a UI round trip, never a write: the path is contained here (again — the
        MCP side contained it too), then published as an `open` event every page follows. Answers
        how many pages were attached, so the caller can say "nobody is looking" honestly."""
        from .nav import PathRefused, contained_rel
        b = await _body(request)
        path, sid = b.get("path"), b.get("session")
        try:
            line = max(1, min(10_000_000, int(b.get("line") or 1)))
        except (TypeError, ValueError):
            line = 1
        out: Dict[str, Any] = {"pages": state.clients, "demo": state.demo}
        if isinstance(sid, str) and sid:
            state.focus, state.focus_version = sid[:200], state.focus_version + 1
            await state.bus.publish({"kind": "focus", "focus": state.focus})
            out["focus"] = state.focus
        if isinstance(path, str) and path:
            try:
                rel = await asyncio.to_thread(contained_rel, state.root, path)
            except PathRefused as exc:
                return JSONResponse({"error": "PathRefused", "note": str(exc)}, status_code=400)
            await state.bus.publish({"kind": "open", "path": rel, "line": line,
                                     "session": state.focus or ""})
            out.update(path=rel, line=line)
        if "path" not in out and "focus" not in out:
            return JSONResponse({"error": "BadRequest", "note": "give a path and/or a session"},
                                status_code=400)
        return JSONResponse(out)

    async def gate_post(request: Request):
        """U8 — a PreToolUse http hook's verdict (`ide/gate_door.py`; opt-in `--http-gates`)."""
        from .bus import read_body
        from .gate_door import decide
        raw = await read_body(request)
        status, body = (400, {"error": "payload too large"}) if raw is None else \
            await asyncio.to_thread(decide, raw)
        return Response(status_code=status) if body is None else JSONResponse(body,
                                                                              status_code=status)

    async def pr(request: Request):
        body = await request.json()
        url = body.get("url", "") if isinstance(body, dict) else ""
        return JSONResponse(await asyncio.to_thread(state.source.pr, url))

    async def quick(request: Request):
        body = await request.json()
        body = body if isinstance(body, dict) else {}
        q_ = body.get("q") if isinstance(body.get("q"), str) else ""
        changed = body.get("changed") if isinstance(body.get("changed"), list) else []
        try:
            return JSONResponse(await asyncio.to_thread(state.source.quick, q_[:200],
                                                        [c for c in changed[:5000]
                                                         if isinstance(c, str)]))
        except MokataError as exc:
            return refused(state.root, exc)
        except Exception as exc:  # noqa: BLE001 — a bug: 500, traceback logged
            return crashed(request.url.path, exc)

    async def query(request: Request):
        """The structural answer as the EXACT bytes `mokata query --json` prints (C1)."""
        from ..knowledge.query import QUERY_KINDS
        kind, target = q(request, "kind"), q(request, "target")
        if kind not in QUERY_KINDS or not target:
            return JSONResponse({"error": "BadQuery",
                                 "note": f"kind must be one of {list(QUERY_KINDS)}, with a target"},
                                status_code=400)
        try:
            depth = max(1, min(6, int(q(request, "depth") or 2)))
        except ValueError:
            depth = 2
        tgt = target[:300]
        try:
            done, body = await run_job(request, "graph.query", _args(request),
                                       lambda: state.source.query(kind, tgt, depth),
                                       f"{kind} · {tgt[:80]}")
        except MokataError as exc:   # e.g. a broken manifest — the repo's state, said as such
            return refused(state.root, exc)
        except Exception as exc:  # noqa: BLE001 — a bug: 500 + logged (was a 409)
            return crashed(request.url.path, exc)
        if not done:
            return body
        return Response(body.encode("utf-8"), media_type="application/json")

    async def _body(request: Request) -> Dict[str, Any]:
        try:
            b = await request.json()
        except ValueError:
            return {}
        return b if isinstance(b, dict) else {}

    def _only(b: Dict[str, Any]):
        only = b.get("only")
        return [p for p in only[:5000] if isinstance(p, str)] if isinstance(only, list) else None

    # 0.0.25 — the health panel: highlight missing / degraded / out of sync; force a re-sync of
    # what is derived; update mokata on the human's click (`update.py`'s plan and executor).
    def _health_layer():
        from . import nav
        return None if state.demo else nav._layer(state.root)

    def health_rows(request: Request) -> Callable[[], Dict[str, Any]]:
        from . import health
        full = q(request, "full") == "1"
        return lambda: health.health(state.root, _health_layer(), demo=state.demo, full=full,
                                     jobs=state.jobs)

    async def health_route(request: Request):
        """INT-2a — `/api/health` ALWAYS answers 200 with what is known now. The checks run as the
        `health.check` job (same kind, same join-the-running-one rule as before); when they finish
        inside the sync window the answer is the full rows, and when they do not (a loaded box, a
        cold graph, `sync_ms=0`) the answer is the CHECKING shape — every row group `checking…`,
        the counts honest about being unknown — with the job's handle in `pending`. The page
        follows `pending` and re-asks with `job=<id>` for the finished rows. Never a bare 202 job
        handle: a health probe that sometimes answers a different shape is the flake P33/P35/P38/
        P39 kept hitting, and a health view must never be blank because the box is busy."""
        from . import health
        try:
            work = health_rows(request)
            full = q(request, "full") == "1"
            done, out = await run_job(request, "health.check", _args(request), work,
                                      "health checks" + (" (full)" if full else ""),
                                      as_handle=True)
            if done:
                return JSONResponse(out)
            body = health.checking(state.root, demo=state.demo, full=full, jobs=state.jobs)
            body["pending"] = out
            return JSONResponse(body)
        except MokataError as exc:
            return refused(state.root, exc)
        except Exception as exc:  # noqa: BLE001 — a bug: 500, traceback logged (`crashed`)
            return crashed(request.url.path, exc)

    async def health_action(request: Request):
        from . import health
        if state.demo:
            return JSONResponse({"error": "BadRequest", "note": "the SAMPLE has nothing to "
                                 "rebuild or update"}, status_code=400)
        b = await _body(request)
        action = b.get("action") if isinstance(b.get("action"), str) else ""
        confirm = b.get("confirm") if isinstance(b.get("confirm"), str) else ""
        try:
            if action not in health.ACTIONS:    # refuse before building anything
                raise BadRequest(f"action must be one of {', '.join(health.ACTIONS)}")
            layer = await asyncio.to_thread(_health_layer)
            state.attach_loop()
            return JSONResponse(health.start(state.root, layer, action, confirm[:2000],
                                             state.jobs))
        except BadRequest as exc:
            return JSONResponse({"error": "BadRequest", "note": str(exc)}, status_code=400)
        except MokataError as exc:
            return refused(state.root, exc)

    async def health_check_update(_request: Request):
        """The ONE outbound call, on the human's click (opt-in, ledger-accounted)."""
        from . import health
        if state.demo:
            return JSONResponse({"ok": False, "message": "the SAMPLE does not check for updates"})
        return JSONResponse(await asyncio.to_thread(health.check_update, state.root))

    async def search_text(request: Request):
        from .search import BadPattern, _compile
        b = await _body(request)
        q_ = (b.get("q") if isinstance(b.get("q"), str) else "")[:500]
        mode = "regex" if b.get("mode") == "regex" else "word"
        case, only = bool(b.get("case")), _only(b)
        offset = b.get("offset", 0)
        try:
            if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
                raise BadRequest("offset must be a whole number ≥ 0 (a page's next_offset)")
            if q_.strip():
                _compile(q_, mode, case)             # a bad pattern is a 400 now, not a job
            done, out = await run_job(request, "search.text",
                                      {"q": q_, "mode": mode, "case": case, "only": only,
                                       "offset": offset},
                                      lambda: state.source.search(q_, mode, case, only, offset),
                                      f"search · {q_[:80]}", sync_ms=b.get("sync_ms"),
                                      job_id=b.get("job"))
            return JSONResponse(out) if done else out
        except BadPattern as exc:
            return JSONResponse({"error": "BadPattern", "note": str(exc)}, status_code=400)
        except BadRequest as exc:
            return JSONResponse({"error": "BadRequest", "note": str(exc)}, status_code=400)
        except MokataError as exc:
            return refused(state.root, exc)
        except Exception as exc:  # noqa: BLE001 — a bug: 500, traceback logged
            return crashed(request.url.path, exc)

    async def search_meaning(request: Request):
        b = await _body(request)
        q_ = (b.get("q") if isinstance(b.get("q"), str) else "")[:500]
        only = _only(b)
        try:
            done, out = await run_job(request, "search.meaning", {"q": q_, "only": only},
                                      lambda: state.source.meaning(q_, only),
                                      f"meaning · {q_[:80]}", sync_ms=b.get("sync_ms"),
                                      job_id=b.get("job"))
            return JSONResponse(out) if done else out
        except Exception as exc:  # noqa: BLE001 — R18: never a 500; the failure is SAID
            # 0.0.26 P36 (R18) — meaning search never answers a 500. The job that failed is in
            # Activity as FAILED with this error; the page gets a `failed` state naming it (and
            # word results beside it are untouched). The traceback is logged like any crash.
            crashed(request.url.path, exc)        # its traceback to stderr; its 500 discarded
            return JSONResponse({"sample": False, "q": q_, "kind": "meaning", "results": [],
                                 "available": False, "state": "failed",
                                 "error": type(exc).__name__,
                                 "note": f"meaning search failed — {type(exc).__name__}: "
                                         f"{str(exc)[:300]}"})

    async def semantic_fetch(request: Request):
        return JSONResponse(await asyncio.to_thread(state.source.semantic_fetch))

    def graph(request: Request) -> Dict[str, Any]:
        view = q(request, "view") or "ripple"
        if view not in ("ripple", "focus", "path", "untested"):
            raise BadRequest("view must be ripple, focus, path or untested")
        symbol, to = q(request, "symbol")[:300], q(request, "to")[:300]
        if not symbol or (view == "path" and not to):
            raise BadRequest("a symbol is required (and `to` for a path)")
        try:
            depth = int(q(request, "depth") or 2)
        except ValueError:
            depth = 2
        raw = q(request, "page") or "1"
        if not raw.isdigit() or not 1 <= int(raw) <= 100000:
            raise BadRequest("page must be a page number (1, 2, …)")
        page = int(raw)
        return lambda: state.source.graph(view, symbol, to, depth, page)

    # 0.0.23 C5 — the plan + spec of a run, and its lifecycle. Every run id is validated (shape,
    # then existence) inside `plans` before anything is read; a bad one is a BadRequest (400).
    def runs(request: Request) -> Dict[str, Any]:
        # O10 — the Plan view opens on the run the FOCUSED session is in (kept across a resume).
        return state.source.runs(q(request, "source") or "local", state.focus)

    def plan(request: Request) -> Dict[str, Any]:
        raw = q(request, "version") or "0"
        if not raw.isdigit() or len(raw) > 4:
            raise BadRequest("version must be a spec version number")
        return state.source.plan(q(request, "run"), q(request, "source") or "local", int(raw))

    def lifecycle(request: Request) -> Dict[str, Any]:
        return state.source.lifecycle(q(request, "run"))

    # 0.0.25 — the saved plan FILES (`.mokata/temp_local/plans`, the repo's `plans/`), read-only.
    def saved_plans(_request: Request) -> Dict[str, Any]:
        return state.source.saved_plans()

    def saved_plan(request: Request) -> Dict[str, Any]:
        return state.source.saved_plan(q(request, "plan"), q(request, "where") or "internal")

    def caps(_request: Request) -> Dict[str, Any]:
        """What answers here — named, so the status bar never shows a floor as an accelerator."""
        from . import nav
        from . import search
        out: Dict[str, Any] = {"sample": state.demo, "matcher": nav.matcher_name(),
                               "more": {"search": search.engine_name()}}
        try:
            sem = state.source.semantic_status()
            out["more"]["semantic"] = ("local model" if sem.get("available") else
                                       "unavailable (" + str(sem.get("state")) + ")")
        except Exception:  # noqa: BLE001
            out["more"]["semantic"] = "unavailable"
        if state.demo:
            out["graph"] = "none (SAMPLE)"
            return out
        try:
            out["graph"] = nav._layer(state.root).backend_name
        except Exception as exc:  # noqa: BLE001 — not a mokata repo: say so, do not 500
            out["graph"] = f"unavailable ({type(exc).__name__})"
            return out
        # 0.0.25 lane S — the graph that is ACTUALLY answering: an adopted graph whose recent
        # answers came from the floor is named degraded, with the answer's own reason. Read off
        # the answers the page already asked for (no query is run for the status bar).
        health = nav.graph_health(state.root)
        if health:
            out["graph"] = nav.degraded_line(health)
            out["graph_degraded"] = True
        return out

    async def ping(request: Request):
        return JSONResponse({"ok": True, "root": state.root, "focus": state.focus,
                             "demo": state.demo, "pid": os.getpid(), "pages": state.clients,
                             "terminal": state.terminals.enabled[0]})

    # ---------------------------------------------------------------- ID.S3 (Lane ID2)
    async def open_ref(request: Request):
        """OSC-8 target (`mokata.ide.links`): a GET with no side effect — it validates the path
        (contained, repo-relative) and redirects the paired browser to the page, which opens it."""
        from urllib.parse import quote
        from .nav import PathRefused, contained_rel
        try:
            rel = contained_rel(state.root, q(request, "path"))
        except PathRefused as exc:
            return PlainTextResponse(f"refused: {exc}", status_code=400)
        raw = q(request, "line")
        line = int(raw) if raw.isdigit() and len(raw) <= 9 else 1
        return RedirectResponse(f"/?open={quote(rel, safe='/')}&line={max(1, line)}",
                                status_code=303)

    def _term_refused(exc: Exception, status: int = 409):
        return JSONResponse({"error": type(exc).__name__, "note": str(exc)}, status_code=status)

    async def term_caps(request: Request):
        return JSONResponse(state.terminals.caps())

    async def term_open(request: Request):
        from .join import find
        from .terminal import TerminalRefused
        b = await _body(request)
        resume = b.get("resume")
        if resume is not None:
            row = await asyncio.to_thread(find, state.root, resume)
            if row is None:
                return _term_refused(BadRequest("resume names no session this repo knows"), 400)
            if row.get("alive"):
                return _term_refused(BadRequest("that session is live in another terminal — "
                                                "join it instead of resuming a second copy"))
        try:
            term = await asyncio.to_thread(state.terminals.open, b.get("program"),
                                           b.get("cols", 100), b.get("rows", 30), resume)
        except TerminalRefused as exc:
            return _term_refused(exc)
        except OSError as exc:
            return _term_refused(exc, 500)
        return JSONResponse({"id": term.id, "program": term.program})

    async def term_input(request: Request):
        from .bus import read_body
        from .terminal import MAX_INPUT, TerminalRefused
        raw = await read_body(request, MAX_INPUT * 4 + 1024)
        try:
            b = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            b = None
        if not isinstance(b, dict) or not isinstance(b.get("data"), str):
            return _term_refused(BadRequest("send {id, data}"), 400)
        try:
            term = state.terminals.get(b.get("id"))
            n = await asyncio.to_thread(term.write, b["data"].encode("utf-8"))
        except TerminalRefused as exc:
            return _term_refused(exc, 404)
        return JSONResponse({"sent": n})

    async def term_resize(request: Request):
        from .terminal import TerminalRefused, _clamp
        b = await _body(request)
        try:
            term = state.terminals.get(b.get("id"))
        except TerminalRefused as exc:
            return _term_refused(exc, 404)
        term.resize(_clamp(b.get("cols"), 10, 500, term.cols), _clamp(b.get("rows"), 4, 300,
                                                                       term.rows))
        return JSONResponse({"cols": term.cols, "rows": term.rows})

    async def term_close(request: Request):
        b = await _body(request)
        return JSONResponse({"closed": await asyncio.to_thread(state.terminals.close,
                                                               b.get("id"))})

    async def term_out(request: Request):
        """Long-poll: the bytes after `after` (base64), waiting up to `wait` s for some. Reading is
        what keeps a terminal alive — a page that stops polling lets it idle out."""
        import base64
        from .terminal import TerminalRefused
        try:
            term = state.terminals.get(q(request, "id"))
        except TerminalRefused as exc:
            return _term_refused(exc, 404)
        try:
            after = max(0, int(q(request, "after") or 0))
            wait = float(q(request, "wait") or 15)
        except ValueError:
            return _term_refused(BadRequest("after/wait must be numbers"), 400)
        out = await asyncio.to_thread(term.read, after, wait)
        out["data"] = base64.b64encode(out["data"]).decode("ascii")
        return JSONResponse(out)

    def join_list(_request: Request) -> Dict[str, Any]:
        from .join import joinable
        return {"joined": state.joined, "sessions": [] if state.demo else joinable(state.root)}

    async def join(request: Request):
        """Join a session: focus it, follow it in every attached page, and make it the default
        target of context sent from the IDE. `{"session": null}` leaves."""
        from .join import find
        b = await _body(request)
        sid = b.get("session")
        if sid is None:
            state.joined = None
            await state.bus.publish({"kind": "join", "session": None})
            return JSONResponse({"joined": None})
        row = None if state.demo else await asyncio.to_thread(find, state.root, sid)
        if row is None:
            return JSONResponse({"error": "UnknownSession",
                                 "note": "no session with that id is known to this repo "
                                         "(session registry or snapshots)"}, status_code=404)
        state.joined = sid
        state.focus, state.focus_version = sid, state.focus_version + 1
        await state.bus.publish({"kind": "focus", "focus": sid})
        await state.bus.publish({"kind": "join", "session": sid})
        return JSONResponse({"joined": sid, "focus": sid, "follow": True,
                             "alive": row.get("alive")})

    def context_list(_request: Request) -> Dict[str, Any]:
        from . import context as C
        items = [] if state.demo else C.read(state.root)
        return {"items": [{k: v for k, v in i.items() if k != "text"} | {"chars": len(i["text"])}
                          for i in items], "max": C.MAX_ITEMS, "joined": state.joined}

    async def context_send(request: Request):
        """The human's explicit "send to session" (P2: this click IS the consent). The server reads
        the text itself from the contained path and redacts it; the page sends only names."""
        from . import context as C
        from .join import find
        if state.demo:
            return JSONResponse({"error": "Sample", "note": "nothing is sent from SAMPLE data"},
                                status_code=409)
        b = await _body(request)
        target = b.get("session", state.joined)
        if target is not None:
            if await asyncio.to_thread(find, state.root, target) is None:
                return JSONResponse({"error": "UnknownSession", "note": "unknown session"},
                                    status_code=404)
        try:
            item = await asyncio.to_thread(C.send, state.root, b.get("kind"), b.get("path"),
                                           b.get("lines"), target)
        except C.ContextRefused as exc:
            return JSONResponse({"error": "ContextRefused", "note": str(exc)}, status_code=400)
        return JSONResponse({"queued": item})

    async def context_clear(request: Request):
        from . import context as C
        n = 0 if state.demo else await asyncio.to_thread(C.clear, state.root)
        return JSONResponse({"cleared": n})

    async def events(request: Request):
        """ID.S3 — events OUT. Pushed: the bus wakes this stream the moment an event is published
        (a hook POST, or the shared watcher's tail). `changed` / `focus` keep their names and
        meaning for the page; everything else is a `bus` event carrying the neutral JSON."""
        try:
            resume = int(request.headers.get("last-event-id", ""))
        except ValueError:
            resume = None

        # P32: the page's id — while this stream is open, the jobs it asked for stay wanted.
        client = (request.query_params.get("client") or "")[:64] or None

        async def gen():
            state.clients += 1
            state.attach_loop()
            if client:
                state.jobs.client_back(client)
            _ensure_watcher(state)
            last = resume if resume is not None and 0 <= resume <= state.bus.seq else state.bus.seq
            try:
                fp = await asyncio.to_thread(_fingerprint, state.root)
                yield {"event": "changed", "data": fp}
                yield {"event": "focus", "data": json.dumps({"focus": state.focus})}
                if resume is None:
                    # A FIRST connect starts at the current seq; the jobs already known (started
                    # before this stream, e.g. by the page's own first requests) are sent as they
                    # stand now — else a fast job's `done` is never heard (0.0.26 O9).
                    for e in state.bus.job_events():
                        if e["id"] <= last:
                            yield {"event": "job", "id": str(e["id"]),
                                   "data": json.dumps(e.get("job") or {})}
                while True:
                    if await request.is_disconnected():
                        break
                    evts, missed = await state.bus.wait_after(last, POLL_SECS)
                    if missed:
                        last = state.bus.seq
                        yield {"event": "changed", "data": "resync"}
                        continue
                    for e in evts:
                        last = e["id"]
                        if e["kind"] == "changed":
                            yield {"event": "changed", "id": str(e["id"]), "data": e.get("fp", "")}
                        elif e["kind"] == "focus":
                            yield {"event": "focus", "id": str(e["id"]),
                                   "data": json.dumps({"focus": e.get("focus")})}
                        elif e["kind"] == "job":
                            yield {"event": "job", "id": str(e["id"]),
                                   "data": json.dumps(e.get("job") or {})}
                        else:
                            yield {"event": "bus", "id": str(e["id"]), "data": json.dumps(e)}
                    state.last_seen = time.time()
            finally:
                state.clients -= 1
                if client:
                    state.jobs.client_gone(client)
        return EventSourceResponse(gen())

    # 0.0.26 P0 (ID-API-CONTRACT) — the routes are a REGISTRY (`ide/routes.py`): the core ones
    # are registered here, a feature module under `ide/features/` registers its own (one new file,
    # no edit to this list), and every one is mounted behind the same `guard(...)` below. The
    # contract (`api_contract.json` + `features/*.contract.json`) lists exactly these.
    from .routes import FeatureContext, Registry, feature_modules
    reg = Registry()

    def add(path, handler, kind, **kw):
        reg.register(path, handler, kind=kind, feature="core", **kw)

    add("/pair", pair, "page", auth=False)
    add("/", index, "page")
    add("/static/{name}", static, "static")
    add("/static/vendor/{name}", vendor, "static")
    add("/static/features/{name}", feature_static, "static")
    add("/api/ping", ping, "read")
    add("/api/state", lambda r: state.source.state(state.focus), "json")
    add("/api/live", lambda r: state.source.live(q(r, "mokata") == "1"), "json")
    add("/api/session", lambda r: state.source.session(q(r, "id")), "json")
    add("/api/timeline", lambda r: state.source.timeline(q(r, "session") or None), "json")
    add("/api/between", lambda r: state.source.between(q(r, "a"), q(r, "b")), "json")
    add("/api/compare", lambda r: state.source.compare(q(r, "base"), q(r, "head")), "json")
    add("/api/pr", pr, "action")
    # 0.0.23 C1 — navigation. Every path-taking route contains its path (nav.contained_rel).
    def tree(r):
        # 0.0.26 P35 (G13/R19) — lazy: ONE directory's children, or a filter over every path,
        # paged (`offset` / the answer's `next`). Validated here (a bad offset is a 400 at once).
        try:
            offset = max(0, int(q(r, "offset") or 0))
        except ValueError:
            raise BadRequest("offset must be a whole number") from None
        d, flt = q(r, "dir") or "", q(r, "filter") or ""
        return lambda: state.source.tree(d, flt, offset)
    add("/api/tree", tree, "json", job="tree.walk",
        job_label=lambda r: "file tree" + (f" filter {q(r, 'filter')}" if q(r, "filter") else ""))
    add("/api/file", lambda r: state.source.file(q(r, "path"), q(r, "outline") == "1"), "json")
    add("/api/outline", lambda r: state.source.outline(q(r, "path")), "json")
    add("/api/impact", impact, "json", job="impact", job_label=_impact_label)
    add("/api/quickopen", quick, "action")
    add("/api/query", query, "read")
    add("/api/caps", caps, "json")
    add("/api/health", health_route, "read")
    add("/api/health/action", health_action, "action")
    add("/api/health/check-update", health_check_update, "action")
    # 0.0.23 C2 — search: word / regex (rg or the floor, named) and meaning (R2).
    add("/api/search", search_text, "action")
    add("/api/meaning", search_meaning, "action")
    add("/api/semantic", lambda r: state.source.semantic_status(), "json")
    add("/api/semantic/fetch", semantic_fetch, "action")
    # 0.0.23 C3 — the graph views, as data drawn by the vendored Cytoscape.js.
    add("/api/graph", graph, "json", job="graph.view", job_label=_graph_label)
    add("/api/runs", runs, "json")
    add("/api/plan", plan, "json")
    add("/api/lifecycle", lifecycle, "json")
    add("/api/saved-plans", saved_plans, "json")
    add("/api/saved-plan", saved_plan, "json")
    add("/api/focus", focus, "action")
    add("/api/events", events, "stream")
    add("/api/bus", bus_post, "hook")
    add(_GATE_PATH, gate_post, "hook")
    # ID.S3 (Lane ID2) — OSC-8 target, embedded terminal, session join, context injection.
    add("/open", open_ref, "page")
    add("/api/term/caps", term_caps, "read")
    add("/api/term/open", term_open, "action")
    add("/api/term/input", term_input, "action")
    add("/api/term/resize", term_resize, "action")
    add("/api/term/close", term_close, "action")
    add("/api/term/out", term_out, "stream")
    add("/api/join", join_list, "json")
    add("/api/join", join, "action")
    add("/api/context", context_list, "json")
    add("/api/context", context_send, "action")
    add("/api/context/clear", context_clear, "action")
    # 0.0.25 ID3 — session browser / needs-you, time-lapse, treemap, and `ide_open`'s door.
    add("/api/sessions", sessions_browser, "read")
    add("/api/timelapse", timelapse, "json")
    add("/api/treemap", treemap, "json")
    add("/api/open", open_in_page, "action")

    ctx = FeatureContext(state=state, q=q, body=_body, bad_request=BadRequest,
                         assets=dict(assets), registry=reg)
    for mod in features if features is not None else feature_modules():
        mod.routes(reg, ctx)

    routes = [Route(s.path, guard(_json(s.handler, job=s.job, job_label=s.job_label)
                                  if s.kind == "json"
                                  else s.handler, auth=s.auth, hook=s.hook),
                    methods=list(s.methods))
              for s in reg.specs()]
    app = Starlette(routes=routes)
    app.state.registry = reg
    return app


def _ensure_watcher(state: AppState) -> None:
    """ONE watcher per server, alive only while a page is attached (B-01: keep the server light)."""
    if state.watcher is None or state.watcher.done():
        state.watcher = asyncio.get_running_loop().create_task(_watch(state))


async def _watch(state: AppState) -> None:
    """The cheap fallback: stat-gated tails of the event store and the snapshot timeline, and the
    review fingerprint. A `stat` per file per tick; a read only when a file moved; no spawn."""
    from .bus import TAIL_SECS, Tails
    tails = None if state.demo else Tails(state.root)
    if tails:
        await asyncio.to_thread(tails.prime)
    fp = await asyncio.to_thread(_fingerprint, state.root)
    afp = None if state.demo else await asyncio.to_thread(_approvals_fp, state.root)
    while True:
        await asyncio.sleep(TAIL_SECS)
        if state.clients <= 0:
            state.watcher = None             # no await between this check and the exit: a page
            return                           # attaching now starts a fresh watcher
        moved = False
        if tails:
            for evt in await asyncio.to_thread(tails.store_events):
                moved = moved or bool(evt.get("path"))
                await state.bus.publish(evt)
            for evt in await asyncio.to_thread(tails.timeline_edits):
                moved = moved or bool(evt.get("path"))
                await state.bus.publish(evt)
        now = await asyncio.to_thread(_fingerprint, state.root)
        if now != fp:
            fp, moved = now, True
            await state.bus.publish({"kind": "changed", "fp": now})
        if not state.demo:
            # P38 — a proposal appeared, was approved / rejected (in a terminal or here) or used:
            # the approval cards update live. A stat of <= MAX_PROPOSALS small files per tick.
            now_afp = await asyncio.to_thread(_approvals_fp, state.root)
            if now_afp != afp:
                afp = now_afp
                await state.bus.publish({"kind": "approval", "fp": afp})
        if moved:
            from .nav import note_change
            note_change(state.root)          # lane S: ⌘P's file/symbol lists follow real change


async def _exit_watch(server: Any) -> None:
    """0.0.26 L2 — the health panel's Windows update asks this server to exit so the detached
    updater can replace mokata's locked files (`ide.health.EXIT`)."""
    from .health import EXIT
    while not server.should_exit:
        await asyncio.sleep(1.0)
        if EXIT.is_set():
            server.should_exit = True


async def _idle_watch(state: AppState, server: Any, idle_secs: float) -> None:
    while not server.should_exit:
        await asyncio.sleep(5)
        if state.clients == 0 and time.time() - state.last_seen > idle_secs:
            server.should_exit = True


def _stopping_server_class():
    import uvicorn

    class StoppingServer(uvicorn.Server):
        """uvicorn's server, with two changes for P37 (closing with jobs running asks first):
        it remembers WHICH signal stopped it, and it does NOT re-raise that signal once it has
        shut down (uvicorn re-raises it, and a re-raised SIGTERM kills the process on the spot —
        every running index build and update died with no word, before `serve`'s cleanup)."""
        stop_signal: Optional[int] = None

        def handle_exit(self, sig: int, frame: Any) -> None:
            if self.stop_signal is None:
                self.stop_signal = sig
            super().handle_exit(sig, frame)

        @contextlib.contextmanager
        def capture_signals(self):
            import signal as _signal
            from uvicorn.server import HANDLED_SIGNALS
            if threading.current_thread() is not threading.main_thread():
                yield
                return
            original = {sig: _signal.signal(sig, self.handle_exit) for sig in HANDLED_SIGNALS}
            try:
                yield
            finally:
                for sig, handler in original.items():
                    _signal.signal(sig, handler)
    return StoppingServer


def _StoppingServer(config: Any) -> Any:
    return _stopping_server_class()(config)


def _on_stop(state: "AppState", sig: Optional[int], *, interactive: Optional[bool] = None,
             read: Any = None) -> str:
    """0.0.26 P37 (Jas: "while closing, it should confirm to run in background") — the server has
    stopped; jobs that outlive their page are still running. Ctrl+C at a terminal ASKS
    (background / cancel safely / wait); any other stop (SIGTERM, no TTY, the idle exit, an
    update's restart) hands them to background workers. Never a silent kill."""
    import signal as _signal
    from . import handoff
    jobs = state.jobs.running_kept() if state.jobs is not None else []
    if not jobs:
        return ""
    if interactive is None:
        try:
            interactive = sig == _signal.SIGINT and sys.stdin.isatty() and sys.stdout.isatty()
        except (OSError, ValueError):
            interactive = False
    choice = handoff.ask(jobs, root=state.root, **({"read": read} if read else {})) \
        if interactive \
        else handoff.BACKGROUND
    if not interactive:
        print(f"mokata ide: stopping with {len(jobs)} job(s) running — they continue in the "
              "background (shown in Activity and Health at the next start)", flush=True)
    handoff.stop(state.jobs, state.root, choice)
    return choice


def serve(root: str, port: int = 0, focus: Optional[str] = None, demo: bool = False,
          idle_secs: Optional[float] = IDLE_SECS, token: Optional[str] = None,
          terminal: bool = False, approve_here: bool = False) -> int:
    """Run the server in THIS process until stopped (or idle). Writes / removes the record.
    `approve_here` (P38): only `mokata ide --foreground` passes it — the code for an in-window
    approval is printed to this process's terminal, so a detached child can never offer it."""
    try:
        import uvicorn
        build_app  # noqa: B018
        import starlette  # noqa: F401
        import sse_starlette  # noqa: F401
    except ImportError:
        print(INSTALL_LINE, file=sys.stderr)
        return 3
    if not port and not demo:
        from .bus import preferred_port
        port = preferred_port(root)           # ID.S3: the stable port the http hooks post to
    port = port or free_port()
    # lane S: heavy graph work is pure-Python CPU on the job lanes' threads; at the default 5 ms
    # GIL switch interval every syscall a light route makes (a walk, a git read) waits up to 5 ms
    # to get the interpreter back — measured on the scale repo, /api/tree took 11 s behind one
    # running blast radius. 1 ms keeps light routes responsive; the job lanes barely notice.
    sys.setswitchinterval(SWITCH_INTERVAL_SECS)
    state = AppState(root, token or secrets.token_urlsafe(24), port, focus, demo, terminal,
                     approve_here)
    app = build_app(state)
    # lane S: a SIGTERM/SIGINT closes within SHUTDOWN_GRACE_SECS even with heavy work in flight —
    # open requests are cut after the grace, the job lanes' jobs are cancelled, and their
    # threads are daemons, so nothing still computing can hold the process open.
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                            access_log=False, lifespan="off",
                            timeout_graceful_shutdown=SHUTDOWN_GRACE_SECS)
    server = _StoppingServer(config)
    _write_record(root, {"port": port, "pid": os.getpid(), "token": state.token, "root": root,
                         "demo": demo, "started": time.time()})

    async def main():
        tasks = [asyncio.create_task(server.serve()), asyncio.create_task(_exit_watch(server))]
        if idle_secs:
            tasks.append(asyncio.create_task(_idle_watch(state, server, idle_secs)))
        await tasks[0]
        for t in tasks[1:]:
            t.cancel()

    try:
        asyncio.run(main())
        _on_stop(state, server.stop_signal)
    finally:
        state.close(SHUTDOWN_GRACE_SECS)   # P32: a running build kills its subprocess on the way out
        from . import nav
        nav.close_layers()          # 0.0.25 IDE-SERVER-LIGHT: no serve process outlives us
        state.terminals.close_all()  # ID.S3: no terminal outlives its window
        _remove_record(root, os.getpid())
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m mokata.ide.server")
    p.add_argument("--root", required=True)
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--focus", default=None)
    p.add_argument("--demo", action="store_true")
    p.add_argument("--token", default=None)
    p.add_argument("--idle", type=float, default=IDLE_SECS)
    p.add_argument("--terminal", action="store_true",
                   help="enable the embedded terminal (the launcher passes it only from a TTY)")
    a = p.parse_args(argv)
    return serve(a.root, a.port, a.focus, a.demo, a.idle or None, a.token, a.terminal)


if __name__ == "__main__":
    # Run the IMPORTED module's main, never this `__main__` copy. Started as
    # `python -m mokata.ide.server` (how `mokata ide` launches it), this file is loaded twice — as
    # `__main__` and, when `plans` does `from .server import BadRequest`, as `mokata.ide.server` —
    # and the two `BadRequest` classes are different: `_json` here caught only its own, so an
    # unknown run's 400 came out as a 500 `error: BadRequest` (lane S, found at scale).
    from mokata.ide.server import main as _main
    raise SystemExit(_main())
