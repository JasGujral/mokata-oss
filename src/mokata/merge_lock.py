"""0.0.27 P21 — the main checkout's `index.lock`, held by the gated merge, and how a stale one is
proven to be ours before anything removes it (rule note §3; re-review R1/R2, coordinator rulings).

WHY THE MERGE HOLDS GIT'S OWN LOCK. From the last pre-CAS check (s4) until the main checkout's
index and files have been brought to the merged commit (s6), git's `index.lock` is held. A human's
`git commit`, `git add`, `git stash` in the main checkout then fail at once ("index.lock exists")
instead of committing an index that does not match HEAD. That matters most right after the CAS:
HEAD is `M` there while the index is still `base`, and a plain `git commit` would silently revert
the merge. On a checkout failure the lock STAYS until an explicit Recover (R1).

THE OWNER RECORD LIVES INSIDE THE LOCK FILE (R2.1). The lock is created with `O_CREAT|O_EXCL` and
the record is written into that same handle before anything else happens, so there is no window
in which the lock exists without saying whose it is, and a lock git made can never match: git's
lock content is index data, which starts with the `DIRC` signature; ours starts with `MAGIC`.

THE RECORD CAN NEVER BECOME `.git/index` (coordinator R2). Exactly one function renames a lock onto
the index — `IndexLock.commit_index` — and it renames only after it has replaced the lock's bytes
with the temp index's bytes and re-read them: the file must start with `DIRC`, must not start with
`MAGIC`, and must hash equal to the temp index it copied (`_assert_index_bytes`). Recovery never
renames; it only unlinks, and only a lock proven ours and dead. Git never renames a lock it did not
create (it holds its lock as an open handle and an `O_EXCL` create of an existing file fails).
So owner-record bytes reach `.git/index` by no path.

OWNER IDENTITY (R2.2/2.3, coordinator R2) = host + boot id + pid + the pid's start time. A lock is
removable only when, re-verified at removal time:
  * the file's content parses as OUR record;
  * the host is this host and the lock is not on a network filesystem (a pid means nothing on
    another machine — refuse loudly, never "dead");
  * the boot id is readable — a DIFFERENT boot id on this host means the machine restarted since,
    so the owner is gone; the SAME boot id means the pid is checked:
      - no such process -> dead;
      - a process whose start time is readable and differs -> the pid was recycled -> dead;
      - our own pid, but no merge with that id is running in this process -> stale;
      - anything that cannot be read -> NOT removable (refuse, say why).

Stdlib only. Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Dict, Optional, Set, Tuple

from .errors import MokataError

MAGIC = b"MOKATA-MERGE-LOCK v1\n"
INDEX_SIGNATURE = b"DIRC"
RECORD_MAX = 4096

#: The merge ids running in THIS process (`execute` adds and removes them) — what makes our own pid
#: with an unknown merge id provably stale.
_ACTIVE: Set[str] = set()
_ACTIVE_LOCK = threading.Lock()

NETWORK_FS = frozenset({"nfs", "nfs4", "cifs", "smb", "smb2", "smb3", "smbfs", "afpfs", "webdav",
                        "fuse.sshfs", "sshfs", "9p", "afs", "ceph", "glusterfs", "fuse.glusterfs",
                        "lustre", "gpfs", "davfs", "fuse.rclone", "fuse.s3fs"})


class LockError(MokataError):
    """The index lock could not be taken, or a lock's ownership could not be proven. `code` is the
    outcome state the merge reports (`busy`, `lock-not-ours`, `lock-unverifiable`, …)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# ================================================================================ identity
def boot_id() -> Optional[str]:
    """This machine's boot identity, or None when it cannot be read (then nothing is removable)."""
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/sys/kernel/random/boot_id", "r", encoding="ascii") as fh:
                return fh.read().strip() or None
        except OSError:
            return None
    if sys.platform == "darwin":
        return _sysctl("kern.bootsessionuuid")
    if os.name == "nt":                                         # pragma: no cover — Windows only
        try:
            import ctypes
            ms = ctypes.windll.kernel32.GetTickCount64()
            # boot time to the minute: stable across reads, different after a restart
            return "boot-" + str(int((time.time() - ms / 1000.0) // 60))
        except (AttributeError, OSError):
            return None
    return None


def _sysctl(name: str) -> Optional[str]:
    from .quiet_spawn import hidden
    exe = "/usr/sbin/sysctl"
    if not os.path.isfile(exe):
        return None
    try:
        out = subprocess.run([exe, "-n", name], capture_output=True, text=True,  # nosec B603
                             stdin=subprocess.DEVNULL, timeout=10, **hidden())
    except (OSError, subprocess.SubprocessError):
        return None
    return (out.stdout or "").strip() or None if out.returncode == 0 else None


def pid_start(pid: int) -> Tuple[bool, Optional[str]]:
    """(exists, start time) for `pid`. `start` is None when it cannot be read; `exists` False only
    when the OS says there is no such process."""
    if sys.platform.startswith("linux"):
        try:
            with open(f"/proc/{int(pid)}/stat", "r", encoding="ascii", errors="replace") as fh:
                raw = fh.read()
        except FileNotFoundError:
            return False, None
        except OSError:
            return _exists(pid), None
        try:
            return True, raw.rsplit(")", 1)[1].split()[19]        # field 22: starttime (ticks)
        except IndexError:
            return True, None
    if os.name == "nt":                                         # pragma: no cover — Windows only
        return _win_pid_start(pid)
    if not _exists(pid):
        return False, None
    from .quiet_spawn import hidden
    exe = "/bin/ps"
    if not os.path.isfile(exe):
        return True, None
    try:
        out = subprocess.run([exe, "-o", "lstart=", "-p", str(int(pid))], capture_output=True,  # nosec B603
                             text=True, stdin=subprocess.DEVNULL, timeout=10,
                             env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"}, **hidden())
    except (OSError, subprocess.SubprocessError):
        return True, None
    text = (out.stdout or "").strip()
    if out.returncode != 0 or not text:
        return _exists(pid), None
    return True, text


def _exists(pid: int) -> bool:
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


def _win_pid_start(pid: int) -> Tuple[bool, Optional[str]]:     # pragma: no cover — Windows only
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, int(pid))                # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        err = ctypes.get_last_error() if hasattr(ctypes, "get_last_error") else 0
        return (err != 87), None                                # 87: invalid parameter = no pid
    try:
        c, e, k, u = (wintypes.FILETIME() for _ in range(4))
        if not k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(k),
                                   ctypes.byref(u)):
            return True, None
        return True, str((c.dwHighDateTime << 32) | c.dwLowDateTime)
    finally:
        k32.CloseHandle(h)


def network_fs(path: str) -> Optional[bool]:
    """True when `path` is on a network filesystem, False when it is local, None when unknown
    (unknown is treated like network by every caller: nothing is declared dead)."""
    try:
        real = os.path.realpath(path)
    except (OSError, ValueError):
        return None
    if os.name == "nt":                                         # pragma: no cover — Windows only
        if real.startswith("\\\\"):
            return True
        try:
            import ctypes
            drive = os.path.splitdrive(real)[0] + "\\"
            return ctypes.windll.kernel32.GetDriveTypeW(drive) == 4     # DRIVE_REMOTE
        except (AttributeError, OSError):
            return None
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/self/mountinfo", "r", encoding="utf-8", errors="replace") as fh:
                rows = fh.read().splitlines()
        except OSError:
            return None
        best, kind = "", None
        for row in rows:
            left, _, right = row.partition(" - ")
            parts, rparts = left.split(), right.split()
            if len(parts) < 5 or not rparts:
                continue
            mnt = parts[4].replace("\\040", " ")
            if (real == mnt or real.startswith(mnt.rstrip("/") + "/")) and len(mnt) >= len(best):
                best, kind = mnt, rparts[0]
        return None if kind is None else kind.lower() in NETWORK_FS
    if sys.platform == "darwin":
        return _darwin_fstype(real)
    return None


def _darwin_fstype(path: str) -> Optional[bool]:
    try:
        import ctypes
        import ctypes.util

        class _Statfs(ctypes.Structure):                       # struct statfs (64-bit inode)
            _fields_ = [("f_bsize", ctypes.c_uint32), ("f_iosize", ctypes.c_int32),
                        ("f_blocks", ctypes.c_uint64), ("f_bfree", ctypes.c_uint64),
                        ("f_bavail", ctypes.c_uint64), ("f_files", ctypes.c_uint64),
                        ("f_ffree", ctypes.c_uint64), ("f_fsid", ctypes.c_int32 * 2),
                        ("f_owner", ctypes.c_uint32), ("f_type", ctypes.c_uint32),
                        ("f_flags", ctypes.c_uint32), ("f_fssubtype", ctypes.c_uint32),
                        ("f_fstypename", ctypes.c_char * 16),
                        ("f_mntonname", ctypes.c_char * 1024),
                        ("f_mntfromname", ctypes.c_char * 1024),
                        ("f_flags_ext", ctypes.c_uint32), ("f_reserved", ctypes.c_uint32 * 7)]
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        fn = getattr(libc, "statfs$INODE64", None) or libc.statfs
        buf = _Statfs()
        if fn(path.encode("utf-8"), ctypes.byref(buf)) != 0:
            return None
        local = int(buf.f_flags) & 0x00001000                   # MNT_LOCAL
        name = buf.f_fstypename.decode("ascii", "replace").lower()
        return (not local) or name in NETWORK_FS
    except (OSError, AttributeError, ValueError):
        return None


def owner(merge_id: str) -> Dict[str, Any]:
    """The record this process writes into a lock it takes."""
    exists, start = pid_start(os.getpid())
    return {"host": socket.gethostname(), "boot": boot_id(), "pid": os.getpid(),
            "start": start if exists else None, "merge": merge_id, "at": time.time()}


def mark_active(merge_id: str, on: bool) -> None:
    with _ACTIVE_LOCK:
        (_ACTIVE.add if on else _ACTIVE.discard)(merge_id)


def is_active(merge_id: str) -> bool:
    with _ACTIVE_LOCK:
        return merge_id in _ACTIVE


# ================================================================================ the lock
def lock_path(gitdir: str) -> str:
    return os.path.join(gitdir, "index.lock")


def read_record(path: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """(our owner record, why not) for the lock at `path`. A lock whose content is not our record
    (git's own index data, garbage, an unreadable file) is never ours."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(len(MAGIC) + RECORD_MAX)
    except FileNotFoundError:
        return None, "no lock"
    except OSError as exc:
        return None, f"the lock cannot be read ({exc})"
    if not head.startswith(MAGIC):
        return None, "the lock was not made by a mokata merge (its content is not our record)"
    body = head[len(MAGIC):].split(b"\n", 1)[0]
    try:
        rec = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None, "the lock starts like ours but its record is damaged — not removable"
    if not isinstance(rec, dict) or not isinstance(rec.get("pid"), int):
        return None, "the lock's record is incomplete — not removable"
    return rec, ""


#: The Recover help every surface repeats (see merge_gate): the trap after a hand-deleted lock.
HAND_DELETED_LOCK_HELP = (
    "If a crash left the lock and you delete it by hand, the branch is already at the merged commit "
    "but the index still reads the old one: do NOT run `git commit` (it would undo the merge). "
    "Run `mokata worktree merge --recover <merge-id>` instead - it reads git and finishes the index "
    "and files from the merged commit.")


def stale_verdict(path: str, rec: Dict[str, Any]) -> Tuple[bool, str]:
    """(removable, why) for OUR record `rec` on the lock at `path`. Never declares a pid dead it
    cannot prove dead."""
    host = socket.gethostname()
    if rec.get("host") != host:
        return False, (f"held by a merge on another machine ({rec.get('host')!r}) — a pid there "
                       f"means nothing here; finish or recover it from that machine")
    nfs = network_fs(os.path.dirname(path))
    if nfs is not False:
        return False, ("the repository is on a network filesystem (or its kind cannot be read) — "
                       "another machine could hold this lock; mokata will not declare its owner "
                       "dead. Remove it by hand only if you are sure no git runs, and read the Recover help below first")
    here = boot_id()
    if not here or not rec.get("boot"):
        return False, "this machine's boot id cannot be read — the owner cannot be proven gone"
    if rec["boot"] != here:
        return True, "the machine has restarted since this lock was taken — its owner is gone"
    pid = int(rec["pid"])
    if pid == os.getpid():
        if is_active(str(rec.get("merge") or "")):
            return False, "this IDE is still running that merge"
        return True, "this process took the lock for a merge that is no longer running"
    exists, start = pid_start(pid)
    if not exists:
        return True, f"the merge's process ({pid}) has exited"
    if start is None or rec.get("start") is None:
        return False, (f"process {pid} exists and its start time cannot be read — it may still be "
                       f"the merge; not removable")
    if start != rec["start"]:
        return True, f"pid {pid} now belongs to a different process — the merge's process is gone"
    return False, f"the merge's process ({pid}) is still running"


class IndexLock:
    """git's `index.lock`, taken with our owner record inside it. The open handle is kept until
    `commit_index` (Windows: closed before the rename) or `release`."""

    def __init__(self, gitdir: str, merge_id: str) -> None:
        self.gitdir, self.merge_id = gitdir, merge_id
        self.path = lock_path(gitdir)
        self.fd: Optional[int] = None
        self.record: Dict[str, Any] = {}

    def acquire(self) -> "IndexLock":
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        try:
            fd = os.open(self.path, flags, 0o644)
        except FileExistsError as exc:
            raise LockError("busy", "the main checkout's index is locked (`.git/index.lock` exists — "
                                    "a git command is running there, or one crashed). Nothing was "
                                    "changed; retry when it is free") from exc
        except OSError as exc:
            raise LockError("busy", f"the index lock could not be taken ({exc}); nothing was "
                                    f"changed") from exc
        self.fd = fd
        self.record = owner(self.merge_id)
        try:
            os.write(fd, MAGIC + json.dumps(self.record, sort_keys=True).encode("utf-8") + b"\n")
            os.fsync(fd)
        except OSError as exc:
            self.release()
            raise LockError("busy", f"the index lock could not be written ({exc}); nothing was "
                                    f"changed") from exc
        return self

    def _close(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None

    def release(self) -> None:
        """Give the lock back unchanged: close our handle, then unlink the file we created."""
        self._close()
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass

    def keep(self) -> None:
        """Leave the lock (and its record) in place for an explicit Recover — close our handle."""
        self._close()

    def commit_index(self, temp_index: str) -> None:
        """Replace `.git/index` with `temp_index`, the way git commits a lock: its bytes into the
        lock, fsync, CLOSE (Windows cannot rename an open file), verify, rename onto the index."""
        if self.fd is None:
            raise LockError("lock-lost", "the merge no longer holds the index lock")
        with open(temp_index, "rb") as fh:
            data = fh.read()
        _assert_index_bytes(data, "the new index")
        want = hashlib.sha256(data).hexdigest()
        try:
            os.lseek(self.fd, 0, os.SEEK_SET)
            os.ftruncate(self.fd, 0)
            view = memoryview(data)
            while view:
                n = os.write(self.fd, view)
                view = view[n:]
            os.fsync(self.fd)
            self._close()
            with open(self.path, "rb") as fh:
                back = fh.read()
            _assert_index_bytes(back, "the lock about to become the index")
            if hashlib.sha256(back).hexdigest() != want:
                raise LockError("lock-lost", "the index lock's bytes changed under the merge — the "
                                             "index was NOT replaced")
            os.replace(self.path, os.path.join(self.gitdir, "index"))
        except (OSError, LockError):
            self._restore_record()
            raise

    def _restore_record(self) -> None:
        """A failed commit leaves the lock HELD and marked as ours again (R1): the half-written
        index bytes are replaced by our record, so Recover can prove the lock is mokata's."""
        self._close()
        try:
            with open(self.path, "r+b") as fh:
                fh.truncate(0)
                fh.write(MAGIC + json.dumps(self.record, sort_keys=True).encode("utf-8") + b"\n")
                fh.flush()
                os.fsync(fh.fileno())
        except OSError:
            pass                 # the lock (whatever it holds) stays; Recover will say it is not ours


def _assert_index_bytes(data: bytes, what: str) -> None:
    """THE guard: only real index bytes are ever renamed onto `.git/index`."""
    if data.startswith(MAGIC) or not data.startswith(INDEX_SIGNATURE) or len(data) < 32:
        raise LockError("lock-lost", f"{what} is not a git index — refusing to rename it onto "
                                     f".git/index")


def remove_stale(gitdir: str, expect_sha: str) -> Tuple[bool, str]:
    """Unlink a stale lock that is OURS and DEAD — re-verified now (content hash equal to what the
    human approved, record, host, boot, pid, start). Returns (removed, why)."""
    path = lock_path(gitdir)
    try:
        with open(path, "rb") as fh:
            raw = fh.read(len(MAGIC) + RECORD_MAX)
    except FileNotFoundError:
        return True, "the lock is already gone"
    except OSError as exc:
        return False, f"the lock cannot be read ({exc})"
    if hashlib.sha256(raw).hexdigest() != expect_sha:
        return False, "the lock changed since you approved its removal — nothing removed; recover again"
    rec, why = read_record(path)
    if rec is None:
        return False, why
    ok, why = stale_verdict(path, rec)
    if not ok:
        return False, why
    try:
        os.unlink(path)
    except FileNotFoundError:
        return True, "the lock is already gone"
    except OSError as exc:
        return False, f"the lock could not be removed ({exc})"
    return True, why


def lock_sha(gitdir: str) -> Optional[str]:
    try:
        with open(lock_path(gitdir), "rb") as fh:
            return hashlib.sha256(fh.read(len(MAGIC) + RECORD_MAX)).hexdigest()
    except OSError:
        return None


__all__ = ["IndexLock", "LockError", "MAGIC", "boot_id", "is_active", "lock_path", "lock_sha",
           "mark_active", "network_fs", "owner", "pid_start", "read_record", "remove_stale",
           "stale_verdict"]
