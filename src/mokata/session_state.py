"""MS.S2 — session-scoped pipeline state (M-2).

The pipeline's per-run state keys were repo-global SINGLETONS: `approved_approach`,
`brainstorm_progress`, `approved_refinements`, `emitted_spec` each lived at one fixed path, so two
Claude Code windows on one repo overwrote each other's brainstorm/spec (MS.S1 made the write atomic
+ locked; it could not stop the OVERWRITE — both windows aimed at the same file). This module moves
those keys UNDER the session's identity so each window keeps its own, while genuinely shared repo
state (memory stats, the shared spec corpus, the session registry itself) stays global.

`SessionScopedStore` wraps a `StateStore` and rewrites ONLY the scoped keys to a per-session
physical name (`<key>__<session_id>.json`); every other key passes through byte-for-byte, so a
single-session flow is unchanged. The value FORMAT is never touched — only the file NAME gains the
session dimension (the state schema is identical). Reads of a scoped key fall back, ONE-WAY, to the
legacy singleton file when the session-scoped file is absent: an in-flight pre-upgrade run is
readable by (and thus resumable in) a new session, and the legacy file is never written back to or
destroyed. Checkpoints (`pipeline_run__<run_id>`) are already keyed by run_id — and run_id ==
session_id (see `session.py`) — so they are session-scoped for free and are NOT rewritten here.

Stdlib-only; the scoped-key set is spelled as literals (cross-referenced to their owning modules)
to avoid an import cycle with those modules and with `config`.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from .state import StateStore

# The per-session pipeline-state keys (the singletons two windows would clobber). Kept as literals
# to avoid importing their owner modules (brainstorm/refine/engine) — a cycle, since config imports
# this. The literals are pinned by test_ms_s2 and mirror:
#   "approved_approach"    -> brainstorm.APPROACH_STATE_KEY
#   "brainstorm_progress"  -> brainstorm.BRAINSTORM_PROGRESS_KEY
#   "approved_refinements" -> refine.REFINE_STATE_KEY
#   "emitted_spec"         -> engine.spec_gate.SPEC_STATE_KEY
# NOT scoped (genuinely shared repo state): memory_stats, spec_corpus, session_registry, and the
# per-run checkpoints (pipeline_run__<run_id>, already session-scoped via run_id == session_id).
SESSION_SCOPED_KEYS = frozenset({
    "approved_approach",
    "brainstorm_progress",
    "approved_refinements",
    "emitted_spec",
})


class SessionScopedStore:
    """A `StateStore` view that scopes `scoped_keys` to `session_id` and passes everything else
    through unchanged. Same surface as `StateStore` (path/exists/read/write/update/delete/root), so
    it is a drop-in for `Surface.state`."""

    def __init__(self, base: StateStore, session_id: str,
                 scoped_keys: "frozenset[str]" = SESSION_SCOPED_KEYS) -> None:
        self._base = base
        self._sid = session_id
        self._scoped = scoped_keys

    # A scoped key -> its per-session physical name; any other key -> itself (byte-identical).
    def _phys(self, name: str) -> str:
        return f"{name}__{self._sid}" if name in self._scoped else name

    @property
    def root(self) -> str:
        return self._base.root

    def path(self, name: str) -> str:
        return self._base.path(self._phys(name))

    def exists(self, name: str) -> bool:
        if self._base.exists(self._phys(name)):
            return True
        # legacy fallback: a pre-upgrade singleton still counts as present for a scoped key.
        return name in self._scoped and self._base.exists(name)

    def write(self, name: str, data: Dict[str, Any]) -> str:
        return self._base.write(self._phys(name), data)

    def update(self, name: str, mutator: Callable[[Any], Dict[str, Any]],
               *, default: Any = None) -> Dict[str, Any]:
        return self._base.update(self._phys(name), mutator, default=default)

    def read(self, name: str) -> Optional[Dict[str, Any]]:
        value = self._base.read(self._phys(name))
        if value is None and name in self._scoped:
            # one-way legacy fallback: read the pre-upgrade singleton, never write it back.
            return self._base.read(name)
        return value

    def delete(self, name: str) -> bool:
        # deletes only THIS session's scoped file; the legacy singleton is left intact (one-way).
        return self._base.delete(self._phys(name))


def scoped_store(base: StateStore, session_id: Optional[str] = None,
                 scoped_keys: "frozenset[str]" = SESSION_SCOPED_KEYS) -> SessionScopedStore:
    """Wrap `base` in a `SessionScopedStore` for `session_id` (default: the current process's
    session). The single factory `Surface.state` uses so scoping is applied at exactly one seam."""
    if session_id is None:
        from .session import current_session_id
        session_id = current_session_id()
    return SessionScopedStore(base, session_id, scoped_keys)
