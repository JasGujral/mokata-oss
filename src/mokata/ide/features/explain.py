"""0.0.27 P46 CHANGE-EXPLAINER — the IDE's Review tab: list the explainer pages, show one page in a
sandboxed frame, ask Claude Code for a new one, cancel, commit a page into the repo (gated).

  GET  /api/explain/list       pages (+ staleness, computed now), hand-off requests (+ their
                               derived WAITING state), MCP-side bundle jobs (status files), sizes
  GET  /api/explain/page       ONE page, served with its OWN headers: `script-src 'none'`, no
                               network, framable by this origin only — the page has zero scripts
  GET  /api/explain/sidecar    its kept-cite list + diagrams + dropped claims (the frame's buttons
                               resolve ONLY through this closed list)
  POST /api/explain/request    "Explain changes": the `explain.bundle` job (progress + Cancel),
                               then the HAND-OFF — the session's next turn when it is live, else
                               the embedded `claude` terminal under the user's own login. The IDE
                               never calls a model.
  POST /api/explain/cancel     a queued request, an IDE job, or an MCP-side job (its cancel flag)
  POST /api/explain/commit     a page INTO the repo: the P2 proposal → human approval → commit

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict

from starlette.responses import JSONResponse, Response

PAGE_CSP = ("default-src 'none'; script-src 'none'; img-src data:; style-src 'unsafe-inline'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'self'")
SAMPLE_PAGE = ("<!doctype html><html lang=\"en\"><head><title>SAMPLE</title></head><body>"
               "<main><h1>SAMPLE change explainer</h1><p>Nothing here is from your repository."
               "</p></main></body></html>")


TAMPERED_PAGE = ("<!doctype html><html lang=\"en\"><head><title>not shown</title></head><body>"
                 "<main><h1>This page is not shown</h1><p>It was changed on disk after mokata "
                 "checked it, and it no longer passes the page allowlist. Regenerate it from the "
                 "Explain screen.</p></main></body></html>")


def _sample_list() -> Dict[str, Any]:
    return {"sample": True, "reviews": [], "requests": [], "jobs": [], "count": 0,
            "size": {"pages": 0, "bundles": 0}, "keep": 20, "keep_bundles": 5}


def routes(reg: Any, ctx: Any) -> None:
    state = ctx.state
    q = ctx.q

    def _err(exc: Exception, status: int = 400) -> JSONResponse:
        return JSONResponse({"error": "BadRequest", "note": str(exc)[:400]}, status_code=status)

    # ------------------------------------------------------------------------------- list
    def listing(request: Any) -> Dict[str, Any]:
        if state.demo:
            return _sample_list()
        from ...explain import store as XS
        from ...explain.safedir import UnsafeStore
        sid = q(request, "session")
        try:
            lr = XS.list_reviews(state.root, XS.check_sid(sid) if sid else None)
            size = XS.store_size(state.root)
            reqs = XS.requests(state.root)
            jobs = XS.list_jobs(state.root)
        except UnsafeStore as exc:
            raise ctx.bad_request(str(exc))
        now = time.time()
        out_reqs = []
        for r in reqs:
            if sid and r.get("sid") != sid:
                continue
            out_reqs.append({**{k: r.get(k) for k in ("request_id", "spec", "sid", "via",
                                                     "session", "at", "delivered_at")},
                             **XS.request_state(r, lr["reviews"], alive=_alive(r), now=now)})
        return {"sample": False, "reviews": lr["reviews"], "requests": out_reqs,
                "jobs": jobs, "count": lr["count"],
                "size": size, "keep": XS.KEEP_PAGES, "keep_bundles": XS.KEEP_BUNDLES}
    reg.register("/api/explain/list", listing, kind="json", feature="explain")

    def _alive(r: Dict[str, Any]) -> bool:
        if r.get("via") == "terminal":
            from ..terminal import TerminalRefused
            try:
                return bool(state.terminals.get(r.get("terminal")).alive)
            except TerminalRefused:          # closed, or idled out: not alive
                return False
        from ..sessions import get as get_session
        row = get_session(state.root, r.get("session") or "")
        return bool(row is not None and row.status == "live")

    # ------------------------------------------------------------------------------- page
    async def page(request: Any):
        rid = q(request, "id")
        if state.demo:
            body = SAMPLE_PAGE.encode("utf-8")
        else:
            import asyncio
            from ...explain import store as XS
            from ...explain.safedir import UnsafeStore
            try:
                body = await asyncio.to_thread(XS.read_page, state.root, rid)
            except UnsafeStore as exc:
                return _err(exc)
            if body is None:
                return _err(ValueError("no such explainer page"), 404)
            # Re-checked on EVERY serve, not only at save: a page changed on disk after mokata
            # validated it (by hand, by another tool) is never shown — the frame's sandbox and
            # CSP would still hold, but a `<meta refresh>` could navigate it, and nothing the
            # allowlist refuses should reach the frame at all.
            from ...explain.render import validate
            if validate(body.decode("utf-8", "replace")):
                body = TAMPERED_PAGE.encode("utf-8")
        return Response(body, media_type="text/html; charset=utf-8", headers={
            "Content-Security-Policy": PAGE_CSP, "X-Frame-Options": "SAMEORIGIN",
            "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer"})
    reg.register("/api/explain/page", page, kind="read", feature="explain")

    def sidecar(request: Any) -> Dict[str, Any]:
        if state.demo:
            return {"sample": True, "id": q(request, "id"), "cites": [], "diagrams": [],
                    "dropped": [], "target": {}, "kept": 0, "dropped_count": 0}
        from ...explain import store as XS
        from ...explain.safedir import UnsafeStore
        try:
            sc = XS.read_sidecar(state.root, q(request, "id"))
        except UnsafeStore as exc:
            raise ctx.bad_request(str(exc))
        if not sc:
            raise ctx.bad_request("no such explainer page (or its sidecar is unreadable)")
        # A base cite carries the base COMMIT it must be read at (P5's `/api/diff/context?rev=`,
        # read-only), decided here from the stored target — never chosen by the page. A target
        # with no base commit (a session read from its snapshots) leaves `rev` empty and the
        # viewer says so; nothing ever falls back to the file at head (P46 ruling 2).
        # Each base cite also carries `base`: present | added | unavailable — an explicit object
        # check of the commit and the blob (P46 r3 M2), so "the change added it" is said only of a
        # path absent from a base commit that IS here, and a pruned / shallow base says so.
        from ...explain import bundle as XB
        base_sha = str((sc.get("target") or {}).get("base_sha") or "")
        raw = [c for c in (sc.get("cites") or []) if isinstance(c, dict)]
        objs = XB.base_objects(state.root, base_sha,
                               [str(c.get("path") or "") for c in raw if c.get("tree") == "base"]
                               ) if base_sha else {}
        cites = [dict(c, rev=base_sha, base=objs.get(str(c.get("path") or ""), "unavailable")
                      if base_sha else "none")
                 if c.get("tree") == "base" else c for c in raw]
        return {"sample": False, "cites": cites, **{k: sc.get(k) for k in (
            "id", "diagrams", "dropped", "target", "kept", "dropped_count",
            "claude_code", "generated_at", "content_hash", "omitted", "pruned")},
            **XS.staleness(state.root, sc)}
    reg.register("/api/explain/sidecar", sidecar, kind="json", feature="explain")

    # ------------------------------------------------------------------------------- request
    async def request_route(request: Any):
        b = await ctx.body(request)
        from ...explain import bundle as B
        try:
            spec = B.parse_target(b.get("target") if isinstance(b, dict) else None)
        except B.BundleRefused as exc:
            return _err(exc)
        state.attach_loop()
        client = request.headers.get("x-mokata-client") or None
        if state.demo:
            job = state.jobs.start("explain.bundle", {"sample": True, "spec": B.target_text(spec)},
                                   lambda: {"sample": True, "via": "none", "request": None,
                                            "note": "SAMPLE — nothing was asked of Claude Code"},
                                   label="explain · SAMPLE (nothing runs)", scope="SAMPLE",
                                   client=client)
            return JSONResponse({"id": job.id, "job": job.snapshot()})
        import asyncio
        try:
            spec = await asyncio.to_thread(_pin, state.root, spec)
        except B.BundleRefused as exc:
            return _err(exc)
        root = state.root
        job = state.jobs.start("explain.bundle", {"route": "/api/explain/request",
                                                  "spec": B.target_text(spec)},
                               lambda: _bundle_then_hand_off(root, spec),
                               label=f"explain · {B.target_text(spec)[:80]}",
                               scope=os.path.basename(root), client=client)
        return JSONResponse({"id": job.id, "job": job.snapshot()})
    reg.register("/api/explain/request", request_route, kind="action", feature="explain")

    def _bundle_then_hand_off(root: str, spec: Dict[str, str]) -> Dict[str, Any]:
        from ...explain import bundle as B
        from ...explain import store as XS
        from ...knowledge.cancel import checkpoint
        built = B.build(root, spec)
        checkpoint()
        h = built["bundle"]["content_hash"]
        cached = XS.cache_bundle(root, built, spill=False)
        checkpoint()
        # over the cache bound: nothing is kept or spilled here (P46 r4 N1, r5 N3) — Claude
        # Code's MCP server builds and spills its own; the user is told, never left to wonder.
        extra = " · " + cached["cache"] if cached.get("cache") else ""
        built = cached = None
        text = B.target_text(spec)
        sid = spec.get("session") or ""
        row = None
        if sid:
            from ..sessions import get as get_session
            row = get_session(root, sid)
        if row is not None and row.status == "live":
            from ..context import queue_explain
            rec = XS.new_request(root, spec, "session", session=sid)
            sup = queue_explain(root, sid, text, rec["request_id"])
            if sup.get("superseded"):
                XS.update_request(root, sup["superseded"], cancelled=True)
            return {"sample": False, "via": "session", "request": rec["request_id"],
                    "content_hash": h,
                    "superseded": sup.get("superseded"),
                    "note": "queued for that session's next turn — it runs when you next send a "
                            "message there" + extra}
        term = state.terminals.open("claude", 120, 32, None, explain=text)
        rec = XS.new_request(root, spec, "terminal", terminal=term.id)
        return {"sample": False, "via": "terminal", "request": rec["request_id"],
                "terminal": term.id, "content_hash": h,
                "note": "Claude Code started in the embedded terminal under your own login — "
                        "mokata cannot see or stop it; close the terminal tab to abandon" + extra}

    # ------------------------------------------------------------------------------- cancel
    async def cancel(request: Any):
        b = await ctx.body(request)
        if state.demo:
            return JSONResponse({"cancelled": "nothing", "id": "", "sample": True,
                                 "note": "SAMPLE — nothing is running"})
        from ...explain import store as XS
        from ...explain.safedir import UnsafeStore
        try:
            if isinstance(b.get("request"), str):
                rec = XS.update_request(state.root, b["request"], cancelled=True)
                if rec is None:
                    return _err(ValueError("no such request"))
                if rec.get("session"):
                    from ..context import explain_clear
                    explain_clear(state.root, rec["session"], rec["request_id"])
                return JSONResponse({"cancelled": "request", "id": rec["request_id"],
                                     "note": "cancelled — if Claude Code already started, it is "
                                             "not stopped by this; close its terminal"})
            if isinstance(b.get("mcp_job"), str):
                XS.request_cancel(state.root, b["mcp_job"])
                return JSONResponse({"cancelled": "mcp_job", "id": b["mcp_job"],
                                     "note": "the MCP-side job stops at its next file"})
        except UnsafeStore as exc:
            return _err(exc)
        return _err(ValueError("say what to cancel: {request} or {mcp_job} (an IDE job is "
                               "cancelled from the jobs strip)"))
    reg.register("/api/explain/cancel", cancel, kind="action", feature="explain")

    # ------------------------------------------------------------------------------- commit
    async def commit(request: Any):
        b = await ctx.body(request)
        if state.demo:
            return JSONResponse({"status": "sample", "committed": False,
                                 "message": "SAMPLE — nothing is proposed or written"})
        import asyncio
        from ...mcp.tools_explain import explain_commit
        review, dest = b.get("review"), b.get("dest")
        if not isinstance(review, str) or not isinstance(dest, str):
            return _err(ValueError("{review, dest} are required"))
        pid = b.get("proposal_id") if isinstance(b.get("proposal_id"), str) else ""
        out = await asyncio.to_thread(explain_commit, path=state.root, review=review, dest=dest,
                                      proposal_id=pid)
        if out.get("status") == "refused":
            return _err(ValueError(out.get("message") or "refused"))
        return JSONResponse({k: out.get(k) for k in ("status", "committed", "proposal_id",
                                                     "message", "reason", "next", "approve_with",
                                                     "command") if k in out})
    reg.register("/api/explain/commit", commit, kind="action", feature="explain")


def _pin(root: str, spec: Dict[str, str]) -> Dict[str, str]:
    """A range is pinned to its two commit SHAs NOW: the hand-off carries hex only, and a ref
    cannot move between the click and the run."""
    from ...explain import bundle as B
    if spec["kind"] != "range":
        return spec
    b, h = B.rev(root, spec["base"]), B.rev(root, spec["head"])
    if not (b and h):
        raise B.BundleRefused("the range's refs do not resolve to commits here")
    return {"kind": "range", "base": b, "head": h}


__all__ = ["routes", "PAGE_CSP"]
