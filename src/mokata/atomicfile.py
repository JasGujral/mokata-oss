"""MS.S6 — the shared atomic-file-write primitive (M-6).

MS.S1 made the STATE STORE crash- and race-safe: every write lands via a temp file in the same
directory → ``flush`` + ``os.fsync`` → ``os.replace``, under an ``oslock`` cross-process lock held
across the whole read-modify-write. But the state store is not mokata's only shared file. The undo
log, the vault index + its artifacts, session bundles, and the flush-liveness state are all written
by BOTH Claude Code windows on one repo, and each kept a plain ``open(path, "w")`` — a torn read for
anyone reading mid-write, and a lost update for anyone writing from a stale copy.

This module is MS.S1's write half, extracted verbatim so the non-``StateStore`` callers reuse it
rather than growing a third mechanism (``StateStore._atomic_write`` now delegates here; its temp-file
naming is preserved by passing ``tmp_suffix=".json"``, so its on-disk behaviour is unchanged). The
LOCK half stays :mod:`mokata.oslock` — the one lock the whole codebase shares. Together they are the
only two primitives a shared-file writer needs:

    with file_lock(lock_path_for(path)):        # oslock (MS.S1/MS.S3) — serialise the whole RMW
        data = read(path)                       # ... read
        atomic_write_text(path, render(mutate(data)))   # ... modify → replace, indivisibly

Callers render their own text (this module has no opinion on JSON formatting), so every fixed path
keeps its exact byte-for-byte output.

Stdlib-only; imports nothing from mokata (like ``oslock``, so it can be imported from anywhere
without a cycle). Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import tempfile


def lock_path_for(path: str) -> str:
    """The sidecar lock handle guarding `path` — MS.S1's convention, one place.

    The lock is NEVER taken on the target file itself: ``os.replace`` swaps the inode out from
    under a lock held on it, which silently defeats the mutual exclusion. So it is a hidden sibling:
    dot-prefixed (never picked up by a directory listing of real artifacts) and suffixed ``.lock``
    (never ``.json``/``.md``, so index/bundle/state scans skip it). Its CONTENTS are never read or
    written — it is a rendezvous handle only, released by the OS on close AND on process death."""
    directory, base = os.path.split(path)
    return os.path.join(directory, f".{base}.lock")


def atomic_write_text(path: str, text: str, *, tmp_prefix: str = ".tmp-",
                      tmp_suffix: str = ".tmp") -> None:
    """Replace `path` with `text` atomically — a reader sees the whole old file or the whole new
    one, never a fragment, and a crash at any point leaves the previous content intact.

    The temp file is created in the SAME directory as the target (so ``os.replace`` is a true
    same-filesystem rename, never a copy), written, flushed, and ``os.fsync``'d before the replace.
    Any failure after the temp file exists unlinks it, so a failed write leaves no stray behind.
    The parent directory is created if needed. Callers pass already-rendered text, so the bytes on
    disk are exactly what the caller decided."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=parent or ".", prefix=tmp_prefix, suffix=tmp_suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        # D5 type-(iv) LEGITIMATE-SUPPRESS — and the one place `BaseException` is the RIGHT width:
        # a KeyboardInterrupt/SystemExit mid-write must still remove the temp file, or a Ctrl-C
        # litters `.mokata/` with `.tmpXXXX` droppings. Nothing is swallowed — the `raise` below
        # re-raises every exception unchanged; this handler only does cleanup on the way out. The
        # inner `except OSError: pass` is the honest floor too: if the temp file is already gone
        # (or the dir is read-only), there is nothing left to clean and nothing to report.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
