"""The explainer store's directories — opened WITHOUT following a link, one component at a time.

A stranger's repo can commit `.mokata/temp_local -> /elsewhere` (git stores symlinks), so a
`realpath` containment check of the final path is vacuous: it would be "contained" in wherever the
link points. Every component from the repo root down (`.mokata`, `temp_local`, `reviews`, `<sid>`)
is therefore checked with `lstat` to be a REAL directory — not a symlink, not a Windows reparse
point — and, on a platform with `dir_fd` support, opened relative to its parent's descriptor with
`O_NOFOLLOW`, so a swap between the check and the open is caught (the fstat must match the lstat).
Writes go to an `O_EXCL|O_NOFOLLOW` temp file in the held directory and are `os.replace`d inside
it; pruning unlinks only exact generated names through the same descriptor, never recursing.

Windows has no `dir_fd`: the same per-component `lstat` + reparse-point refusal runs on paths, and
the residual check-then-use window is disclosed in the P46 report (no test can cover it here).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import re
import secrets as _secrets
import stat
from typing import List, Optional, Sequence

from ..errors import MokataError

_DIR_FD = (os.open in os.supports_dir_fd and os.stat in os.supports_dir_fd
           and os.unlink in os.supports_dir_fd and os.mkdir in os.supports_dir_fd
           and hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY"))
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
#: A component this module will create or enter: no separators, no dots-only, no NUL.
_PART = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,119}$")


class UnsafeStore(MokataError, ValueError):
    """A store directory is a link, a reparse point, not a directory, or a name is unsafe. Nothing
    is written; the message names the component."""


def _is_link(st: os.stat_result) -> bool:
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & _REPARSE)


def _check_part(part: str) -> None:
    if part == ".mokata":
        return
    if not isinstance(part, str) or not _PART.match(part) or part in (".", ".."):
        raise UnsafeStore(f"unsafe store path component {part!r}")


class SafeDir:
    """One verified directory. Use as a context manager (it holds a descriptor where it can)."""

    def __init__(self, path: str, fd: Optional[int]) -> None:
        self.path = path
        self.fd = fd

    def __enter__(self) -> "SafeDir":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None

    # -------------------------------------------------------------------------------- reads
    def names(self) -> List[str]:
        try:
            return sorted(os.listdir(self.fd if self.fd is not None else self.path))
        except OSError:
            return []

    def _lstat(self, name: str) -> Optional[os.stat_result]:
        try:
            if self.fd is not None:
                return os.stat(name, dir_fd=self.fd, follow_symlinks=False)
            return os.lstat(os.path.join(self.path, name))
        except OSError:
            return None

    def read(self, name: str, limit: int) -> Optional[bytes]:
        """A regular file in this directory, never through a link; None when absent/unsafe. Reads
        at most `limit + 1` bytes (the caller decides what "too big" means and says so)."""
        _check_part(name)
        st = self._lstat(name)
        if st is None or not stat.S_ISREG(st.st_mode) or _is_link(st):
            return None
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        try:
            fd = (os.open(name, flags, dir_fd=self.fd) if self.fd is not None
                  else os.open(os.path.join(self.path, name), flags))
        except OSError:
            return None
        try:
            got = os.fstat(fd)
            if (got.st_dev, got.st_ino) != (st.st_dev, st.st_ino) or not stat.S_ISREG(got.st_mode):
                return None
            with os.fdopen(fd, "rb") as fh:
                fd = -1
                return fh.read(limit + 1)
        finally:
            if fd >= 0:
                os.close(fd)

    def size(self, name: str) -> int:
        st = self._lstat(name)
        return int(st.st_size) if st is not None and stat.S_ISREG(st.st_mode) else 0

    def mtime(self, name: str) -> float:
        st = self._lstat(name)
        return float(st.st_mtime) if st is not None else 0.0

    # -------------------------------------------------------------------------------- writes
    def write(self, name: str, data: bytes) -> None:
        """Atomic: an `O_EXCL|O_NOFOLLOW` temp file in THIS directory, then `os.replace` within it."""
        _check_part(name)
        tmp = f".tmp-{_secrets.token_hex(6)}"
        flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                 | getattr(os, "O_BINARY", 0))
        if self.fd is not None:
            fd = os.open(tmp, flags, 0o600, dir_fd=self.fd)
        else:
            fd = os.open(os.path.join(self.path, tmp), flags, 0o600)
        done = False
        try:
            with os.fdopen(fd, "wb") as fh:
                if os.name == "nt":              # Windows ignores 0o600: set the DACL itself
                    from mokata_emit.winacl import owner_only
                    owner_only(os.path.join(self.path, tmp))
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            if self.fd is not None:
                os.replace(tmp, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            else:
                os.replace(os.path.join(self.path, tmp), os.path.join(self.path, name))
            done = True
        finally:
            if not done:                     # the failure propagates; only the temp file goes
                self.unlink(tmp)

    def unlink(self, name: str) -> bool:
        """Remove one regular file (never a directory, never through a link)."""
        st = self._lstat(name)
        if st is None or stat.S_ISDIR(st.st_mode):
            return False
        try:
            if self.fd is not None:
                os.unlink(name, dir_fd=self.fd)
            else:
                os.unlink(os.path.join(self.path, name))
            return True
        except OSError:
            return False

    def sub(self, part: str, create: bool = True) -> Optional["SafeDir"]:
        """A verified child directory (created when `create`), or None when absent."""
        return _step(self, part, create)


def _step(parent: SafeDir, part: str, create: bool) -> Optional[SafeDir]:
    _check_part(part)
    if create:
        try:
            if parent.fd is not None:
                os.mkdir(part, 0o700, dir_fd=parent.fd)
            else:
                made = os.path.join(parent.path, part)
                os.mkdir(made, 0o700)
                if os.name == "nt":              # Windows ignores 0o700: set the DACL itself
                    from mokata_emit.winacl import owner_only
                    owner_only(made)
        except FileExistsError:
            pass
    st = parent._lstat(part)
    if st is None:
        if create:
            raise UnsafeStore(f"could not create {part!r}")
        return None
    if _is_link(st) or not stat.S_ISDIR(st.st_mode):
        raise UnsafeStore(f"{part!r} under {parent.path} is a link or not a directory — refused "
                          "(a store directory must be a real directory inside the repository)")
    path = os.path.join(parent.path, part)
    if parent.fd is None:
        return SafeDir(path, None)
    fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent.fd)
    got = os.fstat(fd)
    if (got.st_dev, got.st_ino) != (st.st_dev, st.st_ino):
        os.close(fd)
        raise UnsafeStore(f"{part!r} changed while it was opened — refused")
    return SafeDir(path, fd)


def open_chain(root: str, parts: Sequence[str], create: bool = True) -> Optional[SafeDir]:
    """The directory `root/parts[0]/…/parts[-1]`, every component verified. `root` is the repo
    root (resolved once with realpath — the repo itself may legitimately sit behind a link); the
    components below it may not be links. None when absent and not `create`."""
    top = os.path.realpath(root)
    st = os.stat(top)
    if not stat.S_ISDIR(st.st_mode):
        raise UnsafeStore(f"{top} is not a directory")
    cur = SafeDir(top, os.open(top, os.O_RDONLY | os.O_DIRECTORY) if _DIR_FD else None)
    done = False
    try:
        for part in parts:
            nxt = _step(cur, part, create)
            cur.close()
            if nxt is None:
                done = True
                return None
            cur = nxt
        done = True
        return cur
    finally:
        if not done:                         # the refusal propagates; the descriptor is closed
            cur.close()


__all__ = ["SafeDir", "UnsafeStore", "open_chain"]
