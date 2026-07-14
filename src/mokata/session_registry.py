"""MS.S2 — the live-session registry (M-2).

Two Claude Code windows on one repo are invisible to each other. Each window mints a `session_id`
(see `session.py`) and keeps its own pipeline state (see `session_state.py`); this small registry
is what lets them be SEEN — the substrate for worktree offers (WT), session save/resume (SS), and
per-run TDD state (SI.2), and the data behind `mokata windows`.

It is one StateStore key (`session_registry`, a GLOBAL/pass-through key — the registry of ALL
windows must be shared, not per-session), holding `{ "sessions": { session_id: entry } }` where an
entry is exactly six small fields: `session_id`, `started_at`, `pid`, `repo_root`, `last_seen`,
`phase`. Nothing else — no DSN, no memory content, no run payload (secret-safe by construction).

Every mutation rides MS.S1's atomic + cross-process-locked `StateStore.update`, so two windows
registering at the same instant never lose an update. Registration is UPSERT-SELF-ONLY (`touch`
never removes a sibling, even a just-exited one — the sibling's row survives until a `list` prunes
it, so a concurrent pair is always both recorded). Staleness is resolved lazily on `list`: an entry
whose pid is no longer alive is shown once as `stale` and pruned from the stored registry. No
daemon, no background reaper. The registry is runtime-transient state under temp_local (like the
progress log / audit ledger) — NOT a durable artifact, so its upkeep is ungated (P2 is about
durable writes; this is transient liveness).

Stdlib-only. Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, List, Optional

# A GLOBAL StateStore key (NOT in session_state.SESSION_SCOPED_KEYS) — the shared registry of all
# windows. Passes through Surface.state's scoping untouched.
SESSION_REGISTRY_KEY = "session_registry"

# The exact entry field set (pinned by the secret-safety test — nothing may be added silently).
# WT.S1 adds `scope` — the session's stated topic ("what am I working on"), recorded by
# `worktree create`; runtime-transient like the rest of the entry, and never a secret.
_ENTRY_FIELDS = ("session_id", "started_at", "pid", "repo_root", "last_seen", "phase", "scope")


@dataclass
class SessionEntry:
    session_id: str
    started_at: str
    pid: int
    repo_root: str
    last_seen: str
    phase: Optional[str]
    alive: bool
    scope: Optional[str] = None

    @property
    def short_id(self) -> str:
        from .session import short_id
        return short_id(self.session_id)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def pid_alive(pid: Any) -> bool:
    """True when a process with `pid` currently exists. Signal-0 probe: `ProcessLookupError` (ESRCH)
    means gone; `PermissionError` (EPERM) means it exists under another user. A non-int / non-positive
    pid is never alive. (POSIX; the Windows probe lands with MS.S7's two-OS proof.)"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def touch(surface: Any, phase: Optional[str] = None, scope: Optional[str] = None) -> None:
    """Register / refresh THIS window's entry (atomic + cross-process locked, via MS.S1). Upserts
    self only — never prunes a sibling — so a concurrent pair is always both recorded. `started_at`
    is preserved across touches; `last_seen`/`pid`/`repo_root` refresh; `phase`/`scope` update when
    given, else the prior value is kept. Raises on a genuine store error (the ungated touchpoint
    wiring wraps this call degrade-clean; the API itself stays honest for callers/tests)."""
    from .session import current_session
    sess = current_session()
    root = _safe_root(getattr(surface, "root", ""))

    def _mutator(cur: Any) -> dict:
        cur = cur if isinstance(cur, dict) else {}
        sessions = dict(cur.get("sessions") or {})
        prev = sessions.get(sess.session_id) or {}
        sessions[sess.session_id] = {
            "session_id": sess.session_id,
            "started_at": prev.get("started_at") or sess.started_at,
            "pid": sess.pid,
            "repo_root": root,
            "last_seen": _now_iso(),
            "phase": phase if phase is not None else prev.get("phase"),
            "scope": scope if scope is not None else prev.get("scope"),
        }
        return {"sessions": sessions}

    _registry_store(surface).update(SESSION_REGISTRY_KEY, _mutator, default={"sessions": {}})


def list_sessions(surface: Any, prune: bool = True) -> List[SessionEntry]:
    """Every recorded window — live and stale — newest last (by start). A stale window (dead pid) is
    returned once with `alive=False`, then (when `prune`) removed from the stored registry so it
    disappears on the next list. The prune rides the same atomic/locked RMW, so it can't race a
    concurrent `touch`.

    NEVER RAISES — and D5b makes that TRUE rather than aspirational. The contract was already
    written here ("an unreadable/locked registry lists nothing rather than raising into the
    read-only `mokata windows` path") and the code did not keep it: the degrade path's fallback
    `store.read` re-opens and re-parses THE SAME FILE the locked RMW just failed on, unguarded — so
    an unreadable (OSError) or torn (ValueError) registry raised one line later, out of the very
    handler written to absorb it, into the read path the contract protects. The registry is a
    TRANSIENT, best-effort list of live windows; no window is ever worth wedging `mokata windows`
    (or the statusline, or the MCP read tool) over. A registry that cannot be reached lists nothing
    — and, since D5b, says so ONCE, loudly, instead of vanishing."""
    snapshot: dict = {}

    def _mutator(cur: Any) -> dict:
        cur = cur if isinstance(cur, dict) else {}
        sessions = dict(cur.get("sessions") or {})
        snapshot.clear()
        snapshot.update(sessions)                       # capture the pre-prune view to return
        if prune:
            sessions = {sid: e for sid, e in sessions.items() if pid_alive(_pid(e))}
        return {"sessions": sessions}

    try:
        store = _registry_store(surface)
        try:
            store.update(SESSION_REGISTRY_KEY, _mutator, default={"sessions": {}})
        except (OSError, TimeoutError, ValueError):
            # D5 — the real raisers of the locked read-modify-write: an unreadable/unwritable
            # registry file (OSError), a contended cross-process lock (`oslock.LockTimeout`, which
            # IS a TimeoutError), and a torn/unparseable JSON registry (ValueError).
            # degrade-clean: fall back to a plain read (no prune) if the locked RMW is unavailable.
            cur = store.read(SESSION_REGISTRY_KEY)
            snapshot = dict((cur or {}).get("sessions") or {}) if isinstance(cur, dict) else {}
    except Exception as exc:
        # D5b — BROAD, and broad is the CONTRACT here, not a failure to name the classes. Two things
        # make it so, and both have to hold: (1) the callers are READ-ONLY views of a transient
        # registry — `mokata windows`, the statusline, the MCP read tool — where an exception does
        # not degrade a feature, it wedges the view; (2) the fallback is the SAFE direction (an
        # empty window list is exactly what "no registry" means), so nothing is silently promoted.
        # What makes it a (ii) and not a swallow: it SPEAKS. The typo this could otherwise hide (an
        # AttributeError from `_registry_store`, or from our own `_mutator`) no longer masquerades
        # as "no windows open" — the notice says the registry could not be READ, once, on stderr and
        # again in doctor's "degraded this session" block, and carries the exception text as its
        # `detail` (which is what the structured MCP marker reports; `render()` deliberately keeps
        # the printed line class-only).
        from .degrade import FAILURE_LOCAL_IO, note_degraded
        snapshot = {}
        note_degraded("session-registry", FAILURE_LOCAL_IO,
                      fallback="no live windows are listed — the session registry could not be read",
                      fix="check permissions/disk under `.mokata/temp_local/`, then run "
                          "`mokata doctor`",
                      detail=str(exc))

    entries = [_entry(sid, e) for sid, e in snapshot.items() if isinstance(e, dict)]
    entries.sort(key=lambda x: (x.started_at, x.session_id))
    return entries


def _entry(session_id: str, e: dict) -> SessionEntry:
    pid = e.get("pid")
    return SessionEntry(
        session_id=e.get("session_id") or session_id,
        started_at=e.get("started_at") or "",
        pid=pid if isinstance(pid, int) else _coerce_pid(pid),
        repo_root=e.get("repo_root") or "",
        last_seen=e.get("last_seen") or "",
        phase=e.get("phase"),
        alive=pid_alive(pid),
        scope=e.get("scope"),
    )


def _pid(e: Any) -> Any:
    return e.get("pid") if isinstance(e, dict) else None


def _coerce_pid(pid: Any) -> int:
    try:
        return int(pid)
    except (TypeError, ValueError):
        return 0


def _safe_root(root: str) -> str:
    """An absolute repo root path (a label for the window). Never a credential."""
    try:
        return os.path.abspath(root) if root else ""
    except OSError:
        return root or ""


def _registry_store(surface: Any) -> Any:
    """The StateStore that holds the SHARED registry. WT.S1 anchors it at the CANONICAL repo root
    (the main checkout) so a window in a linked worktree and a window in the main checkout write
    and read the SAME registry file — otherwise each worktree's own `.mokata/temp_local/` would
    hold a SEPARATE registry and the two windows could never see each other.

    Byte-identical for the common case: a main checkout / non-git dir has `canonical_repo_root ==
    abspath(root)`, so this returns `surface.state` unchanged (the pre-WT path). Only a session
    whose own root is a linked worktree is redirected to the main checkout's state dir.
    Degrade-clean: any resolution error falls back to `surface.state`."""
    base = getattr(surface, "state", None)
    root = getattr(surface, "root", None)
    if base is None or not root:
        return base
    try:
        from .repo_identity import canonical_repo_root
        canon = canonical_repo_root(root)
        if os.path.realpath(canon) == os.path.realpath(os.path.abspath(root)):
            return base                                  # main checkout / non-git: unchanged
        from .state import StateStore
        from . import MOKATA_DIR, TEMP_LOCAL_DIRNAME
        from .config import STATE_DIRNAME
        return StateStore(os.path.join(canon, MOKATA_DIR, TEMP_LOCAL_DIRNAME, STATE_DIRNAME))
    except (OSError, ImportError):
        # D5 — the real raisers of the canonical-root resolution: an unreadable `.git` file /
        # realpath on a broken symlink (OSError) and a half-installed package (ImportError).
        # Falling back to `surface.state` is byte-identical to the pre-WT behaviour.
        return base
