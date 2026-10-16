"""PM.S1 (0.0.23 D15) — prospective memory: `kind=intention` ("remind me when X").

An INTENTION is a commitment to act LATER, with a DUE CONDITION:

  * `due_at`   — a date/instant; due once now >= it;
  * `keywords` — due when a keyword appears (whole word, case-insensitive) in the session's
                 signals (the git branch name + the working tree's changed paths);
  * `paths`    — due when a changed path matches (a glob, or a directory prefix).

Any one firing makes it due. There is NO daemon: due-ness is checked where mokata already runs —
the SessionStart briefing ("N intentions due") and the healing sweep (`store.detect_issues`, which
surfaces each due intention as an `intention_due` proposal; approving it marks it DONE = archived,
deferring leaves it to come back next time). The expiry/TTL is the item's existing `expires_at`,
which the existing STALE arm already handles.

STORAGE — no schema change, on purpose. The due condition lives in the item's existing
`applicability` dict (the "when it applies" envelope, already modelled by every build since TM.S9
and round-tripped verbatim for non-formula kinds), under ONE key, `due`. So there is no new doc key,
no `MEMORY_DOC_VERSION` bump and no DDL: every existing store opens unchanged, and an older mokata
reading an intention keeps its envelope intact (it models `applicability`) rather than dropping it.

NEVER LOSE, REFUSE LOUDLY. An intention whose envelope cannot be read (hand-edited, truncated, a
future shape) is never guessed at, never treated as "not due", and never rewritten: it is reported
as UNREADABLE — counted on the briefing line — and left exactly as it is.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..errors import MokataError
from .item import INTENTION
from .lifecycle import parse_iso

DUE_KEY = "due"
_ENVELOPE_KEYS = frozenset({"due_at", "keywords", "paths"})
MAX_TERMS = 20          # per trigger list — an intention is a reminder, not a query language
MAX_SIGNAL_PATHS = 500  # bounded SessionStart work, whatever the working tree looks like

from ..repo_paths import as_name


class IntentionError(MokataError):
    """A due condition that cannot be stored (empty, unparseable date, oversized)."""


def _split(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = raw.split(",")
    if not isinstance(raw, (list, tuple)):
        raise IntentionError("triggers must be a comma-separated string or a list")
    out = [str(t).strip() for t in raw if str(t).strip()]
    if len(out) > MAX_TERMS:
        raise IntentionError(f"at most {MAX_TERMS} triggers per list")
    return out


def normalize_due_at(raw: str) -> str:
    """`YYYY-MM-DD` (start of that day, UTC) or any ISO-8601 instant -> an ISO UTC string."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        try:
            d = datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise IntentionError(f"due_at {raw!r} is not a real date") from exc
        return d.isoformat()
    parsed = parse_iso(raw)
    if parsed is None:
        raise IntentionError(f"due_at {raw!r} is not an ISO-8601 date or instant")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def make_envelope(due_at: str = "", keywords: Any = None, paths: Any = None) -> Dict[str, Any]:
    """The `applicability` dict for a new intention. Refuses an intention with no due condition —
    a reminder that can never fire is a fact wearing the wrong kind."""
    due = {"due_at": normalize_due_at(due_at), "keywords": _split(keywords),
           "paths": [as_name(p) for p in _split(paths)]}
    if not (due["due_at"] or due["keywords"] or due["paths"]):
        raise IntentionError("an intention needs a due condition: due_at, a keyword or a path")
    return {DUE_KEY: due}


def envelope_of(item: Any) -> Optional[Dict[str, Any]]:
    """The intention's due envelope, or None when it cannot be read. STRICT: unknown keys, wrong
    types, an unparseable date, or no condition at all -> None (unreadable), never a default."""
    app = getattr(item, "applicability", None)
    if not isinstance(app, dict):
        return None
    due = app.get(DUE_KEY)
    if not isinstance(due, dict) or not set(due) <= _ENVELOPE_KEYS:
        return None
    due_at = due.get("due_at", "")
    kws, pts = due.get("keywords", []), due.get("paths", [])
    if not isinstance(due_at, str) or not isinstance(kws, list) or not isinstance(pts, list):
        return None
    if not all(isinstance(x, str) for x in kws + pts):
        return None
    if due_at and parse_iso(due_at) is None:
        return None
    if not (due_at or kws or pts):
        return None
    return {"due_at": due_at, "keywords": kws, "paths": pts}


@dataclass
class Signals:
    """What the session can see that a keyword/path trigger can match: changed paths + free text
    (the branch name). Empty signals fire nothing but dates."""
    paths: List[str] = field(default_factory=list)
    text: str = ""


@dataclass
class DueIntention:
    item: Any
    reason: str


def _path_hit(trigger: str, paths: Sequence[str]) -> Optional[str]:
    t = as_name(trigger).strip("/")
    for p in paths:
        p = as_name(p)
        if fnmatch.fnmatch(p, t) or p == t or p.startswith(t + "/"):
            return p
    return None


def _keyword_hit(keyword: str, text: str) -> bool:
    return re.search(r"(?<![A-Za-z0-9])" + re.escape(keyword.lower()) + r"(?![A-Za-z0-9])",
                     text.lower()) is not None


def due_intentions(items: Sequence[Any], now: Optional[str] = None,
                   signals: Optional[Signals] = None
                   ) -> Tuple[List[DueIntention], List[Any]]:
    """(due, unreadable) over the given ACTIVE items. Pure; writes nothing. Non-intention items
    are ignored."""
    ref = parse_iso(now) if now else datetime.now(timezone.utc)
    sig = signals or Signals()
    sig_text = " ".join([sig.text] + list(sig.paths))
    due: List[DueIntention] = []
    unreadable: List[Any] = []
    for it in items:
        if getattr(it, "kind", "") != INTENTION:
            continue
        env = envelope_of(it)
        if env is None:
            unreadable.append(it)
            continue
        reason = ""
        if env["due_at"]:
            at = parse_iso(env["due_at"])
            if at is not None and ref is not None:
                if at.tzinfo is None:
                    at = at.replace(tzinfo=timezone.utc)
                if ref.tzinfo is None:
                    ref = ref.replace(tzinfo=timezone.utc)
                if at <= ref:
                    reason = f"date {env['due_at'][:10]} reached"
        if not reason:
            for p in env["paths"]:
                hit = _path_hit(p, sig.paths)
                if hit:
                    reason = f"path {p} changed ({hit})"
                    break
        if not reason:
            for k in env["keywords"]:
                if _keyword_hit(k, sig_text):
                    reason = f"keyword '{k}' seen"
                    break
        if reason:
            due.append(DueIntention(it, reason))
    return due, unreadable


def has_signal_triggers(items: Sequence[Any]) -> bool:
    """Whether any intention needs session signals — so callers only pay for `git` when used."""
    for it in items:
        if getattr(it, "kind", "") == INTENTION:
            env = envelope_of(it)
            if env and (env["keywords"] or env["paths"]):
                return True
    return False


def session_signals(root: str, timeout: float = 2.0) -> Signals:
    """The branch name + the working tree's changed paths, via `git`, bounded. Degrades to empty
    signals (dates still fire) when git is absent, slow, or this is not a repo."""
    def _git(*args: str) -> str:
        try:
            cp = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=timeout)
            return cp.stdout if cp.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            return ""
    branch = _git("rev-parse", "--abbrev-ref", "HEAD").strip()
    paths: List[str] = []
    status = _git("status", "--porcelain", "--untracked-files=all")
    for line in status.splitlines()[:MAX_SIGNAL_PATHS]:
        rel = line[3:].strip()
        if " -> " in rel:
            rel = rel.split(" -> ", 1)[1]
        if rel:
            paths.append(rel.strip('"'))
    return Signals(paths=paths, text=branch)


def briefing_line(store: Any, root: str = "", now: Optional[str] = None,
                  signals: Optional[Signals] = None, limit: int = 3) -> Optional[str]:
    """The ONE SessionStart line: "mokata: N intention(s) due — …", plus an UNREADABLE count when
    any envelope cannot be read. None when nothing is due and nothing is unreadable. Reads without
    counting a read (`peek_active`), so the briefing moves no read/write ratio."""
    from .item import PERSISTENT
    if PERSISTENT not in getattr(store, "enabled_types", ()):
        return None
    items = [i for i in store.peek_active(mtype=PERSISTENT) if getattr(i, "kind", "") == INTENTION]
    if not items:
        return None
    if signals is None and root and has_signal_triggers(items):
        signals = session_signals(root)
    due, unreadable = due_intentions(items, now=now, signals=signals)
    parts: List[str] = []
    if due:
        shown = "; ".join(f"{d.item.subject}: {d.item.value[:60]} ({d.reason})"
                          for d in due[:limit])
        more = f" (+{len(due) - limit} more)" if len(due) > limit else ""
        parts.append(f"mokata: {len(due)} intention(s) due — {shown}{more}. Mark one done with "
                     f"the gated `apply_proposal` (approve), or defer it.")
    if unreadable:
        parts.append(f"mokata: {len(unreadable)} intention(s) UNREADABLE (due condition cannot be "
                     f"parsed) — left untouched; inspect with `mokata memory --kind intention`.")
    return "\n".join(parts) if parts else None


__all__ = ["DUE_KEY", "IntentionError", "Signals", "DueIntention", "make_envelope",
           "normalize_due_at", "envelope_of", "due_intentions", "has_signal_triggers",
           "session_signals", "briefing_line"]
