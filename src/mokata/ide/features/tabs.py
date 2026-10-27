"""`/api/workspace` — the IDE's tabs + session restore (0.0.26 P2, `ID-TABS`; doc 109 §13.4).

  GET  /api/workspace   the saved workspace (tabs, active tab, closed tabs, expanded dirs, scroll
                        positions, mode/panel) AND, computed now, what happened while the window
                        was away: the focused session, its run's pipeline position, and the
                        approvals waiting on a person — the "since you left" line px0 has no
                        equivalent of. `refused` names a saved file mokata would not read.
  POST /api/workspace   save it (normalised + bounded by `ide/workspace.py`). The focused session
                        is recorded from the SERVER's focus, never the page's word.

`/api/session` is the core's per-session diff — a distinct path, as the brief asked.

Shared CLI <-> IDE: the file is per repo on the server side, so a `mokata ide` relaunch and a
`/mokata:review-window` from a session both re-open the same tabs; a server started WITHOUT a
session to focus adopts the saved one (re-focus), one started for a session focuses that session.
On SAMPLE data (`--demo`) nothing is written to disk: the workspace lives in memory.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

MAX_BODY = 256 * 1024
MAX_PENDING = 20


def _run_of(state: Any, focus: Optional[str], notes: List[str]) -> Optional[str]:
    if not focus:
        return None
    try:
        row = state.source.state(focus).get("focus_row") or {}   # P33: the focused row only
    except Exception as exc:  # noqa: BLE001 — said in `live.notes`, never a silent "no run"
        notes.append(f"could not read the sessions: {type(exc).__name__}: {str(exc)[:160]}")
        return None
    return str(row["run"]) if row.get("run") else None


def _position(state: Any, run: Optional[str]) -> Optional[Dict[str, Any]]:
    if not run:
        return None
    try:
        lc = state.source.lifecycle(run)
    except Exception as exc:  # noqa: BLE001 — an unknown / unreadable run is said, not raised
        return {"run": run, "current": None, "finished": False, "note": str(exc)[:200]}
    return {"run": lc.get("run", run), "current": lc.get("current"),
            "finished": bool(lc.get("finished")), "regressed": bool(lc.get("regressed"))}


def _pending(state: Any, run: Optional[str], notes: List[str]) -> List[Dict[str, Any]]:
    """Approvals waiting on a PERSON (approved-but-unused ones wait on the agent). Surfaced by id,
    tool, run and the command to answer — never the proposal's content (the `awaiting` rule)."""
    if state.demo:
        return []
    import time
    from ... import approval
    from ...awaiting import APPROVE_CMD
    now = time.time()
    try:
        rows = [p for p in approval.pending(state.root, now) if not p.approved]
    except Exception as exc:  # noqa: BLE001 — said in `live.notes`: "none waiting" would be a lie
        notes.append(f"could not read the approvals: {type(exc).__name__}: {str(exc)[:160]}")
        return []
    if run:
        mine = [p for p in rows if p.run_id == run]
        rows = mine or rows
    rows.sort(key=lambda p: p.created_at)
    return [{"proposal_id": p.proposal_id, "tool": p.tool, "run": p.run_id,
             "command": APPROVE_CMD.format(proposal_id=p.proposal_id),
             "expires_in": p.expires_in(now)} for p in rows[:MAX_PENDING]]


def routes(reg, ctx) -> None:
    from .. import workspace as W
    from starlette.responses import JSONResponse

    state = ctx.state
    box: Dict[str, Any] = {"ws": W.empty(), "refused": None, "blocked": False}
    if not state.demo:
        box["ws"], box["refused"] = W.load(state.root)
        box["blocked"] = bool(box["refused"]) and "could not move it aside" in box["refused"]
        # Re-focus: a window started with no session to focus opens on the saved one.
        if state.focus is None and box["ws"].get("focus"):
            state.focus = box["ws"]["focus"]

    def read(_request) -> Dict[str, Any]:
        ws = dict(box["ws"])
        focus = state.focus
        notes: List[str] = []
        run = _run_of(state, focus, notes) or (ws.get("run") if ws.get("focus") == focus else None)
        return {**ws, "sample": bool(state.demo), "refused": box["refused"],
                "live": {"focus": focus, "run": _position(state, run),
                         "pending": _pending(state, run, notes), "notes": notes}}

    async def write(request):
        raw = await request.body()
        if len(raw) > MAX_BODY:
            return JSONResponse({"error": "BadRequest",
                                 "note": f"a workspace is at most {MAX_BODY} bytes"}, status_code=400)
        try:
            body = json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeDecodeError, ValueError):
            body = None
        if not isinstance(body, dict) or not isinstance(body.get("tabs"), list):
            return JSONResponse({"error": "BadRequest",
                                 "note": "a workspace is a JSON object with a `tabs` list"},
                                status_code=400)
        if box["blocked"]:
            return JSONResponse({"error": "Refused", "note": box["refused"]}, status_code=409)
        body = {**body, "focus": state.focus, "schema": W.SCHEMA}
        clean, dropped = W.normalize(body)
        if state.demo:
            box["ws"] = clean
        else:
            import asyncio
            box["ws"] = await asyncio.to_thread(W.save, state.root, clean)
        return JSONResponse({"saved": True, "tabs": len(clean["tabs"]), "dropped": dropped,
                             "sample": bool(state.demo)})

    reg.register("/api/workspace", read, kind="json", feature="tabs")
    reg.register("/api/workspace", write, kind="action", feature="tabs")
