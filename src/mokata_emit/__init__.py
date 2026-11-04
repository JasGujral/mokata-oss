"""`mokata-hook`'s front door — the stdlib-only observe-hook EMITTER (doc 109 §17.2, ruling U13).

Claude Code launches every mokata hook as a fresh `mokata-hook <sub>` process and WAITS for it. The
observability hooks only record — they never block and never answer — yet they used to do their
recording inside that wait, importing the mokata engine (`dataclasses`, `typing`, the state/run/
harvest/freshness modules) on the harness's clock: 54–125 ms p95 against doc 109's 50 ms budget.

This module is what the `mokata-hook` console script runs. On the hot path it imports ONLY the
standard library (`os`, `sys`, `time`, `threading`, and the C `_json` scanner — not `json`, which drags
in `re`/`enum`/`functools`) and NOTHING from `mokata`:

  * `dirty-track`, `stop`, `session-end`, `pre-compact` are SPOOLED: the payload, argv, cwd, the
    `CLAUDE_*`/`MOKATA_*` environment and — for `dirty-track` — the written file's bytes AS THEY ARE
    AT HOOK TIME are written to one owner-only file in the per-user spool, and a detached DRAINER is
    started to do the work. The hook exits 0. Doc 109's "append to the event log and exit 0".
  * `run-bind` stays SYNCHRONOUS: it writes the session→run binding the phase gate reads on the very
    next tool call, so deferring it would be a correctness change, not a latency trade. Its common
    case (a `session_save` that registered nothing) is answered here on the standard library; a real
    registration runs the engine inline.
  * Everything else (the gates, the context hooks, a mistyped subcommand) goes straight to
    `mokata.hook_cli.main`, unchanged.

The drainer (`python <this dir>/__main__.py --drain <spool>`) holds a directory lock (`os.mkdir` is
atomic on every platform) and runs each entry, in ARRIVAL order, through
`mokata.hook_cli.run_with_stdin` — the same handlers on the same input — then deletes it. A drained
AFTER snapshot is built from the spooled bytes; the drainer never re-reads the working tree. If the
spool cannot be written, or an entry would exceed `MAX_ENTRY_BYTES`, or no drainer can be started, the
work runs inline (slow, never lost).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import sys
import time

#: The subcommands this front DEFERS: `perf.HOOK_KINDS`'s "observe" kind minus `SYNC_OBSERVE` (a test
#: pins it; this module cannot import `perf`). A hook that can block or answer is never here.
DEFERRED = ("dirty-track", "stop", "session-end", "pre-compact")

#: Observe hooks that must NOT be deferred: `run-bind` writes what the next gate decision reads.
SYNC_OBSERVE = ("run-bind",)

#: The observability hooks that do NOTHING outside a mokata repo — each returns 0, silently, with no
#: side effect, once its root resolves to None (and the plugin route's stand-down is the same exit 0).
#: On the PLUGIN route (the one installed globally — `mokata setup` wires the settings route per
#: project) they are answered from `surely_not_mokata` before anything is spooled or imported. The
#: settings route always reaches its handler (test_b3: it never stands down). NOT here: `dirty-track`
#: (it records in any repo), the gate hooks (self-protect and the secret scan run everywhere), and the
#: context hooks (session-start records the plugin root; the others may speak).
#: `mokata.hook_cli` reads this tuple for its own (in-process) fast path.
QUIET_OUTSIDE_MOKATA = ("stop", "session-end", "pre-compact", "subagent-start", "subagent-stop",
                        "run-bind", "stop-gate")

#: The two names Claude Code gives mokata's `session_save` — exactly the strings
#: `run_intent.REGISTER_TOOL_RE` accepts (a test pins the two together).
REGISTER_TOOLS = ("mcp__mokata__session_save", "mcp__plugin_mokata_mokata__session_save")

#: `gate_hook._PATH_KEYS`, in its order — the key `dirty-track`'s `target_path` reads (pinned).
PATH_KEYS = ("file_path", "notebook_path", "path", "target_file")

#: `ide.snapshots.MAX_FILE_BYTES` (pinned): a larger file is recorded "too-large", with no content —
#: exactly what the snapshot store would keep — so its bytes are never read or spooled.
MAX_SNAPSHOT_BYTES = 1024 * 1024
#: `ide.snapshots.PRIVATE` (pinned): the observed state of a private path — no bytes were read.
PRIVATE_STATE = "private"
#: One spool entry (payload + spooled file bytes) larger than this runs inline instead.
MAX_ENTRY_BYTES = 8 * 1024 * 1024

VIA_PLUGIN_FLAG = "--via-plugin"
DRAIN_FLAG = "--drain"
#: `hook_cli.HOOK_TIME_ENV` (pinned): the hook-time end of a spooled call, in its entry's environment.
HOOK_TIME_ENV = "MOKATA_HOOK_T_NS"

#: `MOKATA_HOOK_SPOOL=<dir>` overrides the spool location (tests; a locked-down home).
SPOOL_ENV = "MOKATA_HOOK_SPOOL"
ENTRY_SUFFIX = ".hook"
WORK_SUFFIX = ".work"
LOCK_NAME = "drain.lock"
#: A lock whose directory has not been touched for this long belongs to a drainer that died.
LOCK_STALE_SECS = 60.0
#: Environment carried per entry: one drainer serves every repo and session of this user.
ENV_PREFIXES = ("CLAUDE_", "MOKATA_")

_STDIN_READ_TIMEOUT_SECS = 2.0


# ------------------------------------------------------------------------- the private-path rule
# THE one predicate for "is this git's store or mokata's private store" — single-sourced HERE, in the
# stdlib-only front door, because the front door must apply it BEFORE it copies a written file's
# bytes into its spool (0.0.27 SEC-HARDEN), and it may import nothing from `mokata`. The engine reads
# it through `mokata.repo_paths` (which re-exports these names); every IDE read route reaches it
# through `ide.snapshots.rel_path`.

#: The two directories of a repository that are NEVER repo content: git's own store and mokata's
#: private store. Refused at ANY depth (a nested checkout's `.git`, a sub-project's `.mokata`).
PRIVATE_DIRS = (".git", ".mokata")

#: Inside a git store, the content-addressed object database is the one subtree the hard-link index
#: does not walk: it can hold millions of files, and its bytes are repo HISTORY — content the IDE
#: already serves through its diffs — not git's configuration or hooks.
_GIT_OBJECT_DIRS = ("objects",)


def split_any(path: str) -> list:
    """The components of `path`, split on EITHER separator (a name or a native path). A split, not
    a respelling: nothing is converted, so no name is born here (`repo_paths` owns that)."""
    return [q for p in str(path).split("/") for q in p.split("\\") if q]


def fold_component(part: str) -> str:
    """The comparison key of ONE path component — the same key on every filesystem (0.0.27
    CASEFOLD-FIX).

    A filesystem decides what a name OPENS, and they disagree: APFS (default) and NTFS ignore case,
    APFS also ignores Unicode normalisation (the KELVIN SIGN is a canonical twin of `K`), and
    Windows drops trailing dots and spaces from a component and reads `name:stream` as `name`.
    A refusal that compares spellings case-sensitively is therefore a refusal of one spelling out
    of many — `.MOKATA/x` was served on a Mac. So the key is the union of those foldings, applied
    on EVERY platform: canonical caseless (NFC, then casefold — NFC first, or two canonically
    equal spellings with their marks in a different order fold apart),
    the stream suffix after the first `:` dropped, trailing dots and spaces dropped. Over-refusing
    a Linux file called `.Git` or `.git.` is the stated cost; a decision that depends on the
    filesystem it happens to run on is the defect."""
    import unicodedata
    key = unicodedata.normalize("NFC", part).casefold()
    return key.split(":", 1)[0].rstrip(". ")


def is_private_name(name: str) -> bool:
    """True when ANY component of `name` (either separator) folds to `.git` or `.mokata` — the
    NAME half of the rule. It can raise (a component that cannot be folded); the caller refuses on
    that — fail closed."""
    private = {fold_component(d) for d in PRIVATE_DIRS}
    return any(fold_component(p) in private for p in split_any(name))


def _gitfile_dirs(gitfile: str) -> list:
    """The git dir a `.git` FILE points at (a linked worktree, a submodule, `--separate-git-dir`),
    plus that dir's `commondir` (where a linked worktree's config and hooks really live). [] when it
    is not a readable pointer."""
    try:
        with open(gitfile, "r", encoding="utf-8", errors="replace") as fh:
            line = fh.read(4096).strip()
    except OSError:
        return []
    if not line.startswith("gitdir:"):
        return []
    gitdir = os.path.join(os.path.dirname(gitfile), line[len("gitdir:"):].strip())
    out = [gitdir]
    try:
        with open(os.path.join(gitdir, "commondir"), "r", encoding="utf-8",
                  errors="replace") as fh:
            common = fh.read(4096).strip()
        if common:
            out.append(os.path.join(gitdir, common))
    except OSError:
        pass
    return out


def store_dirs(root: str) -> list:
    """Every directory that IS a private store of the repo at `root`, as paths: its `.mokata`, its
    `.git` (a directory, or the FILE itself plus the git dir and common dir that file points at).
    Paths only — whether each exists is the caller's question."""
    out = [os.path.join(root, d) for d in PRIVATE_DIRS]
    dot_git = os.path.join(root, PRIVATE_DIRS[0])
    if os.path.isfile(dot_git):
        out.extend(_gitfile_dirs(dot_git))
    return out


def _store_ids(root: str) -> set:
    targets = set()
    for d in store_dirs(root):
        try:
            st = os.stat(d)
        except (FileNotFoundError, NotADirectoryError):
            continue
        if st.st_ino:
            targets.add((st.st_dev, st.st_ino))
    return targets


#: realpath(root) -> (directory signature, {(dev, ino) of every file in the stores}). Re-validated on
#: every use by re-stating the directories it was built from: a file created, renamed or replaced in
#: a store changes its directory's mtime, so a stale index is rebuilt, never served.
_LINK_INDEX: dict = {}


def _dir_sig(path: str) -> tuple:
    st = os.stat(path)
    return (st.st_dev, st.st_ino, st.st_mtime_ns)


def _walk_store(base: str, git: bool, files: set, dirs: list) -> None:
    """Every regular file's (dev, ino) under `base` into `files`, every directory's signature into
    `dirs`. Iterative (no recursion limit), symlinks not followed, git's object database skipped."""
    stack = [base]
    while stack:
        here = stack.pop()
        try:
            dirs.append((here, _dir_sig(here)))
            with os.scandir(here) as it:
                entries = list(it)
        except (FileNotFoundError, NotADirectoryError):
            continue
        for e in entries:
            if e.is_dir(follow_symlinks=False):
                if git and fold_component(e.name) in _GIT_OBJECT_DIRS:
                    continue
                stack.append(e.path)
            elif e.is_file(follow_symlinks=False):
                st = os.lstat(e.path)          # DirEntry.stat carries no inode on Windows
                if st.st_ino:
                    files.add((st.st_dev, st.st_ino))


def _store_file_ids(root: str) -> set:
    """(dev, ino) of every file in the repo's private stores (git's object database aside)."""
    key = os.path.realpath(root)
    cached = _LINK_INDEX.get(key)
    if cached is not None:
        sig, ids = cached
        try:
            if all(_dir_sig(p) == s for p, s in sig):
                return ids
        except (FileNotFoundError, NotADirectoryError):
            pass
    files: set = set()
    dirs: list = []
    for d in store_dirs(key):
        if os.path.isdir(d):
            _walk_store(d, fold_component(os.path.basename(d)) != PRIVATE_DIRS[1], files, dirs)
    _LINK_INDEX[key] = (tuple(dirs), files)
    return files


def hardlinked_private(root: str, st) -> bool:
    """Is the regular file whose `os.stat` is `st` a HARD LINK to a file inside the repo's private
    stores? Identity, not spelling: a second name for `.git/config` is `.git/config`. Only a file
    with more than one link can be one, so the common case costs nothing."""
    import stat as _stat
    if not _stat.S_ISREG(st.st_mode) or st.st_nlink < 2 or not st.st_ino:
        return False
    return (st.st_dev, st.st_ino) in _store_file_ids(root)


def aliases_private(root: str, name: str) -> bool:
    """True when a leading run of `name`'s components IS, on disk, the repo's own `.git` or
    `.mokata` — or when the file `name` opens is a hard link to a file inside one of them — the
    IDENTITY half of the rule, compared as (device, inode), never by spelling.

    It catches what no spelling rule can: an 8.3 short name (`MOKATA~1`), a directory symlink or
    junction called anything at all, whatever folding THIS filesystem applies beyond the name
    rule's, and (0.0.27 SEC-HARDEN) a second hard-link name for a store file. A component that does
    not exist (or, on Windows, whose name is malformed) ends the walk (nothing past it can be the
    store); any OTHER failure to look
    (permission, a loop) raises, and the caller refuses — fail closed. An identity of inode 0 (a
    filesystem without stable ids) is not used; the name rule still is."""
    targets = _store_ids(root)
    if not targets:
        return False
    here, st = root, None
    for part in split_any(name):
        here = os.path.join(here, part)
        try:
            st = os.stat(here)
        except (FileNotFoundError, NotADirectoryError):
            return False
        except OSError as exc:
            if _is_invalid_name(exc):
                return False          # a name the filesystem cannot hold names no file at all
            raise
        if (st.st_dev, st.st_ino) in targets:
            return True
    return st is not None and hardlinked_private(root, st)


#: Windows' ERROR_INVALID_NAME: `os.stat('core.py:3:x')` is a malformed alternate-data-stream name, so
#: it answers EINVAL (not "not found"). It is the same fact as a missing component - no file has this
#: name, so none of it can be the store - and must not be read as "could not look" (which refuses).
_WIN_INVALID_NAME = 123


def _is_invalid_name(exc: OSError) -> bool:
    return getattr(exc, "winerror", None) == _WIN_INVALID_NAME


def _nearest_repo(path: str) -> "str | None":
    """The nearest ancestor of `path` holding a `.git` or a `.mokata`, or None."""
    cur = os.path.dirname(path)
    while True:
        if any(os.path.lexists(os.path.join(cur, d)) for d in PRIVATE_DIRS):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def private_path(path: str) -> bool:
    """The rule for an ABSOLUTE path with no repo handed in (the front door's question): private by
    NAME anywhere along the path as written or as resolved, or by IDENTITY against the nearest
    repo's stores. Undecidable is private — the caller then reads no byte."""
    try:
        real = os.path.realpath(path)
        if is_private_name(path) or is_private_name(real):
            return True
        root = _nearest_repo(real)
        if root is None:
            return False
        # `root` is an ancestor of `real` (found by walking up from it): the components below it.
        return aliases_private(root, real[len(root):])
    except (OSError, ValueError, TypeError):
        return True


# ------------------------------------------------------------------------------ the fast "no repo"

def surely_not_mokata(start: str) -> bool:
    """True only when `repo_identity.resolve_mokata_root(start).root` is PROVABLY None — answered on
    `os.path` alone, with no mokata or `dataclasses` import (HOOK-BUDGET, 0.0.25 Lane HB).

    It is the resolver's own walk for the ONLY case it can prove cheaply, and it says False
    ("ask the real resolver") for everything else:
      1. no ancestor of `start` holds `.mokata/manifest.json` — the resolver's first walk;
      2. the nearest `.git` above `start` is absent or a real DIRECTORY (not a file, not a symlink),
         so `canonical_repo_root` is that directory's realpath and the resolver's second walk, from
         there, finds nothing either. A `.git` FILE (a linked worktree or a submodule, whose main
         checkout the resolver consults) or a symlinked `.git` is never decided here.
    False is always safe — it only means the hook takes its normal path.
    `tests/test_hb_hook_budget.py` pins it to the resolver: True ⇒ the resolver says None, across
    plain dirs, repos, worktrees (manifest committed or not), submodules and symlinked paths."""
    try:
        cur = os.path.abspath(start)
        git_dir = None
        while True:
            if os.path.exists(os.path.join(cur, ".mokata", "manifest.json")):
                return False
            if git_dir is None:
                gp = os.path.join(cur, ".git")
                if os.path.lexists(gp):
                    if os.path.islink(gp) or not os.path.isdir(gp):
                        return False
                    git_dir = gp
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
        if git_dir is None:
            return True                  # no git: the resolver's canon is `start` itself
        cur = os.path.dirname(os.path.realpath(git_dir))
        while True:                       # the resolver's second walk, from canonical_repo_root
            if os.path.exists(os.path.join(cur, ".mokata", "manifest.json")):
                return False
            parent = os.path.dirname(cur)
            if parent == cur:
                return True
            cur = parent
    except (OSError, ValueError, TypeError):
        return False


# ------------------------------------------------------------------------------ stdin, bounded

def _read_stdin_bytes(timeout: float = _STDIN_READ_TIMEOUT_SECS) -> bytes:
    """All of stdin as bytes, never blocking longer than `timeout` (an OPEN pipe with no writer would
    block a bare read forever — the same guard as `hook_cli._read_stdin_bounded`). b"" on a TTY, a
    missing stream, an error or a timeout."""
    stream = sys.stdin
    if stream is None:
        return b""
    try:
        if stream.isatty():
            return b""
    except (OSError, ValueError):
        return b""
    import threading
    holder: dict = {}

    def _reader() -> None:
        try:
            holder["raw"] = stream.buffer.read()
        except Exception:            # noqa: BLE001 — a torn-down stream is "no input"
            holder["raw"] = b""

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return b""
    return holder.get("raw") or b""


# ------------------------------------------------------------------------------ JSON, without `re`

class _ScanContext:
    strict = True
    object_hook = None
    object_pairs_hook = None
    parse_float = float
    parse_int = int
    parse_constant = float
    memo: dict = {}


def loads(text: str):
    """`json.loads` for a hook payload, on the C scanner `json` itself uses (`_json.make_scanner`) —
    without importing `json`, whose decoder module imports `re` (and `enum`, `functools`…): ~10 ms
    of a 50 ms budget. Raises ValueError on anything that is not exactly one JSON value."""
    try:
        import _json
        scan = _json.make_scanner(_ScanContext())
    except (ImportError, AttributeError, TypeError):           # an interpreter without the C speedup
        import json
        return json.loads(text)
    start = len(text) - len(text.lstrip())
    try:
        value, end = scan(text, start)
    except StopIteration:
        raise ValueError("no JSON value") from None
    if text[end:].strip():
        raise ValueError("extra data after the JSON value")
    return value


def _payload(raw: bytes) -> dict:
    try:
        loaded = loads(raw.decode("utf-8")) if raw.strip() else {}
    except (ValueError, UnicodeDecodeError, RecursionError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _find_registered(value, depth: int = 0):
    """`run_intent._find_registered`, on the standard library (pinned against it by a test)."""
    if depth > 6:
        return None
    if isinstance(value, dict):
        rid = value.get("registered")
        if isinstance(rid, str) and rid.strip():
            return rid.strip()
        for v in value.values():
            found = _find_registered(v, depth + 1)
            if found:
                return found
    elif isinstance(value, list):
        for v in value:
            found = _find_registered(v, depth + 1)
            if found:
                return found
    elif isinstance(value, str) and "registered" in value:
        try:
            return _find_registered(loads(value), depth + 1)
        except (ValueError, TypeError, RecursionError):
            return None
    return None


def registered_run(payload: dict):
    """`run_intent.registered_run(tool_name, tool_response)` for one payload — None when the
    `session_save` this hook follows registered nothing (the common case: nothing to bind)."""
    name = payload.get("tool_name")
    if not isinstance(name, str) or name not in REGISTER_TOOLS:
        return None
    return _find_registered(payload.get("tool_response"))


# ------------------------------------------------------------------------------ the spool

def spool_dir() -> str:
    """`$MOKATA_HOOK_SPOOL`, else `~/.mokata/hook-spool` — per USER (one drainer serves every repo)."""
    env = os.environ.get(SPOOL_ENV)
    if env:
        return env
    return os.path.join(os.path.expanduser("~"), ".mokata", "hook-spool")


def pending(spool: str) -> list:
    """The spooled entries not yet drained, in arrival order."""
    try:
        return sorted(n for n in os.listdir(spool) if n.endswith(ENTRY_SUFFIX))
    except OSError:
        return []


#: Entry header magic. The header is NUL-framed rather than JSON: argv, a cwd, environment strings and
#: paths cannot contain a NUL on any platform (they are C strings to the OS), so NUL is unambiguous.
MAGIC = b"MKS2"


def _b(text: str) -> bytes:
    return text.encode("utf-8", "surrogateescape")


def observe_file(path: str) -> tuple:
    """`(realpath, (state, size, t, data))` for the file a tool just wrote, read NOW (hook time).
    state is "ok" (data = its bytes), "too-large" (no data) or "absent" — `ide.snapshots`' states."""
    real = os.path.realpath(path)
    t = time.time()
    try:
        if not os.path.isfile(real):
            return real, ("absent", 0, t, None)
        size = os.path.getsize(real)
        if size > MAX_SNAPSHOT_BYTES:
            return real, ("too-large", size, t, None)
        with open(real, "rb") as fh:
            data = fh.read(MAX_SNAPSHOT_BYTES + 1)
        if len(data) > MAX_SNAPSHOT_BYTES:
            return real, ("too-large", len(data), t, None)
        return real, ("ok", len(data), t, data)
    except OSError:
        return real, ("absent", 0, t, None)


def _publish(spool: str, tmp: str, ns: int) -> str:
    """Give `tmp` the first free arrival name at or after `ns` — NEVER overwriting one. A clock that
    ties (Windows' coarse clock, two hooks in one tick) moves the later writer one nanosecond on, so
    arrival order is the name order; the drainer runs names in sorted order."""
    for k in range(10000):
        final = os.path.join(spool, "%020d%s" % (ns + k, ENTRY_SUFFIX))
        try:
            os.link(tmp, final)              # atomic, and fails if `final` exists (POSIX + NTFS)
        except FileExistsError:
            continue
        except (OSError, AttributeError, NotImplementedError):
            try:
                if os.path.exists(final):
                    continue
                os.rename(tmp, final)        # Windows: refuses an existing target
                return final
            except FileExistsError:
                continue
        try:
            os.remove(tmp)
        except OSError:
            pass
        return final
    raise OSError("no free spool name")


def spool_entry(spool: str, argv: list, raw: bytes, observed: dict = None) -> str:
    """Write ONE entry, owner-only (0600 in a 0700 directory; on Windows the default spool sits under
    the user profile, whose inherited ACL is owner-only, and a hook may not pay for `ctypes`), publish it under its arrival
    name; return its path. Raises OSError (the caller then runs the hook inline).

    Format: `MKS2 \0 cwd \0 argc \0 argv… \0 envc \0 K=V… \0 obsc \0 (path \0 state \0 size
    \0 t \0 len \0)…` then the observed files' bytes back to back, then the raw hook payload."""
    ns = time.time_ns()
    os.makedirs(spool, mode=0o700, exist_ok=True)
    if os.name != "nt" and os.stat(spool).st_mode & 0o077:
        os.chmod(spool, 0o700)               # owner-only, even if something else created it
    env = [f"{k}={v}" for k, v in os.environ.items()
           if k.startswith(ENV_PREFIXES) and k != SPOOL_ENV]
    obs = list((observed or {}).items())
    fields = [MAGIC, _b(os.getcwd()), _b(str(len(argv)))] + [_b(a) for a in argv] \
        + [_b(str(len(env)))] + [_b(e) for e in env] + [_b(str(len(obs)))]
    blobs = []
    for path, (state, size, t, data) in obs:
        fields += [_b(path), _b(state), _b(str(int(size))), _b(repr(float(t))),
                   _b(str(-1 if data is None else len(data)))]
        if data is not None:
            blobs.append(data)
    tmp = os.path.join(spool, "%020d-%d.tmp" % (ns, os.getpid()))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(b"\0".join(fields) + b"\0")
            for blob in blobs:
                fh.write(blob)
            fh.write(raw)
        return _publish(spool, tmp, ns)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def read_entry(path: str) -> tuple:
    """`(argv, cwd, env, payload_text, observed)` of one spooled entry. Raises OSError/ValueError."""
    with open(path, "rb") as fh:
        data = fh.read()
    pos = 0

    def field() -> str:
        nonlocal pos
        end = data.index(b"\0", pos)          # ValueError on a truncated header
        out = data[pos:end].decode("utf-8", "surrogateescape")
        pos = end + 1
        return out

    if field() != MAGIC.decode():
        raise ValueError("not a spool entry")
    cwd = field()
    argv = [field() for _ in range(int(field()))]
    env = dict(e.partition("=")[::2] for e in (field() for _ in range(int(field()))))
    heads = [(field(), field(), int(field()), float(field()), int(field()))
             for _ in range(int(field()))]
    observed = {}
    for fpath, state, size, t, n in heads:
        blob = None
        if n >= 0:
            blob = data[pos:pos + n]
            if len(blob) != n:
                raise ValueError("truncated spool entry")
            pos += n
        observed[fpath] = (state, size, t, blob)
    if not argv:
        raise ValueError("spool entry has no argv")
    return argv, cwd, env, data[pos:].decode("utf-8", errors="replace"), observed


# ------------------------------------------------------------------------------ the drain lock

def _lock_path(spool: str) -> str:
    return os.path.join(spool, LOCK_NAME)


def _lock_fresh(spool: str) -> bool:
    try:
        return (time.time() - os.stat(_lock_path(spool)).st_mtime) < LOCK_STALE_SECS
    except OSError:
        return False


def _acquire(spool: str) -> bool:
    lock = _lock_path(spool)
    try:
        os.mkdir(lock)
        return True
    except FileExistsError:
        if _lock_fresh(spool):
            return False
        try:                              # a dead drainer's lock: take it over
            os.utime(lock)
            return True
        except OSError:
            return False
    except OSError:
        return False


def _release(spool: str) -> None:
    try:
        os.rmdir(_lock_path(spool))
    except OSError:
        pass


def _touch(spool: str) -> None:
    try:
        os.utime(_lock_path(spool))
    except OSError:
        pass


# ------------------------------------------------------------------------------ running one entry

def _run_entry(argv: list, cwd, env: dict, payload: str, observed: dict = None) -> None:
    """Run one hook in THIS process with its own cwd and CLAUDE_*/MOKATA_* environment, restoring
    both after. Imports the engine — only the drainer (or an inline fallback) gets here."""
    try:
        saved_cwd = os.getcwd()
    except OSError:
        saved_cwd = None
    saved_env = {k: v for k, v in os.environ.items()
                 if k.startswith(ENV_PREFIXES) and k != SPOOL_ENV}
    try:
        for k in saved_env:
            os.environ.pop(k, None)
        for k, v in env.items():
            if isinstance(k, str) and isinstance(v, str) and k.startswith(ENV_PREFIXES):
                os.environ[k] = v
        if isinstance(cwd, str) and os.path.isdir(cwd):
            os.chdir(cwd)
        from mokata.hook_cli import run_with_stdin
        run_with_stdin(argv, payload, observed)
    except Exception:  # noqa: BLE001 — an observe hook never fails anything
        pass
    finally:
        for k in [k for k in os.environ if k.startswith(ENV_PREFIXES) and k != SPOOL_ENV]:
            os.environ.pop(k, None)
        os.environ.update(saved_env)
        if saved_cwd is not None:
            try:
                os.chdir(saved_cwd)
            except OSError:
                pass


def drain(spool: str) -> int:
    """Run every spooled entry, in arrival order, under the drain lock. Another live drainer ⇒ return
    at once (it re-checks the spool after its release). An entry is renamed to `.work` before it runs
    and deleted after: a `.work` left by a drainer that died is dropped, never retried, so one bad
    payload can never wedge the spool. ALWAYS returns 0."""
    while True:
        if not _acquire(spool):
            return 0
        try:
            now = time.time()
            for n in os.listdir(spool):
                p = os.path.join(spool, n)
                try:
                    if n.endswith(WORK_SUFFIX) or (
                            n.endswith(".tmp") and now - os.stat(p).st_mtime > LOCK_STALE_SECS):
                        os.remove(p)       # a dead drainer's entry / a dead emitter's half-write
                except OSError:
                    pass
            while True:
                names = pending(spool)
                if not names:
                    break
                for n in names:
                    path = os.path.join(spool, n)
                    work = path[:-len(ENTRY_SUFFIX)] + WORK_SUFFIX
                    try:
                        os.replace(path, work)
                    except OSError:
                        continue
                    _touch(spool)
                    try:
                        _run_entry(*read_entry(work))
                    except (OSError, ValueError):
                        pass
                    try:
                        os.remove(work)
                    except OSError:
                        pass
        except OSError:
            pass
        finally:
            _release(spool)
        if not pending(spool):
            return 0


# ------------------------------------------------------------------------------ starting a drainer

def drainer_argv(spool: str) -> list:
    """The drainer command: this package's `__main__.py` BY PATH (so `sys.path[0]` is this package's
    own directory, never the user's repo — a planted module there cannot be picked up)."""
    main = os.path.join(os.path.dirname(os.path.abspath(__file__)), "__main__.py")
    return [sys.executable, main, DRAIN_FLAG, spool]


def _drainer_env() -> dict:
    """The current environment with the directory holding `mokata_emit` (and `mokata` beside it)
    first on PYTHONPATH, so the drainer imports the SAME engine whichever way this hook was found."""
    env = dict(os.environ)
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    old = env.get("PYTHONPATH")
    env["PYTHONPATH"] = here + (os.pathsep + old if old else "")
    return env


def _spawn_drainer(spool: str) -> bool:
    """Start a detached drainer (see `spawn_detached`). False ⇒ it could not be started."""
    if not sys.executable:
        return False
    return spawn_detached(drainer_argv(spool), cwd=spool)


def spawn_mokata_emit(args: list, cwd: str) -> bool:
    """Run THIS package's `__main__.py` BY PATH with `args`, detached (0.0.26 O12: the recall-graph
    warmer reuses the drainer's vetted route — same interpreter, same engine, no `sys.path` entry
    from the user's repo)."""
    if not sys.executable:
        return False
    main = os.path.join(os.path.dirname(os.path.abspath(__file__)), "__main__.py")
    return spawn_detached([sys.executable, main, *args], cwd=cwd)


def spawn_detached(argv: list, cwd: str) -> bool:
    """Start `argv` detached: no inherited stdio, and it must outlive this hook process, which
    Claude Code reaps as soon as it exits. False ⇒ it could not be started.

    Windows: `subprocess.Popen` with `quiet_spawn.detached()` — CREATE_NEW_PROCESS_GROUP (out of the
    hook's console group, so it is not taken down with it) | CREATE_NO_WINDOW (a hidden console of its
    own, NEVER DETACHED_PROCESS: that is the 0.0.25 B-01 cmd-flash bug). The one place the hot path
    imports from `mokata` — `mokata/__init__` and `quiet_spawn` are both import-light — because the
    spawn flags must have one source.
    POSIX: `os.posix_spawn` in a new session (no `subprocess` import), the same detachment
    `quiet_spawn.detached()` gives as `start_new_session=True`."""
    env = _drainer_env()
    if os.name == "nt":
        import subprocess
        from mokata.quiet_spawn import detached
        try:
            subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
                             **detached())
            return True
        except OSError:
            return False
    try:
        devnull = os.open(os.devnull, os.O_RDWR)
    except OSError:
        return False
    try:
        actions = [(os.POSIX_SPAWN_DUP2, devnull, 0), (os.POSIX_SPAWN_DUP2, devnull, 1),
                   (os.POSIX_SPAWN_DUP2, devnull, 2)]
        try:
            os.posix_spawn(argv[0], argv, env, file_actions=actions, setsid=True)
        except NotImplementedError:
            os.posix_spawn(argv[0], argv, env, file_actions=actions, setpgroup=0)
        return True
    except (OSError, AttributeError, TypeError):
        return False
    finally:
        os.close(devnull)


# ------------------------------------------------------------------------------ the front door

def _quiet_here(sub: str, rest: list, payload: dict) -> bool:
    """The plugin route's "nothing to do outside a mokata repo" answer, before anything is spooled."""
    if sub not in QUIET_OUTSIDE_MOKATA or VIA_PLUGIN_FLAG not in rest:
        return False
    if any(a != VIA_PLUGIN_FLAG for a in rest):
        return False
    cwd = payload.get("cwd") or os.getcwd()
    return isinstance(cwd, str) and surely_not_mokata(cwd)


def _observations(payload: dict) -> dict:
    """`dirty-track`'s target file, read at hook time: `{realpath: (state, size, t, data)}`."""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return {}
    target = next((v for v in (tool_input.get(k) for k in PATH_KEYS)
                   if isinstance(v, str) and v), None)
    if target is None:
        return {}
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else None
    ab = target if os.path.isabs(target) else os.path.join(cwd or os.getcwd(), target)
    if private_path(ab):
        # 0.0.27 SEC-HARDEN — git's store or mokata's private store (any spelling, any alias, a
        # hard link into either): the path is recorded and NO byte is read, so none is spooled.
        return {os.path.realpath(ab): (PRIVATE_STATE, 0, time.time(), None)}
    real, seen = observe_file(ab)
    return {real: seen}


def bash_needs_tracking(payload: dict) -> bool:
    """P31 — a Bash call needs the fallback attribution only for a session that is NOT in a linked
    worktree (a worktree session is attributed exactly by `git diff`) and only inside a git checkout:
    the nearest `.git` above `cwd` must be a DIRECTORY. Pure `os.path`; a worktree session's Bash
    calls cost the emitter this walk and nothing else."""
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else os.getcwd()
    cur = os.path.abspath(cwd)
    while True:
        gp = os.path.join(cur, ".git")
        if os.path.isdir(gp):
            return True
        if os.path.exists(gp):
            return False                     # a `.git` FILE: a linked worktree (or a submodule)
        parent = os.path.dirname(cur)
        if parent == cur:
            return False
        cur = parent


def emit(argv: list) -> int:
    """`_emit_spooled`, with the hook-time stamp it may set confined to this call (0.0.26 O12b).

    `dirty-track` stamps `MOKATA_HOOK_T_NS` into the environment so the spooled entry carries WHEN
    the tool call ended. Setting it on `os.environ` and leaving it there was harmless in a hook
    process (it exits) but LEAKED in any long-lived caller: every later in-process hook then read
    that one frozen instant as "now" — P33's heartbeat stopped moving, an order-dependent flake.
    The previous value (or its absence) is restored on the way out."""
    had = HOOK_TIME_ENV in os.environ
    prior = os.environ.get(HOOK_TIME_ENV)
    try:
        return _emit_spooled(argv)
    finally:
        if had:
            os.environ[HOOK_TIME_ENV] = prior
        else:
            os.environ.pop(HOOK_TIME_ENV, None)


def _emit_spooled(argv: list) -> int:
    """Spool one deferred observe hook and make sure a drainer will run it. ALWAYS 0 — every deferred
    hook always exits 0. Extra arguments (the `--path`/`--cwd` manual escape), an oversize entry or
    an unwritable spool run it inline; no drainer ⇒ the spool is drained inline. The work happens."""
    raw = _read_stdin_bytes()
    rest = argv[1:]
    payload = _payload(raw)
    if _quiet_here(argv[0], rest, payload):
        return 0
    observed = None
    if argv[0] == "dirty-track":
        if any(a != VIA_PLUGIN_FLAG for a in rest):
            _run_entry(argv, os.getcwd(), {}, raw.decode("utf-8", errors="replace"))
            return 0
        if payload.get("tool_name") == "Bash" and not bash_needs_tracking(payload):
            return 0
        observed = _observations(payload)
        # P31 — hook-time capture of WHEN the tool call ended: the Bash attribution window's end.
        os.environ[HOOK_TIME_ENV] = str(time.time_ns())
    size = len(raw) + sum(len(d) for (_s, _z, _t, d) in (observed or {}).values() if d)
    spool = spool_dir()
    try:
        if size > MAX_ENTRY_BYTES:
            raise OSError("entry too large to spool")
        spool_entry(spool, argv, raw, observed)
    except OSError:
        _run_entry(argv, os.getcwd(), {}, raw.decode("utf-8", errors="replace"), observed)
        return 0
    if _lock_fresh(spool):
        return 0            # a live drainer re-lists after it releases: it will run this entry
    if not _spawn_drainer(spool):
        drain(spool)
    return 0


def run_bind(argv: list) -> int:
    """`run-bind`, SYNCHRONOUSLY: nothing registered (the common case) is answered here on the
    standard library; a registration is bound inline by `run_intent.bind_from_hook` — the handler's
    own logic — before the hook returns, so the next tool call's gate reads the binding. ALWAYS 0.

    It skips the dispatcher (`hook_cli`, ~2× the cost) and with it the plugin route's stand-down
    (`_settings_route_owns`, which loads `hook_wiring`): a bind is IDEMPOTENT, so when both the
    plugin and `mokata setup` wire run-bind, the second identical bind changes nothing."""
    payload = _payload(_read_stdin_bytes())
    if registered_run(payload) is None:
        return 0
    cwd = payload.get("cwd") or os.getcwd()
    if isinstance(cwd, str) and surely_not_mokata(cwd):
        return 0                     # the resolver would find no root: `bind_from_hook` would no-op
    from mokata.run_intent import bind_from_hook
    bind_from_hook(payload)
    return 0


def main(argv: "list | None" = None) -> int:
    """`mokata-hook <sub> [args…]` — deferred observe hooks are emitted, `run-bind` is answered
    inline, everything else is the engine's (`mokata.hook_cli.main`, which also owns the
    missing/unknown-subcommand exit 1)."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == DRAIN_FLAG:
        spool = argv[1] if len(argv) > 1 else spool_dir()
        try:
            os.chdir(spool)      # never sit in (or depend on) a user repo that may be deleted
        except OSError:
            pass
        return drain(spool)
    if argv and argv[0] in DEFERRED:
        try:
            return emit(argv)
        except Exception:  # noqa: BLE001 — an observe hook never fails the call it follows
            return 0
    if argv and argv[0] in SYNC_OBSERVE:
        try:
            return run_bind(argv)
        except Exception:  # noqa: BLE001 — a PostToolUse hook never fails the call it follows
            return 0
    import mokata.hook_cli as engine
    # 0.0.26 O12b — reached through the front door (Claude Code's hook process), the SessionStart
    # briefing serves its slow lines from the background-refreshed cache. An in-process caller
    # (`hook_cli.main`, tests, the IDE) keeps the synchronous derivation — so the flag is scoped to
    # THIS call and restored (a flag left set would leak into every later in-process call).
    prior, engine.FRONT_DOOR = engine.FRONT_DOOR, True
    try:
        return engine.main(argv)
    finally:
        engine.FRONT_DOOR = prior


__all__ = ["DEFERRED", "PRIVATE_DIRS", "QUIET_OUTSIDE_MOKATA", "SYNC_OBSERVE", "aliases_private",
           "drain", "emit", "fold_component", "hardlinked_private", "is_private_name", "loads",
           "main", "pending", "private_path", "registered_run", "split_any", "spool_dir",
           "spool_entry", "store_dirs", "surely_not_mokata"]
