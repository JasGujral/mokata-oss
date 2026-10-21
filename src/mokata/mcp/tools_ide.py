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
from typing import Any, Dict

from .pagination import DEFAULT_PAGE_LIMIT, paginate
from .registry import _tool

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


@_tool("ide_status", "read")
def ide_status(path: str = ".") -> Dict[str, Any]:
    """Is a `mokata ide` window serving this repo? Returns `running`, its local `url` (the pairing
    token is NEVER returned — run `mokata ide` to print the paired link or re-focus the window),
    `demo` (true = the labelled SAMPLE dataset, nothing real), the focused `session`, how many
    `pages` are attached, and whether the `--ide-bridge` http hooks are set up (`bridge`).
    Read-only: it pings 127.0.0.1 (bounded) and starts nothing. status `not_running` is a clean
    answer, not an error."""
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


@_tool("ide_open", "read")
def ide_open(path: str = ".", file: str = "", line: int = 1, session: str = "") -> Dict[str, Any]:
    """Point the human at code: open `file` (repo-relative) at `line` in the running `mokata ide`
    window, and/or focus `session` (a Claude Code session id) there. A UI round trip — it writes
    nothing and starts no server. The path must be inside the repo (absolute paths, `..` and
    symlinks out are refused). Returns `pages` (how many open pages received it); status `no_page`
    means the window is running but nobody has it open, `not_running` means there is no window —
    both say which command fixes it."""
    from ..ide.launch import _call, running
    from ..ide.nav import PathRefused, contained_rel
    root = _root(path)
    if not file and not session:
        return {"status": "error", "message": "ide_open needs `file` (and optionally `line`) "
                                              "and/or `session`"}
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


@_tool("ide_sessions", "read")
def ide_sessions(path: str = ".", needs_you_only: bool = False, limit: int = DEFAULT_PAGE_LIMIT,
                 offset: int = 0) -> Dict[str, Any]:
    """Every Claude Code session that wrote in this repo (live + past), as the IDE's session browser
    shows it: `status` (live | ended | past, with the `status_basis` it was derived from), files
    and writes, gates fired/blocked for its run, token spend (measured and estimated kept apart),
    ledger links — and `needs_you`, the attention queue SORTED BY RISK (formula in `risk_formula`):
    pending approvals (by proposal id + the `mokata approve <id>` command; the proposal's content is
    never shown), a gate block nothing has moved past, a secret block. Read-only, derived from
    mokata's own stores. PAGED over sessions (`limit` defaults to 50, `limit=0` opts out);
    `needs_you_only=true` lists only the sessions that are waiting on a human."""
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
