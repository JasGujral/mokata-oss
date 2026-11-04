"""Owner-only on Windows — the one helper behind every "owner-only" claim (0.0.27 CI-FIX Lows C).

POSIX gets owner-only from the mode bits (`0o600` file, `0o700` directory). Windows ignores both: a
file made there inherits the ACL of its folder, and outside the user's profile (a repo on `D:\\`)
that folder grants `BUILTIN\\Users` read. So a file whose docstring says "owner-only" was readable
by every local account. `owner_only(path)` makes it true: it replaces the path's DACL with a
PROTECTED one (no inheritance from the parent) holding exactly one entry, full control for the
current user. A directory's entry is inherited by what is made in it afterwards.

No-op on every other OS. Stdlib `ctypes` only (advapi32 / kernel32), no new dependency. A volume
with no ACLs (FAT, exFAT) answers ERROR_NOT_SUPPORTED: there is nothing to protect there, so that
one answer is not an error; every other failure raises `OSError` — owner-only is a promise, and a
promise that could not be kept is not quietly dropped.

Not used by the hook spool: a hook may import the standard library alone and pays no `ctypes`
(`test_o3_hook_emitter`), and the spool lives under the user profile (`~/.mokata/hook-spool`),
whose inherited ACL is already owner-only. Stdlib-only; imports nothing from mokata.
Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
from typing import Optional

_ERROR_NOT_SUPPORTED = 50
_SE_FILE_OBJECT = 1
_DACL_SECURITY_INFORMATION = 0x4
_PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
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
    adv.GetSecurityDescriptorDacl.argtypes = [vp, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(vp),
                                              ctypes.POINTER(wintypes.BOOL)]
    adv.SetNamedSecurityInfoW.argtypes = [ctypes.c_wchar_p, ctypes.c_int, wintypes.DWORD, vp, vp,
                                          vp, vp]
    adv.SetNamedSecurityInfoW.restype = wintypes.DWORD
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


def owner_only(path: str) -> None:
    """Make `path` (a file or a directory) readable and writable by the current user ONLY."""
    if os.name != "nt":
        return
    ctypes, wintypes, adv, ker = _win()
    sd = ctypes.c_void_p()
    text = sddl_for(current_user_sid(), os.path.isdir(path))
    if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            text, _SDDL_REVISION_1, ctypes.byref(sd), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        present, defaulted, dacl = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
        if not adv.GetSecurityDescriptorDacl(sd, ctypes.byref(present), ctypes.byref(dacl),
                                             ctypes.byref(defaulted)):
            raise ctypes.WinError(ctypes.get_last_error())
        rc = adv.SetNamedSecurityInfoW(
            os.fspath(path), _SE_FILE_OBJECT,
            _DACL_SECURITY_INFORMATION | _PROTECTED_DACL_SECURITY_INFORMATION,
            None, None, dacl, None)
    finally:
        ker.LocalFree(sd)
    if rc == _ERROR_NOT_SUPPORTED:
        return                                  # a volume with no ACLs: nothing to protect
    if rc != 0:
        raise OSError(rc, "could not make %s owner-only (Windows error %d)" % (path, rc), path)
