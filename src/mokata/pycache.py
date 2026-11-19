"""0.0.29 F — mokata's ONE user-cache rule, and the bytecode cache's prune.

THE RULE (`USER-CACHE-DIR-RELATIVE-XDG`)
---------------------------------------
`user_cache_base()` is `$XDG_CACHE_HOME` when it is ABSOLUTE, else `<home>/.cache` when the home is
absolute, else None (no cache can be named). A relative value is invalid (the XDG spec says so) and
is IGNORED — taken as given it put the cache, and every hook's bytecode, under whatever directory
the hook ran in: the user's project. "Absolute" is the PLATFORM's own rule, never a blend:

  * POSIX: a leading `/` only (`posixpath.isabs`). `C:\\x`, `C:/x`, `\\\\srv\\s` and `\\x` are
    relative there — accepting them is the row's bug arriving by another spelling.
  * Windows: `ntpath` — a drive WITH a root (`C:\\x`, `C:/x`) or a UNC head (`\\\\srv\\s`). `\\x`
    (drive-relative) and `C:x` (drive, no root) are relative.

The home is `$HOME` on POSIX (what the sh launchers read — the same rule in three languages) and
`%USERPROFILE%` on Windows (what the `.cmd` launcher reads). The shells cannot import this module,
so `hooks/mokata-plugin-env.sh` (`_mk_bytecode_cache`) and `hooks/mokata-hook-launch.cmd` restate it
with builtins only, and one test table grades all of them against this function (`TheOneRule`).

THE PRUNE (`HOOK-PYCACHE-COLD-AND-STALE`) — POSIX only, only what can be proved
---------------------------------------------------------------------------------
Under `PYTHONPYCACHEPREFIX`, CPython writes `<prefix><absolute source dir>/<stem>.<tag>.pyc`, so on
POSIX a `.pyc`'s source path is decodable exactly. `prune(prefix)` deletes a `.pyc` iff its decoded
source no longer exists AND the decode round-trips through `importlib.util.cache_from_source` to the
same `.pyc` path (a name in another interpreter's cache tag, or any shape the decode cannot prove,
is KEPT). It acts only when the RESOLVED root ends `mokata/pycache` (a symlinked `~/.cache` is
normal: the root is resolved once), never follows a link below it, removes the directories it
emptied, skips what it cannot touch, walks iteratively (memory flat at 10^5 files), and is
idempotent and lock-free (a concurrent hook writing a `.pyc` writes one whose source exists). A
source that vanished only for a while costs one recompile. Stray importlib temp files
(`*.pyc.<id>`) are left (disclosed).

On Windows it is a NAMED no-op: CPython strips the drive from the prefixed path (`C:\\a\\m.py` and
`D:\\a\\m.py` map to the same `.pyc`) and leaves UNC heads unmarked, so a decoded path cannot be
proved — a decoder resolving on the refresher's drive would delete every live `.pyc` of a plugin on
`C:` with a repo on `D:`. The Windows cache grows by one tree per plugin version (disclosed).

Runs in SessionStart's detached refresher (`briefing_cache.refresh_main`), once per session —
never on a hook's clock. Stdlib only. Copyright 2026 MoStack. Licensed under the Apache License,
Version 2.0.
"""

from __future__ import annotations

import ntpath
import os
import posixpath
import stat
import sys
from typing import Mapping, Optional

#: The directory under the user-cache base that holds the hooks' bytecode.
PYCACHE_TAIL = ("mokata", "pycache")


def is_absolute(path: str, *, nt: Optional[bool] = None) -> bool:
    """`path` is absolute by the platform's OWN rule (module doc). `nt` defaults to this OS."""
    if not isinstance(path, str) or not path:
        return False
    if os.name == "nt" if nt is None else nt:
        drive, rest = ntpath.splitdrive(path)
        if drive[:2] in ("\\\\", "//"):
            return len(drive) > 2               # a UNC head (`\\srv\share`)
        return bool(drive) and rest[:1] in ("\\", "/")
    return posixpath.isabs(path)


def user_cache_base(environ: Optional[Mapping[str, str]] = None, *,
                    nt: Optional[bool] = None) -> Optional[str]:
    """`$XDG_CACHE_HOME` if absolute, else `<home>/.cache` if the home is absolute, else None."""
    env = os.environ if environ is None else environ
    nt = os.name == "nt" if nt is None else nt
    xdg = env.get("XDG_CACHE_HOME") or ""
    if is_absolute(xdg, nt=nt):
        return xdg
    home = env.get("USERPROFILE" if nt else "HOME") or ""
    if is_absolute(home, nt=nt):
        return (ntpath if nt else posixpath).join(home, ".cache")
    return None


def bytecode_prefix(environ: Optional[Mapping[str, str]] = None, *,
                    nt: Optional[bool] = None) -> Optional[str]:
    """`<base>/mokata/pycache` — where the launchers point `PYTHONPYCACHEPREFIX` — or None."""
    nt = os.name == "nt" if nt is None else nt
    base = user_cache_base(environ, nt=nt)
    return (ntpath if nt else posixpath).join(base, *PYCACHE_TAIL) if base else None


def _decode(root: str, path: str):
    """`(source, optimization)` a prefixed `.pyc` stands for, or None when its name is not this
    interpreter's (`sys.implementation.cache_tag`) shape."""
    tag = sys.implementation.cache_tag
    if not tag:
        return None
    name = os.path.basename(path)
    for opt in ("", "1", "2"):
        suffix = f".{tag}{'.opt-' + opt if opt else ''}.pyc"
        if name.endswith(suffix) and len(name) > len(suffix):
            head = os.path.dirname(path)[len(root):]
            if not head.startswith(os.sep):
                return None
            return os.path.join(head, name[:-len(suffix)] + ".py"), opt
    return None


def _round_trips(root: str, source: str, opt: str, path: str) -> bool:
    import importlib.util
    saved = sys.pycache_prefix
    try:
        sys.pycache_prefix = root
        return importlib.util.cache_from_source(source, optimization=opt) == path
    except (ValueError, NotImplementedError):
        return False
    finally:
        sys.pycache_prefix = saved


def _open_root(root: str, flags: int) -> Optional[int]:
    """An fd on `root` (already `realpath`-resolved, so no component of it is a link), reached
    BY DESCRIPTOR (0.0.29 F round 3, verifier r2 H1): `/` is opened once and every component below
    it is opened relative to its parent's fd with `flags` (`O_NOFOLLOW | O_DIRECTORY`). An ancestor
    swapped for a link after the resolve — `~/.cache`, `~/.cache/mokata` — is refused (ELOOP)
    instead of followed, so the walk can never start in a directory the string check did not name.
    Then what is held must be what was resolved: the same `(st_dev, st_ino)` as the `stat` taken
    right after the resolve (a legitimately symlinked `~/.cache` works: the resolve followed it
    once, deliberately), a directory, owned by this user. Any failure: None (prune nothing)."""
    try:
        want = os.stat(root)
        fd = os.open(os.sep, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return None
    try:
        for name in [c for c in root.split(os.sep) if c]:
            child = os.open(name, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        got = os.fstat(fd)
    except OSError:
        os.close(fd)
        return None
    if ((got.st_dev, got.st_ino) != (want.st_dev, want.st_ino) or not stat.S_ISDIR(got.st_mode)
            or (hasattr(os, "geteuid") and got.st_uid != os.geteuid())):
        os.close(fd)
        return None
    return fd


def prune(prefix: Optional[str], *, nt: Optional[bool] = None) -> int:
    """Delete the bytecode under `prefix` whose source no longer exists (module doc). Returns how
    many `.pyc` files it deleted. A no-op (0) on Windows, for a root that does not resolve to a
    `…/mokata/pycache`, and for anything it cannot read. Never raises."""
    if (os.name == "nt" if nt is None else nt) or not prefix:
        return 0
    try:
        root = os.path.realpath(prefix)
    except (OSError, ValueError):
        return 0
    if tuple(root.split(os.sep)[-len(PYCACHE_TAIL):]) != PYCACHE_TAIL or not os.path.isdir(root):
        return 0
    # 0.0.29 F round 2 (verifier M1): the walk holds every directory BY DESCRIPTOR. Each child is
    # opened relative to its parent's fd with O_NOFOLLOW (a name swapped for a link between the
    # listing and the open is refused, ELOOP), listed through that fd, and every unlink/rmdir is
    # relative to the fd that listed it — so no name can resolve outside the held root, whatever
    # is renamed under it mid-walk. Where the OS cannot do that, nothing is pruned (fail closed).
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if (nofollow is None or os.open not in os.supports_dir_fd or os.scandir not in os.supports_fd
            or os.unlink not in os.supports_dir_fd or os.rmdir not in os.supports_dir_fd):
        return 0
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow
    top = _open_root(root, flags)
    if top is None:
        return 0
    deleted = 0
    # One frame per directory on the CURRENT path only (fds open = depth, not tree size):
    # [fd, path the decode reads, its entries, next index, name in the parent].
    stack = [[top, root, None, 0, None]]
    try:
        while stack:
            frame = stack[-1]
            fd, here = frame[0], frame[1]
            if frame[2] is None:
                try:
                    with os.scandir(fd) as it:
                        frame[2] = list(it)
                except OSError:
                    frame[2] = []
            if frame[3] >= len(frame[2]):
                stack.pop()
                os.close(fd)
                if stack:
                    try:
                        os.rmdir(frame[4], dir_fd=stack[-1][0])  # only an EMPTY directory goes
                    except OSError:
                        pass
                continue
            e = frame[2][frame[3]]
            frame[3] += 1
            try:
                if e.is_symlink():
                    continue                       # never follow, never delete through a link
                if e.is_dir(follow_symlinks=False):
                    try:
                        child = os.open(e.name, flags, dir_fd=fd)
                    except OSError:                # ELOOP: swapped for a link since the listing
                        continue
                    stack.append([child, os.path.join(here, e.name), None, 0, e.name])
                    continue
            except OSError:
                continue
            if not e.name.endswith(".pyc"):
                continue                           # incl. importlib's `*.pyc.<id>` temp files
            path = os.path.join(here, e.name)
            found = _decode(root, path)
            if found is None:
                continue
            source, opt = found
            if os.path.exists(source) or not _round_trips(root, source, opt, path):
                continue
            try:
                os.unlink(e.name, dir_fd=fd)
                deleted += 1
            except OSError:                        # PermissionError / FileNotFoundError: skip
                continue
    finally:
        for frame in stack:                        # only on an unexpected exit
            try:
                os.close(frame[0])
            except OSError:
                pass
    return deleted

__all__ = ["PYCACHE_TAIL", "is_absolute", "user_cache_base", "bytecode_prefix", "prune"]
