"""0.0.28 lane C1 — `WIN-OWNER-ONLY-FILES`: a token, a trust decision, a server or bridge record, a spill
or an explainer-reviews file is readable by the user who made it and NOBODY else, from the first instant it
exists, on every OS. Where the disk cannot hold that promise mokata says so in `mokata doctor` and never
claims it. A file planted at a record path is never trusted.

Business rules (note C §3a, the rule page `0028-evidence/C/C.rule.md`):
  * every member of the private set is BORN private (the descriptor is part of the create call) and is
    verified on its open handle before any content is written; nothing is created and then restricted;
  * a volume that cannot hold privacy degrades plainly: the write succeeds, `doctor` names it;
  * readers accept a record only when it is ours and still private (or the volume cannot hold it);
  * `doctor --fix` re-protects OUR weak file keeping its bytes (the gate keeps authenticating), and never
    touches a file someone else owns;
  * nothing outside the set becomes private, and no doc calls anything outside it owner-only.

"Private" is judged by `_privacy_oracle` (os.stat / `ls -le` / `icacls`), never by `winacl` itself.
Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import ast
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

import _privacy_oracle as O

SRC = Path(__file__).resolve().parents[1] / "src"
DOCS = Path(__file__).resolve().parents[1] / "docs"
SENTINEL = "SENTINELtokenC1xyzzy0123456789abcdefABCDEF"


def _tool(name: str) -> str:
    """A spawned program by its resolved path (never a bare name: `_windows_portability`)."""
    return shutil.which(name) or name



def _silent(*_a, **_k):
    pass


class _Home(unittest.TestCase):
    """A repo and a HOME, both fresh; the user-level IDE state (`~/.mokata/ide-repos`) lands in HOME."""

    governed = True

    def setUp(self):
        self._t = tempfile.mkdtemp(prefix="mk-c1-")
        self.addCleanup(shutil.rmtree, self._t, True)
        base = os.path.realpath(self._t)
        self.root = os.path.join(base, "repo")
        self.home = os.path.join(base, "home")
        # macOS: the whole tree sits under an inheritable `everyone allow read` entry (what File
        # Sharing / MDM leave), so a plain 0600 create is NOT private here and a writer that skips the
        # door is caught (on POSIX the plain temp is already 0600: without this, a dropped door passes).
        # Given BEFORE any subdirectory exists: inheritance is applied at creation, never after.
        O.give_dir_inheritable_acl(base)
        os.makedirs(self.root)
        os.makedirs(self.home)
        if self.governed:
            os.makedirs(os.path.join(self.root, ".mokata", "temp_local"))
        env = mock.patch.dict(os.environ, {"HOME": self.home, "USERPROFILE": self.home})
        env.start()
        self.addCleanup(env.stop)
        from mokata import winacl
        learnt = vars(winacl).get("_HOLDS", {})   # what a volume does is learnt per process: start clean
        learnt.clear()
        self.addCleanup(learnt.clear)

    def assertPrivate(self, path, what=""):
        why = O.why_not_private(path)
        self.assertEqual("", why, f"{what or path} is not private: {why}")

    def assertNotPrivate(self, path, what=""):
        self.assertNotEqual("", O.why_not_private(path), f"{what or path} must keep the folder's "
                            "access (it is not in the private set)")


# =============================================================== the set is born private

class ThePrivateSet(_Home):
    """Each member of the set, made by its REAL writer, is private by the oracle."""

    def test_the_server_record_is_private(self):
        from mokata.ide import server as S
        S._write_record(self.root, {"port": 1234, "pid": os.getpid(), "token": SENTINEL,
                                    "root": self.root})
        self.assertPrivate(S.record_path(self.root), "server.json")
        self.assertEqual(SENTINEL, S.read_record(self.root)["token"])

    def test_the_bridge_record_is_private(self):
        from mokata.ide import bus as B
        rec = B.ensure_bridge(self.root)
        self.assertPrivate(B.bridge_path(self.root), "bridge.json")
        self.assertEqual(rec, B.read_bridge(self.root))

    def test_the_trust_record_is_private(self):
        from mokata.ide import trust as T
        T.decide(self.root, True, "cli")
        self.assertPrivate(T.record_path(self.root), "trust.json")
        self.assertTrue(T.status(self.root)["trusted"])

    def test_the_spill_file_and_its_directory_are_private(self):
        from mokata.ide import usages as U
        d = U.spill_dir(self.root)
        w = U.SpillWriter(d, "usages")
        w.row("k", ["a", 1])
        w.close()
        self.assertPrivate(d, "the spill directory")
        self.assertPrivate(w.path, "a spill file")
        w.discard()

    def test_a_reviews_store_file_is_private_below_reviews_only(self):
        from mokata.explain import store as ST
        with ST._reviews(self.root, "sid1") as d:
            d.write("page.json", b"{}")
        reviews = os.path.join(self.root, ".mokata", "temp_local", "reviews")
        self.assertPrivate(os.path.join(reviews, "sid1"), "reviews/<sid>")
        self.assertPrivate(os.path.join(reviews, "sid1", "page.json"), "a reviews page")

    def test_ten_thousand_private_writes_leak_none(self):
        """Scale: 10k private writes into ONE directory (on macOS, one whose parent carries an
        inheritable allow ACE), judged by one `ls -le` and one stat each: zero leaks."""
        from mokata.atomicfile import atomic_write_text
        d = os.path.join(self.root, "many")
        os.makedirs(d)
        O.give_dir_inheritable_acl(d)
        for i in range(10000):
            atomic_write_text(os.path.join(d, f"s{i}.json"), f"TOKEN-{i:06d}", private=True)
        names = sorted(os.listdir(d))
        self.assertEqual(10000, len(names), names[:5])
        bad = [n for n in names
               if os.lstat(os.path.join(d, n)).st_mode & 0o077 and os.name != "nt"]
        self.assertEqual([], bad[:5])
        if sys.platform == "darwin":
            self.assertEqual([], O.mac_acl_entries_in(d)[:5])
        if os.name == "nt":
            for n in names[::997]:
                self.assertPrivate(os.path.join(d, n))


@unittest.skipUnless(sys.platform == "darwin", "macOS ACL inheritance (Linux masks it, Windows is "
                     "covered by the DACL cells)")
class MacInheritedAcl(_Home):
    """A 0600 create under a parent with an inheritable allow ACE is still readable by everyone on
    macOS (refute M1); the door stages it so the file is born with NO ACL entry."""

    def test_the_oracle_is_live_a_plain_0600_create_inherits_the_entry(self):
        d = os.path.join(self.root, "shared")
        os.makedirs(d)
        self.assertTrue(O.give_dir_inheritable_acl(d), "chmod +a did not take: no oracle")
        fd = os.open(os.path.join(d, "plain"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        self.assertNotEqual([], O.mac_acl_entries(os.path.join(d, "plain")))

    def test_a_private_file_under_an_inheritable_ace_has_no_entry(self):
        from mokata.atomicfile import atomic_write_text
        d = os.path.join(self.root, "shared")
        os.makedirs(d)
        O.give_dir_inheritable_acl(d)
        p = os.path.join(d, "secret.json")
        atomic_write_text(p, "x", private=True)
        self.assertPrivate(p)
        self.assertEqual([".", "secret.json"][1:], [n for n in os.listdir(d)])  # no stage left

    def test_a_private_directory_under_an_inheritable_ace_has_no_entry(self):
        from mokata import winacl
        d = os.path.join(self.root, "shared")
        os.makedirs(d)
        O.give_dir_inheritable_acl(d)
        winacl.make_private_dir(os.path.join(d, "spill"))
        self.assertPrivate(os.path.join(d, "spill"))
        fd = winacl.create_private_file(os.path.join(d, "spill", "f"))
        os.close(fd)
        self.assertPrivate(os.path.join(d, "spill", "f"))


class PrivateDir(_Home):
    """A private directory is born owner-only and what is made in it later is too (1M spill rows,
    the directory made once)."""

    def test_a_million_spill_rows_in_one_private_file_dir_made_once(self):
        from mokata import winacl
        from mokata.ide import usages as U
        d = U.spill_dir(self.root)
        made = []
        real = winacl.make_private_dir
        with mock.patch.object(winacl, "make_private_dir",
                               side_effect=lambda *a, **k: (made.append(a), real(*a, **k))[1]):
            w = U.SpillWriter(d, "usages")
            for i in range(1_000_000):
                w.row(i, [i])
            w.close()
            w2 = U.SpillWriter(d, "usages")
            w2.close()
        self.assertEqual(1, len(made), made)
        self.assertPrivate(d)
        self.assertPrivate(w.path)
        self.assertPrivate(w2.path)
        if os.name == "nt":
            child = os.path.join(d, "later.txt")
            with open(child, "w", encoding="utf-8") as fh:
                fh.write("x")
            aces = O._icacls(child)
            self.assertEqual(1, len(aces), aces)          # inherited from the private dir, one entry
        w.discard()
        w2.discard()


# =============================================================== no window

class NoWindowPosix(_Home):
    """POSIX: a second uid is not available here, so the property is asserted at its source - the
    create call itself carries `O_EXCL|O_NOFOLLOW` and mode 0600, and the fd reads back private."""

    @unittest.skipIf(os.name == "nt", "the Windows cell is TheRaceWindows")
    def test_the_create_call_carries_the_descriptor(self):
        from mokata.atomicfile import atomic_write_text
        seen = []
        real = os.open

        def spy(path, flags, mode=0o777, *a, **k):
            if flags & os.O_CREAT:
                seen.append((os.fspath(path), flags, mode))
            return real(path, flags, mode, *a, **k)
        p = os.path.join(self.root, "secret.json")
        with mock.patch("os.open", side_effect=spy):
            atomic_write_text(p, "x", private=True)
        creates = [s for s in seen if os.path.basename(s[0]).startswith(".tmp-")]
        self.assertEqual(1, len(creates), seen)
        _path, flags, mode = creates[0]
        self.assertTrue(flags & os.O_EXCL)
        self.assertTrue(flags & os.O_NOFOLLOW)
        self.assertEqual(0o600, mode)
        self.assertPrivate(p)


@unittest.skipUnless(os.name == "nt", "a Windows ACL race (POSIX: NoWindowPosix)")
class TheRaceWindows(unittest.TestCase):
    """THE acceptance gate for §3a. A watcher impersonates a RESTRICTED copy of this process's token
    (the user's SID and BUILTIN\\Administrators deny-only), so it can open a file only through an
    entry for some other principal (BUILTIN\\Users, granted on the folder): exactly what another
    standard local account could open. It opens every new temp while 3000 private writes run. It must
    open NONE. Calibrated first: it opens an ordinary file there and is refused on a private one."""

    WRITES = 3000

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
        access = 0x0002 | 0x0008 | 0x0004 | 0x0001
        self.assertTrue(adv.OpenProcessToken(ker.GetCurrentProcess(), access, ctypes.byref(token)))
        deny = (SidAndAttributes * 2)()
        for i, text in enumerate((winacl.current_user_sid(), "S-1-5-32-544")):
            psid = vp()
            self.assertTrue(adv.ConvertStringSidToSidW(text, ctypes.byref(psid)))
            deny[i].Sid = psid
        restricted = vp()
        self.assertTrue(adv.CreateRestrictedToken(token, 0, 2, deny, 0, None, 0, None,
                                                  ctypes.byref(restricted)))

        folder = tempfile.mkdtemp(prefix="mk-race-", dir=os.path.dirname(os.path.abspath(__file__)))
        self.addCleanup(shutil.rmtree, folder, True)
        icacls = os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "System32", "icacls.exe")
        subprocess.run([icacls, folder, "/grant", "*S-1-5-32-545:(OI)(CI)(RX)"],
                       capture_output=True, check=True)
        plain = os.path.join(folder, "plain.txt")
        with open(plain, "w", encoding="utf-8") as fh:
            fh.write("p")
        private = os.path.join(folder, "private.txt")
        atomic_write_text(private, "o", private=True)

        def open_read(path):
            h = ker.CreateFileW(path, 0x80000000, 7, None, 3, 0, None)
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
        while not calibration.get("ready") and "impersonation" not in calibration \
                and watcher.is_alive():
            time.sleep(0.02)
        self.assertEqual({"plain": "opened", "private": "refused 5", "ready": True}, calibration,
                         "the oracle is not live")
        for i in range(self.WRITES):
            atomic_write_text(os.path.join(folder, "secret%d.json" % i), "TOKEN-%06d" % i,
                              private=True)
        stop.set()
        watcher.join()
        for fd in held.values():
            os.close(fd)
        self.assertEqual([], sorted(held), f"another account opened {len(held)} of {self.WRITES} "
                         "private temp files while they were written")


# =============================================================== born, not set

class BornNotSet(_Home):
    """The create is the first touch (the path is absent at create), and nothing restricts the file
    afterwards: no chmod, no SetNamedSecurityInfo/SetSecurityInfo, no icacls."""

    def test_a_child_process_never_inherits_the_handle(self):
        """A spawned child (a hook, a language server) must not hold the token file open: the
        handle is born non-inheritable (POSIX `O_CLOEXEC`; nt `bInheritHandle` FALSE)."""
        from mokata import winacl
        fd = winacl.create_private_file(os.path.join(self.root, "h.json"))
        try:
            self.assertFalse(os.get_inheritable(fd))
        finally:
            os.close(fd)

    @unittest.skipUnless(os.name == "nt", "ACL propagation to existing files is a Windows property")
    def test_a_later_inheritable_grant_on_an_ancestor_never_reaches_it(self):
        """The DACL is PROTECTED (`D:P`): a share or admin tool granting an inheritable entry on an
        ancestor AFTER the record exists propagates to every unprotected child — never to this one."""
        from mokata.ide import server as S
        S._write_record(self.root, {"port": 1, "pid": os.getpid(), "token": SENTINEL, "root": self.root})
        self.assertPrivate(S.record_path(self.root))
        plain = os.path.join(os.path.dirname(S.record_path(self.root)), "plain.txt")
        with open(plain, "w", encoding="utf-8") as fh:   # the control: exists BEFORE the grant
            fh.write("x")
        icacls = os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "System32", "icacls.exe")
        p = subprocess.run([icacls, self.root, "/grant", "*S-1-1-0:(OI)(CI)(R)"],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(0, p.returncode, p.stdout)
        self.assertIn("Everyone", " ".join(O._icacls(plain)), "the control: the grant DID propagate")
        self.assertPrivate(S.record_path(self.root), "server.json after an ancestor grant")

    def test_nothing_restricts_a_private_file_after_it_exists(self):
        from mokata import atomicfile, winacl
        absent_at_create = []
        real_create = winacl.create_private_file

        def create(name, *a, **k):
            absent_at_create.append(not os.path.lexists(name if k.get("dir_fd") is None
                                                        else name))
            return real_create(name, *a, **k)
        forbidden = []
        with mock.patch.object(winacl, "create_private_file", side_effect=create), \
                mock.patch("os.chmod", side_effect=lambda *a, **k: forbidden.append(("chmod", a))), \
                mock.patch("os.fchmod", side_effect=lambda *a, **k: forbidden.append(("fchmod", a)),
                           create=True), \
                mock.patch("subprocess.run", side_effect=lambda *a, **k: forbidden.append(("run", a))):
            atomicfile.atomic_write_text(os.path.join(self.root, "s.json"), "x", private=True)
        self.assertEqual([True], absent_at_create)
        self.assertEqual([], forbidden)
        src = (SRC / "mokata" / "winacl.py").read_text(encoding="utf-8")
        for name in ("SetNamedSecurityInfo", "SetSecurityInfo", "SetFileSecurity", "icacls",
                     "os.chmod", "fchmod"):
            self.assertNotIn(name, src, f"winacl must never restrict after the fact ({name})")


# =============================================================== nothing left behind

class NothingLeftBehind(_Home):
    """A failure at any step leaves no temp file and no stage dir; the error propagates."""

    def _left(self, d):
        return sorted(n for n in os.listdir(d) if n != "keep")

    def _case(self, patch_target, **kw):
        from mokata.atomicfile import atomic_write_text
        d = os.path.join(self.root, "d")
        os.makedirs(d)
        O.give_dir_inheritable_acl(d)
        with mock.patch(patch_target, side_effect=OSError(28, "No space left on device"), **kw):
            with self.assertRaises(OSError):
                atomic_write_text(os.path.join(d, "s.json"), "x", private=True)
        self.assertEqual([], self._left(d))

    def test_a_failed_write_leaves_nothing(self):
        self._case("os.fsync")

    def test_a_failed_rename_leaves_nothing(self):
        self._case("os.replace")

    def test_a_failed_create_leaves_nothing(self):
        self._case("mokata.winacl._verify_fd")

    def test_a_weak_create_on_a_place_that_holds_privacy_raises_and_leaves_nothing(self):
        """Not a degrade: the volume CAN hold privacy and the new file is not private -> refuse."""
        from mokata import winacl
        from mokata.atomicfile import atomic_write_text
        d = os.path.join(self.root, "d")
        os.makedirs(d)
        weak = winacl.Verdict(False, True, True, "mode 0o644 lets other users in")
        with mock.patch.object(winacl, "_verify_fd", return_value=weak):
            with self.assertRaises(OSError) as got:
                atomic_write_text(os.path.join(d, "s.json"), "x", private=True)
        self.assertIn("could not be made private", str(got.exception))
        self.assertEqual([], self._left(d))


# =============================================================== volume without access control

class NoAclVolume(_Home):
    """A REAL volume that cannot hold privacy: the write succeeds plainly, the reader still works (a
    repo on a USB exFAT stick must start the IDE), and `mokata doctor` says so; nothing claims private."""

    governed = False

    def _mount(self):
        base = os.path.join(self._t, "vol")
        if sys.platform == "darwin":
            img = os.path.join(self._t, "x.dmg")
            subprocess.run([_tool("hdiutil"), "create", "-size", "20m", "-fs", "ExFAT", "-volname", "MKX",
                            img], check=True, capture_output=True)
            os.makedirs(base)
            subprocess.run([_tool("hdiutil"), "attach", "-nobrowse", "-mountpoint", base, img],
                           check=True, capture_output=True)
            self.addCleanup(subprocess.run, [_tool("hdiutil"), "detach", "-force", base],
                            capture_output=True)
            return base
        if sys.platform.startswith("linux"):
            if subprocess.run([_tool("sudo"), "-n", "true"], capture_output=True).returncode != 0:
                self.skipTest("NOT RUN: a loop-mounted vfat needs passwordless sudo")
            img = os.path.join(self._t, "x.img")
            subprocess.run([_tool("truncate"), "-s", "20M", img], check=True)
            subprocess.run([_tool("mkfs.vfat"), img], check=True, capture_output=True)
            os.makedirs(base)
            subprocess.run([_tool("sudo"), "-n", "mount", "-o", f"loop,uid={os.getuid()},umask=022",
                            img, base], check=True, capture_output=True)
            self.addCleanup(subprocess.run, [_tool("sudo"), "-n", "umount", base], capture_output=True)
            return base
        return self._vhd()

    def _vhd(self):
        vhd = os.path.join(os.path.realpath(self._t), "x.vhd")
        letter_dir = None
        for letter in "RSTUVWXYZ":
            if not os.path.exists(letter + ":\\"):
                letter_dir = letter
                break
        if letter_dir is None:
            self.skipTest("NOT RUN: no free drive letter for the FAT VHD")
        script = os.path.join(self._t, "dp.txt")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(f'create vdisk file="{vhd}" maximum=64 type=expandable\nselect vdisk file="{vhd}"\n'
                     f"attach vdisk\ncreate partition primary\nformat fs=fat32 quick\n"
                     f"assign letter={letter_dir}\n")
        p = subprocess.run([_tool("diskpart"), "/s", script], capture_output=True, text=True)
        if p.returncode != 0 or not os.path.exists(letter_dir + ":\\"):
            self.skipTest(f"NOT RUN: diskpart could not make a FAT VHD (admin runner needed): "
                          f"{p.stdout[-300:]}")
        det = os.path.join(self._t, "det.txt")
        with open(det, "w", encoding="utf-8") as fh:
            fh.write(f'select vdisk file="{vhd}"\ndetach vdisk\n')
        self.addCleanup(subprocess.run, [_tool("diskpart"), "/s", det], capture_output=True)
        return letter_dir + ":\\"

    def test_a_fat_volume_degrades_plainly_and_doctor_names_it(self):
        from mokata import winacl
        from mokata.cli_commands.diagnostics import privacy_lines
        from mokata.ide import bus as B
        vol = self._mount()
        root = os.path.join(vol, "repo")
        os.makedirs(os.path.join(root, ".mokata", "temp_local"))
        rec = B.ensure_bridge(root)                          # never raises
        self.assertEqual(rec, B.read_bridge(root), "the IDE must still start on this volume")
        v = winacl.verify_private(B.bridge_path(root))
        self.assertFalse(v.holds, v)
        self.assertFalse(v.private, "nothing may claim privacy the disk does not have")
        text = "\n".join(privacy_lines(root))
        self.assertIn("this volume has no access control: private files are as private as the disk",
                      text)
        self.assertNotIn(f"{B.bridge_path(root)}: private", text)


# =============================================================== only the set

class OnlyThePrivateSet(_Home):
    """An ordinary atomic write never goes through the private door; a session bundle keeps the
    folder's access."""

    def test_an_ordinary_atomic_write_does_not_touch_winacl(self):
        from mokata import atomicfile
        with mock.patch.dict(sys.modules, {"mokata.winacl": None}):
            atomicfile.atomic_write_text(os.path.join(self.root, "plain.json"), "x")
        if os.name == "nt":            # POSIX: the plain door has always been 0600 (atomicfile)
            self.assertNotPrivate(os.path.join(self.root, "plain.json"))

    def test_a_session_bundle_keeps_the_folder_access(self):
        """N-L5, decided: bundles keep the inherited ACL — the real writer never reaches the door."""
        from mokata import session_transport as T
        from mokata import winacl
        calls = []
        real = winacl.create_private_file
        with mock.patch.object(winacl, "create_private_file",
                               side_effect=lambda *a, **k: (calls.append(a), real(*a, **k))[1]):
            path = T._FileTransport(os.path.join(self.root, "bundles")).write_bundle("t1", "{}")
        self.assertEqual([], calls)
        if os.name == "nt":            # the folder's ACL (Windows mkdtemp: SYSTEM/BA/OWNER RIGHTS)
            self.assertNotPrivate(path)


# =============================================================== the value sweep

class TokenFlow(_Home):
    """Derived from the VALUE, not a typed list: with the token generator patched to a sentinel, run
    every real flow that mints or bakes a token, then walk the repo and HOME — every file that holds
    the sentinel must be private."""

    def _setup(self, **kw):
        from mokata import harness_setup as HS
        plan = HS.plan_setup("claude", root=self.root, home=self.home, **kw)
        HS.apply_setup(plan, assume_yes=True, out=_silent)

    def test_every_file_holding_the_token_is_private(self):
        from mokata.ide import server as S
        with mock.patch("secrets.token_urlsafe", return_value=SENTINEL):
            self._setup(http_gates=True, ide_bridge=True)
            S._write_record(self.root, {"port": 1, "pid": os.getpid(), "token": SENTINEL,
                                        "root": self.root})
        holders = []
        for top in (self.root, self.home):
            for dp, _dn, fns in os.walk(top):
                for fn in fns:
                    p = os.path.join(dp, fn)
                    try:
                        with open(p, "rb") as fh:
                            if SENTINEL.encode() in fh.read():
                                holders.append(p)
                    except OSError:
                        continue
        top_parts = len(Path(os.path.realpath(self._t)).parts)
        rel = sorted("/".join(Path(os.path.realpath(p)).parts[top_parts:]) for p in holders)
        self.assertIn("repo/.claude/settings.local.json", rel)
        self.assertGreaterEqual(len(rel), 3, rel)
        for p in holders:
            self.assertPrivate(p)


class FixKeepsToken(_Home):
    """`doctor --fix` re-protects OUR weak files with their exact bytes: the token, the port and the
    trust decision survive, so the baked gate header still equals the bridge token."""

    def test_fix_reprotects_and_the_gate_still_authenticates(self):
        from mokata import harness_setup as HS
        from mokata.cli_commands.diagnostics import privacy_lines
        from mokata.ide import bus as B
        from mokata.ide import trust as T
        plan = HS.plan_setup("claude", root=self.root, home=self.home, http_gates=True)
        HS.apply_setup(plan, assume_yes=True, out=_silent)
        T.decide(self.root, True, "cli")
        rec = B.read_bridge(self.root)
        local = os.path.join(self.root, ".claude", "settings.local.json")
        weak = [B.bridge_path(self.root), local, T.record_path(self.root)]
        before = {p: Path(p).read_bytes() for p in weak}
        for p in weak:
            O.grant_others_read(p)
        self.assertIsNone(B.read_bridge(self.root), "a weak bridge record must be refused")
        self.assertFalse(T.status(self.root)["trusted"], "a weak trust record must be refused")
        text = "\n".join(privacy_lines(self.root))
        for p in weak:
            self.assertIn(f"{p}: WEAK", text)
        text = "\n".join(privacy_lines(self.root, fix=True))
        for p in weak:
            self.assertPrivate(p)
            self.assertEqual(before[p], Path(p).read_bytes(), f"--fix changed the bytes of {p}")
        self.assertEqual(rec, B.read_bridge(self.root))
        self.assertTrue(T.status(self.root)["trusted"])
        gate = json.loads(Path(local).read_text(encoding="utf-8"))
        headers = [h.get("headers", {}) for groups in gate["hooks"].values() for g in groups
                   for h in g["hooks"]]
        self.assertIn({"X-Mokata-Hook": rec["token"]}, headers)

    def test_a_worktree_copy_claude_code_made_is_named_and_fixed(self):
        from mokata import harness_setup as HS
        from mokata.cli_commands.diagnostics import privacy_lines
        plan = HS.plan_setup("claude", root=self.root, home=self.home, http_gates=True)
        HS.apply_setup(plan, assume_yes=True, out=_silent)
        src = os.path.join(self.root, ".claude", "settings.local.json")
        copy = os.path.join(self.root, ".claude", "worktrees", "w1", ".claude",
                            "settings.local.json")
        os.makedirs(os.path.dirname(copy))
        shutil.copyfile(src, copy)                           # what Claude Code's copy does
        O.grant_others_read(copy)
        self.assertIn(f"{copy}: WEAK", "\n".join(privacy_lines(self.root)))
        privacy_lines(self.root, fix=True)
        self.assertPrivate(copy)


# =============================================================== planted records

class Planted(_Home):
    """A record that is weaker than the descriptor (or owned by someone else) is refused by every
    reader, replaced by `ensure_bridge`, and never adopted by `--fix`."""

    def _plant(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        O.grant_others_read(path)

    def test_a_weak_server_record_is_refused(self):
        from mokata.ide import bridge_client as BC
        from mokata.ide import server as S
        self._plant(S.record_path(self.root), {"port": 4444, "pid": os.getpid(), "token": "evil",
                                               "root": self.root})
        self.assertIsNone(S.read_record(self.root))
        self.assertIsNone(BC.live_ide(self.root))

    def test_a_weak_bridge_record_is_refused_and_replaced(self):
        from mokata.ide import bridge_client as BC
        from mokata.ide import bus as B
        self._plant(B.bridge_path(self.root), {"port": 24444, "token": "evil"})
        self.assertIsNone(B.read_bridge(self.root))
        self.assertEqual("", BC._hook_token(self.root))
        rec = B.ensure_bridge(self.root)
        self.assertNotEqual("evil", rec["token"])
        self.assertPrivate(B.bridge_path(self.root))

    def test_a_weak_trust_record_is_not_trust(self):
        from mokata.ide import trust as T
        self._plant(T.record_path(self.root), {"trusted": True, "root": T._real(self.root),
                                               "at": "x", "by": "evil"})
        self.assertFalse(T.status(self.root)["trusted"])

    def test_a_fifo_at_the_record_path_does_not_hang_the_reader(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("no FIFOs on this OS")
        from mokata.ide import server as S
        os.makedirs(os.path.dirname(S.record_path(self.root)), exist_ok=True)
        fifo = S.record_path(self.root)
        os.mkfifo(fifo, 0o600)
        got = []
        reader = threading.Thread(target=lambda: got.append(S.read_record(self.root)), daemon=True)
        reader.start()
        reader.join(120)                 # generous: a non-blocking open answers in microseconds
        hung = reader.is_alive()
        if hung:                         # release the blocked reader so the suite goes on (and FAILS)
            fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
            os.close(fd)
            reader.join(120)
        self.assertFalse(hung, "the reader BLOCKED opening a FIFO at the record path")
        self.assertEqual([None], got)

    @unittest.skipUnless(sys.platform == "darwin", "a macOS ACL entry on the file itself")
    def test_a_record_with_an_acl_entry_is_refused(self):
        from mokata.ide import bus as B
        rec = B.ensure_bridge(self.root)
        subprocess.run(["/bin/chmod", "+a", "group:everyone allow read", B.bridge_path(self.root)],
                       check=True, capture_output=True)
        self.assertEqual(0o600, os.stat(B.bridge_path(self.root)).st_mode & 0o777)
        self.assertIsNone(B.read_bridge(self.root), rec)

    @unittest.skipIf(os.name == "nt", "the uid seam is POSIX (Windows: owner SID, admin runner)")
    def test_a_record_another_uid_owns_is_refused_and_never_fixed_seam(self):
        """Without root no file can be given away, so the reader's notion of "me" is moved instead:
        the record's real owner is then someone else."""
        from mokata.cli_commands.diagnostics import privacy_lines
        from mokata.ide import bus as B
        B.ensure_bridge(self.root)
        path = B.bridge_path(self.root)
        before = Path(path).read_bytes()
        mtime = os.stat(path).st_mtime_ns
        with mock.patch("os.geteuid", return_value=os.geteuid() + 4242):
            self.assertIsNone(B.read_bridge(self.root))
            text = "\n".join(privacy_lines(self.root, fix=True))
        self.assertIn(f"{path}: FOREIGN", text)
        self.assertNotIn("re-protected", text)
        self.assertEqual(before, Path(path).read_bytes())
        self.assertEqual(mtime, os.stat(path).st_mtime_ns, "--fix touched a foreign file")

    @unittest.skipUnless(hasattr(os, "geteuid") and os.geteuid() == 0,
                         "NOT RUN: chown to another uid needs root")
    def test_a_record_owned_by_another_uid_is_refused_and_never_fixed(self):
        from mokata.cli_commands.diagnostics import privacy_lines
        from mokata.ide import bus as B
        self._plant(B.bridge_path(self.root), {"port": 24444, "token": "evil"})
        os.chmod(B.bridge_path(self.root), 0o600)
        os.chown(B.bridge_path(self.root), 4242, 4242)
        self.assertIsNone(B.read_bridge(self.root))
        before = Path(B.bridge_path(self.root)).read_bytes()
        text = "\n".join(privacy_lines(self.root, fix=True))
        self.assertIn("FOREIGN", text)
        self.assertEqual(before, Path(B.bridge_path(self.root)).read_bytes())
        self.assertEqual(4242, os.stat(B.bridge_path(self.root)).st_uid)

    @unittest.skipUnless(os.name == "nt", "the Windows owner-SID cell")
    def test_a_record_owned_by_another_sid_is_refused_and_never_fixed(self):
        """Owner set to SYSTEM (S-1-5-18): neither this user nor an Administrators group it holds."""
        from mokata.cli_commands.diagnostics import privacy_lines
        from mokata.ide import bus as B
        path = B.bridge_path(self.root)
        self._plant(path, {"port": 24444, "token": "evil"})
        icacls = os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "System32", "icacls.exe")
        p = subprocess.run([icacls, path, "/setowner", "*S-1-5-18"], capture_output=True, text=True)
        if p.returncode != 0:
            self.skipTest(f"NOT RUN: setting another owner needs SeRestorePrivilege ({p.stdout.strip()[:120]})")
        self.assertIsNone(B.read_bridge(self.root))
        before = Path(path).read_bytes()
        text = "\n".join(privacy_lines(self.root, fix=True))
        self.assertIn("FOREIGN", text)
        self.assertEqual(before, Path(path).read_bytes())


# =============================================================== round 2 (verify M1, M2, M3, M5)

def _released(test, fifo, call):
    """Run `call` in a thread; a FIFO open that BLOCKS is released (so the suite goes on) and FAILS."""
    got = []
    worker = threading.Thread(target=lambda: got.append(call()), daemon=True)
    worker.start()
    worker.join(120)                     # generous: a non-blocking open answers in microseconds
    hung = worker.is_alive()
    if hung:
        try:
            os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
        except OSError:
            pass
        worker.join(120)
    test.assertFalse(hung, f"BLOCKED opening a FIFO at {fifo}")
    return got[0] if got else None


class OneHandle(_Home):
    """`doctor --fix` judges and reads ONE handle (verify M1): a record swapped in after the verdict is
    never what gets re-protected, so planted bytes never become a trusted private record."""

    def test_a_swap_after_the_verdict_is_not_laundered(self):
        from mokata import winacl
        from mokata.cli_commands.diagnostics import privacy_lines
        from mokata.ide import bus as B
        rec = B.ensure_bridge(self.root)
        path = B.bridge_path(self.root)
        O.grant_others_read(path)                       # our own record went weak (a restore, a umask)
        seam = "_nt_handle_verdict" if os.name == "nt" else "_posix_verdict"
        real = getattr(winacl, seam)
        fired = []

        def swap(*a, **k):
            v = real(*a, **k)
            if not fired:                               # the attacker renames over it AFTER the verdict
                fired.append("verdict")
                planted = path + ".evil"
                with open(planted, "w", encoding="utf-8") as fh:
                    json.dump({"port": 24444, "token": "EVIL"}, fh)
                try:
                    os.replace(planted, path)
                    fired.append("swapped")
                except OSError:                         # Windows: the read handle holds the name
                    os.remove(planted)
            return v
        with mock.patch.object(winacl, seam, side_effect=swap):
            privacy_lines(self.root, fix=True)
        self.assertIn("verdict", fired)
        got = B.read_bridge(self.root)
        self.assertNotEqual("EVIL", (got or {}).get("token"), "planted bytes became a trusted record")
        self.assertEqual(rec, got, "the record re-protected must be the one that was verified")

    def test_the_old_profile_record_is_read_through_the_verifying_reader(self):
        """The sweep: `firstrun._move_record` read the user-profile record by a plain open. A weak one
        planted there with this pid is not moved into the repo's private record."""
        from mokata.ide import firstrun as FR
        from mokata.ide import server as S
        from mokata.ide.statedir import user_repo_dir
        old = os.path.join(user_repo_dir(self.root), "ide", "server.json")
        os.makedirs(os.path.dirname(old))
        with open(old, "w", encoding="utf-8") as fh:
            json.dump({"port": 4444, "pid": os.getpid(), "token": "EVIL", "root": self.root}, fh)
        O.grant_others_read(old)
        FR._move_record(self.root)
        self.assertFalse(os.path.exists(S.record_path(self.root)), "a weak record was adopted")
        self.assertIsNone(S.read_record(self.root))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "no FIFOs on this OS")
    def test_a_fifo_at_the_old_profile_record_does_not_hang(self):
        from mokata.ide import firstrun as FR
        from mokata.ide.statedir import user_repo_dir
        old = os.path.join(user_repo_dir(self.root), "ide", "server.json")
        os.makedirs(os.path.dirname(old))
        os.mkfifo(old, 0o600)
        _released(self, old, lambda: FR._move_record(self.root))


@unittest.skipUnless(hasattr(os, "mkfifo"), "no FIFOs on this OS")
class FifoInTheSet(_Home):
    """`mokata doctor` never waits on a FIFO planted where a record is looked for (verify M2)."""

    def _doctor(self, fifo):
        from mokata.cli_commands.diagnostics import privacy_lines
        os.makedirs(os.path.dirname(fifo), exist_ok=True)
        os.mkfifo(fifo, 0o600)
        for fix in (False, True):
            text = "\n".join(_released(self, fifo, lambda: privacy_lines(self.root, fix=fix)) or [])
            self.assertNotIn(f"{fifo}: private", text)
            self.assertNotIn("re-protected", text)

    def test_a_fifo_at_settings_local_json(self):
        self._doctor(os.path.join(self.root, ".claude", "settings.local.json"))

    def test_a_fifo_at_a_worktree_copy(self):
        self._doctor(os.path.join(self.root, ".claude", "worktrees", "w1", ".claude",
                                  "settings.local.json"))

    def test_a_fifo_at_the_bridge_record(self):
        from mokata.ide import bus as B
        self._doctor(B.bridge_path(self.root))


class _Stat:
    """What a mount without POSIX modes reports (CIFS/SMB, WSL2 drvfs, ntfs-3g): one mount-wide mode."""

    def __init__(self, st, mode, uid):
        self._st, self._mode, self._uid = st, mode, uid

    def __getattr__(self, k):
        if k == "st_mode":
            return (self._st.st_mode & ~0o7777) | self._mode
        if k == "st_uid" and self._uid is not None:
            return self._uid
        return getattr(self._st, k)


@unittest.skipIf(os.name == "nt", "POSIX modes (Windows: FILE_PERSISTENT_ACLS, NoAclVolume)")
class ModelessMount(_Home):
    """Decided by what the volume DOES, never by its name (verify M3). Simulated as p7 does: `fstat`
    reports one mount-wide mode. Real CIFS / drvfs / ntfs-3g mounts are NOT RUN here (report)."""

    def _mount(self, mode, uid=None):
        real = os.fstat
        return mock.patch("os.fstat", side_effect=lambda fd: _Stat(real(fd), mode, uid))

    def test_a_create_degrades_instead_of_raising(self):
        from mokata import winacl
        d = os.path.join(self.root, "d")
        os.makedirs(d)
        with self._mount(0o755):
            fd = winacl.create_private_file(os.path.join(d, "x"))
            try:
                v = winacl.verify_private(fd)
            finally:
                os.close(fd)
        self.assertTrue(os.path.exists(os.path.join(d, "x")))
        self.assertFalse(v.holds, v)
        self.assertFalse(v.private, "nothing may claim privacy the volume does not hold")

    def test_the_users_own_record_still_works_and_doctor_says_so(self):
        from mokata.cli_commands.diagnostics import privacy_lines
        from mokata.ide import bus as B
        with self._mount(0o755):
            rec = B.ensure_bridge(self.root)
            self.assertEqual(rec, B.read_bridge(self.root), "the IDE must still start on this mount")
            text = "\n".join(privacy_lines(self.root, fix=True))
        path = B.bridge_path(self.root)
        self.assertIn(f"{path}: NOT private — this volume has no access control", text)
        self.assertNotIn(f"{path}: private", text)
        self.assertNotIn("re-protected", text)

    def test_a_file_already_there_is_read_on_such_a_mount(self):
        from mokata import winacl
        y = os.path.join(self.root, "y")
        with open(y, "wb") as fh:
            fh.write(b"mine")
        with self._mount(0o755):
            self.assertEqual(b"mine", winacl.read_private(y))

    def test_a_mount_that_reports_0600_for_everything_is_not_called_private(self):
        """A fixed 0600 mount looks private; a mode change that does not stick gives it away."""
        from mokata.cli_commands.diagnostics import privacy_lines
        from mokata.ide import bus as B
        with self._mount(0o600):
            B.ensure_bridge(self.root)
            text = "\n".join(privacy_lines(self.root))
        path = B.bridge_path(self.root)
        self.assertIn(f"{path}: NOT private — this volume has no access control", text)

    def test_a_planted_file_another_uid_owns_is_still_refused(self):
        from mokata import winacl
        y = os.path.join(self.root, "y")
        with open(y, "wb") as fh:
            fh.write(b"planted")
        with self._mount(0o755, uid=os.geteuid() + 4242):
            self.assertIsNone(winacl.read_private(y))

    def test_a_volume_that_holds_modes_is_learnt_as_holding(self):
        """The real volume under the test: the probe sees the mode stick, so a weak own file is refused."""
        from mokata import winacl
        y = os.path.join(self.root, "y")
        with open(y, "wb") as fh:
            fh.write(b"weak")
        os.chmod(y, 0o644)
        self.assertIsNone(winacl.read_private(y))
        self.assertEqual([], [n for n in os.listdir(self.root) if n.startswith(".mkprobe-")],
                         "the probe left a file behind")


@unittest.skipIf(os.name == "nt", "POSIX modes")
class WritableByOthers(_Home):
    """A record others can WRITE (not read) is as weak as one they can read (verify M5: the mask)."""

    def test_group_or_other_write_is_refused(self):
        from mokata import winacl
        from mokata.ide import bus as B
        path = B.bridge_path(self.root)
        for mode in (0o602, 0o620):
            B.ensure_bridge(self.root)
            os.chmod(path, mode)
            self.assertIsNone(B.read_bridge(self.root), oct(mode))
            self.assertFalse(winacl.verify_private(path).private, oct(mode))


# =============================================================== safedir's reach

class SafedirReach(_Home):
    """Private-ness is a property of the CALL: `explain_commit` output and `.mokata/runs/*` keep the
    folder's access; a chain that creates `.mokata` leaves it unprotected; the reviews store is private
    below `reviews/` only."""

    governed = False

    def test_a_plain_chain_and_its_files_keep_the_folder_access(self):
        from mokata.explain.safedir import open_chain
        with open_chain(self.root, [".mokata", "runs", "r1"], create=True) as d:
            d.write("record.json", b"{}")
        with open_chain(self.root, ["docs", "explain"], create=True) as d:
            d.write("page.html", b"<p>")
        for p in (".mokata", ".mokata/runs", ".mokata/runs/r1", ".mokata/runs/r1/record.json",
                  "docs/explain/page.html"):
            self.assertNotPrivate(os.path.join(self.root, *p.split("/")), p)

    def test_the_reviews_store_is_private_below_reviews_only(self):
        from mokata.explain import store as ST
        os.makedirs(os.path.join(self.root, ".mokata"))
        with ST._reviews(self.root, "s1") as d:
            d.write("p.json", b"{}")
        tl = os.path.join(self.root, ".mokata", "temp_local")
        self.assertNotPrivate(tl, "temp_local")
        self.assertPrivate(os.path.join(tl, "reviews"), "reviews/")
        self.assertPrivate(os.path.join(tl, "reviews", "s1", "p.json"))

    def test_open_chain_is_plain_by_default(self):
        import inspect
        from mokata.explain.safedir import open_chain
        self.assertIs(False, inspect.signature(open_chain).parameters["private"].default)


class SafedirNoFollow(_Home):
    """The primitive keeps safedir's defence: `dir_fd` + `O_NOFOLLOW` on POSIX (a swapped final
    symlink is refused, its target untouched); `CREATE_NEW` refuses an existing name on Windows."""

    def test_an_existing_name_or_link_is_refused_and_not_followed(self):
        from mokata import winacl
        d = os.path.join(self.root, "d")
        os.makedirs(d)
        target = os.path.join(self.root, "victim.txt")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("keep")
        if os.name == "nt":
            with self.assertRaises(FileExistsError):
                winacl.create_private_file(target)
        else:
            os.symlink(target, os.path.join(d, "t"))
            dfd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with self.assertRaises(OSError):
                    winacl.create_private_file("t", dir_fd=dfd)
            finally:
                os.close(dfd)
        self.assertEqual("keep", Path(target).read_text(encoding="utf-8"))

    @unittest.skipIf(os.name == "nt", "Windows has no dir_fd")
    def test_the_held_directory_is_where_the_file_is_made(self):
        from mokata import winacl
        d = os.path.join(self.root, "held")
        os.makedirs(d)
        dfd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
        os.rename(d, d + "-moved")
        try:
            os.close(winacl.create_private_file("f", dir_fd=dfd))
        finally:
            os.close(dfd)
        self.assertTrue(os.path.exists(os.path.join(d + "-moved", "f")))
        self.assertFalse(os.path.exists(os.path.join(d, "f")))

    @unittest.skipUnless(os.name == "nt", "the refusal is Windows-only")
    def test_windows_refuses_a_dir_fd_rather_than_ignoring_it(self):
        from mokata import winacl
        with self.assertRaises(ValueError):
            winacl.create_private_file("f", dir_fd=3)


# =============================================================== no overclaim

#: Files whose "owner-only" claims are about members of the private set (or the door itself).
_SET_FILES = {"src/mokata/winacl.py", "src/mokata/ide/usages.py", "src/mokata/ide/features/usages.py",
              "src/mokata/explain/spill.py", "src/mokata/atomicfile.py", "src/mokata/ide/bus.py",
              "src/mokata/ide/server.py", "src/mokata/ide/trust.py", "src/mokata/explain/store.py",
              "src/mokata/explain/safedir.py", "src/mokata/harness_setup.py",
              "src/mokata/cli_commands/diagnostics.py", "docs/security.md",
              # a content-free lock sidecar whose own comment says Windows ignores the mode:
              "src/mokata/oslock.py"}


def overclaims(corpus):
    """Pure: `(file, line)` of every "owner-only" claim in a file outside the set, plus every claim
    in docs/security.md that is not on a line naming a set member."""
    out = []
    for rel, text in corpus.items():
        for i, ln in enumerate(text.splitlines(), 1):
            if "owner-only" not in ln.lower():
                continue
            if rel not in _SET_FILES:
                out.append((rel, i))
            elif rel == "docs/security.md" and "spool" in ln.lower():
                out.append((rel, i))
    return out


class NoOverclaim(unittest.TestCase):
    def test_the_check_finds_a_planted_overclaim(self):
        self.assertEqual([("src/mokata_emit/__init__.py", 2)],
                         overclaims({"src/mokata_emit/__init__.py": "x\nthe spool is owner-only\n"}))
        self.assertEqual([("docs/security.md", 1)],
                         overclaims({"docs/security.md": "hook spool (owner-only)\n"}))

    @unittest.skipIf(os.name == "nt", "NOT RUN on Windows: a text sweep of the tree, OS-independent")
    def test_the_real_tree_claims_owner_only_only_for_the_set(self):
        top = SRC.parent
        corpus = {}
        for base in (SRC, DOCS):
            for p in base.rglob("*"):
                if p.suffix in (".py", ".md") and "build" not in p.parts:
                    corpus["/".join(p.relative_to(top).parts)] = p.read_text(encoding="utf-8",
                                                                        errors="replace")
        self.assertEqual([], overclaims(corpus))


if __name__ == "__main__":
    unittest.main()
