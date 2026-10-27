"""`/api/sessions/*` — the Sessions screen (S1): EVERY Claude Code session that ran in this repo, and
what each one changed (0.0.26 P33 `SESSION-INDEX+SCREEN`; PRD F-2, R6–R9, R12, R13).

  GET /api/sessions/list?offset=&limit=&q=&filter=
        one page of the ONE session list (`ide.sessions.page`): first prompt, start, duration,
        live/ended (+ its basis), branch / worktree, run + stage, files, resumed chain. Newest
        first, paginated (never capped); `q` matches prompt, file, branch, run, id and date;
        `filter` = all | live | needs | worktree | changed | nochange.
  GET /api/sessions/stats?ids=a,b,…
        files and +/− of the visible rows, from the one diff source (`diffs.session_diff`) — a
        job (`sessions.stats`) when it takes longer than the sync window.
  GET /api/session/write?snap=
        ONE write: the previous capture of the same file by the same session → this capture (R9).

The changed files and diffs of a session come from `GET /api/session?id=` (core) — the same
`diffs.session_diff` the Worktrees panel's Diff and the Review pane read (G1/G17).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from typing import Any, Dict, List

MAX_STATS_IDS = 100


def routes(reg: Any, ctx: Any) -> None:
    from ..sessions import FILTERS, PAGE_MAX

    state = ctx.state

    def _int(request: Any, name: str, default: int, lo: int, hi: int) -> int:
        raw = ctx.q(request, name)
        if not raw:
            return default
        if not raw.isdigit() or len(raw) > 7:
            raise ctx.bad_request(f"{name} must be a whole number")
        return max(lo, min(hi, int(raw)))

    def listing(request: Any) -> Dict[str, Any]:
        filt = ctx.q(request, "filter") or "all"
        if filt not in FILTERS:
            raise ctx.bad_request("filter must be one of " + ", ".join(FILTERS))
        offset = _int(request, "offset", 0, 0, 10 ** 7)
        limit = _int(request, "limit", 50, 1, PAGE_MAX)
        q = ctx.q(request, "q")[:200]
        needs: List[str] = []
        if filt == "needs" and not state.demo:
            from ..session_views import needs_you
            needs = [x["session"] for x in needs_you(state.root,
                                                     bus_events=state.bus.recent())["needs_you"]
                     if x.get("session")]
        return state.source.session_list(offset, limit, q, filt, needs)

    def stats(request: Any):
        ids = [i for i in ctx.q(request, "ids").split(",") if i][:MAX_STATS_IDS]
        if not ids or any(len(i) > 200 for i in ids):
            raise ctx.bad_request("ids is required: comma-separated session ids")
        if state.demo:
            return lambda: {"sample": True, "stats": {
                i: {"files": 2, "added": 5, "removed": 1, "too_large": 0, "basis": "SAMPLE",
                    "note": ""} for i in ids}}
        from ..sessions import stats as compute
        return lambda: compute(state.root, ids)

    def write(request: Any) -> Dict[str, Any]:
        snap = ctx.q(request, "snap")
        if not snap or len(snap) > 200:
            raise ctx.bad_request("snap is required: a snapshot id from the timeline")
        return state.source.write(snap)

    reg.register("/api/sessions/list", listing, kind="json", feature="sessionindex")
    reg.register("/api/sessions/stats", stats, kind="json", feature="sessionindex",
                 job="sessions.stats", job_label=lambda r: "session +/− · "
                 + str(len(ctx.q(r, "ids").split(","))) + " session(s)")
    reg.register("/api/session/write", write, kind="json", feature="sessionindex")
