"""`/api/jobs` — the IDE's long work, as jobs (0.0.26 P32 `IDE-JOBS`; PRD §5.3).

  GET  /api/jobs               the active jobs, the last 20 finished, and which graph kinds are
                               failing at use (R35)
  POST /api/jobs               `{kind, args}` -> `{id, job}`: start the question (or join the same
                               one running — same kind + args is the same job)
  GET  /api/jobs/{id}          one job: its state, its partial answer so far, and its result once
                               done (the Activity drawer's "Open result")
  POST /api/jobs/{id}/cancel   stop it: the work stops at its next checkpoint, a subprocess is
                               killed, and the job is `cancelled` at once
  POST /api/jobs/{id}/leave    `{client}`: this page no longer wants it (a superseded request); a
                               query job nobody wants is cancelled, an index build keeps going

Progress arrives on `/api/events` as `job` events (<= 1 s apart while a job runs). The heavy core
routes (`/api/graph`, `/api/query`, `/api/impact`, `/api/search`, `/api/meaning`, `/api/tree`)
start the same jobs themselves and answer 202 with the handle when the answer takes longer than
the sync window. `/api/health` runs its checks as the `health.check` job too but always answers
200 — past the window, in the CHECKING shape with the handle in `pending` (INT-2a). `POST
/api/jobs` is the door for a page (or a script) that wants the handle straight away.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, Tuple

from starlette.responses import JSONResponse

#: Kinds a page may start here, and what each needs. `model.fetch` is not one: it goes ONLINE and
#: has its own button (the Meaning column's), which is the human's consent for that one call.
_INDEX_ACTIONS = {"index.crg": "rebuild-graph", "index.ast": "rebuild-ast",
                  "index.meaning": "rebuild-search", "mokata.update": "update"}


def _s(args: Dict[str, Any], k: str, n: int = 300) -> str:
    v = args.get(k)
    return v[:n] if isinstance(v, str) else ""


def routes(reg: Any, ctx: Any) -> None:
    state = ctx.state

    def builder(kind: str, args: Dict[str, Any]) -> Tuple[Callable[[], Any], str]:
        """(the work, its label) for a page-started job — validated here (BadRequest -> 400)."""
        src = state.source
        if kind == "tree.walk":
            return src.tree, "file tree"
        if kind == "graph.view":
            view = _s(args, "view") or "ripple"
            sym, to = _s(args, "symbol"), _s(args, "to")
            if view not in ("ripple", "focus", "path", "untested"):
                raise ctx.bad_request("view must be ripple, focus, path or untested")
            if not sym or (view == "path" and not to):
                raise ctx.bad_request("a symbol is required (and `to` for a path)")
            depth = args.get("depth") if isinstance(args.get("depth"), int) else 2
            page = args.get("page") if isinstance(args.get("page"), int) and args["page"] > 0 else 1
            return (lambda: src.graph(view, sym, to, depth, page)), f"graph {view} · {sym[:80]}"
        if kind == "graph.query":
            from ...knowledge.query import QUERY_KINDS
            qk, target = _s(args, "kind", 40), _s(args, "target")
            if qk not in QUERY_KINDS or not target:
                raise ctx.bad_request(f"kind must be one of {list(QUERY_KINDS)}, with a target")
            depth = max(1, min(6, args.get("depth") if isinstance(args.get("depth"), int)
                               else 2))
            return (lambda: json.loads(src.query(qk, target, depth))), f"{qk} · {target[:80]}"
        if kind == "impact":
            path = _s(args, "path", 1000)
            if not path:
                raise ctx.bad_request("impact needs a path")
            return (lambda: src.impact(path, _s(args, "lines", 2000), None)), f"impact · {path}"
        if kind == "search.text":
            from ..search import BadPattern, _compile
            q, mode = _s(args, "q", 500), "regex" if args.get("mode") == "regex" else "word"
            case = bool(args.get("case"))
            try:
                if q.strip():
                    _compile(q, mode, case)
            except BadPattern as exc:
                raise ctx.bad_request(str(exc)) from exc
            return (lambda: src.search(q, mode, case, None)), f"search · {q[:80]}"
        if kind == "search.meaning":
            q = _s(args, "q", 500)
            return (lambda: src.meaning(q, None)), f"meaning · {q[:80]}"
        raise ctx.bad_request(f"a page cannot start a {kind!r} job here")

    def read_jobs(_request: Any) -> Dict[str, Any]:
        out = state.jobs.listing()
        out["failing"] = state.jobs.failing()
        # P37 — what the last close handed to background workers (running, done or failed).
        from .. import handoff
        out["background"] = [] if state.demo else handoff.snapshots(state.root)
        return out
    reg.register("/api/jobs", read_jobs, kind="json", feature="jobs")

    async def start(request: Any):
        from .. import health
        b = await ctx.body(request)
        kind = b.get("kind") if isinstance(b.get("kind"), str) else ""
        args = b.get("args") if isinstance(b.get("args"), dict) else {}
        client = request.headers.get("x-mokata-client") or None
        state.attach_loop()
        try:
            if kind == "index.symbols":
                if state.demo:
                    raise ctx.bad_request("the SAMPLE has no symbol index to build")
                from .. import nav
                sx = nav._per_root(state.root, "symbols", nav._Symbols)
                sx.invalidate()
                sx.get(0)                       # starts the build as its background job
                job = state.jobs.find("index.symbols", {"root": os.path.abspath(state.root)})
                if job is None:
                    raise ctx.bad_request("the symbol index is already up to date")
            elif kind in _INDEX_ACTIONS:
                if state.demo:
                    raise ctx.bad_request("the SAMPLE has nothing to rebuild or update")
                import asyncio
                from .. import nav
                layer = await asyncio.to_thread(nav._layer, state.root)
                snap = health.start(state.root, layer, _INDEX_ACTIONS[kind],
                                    _s(args, "confirm", 2000), state.jobs)["job"]
                return JSONResponse({"id": snap["id"], "job": snap})
            else:
                from ..jobs import KINDS
                if kind not in KINDS:
                    raise ctx.bad_request(f"kind must be one of {sorted(KINDS)}")
                work, label = builder(kind, args)
                job = state.jobs.start(kind, {"route": "/api/jobs", **args}, work, label=label,
                                       scope=os.path.basename(state.root), client=client)
        except ctx.bad_request as exc:
            return JSONResponse({"error": "BadRequest", "note": str(exc)}, status_code=400)
        return JSONResponse({"id": job.id, "job": job.snapshot()})
    reg.register("/api/jobs", start, kind="action", feature="jobs")

    def _unknown(job_id: str):
        return JSONResponse({"error": "UnknownJob",
                             "note": f"no job {job_id[:80]!r} (finished ones are kept for the "
                                     "last 20)"}, status_code=404)

    async def one(request: Any):
        jid = request.path_params["id"]
        job = state.jobs.get(jid)
        if job is None:
            return _unknown(jid)
        res = job.result if job.state == "done" else None
        if isinstance(res, str):
            res = json.loads(res)                # `/api/query` keeps the CLI's exact bytes
        return JSONResponse({"job": job.snapshot(), "partial": job.partial, "result": res})
    reg.register("/api/jobs/{id}", one, kind="read", feature="jobs")

    async def cancel(request: Any):
        jid = request.path_params["id"]
        job = state.jobs.cancel(jid)
        if job is None:
            return _unknown(jid)
        return JSONResponse({"job": job.snapshot()})
    reg.register("/api/jobs/{id}/cancel", cancel, kind="action", feature="jobs")

    async def leave(request: Any):
        jid = request.path_params["id"]
        job = state.jobs.get(jid)
        if job is None:
            return _unknown(jid)
        b = await ctx.body(request)
        client = b.get("client") if isinstance(b.get("client"), str) else None
        state.jobs.leave(job, client or request.headers.get("x-mokata-client"))
        return JSONResponse({"job": job.snapshot()})
    reg.register("/api/jobs/{id}/leave", leave, kind="action", feature="jobs")
