"""0.0.27 P4 `ID-COPY-CONTEXT` — Copy with Context (its ref shape) and Find Usages (`ide/usages.py`).

  GET  /api/ref?path=&lo=&hi=          the ref line for those lines (and the definition it
                                       names); `?ref=` reads a ref back to {path, lines, symbol}
  POST /api/usages {path, line}|{ref}  Find Usages of the definition there, as a `usages.find`
                                       JOB (202 + handle; the page follows it and re-asks with
                                       `job` for the answer's head). The job key is the RESOLVED
                                       definition, so aliased spellings join one job.
  GET  /api/usages/page?id=&after=&limit=  one page of a finished answer, read from its spill by
                                       a stable (outside, path, line, col) cursor, with the
                                       staleness of the symbol and of the page's rows
  POST /api/usages/discard {id}        drop it now (its spill file is deleted)
  POST /api/copy-context {path, lo, hi}  Copy with Context as a `copy.context` JOB: both shapes
                                       built, secret-scanned and sized before anything is copied
  GET  /api/copy-context/text?id=&shape=&offset=  the chosen shape's text, chunked
  POST /api/copy-context/discard {id}  drop it now

A replaced, discarded or pre-restart result answers 410 `Expired` (never another result's rows).
Everything here READS: no durable write, nothing sent to a session (P2); the spill files are the
IDE's own scratch, owner-only, deleted on replace / discard / Cancel / stop.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import uuid
from typing import Any, Dict, Optional, Tuple

from starlette.responses import JSONResponse

from ...errors import MokataError
from .. import nav
from .. import usages as U

CLIENT_HEADER = "x-mokata-client"


def _err(status: int, exc: BaseException) -> JSONResponse:
    return JSONResponse({"error": type(exc).__name__, "note": str(exc)[:400]}, status_code=status)


def _int(v: Any, what: str) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise U.RefRefused(f"{what} must be a 1-based line number") from None
    if n < 1:
        raise U.RefRefused(f"{what} must be a 1-based line number")
    return n


def routes(reg: Any, ctx: Any) -> None:
    state = ctx.state
    holder: Dict[str, Any] = {}

    lock = threading.Lock()

    def store() -> U.Store:
        with lock:
            s = holder.get("store")
            if s is None:
                s = holder["store"] = U.Store(U.spill_dir(state.root))
                _close = state.close

                def close(wait: float = 0.0) -> None:   # the spill files go with the IDE
                    s.close()
                    _close(wait)
                state.close = close
        return s

    # V4 — a page that leaves releases its results: its event stream closing starts a grace
    # (an EventSource reconnects by itself), and `client_back` within it cancels the release.
    jobs = getattr(state, "jobs", None)
    if jobs is not None and hasattr(jobs, "client_gone"):
        _gone, _back = jobs.client_gone, getattr(jobs, "client_back", None)

        def client_gone(client: str) -> None:
            _gone(client)
            s = holder.get("store")
            if s is not None:
                s.client_gone(client)
        jobs.client_gone = client_gone
        if _back is not None:
            def client_back(client: str) -> None:
                _back(client)
                s = holder.get("store")
                if s is not None:
                    s.client_back(client)
            jobs.client_back = client_back

    def code_root() -> str:
        return state.source._code_root()

    def handle(j: Any) -> Dict[str, Any]:
        snap = j.snapshot()
        return {"job": j.id, "kind": j.kind, "label": j.label, "state": j.state,
                "done": snap["done"], "total": snap["total"], "unit": snap["unit"],
                "note": snap["note"], "error": snap["error"], "elapsed": snap["elapsed"],
                "partial_ref": snap["partial_ref"], "partial": None}

    def sample() -> JSONResponse:
        return JSONResponse({"error": "Sample", "note": "SAMPLE data — there is no repository to "
                                                        "read"}, status_code=409)

    # ------------------------------------------------------------------------ Copy Ref
    def ref(request: Any) -> Dict[str, Any]:
        if state.demo:
            return {"sample": True, "ref": "", "path": "", "lines": None, "symbol": None,
                    "note": "SAMPLE data — no repository"}
        root = code_root()
        raw = ctx.q(request, "ref")
        try:
            if raw:
                r = U.parse_ref(raw)
                rel = U.canon_path(root, r.path)
                if r.qual:
                    d = U.resolve_qual(root, rel, r.qual)
                    return {"sample": False, "ref": raw, "path": rel, "lines": [d.line, d.line],
                            "symbol": d.facts(), "note": ""}
                lo = r.lo or 1
                out = U.copy_ref(root, rel, lo, r.hi or lo)
            else:
                lo = _int(ctx.q(request, "lo"), "lo")
                hi = _int(ctx.q(request, "hi") or lo, "hi")
                out = U.copy_ref(root, ctx.q(request, "path"), lo, hi)
        except U.RefRefused as exc:
            raise ctx.bad_request(str(exc)) from None
        out["sample"] = False
        return out
    reg.register("/api/ref", ref, kind="json", feature="usages")

    # ------------------------------------------------------------------------ Find Usages
    def _resolve(body: Dict[str, Any]) -> U.Definition:
        root = code_root()
        if isinstance(body.get("ref"), str) and body["ref"]:
            r = U.parse_ref(body["ref"])
            rel = U.canon_path(root, r.path)
            if r.qual:
                return U.resolve_qual(root, rel, r.qual)
            line = r.lo or 1
        else:
            rel = U.canon_path(root, str(body.get("path") or ""))
            line = _int(body.get("line"), "line")
        d, _ol = U.definition_at(root, rel, line)
        if d is None:
            raise U.RefRefused(f"no definition holds {rel}:{line} — put the caret inside a "
                               "function, method or class")
        return d

    def _start(request: Any, kind: str, feature: str, args: Dict[str, Any],
               make: Any, label: str) -> JSONResponse:
        """Start (or join) the job, or — re-asked with `job=` — hand this page its answer and make
        it the page's result unless the page has asked something newer since (V2). `make(cell)`
        builds the work; the work stores its result only after `cell["ready"]` is set, which is
        after this page is recorded as an asker, so a fast answer is never dropped as unwanted."""
        from .. import jobs as J
        state.attach_loop()
        want = args.pop("job", None)
        question = {"route": request.url.path, **args}
        client = request.headers.get(CLIENT_HEADER) or "anon"
        if isinstance(want, str) and want:
            kept = state.jobs.answer(want, kind, question)
            if kept is not None:
                out = dict(kept.result)
                try:
                    out["current"] = store().claim(out["id"], client, feature)
                except U.Expired as exc:
                    return _err(410, exc)
                return JSONResponse(out)
        seq = store().ask()
        cell: Dict[str, Any] = {"ready": threading.Event()}
        j = state.jobs.start(kind, question, make(cell), label=label,
                             scope=os.path.basename(state.root),
                             client=request.headers.get(CLIENT_HEADER) or None)
        store().asked(j.id, client, seq)
        cell["job"] = j.id
        cell["ready"].set()
        return JSONResponse(handle(j), status_code=202 if j.state != J.FAILED else 409)

    def _wait_ready(cell: Dict[str, Any]) -> str:
        cell["ready"].wait(30)
        return str(cell.get("job") or "")

    async def usages(request: Any):
        if state.demo:
            return sample()
        body = await ctx.body(request)
        body = body if isinstance(body, dict) else {}
        try:
            d = await asyncio.to_thread(_resolve, body)
        except (U.RefRefused, nav.PathRefused) as exc:
            return _err(400, exc)
        except MokataError as exc:
            return _err(409, exc)
        root, croot = state.root, code_root()
        host = getattr(state, "lsp", None)

        lexical = body.get("mode") == "lexical"

        def make(cell: Dict[str, Any]) -> Any:
            def work() -> Dict[str, Any]:
                w = U.SpillWriter(store().dir, "usages")
                try:
                    head = U.find_usages(root, croot, d, w, host, lexical=lexical)
                    w.close()
                except BaseException:
                    w.discard()
                    raise
                rid = uuid.uuid4().hex[:16]
                res = U.Result(rid, "usages", head, files={"rows": w.path},
                               index={"rows": w.index}, rows={"rows": w.rows},
                               sizes={"rows": w.bytes}, job=_wait_ready(cell))
                store().put(res)
                return {"id": rid, "total": w.rows, **head}
            return work
        args = {"path": d.path, "line": d.line, "qual": d.qual or "", "name": d.name,
                "mode": "lexical" if lexical else "", "job": body.get("job")}
        return _start(request, "usages.find", "usages", args, make,
                      f"usages · {(d.qual or d.name)[:80]} ({d.path}:{d.line})")
    reg.register("/api/usages", usages, kind="action", feature="usages")

    async def usages_page(request: Any):
        if state.demo:
            return sample()
        rid = ctx.q(request, "id")
        try:
            after = json.loads(ctx.q(request, "after")) if ctx.q(request, "after") else None
            if after is not None:
                if not (isinstance(after, list) and len(after) == 4):
                    raise ValueError
                after = [int(after[0]), str(after[1]), int(after[2]), int(after[3])]
            limit = int(ctx.q(request, "limit") or 100)
        except (ValueError, TypeError):
            return _err(400, U.RefRefused("after is a [outside, path, line, col] cursor and "
                                          "limit an integer"))
        try:
            res = store().get(rid)
            got = await asyncio.to_thread(U.page, res, "rows", after, limit)
            got["stale"] = await asyncio.to_thread(U.staleness, code_root(), res, got["rows"])
        except U.Expired as exc:
            return _err(410, exc)
        got["id"] = rid
        return JSONResponse(got)
    reg.register("/api/usages/page", usages_page, kind="read", feature="usages")

    def discard_route(path: str) -> None:
        async def discard(request: Any):
            body = await ctx.body(request)
            body = body if isinstance(body, dict) else {}
            client = request.headers.get(CLIENT_HEADER) or "anon"
            if body.get("all") is True:                # the page is leaving (pagehide)
                return JSONResponse({"discarded": bool(store().release_client(client))})
            rid = body.get("id")
            if not isinstance(rid, str) or not rid:
                return _err(400, U.RefRefused("id (or all: true) is required"))
            return JSONResponse({"discarded": store().discard(rid, client)})
        reg.register(path, discard, kind="action", feature="usages")
    discard_route("/api/usages/discard")
    discard_route("/api/copy-context/discard")

    # ------------------------------------------------------------------------ Copy with Context
    async def copy_context(request: Any):
        if state.demo:
            return sample()
        body = await ctx.body(request)
        body = body if isinstance(body, dict) else {}
        root, croot = state.root, code_root()
        try:
            rel = await asyncio.to_thread(U.canon_path, croot, str(body.get("path") or ""))
            lo, hi = _int(body.get("lo"), "lo"), _int(body.get("hi") or body.get("lo"), "hi")
        except (U.RefRefused, nav.PathRefused) as exc:
            return _err(400, exc)
        except MokataError as exc:
            return _err(409, exc)
        lo, hi = min(lo, hi), max(lo, hi)

        def make(cell: Dict[str, Any]) -> Any:
            def work() -> Dict[str, Any]:
                meta, files, sizes = U.copy_context(root, croot, rel, lo, hi, store().dir)
                rid = uuid.uuid4().hex[:16]
                store().put(U.Result(rid, "copy", meta, files=files, sizes=sizes,
                                     job=_wait_ready(cell)))
                return {"id": rid, **meta}
            return work
        args = {"path": rel, "lo": lo, "hi": hi, "job": body.get("job")}
        return _start(request, "copy.context", "copy", args, make,
                      f"copy with context · {rel}:{lo}-{hi}")
    reg.register("/api/copy-context", copy_context, kind="action", feature="usages")

    async def copy_text(request: Any):
        if state.demo:
            return sample()
        shape = ctx.q(request, "shape")
        if shape not in ("full", "summary"):
            return _err(400, U.RefRefused("shape is full or summary"))
        try:
            off = int(ctx.q(request, "offset") or 0)
            if off < 0:
                raise ValueError
        except ValueError:
            return _err(400, U.RefRefused("offset is a byte offset"))
        try:
            res = store().get(ctx.q(request, "id"))
            got = await asyncio.to_thread(U.text_chunk, res, shape, off)
        except U.Expired as exc:
            return _err(410, exc)
        return JSONResponse(got)
    reg.register("/api/copy-context/text", copy_text, kind="read", feature="usages")
