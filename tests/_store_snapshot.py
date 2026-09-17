"""THE whole-tree "nothing was written" instrument, in ONE place.

Two suites grew their own byte-identical copy of this (`test_si_6_behavioural_write_freedom`,
`test_db_s7a_edge_substrate`) and both carried the same wrong assumption, so they both broke on the
same machine on the same day. One instrument, one place.

⛔ WHAT BROKE, AND WHY IT IS NOT A PLATFORM QUIRK. The old snapshot was whole-tree BYTES, sidecars
included, and si_6's copy defended that choice in a comment that deserves quoting because it is the
whole defect:

    A SQLite store in WAL mode rewrites its `-shm` shared-memory index on an ordinary SELECT,
    which would have forced either a file-class exclusion (blinding the harness to every
    WAL-buffered write — the store's real writes land in `-wal`) or a row-level content dump
    instead of bytes (a second notion of "durable" to keep true). NEITHER WAS NEEDED: ... the
    last connection to close checkpoints and removes `-wal`/`-shm`.

It foresaw this exact failure, enumerated both honest repairs, named the trap in each — and then
declined both because *the library cleans up for us*. It does not. Whether a clean last-close
unlinks `-wal`/`-shm` is a property of the **libsqlite3 build**, and MEASURED at the 0.0.20 cut the
answer differs on two machines running the identical tree: removed on Debian sqlite 3.37, PERSISTED
on pyenv py3.13 / sqlite 3.51 (proven with `sqlite3` alone and no mokata in the process at all).

⭐ SO THIS TAKES THE REPAIR THAT COMMENT REJECTED, AND PAYS ITS PRICE PROPERLY. Sidecars are dropped
from the byte set — their EXISTENCE says something about libsqlite3, never about the subject under
test — and in their place each store contributes an authoritative read of its COMMITTED CONTENT.
That is not "a second notion of durable": it is the SAME notion, read through the store's own API
instead of through the filesystem, and it is strictly stronger than the bytes it replaces, because a
write buffered in an un-checkpointed `-wal` moves the content while leaving `m.db` untouched.

⚠ The blinding the old comment feared is REAL, and it is the thing to keep graded. A file-class
exclusion ALONE would hide exactly that write. `test_a_write_buffered_in_the_WAL_is_still_caught`
exists to fail if this module is ever reduced to the exclusion half.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import sqlite3

import _support

# SQLite's two sidecars. `-wal` is the write-ahead log, `-shm` its shared-memory index. Both are
# created on OPEN (a plain SELECT is enough) and whether they survive the last close is a
# compile-time property of libsqlite3, not a fact about mokata.
SIDECAR_SUFFIXES = ("-wal", "-shm")

# What marks a store's content in the snapshot dict. Deliberately NOT a path that could collide
# with a real file name under the tree (§7g — an ABSENT answer and a REAL answer must never share
# a representation; a content row and a file row must not either).
CONTENT_SUFFIX = "\x00content"


def is_sidecar(name: str) -> bool:
    """True for a SQLite WAL sidecar, by NAME. Used for both the snapshot and for directory
    listings that mean 'mokata created no files here'."""
    return name.endswith(SIDECAR_SUFFIXES)


def without_sidecars(names):
    """A directory listing with the sidecars removed — for pins that assert WHICH FILES exist."""
    return sorted(n for n in names if not is_sidecar(n))


def looks_like_sqlite(path: str) -> bool:
    """True if `path` opens as a SQLite database. Read by HEADER, never by extension: the stores
    under test are variously `m.db`, `memory.db` and names chosen by a fixture, and a rule that
    keyed on `.db` would silently stop reading content the day one of them was renamed."""
    try:
        with open(path, "rb") as fh:
            return fh.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def db_content(path: str) -> bytes:
    """The store's COMMITTED content, as bytes — `sqlite3`'s own `iterdump()`.

    ⭐ `iterdump` RATHER THAN A HAND-ROLLED `SELECT *` PER TABLE, for two reasons and the second is
    the one that matters. It is WIDER: the dump carries the schema, the indexes and the triggers
    alongside the rows, so a migration that quietly adds a trigger is a change this sees and a
    row-only walk would not. And it writes NO SQL — enumerating tables from `sqlite_master` meant
    interpolating each name into a query and escaping it by hand, which `test_shim_declaration`
    correctly convicted as a REWRITTEN SQL string. That guard is about Postgres SQL executed on
    another engine after a rewrite, which this never was, but the honest answer to a guard firing
    on a real pattern is to stop exhibiting the pattern rather than to allow-list around it.

    Opened read-WRITE on purpose. A `mode=ro` connection cannot create the `-shm` index it needs to
    read a WAL store that has none, so the read-only spelling would fail exactly where the WAL is
    live, which is the case this exists to cover. Nothing here issues a write; the connection's own
    sidecars are excluded from the snapshot anyway.

    Never raises: an unreadable store is a DISTINCT value, not an empty one (§7g), so a store that
    breaks mid-test cannot be mistaken for a store nobody touched."""
    try:
        conn = sqlite3.connect(path)
    except sqlite3.Error as exc:
        return b"UNOPENABLE:" + repr(str(exc)).encode()
    try:
        return "\n".join(conn.iterdump()).encode()
    except sqlite3.Error as exc:
        return b"UNREADABLE:" + repr(str(exc)).encode()
    finally:
        conn.close()


def tree_snapshot(root: str) -> dict:
    """Every byte under `root` EXCEPT the WAL sidecars, plus each store's committed content.

    Whole-tree rather than scoped to the file the pin's author had in mind: the write that breaks a
    charter is by definition the one nobody anticipated. Bytes, not mtimes — a re-read that rewrites
    identical content is not a write in the sense this pins."""
    snap = {}
    stores = []
    for base, _dirs, files in os.walk(root):
        for name in sorted(files):
            path = os.path.join(base, name)
            if is_sidecar(name):
                continue
            try:
                with open(path, "rb") as fh:
                    blob = fh.read()
            except OSError as exc:                     # a file that vanished mid-walk is DISTINCT
                blob = b"UNREADABLE:" + repr(str(exc)).encode()
            snap[_support.posix_rel(path, root)] = blob
            if looks_like_sqlite(path):
                stores.append(path)
    for path in stores:
        snap[_support.posix_rel(path, root) + CONTENT_SUFFIX] = db_content(path)
    return snap


def changed(before: dict, after: dict):
    """The keys whose value moved, added or vanished. Content keys render with their marker made
    visible, so a failure message names WHICH notion of change fired."""
    keys = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    return [k.replace(CONTENT_SUFFIX, " (committed content)") for k in keys]
