"""H-1a S4 — the per-session ALREADY-INJECTED ledger.

The per-turn pack ranks by relevance, and relevance is stable: the item that best matched turn 3
is very likely the item that best matches turn 4, and turn 5. Without a memory of what it has
already said, the injection re-hands the model the same three items every turn for the length of
a session — spending the whole 300-token budget on context the model was given twenty turns ago,
while the items that would actually be new stay below the cut.

So the hook records what it emitted, and excludes it next turn. Rules are NOT recorded: the
always-on set is the reserved slice precisely because it is what the turn must not VIOLATE, and a
guardrail that scrolled out of the window an hour ago is not "already known". Only the ranked JIT
items dedup.

SHAPE — deliberately the `knowledge/freshness.py` dirty-set, not something new:

  * TRANSIENT RUN-STATE under `.mokata/temp_local/`, which a committed `.mokata/.gitignore` keeps
    out of version control. UNGATED, on the same reading freshness records: P2 gates DURABLE
    writes — memory, code, config — and this is run-tracking, the same class as the dirty-set and
    the session snapshot. It is derived, disposable, and deleting it costs a session one
    re-injection.
  * SESSION-KEYED, and that is a correctness property rather than tidiness. A NEW session must
    start clean: its model has none of the previous session's context, so carrying the ledger
    across would suppress exactly the items a fresh session most needs. The SessionStart briefing
    is the session's baseline; this ledger is what has been added since.
  * APPEND-ONLY, one id per line, written with a single `O_APPEND` write — atomic on POSIX, and
    no read-modify-write to lose an interleaved turn. There is no update path and no delete path.
  * NEVER RAISES. This runs on the async context-injection lane, and a bookkeeping failure must
    never cost a turn. Every failure degrades to "we don't know what was injected", whose worst
    case is a repeated item — the exact thing the ledger exists to reduce, never worse than not
    having it.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
from typing import Iterable, List, Optional, Set

# The leaf under `.mokata/temp_local/` this ledger owns. Named as a constant because the P2
# read-only pin carves out exactly this directory and nothing else — a test that hard-coded the
# path would stop matching the moment it moved, and would carve out the wrong thing silently.
LEDGER_DIRNAME = "injection_ledger"


def _sid(session_id: Optional[str]) -> str:
    """The session key. Mirrors `knowledge.freshness._sid` — including the `"default"` floor, so a
    caller with no session identity still gets ONE stable bucket rather than a new file per turn."""
    if session_id:
        return session_id
    try:
        from .session import current_session_id
        return current_session_id()
    except Exception:  # noqa: BLE001 — bookkeeping must never break on identity resolution
        return "default"


def ledger_dir(root: str) -> str:
    from . import MOKATA_DIR, TEMP_LOCAL_DIRNAME
    return os.path.join(root, MOKATA_DIR, TEMP_LOCAL_DIRNAME, LEDGER_DIRNAME)


def _ledger_path(root: str, session_id: Optional[str]) -> str:
    safe = _sid(session_id).replace("/", "_").replace(os.sep, "_")
    return os.path.join(ledger_dir(root), f"injected__{safe}.log")


def record_injected(root: str, item_ids: Iterable[str], *,
                    session_id: Optional[str] = None) -> None:
    """Append the ids just injected. Atomic O(1) append, ungated, and NEVER raises."""
    try:
        payload = "".join(f"{i}\n" for i in item_ids if i)
        if not payload:
            return
        os.makedirs(ledger_dir(root), exist_ok=True)
        # O_APPEND makes each write atomic on POSIX; a small line write never interleaves. No
        # read-modify-write, so two windows on one repo cannot lose each other's turns.
        with open(_ledger_path(root, session_id), "a", encoding="utf-8") as fh:
            fh.write(payload)
    except Exception:  # noqa: BLE001 — the async lane never fails a turn over bookkeeping
        pass


def already_injected(root: str, *, session_id: Optional[str] = None) -> Set[str]:
    """The ids injected so far THIS session. Empty set on a first turn, an absent ledger, or any
    read failure — the fail-open direction, whose worst case is one repeated item."""
    try:
        with open(_ledger_path(root, session_id), encoding="utf-8") as fh:
            return {ln.strip() for ln in fh if ln.strip()}
    except (OSError, ValueError):
        # OSError = absent/unreadable; ValueError = a ledger whose bytes are not UTF-8 (a torn
        # write, a disk that lied). `UnicodeDecodeError` is a ValueError, and catching only
        # OSError let a corrupt ledger raise straight out of a function whose contract — and this
        # module's whole reason for existing on the async lane — is that it never does.
        return set()


def read_injected(root: str, *, session_id: Optional[str] = None) -> List[str]:
    """The ids in the ORDER they were injected (duplicates preserved) — for inspecting the ledger
    itself rather than using it. `already_injected` is the set the injection path wants."""
    try:
        with open(_ledger_path(root, session_id), encoding="utf-8") as fh:
            return [ln.strip() for ln in fh if ln.strip()]
    except (OSError, ValueError):        # same two classes as `already_injected` — see there
        return []
