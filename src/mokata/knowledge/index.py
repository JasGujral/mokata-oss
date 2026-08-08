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

from ..repo_walk import prune_source_dirs

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
        # The nested checkouts the LAST walk skipped, repo-relative. Transient by design: it
        # describes a walk, not the index, so it is neither persisted by `to_dict` nor carried
        # across walks — a caller reading it after a build is reading THAT build.
        self.skipped_checkouts: List[str] = []

    def _iter_files(self, root: str, extensions):
        """Every source file under `root`, nested checkouts EXCLUDED and RECORDED.

        Excluded because a vendored dependency or a worktree inside the repo duplicates every
        symbol it holds, and this index feeds impact analysis and blast radius — the two
        answers a duplicate corrupts silently. Recorded because a user who vendored that
        dependency deliberately would otherwise find it mysteriously unsearchable with nothing
        to read; `cmd_index` says how many were skipped and where."""
        skipped: List[str] = []
        # Cleared up front, so a caller that abandons the walk part-way (the freshness
        # cold-walk stops at its file cap) is left with an empty record rather than the
        # previous walk's — a stale "1 checkout skipped" is the false evidence, not the fix.
        self.skipped_checkouts = []
        for dirpath, dirnames, filenames in os.walk(root):
            prune_source_dirs(dirpath, dirnames, skipped=skipped)
            for fn in filenames:
                if fn.endswith(tuple(extensions)):
                    yield os.path.join(dirpath, fn)
        self.skipped_checkouts = sorted(os.path.relpath(p, root) for p in skipped)

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


# How many skipped checkouts are NAMED before the line starts summarising. A vendored tree can
# hold dozens; the point is that the user can see the ones that matter and is told exactly how
# many they are not being shown — a silent truncation would read as "that's all of them".
SKIPPED_CHECKOUT_NAME_CAP = 5


def skipped_checkout_lines(skipped: List[str]) -> List[str]:
    """The declaration for a walk that skipped nested checkouts: how many, and where.

    Empty when nothing was skipped — the overwhelmingly common case, and a repo with no
    vendored tree must not gain a line saying so. Paths are rendered POSIX-style so the same
    repo reads identically on Windows."""
    if not skipped:
        return []
    shown = [p.replace(os.sep, "/") for p in skipped[:SKIPPED_CHECKOUT_NAME_CAP]]
    where = ", ".join(shown)
    remainder = len(skipped) - len(shown)
    if remainder:
        where += f", +{remainder} more"
    noun = "nested checkout" if len(skipped) == 1 else "nested checkouts"
    return [f"index: skipped {len(skipped)} {noun} — a different repo's source, "
            f"not indexed: {where}"]


def surface_staleness(result: Any, index: KnowledgeIndex, root: str) -> Any:
    """Annotate a QueryResult's `note` when any referenced file is stale. Never hides
    staleness; the warning rides on the existing note field (no schema change)."""
    stale = index.stale_files(root, [r.path for r in result.references])
    if stale:
        msg = f"STALE: {', '.join(sorted(set(stale)))} changed since indexing"
        result.note = (result.note + " | " + msg) if result.note else msg
    return result
