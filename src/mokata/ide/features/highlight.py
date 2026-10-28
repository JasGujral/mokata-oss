"""Syntax highlighting routes (0.0.26 P1 — ID-SYNTAX-HIGHLIGHT). The work is `mokata.ide.highlight`;
these are its doors. Every answer is TOKEN SPANS (`[[cls, text], ...]` per line), never markup.

  GET  /api/highlight?path=&start=&end=   one line window of a repo file (chunked, LRU-cached)
  POST /api/highlight/diff                 a file's diff hunks, each line coloured in its own side
  POST /api/highlight/snippet              a spec / AC code block (fence language, else detected)
  GET  /api/highlight/info                 is Pygments here, how many lexers, the cache bounds

All reads: nothing is written, nothing runs at idle, the SAMPLE is served from its own invented
files (no disk). The POST bodies are read bounded and carry no path that is opened."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict

from .. import highlight as H

BODY_MAX = H.SNIPPET_MAX_BYTES * 2 + 65536


def routes(reg, ctx) -> None:
    from starlette.responses import JSONResponse

    def _int(request, name: str, default: int) -> int:
        raw = ctx.q(request, name)
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            raise ctx.bad_request(f"{name} must be a line number") from None

    def window(request) -> Dict[str, Any]:
        path = ctx.q(request, "path")
        if not path:
            raise ctx.bad_request("path is required")
        start = _int(request, "start", 1)
        end = _int(request, "end", start + H.CHUNK - 1)
        if start < 1 or end < start:
            raise ctx.bad_request("want 1 <= start <= end")
        st = ctx.state
        if st.demo:
            text = st.source._FILES.get(path)
            if text is None:
                return {**H.not_served(path, "not in the SAMPLE"), "sample": True}
            return {**H.window_text(path, text, start, end, ("sample", path)), "sample": True}
        return {**H.window(st.root, path[:1000], start, end), "sample": False}

    reg.register("/api/highlight", window, kind="json", feature="highlight")

    def info(_request) -> Dict[str, Any]:
        ok = H.available()
        return {"available": ok, "lexers": H.lexer_count() if ok else 0,
                "install": "" if ok else H.INSTALL, "classes": list(H.TOKEN_CLASSES),
                "chunk": H.CHUNK, "lead": H.LEAD, "max_window": H.MAX_WINDOW,
                "hl_max_bytes": H.HL_MAX_BYTES, "read_max_bytes": H.READ_MAX_BYTES,
                "long_line": H.LONG_LINE, "cache": H.cache_info()}

    reg.register("/api/highlight/info", info, kind="json", feature="highlight")

    async def _read(request):
        from ..bus import read_body
        raw = await read_body(request, BODY_MAX)
        if raw is None:
            return None, JSONResponse({"error": "TooLarge", "note": "the body is too large to "
                                       "highlight — shown as plain text"}, status_code=413)
        try:
            b = json.loads(raw.decode("utf-8", "replace") or "{}")
        except ValueError:
            b = None
        if not isinstance(b, dict):
            return None, JSONResponse({"error": "BadRequest", "note": "a JSON object is "
                                       "required"}, status_code=400)
        return b, None

    async def diff(request):
        b, bad = await _read(request)
        if bad is not None:
            return bad
        path, hunks = b.get("path"), b.get("hunks")
        if not isinstance(path, str) or not path or not isinstance(hunks, list) or \
                not all(isinstance(h, dict) and isinstance(h.get("lines"), list) for h in hunks):
            return JSONResponse({"error": "BadRequest", "note": "want {path, hunks: [{lines: "
                                 "[[op, old, new, text], ...]}]}"}, status_code=400)
        clean = [{"lines": [ln for ln in h["lines"] if isinstance(ln, list) and len(ln) >= 4
                            and ln[0] in (" ", "+", "-") and isinstance(ln[3], str)]}
                 for h in hunks]
        out = await asyncio.to_thread(H.diff, path[:1000], clean)
        return JSONResponse({**out, "sample": bool(ctx.state.demo)})

    reg.register("/api/highlight/diff", diff, kind="action", feature="highlight")

    async def snippet(request):
        b, bad = await _read(request)
        if bad is not None:
            return bad
        text = b.get("text")
        if not isinstance(text, str):
            return JSONResponse({"error": "BadRequest", "note": "want {text, lang?, path?}"},
                                status_code=400)
        lang = b.get("lang") if isinstance(b.get("lang"), str) else ""
        path = b.get("path") if isinstance(b.get("path"), str) else ""
        out = await asyncio.to_thread(H.snippet, text, lang[:64], path[:1000])
        return JSONResponse({**out, "sample": bool(ctx.state.demo)})

    reg.register("/api/highlight/snippet", snippet, kind="action", feature="highlight")
