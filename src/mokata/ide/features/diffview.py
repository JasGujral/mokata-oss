"""`/api/diff/*` — how the review screen SHOWS a diff (0.0.27 P5, `ID-DIFF-SPLIT` +
`ID-DIFF-INTRALINE`). The work is `mokata.ide.diffview`; these are its doors.

  POST /api/diff/intraline   `{pairs: [[old, new], ...]}` — the changed line pairs IN VIEW (the
                             page sends only the rows it shows): which characters of each changed,
                             at token level. Its cost (tokens left once the common head and tail
                             are trimmed) is measured first: over `SYNC_TOKENS` — or `sync_ms: 0` —
                             it runs as a `diff.intraline` job (202 + handle, progress in tokens,
                             Cancel); the page re-asks with `job` for the finished answer.
  GET  /api/diff/context     `?path=&start=&count=` + where the new side is (`rev=` | `snap=` |
                             `session=` | nothing = the working tree): one PAGE (<= `PAGE_MAX`
                             lines, ~`PAGE_BYTES`; a line over `LINE_BYTES` is cut and listed in
                             `cut`) of the unchanged lines between hunks — "expand context". A
                             `diff.context` job past the sync window (a page deep in a big file).

Both are reads. Nothing is written; no text from a body is ever opened as a path.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Dict

from .. import diffview as DV
from ..diffs import MAX_DIFF_BYTES

#: A pair's text came from a diff the server sent (git output <= MAX_DIFF_BYTES; a snapshot side
#: <= 1 MB), so a body of in-view pairs is bounded by that, with room for JSON escaping.
BODY_MAX = 3 * MAX_DIFF_BYTES + 65536
_SNAP = re.compile(r"^[\w.:-]{1,200}$")
_SESSION = re.compile(r"^[\w.-]{1,128}$")
KIND_INTRA, KIND_CTX = "diff.intraline", "diff.context"


def routes(reg: Any, ctx: Any) -> None:
    from starlette.responses import JSONResponse
    state = ctx.state

    def _handle(j: Any) -> Dict[str, Any]:
        snap = j.snapshot()
        return {"job": j.id, "kind": j.kind, "label": j.label, "state": j.state,
                "done": snap["done"], "total": snap["total"], "unit": snap["unit"],
                "note": snap["note"], "error": snap["error"], "elapsed": snap["elapsed"],
                "partial_ref": snap["partial_ref"], "partial": j.partial}

    # ------------------------------------------------------------------- intra-line
    async def intraline(request: Any):
        from ..bus import read_body
        raw = await read_body(request, BODY_MAX)
        if raw is None:
            return JSONResponse({"error": "TooLarge", "note": "these lines are too large to "
                                 "compare in one request — the page asks for fewer"},
                                status_code=413)
        try:
            b = json.loads(raw.decode("utf-8", "replace") or "{}")
        except ValueError:
            b = None
        if not isinstance(b, dict):
            return JSONResponse({"error": "BadRequest", "note": "a JSON object is required"},
                                status_code=400)
        try:
            pairs = DV._pairs_of(b.get("pairs"))
        except ValueError as exc:
            return JSONResponse({"error": "BadRequest", "note": str(exc)}, status_code=400)
        sync = b.get("sync_ms")
        if sync is not None and (isinstance(sync, bool) or not isinstance(sync, int)):
            return JSONResponse({"error": "BadRequest", "note": "sync_ms must be a whole number"},
                                status_code=400)
        demo = bool(state.demo)
        question = {"route": "/api/diff/intraline", "digest": DV.digest(pairs), "n": len(pairs)}
        want = b.get("job")
        if isinstance(want, str) and want:
            kept = state.jobs.answer(want, KIND_INTRA, question)
            if kept is not None:
                return JSONResponse(kept.result)
        total = await asyncio.to_thread(DV.cost, pairs)
        if total <= DV.SYNC_TOKENS and sync != 0:
            out = await asyncio.to_thread(DV.batch, pairs)
            return JSONResponse({**out, "cost": total, "job": None, "sample": demo})
        state.attach_loop()
        j = state.jobs.start(KIND_INTRA, question,
                             lambda: {**DV.batch(pairs, total), "cost": total, "job": None,
                                      "sample": demo},
                             label=f"intra-line diff · {len(pairs)} line pair(s) · "
                                   f"{total:,} tokens",
                             scope=os.path.basename(state.root),
                             client=request.headers.get("x-mokata-client") or None)
        return JSONResponse(_handle(j), status_code=202)

    reg.register("/api/diff/intraline", intraline, kind="action", feature="diffview")

    # ------------------------------------------------------------------- context pages
    def _int(request: Any, name: str, default: int) -> int:
        raw = ctx.q(request, name)
        if not raw:
            return default
        try:
            v = int(raw)
        except ValueError:
            raise ctx.bad_request(f"{name} must be a whole number") from None
        if v < 1:
            raise ctx.bad_request(f"{name} must be at least 1")
        return v

    def context(request: Any):
        path = ctx.q(request, "path")
        if not path:
            raise ctx.bad_request("path is required")
        start = _int(request, "start", 1)
        count = min(_int(request, "count", 20), DV.PAGE_MAX)
        rev, snap, session = ctx.q(request, "rev"), ctx.q(request, "snap"), ctx.q(request, "session")
        if sum(1 for x in (rev, snap, session) if x) > 1:
            raise ctx.bad_request("name ONE side: rev, snap or session (none = the working tree)")
        if rev:
            from .. import review_context as RC
            rev = RC.check_rev(rev, "rev")
        if snap and not _SNAP.match(snap):
            raise ctx.bad_request("snap must be a snapshot id")
        if session and not _SESSION.match(session):
            raise ctx.bad_request("session must be a session id")
        base = {"path": path[:1000], "start": start, "lines": [], "cut": [], "eof": True,
                "next": None}
        if state.demo:
            return lambda: {**base, "state": "unavailable", "sample": True,
                            "note": "the SAMPLE has no files on disk to expand"}
        from .. import snapshots as S
        from ..nav import PathRefused
        root = state.root
        rel = S.rel_path(root, path)
        if not rel:
            raise PathRefused(S.refusal(root, path or "",
                                        "a repo-relative path inside the repository is required"))

        def work() -> Dict[str, Any]:
            tree = ""
            if session:
                from ... import session_worktrees as SW
                from ..diffs import _chain
                for sid in _chain(root, session):
                    bnd = SW.binding(root, sid)
                    if bnd and os.path.isdir(str(bnd["path"])):
                        tree = str(bnd["path"])
                        break
                if not tree:
                    return {**base, "path": rel, "state": "unavailable", "sample": False,
                            "note": f"session {session[:12]} has no worktree to read"}
            try:
                got = DV.context_page(root, rel, start, count, rev=rev, snap=snap, tree=tree)
            except DV.ContextUnavailable as exc:
                return {**base, "path": rel, "state": "unavailable", "sample": False,
                        "note": str(exc)}
            return {**got, "state": "ok", "sample": False}
        return work

    reg.register("/api/diff/context", context, kind="json", feature="diffview", job=KIND_CTX,
                 job_label=lambda r: f"diff context · {ctx.q(r, 'path')[:120]}")
