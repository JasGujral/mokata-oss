"""`POST /api/locate` — `path:line:col` -> the workspace file to open (0.0.27 P11).

One door for every in-IDE `path:line:col` (a pasted palette entry, a cite P7/P46 print): the page
sends the text, the server parses it with `ide/locator.py` (Windows drive letters included) and
answers the contained, repo-relative path with the line and column — or refuses a path outside the
workspace. A read: nothing is written, nothing is opened server-side.
"""

from __future__ import annotations

import asyncio
from typing import Any


def routes(reg, ctx) -> None:
    state = ctx.state

    async def locate(request) -> Any:
        from starlette.responses import JSONResponse
        from ..locator import resolve
        from ..nav import PathRefused
        b = await ctx.body(request)
        text = b.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 4096:
            return JSONResponse({"error": "BadRequest", "note": "send {text: 'path:line:col'}"},
                                status_code=400)
        if state.demo:
            return JSONResponse({"error": "PathRefused",
                                 "note": "SAMPLE data has no files to open"}, status_code=400)
        try:
            loc = await asyncio.to_thread(resolve, state.root, text)
        except PathRefused as exc:
            return JSONResponse({"error": "PathRefused", "note": str(exc)}, status_code=400)
        return JSONResponse({"path": loc.path, "line": loc.line, "col": loc.col})

    reg.register("/api/locate", locate, kind="action", feature="locate")
