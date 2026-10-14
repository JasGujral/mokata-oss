"""Who wrote what — the sessions the review window can focus on (0.0.22 E2).

A session appears here once it has written something mokata snapshotted, or holds a run claim
(C1). Each carries its run, the files it wrote, and when it was active. Parallel sessions are
separate rows by construction: everything is keyed by Claude Code's session id, never by "the
active run".
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import snapshots as S


@dataclass
class SessionRow:
    id: str
    run: str = ""
    files: List[str] = field(default_factory=list)
    writes: int = 0
    first: float = 0.0
    last: float = 0.0
    claim: str = ""                 # bound | open | "" (from C1)

    def facts(self) -> Dict:
        return {"id": self.id, "short": self.id[:8], "run": self.run, "files": self.files,
                "writes": self.writes, "first": self.first, "last": self.last,
                "claim": self.claim,
                "last_iso": time.strftime("%Y-%m-%d %H:%M", time.localtime(self.last))
                if self.last else ""}


def sessions(root: str) -> List[SessionRow]:
    """Every session with snapshots or a claim, most recently active first."""
    rows: Dict[str, SessionRow] = {}
    for s in S.timeline(root):
        if not s.session:
            continue
        r = rows.setdefault(s.session, SessionRow(s.session, first=s.t))
        r.run = s.run or r.run
        if s.path not in r.files:
            r.files.append(s.path)
        if s.kind == S.AFTER:
            r.writes += 1
        r.last = max(r.last, s.t)
    for sid, claim, rid, at in _claims(root):
        r = rows.setdefault(sid, SessionRow(sid, first=at, last=at))
        r.claim = claim
        r.run = rid or r.run
    return sorted(rows.values(), key=lambda r: r.last, reverse=True)


def _claims(root: str):
    """(session, claim, run, at) for each C1 run-intent record."""
    from ..run_intent import INTENT_PREFIX, session_claim
    from ..state import StateStore
    from ..tdd_state import state_dir
    sd = state_dir(root)
    try:
        names = [n[:-5] for n in os.listdir(sd)
                 if n.startswith(INTENT_PREFIX) and n.endswith(".json")]
    except OSError:
        return
    store = StateStore(sd)
    for name in names:
        sid = name[len(INTENT_PREFIX):]
        claim, rid = session_claim(root, sid)
        if claim == "none":
            continue
        data = store.read(name) or {}
        try:
            at = float(data.get("at") or 0)
        except (TypeError, ValueError):
            at = 0.0
        yield sid, claim, rid or "", at


def get(root: str, session_id: Optional[str]) -> Optional[SessionRow]:
    if not session_id:
        return None
    for r in sessions(root):
        if r.id == session_id:
            return r
    return None


__all__ = ["SessionRow", "sessions", "get"]
