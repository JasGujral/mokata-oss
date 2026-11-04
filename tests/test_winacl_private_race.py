"""0.0.27 CI-FIX Lows C, N-M1: no other account can open a private file at ANY instant of its write.

Windows checks access when a handle is OPENED, not when it is used. A private temp file created
under its folder's inherited ACL and protected a moment later is therefore still readable through a
handle some other account opened in between (measured on a runner: 105 of 3000 writes leaked the
token). So a private file must be CREATED owner-only. This test is the property itself, not a
proxy for it: a watcher thread impersonates a RESTRICTED copy of this process's token in which the
user's own SID and BUILTIN\\Administrators are deny-only, so it can open a file only through an
entry for some other principal (here BUILTIN\\Users, which a folder on a default `D:\\` grants):
exactly what another standard local account could open. It opens every new temp file it sees,
while the main thread makes thousands of private writes. It must open NONE.

The oracle is calibrated first: it must be able to open an ordinary file in the same folder and be
refused on an owner-only one, or a green result would prove nothing.

Windows only: elsewhere there is no ACL (POSIX gets `0600` at `open`, atomically).
Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

import _support  # noqa: F401

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

WRITES = 3000
_BUILTIN_USERS = "S-1-5-32-545"
_BUILTIN_ADMINS = "S-1-5-32-544"


@unittest.skipUnless(os.name == "nt", "the race is a Windows ACL race; POSIX creates the file 0600")
class NoOtherAccountCanOpenAPrivateTempFile(unittest.TestCase):

    def test_a_restricted_identity_opens_none_of_three_thousand_private_writes(self):
        import ctypes
        import msvcrt
        from ctypes import wintypes
        from mokata import winacl
        from mokata.atomicfile import atomic_write_text

        ker = ctypes.WinDLL("kernel32", use_last_error=True)
        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        vp = ctypes.c_void_p
        ker.CreateFileW.argtypes = [ctypes.c_wchar_p, wintypes.DWORD, wintypes.DWORD, vp,
                                    wintypes.DWORD, wintypes.DWORD, vp]
        ker.CreateFileW.restype = vp
        ker.GetCurrentProcess.restype = vp
        ker.CloseHandle.argtypes = [vp]
        adv.OpenProcessToken.argtypes = [vp, wintypes.DWORD, ctypes.POINTER(vp)]
        adv.ConvertStringSidToSidW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(vp)]

        class SidAndAttributes(ctypes.Structure):
            _fields_ = [("Sid", vp), ("Attributes", wintypes.DWORD)]
        adv.CreateRestrictedToken.argtypes = [vp, wintypes.DWORD, wintypes.DWORD,
                                              ctypes.POINTER(SidAndAttributes), wintypes.DWORD, vp,
                                              wintypes.DWORD, vp, ctypes.POINTER(vp)]
        adv.ImpersonateLoggedOnUser.argtypes = [vp]
        invalid = ctypes.c_void_p(-1).value

        token = vp()
        access = 0x0002 | 0x0008 | 0x0004 | 0x0001     # DUPLICATE | QUERY | IMPERSONATE | ASSIGN_PRIMARY
        self.assertTrue(adv.OpenProcessToken(ker.GetCurrentProcess(), access, ctypes.byref(token)))
        deny = (SidAndAttributes * 2)()
        for i, text in enumerate((winacl.current_user_sid(), _BUILTIN_ADMINS)):
            psid = vp()
            self.assertTrue(adv.ConvertStringSidToSidW(text, ctypes.byref(psid)))
            deny[i].Sid = psid
        restricted = vp()
        self.assertTrue(adv.CreateRestrictedToken(token, 0, 2, deny, 0, None, 0, None,
                                                  ctypes.byref(restricted)),
                        "no restricted token: the oracle cannot be built")

        # A folder OUTSIDE the profile that admits BUILTIN\Users: what a repo on a default D:\ gets.
        folder = tempfile.mkdtemp(prefix="mk-race-", dir=os.path.dirname(os.path.abspath(__file__)))
        self.addCleanup(shutil.rmtree, folder, True)
        icacls = os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "System32", "icacls.exe")
        subprocess.run([icacls, folder, "/grant", "*%s:(OI)(CI)(RX)" % _BUILTIN_USERS],
                       capture_output=True, check=True)
        plain = os.path.join(folder, "plain.txt")
        with open(plain, "w", encoding="utf-8") as fh:
            fh.write("p")
        private = os.path.join(folder, "private.txt")
        atomic_write_text(private, "o", private=True)

        def open_read(path):
            h = ker.CreateFileW(path, 0x80000000, 7, None, 3, 0, None)    # read; share all; existing
            if not h or h == invalid:
                return None, ctypes.get_last_error()
            return h, 0

        stop, held, calibration = threading.Event(), {}, {}

        def watch():
            if not adv.ImpersonateLoggedOnUser(restricted):
                calibration["impersonation"] = ctypes.get_last_error()
                return
            try:
                for label, path in (("plain", plain), ("private", private)):
                    h, err = open_read(path)
                    calibration[label] = "opened" if h else "refused %d" % err
                    if h:
                        ker.CloseHandle(vp(h))
                calibration["ready"] = True
                while not stop.is_set():
                    try:
                        names = os.listdir(folder)
                    except OSError:
                        continue
                    for name in names:
                        if name in held or not name.startswith(".tmp-"):
                            continue
                        h, _err = open_read(os.path.join(folder, name))
                        if h:
                            held[name] = msvcrt.open_osfhandle(h, os.O_RDONLY)
            finally:
                adv.RevertToSelf()

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        deadline = time.time() + 30.0
        while not calibration.get("ready") and "impersonation" not in calibration \
                and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual({"plain": "opened", "private": "refused 5", "ready": True}, calibration,
                         "the oracle is not live: a restricted identity must open an ordinary file "
                         "here and be refused on an owner-only one")

        for i in range(WRITES):
            atomic_write_text(os.path.join(folder, "secret%d.json" % i), "TOKEN-%06d" % i,
                              private=True)
        time.sleep(0.5)
        stop.set()
        watcher.join(30.0)
        leaked = []
        for name, fd in held.items():
            os.lseek(fd, 0, 0)
            data = os.read(fd, 100)
            if data.startswith(b"TOKEN-"):
                leaked.append((name, data))
            os.close(fd)
        self.assertEqual([], sorted(held), "another account opened a private temp file while it "
                         "was being written (%d of %d writes); it read the secret through %d of "
                         "those handles, e.g. %r" % (len(held), WRITES, len(leaked), leaked[:1]))


if __name__ == "__main__":
    unittest.main()
