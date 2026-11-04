"""Owner-only on Windows — files and folders BORN owner-only (0.0.27 CI-FIX Lows C).

POSIX gets owner-only from the mode bits (`0o600` file, `0o700` directory). Windows ignores both: a
file made there inherits the ACL of its folder, and outside the user's profile (a repo on `D:\\`)
that folder grants `BUILTIN\\Users` read. So a file whose docstring says "owner-only" was readable
by every local account.

Setting the ACL AFTER the file exists is not enough: Windows checks access when a handle is OPENED,
so an account that opens the new file in the window before the ACL lands keeps a handle that reads
the secret later (measured: 105 of 3000 writes). So the file is CREATED with its descriptor:
`create_private_file(path)` is `CreateFileW(CREATE_NEW, <protected DACL: this user only>)`, and
`make_private_dir(path)` is `CreateDirectoryW` with the same. The descriptor is protected (no
inheritance from the parent) and holds exactly one entry, full control for the current user; a
directory's entry is inherited by what is made in it afterwards. On every other OS the same two
calls make a `0o600` file (exclusively) and a `0o700` directory.

Stdlib `ctypes` only (advapi32 / kernel32), no new dependency. A volume with no ACLs (FAT, exFAT)
answers ERROR_NOT_SUPPORTED: there is nothing to protect there, so the file is made plainly; every
other failure raises `OSError` — owner-only is a promise, and a promise that could not be kept is
not quietly dropped.

Not used by the hook spool: a hook may import the standard library alone and pays no `ctypes`
(`test_o3_hook_emitter`). The spool must live under the user profile (`~/.mokata/hook-spool`), whose
inherited ACL admits this user, the administrators and SYSTEM, and no other standard account; a
spool outside the profile is refused on Windows (the hook then runs inline). Stdlib-only; imports nothing from mokata.
Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import sys
from typing import Optional

_ERROR_NOT_SUPPORTED = 50
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_SHARE_ALL = 0x7                       # read | write | delete: the same sharing `os.open` gives
_CREATE_NEW = 1                        # fails if the name exists (the O_EXCL of CreateFileW)
_FILE_ATTRIBUTE_NORMAL = 0x80
_TOKEN_QUERY = 0x8
_TOKEN_USER = 1
_SDDL_REVISION_1 = 1

_SID: Optional[str] = None


def sddl_for(sid: str, directory: bool) -> str:
    """The security descriptor text: protected (`P`), one allow entry, full access, that SID. A
    directory's entry carries object+container inherit so its children get the same one."""
    return "D:P(A;%s;FA;;;%s)" % ("OICI" if directory else "", sid)


def _win():
    import ctypes
    from ctypes import wintypes
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    ker = ctypes.WinDLL("kernel32", use_last_error=True)
    vp = ctypes.c_void_p
    ker.GetCurrentProcess.restype = vp
    ker.LocalFree.argtypes = [vp]
    ker.LocalFree.restype = vp
    adv.OpenProcessToken.argtypes = [vp, wintypes.DWORD, ctypes.POINTER(vp)]
    adv.GetTokenInformation.argtypes = [vp, ctypes.c_int, vp, wintypes.DWORD,
                                        ctypes.POINTER(wintypes.DWORD)]
    adv.ConvertSidToStringSidW.argtypes = [vp, ctypes.POINTER(ctypes.c_wchar_p)]
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        ctypes.c_wchar_p, wintypes.DWORD, ctypes.POINTER(vp), vp]
    ker.CreateFileW.argtypes = [ctypes.c_wchar_p, wintypes.DWORD, wintypes.DWORD, vp,
                                wintypes.DWORD, wintypes.DWORD, vp]
    ker.CreateFileW.restype = vp
    ker.CreateDirectoryW.argtypes = [ctypes.c_wchar_p, vp]
    ker.CreateDirectoryW.restype = wintypes.BOOL
    ker.CloseHandle.argtypes = [vp]
    return ctypes, wintypes, adv, ker


def current_user_sid() -> str:
    """The string SID of the user this process runs as (cached)."""
    global _SID
    if _SID:
        return _SID
    ctypes, wintypes, adv, ker = _win()
    token = ctypes.c_void_p()
    if not adv.OpenProcessToken(ker.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = wintypes.DWORD(0)
        adv.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(size))
        buf = ctypes.create_string_buffer(size.value)
        if not adv.GetTokenInformation(token, _TOKEN_USER, buf, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]     # TOKEN_USER.User.Sid
        text = ctypes.c_wchar_p()
        if not adv.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            _SID = str(text.value)
        finally:
            ker.LocalFree(ctypes.cast(text, ctypes.c_void_p))
    finally:
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(token.value))
    return _SID


def _plain_flags() -> int:
    return (os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOINHERIT", 0))


def _born_with_descriptor(directory: bool, create):
    """Run `create(ctypes, kernel32, SECURITY_ATTRIBUTES*)` with this user's owner-only descriptor."""
    ctypes, wintypes, adv, ker = _win()

    class SecurityAttributes(ctypes.Structure):
        _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
                    ("bInheritHandle", wintypes.BOOL)]

    sd = ctypes.c_void_p()
    if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl_for(current_user_sid(), directory), _SDDL_REVISION_1, ctypes.byref(sd), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        sa = SecurityAttributes(ctypes.sizeof(SecurityAttributes), sd.value, False)  # not inherited
        return create(ctypes, ker, ctypes.byref(sa))
    finally:
        ker.LocalFree(sd)


def create_private_file(path: str) -> int:
    """Create `path` NEW (it must not exist: `FileExistsError`), readable and writable by the current
    user ONLY from its first instant, and return an open read-write binary fd that children do not
    inherit. Nobody else can ever hold a handle to it, because nobody else could open it."""
    if sys.platform != "win32":
        return os.open(path, _plain_flags(), 0o600)

    def create(ctypes, ker, sa):
        h = ker.CreateFileW(os.fspath(path), _GENERIC_READ | _GENERIC_WRITE, _SHARE_ALL, sa,
                            _CREATE_NEW, _FILE_ATTRIBUTE_NORMAL, None)
        if h is None or h == ctypes.c_void_p(-1).value:
            err = ctypes.get_last_error()
            if err == _ERROR_NOT_SUPPORTED:
                return None                       # a volume with no ACLs: nothing to protect
            raise ctypes.WinError(err)
        import msvcrt
        try:
            return msvcrt.open_osfhandle(h, 0)
        except BaseException:
            ker.CloseHandle(h)
            raise
    fd = _born_with_descriptor(False, create)
    return os.open(path, _plain_flags(), 0o600) if fd is None else fd


def make_private_dir(path: str) -> None:
    """Create the directory `path` NEW (`FileExistsError` if it exists), usable by the current user
    ONLY from its first instant, and inherited that way by everything made in it afterwards."""
    if sys.platform != "win32":
        os.mkdir(path, 0o700)
        return

    def create(ctypes, ker, sa):
        if ker.CreateDirectoryW(os.fspath(path), sa):
            return True
        err = ctypes.get_last_error()
        if err == _ERROR_NOT_SUPPORTED:
            return False
        raise ctypes.WinError(err)
    if not _born_with_descriptor(True, create):
        os.mkdir(path, 0o700)
