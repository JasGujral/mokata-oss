"""Per-write snapshots — what makes "the diff at ANY point in time" answerable (0.0.22 E2).

A content-addressed, deduplicated store under `.mokata/temp_local/snapshots/` (doc 85 §1b: mokata's
own transient area, NOT git's object store — writing into the user's `.git` is a durable write to a
tree mokata does not own):

    objects/<sha[:2]>/<sha>     zlib-compressed file content, written once per distinct content
    timeline.jsonl              one line per capture: when, which session/run, which file, which
                                content, and whether it was the BEFORE of a session's first write
                                or the AFTER of a write
    first/<session>.json        the files this session has already captured a BEFORE for

Captured from hooks mokata already runs: gate-guard records the BEFORE the first time a session
writes a file, dirty-track records the AFTER of every native write. Bounded: a file over
`MAX_FILE_BYTES` is recorded as `too-large` (its content is not kept), and the timeline keeps the
newest `MAX_ENTRIES` lines, with objects no surviving line references collected.

⚠ BOUND: a write made through Bash is not snapshotted (no PostToolUse lane sees its result). The
live diff still shows it — it reads the working tree — but its point-in-time history starts at the
next native write.

Never raises: a capture that cannot be made is skipped; the write it observes is never affected.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import zlib
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Tuple

from ..tdd_state import state_dir

MAX_FILE_BYTES = 1024 * 1024
MAX_ENTRIES = 20000
BEFORE, AFTER = "before", "after"
OK, ABSENT, TOO_LARGE = "ok", "absent", "too-large"
#: 0.0.27 CASEFOLD-FIX r2: a write into `.git/` or `.mokata/` (any spelling) is RECORDED - its path,
#: its time, its session - but its content is never read, stored or served.
PRIVATE = "private"
PRIVATE_NOTE = "private path \u2014 content not captured"

_SID_SAFE = "".join(chr(c) for c in range(48, 58)) + "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-_"


def store_dir(root: str) -> str:
    return os.path.join(os.path.dirname(state_dir(root)), "snapshots")


@dataclass(frozen=True)
class Snapshot:
    id: str
    t: float
    session: str
    run: str
    path: str            # repo-relative, '/'-separated
    kind: str            # before | after
    sha: str             # "" unless state == ok
    state: str           # ok | absent | too-large
    size: int

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Snapshot":
        return cls(str(d.get("id", "")), float(d.get("t", 0)), str(d.get("session") or ""),
                   str(d.get("run") or ""), str(d.get("path", "")), str(d.get("kind", "")),
                   str(d.get("sha") or ""), str(d.get("state", "")), int(d.get("size") or 0))

    def facts(self) -> Dict[str, Any]:
        return {"id": self.id, "t": self.t, "session": self.session, "run": self.run,
                "path": self.path, "kind": self.kind, "sha": self.sha, "state": self.state,
                "size": self.size}


def rel_path(root: str, path: str, cwd: Optional[str] = None) -> Optional[str]:
    """`path` as a repo-relative '/' name, or None when it is outside the repo (realpath'd —
    a symlink out of the tree is outside) or is PRIVATE: inside `.git/` or `.mokata/` at any depth.

    THE ONE CONTAINMENT RULE every IDE read route goes through (directly, or via
    `nav.contained_rel` / `diffs.open_contained`). 0.0.27 CASEFOLD-FIX: "private" is decided
    twice, and either refuses — by NAME (`repo_paths.is_private_name`: every component compared
    case-folded and Windows-folded, so the answer is the same on every filesystem) and by IDENTITY
    (`repo_paths.aliases_private`: the same file on disk as the repo's own `.git`/`.mokata`, for
    an 8.3 short name or a folding the name rule does not model). Anything that cannot be decided
    is refused — fail closed.

    This answers "may I READ it". "May I RECORD a write to it" is `record_name`."""
    got = record_name(root, path, cwd)
    if got is None or got[1]:
        return None                               # git's store and mokata's record are not "code"
    return got[0]


def record_name(root: str, path: str, cwd: Optional[str] = None) -> Optional[Tuple[str, bool]]:
    """`(name, private)` for a path INSIDE the repo, or None when it is outside (realpath'd).

    The RECORD side of the split (verify M1): a write into `.git/hooks/` is exactly what the
    activity feed and the snapshot timeline must still show, so the private rule does not drop it
    here - it is reported as `private`, and the caller records the path and never the content.
    Undecidable means private: the content is then not read (fail closed)."""
    from .. import repo_paths as RP
    try:
        real_root = os.path.realpath(root)
        ab = path if os.path.isabs(path) else os.path.join(cwd or root, path)
        rel = str(RP.name_of(os.path.realpath(ab), real_root))
    except (OSError, ValueError, TypeError):
        return None
    if rel in (".", "..") or rel.startswith("../") or os.path.isabs(rel):
        return None
    try:
        private = bool(RP.is_private_name(rel) or RP.aliases_private(real_root, rel))
    except (OSError, ValueError, TypeError):
        private = True
    if private:
        rel = _canonical_store_name(rel, real_root)
    return rel, private


def _canonical_store_name(rel: str, real_root: str) -> str:
    """The recorded spelling of a private path: every component that IS `.git` or `.mokata` (case,
    Windows trailing dot/space, Unicode folding) is written as the store's own name. Without this a
    write to `.MOKATA/x` was recorded `.mokata/x` on Windows (realpath answers the disk's case) and
    `.MOKATA/x` on macOS (it keeps the typed case): one file, two records. The rest of the path is
    left as the OS named it.

    Only where the two spellings are the SAME directory on disk (`samefile`): on a case-sensitive
    filesystem `.MOKATA` is a different directory from `.mokata`, a write into it is not a write to
    mokata's store, and it is recorded as typed."""
    from .. import repo_paths as RP
    try:
        parts = RP.split_path(rel)
        out = []
        for part in parts:
            key = RP.fold_component(part)
            if key in (".git", ".mokata") and part != key and _same_dir(real_root, out, part, key):
                part = key
            out.append(part)
        return "/".join(out)
    except (ValueError, TypeError):
        return rel


def _same_dir(real_root: str, done: List[str], typed: str, canonical: str) -> bool:
    try:
        return os.path.samefile(os.path.join(real_root, *done, typed),
                                os.path.join(real_root, *done, canonical))
    except (OSError, ValueError):
        return False


#: The plain words of a read refusal for each kind of private path (0.0.27 SEC-HARDEN). A refusal
#: that says "outside the repository" about `.git/config` is wrong about where the file is, and a
#: reader who knows it is inside the repo concludes the IDE is broken, not deliberate.
REFUSE_GIT = ("{path} is inside git's own store (.git/) — git's config, hooks and history are not "
              "code, and mokata ide does not open them")
REFUSE_MOKATA = "{path} is mokata's own record (.mokata/), not code — mokata ide does not open it"
REFUSE_ALIAS = ("{path} is another name (a link or an alias) for a file in git's or mokata's "
                "private store (.git/ or .mokata/) — mokata ide does not open it")


def refusal(root: str, path: str, otherwise: str, cwd: Optional[str] = None) -> str:
    """The plain-words reason `rel_path(root, path, cwd)` said no: git's store, mokata's record, an
    alias of either, or — for a path that is not private — `otherwise` (the caller's own words for
    "outside"/"not a repo path"). Words only; never a decision."""
    got = record_name(root, path, cwd) if path else None
    if got is None or not got[1]:
        return otherwise
    from .. import repo_paths as RP
    try:
        keys = [RP.fold_component(p) for p in RP.split_path(got[0])]
    except (ValueError, TypeError):
        keys = []
    for k in keys:
        if k == RP.fold_component(RP.PRIVATE_DIRS[0]):
            return REFUSE_GIT.format(path=path)
        if k == RP.fold_component(RP.PRIVATE_DIRS[1]):
            return REFUSE_MOKATA.format(path=path)
    return REFUSE_ALIAS.format(path=path)


def git_hook(rel: str) -> bool:
    """Is `rel` inside a `.git/hooks/` directory (any spelling, any depth)? A hook runs on the
    next commit, so a write there is flagged in the activity panel, never shown as an ordinary
    edit. Undecidable is a hook."""
    from .. import repo_paths as RP
    try:
        keys = [RP.fold_component(p) for p in RP.split_path(rel)]
    except (ValueError, TypeError):
        return True
    return any(a == ".git" and b == "hooks" for a, b in zip(keys, keys[1:]))


def _safe_sid(session_id: str) -> str:
    return "".join(c if c in _SID_SAFE else "_" for c in session_id)[:80] or "_"


def _write_object(root: str, data: bytes) -> str:
    sha = hashlib.sha256(data).hexdigest()
    path = os.path.join(store_dir(root), "objects", sha[:2], sha)
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + f".tmp-{os.getpid()}"
        with open(tmp, "wb") as fh:
            fh.write(zlib.compress(data, 6))
        os.replace(tmp, path)
    return sha


def read_object(root: str, sha: str) -> Optional[bytes]:
    if not sha or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        return None
    try:
        with open(os.path.join(store_dir(root), "objects", sha[:2], sha), "rb") as fh:
            return zlib.decompress(fh.read())
    except (OSError, zlib.error):
        return None


def capture(root: str, path: str, kind: str, session_id: Optional[str] = None,
            run_id: Optional[str] = None, cwd: Optional[str] = None,
            observed: Optional[Tuple[str, int, float, Optional[bytes]]] = None
            ) -> Optional[Snapshot]:
    """Record the CURRENT content of `path` as a `kind` snapshot. None when it is outside the repo
    or anything fails.

    `observed` = `(state, size, t, data)` is the file AS SOMEONE ELSE SAW IT, at time `t` — the
    `mokata-hook` front door reads it at hook time and spools it (0.0.26 O3), so a drained AFTER
    snapshot is what the file held when the tool finished, not whatever the working tree holds by
    the time the drainer runs. With `observed`, the working tree is NEVER read."""
    try:
        got = record_name(root, path, cwd)
        if got is None:
            return None
        rel, private = got
        sha, state, size = "", ABSENT, 0
        if private or (observed is not None and observed[0] == PRIVATE):
            # path only: no byte is read, kept or served. The front door's own PRIVATE verdict
            # (0.0.27 SEC-HARDEN: it read no byte) is honoured even if the tree changed since.
            state, t = PRIVATE, (observed[2] if observed is not None else time.time())
        elif observed is not None:
            o_state, size, t, data = observed
            if o_state == OK and data is not None and len(data) <= MAX_FILE_BYTES:
                sha, state = _write_object(root, data), OK
            elif o_state == TOO_LARGE:
                state = TOO_LARGE
            else:
                state, size = ABSENT, 0
        else:
            from ..repo_paths import path_of
            ab = path_of(root, rel)
            if os.path.isfile(ab):
                size = os.path.getsize(ab)
                if size > MAX_FILE_BYTES:
                    state = TOO_LARGE
                else:
                    with open(ab, "rb") as fh:
                        sha = _write_object(root, fh.read())
                    state = OK
            t = time.time()
        snap = Snapshot(f"{int(t * 1000)}-{(sha or state)[:10]}-{kind[0]}", t, session_id or "",
                        run_id or "", rel, kind, sha, state, size)
        _append(root, snap)
        return snap
    except Exception:  # noqa: BLE001 — observability never touches the write it observes
        return None


def capture_before_first_write(root: str, path: str, session_id: Optional[str],
                               run_id: Optional[str] = None,
                               cwd: Optional[str] = None) -> Optional[Snapshot]:
    """The BEFORE of this session's FIRST write to `path` — once per (session, file)."""
    if not session_id:
        return None
    try:
        from ..oslock import file_lock
        got = record_name(root, path, cwd)
        if got is None:
            return None
        rel = got[0]
        idx_path = os.path.join(store_dir(root), "first", _safe_sid(session_id) + ".json")
        os.makedirs(os.path.dirname(idx_path), exist_ok=True)
        # 0.0.23 C4 (VERIFICATION-MINORS-0022 "first-write index unlocked"): the read → check →
        # capture → write is ONE step under the OS lock, so two hooks racing on one session's
        # first write record ONE before, and neither overwrites the other's index entry.
        with file_lock(idx_path + ".lock"):
            seen: Dict[str, str] = {}
            if os.path.exists(idx_path):
                with open(idx_path, encoding="utf-8") as fh:
                    seen = json.load(fh) or {}
            if rel in seen:
                return None
            snap = capture(root, rel, BEFORE, session_id, run_id)
            if snap is None:
                return None
            seen[rel] = snap.id
            tmp = idx_path + f".tmp-{os.getpid()}"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(seen, fh)
            os.replace(tmp, idx_path)
            return snap
    except Exception:  # noqa: BLE001
        return None


def _timeline_path(root: str) -> str:
    return os.path.join(store_dir(root), "timeline.jsonl")


def _append(root: str, snap: Snapshot) -> None:
    from ..oslock import file_lock
    path = _timeline_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with file_lock(path + ".lock"):
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(snap.facts()) + "\n")
        _maybe_prune(root, path)


def _maybe_prune(root: str, path: str) -> None:
    """Keep the newest `MAX_ENTRIES` lines once the file is 10% over; collect orphaned objects."""
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return
    if len(lines) <= MAX_ENTRIES * 1.1:
        return
    keep = lines[-MAX_ENTRIES:]
    # 0.0.23 C4 (VERIFICATION-MINORS-0022 "pruning can drop a BEFORE"): a session's diff is its
    # FIRST write's BEFORE against its latest AFTER. Cutting by age alone dropped that BEFORE
    # while the session's later lines survived, and its diff silently lost its baseline. A BEFORE
    # whose (session, file) still has a surviving line is kept, ahead of the cut, in order.
    alive = set()
    for ln in keep:
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        alive.add((d.get("session"), d.get("path")))
    pinned = []
    for ln in lines[:-MAX_ENTRIES]:
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if d.get("kind") == BEFORE and (d.get("session"), d.get("path")) in alive:
            pinned.append(ln)
    keep = pinned + keep
    tmp = path + f".tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.writelines(keep)
    os.replace(tmp, path)
    live = set()
    for ln in keep:
        try:
            live.add(json.loads(ln).get("sha") or "")
        except ValueError:
            continue
    objects = os.path.join(store_dir(root), "objects")
    for sub in os.listdir(objects) if os.path.isdir(objects) else ():
        d = os.path.join(objects, sub)
        for name in os.listdir(d):
            if name not in live and ".tmp-" not in name:
                try:
                    os.remove(os.path.join(d, name))
                except OSError:
                    pass


def timeline(root: str, session: Optional[str] = None, run: Optional[str] = None,
             path: Optional[str] = None) -> List[Snapshot]:
    """The captures, oldest first, filtered by any of session / run / path."""
    out: List[Snapshot] = []
    for d in _lines(root):
        s = Snapshot.from_dict(d)
        if session and s.session != session:
            continue
        if run and s.run != run:
            continue
        if path and s.path != path:
            continue
        out.append(s)
    return out


def get(root: str, snap_id: str) -> Optional[Snapshot]:
    for d in _lines(root):
        if d.get("id") == snap_id:
            return Snapshot.from_dict(d)
    return None


def _lines(root: str) -> Iterator[Dict[str, Any]]:
    try:
        with open(_timeline_path(root), encoding="utf-8") as fh:
            for ln in fh:
                try:
                    d = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(d, dict):
                    yield d
    except OSError:
        return


def content_of(root: str, snap: Snapshot) -> Optional[str]:
    """The text a snapshot holds; "" for `absent`; None when too large / binary / unreadable."""
    if snap.state == ABSENT:
        return ""
    if snap.state != OK:
        return None
    data = read_object(root, snap.sha)
    if data is None or b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", errors="replace")


__all__ = ["MAX_FILE_BYTES", "MAX_ENTRIES", "BEFORE", "AFTER", "OK", "ABSENT", "TOO_LARGE",
           "Snapshot", "store_dir", "rel_path", "record_name", "git_hook", "PRIVATE", "refusal",
           "PRIVATE_NOTE", "read_object", "capture",
           "capture_before_first_write", "timeline", "get", "content_of"]
