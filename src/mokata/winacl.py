"""Private files — BORN readable by their creator only, verified, and read back only if they still are
(0.0.28 lane C, `WIN-OWNER-ONLY-FILES`; note C §3a/§3c, rule page `0028-evidence/C/C.rule.md`).

The private set is small and named by what the bytes ARE: a file holding the IDE server token or the
bridge hook token (`server.json`, `bridge.json`, and `.claude/settings.local.json` when mokata writes the
token into it), the user's workspace-trust decision (`trust.json`), IDE spill scratch, and the explainer
reviews store from `reviews/` down. Nothing else is made private here.

THE RULE. The descriptor is part of the CREATE call, and the open handle is verified before any content is
written. Nothing is created and then restricted: Windows checks access when a handle is OPENED, so a file
protected a moment after it exists has already leaked to whoever opened it in between (measured on a
runner: 105 of 3000 writes).
  * Windows: `CreateFileW(CREATE_NEW)` / `CreateDirectoryW` with a protected DACL holding ONE allow entry,
    full access, for the current user (a directory's entry is inherited by its children). No SYSTEM, no
    Administrators entry (ruling C-Q1). Backup software (`SeBackupPrivilege`) and Defender still read the
    file, and an administrator can take ownership.
  * POSIX: `open(O_CREAT|O_EXCL|O_NOFOLLOW, 0o600)` / `mkdir(0o700)`, relative to a held directory fd when
    the caller has one. macOS also inherits an `allow` ACE from a parent that carries an inheritable one,
    and ACLs are read before the mode bits: under such a parent the file is created inside a fresh stage
    directory whose ACL is stripped while it is still EMPTY, then linked into place.
  * Verified on the open handle: owner is this user (or, on Windows, BUILTIN\\Administrators when this
    token holds that group — an elevated process owns its files as Administrators), no group/other access
    (POSIX mode `& 0o077 == 0`; macOS: no ACL entry; Windows: exactly one allow entry, this user's).

DEGRADE, NEVER RAISE, NEVER CLAIM. Decided by what the volume DOES, never by its name. Windows: a volume
without `FILE_PERSISTENT_ACLS` (FAT, exFAT: `CreateFileW` ignores the descriptor and SUCCEEDS). POSIX: a
probe file made 0o600 and one made 0o400 in the same directory do not read back as made (CIFS/SMB, WSL2
drvfs, ntfs-3g, FAT: one mount-wide mode) — learnt once per volume per process, and asked only when it
changes the outcome; macOS also: a mount that ignores ownership. There the file is made plainly, the writer
returns normally, the user's own record is still read, and `mokata doctor` says "this volume has no access
control: private files are as private as the disk". A file owned by someone else is still refused wherever
ownership is observable. A verification that fails on a place that CAN hold privacy is a real failure: the
file is removed and `OSError` raised.

READERS (`read_record`, `read_private`) open once without following a link and without blocking (a FIFO
planted at a record path cannot hang a hook or doctor), require a regular file, verify THAT handle, and
read THAT handle. A file owned by someone else, or weaker than the descriptor on a place that can hold it,
is refused (None): never returned, never re-protected. A reader never writes the record (the volume probe
is a scratch file of its own, removed at once).

Stdlib only (`ctypes` is imported lazily, and only for the private door). Imports nothing from mokata.
Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import errno
import os
import stat
import sys
from typing import Dict, NamedTuple, Optional, Tuple, Union

_NT = os.name == "nt"
_MAC = sys.platform == "darwin"

#: The doctor line for a place that cannot hold privacy (said, never hidden).
NO_ACCESS_CONTROL = "this volume has no access control: private files are as private as the disk"


class Verdict(NamedTuple):
    private: bool          # only this user can open it, on a place that holds that
    owner_is_me: bool
    holds: bool            # can this place hold privacy at all
    reason: str            # plain words when not private

    @property
    def accept(self) -> bool:
        """May a reader trust it? Ours, and private — or ours on a place that cannot hold privacy."""
        return self.owner_is_me and (self.private or not self.holds)


# =============================================================================== POSIX helpers

_LIBC = None
_ACL_TYPE_EXTENDED = 0x00000100
_MNT_IGNORE_OWNERSHIP = 0x00200000


def _libc():
    global _LIBC
    if _LIBC is None:
        import ctypes
        lib = ctypes.CDLL(None, use_errno=True)
        vp = ctypes.c_void_p
        if _MAC:
            lib.acl_get_fd_np.argtypes = [ctypes.c_int, ctypes.c_int]
            lib.acl_get_fd_np.restype = vp
            lib.acl_get_entry.argtypes = [vp, ctypes.c_int, ctypes.POINTER(vp)]
            lib.acl_get_entry.restype = ctypes.c_int
            lib.acl_free.argtypes = [vp]
            lib.acl_init.argtypes = [ctypes.c_int]
            lib.acl_init.restype = vp
            lib.acl_set_fd_np.argtypes = [ctypes.c_int, vp, ctypes.c_int]
            lib.acl_set_fd_np.restype = ctypes.c_int
        lib.fstatfs.argtypes = [ctypes.c_int, ctypes.c_char_p]
        lib.fstatfs.restype = ctypes.c_int
        _LIBC = (ctypes, lib)
    return _LIBC


def _mac_acl_entries(fd: int) -> int:
    """macOS: 1 when the fd's file carries any extended ACL entry, else 0."""
    ctypes, lib = _libc()
    acl = lib.acl_get_fd_np(fd, _ACL_TYPE_EXTENDED)
    if not acl:
        return 0
    try:
        entry = ctypes.c_void_p()
        return 1 if lib.acl_get_entry(acl, 0, ctypes.byref(entry)) == 0 else 0
    finally:
        lib.acl_free(acl)


def _mac_strip(fd: int) -> None:
    """macOS: replace the fd's ACL with an EMPTY one (called only on an empty directory)."""
    ctypes, lib = _libc()
    acl = lib.acl_init(0)
    if not acl:
        raise OSError(ctypes.get_errno(), "acl_init failed")
    try:
        if lib.acl_set_fd_np(fd, acl, _ACL_TYPE_EXTENDED) != 0:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err))
    finally:
        lib.acl_free(acl)


#: st_dev -> does this volume HOLD a private mode? Learnt from what the volume DID to a probe file this
#: process made (`_probe`), never typed from a filesystem's name. Per process; unknown means "holds".
_HOLDS: Dict[int, bool] = {}


def _ignores_ownership(fd: int) -> bool:
    """macOS: the volume is mounted ignoring ownership (every file reports this user as its owner), so a
    planted file cannot be told from ours. The OS reports it in the mount's flags."""
    if not _MAC:
        return False
    try:
        ctypes, lib = _libc()
        buf = ctypes.create_string_buffer(4096)
        if lib.fstatfs(fd, buf) != 0:
            return False
        import struct
        return bool(struct.unpack_from("<I", buf.raw, 64)[0] & _MNT_IGNORE_OWNERSHIP)
    except (OSError, AttributeError):
        return False


def _probe(directory: Optional[str], dir_fd: Optional[int] = None) -> Optional[bool]:
    """Ask the volume, by behaviour: make two fresh files in `directory`, one 0o600 and one 0o400, read
    each mode back on its handle, remove both. The volume holds a private mode only if each reads back
    exactly as it was created (a CIFS / drvfs / ntfs-3g / FAT mount reports one mount-wide mode for both).
    Nothing is ever chmod'ed. None when no probe file can be made there (unknown)."""
    import secrets
    seen = []
    for want in (0o600, 0o400):
        name = f".mkprobe-{secrets.token_hex(8)}"
        path = name if dir_fd is not None else os.path.join(directory or ".", name)
        try:
            fd = os.open(path, _FLAGS, want, dir_fd=dir_fd)
        except OSError:
            return None
        try:
            st = os.fstat(fd)
            seen.append(stat.S_IMODE(st.st_mode) & 0o777 == want)
        except OSError:
            return None
        finally:
            os.close(fd)
            _unlink(path, dir_fd)
    _HOLDS[st.st_dev] = held = all(seen)
    return held


def _posix_verdict(fd: int, where: Optional[str] = None, dir_fd: Optional[int] = None,
                   ask: bool = False) -> Verdict:
    """The verdict of `fd`. Whether its volume holds a private mode is the volume's own behaviour
    (`_probe`, run in `where` — the file's directory — once per volume per process), asked only when the
    answer changes what a caller does: a file of OURS that looks weak (accept it degraded, or refuse it),
    or `ask` (doctor, so it never calls a file private on a volume that cannot hold that)."""
    st = os.fstat(fd)
    mine = st.st_uid == os.geteuid()
    mode = stat.S_IMODE(st.st_mode)
    why = []
    if not mine:
        why.append(f"owned by uid {st.st_uid}, not this user ({os.geteuid()})")
    if mode & 0o077:
        why.append(f"mode {oct(mode)} lets other users in")
    if _MAC and _mac_acl_entries(fd):
        why.append("it carries an ACL entry for someone else")
    if _ignores_ownership(fd):
        holds = False
    else:
        known = _HOLDS.get(st.st_dev)
        if known is None and (where is not None or dir_fd is not None) and (ask or (mine and why)):
            known = _probe(where, dir_fd)
        holds = True if known is None else known
    if not holds:
        why.append(NO_ACCESS_CONTROL)
    return Verdict(holds and not why, mine, holds, "; ".join(why))


# =============================================================================== Windows helpers

_W = None
_SID: Optional[str] = None
_GENERIC_READ, _GENERIC_WRITE = 0x80000000, 0x40000000
_READ_CONTROL, _FILE_READ_ATTRIBUTES = 0x00020000, 0x80
_SHARE_ALL = 0x7
_CREATE_NEW, _OPEN_EXISTING = 1, 3
_FILE_ATTRIBUTE_NORMAL = 0x80
_BACKUP_SEMANTICS, _OPEN_REPARSE = 0x02000000, 0x00200000
_FILE_PERSISTENT_ACLS = 0x8
_OWNER_INFO, _DACL_INFO = 0x1, 0x4
_SE_FILE_OBJECT = 1
_ADMINS = "S-1-5-32-544"


def _win():
    global _W
    if _W is None:
        import ctypes
        from ctypes import wintypes
        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        ker = ctypes.WinDLL("kernel32", use_last_error=True)
        vp = ctypes.c_void_p
        ker.GetCurrentProcess.restype = vp
        ker.LocalFree.argtypes = [vp]
        ker.LocalFree.restype = vp
        ker.CloseHandle.argtypes = [vp]
        ker.CreateFileW.argtypes = [ctypes.c_wchar_p, wintypes.DWORD, wintypes.DWORD, vp,
                                    wintypes.DWORD, wintypes.DWORD, vp]
        ker.CreateFileW.restype = vp
        ker.CreateDirectoryW.argtypes = [ctypes.c_wchar_p, vp]
        ker.CreateDirectoryW.restype = wintypes.BOOL
        ker.GetVolumeInformationByHandleW.argtypes = [vp, vp, wintypes.DWORD, vp, vp,
                                                      ctypes.POINTER(wintypes.DWORD), vp,
                                                      wintypes.DWORD]
        ker.GetVolumeInformationByHandleW.restype = wintypes.BOOL
        adv.OpenProcessToken.argtypes = [vp, wintypes.DWORD, ctypes.POINTER(vp)]
        adv.GetTokenInformation.argtypes = [vp, ctypes.c_int, vp, wintypes.DWORD,
                                            ctypes.POINTER(wintypes.DWORD)]
        adv.ConvertSidToStringSidW.argtypes = [vp, ctypes.POINTER(ctypes.c_wchar_p)]
        adv.ConvertStringSidToSidW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(vp)]
        adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            ctypes.c_wchar_p, wintypes.DWORD, ctypes.POINTER(vp), vp]
        adv.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
            vp, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_wchar_p), vp]
        adv.GetSecurityInfo.argtypes = [vp, ctypes.c_int, wintypes.DWORD, ctypes.POINTER(vp),
                                        ctypes.POINTER(vp), ctypes.POINTER(vp), ctypes.POINTER(vp),
                                        ctypes.POINTER(vp)]
        adv.GetSecurityInfo.restype = wintypes.DWORD
        adv.GetAclInformation.argtypes = [vp, vp, wintypes.DWORD, ctypes.c_int]
        adv.GetAce.argtypes = [vp, wintypes.DWORD, ctypes.POINTER(vp)]
        adv.EqualSid.argtypes = [vp, vp]
        _W = (ctypes, wintypes, adv, ker)
    return _W


def _token_info(klass: int):
    ctypes, wintypes, adv, ker = _win()
    token = ctypes.c_void_p()
    if not adv.OpenProcessToken(ker.GetCurrentProcess(), 0x8, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = wintypes.DWORD(0)
        adv.GetTokenInformation(token, klass, None, 0, ctypes.byref(size))
        buf = ctypes.create_string_buffer(size.value)
        if not adv.GetTokenInformation(token, klass, buf, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        return buf
    finally:
        ker.CloseHandle(token)


def _sid_text(psid) -> str:
    ctypes, _w, adv, ker = _win()
    text = ctypes.c_wchar_p()
    if not adv.ConvertSidToStringSidW(psid, ctypes.byref(text)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return str(text.value)
    finally:
        ker.LocalFree(ctypes.cast(text, ctypes.c_void_p))


def current_user_sid() -> str:
    """The string SID of the user this process runs as (cached)."""
    global _SID
    if not _SID:
        ctypes = _win()[0]
        buf = _token_info(1)                                     # TokenUser
        _SID = _sid_text(ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0])
    return _SID


_ADMIN_MEMBER: Optional[bool] = None


def _holds_admins() -> bool:
    """Does this token hold BUILTIN\\Administrators (enabled or deny-only)? (cached: a token's groups
    do not change)"""
    global _ADMIN_MEMBER
    if _ADMIN_MEMBER is not None:
        return _ADMIN_MEMBER
    ctypes = _win()[0]
    buf = _token_info(2)                                         # TokenGroups
    count = ctypes.cast(buf, ctypes.POINTER(ctypes.c_uint32))[0]
    ptrs = ctypes.cast(ctypes.addressof(buf) + ctypes.sizeof(ctypes.c_void_p),
                       ctypes.POINTER(ctypes.c_void_p))
    _ADMIN_MEMBER = any(_sid_text(ptrs[2 * i]) == _ADMINS for i in range(count))
    return _ADMIN_MEMBER


def sddl_for(sid: str, directory: bool) -> str:
    """Protected (`P`), one allow entry, full access, that SID; a directory's entry is inherited."""
    return "D:P(A;%s;FA;;;%s)" % ("OICI" if directory else "", sid)


def _with_descriptor(directory: bool, create):
    """Run `create(SECURITY_ATTRIBUTES*)` with this user's one-entry descriptor (handle NOT inheritable)."""
    ctypes, wintypes, adv, ker = _win()

    class SecurityAttributes(ctypes.Structure):
        _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
                    ("bInheritHandle", wintypes.BOOL)]

    sd = ctypes.c_void_p()
    if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl_for(current_user_sid(), directory), 1, ctypes.byref(sd), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        sa = SecurityAttributes(ctypes.sizeof(SecurityAttributes), sd.value, False)
        return create(ctypes.byref(sa))
    finally:
        ker.LocalFree(sd)


def _nt_handle_verdict(h) -> Verdict:
    ctypes, wintypes, adv, ker = _win()
    flags = wintypes.DWORD(0)
    holds = bool(ker.GetVolumeInformationByHandleW(h, None, 0, None, None, ctypes.byref(flags),
                                                   None, 0)) and bool(flags.value
                                                                      & _FILE_PERSISTENT_ACLS)
    if not holds:
        # FAT/exFAT: `CreateFileW` ignored the descriptor; there is no owner to compare - every account
        # is the owner here. Said by doctor, never claimed private.
        return Verdict(False, True, False, NO_ACCESS_CONTROL)
    owner, dacl, sd = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    rc = adv.GetSecurityInfo(h, _SE_FILE_OBJECT, _OWNER_INFO | _DACL_INFO, ctypes.byref(owner),
                             None, ctypes.byref(dacl), None, ctypes.byref(sd))
    if rc != 0:
        return Verdict(False, False, holds, f"its security descriptor cannot be read ({rc})")
    try:
        me = current_user_sid()
        who = _sid_text(owner)
        mine = who == me or (who == _ADMINS and _holds_admins())
        why = []
        if not mine:
            why.append(f"owned by {who}, not this user ({me})")
        if not dacl.value:
            why.append("it has NO access list (everyone may open it)")
        else:
            info = (ctypes.c_uint32 * 3)()                       # AceCount, AclBytesInUse, AclBytesFree
            if not adv.GetAclInformation(dacl, info, ctypes.sizeof(info), 2):
                why.append("its access list cannot be read")
            else:
                aces = []
                for i in range(info[0]):
                    ace = ctypes.c_void_p()
                    if not adv.GetAce(dacl, i, ctypes.byref(ace)):
                        aces.append("?")
                        continue
                    kind = ctypes.cast(ace, ctypes.POINTER(ctypes.c_ubyte))[0]
                    sid = _sid_text(ctypes.c_void_p(ace.value + 8))
                    aces.append(("allow " if kind == 0 else f"type{kind} ") + sid)
                if aces != ["allow " + me]:
                    why.append("its access list is " + (", ".join(aces) or "empty")
                               + f", not one entry for {me}")
        return Verdict(not why, mine, holds, "; ".join(why))
    finally:
        ker.LocalFree(sd)


def _nt_open_meta(path: str):
    ctypes, _w, _adv, ker = _win()
    h = ker.CreateFileW(os.fspath(path), _READ_CONTROL | _FILE_READ_ATTRIBUTES, _SHARE_ALL, None,
                        _OPEN_EXISTING, _BACKUP_SEMANTICS | _OPEN_REPARSE, None)
    if h is None or h == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return h


# =============================================================================== the primitives

def _verify_fd(fd: int, where: Optional[str] = None, dir_fd: Optional[int] = None,
               ask: bool = False) -> Verdict:
    """The verdict of an open fd (the seam a test can force). `where`/`dir_fd`: the directory the file is
    in, where POSIX may ask the volume whether it holds a private mode (`_posix_verdict`)."""
    if _NT:
        import msvcrt
        return _nt_handle_verdict(msvcrt.get_osfhandle(fd))
    return _posix_verdict(fd, where, dir_fd, ask)


def _must_hold(v: Verdict, what: str) -> None:
    """Degrade on a place that cannot hold privacy; refuse loudly on one that can."""
    if v.private or not v.holds:
        return
    raise OSError(errno.EPERM, f"{what} could not be made private: {v.reason} "
                               f"(run `mokata doctor`)")


def _unlink(name: str, dir_fd: Optional[int]) -> None:
    try:
        os.unlink(name, dir_fd=dir_fd) if dir_fd is not None else os.unlink(name)
    except OSError:
        pass


def _has_acl_dir(name: str, dir_fd: Optional[int]) -> bool:
    """macOS: does the directory `name` will be made in carry any ACL entry?"""
    if dir_fd is not None:
        return bool(_mac_acl_entries(dir_fd))
    parent = os.path.dirname(name) or "."
    pfd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        return bool(_mac_acl_entries(pfd))
    finally:
        os.close(pfd)


_FLAGS = (os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
          | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0))


def _staged_create(name: str, dir_fd: Optional[int]) -> int:
    """macOS under an inheritable ACE: born in an empty stage dir stripped of its ACL, linked into place
    (`link` refuses an existing name, as `O_EXCL` does), the stage removed."""
    import secrets
    stage = f".mkstage-{secrets.token_hex(8)}"
    stage_path = stage if dir_fd is not None else os.path.join(os.path.dirname(name) or ".", stage)
    base = os.path.basename(name)
    if dir_fd is not None:
        os.mkdir(stage, 0o700, dir_fd=dir_fd)
    else:
        os.mkdir(stage_path, 0o700)
    try:
        sfd = os.open(stage, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd) \
            if dir_fd is not None else os.open(stage_path, os.O_RDONLY | os.O_DIRECTORY
                                               | os.O_NOFOLLOW)
        try:
            _mac_strip(sfd)
            fd = os.open(base, _FLAGS, 0o600, dir_fd=sfd)
            try:
                if dir_fd is not None:
                    os.link(base, name, src_dir_fd=sfd, dst_dir_fd=dir_fd, follow_symlinks=False)
                else:
                    os.link(base, name, src_dir_fd=sfd, follow_symlinks=False)
            except BaseException:
                os.close(fd)
                raise
            finally:
                _unlink(base, sfd)
            return fd
        finally:
            os.close(sfd)
    finally:
        try:
            os.rmdir(stage, dir_fd=dir_fd) if dir_fd is not None else os.rmdir(stage_path)
        except OSError:
            pass                                 # the file is in place; a stray empty stage is harmless


def create_private_file(name: str, *, dir_fd: Optional[int] = None) -> int:
    """Create `name` NEW (exists, or a link there: `OSError`), readable and writable by this user ONLY from
    its first instant, verified on the handle; returns a read-write binary fd children do not inherit.
    `dir_fd`: create relative to a held directory (POSIX; Windows refuses one rather than ignore it). On a
    place that cannot hold privacy the file is made plainly (doctor names it)."""
    if _NT:
        if dir_fd is not None:
            raise ValueError("Windows has no dir_fd: pass the full path")

        def create(sa):
            ctypes, _w, _adv, ker = _win()
            h = ker.CreateFileW(os.fspath(name), _GENERIC_READ | _GENERIC_WRITE, _SHARE_ALL, sa,
                                _CREATE_NEW, _FILE_ATTRIBUTE_NORMAL, None)
            if h is None or h == ctypes.c_void_p(-1).value:
                err = ctypes.get_last_error()
                if err in (80, 183):                         # ERROR_FILE_EXISTS / ALREADY_EXISTS
                    raise FileExistsError(errno.EEXIST, "File exists", name)
                raise ctypes.WinError(err)
            import msvcrt
            try:
                return msvcrt.open_osfhandle(h, 0)
            except BaseException:
                ker.CloseHandle(h)
                raise
        fd = _with_descriptor(False, create)
    elif _MAC and _has_acl_dir(name, dir_fd):
        fd = _staged_create(name, dir_fd)
    else:
        fd = os.open(name, _FLAGS, 0o600, dir_fd=dir_fd)
    try:
        _must_hold(_verify_fd(fd, None if dir_fd is not None else (os.path.dirname(name) or "."),
                              dir_fd), name)
    except BaseException:
        os.close(fd)                                         # close first: Windows cannot unlink it open
        _unlink(name, dir_fd)
        raise
    return fd


def make_private_dir(name: str, *, dir_fd: Optional[int] = None) -> None:
    """Create the directory `name` NEW, usable by this user ONLY from its first instant; on Windows its
    one entry is inherited by everything made in it later. macOS: an ACE inherited from the parent is
    stripped while the directory is still EMPTY. Verified; degrades like `create_private_file`."""
    if _NT:
        if dir_fd is not None:
            raise ValueError("Windows has no dir_fd: pass the full path")

        def create(sa):
            ctypes, _w, _adv, ker = _win()
            if not ker.CreateDirectoryW(os.fspath(name), sa):
                err = ctypes.get_last_error()
                if err == 183:
                    raise FileExistsError(errno.EEXIST, "File exists", name)
                raise ctypes.WinError(err)
        _with_descriptor(True, create)
        try:
            _must_hold(verify_private(name), name)
        except BaseException:
            try:
                os.rmdir(name)
            except OSError:
                pass
            raise
        return
    if dir_fd is not None:
        os.mkdir(name, 0o700, dir_fd=dir_fd)
    else:
        os.mkdir(name, 0o700)
    try:
        dfd = os.open(name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                      dir_fd=dir_fd)
        try:
            if _MAC and _mac_acl_entries(dfd):
                _mac_strip(dfd)
            _must_hold(_verify_fd(dfd, dir_fd=dfd), name)
        finally:
            os.close(dfd)
    except BaseException:
        try:
            os.rmdir(name, dir_fd=dir_fd) if dir_fd is not None else os.rmdir(name)
        except OSError:
            pass
        raise


def verify_private(target: Union[int, str]) -> Verdict:
    """The verdict of an open fd or of a path (opened without following a link, without blocking).
    A path that cannot be opened by this user is not ours. A verdict on a PATH is about that instant
    only: a caller that will act on the file's bytes uses `read_record`, which verifies and reads ONE
    handle."""
    if isinstance(target, int):
        return _verify_fd(target)
    if _NT:
        try:
            h = _nt_open_meta(target)
        except OSError as exc:
            return Verdict(False, False, True, f"cannot be opened by this user ({exc})")
        try:
            return _nt_handle_verdict(h)
        finally:
            _win()[3].CloseHandle(h)
    try:
        fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
                     | getattr(os, "O_CLOEXEC", 0))
    except OSError as exc:
        return Verdict(False, False, True, f"cannot be opened by this user ({exc.strerror})")
    try:
        return _posix_verdict(fd, os.path.dirname(target) or ".")
    finally:
        os.close(fd)


def read_record(path: str, limit: int = 1 << 20,
                ask: bool = False) -> Optional[Tuple[Verdict, Optional[bytes]]]:
    """`(verdict, bytes)` of a private-set file from ONE open: no link followed, no blocking on a FIFO, a
    regular file only, and the verdict and the bytes taken from the SAME handle — so nothing swapped in
    between can be judged by one file and read from another. None when there is no regular file this user
    can open there; bytes None when it is larger than `limit`. `ask`: let POSIX ask the volume whether it
    holds a private mode even for a file that looks private (doctor). Never writes the file."""
    if _NT:
        try:
            st = os.lstat(path)
        except OSError:
            return None
        if stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & 0x400:
            return None
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    else:
        flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
                 | getattr(os, "O_CLOEXEC", 0))
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        v = _verify_fd(fd, os.path.dirname(path) or ".", None, ask)
        chunks, got = [], 0
        while got <= limit:
            b = os.read(fd, min(1 << 16, limit + 1 - got))
            if not b:
                break
            chunks.append(b)
            got += len(b)
        return v, (b"".join(chunks) if got <= limit else None)
    except OSError:
        return None
    finally:
        os.close(fd)


def read_private(path: str, limit: int = 1 << 20) -> Optional[bytes]:
    """The bytes of a private-set file IF it may be trusted (`Verdict.accept`), else None (`read_record`:
    one open, the SAME handle verified and read). Never writes anything."""
    got = read_record(path, limit)
    if got is None or not got[0].accept:
        return None
    return got[1]


def acl_sddl(path: str) -> str:
    """Windows: the DACL of `path` as SDDL (the DACL only — never the owner); "" elsewhere or unreadable."""
    if not _NT:
        return ""
    ctypes, wintypes, adv, ker = _win()
    try:
        h = _nt_open_meta(path)
    except OSError:
        return ""
    try:
        dacl, sd = ctypes.c_void_p(), ctypes.c_void_p()
        if adv.GetSecurityInfo(h, _SE_FILE_OBJECT, _DACL_INFO, None, None, ctypes.byref(dacl), None,
                               ctypes.byref(sd)) != 0:
            return ""
        try:
            text = ctypes.c_wchar_p()
            if not adv.ConvertSecurityDescriptorToStringSecurityDescriptorW(sd, 1, _DACL_INFO,
                                                                            ctypes.byref(text), None):
                return ""
            try:
                return str(text.value)
            finally:
                ker.LocalFree(ctypes.cast(text, ctypes.c_void_p))
        finally:
            ker.LocalFree(sd)
    finally:
        ker.CloseHandle(h)


__all__ = ["NO_ACCESS_CONTROL", "Verdict", "acl_sddl", "create_private_file", "current_user_sid",
           "make_private_dir", "read_private", "sddl_for", "verify_private"]
