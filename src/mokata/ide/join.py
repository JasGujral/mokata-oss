"""ID.S3 — session join: the IDE attaches to a live session (0.0.25 Lane ID2; doc 109 §4).

Doc 109 §4: "one session, one store, events both ways. The IDE joins the CLI's `session_id`
through `session_registry`, the same registry `mokata windows` reads."

So the list of joinable sessions is the UNION of the two records mokata already keeps, never a new
one:
  * `session_registry` — every window, with its pid liveness (`alive`), phase, scope and branch;
  * `ide.sessions` — every session that wrote something mokata snapshotted (or holds a run claim).

Joining one makes it the window's focus (its run, files, timeline — what `/mokata:review-window`
already does), turns follow mode on for it in every attached page, and makes it the default
target of context the human sends (`ide.context`). The join is the window's state, not a store
write: nothing durable changes, and the session itself is not touched.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from ..errors import MokataError


def _registry(root: str) -> List[Any]:
    """`mokata windows`'s rows, read WITHOUT pruning (a GET has no side effects)."""
    from ..config import Surface
    from ..session_registry import list_sessions
    try:
        if not Surface.is_initialized(root):
            return []
        surface = Surface.load(root)
    except (MokataError, OSError, ValueError):
        return []
    return list_sessions(surface, prune=False)


def joinable(root: str) -> List[Dict[str, Any]]:
    """Every session the window can join: live windows first, then most recently active."""
    from . import sessions as S
    rows: Dict[str, Dict[str, Any]] = {}
    for e in _registry(root):
        rows[e.session_id] = {"id": e.session_id, "short": e.session_id[:8], "alive": e.alive,
                              "phase": e.phase or "", "scope": e.scope or "",
                              "branch": e.branch or "", "last_seen": e.last_seen or "",
                              "writes": 0, "files": 0, "claim": "", "last": 0.0,
                              "known_by": ["registry"]}
    for r in S.sessions(root):
        row = rows.setdefault(r.id, {"id": r.id, "short": r.id[:8], "alive": None, "phase": "",
                                     "scope": "", "branch": "", "last_seen": "", "known_by": []})
        row.update({"writes": r.writes, "files": len(r.files), "claim": r.claim, "last": r.last})
        if not row["last_seen"] and r.last:
            row["last_seen"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(r.last))
        row["known_by"].append("snapshots")
    return sorted(rows.values(), key=lambda r: (r["alive"] is not True, -(r.get("last") or 0)))


def find(root: str, session: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(session, str) or not session:
        return None
    for r in joinable(root):
        if r["id"] == session:
            return r
    return None


__all__ = ["joinable", "find"]
