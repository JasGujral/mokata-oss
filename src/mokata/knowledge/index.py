"""B4 — incremental re-index + staleness detection.

A per-file fingerprint index (content hash + mtime + size). It re-indexes only what
changed and SURFACES staleness — when a file backing a query result has changed since it
was indexed, the result is flagged rather than served silently. This is a freshness
cache over the adopted graph / grep floor; it builds no parser.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_EXTENSIONS = (".py",)


def file_fingerprint(abspath: str) -> Tuple[str, float, int]:
    with open(abspath, "rb") as fh:
        data = fh.read()
    return hashlib.sha256(data).hexdigest(), os.path.getmtime(abspath), len(data)


@dataclass
class IndexEntry:
    path: str            # relative to the repo root
    content_hash: str
    mtime: float
    size: int

    def to_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "content_hash": self.content_hash,
                "mtime": self.mtime, "size": self.size}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "IndexEntry":
        return cls(path=d["path"], content_hash=d["content_hash"],
                   mtime=float(d.get("mtime", 0)), size=int(d.get("size", 0)))


class KnowledgeIndex:
    def __init__(self, entries: Optional[Dict[str, IndexEntry]] = None) -> None:
        self.entries: Dict[str, IndexEntry] = entries or {}

    @staticmethod
    def _iter_files(root: str, extensions):
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [x for x in dirnames if not x.startswith(".")]
            for fn in filenames:
                if fn.endswith(tuple(extensions)):
                    yield os.path.join(dirpath, fn)

    def _current(self, root: str, extensions) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for ab in self._iter_files(root, extensions):
            rel = os.path.relpath(ab, root)
            out[rel] = file_fingerprint(ab)[0]
        return out

    def build(self, root: str, extensions=DEFAULT_EXTENSIONS) -> List[str]:
        """Index every source file from scratch. Returns the indexed paths."""
        self.entries = {}
        for ab in self._iter_files(root, extensions):
            rel = os.path.relpath(ab, root)
            h, m, s = file_fingerprint(ab)
            self.entries[rel] = IndexEntry(rel, h, m, s)
        return list(self.entries)

    def diff(self, root: str, extensions=DEFAULT_EXTENSIONS) -> Dict[str, List[str]]:
        current = self._current(root, extensions)
        stored = {rel: e.content_hash for rel, e in self.entries.items()}
        added = [r for r in current if r not in stored]
        changed = [r for r in current if r in stored and current[r] != stored[r]]
        removed = [r for r in stored if r not in current]
        return {"added": sorted(added), "changed": sorted(changed),
                "removed": sorted(removed)}

    def reindex(self, root: str, only: Optional[List[str]] = None,
                extensions=DEFAULT_EXTENSIONS) -> List[str]:
        """Re-index only what changed (or the given paths). Returns reindexed paths."""
        if only is None:
            d = self.diff(root, extensions)
            for rel in d["removed"]:
                self.entries.pop(rel, None)
            targets = list(d["added"]) + list(d["changed"])
        else:
            targets = list(only)
        for rel in targets:
            ab = os.path.join(root, rel)
            if os.path.exists(ab):
                h, m, s = file_fingerprint(ab)
                self.entries[rel] = IndexEntry(rel, h, m, s)
        return targets

    def is_stale(self, root: str, rel_path: str) -> bool:
        entry = self.entries.get(rel_path)
        if entry is None:
            return False                 # untracked -> not "stale" (just unknown)
        ab = os.path.join(root, rel_path)
        if not os.path.exists(ab):
            return True                  # indexed but now missing
        return file_fingerprint(ab)[0] != entry.content_hash

    def stale_files(self, root: str,
                    paths: Optional[List[str]] = None) -> List[str]:
        candidates = paths if paths is not None else list(self.entries)
        return [p for p in candidates if self.is_stale(root, p)]

    def to_dict(self) -> Dict[str, Any]:
        return {"entries": {rel: e.to_dict() for rel, e in self.entries.items()}}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "KnowledgeIndex":
        return cls(entries={rel: IndexEntry.from_dict(e)
                            for rel, e in d.get("entries", {}).items()})


def surface_staleness(result: Any, index: KnowledgeIndex, root: str) -> Any:
    """Annotate a QueryResult's `note` when any referenced file is stale. Never hides
    staleness; the warning rides on the existing note field (no schema change)."""
    stale = index.stale_files(root, [r.path for r in result.references])
    if stale:
        msg = f"STALE: {', '.join(sorted(set(stale)))} changed since indexing"
        result.note = (result.note + " | " + msg) if result.note else msg
    return result
