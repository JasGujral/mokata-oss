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
    return _ident_or_absent(path)[0]


#: The errors that say an entry is NOT THERE (and nothing more): no such file or path, a drive letter
#: with no volume (15), a name no entry can have (123, 161), a file where a directory was named (267).
#: NOT "device not ready" (21) or "network path / name not found" (53, 67): those say the volume could
#: not be READ, not that the entry is absent — unknown, fail closed (verifier Low).
_ABSENT_WINERRORS = frozenset({2, 3, 15, 123, 161, 267})


def _ident_or_absent(path: str) -> Tuple[Optional[Tuple[int, int]], bool]:
    """`(identity, absent)`. ONE meaning for an unknown identity, everywhere (0.0.29 H-b xplat): ABSENT
    (the entry provably does not exist — it cannot be, or hold, anything) is told apart from UNKNOWN (it
    could not be read: permission, I/O, a zero index). Only UNKNOWN is undecidable; each question then
    fails closed its own way — the REFUSE question refuses, the ALLOW question answers outside."""
    try:
        st = _stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None, True
    except OSError as exc:
        return None, getattr(exc, "winerror", None) in _ABSENT_WINERRORS
    except ValueError:
        return None, False
    return ((st.st_dev, st.st_ino), False) if st.st_ino else (None, False)


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
    """True: an ancestor IS the base. False: none is — the chain was readable, or every entry of it is
    ABSENT (a path on no existing volume, e.g. Windows' `m:k` read as drive M:, cannot be inside a base
    that exists). None: an entry could not be READ and none answered (undecidable)."""
    readable = unknown = False
    for cur in _chain(path):
        got, absent = _ident_or_absent(cur)
        if got is None:
            unknown = unknown or not absent
            continue
        readable = True
        if got == base_id:
            return True
    return False if (readable or not unknown) else None


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
    """Is `path` really inside `base`? (The ALLOW question: never the fold; undecidable is outside.)
    A `base` that does not exist YET is judged by its deepest existing ancestor (identity) and its own
    missing names, spelled EXACTLY (`_absent_base`)."""
    p, b = os.path.realpath(path), os.path.realpath(base)
    base_id, absent = _ident_or_absent(b)
    if base_id is not None:
        return bool(_identity_walk(p, base_id))
    if absent and not os.path.lexists(b) and not _dangles(base):
        return _absent_base(b, p) is not None
    return False                                        # UNKNOWN base: undecidable is outside


def _dangles(path: str) -> bool:
    """Is the deepest EXISTING entry of `path` as spelled a symlink? Then `path` is absent only because a
    link names nothing: that is not "not created yet" but a link whose target is unknown, and it gets the
    unknown answer (outside), never the absent-base shape."""
    cur = os.path.abspath(path)
    while not os.path.lexists(cur):
        parent = os.path.dirname(cur)
        if parent == cur:
            return False
        cur = parent
    return os.path.islink(cur) or _is_junction(cur)


def _is_junction(path: str) -> bool:
    """A Windows junction (or any reparse point): `islink()` answers False for a junction, so a dangling
    junction would pass for a base not created yet (verifier Low)."""
    isj = getattr(os.path, "isjunction", None)
    if isj is not None and isj(path):
        return True
    try:
        return bool(getattr(os.lstat(path), "st_file_attributes", 0) & _NT_REPARSE)
    except OSError:
        return False


def _absent_base(real_base: str, p: str) -> Optional[Tuple[List[str], str]]:
    """`(names below the base, deepest existing)` for a path inside a base that does NOT exist yet (no
    `~/.claude` on a fresh machine): the deepest existing ancestor of the base is found by IDENTITY, and the
    base's missing components must be the path's next components spelled EXACTLY — never folded: an absent
    directory has no identity to fold against, so a flipped spelling of it is not its shape (outside: the
    ALLOW question's fail-closed answer). None otherwise."""
    anchor, missing = real_base, []
    while not os.path.lexists(anchor):
        head, tail = os.path.split(anchor)
        if head == anchor or not tail:
            return None
        missing.insert(0, tail)
        anchor = head
    anchor_id = _ident(anchor)
    if anchor_id is None:
        return None
    got = _disk_rel(anchor, anchor_id, p)
    if got is None or got[0]:
        return None                       # a component below the anchor EXISTS: not the absent base
    tail = got[1]
    if tail[:len(missing)] != missing:
        return None
    return tail[len(missing):], got[2]


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
    root_id, absent = _ident_or_absent(real_root)
    if root_id is None and absent and not os.path.lexists(real_root) and not _dangles(root):
        got = _absent_base(real_root, p)                # the root does not exist yet: every name is tail
        return None if got is None else ([], got[0], got[1])
    return _disk_rel(real_root, root_id, p)


def _disk_rel(real_root: str, root_id: Optional[Tuple[int, int]],
              p: str) -> Optional[Tuple[List[str], List[str], str]]:
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


# ------------------------------------------------------------------------------ the contained read

#: Windows device names: a component with one of these stems (any extension, any case) opens a DEVICE.
_NT_DEVICES = frozenset({"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$", "CLOCK$"}
                        | {f"{k}{n}" for k in ("COM", "LPT") for n in "123456789¹²³"})
_NT_REPARSE = 0x400                     # FILE_ATTRIBUTE_REPARSE_POINT
#: Can `os.open` take `dir_fd` here? Read once at import (a later patch of `os.open` is a test seam).
_OPEN_DIR_FD = os.open in os.supports_dir_fd


def _part_ok(part: str) -> bool:
    if not isinstance(part, str) or part in ("", ".", "..") or "\0" in part:
        return False
    if "/" in part or "\\" in part or os.sep in part or (os.altsep and os.altsep in part):
        return False
    if os.name == "nt":
        if ":" in part:
            return False                                 # an alternate data stream (or a drive)
        stem = part.split(".", 1)[0].rstrip(" ").upper()
        if stem in _NT_DEVICES:
            return False
    return True


def _sid(st: os.stat_result) -> Optional[Tuple[int, int]]:
    return (st.st_dev, st.st_ino) if st.st_ino else None


def _gitdirs(text: str, real_root: str) -> List[str]:
    """The git dir (and its `commondir`) a `.git` FILE's text names — `mokata_emit._gitfile_dirs`'s
    parse, fed bytes read through the held root."""
    line = text.strip()
    if not line.startswith("gitdir:"):
        return []
    gitdir = os.path.join(real_root, line[len("gitdir:"):].strip())
    out = [gitdir]
    try:
        with open(os.path.join(gitdir, "commondir"), "r", encoding="utf-8", errors="replace") as fh:
            common = fh.read(4096).strip()
        if common:
            out.append(os.path.join(gitdir, common))
    except OSError:
        pass
    return out


def _held_store_ids(rootfd: int, real_root: str) -> Set[Tuple[int, int]]:
    """POSIX: the identities of the repo's private stores, asked of the HELD root (`dir_fd`), not of a
    path a swap could re-point. A `.git` FILE is read through the held root. Any failure but "absent"
    raises (the caller refuses)."""
    from mokata_emit import PRIVATE_DIRS
    ids: Set[Tuple[int, int]] = set()
    for name in PRIVATE_DIRS:
        try:
            st = os.stat(name, dir_fd=rootfd)
        except (FileNotFoundError, NotADirectoryError):
            continue
        got = _sid(st)
        if got:
            ids.add(got)
        if _stat_mod.S_ISREG(st.st_mode):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
                         | getattr(os, "O_CLOEXEC", 0), dir_fd=rootfd)
            try:
                text = os.read(fd, 4096).decode("utf-8", "replace")
            finally:
                os.close(fd)
            for d in _gitdirs(text, real_root):
                try:
                    got = _sid(os.stat(d))
                except (FileNotFoundError, NotADirectoryError):
                    continue
                if got:
                    ids.add(got)
    return ids


def _root_listing(root: str) -> Dict[str, "os.DirEntry"]:
    """ONE `os.scandir` of the root: every entry's name and inode from a single directory read. It is the
    FIRST filesystem event of a pin (0.0.29 H-b round 3), so every store that sits in the root — `.git`,
    `.mokata`, and a gitfile layout's git dir such as `.gitdata` — is judged at the same instant: there is no
    event of the call before which one of them could be moved while another is already judged."""
    with os.scandir(root) as it:
        return {e.name: e for e in it}


def _listed_store_ids(listing: Dict[str, "os.DirEntry"], rootfd: int, real_root: str,
                      dev: int) -> Set[Tuple[int, int]]:
    """The stores' identities from the root `listing` (inode from the directory entry, device of the held
    root). Read LATER, by a stat — disclosed, the one place a store is judged after the first event: a store
    that is a SYMLINK in the root (its target), and a gitfile's git dir / `commondir` that is not an entry of
    the root itself (a linked worktree's or a submodule's, outside the root, or deeper in it). A store that is
    a mount point lists the covered inode, so the post-open comparison refuses (fail closed). Any failure but
    "absent" raises (the caller refuses)."""
    from mokata_emit import PRIVATE_DIRS
    ids: Set[Tuple[int, int]] = set()

    def listed(name: str) -> Optional[Tuple[int, int]]:
        e = listing.get(name)
        if e is None:
            return None
        if e.is_symlink():
            try:
                return _sid(os.stat(name, dir_fd=rootfd))
            except (FileNotFoundError, NotADirectoryError):
                return None
        ino = e.inode()
        return (dev, ino) if ino else None

    for name in PRIVATE_DIRS:
        got = listed(name)
        if got is None:
            continue
        ids.add(got)
        e = listing[name]
        if e.is_file(follow_symlinks=True):                   # a `.git` FILE: the git dir it names
            fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
                         | getattr(os, "O_CLOEXEC", 0), dir_fd=rootfd)
            try:
                text = os.read(fd, 4096).decode("utf-8", "replace")
            finally:
                os.close(fd)
            for d in _gitdirs(text, real_root):
                nd = os.path.normpath(d)
                if os.path.dirname(nd) == real_root and os.path.basename(nd) in listing:
                    got = listed(os.path.basename(nd))      # an entry of the root: judged at event 1
                else:
                    try:
                        got = _sid(os.stat(nd))
                    except (FileNotFoundError, NotADirectoryError):
                        continue
                if got:
                    ids.add(got)
    return ids


class Pin:
    """Everything a contained read's DECISION judged, held until the read is over (0.0.29 H-b round 2).

    `pin_root` takes it BEFORE the decision: the store identities, the root's identity, and the root's
    DESCRIPTOR. The decision (`rel`) finds the name under the pinned identity, and `open_beneath(..., pin=)`
    walks from the pinned descriptor and judges every directory and the file against the pinned stores — so
    nothing the decision relied on is sampled a second time (the hole the `mv .git docs` race went through:
    the stores were sampled again after the decision, by then already moved). Close it (or use `with`)."""

    __slots__ = ("root", "real_root", "fd", "ident", "stores")

    def __init__(self, root: str, real_root: str, fd: int, ident: Tuple[int, int],
                 stores: Set[Tuple[int, int]]) -> None:
        self.root, self.real_root, self.fd, self.ident = root, real_root, fd, ident
        self.stores = frozenset(stores)

    def rel(self, path: str) -> Optional[Tuple[List[str], List[str], str]]:
        """`disk_rel` against the PINNED root identity (not a fresh stat of the root path)."""
        return _disk_rel(self.real_root, self.ident, os.path.realpath(path))

    def close(self) -> None:
        if self.fd >= 0:
            fd, self.fd = self.fd, -1
            try:
                os.close(fd)
            except OSError:
                pass

    def __enter__(self) -> "Pin":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def pin_root(root: str) -> Optional[Pin]:
    """Pin `root` for one contained read, or None (fail closed). POSIX: ONE listing of the root (its first
    event: every in-root store judged at once, `_root_listing`), then the root opened `O_DIRECTORY`, its
    identity from `fstat`, and the store identities from the listing (`_listed_store_ids`). Whether the held descriptor's stores are
    the pinned set is asked ONCE, after the file is open (`open_beneath`): a store that moved at any instant
    after the listing — during the pin or the walk — refuses there. (Round 2 dropped a second, identical
    comparison at pin time: each hid the other from every mutant.) Windows: the root is a held handle (no
    delete share: it cannot be renamed while held) and the stores are sampled before and after taking it."""
    if os.name == "nt":
        return _pin_nt(root)
    try:
        listing = _root_listing(root)                        # event 1: every in-root store at once
        real_root = os.path.realpath(root)
    except (OSError, ValueError, TypeError):
        return None
    directory = getattr(os, "O_DIRECTORY", 0)
    if not directory or not getattr(os, "O_NOFOLLOW", 0) or not _OPEN_DIR_FD:
        return None                                      # never a silent follow
    fd = -1
    try:
        fd = os.open(real_root, os.O_RDONLY | directory | getattr(os, "O_CLOEXEC", 0))
        st = os.fstat(fd)
        ident = _sid(st)
        if ident is None:
            return None                                  # a root with no identity cannot be pinned
        stores = _listed_store_ids(listing, fd, real_root, st.st_dev)
        pin, fd = Pin(root, real_root, fd, ident, stores), -1
        return pin
    except (OSError, ValueError):
        return None
    finally:
        if fd >= 0:
            os.close(fd)


def _open_beneath_posix(pin: Pin, parts: List[str]) -> Optional[int]:
    from mokata_emit import hardlinked_private
    nofollow, directory = getattr(os, "O_NOFOLLOW", 0), getattr(os, "O_DIRECTORY", 0)
    cloexec, nonblock = getattr(os, "O_CLOEXEC", 0), getattr(os, "O_NONBLOCK", 0)
    held: List[int] = []
    ffd = -1
    try:
        cur = pin.fd
        for part in parts[:-1]:
            fd = os.open(part, os.O_RDONLY | directory | nofollow | nonblock | cloexec, dir_fd=cur)
            held.append(fd)
            if _sid(os.fstat(fd)) in pin.stores:
                return None                              # a pinned store, by whatever name it has now
            cur = fd
        ffd = os.open(parts[-1], os.O_RDONLY | nofollow | nonblock | getattr(os, "O_NOCTTY", 0)
                      | cloexec, dir_fd=cur)
        st = os.fstat(ffd)
        ok = (_stat_mod.S_ISREG(st.st_mode)
              and _sid(st) not in pin.stores
              and _held_store_ids(pin.fd, pin.real_root) == pin.stores
              and _ident(pin.real_root) == pin.ident
              and not hardlinked_private(pin.real_root, st))
        if ok:
            got, ffd = ffd, -1
            return got
        return None
    except (OSError, ValueError):
        return None
    finally:
        if ffd >= 0:
            os.close(ffd)
        for fd in held:
            try:
                os.close(fd)
            except OSError:
                pass


def _nt_dir_fd(path: str, follow: bool, list_dir: bool = False) -> int:
    """Windows: a directory HANDLE as a CRT fd, shared for read and write but NOT delete — so while it is
    held the directory cannot be renamed or replaced — BUT only when it is opened with data access: a handle
    with FILE_READ_ATTRIBUTES alone takes no part in share checks (0.0.29 H-b xplat, found on the first
    Windows run), so every handle the contained read HOLDS passes `list_dir` (FILE_LIST_DIRECTORY), which
    also lets the root's handle enumerate its own entries (`_nt_listing`). `follow=False` opens a reparse
    point itself."""
    import msvcrt
    from . import winacl as W
    ctypes, _w, _adv, ker = W._win()
    flags = W._BACKUP_SEMANTICS | (0 if follow else W._OPEN_REPARSE)
    access = W._FILE_READ_ATTRIBUTES | (0x1 if list_dir else 0)          # 0x1: FILE_LIST_DIRECTORY
    h = ker.CreateFileW(path, access, 0x1 | 0x2, None, W._OPEN_EXISTING, flags, None)
    if h is None or h == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    fd = -1
    try:
        fd = msvcrt.open_osfhandle(h, os.O_RDONLY)
    finally:
        if fd < 0:
            ker.CloseHandle(h)                           # the CRT never took the handle
    return fd


def _nt_store_ids(real_root: str) -> Set[Tuple[int, int]]:
    from mokata_emit import store_dirs
    ids: Set[Tuple[int, int]] = set()
    for d in store_dirs(real_root):
        try:
            fd = _nt_dir_fd(d, follow=True)
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError as exc:
            if getattr(exc, "winerror", None) in (2, 3, 267):   # not found / path / not a directory
                continue
            raise
        try:
            got = _sid(os.fstat(fd))
        finally:
            os.close(fd)
        if got:
            ids.add(got)
    return ids


def _final_parts(text: Optional[str]) -> Optional[List[str]]:
    return [c for c in text.split("\\") if c] if text else None


def _same_component(a: str, b: str) -> bool:
    return a == b or _key(a) == _key(b)


#: FILE_ID_BOTH_DIR_INFO (x64 and x86 alike): the offsets `_nt_listing` reads.
_NT_NEXT, _NT_ATTRS, _NT_NAMELEN, _NT_FILEID, _NT_NAME = 0, 56, 60, 96, 104


def _nt_listing(fd: int) -> Dict[str, Tuple[int, int]]:
    """Windows: ONE enumeration of the HELD directory handle — `GetFileInformationByHandleEx` with
    `FileIdBothDirectoryInfo` — every entry's name -> (file id, attributes), read from the directory itself
    (the NTFS file id is what `os.stat` reports as `st_ino`). The Windows twin of `_root_listing` (0.0.29 H-b
    xplat): every store that is an entry of the root is judged by this one read. Raises on any failure (the
    caller refuses)."""
    import ctypes
    import msvcrt
    from ctypes import wintypes
    from . import winacl as W
    _c, _w, _adv, ker = W._win()
    fn = ker.GetFileInformationByHandleEx
    fn.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    fn.restype = wintypes.BOOL
    h = msvcrt.get_osfhandle(fd)
    out: Dict[str, Tuple[int, int]] = {}
    size = 64 * 1024
    buf = ctypes.create_string_buffer(size)
    klass = 11                                            # FileIdBothDirectoryRestartInfo, then 10
    while True:
        if not fn(h, klass, buf, size):
            err = ctypes.get_last_error()
            if err == 18:                                 # ERROR_NO_MORE_FILES
                return out
            raise ctypes.WinError(err)
        klass = 10                                        # FileIdBothDirectoryInfo
        raw, off = buf.raw, 0
        while True:
            nxt = int.from_bytes(raw[off + _NT_NEXT:off + _NT_NEXT + 4], "little")
            attrs = int.from_bytes(raw[off + _NT_ATTRS:off + _NT_ATTRS + 4], "little")
            nlen = int.from_bytes(raw[off + _NT_NAMELEN:off + _NT_NAMELEN + 4], "little")
            fid = int.from_bytes(raw[off + _NT_FILEID:off + _NT_FILEID + 8], "little")
            name = raw[off + _NT_NAME:off + _NT_NAME + nlen].decode("utf-16-le")
            out[name] = (fid, attrs)
            if not nxt:
                break
            off += nxt


def _nt_listed_store_ids(listing: Dict[str, Tuple[int, int]], real_root: str, dev: int) -> Set[Tuple[int, int]]:
    """The stores' identities from the root's one enumeration (`_nt_listing`), as `_listed_store_ids` does on
    POSIX. Read LATER, by a held handle — disclosed, exactly as on POSIX: a store that is a reparse point in
    the root (its target), and a gitfile's git dir / `commondir` that is not an entry of the root. Any failure
    but "absent" raises (the caller refuses)."""
    from mokata_emit import PRIVATE_DIRS, _gitfile_dirs

    def later(path: str) -> Optional[Tuple[int, int]]:
        try:
            fd = _nt_dir_fd(path, follow=True)
        except (FileNotFoundError, NotADirectoryError):
            return None
        except OSError as exc:
            if getattr(exc, "winerror", None) in (2, 3, 267):
                return None
            raise
        try:
            return _sid(os.fstat(fd))
        finally:
            os.close(fd)

    def listed(name: str) -> Optional[Tuple[int, int]]:
        got = _lookup_nt(listing, name)
        if got is None:
            return None
        fid, attrs = got
        if attrs & _NT_REPARSE:
            return later(os.path.join(real_root, name))
        return (dev, fid) if fid else None

    ids: Set[Tuple[int, int]] = set()
    for name in PRIVATE_DIRS:
        got = listed(name)
        if got is None:
            continue
        ids.add(got)
        hit = _lookup_nt(listing, name)
        if hit is not None and not hit[1] & 0x10:            # not FILE_ATTRIBUTE_DIRECTORY: a gitfile
            for d in _gitfile_dirs(os.path.join(real_root, name)):
                nd = os.path.normpath(d)
                if os.path.normcase(os.path.dirname(nd)) == os.path.normcase(real_root):
                    g = listed(os.path.basename(nd))         # an entry of the root: judged by the listing
                else:
                    g = later(nd)
                if g:
                    ids.add(g)
    return ids


def _lookup_nt(listing: Dict[str, Tuple[int, int]], name: str) -> Optional[Tuple[int, int]]:
    """`name`'s entry in an NTFS listing, which opens a name under any case."""
    got = listing.get(name)
    if got is not None:
        return got
    key = _key(name)
    return next((v for k, v in listing.items() if _key(k) == key), None)


def _pin_nt(root: str) -> Optional[Pin]:
    """Windows: the root's HELD handle (no delete share: it cannot be renamed while held), then ONE
    enumeration of that handle judges every in-root store at once (`_nt_listing`, the twin of POSIX's first
    listing). The stores are asked again, by path, after the file is open (`_open_beneath_nt`)."""
    fd = -1
    try:
        real_root = os.path.realpath(root)
        fd = _nt_dir_fd(real_root, follow=True, list_dir=True)
        listing = _nt_listing(fd)                        # every in-root store, one directory read
        st = os.fstat(fd)
        ident = _sid(st)
        if ident is None:
            return None
        stores = _nt_listed_store_ids(listing, real_root, st.st_dev)
        pin, fd = Pin(root, real_root, fd, ident, stores), -1
        return pin
    except (OSError, ValueError, TypeError):
        return None
    finally:
        if fd >= 0:
            os.close(fd)


def _open_beneath_nt(pin: Pin, parts: List[str]) -> Optional[int]:
    from mokata_emit import hardlinked_private, is_private_name
    from .winacl import final_path
    held: List[int] = []
    ffd = -1
    try:
        cur = pin.real_root
        for part in parts[:-1]:
            cur = os.path.join(cur, part)
            fd = _nt_dir_fd(cur, follow=False, list_dir=True)   # list access: a held dir cannot be renamed
            held.append(fd)
            st = os.fstat(fd)
            if getattr(st, "st_file_attributes", 0) & _NT_REPARSE or not _stat_mod.S_ISDIR(st.st_mode):
                return None                              # a junction / link where a directory was
            if _sid(st) in pin.stores:
                return None
        ffd = os.open(os.path.join(cur, parts[-1]),
                      os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0))
        st = os.fstat(ffd)
        if not _stat_mod.S_ISREG(st.st_mode):
            return None
        rp, fp = _final_parts(final_path(pin.fd)), _final_parts(final_path(ffd))
        if rp is None or fp is None or len(fp) != len(rp) + len(parts):
            return None
        if not all(_same_component(a, b) for a, b in zip(fp, rp + parts)):
            return None                                  # the handle is not where the walk named
        if is_private_name("/".join(fp[len(rp):])):
            return None
        ok = (_sid(st) not in pin.stores
              and _nt_store_ids(pin.real_root) == pin.stores
              and not hardlinked_private(pin.real_root, st))
        if ok:
            got, ffd = ffd, -1
            return got
        return None
    except (OSError, ValueError):
        return None
    finally:
        # Every directory handle is held until the file is verified or refused (attack H2-4).
        if ffd >= 0:
            os.close(ffd)
        for fd in held:
            try:
                os.close(fd)
            except OSError:
                pass


def open_beneath(root: str, parts: List[str], pin: Optional[Pin] = None) -> Optional[int]:
    """THE contained read (0.0.29 H2): a read-only fd for the regular file `parts` names below `root`, or
    None. Never raises; the caller closes the fd.

    `pin` is what the caller's decision judged (`pin_root(root)`, taken BEFORE the decision); without one,
    the call pins `root` itself (it is then its own decision). A pin of a different root, or a closed one,
    refuses. Nothing the decision judged is sampled again: the walk starts at the pinned DESCRIPTOR and every
    directory and the file are judged against the pinned STORES.

    POSIX: every directory part is opened relative to the held fd with `O_DIRECTORY|O_NOFOLLOW` (a pinned
    store refuses at once); the file with `O_NOFOLLOW|O_NONBLOCK|O_NOCTTY` and must be `S_ISREG`, not a pinned
    store, not a hard link into one; and, after the open, the stores asked of the held root are still the
    pinned set and the root path still names the pinned root. Windows: no `:` and no device name; the root and
    every directory are HELD handles (no delete share: they cannot be renamed while held; a reparse point
    refuses); the file handle's final path must be the root handle's final path plus `parts`; then the same
    identity checks.

    Disclosed: a mount point inside the repo is a real directory to the walk (only `openat2` with
    `RESOLVE_NO_XDEV` stops it, and Python has no wrapper); a hard link inside the repo to an outside file is
    that file; the hard-link index skips git's object database (`mokata_emit._walk_store`)."""
    parts = list(parts or [])
    if not parts or not all(_part_ok(p) for p in parts):
        return None
    if pin is None:
        own = pin_root(root)
        if own is None:
            return None
        with own:
            return open_beneath(root, parts, pin=own)
    if pin.fd < 0 or pin.root != root:
        return None                                      # a pin judged another root (or is spent)
    if os.name == "nt":
        return _open_beneath_nt(pin, parts)
    return _open_beneath_posix(pin, parts)


__all__ = ["refuse_if_inside", "require_inside", "same_entry", "identity_known", "disk_spelling",
           "disk_rel", "folds_case", "forget", "REFRESH_SECS", "open_beneath", "pin_root", "Pin"]
