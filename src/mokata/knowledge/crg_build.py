"""0.0.26 O13 — `CRG-AUTO-BUILD`: code-review-graph's index for THIS repo, built once, never twice.

Rulings (Jas, 2026-10-21):

  (R-a) code-review-graph installed, its index never built here -> build it on FIRST USE, in the
        background, and answer that query (and every query until the build lands) from the floor;
  (R-b) `mokata setup claude --mode full` builds the index as part of setup (~6 min, ~1 GB on a
        1M-line repo) with visible progress;
  (R-c) approving the setup run is the consent for setup's writes, the index build among them.

What "built" means is read from code-review-graph's OWN record, never inferred from a file being
there. Measured on 2.3.9: its first `serve`/`query` against an unbuilt repo creates
`.code-review-graph/` (and the MCP server an empty `graph.db`), so the directory or the file alone
proves nothing. A build writes `metadata.last_updated`, and records `metadata.build_state`
(`in-progress` -> `postprocess-pending` -> `complete`) so a killed build is visible as one. Only
`last_updated` present and the state not one of the two incomplete ones is BUILT. The DB is opened
READ-ONLY by URI (`mode=ro`), so the probe can never create the file it is asking about.

Run-state (all under the gitignored `.mokata/temp_local/crg-build/`):

  * `lock`  — `{"pid", "started"}`, created with O_EXCL: ONE build per repo across every process
              (two CLI queries, the IDE, a parallel session). A holder whose pid is gone is a dead
              build: its partial index is discarded and the lock is taken over. There is NO time
              limit on a live one (Jas, 2026-10-21: a 1M+ line repo builds for many minutes —
              "don't put non-reasonable timeouts"); progress and Ctrl+C are the controls.
  * `last.json` — the last outcome: built / failed (+ the error, attempts, and when an automatic
              retry is next allowed: exponential back-off, so a broken tool is not re-run on every
              query) / interrupted (Ctrl+C in setup: no back-off, the next query builds it).
  * `build.log` — the background builder's output, for `mokata doctor` and a human.

A partial index is never left looking healthy: a build that fails, times out or is interrupted
removes `graph.db` (and its WAL/SHM) unless code-review-graph's own record says it completed. The
`.gitignore` code-review-graph writes into the directory (`*`) is left in place.

Stdlib only. Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..quiet_spawn import detached, hidden
from .cancel import cancelled, report

INDEX_DIRNAME = ".code-review-graph"
DB_FILENAME = "graph.db"
RUN_DIRNAME = "crg-build"

#: Index states read off code-review-graph's own record.
INDEX_BUILT = "built"
INDEX_INCOMPLETE = "incomplete"      # a build is running, or one died part-way
INDEX_ABSENT = "absent"              # never built (no DB, or the empty one `serve` creates)

#: What mokata says about the repo's code-review-graph index.
BUILT = "built"
BUILDING = "building"
UNBUILT = "unbuilt"
FAILED = "failed"

#: How often setup says "still building" when code-review-graph prints nothing.
HEARTBEAT_S = 15.0
#: Back-off after a failed AUTOMATIC build: 1 min, 2, 4, ... capped at 1 h. A human's explicit
#: `mokata graph build` (or the IDE's rebuild button) ignores it.
BACKOFF_BASE_S = 60.0
BACKOFF_MAX_S = 3600.0
_TAIL_LINES = 12

_PROGRESS = re.compile(r"Progress:\s*(\d+)\s*/\s*(\d+)\s*files")


# ----------------------------------------------------------------------------- paths
def index_dir(root: str) -> str:
    return os.path.join(root, INDEX_DIRNAME)


def db_path(root: str) -> str:
    return os.path.join(root, INDEX_DIRNAME, DB_FILENAME)


def _run_dir(root: str) -> str:
    from .. import MOKATA_DIR, TEMP_LOCAL_DIRNAME
    return os.path.join(root, MOKATA_DIR, TEMP_LOCAL_DIRNAME, RUN_DIRNAME)


def _lock_path(root: str) -> str:
    return os.path.join(_run_dir(root), "lock")


def _last_path(root: str) -> str:
    return os.path.join(_run_dir(root), "last.json")


def log_path(root: str) -> str:
    return os.path.join(_run_dir(root), "build.log")


def _can_record(root: str) -> bool:
    """Run-state lives in the repo's `.mokata/`. A directory mokata was never set up in gets no
    `.mokata/` created behind its back — and therefore no automatic build (a human can still run
    `code-review-graph build` or `mokata graph build`)."""
    from .. import MOKATA_DIR
    return os.path.isdir(os.path.join(root, MOKATA_DIR))


# ----------------------------------------------------------------------------- the index probe
def probe_index(root: str) -> str:
    """BUILT / INCOMPLETE / ABSENT, from code-review-graph's own metadata (module docstring).
    Read-only, never creates anything, never raises.

    0.0.26 P37 (R39): an index whose marker says `in-progress` ONLY because an incremental update
    mokata started over a COMPLETE index is running, or was killed (the IDE closed, a cancel, a
    crash), is still BUILT — `update_state` says which, and the answers say "updating…". The
    attempt is recorded per update (`update.json`: the `last_updated` it started from), so a full
    build that died part-way is never mistaken for one: without a matching record, INCOMPLETE."""
    state = probe_db(db_path(root))
    if state == INDEX_INCOMPLETE and update_state(root):
        return INDEX_BUILT
    return state


# ----------------------------------------------------------------------------- update attempts
#: The incremental update in flight (or killed), over a COMPLETE index: `{"from", "pid",
#: "started"}` — `from` is the index's `metadata.last_updated` when the update began.
UPDATE_FILENAME = "update.json"
UPDATING = "updating"
UPDATE_INTERRUPTED = "interrupted"


def _update_path(root: str) -> str:
    return os.path.join(_run_dir(root), UPDATE_FILENAME)


def _last_updated(path: str) -> Optional[str]:
    if not os.path.isfile(path):
        return None
    from ..memory._sqlite import connect_sqlite_readonly
    try:
        conn = connect_sqlite_readonly(path, timeout_s=1.0)
    except (sqlite3.Error, ValueError, OSError):
        return None
    try:
        row = conn.execute("SELECT value FROM metadata WHERE key = 'last_updated'").fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return str(row[0]) if row and row[0] else None


def _meta_value(path: str, key: str) -> Optional[str]:
    from ..memory._sqlite import connect_sqlite_readonly
    try:
        conn = connect_sqlite_readonly(path, timeout_s=1.0)
    except (sqlite3.Error, ValueError, OSError):
        return None
    try:
        row = conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return str(row[0]) if row and row[0] else None


# ----------------------------------------------------------------------------- repairs beside
#: Free space kept on top of what a build beside the index needs (never fill the disk).
DISK_MARGIN_BYTES = 512 * 1024 * 1024
REPAIR_INCREMENTAL = "incremental"
REPAIR_FULL = "full"


def _index_bytes(root: str) -> int:
    total = 0
    for suffix in ("", "-wal"):
        try:
            total += os.path.getsize(db_path(root) + suffix)
        except OSError:
            pass
    return total


def disk_shortfall(root: str, need: Optional[int] = None) -> str:
    """"" when the disk holding the index has room for a second copy of it (plus a margin), else
    why not — a build beside the index is then REFUSED (the graph in use is kept), never run
    until the disk is full."""
    import shutil as _sh
    need = (_index_bytes(root) if need is None else need) + DISK_MARGIN_BYTES
    try:
        free = _sh.disk_usage(index_dir(root)).free
    except OSError:
        return ""
    if free >= need:
        return ""
    gb = 1024 ** 3
    return (f"not enough free disk to rebuild the code graph beside the one in use: needs "
            f"{need / gb:.1f} GB, {free / gb:.1f} GB free — the graph in use is kept; free some "
            f"space and it is repaired on the next update")


def _seed_beside(root: str) -> str:
    """P37 — the incremental repair of an interrupted UPDATE: copy the index (with its WAL) to the
    side directory, check the copy's integrity, and put back the COMPLETE marker the update began
    from — in the COPY only — so code-review-graph's `update` there redoes just the changed files
    (its anchor did not advance) instead of a full rebuild. Returns "" or why it cannot (the
    caller then rebuilds in full beside)."""
    import shutil as _sh
    d = next_dir(root)
    _remove_db(os.path.join(d, DB_FILENAME))
    os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, DB_FILENAME)
    for suffix in ("", "-wal"):
        if os.path.exists(db_path(root) + suffix):
            _sh.copyfile(db_path(root) + suffix, dst + suffix)
    from ..memory._sqlite import connect_sqlite
    try:
        conn = connect_sqlite(dst, out=lambda _s: None)   # OUR copy: the one connect factory
    except sqlite3.Error as exc:
        return f"the copy could not be opened ({exc})"
    try:
        report(note="checking the copied graph's integrity")
        verdict = conn.execute("PRAGMA quick_check").fetchone()
        if not verdict or verdict[0] != "ok":
            return f"the graph failed its integrity check ({verdict[0] if verdict else '?'})"
        conn.execute("UPDATE metadata SET value = 'complete' WHERE key = 'build_state'")
        conn.commit()
    except sqlite3.Error as exc:
        return f"the copy could not be prepared ({exc})"
    finally:
        conn.close()
    return ""


def update_state(root: str) -> str:
    """"" (no update of a complete index is unfinished), UPDATING (its process is alive) or
    UPDATE_INTERRUPTED (it died, or was cancelled, before code-review-graph marked the index
    complete again). Read-only, never raises."""
    rec = _read_json(_update_path(root))
    if not rec.get("from"):
        return ""
    if probe_db(db_path(root)) != INDEX_INCOMPLETE or _last_updated(db_path(root)) != rec["from"]:
        return ""
    return UPDATING if _pid_alive(rec.get("pid")) and not rec.get("ended") else UPDATE_INTERRUPTED


def update_note(root: str) -> str:
    """What an answer says while the index is not current: "" when nothing is pending."""
    st = update_state(root)
    if st == UPDATING:
        return ("code graph updating… — answered from the last complete graph")
    if st == UPDATE_INTERRUPTED:
        return ("a code-graph update did not finish — answered from the last complete graph; the "
                "next update redoes it beside this one")
    return ""


def _begin_update(root: str) -> bool:
    """Record an incremental update about to run over a COMPLETE index (True), before
    code-review-graph marks it `in-progress`."""
    if not _can_record(root) or probe_db(db_path(root)) != INDEX_BUILT:
        return False
    since = _last_updated(db_path(root))
    if not since:
        return False
    try:
        _write_json(_update_path(root), {"from": since, "pid": os.getpid(),
                                         "started": time.time(),
                                         "anchor": _meta_value(db_path(root), "git_head_sha")})
    except OSError:
        return False
    return True


def _end_update(root: str, ok: bool) -> None:
    """A finished update: the record goes once the index is COMPLETE again; a failed or cancelled
    one stays recorded as ended (the index is still usable; the next update repairs it)."""
    path = _update_path(root)
    if ok and probe_db(db_path(root)) == INDEX_BUILT:
        try:
            os.remove(path)
        except OSError:
            pass
        return
    rec = _read_json(path)
    if rec.get("from"):
        rec["ended"] = time.time()
        try:
            _write_json(path, rec)
        except OSError:
            pass


def probe_db(path: str) -> str:
    """`probe_index` for a graph.db at `path` (0.0.26 P32: the swap build's temp copy too)."""
    if not os.path.isfile(path):
        return INDEX_ABSENT
    from ..memory._sqlite import connect_sqlite_readonly
    try:
        conn = connect_sqlite_readonly(path, timeout_s=1.0)
    except (sqlite3.Error, ValueError, OSError):
        return INDEX_INCOMPLETE
    try:
        rows = dict(conn.execute(
            "SELECT key, value FROM metadata WHERE key IN ('last_updated', 'build_state')"
        ).fetchall())
    except sqlite3.OperationalError as exc:
        # "no such table": the empty DB a `serve` against an unbuilt repo leaves. Anything else
        # (locked mid-write) is a build in flight.
        return INDEX_ABSENT if "no such table" in str(exc) else INDEX_INCOMPLETE
    except sqlite3.Error:
        return INDEX_INCOMPLETE
    finally:
        conn.close()
    if not rows.get("last_updated"):
        return INDEX_ABSENT if not rows.get("build_state") else INDEX_INCOMPLETE
    if rows.get("build_state") in ("in-progress", "postprocess-pending"):
        return INDEX_INCOMPLETE
    return INDEX_BUILT


def index_built(root: str) -> bool:
    return probe_index(root) == INDEX_BUILT


#: A WAL / rollback journal written this recently means another process is writing the index.
WRITER_ACTIVE_S = 120.0


def had_complete_build(path: str) -> bool:
    """True when code-review-graph's record says a build of this DB COMPLETED at some point
    (`metadata.last_updated` is written only once every file is stored), whatever its marker says
    now. An `update` marks the graph `in-progress` while it runs, and code-review-graph repairs
    an interrupted one itself on its next run (`build_was_interrupted` → full rebuild) — so such
    an index is the user's working graph, never a "partial" to delete (P35, the K incident)."""
    if not os.path.isfile(path):
        return False
    from ..memory._sqlite import connect_sqlite_readonly
    try:
        conn = connect_sqlite_readonly(path, timeout_s=1.0)
    except (sqlite3.Error, ValueError, OSError):
        return True              # unreadable right now (locked mid-write): never assume partial
    try:
        row = conn.execute("SELECT value FROM metadata WHERE key = 'last_updated'").fetchone()
    except sqlite3.OperationalError as exc:
        return "no such table" not in str(exc)
    except sqlite3.Error:
        return True
    finally:
        conn.close()
    return bool(row and row[0])


def writer_active(path: str, now: Optional[float] = None) -> bool:
    """True when another process is (or was just) writing this DB: its WAL or rollback journal
    changed within WRITER_ACTIVE_S."""
    now = time.time() if now is None else now
    for suffix in ("-wal", "-journal"):
        try:
            st = os.stat(path + suffix)
        except OSError:
            continue
        if st.st_size > 0 and now - st.st_mtime < WRITER_ACTIVE_S:
            return True
    return False


def discard_partial(root: str) -> bool:
    """Remove a partial index so it can never be read as a healthy one. True when something was
    removed. KEPT, always (0.0.26 P35 — a 2.8 GB complete kubernetes index was deleted because a
    concurrent `code-review-graph update` had marked it `in-progress`):

      * an index code-review-graph records as COMPLETE;
      * an index that EVER completed a build (`had_complete_build`) — an update in flight, or one
        that died, is the tool's to repair, not ours to delete;
      * any index another process is writing right now (`writer_active`).

    What is removed is only a FIRST build that never finished and that nobody is writing."""
    path = db_path(root)
    if probe_index(root) == INDEX_BUILT or had_complete_build(path) or writer_active(path):
        return False
    return _remove_db(path)


def _remove_db(path: str) -> bool:
    removed = False
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            os.remove(path + suffix)
            removed = True
        except FileNotFoundError:
            pass
        except OSError:
            pass
    return removed


#: 0.0.26 P32 (R39) — where a REBUILD of a built index is written before it replaces the one in use.
NEXT_DIRNAME = "next-build"


def next_dir(root: str) -> str:
    return os.path.join(index_dir(root), NEXT_DIRNAME)


def swap_in(root: str, before_swap: Optional[Callable[[], None]] = None,
            since: Optional[float] = None) -> str:
    """Replace the index in use with the finished rebuild in `next_dir` — one `os.replace` of
    graph.db, so a reader sees the old graph or the new one, never half of either. Returns "" on
    success, else why the old index was kept (the rebuild is then discarded).

    `before_swap` closes this process's code-review-graph sessions first, so their connection
    checkpoints and drops the old index's WAL (a WAL left beside the NEW file would be replayed
    into it). A file another process holds open cannot be replaced on Windows: that is reported
    and the old index stays COMPLETE."""
    new = os.path.join(next_dir(root), DB_FILENAME)
    if probe_db(new) != INDEX_BUILT:
        _remove_db(new)
        return "the rebuilt index is not complete"
    if before_swap is not None:
        before_swap()
    cur = db_path(root)
    # A non-empty WAL that another process is WRITING (changed within WRITER_ACTIVE_S) would be
    # replayed into the new file: refused. P37: a WAL nobody has written for that long is a DEAD
    # writer's (an update killed with the IDE) — it belongs to the old file and goes with it, or
    # the one repair for that state could never be swapped in.
    # `since` (the build's start): a WAL not written since then had no writer for the whole build
    # — the build holds the repo's lock, and an incremental repair COPIED that WAL — so it goes.
    try:
        wal = cur + "-wal"
        live = (os.path.getmtime(wal) >= since) if since is not None and os.path.exists(wal) \
            else writer_active(cur)
        if os.path.exists(wal) and os.path.getsize(wal) > 0 and live:
            return ("another process still has the index open with unsaved changes "
                    "(its WAL is not empty) — the old index is kept; close it and rebuild")
    except OSError:
        pass
    try:
        os.replace(new, cur)
    except OSError as exc:
        _remove_db(new)
        return f"could not replace the index in use ({exc}) — the old index is kept"
    for suffix in ("-wal", "-shm"):
        try:
            os.remove(cur + suffix)            # the OLD file's side files: never the new one's
        except OSError:
            pass
    _drop_next(root)
    return ""


def _drop_next(root: str) -> None:
    """Remove the side-build directory (its graph.db + the `.gitignore` the tool writes)."""
    d = next_dir(root)
    _remove_db(os.path.join(d, DB_FILENAME))
    try:
        for name in os.listdir(d):
            os.remove(os.path.join(d, name))
        os.rmdir(d)
    except OSError:
        pass


# ----------------------------------------------------------------------------- run-state
def _read_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, sort_keys=True)
    os.replace(tmp, path)


def _pid_alive(pid: Any) -> bool:
    from ..session_registry import pid_alive
    return pid_alive(pid)


def hhmm(ts: Optional[float]) -> str:
    return time.strftime("%H:%M", time.localtime(ts)) if ts else "?"


@dataclass
class BuildStatus:
    """What mokata knows about the repo's code-review-graph index right now."""

    state: str
    started: Optional[float] = None       # BUILDING: when the running build started
    error: str = ""                       # FAILED: why
    finished: Optional[float] = None      # FAILED/BUILT: when the last build ended
    retry_after: Optional[float] = None   # FAILED: no automatic retry before this
    attempts: int = 0

    def line(self, tool: str = "code-review-graph") -> str:
        """One sentence a reader can act on, shared by the query note, status, doctor and IDE."""
        if self.state == BUILDING:
            return f"{tool} is building its index (started {hhmm(self.started)})"
        if self.state == FAILED:
            retry = (f"; automatic retry after {hhmm(self.retry_after)}"
                     if self.retry_after and self.retry_after > time.time() else "")
            return (f"{tool}'s index build failed at {hhmm(self.finished)} ({self.error})"
                    f"{retry} — rebuild: `mokata graph build`")
        if self.state == UNBUILT:
            return f"{tool} has no index for this repo yet — `mokata graph build` builds it"
        return f"{tool}'s index is built"


def _lock_holder(root: str, now: float) -> Optional[Dict[str, Any]]:
    """The live build holding the lock, or None. A DEAD holder (its process is gone) is reaped
    here: its partial index discarded, its failure recorded, its lock removed. A live one is never
    second-guessed by the clock — a big repo's build is allowed all the time it needs."""
    path = _lock_path(root)
    if not os.path.exists(path):
        return None
    data = _read_json(path)
    if data and _pid_alive(data.get("pid")):
        return data
    if not data:
        # Just created by a racing starter that has not written it yet — give it a moment.
        try:
            if now - os.path.getmtime(path) < 10:
                return {"pid": None, "started": os.path.getmtime(path)}
        except OSError:
            return None
    discard_partial(root)
    _drop_next(root)                                     # P32: its side-built rebuild, if any
    _record(root, ok=False, error="the build process ended without reporting a result", now=now)
    try:
        os.remove(path)
    except OSError:
        pass
    return None


def build_status(root: str, *, now: Optional[float] = None) -> BuildStatus:
    """Read-only apart from reaping a dead build (see `_lock_holder`). Never raises."""
    now = time.time() if now is None else now
    try:
        holder = _lock_holder(root, now) if _can_record(root) else None
    except OSError:
        holder = None
    if holder is not None:
        return BuildStatus(BUILDING, started=float(holder.get("started") or now))
    if index_built(root):
        last = _read_json(_last_path(root))
        return BuildStatus(BUILT, finished=last.get("finished"))
    last = _read_json(_last_path(root))
    if last.get("outcome") == "failed":
        return BuildStatus(FAILED, error=str(last.get("error") or "unknown error"),
                           finished=last.get("finished"), retry_after=last.get("retry_after"),
                           attempts=int(last.get("attempts") or 0))
    return BuildStatus(UNBUILT)


def _record(root: str, *, ok: bool, error: str = "", interrupted: bool = False,
            detail: Optional[Dict[str, Any]] = None, now: Optional[float] = None) -> None:
    if not _can_record(root):
        return
    now = time.time() if now is None else now
    prev = _read_json(_last_path(root))
    if ok:
        data = {"outcome": "built", "finished": now}
    elif interrupted:
        data = {"outcome": "interrupted", "finished": now, "error": error}
    else:
        attempts = int(prev.get("attempts") or 0) + 1 if prev.get("outcome") == "failed" else 1
        wait = min(BACKOFF_MAX_S, BACKOFF_BASE_S * (2 ** (attempts - 1)))
        data = {"outcome": "failed", "finished": now, "error": error, "attempts": attempts,
                "retry_after": now + wait}
    data.update(detail or {})
    try:
        _write_json(_last_path(root), data)
    except OSError:
        pass


def _acquire(root: str, pid: int, now: float) -> bool:
    """Create the lock atomically. False when another build holds it."""
    os.makedirs(_run_dir(root), exist_ok=True)
    _lock_holder(root, now)                          # reap a dead holder first
    try:
        fd = os.open(_lock_path(root), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"pid": pid, "started": now}, fh)
    return True


def _set_holder(root: str, pid: int, started: float) -> None:
    try:
        _write_json(_lock_path(root), {"pid": pid, "started": started})
    except OSError:
        pass


def _release(root: str) -> None:
    try:
        os.remove(_lock_path(root))
    except OSError:
        pass


# ----------------------------------------------------------------------------- the build
@dataclass
class BuildResult:
    ok: bool
    error: str = ""
    interrupted: bool = False
    incremental: bool = False             # an existing index was brought up to date (`update`)
    files_done: int = 0
    files_total: int = 0
    elapsed: float = 0.0
    summary: str = ""                     # code-review-graph's own closing line, when it printed one
    busy: Optional[BuildStatus] = None    # another process holds the build (nothing was run)
    repair: str = ""                      # P37: "incremental" / "full" — how an interrupted
    #                                       update was repaired beside the index in use

    @property
    def detail(self) -> str:
        if self.repair == REPAIR_INCREMENTAL:
            return (f"an interrupted update was redone incrementally beside the graph in use and "
                    f"swapped in, in {_clock(self.elapsed)}")
        if self.repair == REPAIR_FULL:
            what = self.summary or "rebuilt"
            return f"repaired by a full rebuild beside the graph in use ({what}) in " \
                   f"{_clock(self.elapsed)}"
        if self.incremental:
            return f"brought up to date incrementally in {_clock(self.elapsed)}"
        what = self.summary or (f"{self.files_total} files" if self.files_total else "built")
        return f"{what} in {_clock(self.elapsed)}"


def _clock(seconds: float) -> str:
    s = int(seconds)
    if s >= 3600:
        return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"
    return f"{s // 60}:{s % 60:02d}"


def _scan_tail(lines: List[str]) -> str:
    """The build's last words, secret-checked (foreign text: withheld whole if anything is
    secret-shaped, never redacted around — the `_McpStdioSession._stderr_tail` rule)."""
    text = " | ".join(ln.strip() for ln in lines if ln.strip())[-400:]
    if not text:
        return ""
    try:
        from ..govern.secrets import scan
    except Exception:                         # noqa: BLE001 — fail CLOSED
        return "(output withheld: it could not be checked for secrets)"
    if scan(text=text):
        return "(output withheld: it contained secret-shaped content)"
    return text


def _crg_command() -> Optional[str]:
    """The resolved code-review-graph (0.0.26 O15: mokata's own environment, version-checked, or
    an explicit `MOKATA_CRG`), or None. Never a bare name a subprocess would look up on PATH."""
    from ..detect import find_command
    from .crg_client import CRG_COMMAND
    return find_command(CRG_COMMAND)


def run_build(root: str, *, command: Optional[str] = None, incremental: bool = False,
              on_progress: Optional[Callable[[str], None]] = None,
              heartbeat: float = HEARTBEAT_S,
              popen: Optional[Callable[..., Any]] = None,
              clock: Callable[[], float] = time.monotonic,
              data_dir: Optional[str] = None) -> BuildResult:
    """Run `code-review-graph build --repo <root>` (or `update`, `incremental=True`, on an index
    that exists) to completion, saying how far it got.

    Progress comes from code-review-graph's own `Progress: N/M files parsed` lines (every 50 files)
    and its post-processing lines; between them a heartbeat names the elapsed time. NO time limit:
    a 1M+ line repo builds for many minutes, and the controls are progress and Ctrl+C
    (KeyboardInterrupt), which kills the build and returns `interrupted` — the caller decides
    whether to go on. 0.0.26 P32: run as an IDE job, the job's Cancel is the same stop
    (`knowledge.cancel`), progress is the job's n/N, and `data_dir` builds into another directory
    (code-review-graph's `CRG_DATA_DIR`) so a cancelled REBUILD never touches the index in use
    (`build_now(swap=True)`). A non-success discards a PARTIAL index (a complete one is kept), because
    code-review-graph cannot resume one: measured in 2.3.9's incremental-build module, a graph marked
    `in-progress` is repaired only by a full `build`. Lock-free: callers hold the lock
    (`build_now`)."""
    say = on_progress or (lambda _s: None)
    res = BuildResult(ok=False)
    binary = command or _crg_command()
    if not binary:
        from ..crg_resolve import resolve_crg
        res.error = resolve_crg().describe()
        return res
    cmd = [binary, "update" if incremental else "build",
           "--repo", os.path.abspath(root)]
    spawn = popen or subprocess.Popen
    t0 = clock()
    tail: deque = deque(maxlen=_TAIL_LINES)
    env = None
    if data_dir is not None:
        env = dict(os.environ, CRG_DATA_DIR=os.path.abspath(data_dir))
    try:
        proc = spawn(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                     stdin=subprocess.DEVNULL, text=True, errors="replace", cwd=root,
                     **({"env": env} if env is not None else {}), **hidden())
    except (OSError, subprocess.SubprocessError) as exc:
        res.error = f"could not start {cmd[0]}: {exc}"
        return res
    lines: deque = deque()
    done = threading.Event()

    def pump() -> None:
        try:
            for line in proc.stdout:
                lines.append(line)
        except (OSError, ValueError):
            pass
        finally:
            done.set()
    threading.Thread(target=pump, name="mokata-crg-build-out", daemon=True).start()
    last_said = clock()
    phase = "starting"
    try:
        while True:
            while lines:
                line = lines.popleft()
                tail.append(line)
                m = _PROGRESS.search(line)
                if m:
                    res.files_done, res.files_total = int(m.group(1)), int(m.group(2))
                    pct = (100 * res.files_done // res.files_total) if res.files_total else 0
                    phase = "post-processing" if res.files_done >= res.files_total else "indexing"
                    say(f"indexing: {res.files_done}/{res.files_total} files ({pct}%) · "
                        f"{_clock(clock() - t0)}")
                    report(done=res.files_done, total=res.files_total, unit="files",
                           note=phase)
                    last_said = clock()
                elif line.startswith("Full build:"):
                    res.summary = line.strip()[len("Full build:"):].strip()
            if done.is_set() and not lines and proc.poll() is not None:
                break
            if clock() - last_said >= heartbeat:
                say(f"still building ({phase}) · {_clock(clock() - t0)} elapsed")
                last_said = clock()
            if cancelled():
                raise KeyboardInterrupt      # the job's Cancel: the same clean stop as Ctrl+C
            if done.is_set():
                try:
                    proc.wait(timeout=0.25)        # output closed: only the exit is left
                except subprocess.TimeoutExpired:
                    pass
            else:
                done.wait(0.25)
    except KeyboardInterrupt:
        _kill(proc)
        res.interrupted = True
    res.elapsed = clock() - t0
    target = os.path.join(data_dir, DB_FILENAME) if data_dir is not None else db_path(root)
    if not res.interrupted:
        rc = proc.wait()
        res.ok = rc == 0 and probe_db(target) == INDEX_BUILT
        if not res.ok:
            why = _scan_tail(list(tail))
            what = cmd[1]
            res.error = (f"code-review-graph {what} exited {rc}" if rc != 0 else
                         f"code-review-graph {what} finished but its index is not complete")
            if why:
                res.error += f": {why}"
    else:
        res.error = "cancelled" if cancelled() else "interrupted (Ctrl+C)"
    if not res.ok:
        if data_dir is None:
            discard_partial(root)
        else:
            _remove_db(target)               # the temp copy only: the index in use is untouched
    try:
        if not res.interrupted or done.wait(5):
            proc.stdout.close()
    except (OSError, ValueError, AttributeError):
        pass
    return res


def _kill(proc: Any) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except (OSError, subprocess.SubprocessError):
        try:
            proc.kill()
            proc.wait(timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass


def build_now(root: str, *, on_progress: Optional[Callable[[str], None]] = None,
              runner: Optional[Callable[..., BuildResult]] = None,
              full: bool = False, swap: bool = False,
              before_swap: Optional[Callable[[], None]] = None) -> BuildResult:
    """Build in THIS process, holding the repo's build lock — setup, `mokata graph build`, the
    IDE's rebuild button, and the background worker all come through here, so two can never run
    at once. When another process holds the lock, nothing runs and `busy` says who.

    A re-run on an index that is already BUILT is incremental (`code-review-graph update`: only
    what changed since) unless `full` asks for a from-scratch rebuild."""
    now = time.time()
    recordable = _can_record(root)
    if recordable and not _acquire(root, os.getpid(), now):
        return BuildResult(ok=False, error="another build of this index is running",
                           busy=build_status(root))
    try:
        try:
            # P35: an index that ever completed is UPDATED (code-review-graph promotes an
            # interrupted one to a full rebuild itself), never rebuilt from scratch in place.
            incremental = not full and (index_built(root) or had_complete_build(db_path(root)))
            repair = ""
            if incremental and probe_db(db_path(root)) == INDEX_INCOMPLETE:
                # P37 (R39): over an index marked `in-progress` code-review-graph's `update` is a
                # full build IN PLACE (`build_was_interrupted`). An UPDATE we recorded is redone
                # incrementally on a copy beside the index (minutes become seconds); anything else
                # (an unrecorded marker, a copy that fails its integrity check) is rebuilt in full
                # beside it. Either way the index in use serves until the swap.
                repair = REPAIR_INCREMENTAL if update_state(root) == UPDATE_INTERRUPTED \
                    else REPAIR_FULL
                swap = True
                full = repair == REPAIR_FULL
            if swap and (full or repair) and (index_built(root)
                                              or had_complete_build(db_path(root))):
                # R39: rebuild BESIDE the index in use; it is replaced only when the rebuild is
                # complete. A cancel (or a failure) leaves the old graph COMPLETE.
                short = disk_shortfall(root)
                if short:
                    res = BuildResult(ok=False, error=short)
                    res.incremental = incremental
                    res.repair = repair
                    if recordable:
                        _record(root, ok=False, error=short)
                    return res
                why_full = ""
                began_at = time.time()
                if repair == REPAIR_INCREMENTAL:
                    why_full = _seed_beside(root)
                    if why_full:
                        repair = REPAIR_FULL
                if repair != REPAIR_INCREMENTAL:
                    _remove_db(os.path.join(next_dir(root), DB_FILENAME))
                    os.makedirs(next_dir(root), exist_ok=True)
                res = (runner or run_build)(root, on_progress=on_progress,
                                            incremental=repair == REPAIR_INCREMENTAL,
                                            data_dir=next_dir(root))
                res.repair = repair
                res.elapsed = time.time() - began_at          # the copy and its check included
                if why_full:
                    res.summary = (f"full rebuild: {why_full}" + (f" · {res.summary}"
                                                                  if res.summary else ""))
                if res.ok:
                    why = swap_in(root, before_swap, since=began_at)
                    if why:
                        res.ok, res.error = False, why
                _drop_next(root)             # whatever happened, no side copy is left behind
                if res.interrupted:
                    # Not a failure of the index in use: record nothing that would back off.
                    return res
                incremental = repair == REPAIR_INCREMENTAL
            else:
                began = incremental and _begin_update(root)
                try:
                    res = (runner or run_build)(root, on_progress=on_progress,
                                                incremental=incremental)
                except BaseException:
                    if began:
                        _end_update(root, False)
                    raise
                if began:
                    _end_update(root, res.ok)
            if res.ok and (swap or repair):
                _end_update(root, True)      # the swapped-in index is complete: nothing pending
            res.incremental = incremental
        except KeyboardInterrupt:
            # A Ctrl+C that landed outside the runner's own loop (while spawning, while reaping):
            # same outcome — no partial index left looking healthy.
            discard_partial(root)
            res = BuildResult(ok=False, interrupted=True, error="interrupted (Ctrl+C)")
        if recordable:
            _record(root, ok=res.ok, error=res.error, interrupted=res.interrupted)
        return res
    finally:
        if recordable:
            _release(root)


_LOG_PROGRESS = re.compile(r"(\d+)\s*/\s*(\d+)\s*files")


def watch_running(root: str, *, poll: float = 1.0,
                  sleep: Callable[[float], None] = time.sleep) -> BuildStatus:
    """0.0.26 P32 — a build ANOTHER process runs (the first-use background worker, a terminal's
    `mokata graph build`), followed for the IDE's Activity: its progress is read off the
    worker's log and reported as the job's n/N until the build ends. Read-only; never stops it
    (it is not this process's to stop). Returns how it ended."""
    started = None
    while True:
        st = build_status(root)
        if st.state != BUILDING:
            return st
        started = started or st.started
        done = total = None
        try:
            with open(log_path(root), "rb") as fh:
                fh.seek(0, os.SEEK_END)
                fh.seek(max(0, fh.tell() - 4096))
                tail = fh.read().decode("utf-8", "replace")
            hits = _LOG_PROGRESS.findall(tail)
            if hits:
                done, total = int(hits[-1][0]), int(hits[-1][1])
        except OSError:
            pass
        report(done=done, total=total, unit="files",
               note=f"code-review-graph is building its index (started {hhmm(started)}, "
                    "by another process)")
        sleep(poll)


# ----------------------------------------------------------------------------- first use (R-a)
def _spawn_worker(root: str) -> int:
    """Start the background builder: this interpreter, `-m mokata.knowledge.crg_build <root>`,
    detached (outlives the query that started it) and console-less on Windows. Returns its pid."""
    os.makedirs(_run_dir(root), exist_ok=True)
    log = open(log_path(root), "w", encoding="utf-8", newline="\n")
    try:
        env = dict(os.environ)
        here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        env["PYTHONPATH"] = here + (os.pathsep + env["PYTHONPATH"]
                                    if env.get("PYTHONPATH") else "")
        proc = subprocess.Popen([sys.executable, "-m", "mokata.knowledge.crg_build",
                                 os.path.abspath(root)],
                                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                cwd=root, env=env, **detached())
    finally:
        log.close()
    return proc.pid


def ensure_building(root: str, *, now: Optional[float] = None,
                    spawn: Optional[Callable[[str], int]] = None) -> BuildStatus:
    """R-a: called when a query hit an unbuilt index. Starts AT MOST ONE background build per repo
    and NEVER waits for it. Returns what the query's note should say:

      * BUILT      — it finished meanwhile (the next query is the graph's);
      * BUILDING   — one is running (this call's or another process's);
      * FAILED     — the last automatic build failed and its back-off has not expired: no retry
                     storm; the error is surfaced (doctor, IDE health) with the rebuild action;
      * UNBUILT    — no `.mokata/` here to keep run-state in, so nothing was started."""
    now = time.time() if now is None else now
    status = build_status(root, now=now)
    if status.state in (BUILT, BUILDING):
        return status
    if status.state == FAILED and status.retry_after and now < float(status.retry_after):
        return status
    if not _can_record(root):
        return status
    try:
        if not _acquire(root, os.getpid(), now):
            return build_status(root, now=now)
        try:
            pid = (spawn or _spawn_worker)(root)
        except (OSError, subprocess.SubprocessError) as exc:
            _release(root)
            _record(root, ok=False, error=f"could not start the background build: {exc}",
                    now=now)
            return build_status(root, now=now)
        _set_holder(root, pid, now)
    except OSError:
        return status
    return BuildStatus(BUILDING, started=now)


def main(argv: Optional[List[str]] = None) -> int:
    """The background worker (`python -m mokata.knowledge.crg_build <root>`). Started holding the
    lock its starter created; runs the build, records the outcome, releases the lock."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return 2
    root = args[0]
    try:
        res = run_build(root, on_progress=lambda s: print(s, flush=True))
        _record(root, ok=res.ok, error=res.error, interrupted=res.interrupted)
        print(("built: " + res.detail) if res.ok else ("failed: " + res.error), flush=True)
        return 0 if res.ok else 1
    except Exception as exc:  # noqa: BLE001 — a worker that dies must still leave a verdict
        _record(root, ok=False, error=f"{type(exc).__name__}: {exc}")
        return 1
    finally:
        _release(root)


if __name__ == "__main__":
    sys.exit(main())
