"""Is this path inside that one? — answered by the DISK, with two named questions (0.0.28 lane C, note C §3d/§3e).

A filesystem decides what a name opens, and they disagree: APFS (default) and NTFS ignore case, APFS also
ignores Unicode normalisation, ext4 `casefold` folds per directory, and `realpath` keeps whatever case the
caller typed on macOS. So a string comparison of realpaths says `/Users/jas/REPO/tools` is outside
`/Users/jas/repo` while the disk says it is the same folder (LB5-1: a planted `poetry` answered). The fix is
not one better `_under`: containment is asked in two OPPOSITE polarities, and each needs its own safe side.

  * `refuse_if_inside(path, base)` — "is it inside? then refuse". True when an existing ancestor of
    `realpath(path)` IS `base` on disk (`st_dev` and `st_ino`, a zero index never counts), OR the per-component
    fold (`fold_component`, NFC + casefold, Windows trailing dot/space/stream) says inside, OR the candidate's
    chain cannot be stat-ed while the base's identity is known. A BASE with no identity lets the fold alone
    decide (`identity_known` lets `mokata doctor` say so). The fold applies on every volume: on a
    case-sensitive volume a fold-equal sibling IS refused — the stated cost, on the safe side.
  * `require_inside(path, base)` — "must be inside, else refuse". Identity; when identity cannot be decided
    for the base, the exact realpath string containment. NEVER the fold; undecidable is OUTSIDE.
  * `same_entry(a, b)` — identity equality (the cwd skip is an equality, not containment).
  * `disk_spelling(directory, name)` — a component as the DIRECTORY spells it (scandir only, ruling C-Q2),
    and `disk_rel(root, path)` — the repo-relative name of a path in the disk's case, root found by identity.

No answer depends on `sys.platform` or git's `core.ignorecase`. There is no `undecided=` keyword: a caller
cannot pick the wrong default. Disclosed: a hard link to a repo file in an outside directory passes (git cannot
make one); NFS and a UNC loopback reach one directory under another `st_dev`.

Stdlib only. Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import stat as _stat_mod
import time
from collections import OrderedDict
from typing import Dict, List, Optional, Set, Tuple

from mokata_emit import fold_component

#: The clock a directory listing's age is read on (a seam: tests drive it).
_clock = time.monotonic
#: A directory's listing is re-read at most this often (never keyed on mtime: churn would rescan a
#: 200k-entry directory per create, and FAT/SMB mtimes are 1-2 s coarse anyway).
REFRESH_SECS = 1.0
#: How many directories' listings are kept (least recently used goes first).
MAX_DIRS = 256


def _stat(path: str) -> os.stat_result:
    """`os.stat` — the one seam every identity question goes through."""
    return os.stat(path)


def _ident(path: str) -> Optional[Tuple[int, int]]:
    try:
        st = _stat(path)
    except (OSError, ValueError):
        return None
    return (st.st_dev, st.st_ino) if st.st_ino else None


def identity_known(path: str) -> bool:
    """Does `path` have a file identity here (stat-able, non-zero index)?"""
    return _ident(os.path.realpath(path)) is not None


def _parts(path: str) -> List[str]:
    p = os.path.normpath(path)
    if os.name == "nt":
        p = p.replace("/", "\\")
        return [c for c in p.split("\\") if c]
    return [c for c in p.split("/") if c]


def _fold_inside(path: str, base: str) -> bool:
    """Component-wise: `/repo2` is not inside `/repo`. Undecidable folding is inside (refuse side)."""
    try:
        p = [fold_component(c) for c in _parts(path)]
        b = [fold_component(c) for c in _parts(base)]
    except (ValueError, TypeError):
        return True
    return len(p) >= len(b) and p[:len(b)] == b


def _chain(path: str):
    """`path` then each ancestor, up to the filesystem root."""
    cur = path
    while True:
        yield cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return
        cur = parent


def _identity_walk(path: str, base_id: Tuple[int, int]) -> Optional[bool]:
    """True: an ancestor IS the base. False: the chain was readable and none is. None: no entry of the
    chain could be stat-ed (undecidable)."""
    readable = False
    for cur in _chain(path):
        got = _ident(cur)
        if got is None:
            continue
        readable = True
        if got == base_id:
            return True
    return False if readable else None


def refuse_if_inside(path: str, base: str) -> bool:
    """Is `path` — on this disk or by any spelling — `base` or inside it? (The REFUSE question.)"""
    p, b = os.path.realpath(path), os.path.realpath(base)
    if _fold_inside(p, b):
        return True
    base_id = _ident(b)
    if base_id is None:
        return False                                   # the fold decided (doctor says so)
    walked = _identity_walk(p, base_id)
    return True if walked is None else walked


def require_inside(path: str, base: str) -> bool:
    """Is `path` really inside `base`? (The ALLOW question: never the fold; undecidable is outside.)"""
    p, b = os.path.realpath(path), os.path.realpath(base)
    base_id = _ident(b)
    if base_id is not None:
        return bool(_identity_walk(p, base_id))
    try:
        return os.path.commonpath([p, b]) == b
    except ValueError:                                  # another drive, or mixed absolute/relative
        return False


def same_entry(a: str, b: str) -> bool:
    """Are `a` and `b` one directory entry on disk? Identity; when either has none, the exact realpath."""
    ra, rb = os.path.realpath(a), os.path.realpath(b)
    ia, ib = _ident(ra), _ident(rb)
    if ia is not None and ib is not None:
        return ia == ib
    return os.path.normcase(ra) == os.path.normcase(rb)


# ------------------------------------------------------------------------------ the disk's spelling

class _Listing:
    __slots__ = ("names", "folded", "at", "folds")

    def __init__(self, names: Set[str], at: float) -> None:
        self.names = names
        self.folded: Optional[Dict[str, List[str]]] = None      # built on the first non-exact lookup
        self.at = at
        self.folds: Optional[bool] = None


_LISTINGS: "OrderedDict[str, _Listing]" = OrderedDict()


def forget() -> None:
    """Drop every cached listing (a test, or a process that knows the tree was replaced)."""
    _LISTINGS.clear()


def _listing(directory: str) -> Optional[_Listing]:
    """The directory's listing snapshot, re-read when it is `REFRESH_SECS` old (so an exact hit is never
    older than that, and a 200k-entry directory under churn is listed once a second, not once an event)."""
    now = _clock()
    got = _LISTINGS.get(directory)
    if got is not None and now - got.at < REFRESH_SECS:
        _LISTINGS.move_to_end(directory)
        return got
    try:
        with os.scandir(directory) as it:
            names = {e.name for e in it}
    except OSError:
        return got
    lst = _Listing(names, now)
    _LISTINGS[directory] = lst
    _LISTINGS.move_to_end(directory)
    while len(_LISTINGS) > MAX_DIRS:
        _LISTINGS.popitem(last=False)
    return lst


def _key(name: str) -> str:
    try:
        return fold_component(name)
    except (ValueError, TypeError):
        return name


def _lookup(lst: _Listing, directory: str, name: str) -> Optional[str]:
    if name in lst.names:
        return name
    if lst.folded is None:
        folded: Dict[str, List[str]] = {}
        for n in lst.names:
            folded.setdefault(_key(n), []).append(n)
        lst.folded = folded
    cands = lst.folded.get(_key(name), [])
    if not cands:
        return None
    try:
        typed = os.lstat(os.path.join(directory, name))
    except OSError:
        return None
    for c in cands:
        try:
            if os.path.samestat(typed, os.lstat(os.path.join(directory, c))):
                return c
        except OSError:
            continue
    return None


def disk_spelling(directory: str, name: str) -> str:
    """`name` as `directory` spells the entry it opens: an exact name in the listing snapshot is taken as
    is; else the snapshot entry that folds equal AND is the same file (`samestat`); else the typed name (a
    new file, not yet in a snapshot at most `REFRESH_SECS` old, takes its typed spelling). No `swapcase`
    precheck. Disclosed: a case-twin created, or a case-only rename made, within that second."""
    lst = _listing(directory)
    if lst is not None:
        got = _lookup(lst, directory, name)
        if got is not None:
            return got
    return name


def folds_case(directory: str) -> bool:
    """Does THIS directory open a name under another case? Probed on an existing entry with a cased letter
    (its flipped spelling `samestat`s it, or does not exist); never read from git's `core.ignorecase`. A
    directory with no cased entry answers with its nearest ancestor's probe; none anywhere: False."""
    for cur in _chain(os.path.realpath(directory)):
        lst = _listing(cur)
        if lst is None:
            continue
        if lst.folds is not None:
            return lst.folds
        for n in sorted(lst.names)[:64]:
            flip = n.swapcase()
            if flip == n:
                continue
            try:
                a = os.lstat(os.path.join(cur, n))
            except OSError:
                continue
            try:
                b = os.lstat(os.path.join(cur, flip))
            except OSError:
                lst.folds = False
                return False
            lst.folds = os.path.samestat(a, b)
            return lst.folds
    return False


def disk_rel(root: str, path: str) -> Optional[Tuple[List[str], List[str], str]]:
    """`(existing, tail, deepest)`: the components of `path` below the directory that IS `root` (found by
    identity, walking up from `realpath(path)`), the existing ones spelled as the disk spells them and the
    vanished tail as typed; `deepest` is the deepest existing directory. None when `path` is not inside
    `root` (or the root has no identity and the exact realpath says outside)."""
    real_root = os.path.realpath(root)
    p = os.path.realpath(path)
    root_id = _ident(real_root)
    tail: List[str] = []
    cur = p
    while not os.path.lexists(cur):
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        tail.insert(0, os.path.basename(cur))
        cur = parent
    deepest = cur
    below: List[str] = []
    if root_id is None:
        try:
            if os.path.commonpath([cur, real_root]) != real_root:
                return None
        except ValueError:
            return None
        below = _parts(cur)[len(_parts(real_root)):]
    else:
        walk = cur
        while True:
            if _ident(walk) == root_id:
                break
            parent = os.path.dirname(walk)
            if parent == walk:
                return None
            below.insert(0, os.path.basename(walk))
            walk = parent
        spelled, here = [], walk
        for c in below:
            s = disk_spelling(here, c)
            spelled.append(s)
            here = os.path.join(here, s)
        below = spelled
    return below, tail, deepest


__all__ = ["refuse_if_inside", "require_inside", "same_entry", "identity_known", "disk_spelling",
           "disk_rel", "folds_case", "forget", "REFRESH_SECS"]
