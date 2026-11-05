"""`/api/activity*` — the Activity panel's two reads (0.0.27 P12 `ID-ACTIVITY-PANEL`).

  GET /api/activity?focus=
        the live picture: every live session (+ the focused one, + any waiting on you) with its
        phase, last tool, files, RED/GREEN record, gates and needs-you; the IDE's jobs; the
        needs-you queue. Read ONCE by the page; every later change is pushed on `/api/events`
        (`edit` · `tool` · `turn_end` · `event` · `job` · `approval`) — the page never polls.
  GET /api/activity/history?session=&before=&limit=&types=
        one page of a session's timeline, newest first, paged by the `next` cursor (never capped);
        a resumed session is joined to the sessions it continues (O10). `types` narrows to a
        comma-separated subset of the item types (`ide.activity.TYPES`).

Cancel on a job is the jobs feature's own door (`POST /api/jobs/{id}/cancel`); the panel calls it.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from starlette.responses import JSONResponse

from ..activity import PAGE, PAGE_MAX, ActivityRefused


def routes(reg: Any, ctx: Any) -> None:
    state = ctx.state

    def _refused(exc: Exception) -> Any:
        return JSONResponse({"error": type(exc).__name__, "note": str(exc)}, status_code=400)

    async def live(request: Any):
        """kind=read: the bus ring is copied ON the loop (its owner), then read in a thread."""
        from ..server import crashed, refused
        from ...errors import MokataError
        ring = state.bus.recent()
        focus = (ctx.q(request, "focus") or state.focus or "")[:200]
        jobs = state.jobs.listing()
        try:
            out = await asyncio.to_thread(state.source.activity, ring, focus, jobs)
        except (ActivityRefused, ctx.bad_request) as exc:
            return _refused(exc)
        except MokataError as exc:
            return refused(state.root, exc)
        except Exception as exc:  # noqa: BLE001 — a bug: 500 + traceback (`crashed`)
            return crashed(request.url.path, exc)
        return JSONResponse(out)

    def history(request: Any) -> Dict[str, Any]:
        sid = ctx.q(request, "session")
        if not sid or len(sid) > 200:
            raise ctx.bad_request("session is required: a session id")
        raw = ctx.q(request, "limit")
        limit = PAGE
        if raw:
            if not raw.isdigit() or len(raw) > 6:
                raise ctx.bad_request("limit must be a whole number")
            limit = max(1, min(PAGE_MAX, int(raw)))
        types = [t for t in ctx.q(request, "types").split(",") if t]
        try:
            return state.source.activity_history(sid, ctx.q(request, "before") or None, limit,
                                                 types)
        except ActivityRefused as exc:
            raise ctx.bad_request(str(exc)) from exc

    reg.register("/api/activity", live, kind="read", feature="activity")
    reg.register("/api/activity/history", history, kind="json", feature="activity")
