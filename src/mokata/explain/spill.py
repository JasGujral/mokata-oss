"""An oversize fact bundle, SPILLED (0.0.27 P46 round 5, N3).

A bundle record over `store.CACHE_MAX_BYTES` is not cached as one JSON file (it could never be
read back under the same bound) and is not held in memory. It is written ONCE, as rows, through
P4's owner-only `SpillWriter` (`ide/usages.py`, mkstemp in the IDE's spill dir, named for this
process's pid), and kept KEYED BY ITS CONTENT HASH. Every later page and the save stream from it
with bounded memory — one page's facts, one blob at a time — and nothing is rebuilt while the
hash matches.

Rows (one JSON array per line):

    ["m", <the bundle without its facts>, <page map>]      the header
    ["f", <page>, <fact>]                                  every fact, in page order
    ["b", <blob key>, <text>]                              every blob (each <= 1 MiB by build)

Released, under P4's rules: the spill of a hash goes when a NEWER spill of the same target
replaces it (a hash change); when nobody has asked for it for `IDLE_S` (the MCP stream's analogue
of P4's "stream gone" grace, sized for Claude writing the page between its last page and the
save); and at process exit. A crash leaves a `p4-<pid>-…` file that P4's `sweep` removes at the
next IDE start (dead pid). A released or missing spill is REBUILT AS A JOB (progress, Cancel) —
never inline in a tool call.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import atexit
import json
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from ..errors import ControlSignal

#: a spill nobody asked for in this long is released (its file deleted)
IDLE_S = 30 * 60.0
#: at most this many spills per process (the oldest goes first) — a disk bound, not memory
MAX_SPILLS = 4

_LOCK = threading.Lock()
_SPILLS: Dict[Tuple[str, str], "BundleSpill"] = {}


class BundleSpill:
    """One spilled record: where each page's facts and each blob start in the file."""

    def __init__(self, path: str, sid: str, content_hash: str, meta: Dict[str, Any],
                 page_of: Dict[str, int], pages: Dict[int, int], blobs: Dict[str, int],
                 facts_at: int, size: int) -> None:
        self.path, self.sid, self.hash = path, sid, content_hash
        self.meta, self.page_of = meta, page_of
        self.pages, self.blob_at, self.facts_at, self.size = pages, blobs, facts_at, size
        self.used = time.monotonic()

    # -------------------------------------------------------------------------------- reading
    def _rows(self, offset: int):
        try:
            fh = open(self.path, "rb")
        except OSError:
            raise SpillGone(self.hash) from None
        with fh:
            fh.seek(offset)
            for raw in fh:
                yield json.loads(raw)

    def page_facts(self, page: int) -> List[Dict[str, Any]]:
        """The facts of ONE page — only those rows are read."""
        at = self.pages.get(page)
        if at is None:
            return []
        out = []
        for row in self._rows(at):
            if row[0] != "f" or row[1] != page:
                break
            out.append(row[2])
        return out

    def facts(self) -> List[Dict[str, Any]]:
        out = []
        for row in self._rows(self.facts_at):
            if row[0] != "f":
                break
            out.append(row[2])
        return out

    def blob(self, key: str) -> Optional[str]:
        at = self.blob_at.get(key)
        if at is None:
            return None
        for row in self._rows(at):
            return row[2]
        return None

    def drop(self) -> None:
        from ..ide.usages import _unlink
        _unlink(self.path)


class SpillBlobs:
    """`blobs.get(key)` read from the spill on demand — the Resolver's only use of blobs."""

    def __init__(self, spill: BundleSpill) -> None:
        self._s = spill

    def get(self, key: str, default: Any = None) -> Any:
        got = self._s.blob(key)
        return default if got is None else got


class SpillGone(ControlSignal):
    """The spill file vanished under us (removed by hand, swept): treated as released — the
    store turns it into `NeedsRebuild` (a control signal, d5), never a failure."""


def write(root: str, sid: str, rec: Dict[str, Any], size: int) -> BundleSpill:
    """Spill `rec` (bundle + blobs + page map) through P4's `SpillWriter`, row by row — the only
    extra memory is one row at a time. Registers it under its hash; releases older spills of
    the same target (`sid`) and any past the idle / count bounds."""
    from ..ide import usages as U
    b = rec["bundle"]
    h = b["content_hash"]
    w = U.SpillWriter(U.spill_dir(root), "explain")
    pages: Dict[int, int] = {}
    blob_at: Dict[str, int] = {}
    done = False
    try:
        meta = {k: v for k, v in b.items() if k != "facts"}
        w.row(0, ["m", meta, rec["page_of"]])
        facts_at = w.bytes
        page_of = rec["page_of"]
        for f in sorted(b["facts"], key=lambda f: page_of.get(f["id"], 1)):
            p = page_of.get(f["id"], 1)
            pages.setdefault(p, w.bytes)
            w.row(p, ["f", p, f])
        for key in sorted(rec["blobs"]):
            blob_at[key] = w.bytes
            w.row(key, ["b", key, rec["blobs"][key]])
        w.close()
        done = True
    finally:
        if not done:                      # any failure or Cancel: the half-written spill goes
            w.discard()
    sp = BundleSpill(w.path, sid, h, meta, dict(page_of), pages, blob_at, facts_at, size)
    _register(os.path.realpath(root), sp)
    return sp


def _register(root: str, sp: BundleSpill) -> None:
    drop: List[BundleSpill] = []
    with _LOCK:
        for key, old in list(_SPILLS.items()):
            if key[0] == root and old.sid == sp.sid and old.hash != sp.hash:
                drop.append(_SPILLS.pop(key))                      # the change moved: a new hash
        prev = _SPILLS.pop((root, sp.hash), None)
        if prev is not None and prev.path != sp.path:
            drop.append(prev)
        _SPILLS[(root, sp.hash)] = sp
        drop += _expire_locked()
    for d in drop:
        d.drop()


def _expire_locked() -> List[BundleSpill]:
    now = time.monotonic()
    out = [_SPILLS.pop(k) for k, s in list(_SPILLS.items()) if now - s.used > IDLE_S]
    while len(_SPILLS) > MAX_SPILLS:
        k = min(_SPILLS, key=lambda k: _SPILLS[k].used)
        out.append(_SPILLS.pop(k))
    return out


def get(root: str, content_hash: str) -> Optional[BundleSpill]:
    """The live spill of `content_hash`, or None (never made here, released, or its file gone)."""
    with _LOCK:
        drop = _expire_locked()
        sp = _SPILLS.get((os.path.realpath(root), content_hash))
        if sp is not None:
            if os.path.isfile(sp.path):
                sp.used = time.monotonic()
            else:
                _SPILLS.pop((os.path.realpath(root), content_hash), None)
                sp = None
    for d in drop:
        d.drop()
    return sp


def live(root: str) -> List[BundleSpill]:
    """This repo's registered spills (newest use first)."""
    with _LOCK:
        mine = [sp for k, sp in _SPILLS.items() if k[0] == os.path.realpath(root)]
    return sorted(mine, key=lambda sp: -sp.used)


def release(root: Optional[str] = None, content_hash: Optional[str] = None) -> int:
    """Release spills (all, a repo's, or one hash's); their files are deleted. Returns how many."""
    with _LOCK:
        keys = [k for k in _SPILLS if (root is None or k[0] == os.path.realpath(root))
                and (content_hash is None or k[1] == content_hash)]
        drop = [_SPILLS.pop(k) for k in keys]
    for d in drop:
        d.drop()
    return len(drop)


atexit.register(release)

__all__ = ["BundleSpill", "SpillBlobs", "SpillGone", "write", "get", "live", "release", "IDLE_S",
           "MAX_SPILLS"]
