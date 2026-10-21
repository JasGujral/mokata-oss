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
    fetch behind the Meaning tab's button — meaning search otherwise loads its model offline.

LIFETIME. Started from a terminal it lives as long as its command (`--foreground`). Started from a
Claude session (`/mokata:review-window`) it is a child that exits after `IDLE_SECS` with no page attached —
never a daemon that outlives its use. Its record (port, pid, token) is `temp_local/ide/server.json`,
removed on exit, so a second `/mokata:review-window` finds it and RE-FOCUSES it instead of starting another.
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import os
import queue
import secrets
import socket
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from ..errors import MokataError
from .bus import HOOK_HEADER
from .gate_door import GATE_PATH as _GATE_PATH
from ..tdd_state import state_dir

IDLE_SECS = 30 * 60
POLL_SECS = 1.0            # SSE keep-alive tick: focus / idle bookkeeping (events arrive pushed)
COOKIE = "mokata_ide"
TOKEN_HEADER = "x-mokata-token"
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
STATIC_FILES = ("index.html", "app.js", "combo.js", "nav.js", "search.js", "graph.js", "plan.js",
                "terminal.js", "bridge.js", "sessions.js", "health.js", "boot.js", "app.css",
                "mokata-wordmark-dark.svg", "mokata-wordmark-light.svg", "mokata-icon.svg")
# 0.0.23 C3 — the vendored graph libraries (MIT; pins + sha256 in static/vendor/VERSIONS.json).
VENDOR_FILES = ("cytoscape.min.js", "layout-base.js", "cose-base.js", "cytoscape-fcose.js",
                "cytoscape-dagre.min.js")
_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
          ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}

INSTALL_LINE = ('mokata ide needs its web dependencies: pip install "mokata[ide]" '
                "(starlette, uvicorn, sse-starlette — normally already present with mokata's MCP SDK)")


class BadRequest(MokataError, ValueError):
    """A request the page should not have made (a missing or unknown parameter) — a HARD refusal
    answered 400 (D5); nothing is attempted."""


# ------------------------------------------------------------------------------ the heavy lane
#
# 0.0.25 (lane S — the 1M-line scale run). Every data route used to run on asyncio's ONE default
# thread pool with no deadline and no cancellation. The Review page asks `/api/impact` for each
# changed file; on a hub function one impact never finished, the abandoned calls filled the pool,
# and then /api/caps, /api/tree and /api/state hung too — and SIGTERM could not stop the server
# (its non-daemon pool threads were joined on the way out). Now the graph's heavy questions
# (impact, the graph views, `query`) run HERE:
#
#   * a SMALL bounded pool of DAEMON threads of its own — light routes never queue behind them,
#     and a stuck one can never hold the process open;
#   * a DEADLINE per request (queue time included): past it the request answers an honest
#     "timed out — <what to do>" (503 + `timed_out: true`), never a 500 and never silence;
#   * a request whose browser went away stops waiting at once;
#   * identical questions in flight are COALESCED (a page refresh re-asking joins the running
#     answer instead of queueing a second copy);
#   * when nobody is waiting any more the job's cancel flag is set and the work stops at its next
#     graph question (`nav.check_abandoned`); a job nobody waits for is never started.
HEAVY_WORKERS = 2
HEAVY_DEADLINE_SECS = 20.0
MEANING_DEADLINE_SECS = 120.0
SHUTDOWN_GRACE_SECS = 2.0       # how long a SIGTERM/SIGINT waits for open requests before closing
SWITCH_INTERVAL_SECS = 0.001   # the GIL hand-off interval while serving (see `serve`)
_WAIT_TICK = 0.25               # how often a waiting request checks its client is still there


class _Job:
    __slots__ = ("key", "fn", "cancel", "done", "result", "exc", "waiters", "_wake")

    def __init__(self, key: Any, fn: Callable[[], Any]):
        self.key, self.fn = key, fn
        self.cancel, self.done = threading.Event(), threading.Event()
        self.result: Any = None
        self.exc: Optional[BaseException] = None
        self.waiters = 0
        self._wake: List[Callable[[], None]] = []


class HeavyLane:
    """A small bounded pool of daemon threads for slow questions (see the section note)."""

    def __init__(self, workers: int = HEAVY_WORKERS, name: str = "mokata-heavy"):
        self._q: "queue.Queue[Optional[_Job]]" = queue.Queue()
        self._lock = threading.Lock()
        self._inflight: Dict[Any, _Job] = {}
        self._closed = False
        self.started = 0                     # jobs that actually ran (tests read it)
        self._name, self._workers = name, max(1, workers)
        self._threads: List[threading.Thread] = []

    def submit(self, key: Any, fn: Callable[[], Any]) -> _Job:
        with self._lock:
            if not self._threads and not self._closed:      # started on first use
                self._threads = [threading.Thread(target=self._work, daemon=True,
                                                  name=f"{self._name}-{i}")
                                 for i in range(self._workers)]
                for t in self._threads:
                    t.start()
            job = self._inflight.get(key)
            if job is None or job.cancel.is_set():
                job = self._inflight[key] = _Job(key, fn)
                if self._closed:
                    job.cancel.set()
                self._q.put_nowait(job)
            job.waiters += 1
            return job

    def leave(self, job: _Job) -> None:
        """One waiter stopped waiting. The last one out cancels the job."""
        with self._lock:
            job.waiters -= 1
            if job.waiters <= 0 and not job.done.is_set():
                job.cancel.set()
                if self._inflight.get(job.key) is job:
                    del self._inflight[job.key]

    def on_done(self, job: _Job, wake: Callable[[], None]) -> None:
        with self._lock:
            if not job.done.is_set():
                job._wake.append(wake)
                return
        wake()

    def busy(self) -> int:
        with self._lock:
            return len(self._inflight)

    def _work(self) -> None:
        from . import nav
        while True:
            job = self._q.get()
            if job is None:
                return
            if job.cancel.is_set():
                job.exc = nav.Abandoned("nobody was waiting any more — not started")
            else:
                self.started += 1
                with nav.cancellable(job.cancel):
                    try:
                        job.result = job.fn()
                    except Exception as exc:  # noqa: BLE001 — handed to the waiting request
                        job.exc = exc
            with self._lock:
                if self._inflight.get(job.key) is job:
                    del self._inflight[job.key]
                job.done.set()
                wakes, job._wake = job._wake, []
            for w in wakes:
                w()

    def close(self) -> None:
        """Cancel everything queued or running and let the (daemon) workers go."""
        with self._lock:
            self._closed = True
            for job in self._inflight.values():
                job.cancel.set()
            self._inflight.clear()
        for _ in self._threads:
            self._q.put_nowait(None)


class TimedOut(MokataError):
    """A heavy question did not finish inside its deadline (answered 503 with what to do)."""


class ClientGone(MokataError):
    """The browser that asked went away; nobody reads the answer."""


async def run_heavy(lane: HeavyLane, request: Any, key: Any, fn: Callable[[], Any],
                    deadline: float) -> Any:
    """Run `fn` on `lane` for this request: its answer, or TimedOut / ClientGone. Waiting is
    bounded by `deadline` and stops early when the client disconnects."""
    loop = asyncio.get_running_loop()
    job = lane.submit(key, fn)
    woke = loop.create_future()

    def wake() -> None:
        loop.call_soon_threadsafe(lambda: woke.done() or woke.set_result(None))
    lane.on_done(job, wake)
    end = time.monotonic() + deadline
    try:
        while not job.done.is_set():
            left = end - time.monotonic()
            if left <= 0:
                raise TimedOut(f"did not finish within {deadline:g} s")
            await asyncio.wait({woke}, timeout=min(_WAIT_TICK, left))
            if not job.done.is_set() and request is not None and await request.is_disconnected():
                raise ClientGone("the client went away")
    finally:
        lane.leave(job)
    if job.exc is not None:
        raise job.exc
    return job.result


def record_path(root: str) -> str:
    return os.path.join(os.path.dirname(state_dir(root)), "ide", "server.json")


def read_record(root: str) -> Optional[Dict[str, Any]]:
    try:
        with open(record_path(root), encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _write_record(root: str, data: Dict[str, Any]) -> None:
    path = record_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
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
                 terminal: bool = False):
        from .source import DemoSource, RealSource
        self.root = root
        self.token = token
        self.port = port
        self.focus = focus
        self.demo = demo
        self.source = DemoSource() if demo else RealSource(root)
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
        # 0.0.25 lane S — the graph's heavy questions, and meaning search, each on a lane of
        # their own: never on the pool the light routes (caps/state/tree/file/…) answer from.
        self.heavy = HeavyLane(HEAVY_WORKERS, "mokata-heavy")
        self.meaning = HeavyLane(1, "mokata-meaning")

    def close(self) -> None:
        self.heavy.close()
        self.meaning.close()

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


def build_app(state: AppState):
    """The Starlette app. Imported lazily so the core never needs the `[ide]` deps."""
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

    async def index(request: Request):
        return _static("index.html")

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
            body = fh.read()
        return Response(body, media_type=_TYPES.get(os.path.splitext(name)[1], "text/plain"),
                        headers={"Cache-Control": "no-store",
                                 "Content-Security-Policy": "default-src 'self'; "
                                 "img-src 'self' data:; style-src 'self'; script-src 'self'; "
                                 "frame-ancestors 'none'",
                                 "X-Frame-Options": "DENY"})

    def _json(fn, heavy: Optional[Callable[[Request, float], str]] = None):
        """A JSON read route. LIGHT by default (asyncio's pool); `heavy` routes run on the
        server's heavy lane with HEAVY_DEADLINE_SECS — `heavy(request, deadline)` is the honest
        "timed out — what to do" line the page shows when the deadline passes."""
        async def handler(request: Request):
            from .nav import PathRefused
            try:
                if heavy is None:
                    return JSONResponse(await asyncio.to_thread(fn, request))
                deadline = HEAVY_DEADLINE_SECS
                return JSONResponse(await run_heavy(state.heavy, request, _key(request),
                                                    lambda: fn(request), deadline))
            except (PathRefused, BadRequest) as exc:
                return JSONResponse({"error": type(exc).__name__, "note": str(exc)},
                                    status_code=400)
            except TimedOut:
                return _timed_out(heavy(request, deadline), deadline)
            except ClientGone:
                return Response(status_code=499)
            except Exception as exc:  # noqa: BLE001 — a failed read is an answer, not a 500 page
                return JSONResponse({"error": type(exc).__name__, "note": str(exc)[:300]},
                                    status_code=500)
        return handler

    def _key(request: Request) -> Any:
        """Identical questions coalesce: the route and its sorted parameters."""
        return (request.url.path, tuple(sorted(request.query_params.multi_items())))

    def _timed_out(note: str, deadline: float) -> Response:
        return JSONResponse({"error": "TimedOut", "timed_out": True, "deadline_s": deadline,
                             "note": note}, status_code=503)

    def _impact_late(request: Request, deadline: float) -> str:
        return (f"impact for {q(request, 'path')} did not finish within {deadline:g} s — a widely "
                f"called symbol makes its blast radius huge. Open the changed symbol in Focus "
                f"(one hop each way) or ask `mokata query callers <name>`; this page will not ask "
                f"again until the file changes")

    def _graph_late(request: Request, deadline: float) -> str:
        return (f"the {q(request, 'view') or 'ripple'} view of {q(request, 'symbol')} did not "
                f"finish within {deadline:g} s — open it in Focus (one hop each way), or lower "
                f"the depth")

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
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": type(exc).__name__, "note": str(exc)[:300]},
                                status_code=500)

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
        deadline = HEAVY_DEADLINE_SECS
        try:
            body = await run_heavy(state.heavy, request, _key(request),
                                   lambda: state.source.query(kind, target[:300], depth), deadline)
        except TimedOut:
            return _timed_out(f"{kind}({target[:80]}) did not finish within {deadline:g} s — "
                              f"lower the depth, or ask `callers` (one hop) instead", deadline)
        except ClientGone:
            return Response(status_code=499)
        except Exception as exc:  # noqa: BLE001 — e.g. the repo is not a mokata repo
            return JSONResponse({"error": type(exc).__name__, "note": str(exc)[:300]},
                                status_code=409)
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
    # what is derived; update mokata on a confirmed click. Nothing installs a dependency.
    def _health_layer():
        from . import nav
        return None if state.demo else nav._layer(state.root)

    def _health_late(_request: Request, deadline: float) -> str:
        return (f"the health checks did not finish within {deadline:g} s — run `mokata doctor` "
                f"in a terminal for the same verdicts")

    def health_rows(request: Request) -> Dict[str, Any]:
        from . import health
        return health.health(state.root, _health_layer(), demo=state.demo,
                             full=q(request, "full") == "1")

    def health_job(_request: Request) -> Dict[str, Any]:
        from . import health
        return health.JOB.snapshot()

    async def health_action(request: Request):
        from . import health
        if state.demo:
            return JSONResponse({"error": "BadRequest", "note": "the SAMPLE has nothing to "
                                 "rebuild or update"}, status_code=400)
        b = await _body(request)
        action = b.get("action") if isinstance(b.get("action"), str) else ""
        confirm = b.get("confirm") if isinstance(b.get("confirm"), str) else ""
        try:
            layer = await asyncio.to_thread(_health_layer)
            return JSONResponse(health.start(state.root, layer, action, confirm[:2000]))
        except BadRequest as exc:
            return JSONResponse({"error": "BadRequest", "note": str(exc)}, status_code=400)

    async def health_check_update(_request: Request):
        """The ONE outbound call, on the human's click (opt-in, ledger-accounted)."""
        from . import health
        if state.demo:
            return JSONResponse({"ok": False, "message": "the SAMPLE does not check for updates"})
        return JSONResponse(await asyncio.to_thread(health.check_update, state.root))

    async def search_text(request: Request):
        from .search import BadPattern
        b = await _body(request)
        q_ = b.get("q") if isinstance(b.get("q"), str) else ""
        mode = "regex" if b.get("mode") == "regex" else "word"
        try:
            return JSONResponse(await asyncio.to_thread(state.source.search, q_[:500], mode,
                                                        bool(b.get("case")), _only(b)))
        except BadPattern as exc:
            return JSONResponse({"error": "BadPattern", "note": str(exc)}, status_code=400)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": type(exc).__name__, "note": str(exc)[:300]},
                                status_code=500)

    async def search_meaning(request: Request):
        b = await _body(request)
        q_ = b.get("q") if isinstance(b.get("q"), str) else ""
        only = _only(b)
        key = ("meaning", q_[:500], tuple(only) if only is not None else None)
        deadline = MEANING_DEADLINE_SECS
        try:
            return JSONResponse(await run_heavy(state.meaning, request, key,
                                                lambda: state.source.meaning(q_[:500], only),
                                                deadline))
        except TimedOut:
            return JSONResponse({"error": "TimedOut", "timed_out": True, "available": False,
                                 "note": f"meaning search did not finish within {deadline:g} s "
                                         f"(the first search embeds the whole repo) — try again "
                                         f"in a moment, or limit it to the changed files"},
                                status_code=503)
        except ClientGone:
            return Response(status_code=499)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": type(exc).__name__, "note": str(exc)[:300],
                                 "available": False}, status_code=500)

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
        return state.source.graph(view, symbol, to, depth)

    # 0.0.23 C5 — the plan + spec of a run, and its lifecycle. Every run id is validated (shape,
    # then existence) inside `plans` before anything is read; a bad one is a BadRequest (400).
    def runs(request: Request) -> Dict[str, Any]:
        return state.source.runs(q(request, "source") or "local")

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

        async def gen():
            state.clients += 1
            _ensure_watcher(state)
            last = resume if resume is not None and 0 <= resume <= state.bus.seq else state.bus.seq
            try:
                fp = await asyncio.to_thread(_fingerprint, state.root)
                yield {"event": "changed", "data": fp}
                yield {"event": "focus", "data": json.dumps({"focus": state.focus})}
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
                        else:
                            yield {"event": "bus", "id": str(e["id"]), "data": json.dumps(e)}
                    state.last_seen = time.time()
            finally:
                state.clients -= 1
        return EventSourceResponse(gen())

    routes = [
        Route("/pair", guard(pair, auth=False)),
        Route("/", guard(index)),
        Route("/static/{name}", guard(static)),
        Route("/api/ping", guard(ping)),
        Route("/api/state", guard(_json(lambda r: state.source.state(state.focus)))),
        Route("/api/live", guard(_json(lambda r: state.source.live()))),
        Route("/api/session", guard(_json(lambda r: state.source.session(q(r, "id"))))),
        Route("/api/timeline", guard(_json(lambda r: state.source.timeline(q(r, "session") or None)))),
        Route("/api/between", guard(_json(lambda r: state.source.between(q(r, "a"), q(r, "b"))))),
        Route("/api/compare", guard(_json(lambda r: state.source.compare(q(r, "base"),
                                                                         q(r, "head"))))),
        Route("/api/pr", guard(pr), methods=["POST"]),
        # 0.0.23 C1 — navigation. Every path-taking route contains its path (nav.contained_rel).
        Route("/api/tree", guard(_json(lambda r: state.source.tree()))),
        Route("/api/file", guard(_json(lambda r: state.source.file(q(r, "path"))))),
        Route("/api/outline", guard(_json(lambda r: state.source.outline(q(r, "path"))))),
        Route("/api/impact", guard(_json(lambda r: state.source.impact(
            q(r, "path"), q(r, "lines"),
            [n for n in (q(r, "symbols") or "").split(",")[:40] if n.strip()] or None),
            heavy=_impact_late))),
        Route("/api/quickopen", guard(quick), methods=["POST"]),
        Route("/api/query", guard(query)),
        Route("/api/caps", guard(_json(caps))),
        Route("/api/health", guard(_json(health_rows, heavy=_health_late))),
        Route("/api/health/job", guard(_json(health_job))),
        Route("/api/health/action", guard(health_action), methods=["POST"]),
        Route("/api/health/check-update", guard(health_check_update), methods=["POST"]),
        # 0.0.23 C2 — search: word / regex (rg or the floor, named) and meaning (R2).
        Route("/api/search", guard(search_text), methods=["POST"]),
        Route("/api/meaning", guard(search_meaning), methods=["POST"]),
        Route("/api/semantic", guard(_json(lambda r: state.source.semantic_status()))),
        Route("/api/semantic/fetch", guard(semantic_fetch), methods=["POST"]),
        # 0.0.23 C3 — the graph views, as data drawn by the vendored Cytoscape.js.
        Route("/api/graph", guard(_json(graph, heavy=_graph_late))),
        Route("/static/vendor/{name}", guard(vendor)),
        Route("/api/runs", guard(_json(runs))),
        Route("/api/plan", guard(_json(plan))),
        Route("/api/lifecycle", guard(_json(lifecycle))),
        Route("/api/saved-plans", guard(_json(saved_plans))),
        Route("/api/saved-plan", guard(_json(saved_plan))),
        Route("/api/focus", guard(focus), methods=["POST"]),
        Route("/api/events", guard(events)),
        Route("/api/bus", guard(bus_post, hook=True), methods=["POST"]),
        Route(_GATE_PATH, guard(gate_post, hook=True), methods=["POST"]),
        # ID.S3 (Lane ID2) — OSC-8 target, embedded terminal, session join, context injection.
        Route("/open", guard(open_ref)),
        Route("/api/term/caps", guard(term_caps)),
        Route("/api/term/open", guard(term_open), methods=["POST"]),
        Route("/api/term/input", guard(term_input), methods=["POST"]),
        Route("/api/term/resize", guard(term_resize), methods=["POST"]),
        Route("/api/term/close", guard(term_close), methods=["POST"]),
        Route("/api/term/out", guard(term_out)),
        Route("/api/join", guard(_json(join_list))),
        Route("/api/join", guard(join), methods=["POST"]),
        Route("/api/context", guard(_json(context_list))),
        Route("/api/context", guard(context_send), methods=["POST"]),
        Route("/api/context/clear", guard(context_clear), methods=["POST"]),
        # 0.0.25 ID3 — session browser / needs-you, time-lapse, treemap, and `ide_open`'s door.
        Route("/api/sessions", guard(sessions_browser)),
        Route("/api/timelapse", guard(_json(timelapse))),
        Route("/api/treemap", guard(_json(treemap))),
        Route("/api/open", guard(open_in_page), methods=["POST"]),
    ]
    return Starlette(routes=routes)


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
        if moved:
            from .nav import note_change
            note_change(state.root)          # lane S: ⌘P's file/symbol lists follow real change


async def _idle_watch(state: AppState, server: Any, idle_secs: float) -> None:
    while not server.should_exit:
        await asyncio.sleep(5)
        if state.clients == 0 and time.time() - state.last_seen > idle_secs:
            server.should_exit = True


def serve(root: str, port: int = 0, focus: Optional[str] = None, demo: bool = False,
          idle_secs: Optional[float] = IDLE_SECS, token: Optional[str] = None,
          terminal: bool = False) -> int:
    """Run the server in THIS process until stopped (or idle). Writes / removes the record."""
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
    # lane S: heavy graph work is pure-Python CPU on the heavy lane's threads; at the default 5 ms
    # GIL switch interval every syscall a light route makes (a walk, a git read) waits up to 5 ms
    # to get the interpreter back — measured on the scale repo, /api/tree took 11 s behind one
    # running blast radius. 1 ms keeps light routes responsive; the heavy lane barely notices.
    sys.setswitchinterval(SWITCH_INTERVAL_SECS)
    state = AppState(root, token or secrets.token_urlsafe(24), port, focus, demo, terminal)
    app = build_app(state)
    # lane S: a SIGTERM/SIGINT closes within SHUTDOWN_GRACE_SECS even with heavy work in flight —
    # open requests are cut after the grace, the heavy lanes' jobs are cancelled, and their
    # threads are daemons, so nothing still computing can hold the process open.
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                            access_log=False, lifespan="off",
                            timeout_graceful_shutdown=SHUTDOWN_GRACE_SECS)
    server = uvicorn.Server(config)
    _write_record(root, {"port": port, "pid": os.getpid(), "token": state.token, "root": root,
                         "demo": demo, "started": time.time()})

    async def main():
        tasks = [asyncio.create_task(server.serve())]
        if idle_secs:
            tasks.append(asyncio.create_task(_idle_watch(state, server, idle_secs)))
        await tasks[0]
        for t in tasks[1:]:
            t.cancel()

    try:
        asyncio.run(main())
    finally:
        state.close()
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
