"""Governed pipeline-state store.

The manifest + constitution are mokata's *config* (committed, hand-edited). Pipeline
runs also produce durable *state* — the first being the brainstorm phase's approved
approach (D6/D7). That state lives under `.mokata/state/` as plain, indented JSON so it
is reviewable and committable alongside the rest of the config (K7 spirit), and is read
back by downstream phases (e.g. the completeness gate).

`StateStore` is a tiny, dependency-free read/write surface. It does not gate writes
itself — the human gate sits upstream (an approach is only persisted *after* explicit
approval); this is the mechanism that approval drives.

Multi-session safety (MS.S1 / M-1): two Claude Code windows on one repo are two MCP
processes writing these SAME files. So every write is **atomic** (write to a temp file in
the same directory → ``flush`` + ``os.fsync`` → ``os.replace``) — a reader can never observe
a torn/half-written file, and a crash at any point leaves either the whole old state or the
whole new state, never a fragment. And each write, plus every read-modify-write via
:meth:`StateStore.update`, is serialised by an OS-level cross-process lock (``oslock``) held
across the full cycle, so two windows incrementing/merging the same key can't lose an update.
The lock is a hidden sidecar handle per key; it changes nothing about the state file's name,
location, format, or contents.
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, Optional

from .atomicfile import atomic_write_text
from .oslock import file_lock


class StateStore:
    def __init__(self, root: str) -> None:
        # root is the directory state files live in (e.g. <repo>/.mokata/state).
        self.root = root

    def path(self, name: str) -> str:
        return os.path.join(self.root, f"{name}.json")

    def _lock_path(self, name: str) -> str:
        # A hidden per-key sidecar handle (never the state file itself: os.replace swaps the
        # inode out from under a lock held on the target, defeating mutual exclusion). Dot-prefixed
        # + a non-".json" suffix so state-dir scans (e.g. progress.list_runs) never pick it up.
        return os.path.join(self.root, f".{name}.json.lock")

    def exists(self, name: str) -> bool:
        return os.path.exists(self.path(name))

    def _atomic_write(self, path: str, data: Dict[str, Any]) -> None:
        """Serialise `data` and replace `path` atomically: a temp file in the SAME directory is
        written, flushed, and ``os.fsync``'d, then ``os.replace``'d over the target. Serialisation
        happens before the temp file is created, so a serialisation error touches nothing on disk;
        any failure after that unlinks the temp file, leaving the previous state intact.

        MS.S6 extracted the replace half to :func:`atomicfile.atomic_write_text` so the vault index,
        session bundles, and the liveness state reuse THIS mechanism instead of growing another one.
        The temp-file naming is passed through unchanged (``.tmp-*.json`` in ``self.root``), so this
        store's on-disk behaviour is byte-for-byte what MS.S1 shipped."""
        text = json.dumps(data, indent=2, sort_keys=False) + "\n"
        atomic_write_text(path, text, tmp_suffix=".json")

    def write(self, name: str, data: Dict[str, Any]) -> str:
        """Write `data` as indented JSON (create the dir if needed), atomically and under the
        cross-process lock. Returns the path. Byte-identical output to the plain write it replaces."""
        os.makedirs(self.root, exist_ok=True)
        path = self.path(name)
        with file_lock(self._lock_path(name)):
            self._atomic_write(path, data)
        return path

    def update(self, name: str, mutator: Callable[[Any], Dict[str, Any]],
               *, default: Any = None) -> Dict[str, Any]:
        """Locked read-modify-write: hold the cross-process lock across read→``mutator``→atomic
        replace, so two processes updating the same key interleave without losing an update. The
        current value (``default`` when the key is absent) is passed to `mutator`, whose return value
        is persisted and returned. No lost update, no torn read, no schema change."""
        os.makedirs(self.root, exist_ok=True)
        path = self.path(name)
        with file_lock(self._lock_path(name)):
            current = self._read_path(path)
            if current is None:
                current = default
            new = mutator(current)
            self._atomic_write(path, new)
        return new

    def read(self, name: str) -> Optional[Dict[str, Any]]:
        """Return the parsed JSON, or None if the artifact does not exist.

        A corrupt / truncated / unreadable state file (e.g. an interrupted write, a
        half-synced bundle) degrades to None — "absent" — rather than raising into a
        read-only view (the progress/badge/lanes/resume hot paths). State lives in
        gitignored temp_local/; a malformed file must never traceback into a session
        (degrade-clean, P11). Only file-content/IO problems are swallowed; the gate
        layers above re-create state from a clean point."""
        return self._read_path(self.path(name))

    @staticmethod
    def _read_path(path: str) -> Optional[Dict[str, Any]]:
        """Parse the JSON at `path`, or None if absent / corrupt / unreadable (degrade-clean). Shared
        by :meth:`read` and the locked :meth:`update` RMW so both degrade identically."""
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (ValueError, OSError):
            return None

    def delete(self, name: str) -> bool:
        """Remove the artifact. Returns True if it existed, False otherwise."""
        path = self.path(name)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False
