"""Delimited text as a table (0.0.27 P8 — ID-CSV-VIEW). The work is `mokata.ide.tableview`; these
are its doors. Every cell is TEXT in a JSON string, never markup.

  GET  /api/table/info?path=            dialect, header, size, and the row count if already known
  GET  /api/table/rows?path=&row=&count= a page of data rows by index (read from a byte offset)
  GET  /api/table/count?path=           the whole-file row count, as the visible `table.count` job
  GET  /api/table/view?path=&scol=&sdir=&fcol=&fop=&fval=   a FILTERED and/or SORTED view of the
                                        whole file (`table.view` job: progress, Cancel, external sort)
  GET  /api/table/view/rows?id=&from=&count=   a page of that view
  GET  /api/table/diff?path=&base=&head=&key=   cell-level diff of a changed file (`table.diff` job)
  GET  /api/table/diff/rows?id=&from=&count=    a page of that diff's changes
  POST /api/table/export                {rows, format} -> the rows as CSV / TSV with the
                                        CSV-injection guard applied (the copy / export door)

All reads. Nothing is written to the repo; a diff spools to a temp file that is removed at exit."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from typing import Any, Dict

from starlette.responses import JSONResponse

from .. import tableview as T


def routes(reg, ctx) -> None:
    state = ctx.state

    def _int(request, name: str, default: int, low: int = 0) -> int:
        raw = ctx.q(request, name)
        if not raw:
            return default
        try:
            v = int(raw)
        except ValueError:
            raise ctx.bad_request(f"{name} must be a whole number") from None
        if v < low:
            raise ctx.bad_request(f"{name} must be at least {low}")
        return v

    def sample(path: str) -> Dict[str, Any]:
        return {"sample": True, "path": path, "state": "sample",
                "note": "the SAMPLE has no delimited files"}

    def resolve(path: str):
        """(rel, native path, contained opener) of a repo file — or a dict answer saying why not.
        Every read of the file goes through the opener (the CONTAINED open), never a bare path."""
        from .. import snapshots as S
        from ..nav import PathRefused
        from ...repo_paths import path_of
        rel = S.rel_path(state.root, path or "") if path else None
        if not rel:
            raise PathRefused(S.refusal(state.root, path or "",
                                        "a repo-relative path inside the repository is required"))
        return (rel, path_of(state.root, rel), T.contained_opener(state.root, rel)), None

    def table_of(request):
        path = ctx.q(request, "path")
        if not path:
            raise ctx.bad_request("path is required")
        if state.demo:
            return None, sample(path)
        got, why = resolve(path[:1000])
        if why is not None:
            return None, why
        try:
            t = T.open_table(got[1], got[0], got[2])
            t.head()
            return t, None
        except T.NotATable as exc:
            return None, {"path": got[0], "state": exc.state, "note": exc.note}

    def info(request) -> Dict[str, Any]:
        t, why = table_of(request)
        if why is not None:
            return why
        return {**t.info(), "state": "ok", "sample": False, "cache": T.cache_info()}
    reg.register("/api/table/info", info, kind="json", feature="table")

    def rows(request) -> Dict[str, Any]:
        row, cnt = _int(request, "row", 0), _int(request, "count", T.PAGE_DEFAULT, 1)
        t, why = table_of(request)
        if why is not None:
            return why
        try:
            return {**t.page(row, cnt), "path": t.rel, "state": "ok", "sample": False}
        except T.NotATable as exc:
            return {"path": t.rel, "state": exc.state, "note": exc.note}
    reg.register("/api/table/rows", rows, kind="json", feature="table")

    def count(request):
        t, why = table_of(request)
        if why is not None:
            return lambda: why
        def work():
            try:
                return {**t.count(), "path": t.rel, "state": "ok", "sample": False}
            except T.NotATable as exc:
                return {"path": t.rel, "state": exc.state, "note": exc.note}
        return work
    reg.register("/api/table/count", count, kind="json", feature="table", job="table.count",
                 job_label=lambda r: f"counting rows · {ctx.q(r, 'path')[:120]}")

    def view(request):
        scol_raw, fcol_raw = ctx.q(request, "scol"), ctx.q(request, "fcol")
        scol = _int(request, "scol", -1, -1) if scol_raw != "" else -1
        sdir = -1 if ctx.q(request, "sdir") in ("-1", "desc") else 1
        fop = ctx.q(request, "fop")
        if fop and fop not in T.FILTER_OPS:
            raise ctx.bad_request("fop must be one of " + ", ".join(T.FILTER_OPS))
        fcol = _int(request, "fcol", -1, -1) if fcol_raw != "" else -1
        fval = ctx.q(request, "fval")[:2000]
        if scol < 0 and not fop:
            raise ctx.bad_request("a view needs a sort column (scol) or a filter (fop)")
        t, why = table_of(request)
        if why is not None:
            return lambda: why
        vid = hashlib.sha1("\0".join((t.path, str(t.mtime), ctx.q(request, "scol"), str(sdir), fop,
                                      str(fcol), fval)).encode()).hexdigest()[:16] + f"-{os.urandom(3).hex()}"

        def work() -> Dict[str, Any]:
            try:
                if scol >= t.width and scol >= 0 or fcol >= t.width:
                    return {"path": t.rel, "state": "bad-column", "note": f"the file has {t.width} columns"}
                s = T.make_view(t, vid, fcol, fop, fval, scol if scol >= 0 else None, sdir)
            except T.NotATable as exc:
                return {"path": t.rel, "state": exc.state, "note": exc.note}
            return {"path": t.rel, "state": "ok", "sample": False, **s}
        return work
    reg.register("/api/table/view", view, kind="json", feature="table", job="table.view",
                 job_label=lambda r: f"table view · {ctx.q(r, 'path')[:120]}")

    def view_rows(request) -> Dict[str, Any]:
        start, count = _int(request, "from", 0), _int(request, "count", T.PAGE_DEFAULT, 1)
        d = T.get_diff(ctx.q(request, "id")[:80])
        if d is None:
            return {"state": "gone", "note": "that view is no longer held — build it again"}
        ents = d.page(start, count)
        flags = {str(start + i): e["f"] for i, e in enumerate(ents) if e["f"]}
        return {"state": "ok", "id": d.id, "from": start, "total": len(d.at),
                "rows": [e["c"] for e in ents], "nums": [e["n"] for e in ents], "flags": flags}
    reg.register("/api/table/view/rows", view_rows, kind="json", feature="table")

    def _rev(raw: str, what: str) -> str:
        from .. import review_context as RC
        return RC.check_rev(raw, what) if raw else ""

    def diff(request):
        path = ctx.q(request, "path")
        if not path:
            raise ctx.bad_request("path is required")
        base, head = _rev(ctx.q(request, "base"), "base") or "HEAD", _rev(ctx.q(request, "head"), "head")
        key = ctx.q(request, "key")[:300]
        old_path = ctx.q(request, "old_path")[:1000] or path
        if state.demo:
            return lambda: sample(path)
        from .. import snapshots as S
        from ..nav import PathRefused
        rel, old_rel = S.rel_path(state.root, path), S.rel_path(state.root, old_path)
        if not rel or not old_rel:
            raise PathRefused(S.refusal(state.root, old_path if rel else path,
                                        "a repo-relative path inside the repository is required"))
        root = state.root
        did = hashlib.sha1("\0".join((root, rel, old_rel, base, head, key)).encode()
                           ).hexdigest()[:16] + f"-{os.urandom(3).hex()}"

        def work() -> Dict[str, Any]:
            try:
                summary = T.diff_files(root, rel, old_rel, base, head, key, did)
            except KeyError:
                return {"path": rel, "state": "bad-key", "key": key,
                        "note": f"{key!r} is not a column of both versions"}
            except T.NotATable as exc:
                return {"path": rel, "state": exc.state, "note": exc.note}
            d = T.get_diff(did)
            return {"path": rel, "state": "ok", "id": did, "base": base, "head": head,
                    "summary": summary, "from": 0, "changes": d.page(0, T.PAGE_DEFAULT),
                    "sample": False}
        return work
    reg.register("/api/table/diff", diff, kind="json", feature="table", job="table.diff",
                 job_label=lambda r: f"table diff · {ctx.q(r, 'path')[:120]}")

    def diff_rows(request) -> Dict[str, Any]:
        start, count = _int(request, "from", 0), _int(request, "count", T.PAGE_DEFAULT, 1)
        d = T.get_diff(ctx.q(request, "id")[:80])
        if d is None:
            return {"state": "gone", "note": "that diff is no longer held — compare again"}
        return {"state": "ok", "id": d.id, "from": start, "total": len(d.at),
                "changes": d.page(start, count)}
    reg.register("/api/table/diff/rows", diff_rows, kind="json", feature="table")

    async def export(request):
        from ..bus import read_body
        raw = await read_body(request, T.EXPORT_MAX_BYTES)
        if raw is None:
            return JSONResponse({"error": "TooLarge", "note": "too many cells to copy at once — "
                                 "select fewer rows"}, status_code=413)
        try:
            b = json.loads(raw.decode("utf-8", "replace") or "{}")
        except ValueError:
            b = None
        rows_ = b.get("rows") if isinstance(b, dict) else None
        fmt = b.get("format") if isinstance(b, dict) else None
        if not isinstance(rows_, list) or fmt not in ("csv", "tsv") or \
                not all(isinstance(r, list) for r in rows_):
            return JSONResponse({"error": "BadRequest", "note": "want {rows: [[cell, ...], ...], "
                                 "format: 'csv' | 'tsv'}"}, status_code=400)
        text = await asyncio.to_thread(T.to_text, rows_, fmt)
        return JSONResponse({"text": text, "format": fmt, "rows": len(rows_), "guarded": True})
    reg.register("/api/table/export", export, kind="action", feature="table")
