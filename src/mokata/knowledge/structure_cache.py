"""0.0.26 O12 — the SessionStart briefing's code-structure line, computed OFF the session-start path.

WHY (measured). `graph_structure_line` summarises the repo from the AST floor, and that means
`AstBackend._ensure_index`: a walk of every source file plus a load of the whole edge cache. On a
1M-line repo that was 3.7 s of a 5.1 s SessionStart (normalised p50 1252 ms against the `context`
kind's 150 ms; 617 ms on K, 317 ms on a one-file repo). The line is a coarse summary ("N Python
modules; top: a, b, c"), so it is cached here and refreshed in the background:

  * FRESH cache (its key — the stat signatures of the manifest and of git's HEAD and index — has not
    moved): the cached line, no walk;
  * STALE cache: the last line, marked "(as of the last summary; refreshing)", and ONE detached
    refresher is started;
  * NO cache yet: the briefing SAYS the summary is being built in the background (never a silent
    gap), and the refresher is started;
  * a SMALL tree (≤ SMALL_TREE_ENTRIES entries, probed with an early-exit scan) is summarised
    inline instead — the walk costs a few ms there.

`mokata bootstrap` (a human asking) still computes inline — and refreshes this cache as it does.
Run-state only (temp_local/briefing/), entirely derived.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import time

DIRNAME = "briefing"
CACHE_FILENAME = "structure.json"
LOCK_FILENAME = "structure.lock"
CACHE_VERSION = 1
LOCK_STALE_SECS = 600.0
WARM_FLAG = "--warm-briefing-structure"

PENDING_LINE = ("Code structure: being summarised in the background (a full-tree walk kept off "
                "session start) — it appears in the next briefing.")
STALE_SUFFIX = " (as of the last summary; refreshing in the background)"


def cache_dir(root: str) -> str:
    from .. import MOKATA_DIR, TEMP_LOCAL_DIRNAME
    return os.path.join(root, MOKATA_DIR, TEMP_LOCAL_DIRNAME, DIRNAME)


def _git_dir(root: str):
    dot = os.path.join(root, ".git")
    if os.path.isdir(dot):
        return dot
    try:
        with open(dot, "r", encoding="utf-8", errors="replace") as fh:
            line = fh.read().strip()
    except OSError:
        return None
    if line.startswith("gitdir:"):
        return os.path.join(root, line[len("gitdir:"):].strip())
    return None


def _sig(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return [st.st_mtime_ns, st.st_size]


def key_for(root: str) -> dict:
    """What the summary depends on, cheaply: the manifest (which graph/floor answers) and git's
    HEAD + index (what is committed / staged). Unstaged edits do not move it — a coarse summary of
    module names does not need them, and the next commit or `git add` refreshes it."""
    from .. import MOKATA_DIR
    paths = [os.path.join(root, MOKATA_DIR, "manifest.json")]
    gd = _git_dir(root)
    if gd:
        paths += [os.path.join(gd, "HEAD"), os.path.join(gd, "index")]
    return {p: _sig(p) for p in paths}


def _read(root: str):
    import json
    try:
        with open(os.path.join(cache_dir(root), CACHE_FILENAME), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("v") != CACHE_VERSION:
        return None
    return data


def store(root: str, line) -> None:
    """Record `line` (None = the floor had nothing to summarise) under the CURRENT key."""
    import json
    from ..atomicfile import atomic_write_text
    atomic_write_text(os.path.join(cache_dir(root), CACHE_FILENAME),
                      json.dumps({"v": CACHE_VERSION, "key": key_for(root), "line": line},
                                 sort_keys=True))


#: A tree with at most this many entries is summarised INLINE (bounded: the probe stops one past
#: it) — the walk is a few ms there, and a background process would be pure overhead.
SMALL_TREE_ENTRIES = 400


def _small_tree(root: str, cap: int = SMALL_TREE_ENTRIES) -> bool:
    """True when `root` holds at most `cap` files/dirs (dot-dirs skipped). Stops at `cap + 1`."""
    seen = 0
    stack = [root]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    if e.name.startswith("."):
                        continue
                    seen += 1
                    if seen > cap:
                        return False
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
        except OSError:
            continue
    return True


def is_fresh(root: str) -> bool:
    data = _read(root)
    return data is not None and data.get("key") == key_for(root)


def cached_line(root: str, surface=None, spawn: bool = True):
    """The briefing's structure line WITHOUT an unbounded walk: (text or None, started_refresh).
    A small tree is summarised inline (and cached); a large one is served from the cache and
    refreshed by a detached job. Never raises."""
    try:
        data = _read(root)
        if data is not None and data.get("key") == key_for(root):
            return data.get("line"), False
        if surface is not None and _small_tree(root):
            from .layer import graph_structure_line
            line = graph_structure_line(surface)
            store(root, line)
            return line, False
        started = _start_refresh(root) if spawn else False
        if data is None:
            return PENDING_LINE, started
        line = data.get("line")
        return (line + STALE_SUFFIX if line else None), started
    except Exception:  # noqa: BLE001 — the briefing must start even if this line cannot
        return None, False


def _lock_fresh(d: str) -> bool:
    try:
        return time.time() - os.stat(os.path.join(d, LOCK_FILENAME)).st_mtime < LOCK_STALE_SECS
    except OSError:
        return False


def _start_refresh(root: str) -> bool:
    d = cache_dir(root)
    os.makedirs(d, exist_ok=True)
    if _lock_fresh(d):
        return False
    from mokata_emit import spawn_mokata_emit
    return spawn_mokata_emit([WARM_FLAG, root], cwd=d)


def _acquire(d: str) -> bool:
    path = os.path.join(d, LOCK_FILENAME)
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if _lock_fresh(d):
                return False
            try:
                os.remove(path)               # a dead refresher's lock — take it over
            except OSError:
                return False
            continue
        os.write(fd, str(os.getpid()).encode("ascii"))
        os.close(fd)
        return True
    return False


def _release(d: str) -> None:
    try:
        os.remove(os.path.join(d, LOCK_FILENAME))
    except OSError:
        pass


def warm_main(argv: list) -> int:
    """The detached refresher: compute the line (the full walk, here, off session start) and store
    it. One per repo (O_EXCL lock). Never raises."""
    if not argv:
        return 1
    root = argv[0]
    d = cache_dir(root)
    try:
        os.makedirs(d, exist_ok=True)
        if not _acquire(d):
            return 0
    except OSError:
        return 0
    try:
        from ..config import Surface
        from .layer import graph_structure_line
        store(root, graph_structure_line(Surface.load(root)))
        return 0
    except Exception:  # noqa: BLE001 — nobody to tell; the next briefing says it is still pending
        return 1
    finally:
        _release(d)
