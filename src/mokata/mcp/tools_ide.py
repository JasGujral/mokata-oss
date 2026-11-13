"""IDE tools — ID.S3's `ide_*` MCP tools (0.0.25 Lane ID3; doc 109 §4.2, §17.1).

The agent's side of the CLI↔IDE bridge. All three are READ-kind (no durable write, nothing gated,
nothing to approve), registered at the READ tail by `tools_read` so every existing registry
position is unchanged:

  * `ide_status`   — is a `mokata ide` window serving THIS repo, where, in which mode, and is a
                     page attached? Answers from the server's own ping, never from the record file
                     alone (a crashed server's record reads as not running).
  * `ide_open`     — doc 109 §4.2: "the agent points the human at code". Opens `file` at `line`
                     and/or focuses `session` in the running window. A UI ROUND TRIP: the path is
                     contained on this side and again by the server, then published to the page's
                     event stream. It writes nothing, starts nothing (no server is spawned from an
                     MCP call) and reports how many pages saw it, so "nobody is looking" is said.
  * `ide_sessions` — the neutral twin of the browser's session list + needs-you queue
                     (ID-SESSION-BROWSER / ID-NEEDS-YOU), from the same `ide.session_views`
                     derivation the page draws, for a harness with no browser open.

The pairing token never leaves the server: `ide_status` names the command that prints the link, not
the link (a tool result can be retained in a transcript). Every network call is to 127.0.0.1 and
bounded (`IDE_CALL_TIMEOUT`), inside the `_serve` front-door budget.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
from typing import Annotated, Any, Dict

from .pagination import DEFAULT_PAGE_LIMIT, paginate
from .registry import _tool
from .tool_annotations import Field
from .validation import ValidationError

_PATH = "Repo root the tool operates on (default: the server's working directory)."

IDE_CALL_TIMEOUT = 2.0
OPEN_CMD = "mokata ide"
NOT_RUNNING = "not_running"
NO_PAGE = "no_page"


def _root(path: str) -> str:
    """The repo root exactly as `mokata ide` resolves it, so the server record is the same file."""
    from ..config import find_project_root
    return os.path.realpath(find_project_root(path or "."))


def _not_running(root: str) -> Dict[str, Any]:
    return {"status": NOT_RUNNING, "running": False, "repo": root,
            "message": "no mokata ide window is serving this repository",
            "hint": f"run `{OPEN_CMD}` in a terminal (or `/mokata:review-window` in a session) — "
                    "it opens the window, or re-focuses the open one"}


# Provenance (developers): ID.S3 (0.0.25 Lane ID3). Read-only: it pings 127.0.0.1 (bounded by
# `IDE_CALL_TIMEOUT`) and starts nothing; `not_running` is a clean answer, not an error. It answers
# from the server's own ping, never from the record file alone (a crashed server's record reads as
# not running). The pairing token never leaves the server: the result names the command that prints
# the link, not the link (a tool result can be retained in a transcript).
@_tool("ide_status", "read")
def ide_status(path: Annotated[str, Field(description=_PATH)] = ".") -> Dict[str, Any]:
    """Find out whether a `mokata ide` window is serving this repo, and where.

    Read-only: it pings the local window and starts nothing. `not_running` is a normal answer. The pairing token is never returned; run `mokata ide` to print the paired link.

    Returns: `running`, local `url`, `demo` (true means labelled sample data), focused `session`, attached `pages`, and `bridge` hook status.
    Errors: none; an absent window is reported as `not_running`.
    """
    from ..ide.bus import read_bridge
    from ..ide.launch import _call, running
    root = _root(path)
    bridge = read_bridge(root)
    bridge_view = {"wired": bool(bridge), "port": bridge["port"] if bridge else None}
    rec = running(root)
    if not rec:
        return {**_not_running(root), "bridge": bridge_view}
    port = int(rec.get("port") or 0)
    pong = _call(port, str(rec.get("token") or ""), "/api/ping", timeout=IDE_CALL_TIMEOUT) or {}
    return {"status": "ok", "running": True, "repo": root, "url": f"http://127.0.0.1:{port}/",
            "port": port, "pid": pong.get("pid"), "demo": bool(rec.get("demo")),
            "session": pong.get("focus"), "pages": pong.get("pages"),
            "bridge": dict(bridge_view, reaches_this_window=bool(bridge) and bridge["port"] == port),
            "pair": f"run `{OPEN_CMD}` to print the paired link (the token is never returned here)"}


# Provenance (developers): doc 109 §4.2 "the agent points the human at code". A UI round trip: the
# path is contained on this side and again by the server, then published to the page's event stream.
# It writes nothing, starts nothing (no server is spawned from an MCP call) and reports how many
# pages saw it, so "nobody is looking" is said. The window not accepting the open is a genuine
# mokata-side fault, so that one return stays `status: "error"`.
@_tool("ide_open", "read")
def ide_open(
        path: Annotated[str, Field(description=_PATH)] = ".",
        file: Annotated[str, Field(
            description="Repo-relative file to open; absolute paths and `..` are refused.")] = "",
        line: Annotated[int, Field(description="Line to scroll to, from 1.")] = 1,
        session: Annotated[str, Field(
            description="Claude Code session id to focus in the window.")] = "",
        ) -> Dict[str, Any]:
    """Point the person at a file or session by opening it in the running `mokata ide` window.

    Give `file` (with optional `line`) and/or `session`. The path must stay inside the repo. It writes nothing and starts no window; run `mokata ide` first if none is open.

    Returns: `ok` with `pages` that received it; `no_page` when the window has no open page; `not_running` when there is no window.
    Errors: refused (invalid_input) when neither `file` nor `session` is given; refused when `file` escapes the repo; error when the window rejects the open.
    """
    from ..ide.launch import _call, running
    from ..ide.nav import PathRefused, contained_rel
    root = _root(path)
    if not file and not session:
        raise ValidationError("file", "or `session` is required: name a file (and optionally a "
                              "line) and/or a session to open")
    body: Dict[str, Any] = {}
    if file:
        try:
            body["path"] = contained_rel(root, file)
        except PathRefused as exc:
            return {"status": "refused", "file": file, "message": f"ide_open: {exc}"}
        try:
            body["line"] = max(1, int(line or 1))
        except (TypeError, ValueError):
            body["line"] = 1
    if session:
        body["session"] = str(session)[:200]
    rec = running(root)
    if not rec:
        return _not_running(root)
    got = _call(int(rec.get("port") or 0), str(rec.get("token") or ""), "/api/open", body,
                timeout=IDE_CALL_TIMEOUT)
    if not got or got.get("error"):
        return {"status": "error", "message": "the mokata ide window did not accept the open "
                f"({(got or {}).get('note') or 'no answer'})", "hint": f"re-run `{OPEN_CMD}`"}
    pages = int(got.get("pages") or 0)
    out: Dict[str, Any] = {"status": "ok" if pages else NO_PAGE, "pages": pages,
                           "path": got.get("path"), "line": got.get("line"),
                           "session": got.get("focus"), "demo": bool(got.get("demo"))}
    if pages:
        what = f"{out['path']}:{out['line']}" if out["path"] else f"session {out['session']}"
        out["message"] = f"opened {what} in mokata ide ({pages} page(s))"
    else:
        out["message"] = ("mokata ide is running but no page is open, so nobody saw it — "
                          f"run `{OPEN_CMD}` to open the window (it re-focuses the running one)")
    return out


# Provenance (developers): ID-SESSION-BROWSER / ID-NEEDS-YOU. The neutral twin of the browser's
# session list and needs-you queue, from the same `ide.session_views` derivation the page draws, for
# a harness with no browser open. Pending approvals are shown by proposal id and the
# `mokata approve <id>` command; the proposal's content is never shown. PAGED over sessions
# (`limit` defaults to 50, `limit=0` opts out).
@_tool("ide_sessions", "read")
def ide_sessions(
        path: Annotated[str, Field(description=_PATH)] = ".",
        needs_you_only: Annotated[bool, Field(
            description="List only sessions waiting on a human.")] = False,
        limit: Annotated[int, Field(
            description="Sessions per page; 0 returns all.")] = DEFAULT_PAGE_LIMIT,
        offset: Annotated[int, Field(description="Sessions to skip, for paging.")] = 0,
        ) -> Dict[str, Any]:
    """List every Claude Code session that wrote in this repo, with the ones waiting on a human first.

    Each row has its status (live, ended or past), writes, gates fired and blocked, token spend and ledger links. `needs_you` is the attention queue sorted by risk: pending approvals, an unresolved gate block, a secret block. Read-only.

    Returns: a page of `sessions` plus `needs_you`, `waiting` and `risk_formula`.
    Errors: none; an empty repo returns an empty list with a message.
    """
    from ..ide import session_views as SV
    root = _root(path)
    data = SV.browser(root)
    rows = [r for r in data["sessions"] if r["needs_you"]] if needs_you_only else data["sessions"]
    out = paginate(rows, key="sessions", limit=limit, offset=offset)
    out.update(needs_you=data["needs_you"], waiting=data["waiting"],
               risk_formula=data["risk_formula"], reads=data["reads"])
    if not data["sessions"]:
        out["message"] = ("no session has written in this repo yet — a session appears once "
                          "Claude Code writes a file here")
    elif not data["needs_you"]:
        out["message"] = "nothing is waiting on you ✓"
    return out


__all__ = ["ide_status", "ide_open", "ide_sessions", "IDE_CALL_TIMEOUT"]
