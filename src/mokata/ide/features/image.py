"""`/api/image/*` - a changed image, before against after (0.0.27 P9, `ID-IMAGE-DIFF`). The work is
`mokata.ide.imagediff`; these are its two doors. Both are reads - nothing is written.

  GET  /api/image/info   `?path=&old_path=&base=&head=`: both sides of one changed image - state
                         (ok / missing / too_large / undecodable / unreadable), byte size,
                         dimensions (from the header; the server decodes nothing), whether the pixel
                         difference fits its budget, and the limits. `base` defaults to HEAD, an
                         empty `head` is the working tree.
  GET  /api/image/raw    `?path=&rev=`: the bytes of ONE side as an image. The Content-Type is the
                         file NAME's (never sniffed), `nosniff`, and a CSP that allows nothing with
                         a sandbox - an SVG opened directly runs no script, and drawn in an `<img>`
                         it cannot load anything. 404 not on that side, 413 too large (with the
                         words the page shows), 415 the bytes are not the image the name claims,
                         400 a path outside the repo / a bad revision.

SVG is untrusted repo content: the page puts it in an `<img src>` pointing at this route and nowhere
else; no byte of it is ever parsed as markup by the page.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Dict

from .. import imagediff as IM

#: Headers on every image answer. `sandbox` with no `allow-*` token: an SVG navigated to directly is
#: an opaque origin with no script, no forms, no top navigation; `default-src 'none'` loads nothing.
IMAGE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
    "Cross-Origin-Resource-Policy": "same-origin",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
}
_STATUS = {"missing": 404, "unreadable": 404, "too_large": 413, "undecodable": 415,
           "unresponsive": 504}
#: error answers are JSON and say so: never sniffed into anything else
_ERR = {"X-Content-Type-Options": "nosniff"}
#: how often a running read looks to see whether the client is still there
POLL_SECS = 0.25


def routes(reg: Any, ctx: Any) -> None:
    from starlette.responses import JSONResponse, Response
    state = ctx.state

    async def watched(request: Any, work: Any) -> Any:
        """Run `work(cancelled)` off the loop and WATCH the client: Starlette does not cancel a plain
        endpoint when the browser goes away (moved to another image, closed the tab), so this polls
        `is_disconnected()` and fires the cancel - a git still being read is then killed, not left
        to a worker thread for ever."""
        gone = threading.Event()
        task = asyncio.ensure_future(asyncio.to_thread(work, gone.is_set))
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=POLL_SECS)
                if done:
                    return task.result()
                if await request.is_disconnected():
                    gone.set()
        except asyncio.CancelledError:
            gone.set()
            raise

    async def info(request: Any):
        from ..nav import PathRefused
        path = ctx.q(request, "path")
        if not path:
            return JSONResponse({"error": "BadRequest", "note": "path is required"},
                                status_code=400, headers=_ERR)
        if state.demo:
            return JSONResponse({"state": "sample", "sample": True, "status": "sample",
                                 "path": path[:1000], "note": "the SAMPLE has no image files"})
        try:
            out = await watched(request, lambda c: IM.pair(
                state.root, path, ctx.q(request, "old_path"), ctx.q(request, "base"),
                ctx.q(request, "head"), c))
        except (PathRefused, ValueError) as exc:     # not an image name, a path outside, a bad rev
            return JSONResponse({"error": type(exc).__name__, "note": str(exc)}, status_code=400,
                                headers=_ERR)
        return JSONResponse(out)

    reg.register("/api/image/info", info, kind="read", feature="image")

    async def raw(request: Any):
        from ..nav import PathRefused
        path = ctx.q(request, "path")
        if not path:
            return JSONResponse({"error": "BadRequest", "note": "path is required"},
                                status_code=400, headers=_ERR)
        if state.demo:
            return JSONResponse({"error": "Missing", "note": "the SAMPLE has no image files"},
                                status_code=404, headers=_ERR)
        try:
            mime, data = await watched(request, lambda c: IM.read_image(
                state.root, path, ctx.q(request, "rev"), c))
        except IM.ImageUnavailable as exc:
            return JSONResponse({"error": "ImageUnavailable", "state": exc.state,
                                 "note": exc.note}, status_code=_STATUS[exc.state], headers=_ERR)
        except (PathRefused, ValueError) as exc:
            return JSONResponse({"error": type(exc).__name__, "note": str(exc)}, status_code=400,
                                headers=_ERR)
        return Response(data, media_type=mime, headers=IMAGE_HEADERS)

    reg.register("/api/image/raw", raw, kind="read", feature="image")
