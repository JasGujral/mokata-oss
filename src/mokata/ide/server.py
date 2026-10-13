"""The `mokata ide` server — localhost-only, paired, read-only (0.0.22 E2; ADR doc 85 §1b).

GUARDS, every one tested (doc 109 §6 — designed from px0's failures):
  * LOOPBACK ONLY. It binds 127.0.0.1; there is no option to bind anything else.
  * PAIRED. A per-launch random token. `/pair?t=<token>` swaps it for an HttpOnly, SameSite=Strict
    cookie; every other route refuses without that cookie (or, for the launcher's own calls from
    the terminal, the `X-Mokata-Token` header). Compared in constant time.
  * HOST CHECKED on every request (DNS rebinding): only `127.0.0.1:<port>` / `localhost:<port>`.
  * ORIGIN CHECKED on every non-GET: a page on another origin cannot drive it.
  * CONTAINED. Every path it reads goes through `snapshots.rel_path` (realpath inside the repo).
  * READ-ONLY. No route writes the repo. The one network call is a PR fetch the user asked for.

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
import secrets
import socket
import sys
import time
from typing import Any, Dict, Optional

from ..tdd_state import state_dir

IDLE_SECS = 30 * 60
POLL_SECS = 1.0
COOKIE = "mokata_ide"
TOKEN_HEADER = "x-mokata-token"
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
STATIC_FILES = ("index.html", "app.js", "app.css", "mokata-wordmark-dark.svg",
                "mokata-wordmark-light.svg", "mokata-icon.svg")
_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
          ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}

INSTALL_LINE = ('mokata ide needs its web dependencies: pip install "mokata[ide]" '
                "(starlette, uvicorn, sse-starlette — normally already present with mokata's MCP SDK)")


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


class AppState:
    def __init__(self, root: str, token: str, port: int, focus: Optional[str], demo: bool):
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

    def guard(handler, *, auth: bool = True):
        async def wrapped(request: Request):
            if request.headers.get("host", "") not in state.hosts():
                return PlainTextResponse("refused: unexpected Host (mokata ide is localhost-only)",
                                         status_code=403)
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

    def _static(name: str) -> Response:
        with open(os.path.join(STATIC_DIR, name), "rb") as fh:
            body = fh.read()
        return Response(body, media_type=_TYPES.get(os.path.splitext(name)[1], "text/plain"),
                        headers={"Cache-Control": "no-store",
                                 "Content-Security-Policy": "default-src 'self'; "
                                 "img-src 'self' data:; style-src 'self'; script-src 'self'; "
                                 "frame-ancestors 'none'",
                                 "X-Frame-Options": "DENY"})

    def _json(fn):
        async def handler(request: Request):
            try:
                return JSONResponse(await asyncio.to_thread(fn, request))
            except Exception as exc:  # noqa: BLE001 — a failed read is an answer, not a 500 page
                return JSONResponse({"error": type(exc).__name__, "note": str(exc)[:300]},
                                    status_code=500)
        return handler

    q = lambda r, k: r.query_params.get(k, "")  # noqa: E731

    async def focus(request: Request):
        body = await request.json()
        sid = body.get("session") if isinstance(body, dict) else None
        state.focus = sid if isinstance(sid, str) and sid else None
        state.focus_version += 1
        return JSONResponse({"focus": state.focus})

    async def pr(request: Request):
        body = await request.json()
        url = body.get("url", "") if isinstance(body, dict) else ""
        return JSONResponse(await asyncio.to_thread(state.source.pr, url))

    async def ping(request: Request):
        return JSONResponse({"ok": True, "root": state.root, "focus": state.focus,
                             "demo": state.demo, "pid": os.getpid()})

    async def events(request: Request):
        async def gen():
            state.clients += 1
            last, last_focus = None, -1
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    fp = await asyncio.to_thread(_fingerprint, state.root)
                    if fp != last:
                        last = fp
                        yield {"event": "changed", "data": fp}
                    if state.focus_version != last_focus:
                        last_focus = state.focus_version
                        yield {"event": "focus", "data": json.dumps({"focus": state.focus})}
                    state.last_seen = time.time()
                    await asyncio.sleep(POLL_SECS)
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
        Route("/api/focus", guard(focus), methods=["POST"]),
        Route("/api/events", guard(events)),
    ]
    return Starlette(routes=routes)


async def _idle_watch(state: AppState, server: Any, idle_secs: float) -> None:
    while not server.should_exit:
        await asyncio.sleep(5)
        if state.clients == 0 and time.time() - state.last_seen > idle_secs:
            server.should_exit = True


def serve(root: str, port: int = 0, focus: Optional[str] = None, demo: bool = False,
          idle_secs: Optional[float] = IDLE_SECS, token: Optional[str] = None) -> int:
    """Run the server in THIS process until stopped (or idle). Writes / removes the record."""
    try:
        import uvicorn
        build_app  # noqa: B018
        import starlette  # noqa: F401
        import sse_starlette  # noqa: F401
    except ImportError:
        print(INSTALL_LINE, file=sys.stderr)
        return 3
    port = port or free_port()
    state = AppState(root, token or secrets.token_urlsafe(24), port, focus, demo)
    app = build_app(state)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                            access_log=False, lifespan="off")
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
    a = p.parse_args(argv)
    return serve(a.root, a.port, a.focus, a.demo, a.idle or None, a.token)


if __name__ == "__main__":
    raise SystemExit(main())
