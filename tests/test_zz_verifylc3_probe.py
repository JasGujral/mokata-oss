"""VERIFIER-ONLY probe v3 (probe branch only). Prints VLC3: lines; gating asserts are the race ones."""
import os, sys, time, shutil, subprocess, tempfile, threading, unittest
from unittest import mock
import _support  # noqa
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from mokata import winacl, atomicfile

ICACLS = os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "System32", "icacls.exe")
USERS = "S-1-5-32-545"


def P(*a):
    print("VLC3:", *a, flush=True)


def acl(p):
    try:
        return subprocess.run([ICACLS, p], capture_output=True, text=True).stdout.strip().replace("\n", " | ")
    except Exception as e:
        return repr(e)


def base():
    root = os.environ.get("GITHUB_WORKSPACE") or os.getcwd()     # D:\a\... : outside the profile
    return tempfile.mkdtemp(prefix="vlc3-", dir=root)


def users_rx(d):
    subprocess.run([ICACLS, d, "/grant", "*%s:(OI)(CI)(RX)" % USERS], capture_output=True, check=True)


def k32():
    import ctypes
    from ctypes import wintypes
    ker = ctypes.WinDLL("kernel32", use_last_error=True)
    vp = ctypes.c_void_p
    ker.CreateFileW.argtypes = [ctypes.c_wchar_p, wintypes.DWORD, wintypes.DWORD, vp,
                                wintypes.DWORD, wintypes.DWORD, vp]
    ker.CreateFileW.restype = vp
    ker.GetCurrentProcess.restype = vp
    ker.CloseHandle.argtypes = [vp]
    ker.GetProcessHandleCount.argtypes = [vp, ctypes.POINTER(wintypes.DWORD)]
    return ctypes, wintypes, ker


def handles():
    ctypes, wintypes, ker = k32()
    n = wintypes.DWORD()
    ker.GetProcessHandleCount(ker.GetCurrentProcess(), ctypes.byref(n))
    return n.value


def dacl_sddl(path):
    import ctypes
    from ctypes import wintypes
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    ker = ctypes.WinDLL("kernel32", use_last_error=True)
    vp = ctypes.c_void_p
    adv.GetNamedSecurityInfoW.argtypes = [ctypes.c_wchar_p, ctypes.c_int, wintypes.DWORD, vp, vp,
                                          ctypes.POINTER(vp), vp, ctypes.POINTER(vp)]
    adv.GetNamedSecurityInfoW.restype = wintypes.DWORD
    adv.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        vp, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_wchar_p), vp]
    ker.LocalFree.argtypes = [vp]
    dacl, sd = vp(), vp()
    rc = adv.GetNamedSecurityInfoW(path, 1, 0x4 | 0x1, None, None, ctypes.byref(dacl), None, ctypes.byref(sd))
    if rc:
        return "ERR%d" % rc
    s = ctypes.c_wchar_p()
    adv.ConvertSecurityDescriptorToStringSecurityDescriptorW(sd, 1, 0x4 | 0x1, ctypes.byref(s), None)
    out = s.value
    ker.LocalFree(sd)
    return out


class Restricted:
    """A watcher identity: this token with the user's SID and Administrators DENY-ONLY."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ctypes = ctypes
        ctypes_, wintypes_, self.ker = k32()
        self.adv = adv = ctypes.WinDLL("advapi32", use_last_error=True)
        vp = ctypes.c_void_p
        adv.OpenProcessToken.argtypes = [vp, wintypes.DWORD, ctypes.POINTER(vp)]
        adv.ConvertStringSidToSidW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(vp)]

        class SAA(ctypes.Structure):
            _fields_ = [("Sid", vp), ("Attributes", wintypes.DWORD)]
        adv.CreateRestrictedToken.argtypes = [vp, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(SAA),
                                              wintypes.DWORD, vp, wintypes.DWORD, vp, ctypes.POINTER(vp)]
        adv.ImpersonateLoggedOnUser.argtypes = [vp]
        tok = vp()
        assert adv.OpenProcessToken(self.ker.GetCurrentProcess(), 0xF, ctypes.byref(tok))
        sids = (SAA * 2)()
        for i, s_ in enumerate((winacl.current_user_sid(), "S-1-5-32-544")):
            psid = vp()
            assert adv.ConvertStringSidToSidW(s_, ctypes.byref(psid))
            sids[i].Sid = psid
        self.tok = vp()
        assert adv.CreateRestrictedToken(tok, 0, 2, sids, 0, None, 0, None, ctypes.byref(self.tok))
        self.INVALID = ctypes.c_void_p(-1).value

    def open(self, path, access=0x80000000, disp=3, flags=0):
        h = self.ker.CreateFileW(path, access, 7, None, disp, flags, None)
        if not h or h == self.INVALID:
            return None, self.ctypes.get_last_error()
        return h, 0

    def open_dir(self, path):
        return self.open(path, 0x1, 3, 0x02000000)          # FILE_LIST_DIRECTORY, backup semantics

    def run(self, fn):
        out = {}

        def body():
            if not self.adv.ImpersonateLoggedOnUser(self.tok):
                out["imp"] = self.ctypes.get_last_error()
                return
            try:
                out["r"] = fn()
            except Exception as e:      # noqa
                out["exc"] = repr(e)
            finally:
                self.adv.RevertToSelf()
        t = threading.Thread(target=body)
        t.start()
        t.join(60)
        return out


class Watch:
    """Restricted watcher polling `folder`: opens every name matching `pred` it has not seen."""

    def __init__(self, R, folder, pred, opener=None):
        self.R, self.folder, self.pred = R, folder, pred
        self.opener = opener or R.open
        self.stop = threading.Event()
        self.seen, self.opened, self.refused, self.listfail = set(), {}, {}, 0
        self.calib = {}

    def start(self, plain, private):
        def body():
            import msvcrt
            if not self.R.adv.ImpersonateLoggedOnUser(self.R.tok):
                self.calib["imp"] = self.R.ctypes.get_last_error(); return
            try:
                for lab, p in (("plain", plain), ("private", private)):
                    h, e = self.R.open(p)
                    self.calib[lab] = "opened" if h else "refused %d" % e
                    if h:
                        self.R.ker.CloseHandle(h)
                self.calib["ready"] = True
                while not self.stop.is_set():
                    try:
                        names = os.listdir(self.folder)
                    except OSError:
                        self.listfail += 1
                        continue
                    for n in names:
                        if n in self.seen or not self.pred(n):
                            continue
                        self.seen.add(n)
                        h, e = self.opener(os.path.join(self.folder, n))
                        if h:
                            self.opened[n] = h
                        else:
                            self.refused[e] = self.refused.get(e, 0) + 1
            finally:
                self.R.adv.RevertToSelf()
        self.t = threading.Thread(target=body, daemon=True)
        self.t.start()
        for _ in range(600):
            if self.calib.get("ready") or "imp" in self.calib:
                break
            time.sleep(0.05)
        return self.calib

    def finish(self):
        time.sleep(0.5)
        self.stop.set()
        self.t.join(30)
        leaked = 0
        for n, h in self.opened.items():
            import msvcrt
            try:
                fd = msvcrt.open_osfhandle(h, os.O_RDONLY)
                data = os.read(fd, 100)
                os.close(fd)
                if data:
                    leaked += 1
            except OSError:
                self.R.ker.CloseHandle(h)
        return dict(seen=len(self.seen), opened=len(self.opened), refused=self.refused,
                    leaked=leaked, listfail=self.listfail)


@unittest.skipUnless(os.name == "nt", "windows")
class VLC3Race(unittest.TestCase):
    N = 3000

    def setUp(self):
        self.R = Restricted()
        self.d = base()
        users_rx(self.d)
        self.plain = os.path.join(self.d, "plain.txt")
        open(self.plain, "w").write("p")
        self.private = os.path.join(self.d, "private.txt")
        os.close(winacl.create_private_file(self.private))

    def _calib(self, w):
        c = w.start(self.plain, self.private)
        P("calibration", c)
        self.assertEqual({"plain": "opened", "private": "refused 5", "ready": True}, c)

    def test_r1_atomicfile_private(self):
        w = Watch(self.R, self.d, lambda n: n.startswith(".tmp-") or n.startswith("secret"))
        self._calib(w)
        for i in range(self.N):
            atomicfile.atomic_write_text(os.path.join(self.d, "secret%d.json" % i), "TOKEN-%06d" % i,
                                         private=True)
        r = w.finish()
        P("atomicfile private x%d:" % self.N, r)
        self.assertEqual(0, r["opened"], r)
        self.assertGreater(r["seen"], 0, "oracle saw nothing")

    def test_r2_create_private_file_raw(self):
        w = Watch(self.R, self.d, lambda n: n.startswith("raw"))
        self._calib(w)
        for i in range(self.N):
            fd = winacl.create_private_file(os.path.join(self.d, "raw%d" % i))
            os.write(fd, b"TOKEN")
            os.close(fd)
        r = w.finish()
        P("create_private_file x%d:" % self.N, r)
        self.assertEqual(0, r["opened"], r)
        self.assertGreater(r["seen"], 0)

    def test_r3_make_private_dir(self):
        # each new dir: the watcher tries to LIST it and to CREATE a file inside it (a plant)
        def opener(p):
            h, e = self.R.open_dir(p)
            if h:
                return h, 0
            h2, e2 = self.R.open(os.path.join(p, "plant"), 0x40000000, 1, 0)
            if h2:
                return h2, 0
            return None, (e, e2)
        w = Watch(self.R, self.d, lambda n: n.startswith("pd"), opener)
        self._calib(w)
        n = 1000
        for i in range(n):
            p = os.path.join(self.d, "pd%d" % i)
            winacl.make_private_dir(p)
            with open(os.path.join(p, "child.txt"), "w") as fh:     # an ORDINARY file inside
                fh.write("TOKEN")
        r = w.finish()
        child = os.path.join(self.d, "pd0", "child.txt")
        r2 = self.R.run(lambda: self.R.open(child))
        P("make_private_dir x%d:" % n, r, "| ordinary child opened by restricted:", r2,
          "| dir sddl", dacl_sddl(os.path.join(self.d, "pd0")), "| child sddl", dacl_sddl(child))
        self.assertEqual(0, r["opened"], r)
        self.assertGreater(r["seen"], 0)
        self.assertIsNone(r2.get("r", (1,))[0], r2)

    def test_r4_spill_in_a_preexisting_listable_folder(self):
        from mokata.ide import usages as U
        sp = os.path.join(self.d, "spill")
        os.mkdir(sp)                                  # inherited Users RX: the watcher can list
        w = Watch(self.R, sp, lambda n: n.endswith(".spill"))
        self._calib(w)
        n = 1500
        bad = 0
        for i in range(n):
            sw = U.SpillWriter(sp, "k")
            for j in range(3):
                sw.row([j], ["TOKEN\n%d" % j])
            sw.close()
            if os.path.getsize(sw.path) != sw.bytes:
                bad += 1
        r = w.finish()
        P("spill (pre-existing dir) x%d:" % n, r, "| size!=bytes:", bad)
        self.assertEqual(0, r["opened"], r)
        self.assertGreater(r["seen"], 0)
        self.assertEqual(0, bad)

    def test_r5_spill_fresh_dir(self):
        from mokata.ide import usages as U
        sp = os.path.join(self.d, "fresh", "spill")
        sw = U.SpillWriter(sp, "k")
        sw.row([0], ["x"])
        sw.close()
        r = self.R.run(lambda: (self.R.open_dir(sp), self.R.open(sw.path)))
        P("spill fresh dir: restricted list/open:", r, "| dir", dacl_sddl(sp), "| file", dacl_sddl(sw.path))
        self.assertIsNone(r["r"][0][0])
        self.assertIsNone(r["r"][1][0])

    def test_r6_safedir(self):
        from mokata.explain.safedir import SafeDir, open_chain
        sd = SafeDir(self.d, None)
        w = Watch(self.R, self.d, lambda n: n.startswith(".tmp-") or n.startswith("sd"))
        self._calib(w)
        for i in range(self.N):
            sd.write("sd%d" % i, b"TOKEN-%06d" % i)
        r = w.finish()
        P("safedir x%d:" % self.N, r)
        self.assertEqual(0, r["opened"], r)
        self.assertGreater(r["seen"], 0)
        ch = open_chain(self.d, ["c1", "c2"], create=True)
        ch.write("f", b"TOKEN")
        rr = self.R.run(lambda: (self.R.open_dir(os.path.join(self.d, "c1")), self.R.open(os.path.join(ch.path, "f"))))
        P("safedir chain: restricted", rr, "| c1", dacl_sddl(os.path.join(self.d, "c1")), "| f",
          dacl_sddl(os.path.join(ch.path, "f")))


@unittest.skipUnless(os.name == "nt", "windows")
class VLC3Edges(unittest.TestCase):
    def test_e1_descriptor_shape_and_replace(self):
        d = base(); users_rx(d)
        sid = winacl.current_user_sid()
        f = os.path.join(d, "f")
        os.close(winacl.create_private_file(f))
        P("file sddl", dacl_sddl(f), "| expected D:P(A;;FA;;;%s) owner" % sid)
        t = os.path.join(d, "t.json")
        open(t, "w").write("old")                               # an ordinary target, Users RX inherited
        P("target before", dacl_sddl(t))
        atomicfile.atomic_write_text(t, "NEW", private=True)
        P("target after private replace", dacl_sddl(t), open(t).read())
        atomicfile.atomic_write_text(t, "NEW2", private=True)    # private over private
        P("target after 2nd private replace", dacl_sddl(t))
        # text-mode check
        g = os.path.join(d, "g")
        fd = winacl.create_private_file(g); os.write(fd, b"a\nb\n"); os.close(fd)
        P("bytes a\\nb\\n -> size", os.path.getsize(g))
        # errno mapping
        for lab, fn in (("file exists", lambda: winacl.create_private_file(f)),
                        ("dir exists", lambda: winacl.make_private_dir(d)),
                        ("file no parent", lambda: winacl.create_private_file(os.path.join(d, "nope", "x"))),
                        ("dir no parent", lambda: winacl.make_private_dir(os.path.join(d, "nope", "x"))),
                        ("file is a dir", lambda: winacl.create_private_file(d))):
            try:
                fn(); P(lab, "NO ERROR")
            except OSError as e:
                P(lab, type(e).__name__, e.errno, getattr(e, "winerror", None))

    def test_e2_handle_and_file_leaks(self):
        import msvcrt
        d = base()
        h0 = handles()
        for i in range(300):
            with mock.patch.object(msvcrt, "open_osfhandle", side_effect=OSError(24, "EMFILE")):
                try:
                    winacl.create_private_file(os.path.join(d, "o%d" % i))
                except OSError:
                    pass
        h1 = handles()
        P("open_osfhandle fails x300: handle delta", h1 - h0, "| files left behind:", len(os.listdir(d)))
        d2 = base()
        h0 = handles()
        for i in range(300):
            with mock.patch.object(os, "fsync", side_effect=OSError(28, "nospace")):
                try:
                    atomicfile.atomic_write_text(os.path.join(d2, "w%d" % i), "S", private=True)
                except OSError:
                    pass
        P("write fails x300: handle delta", handles() - h0, "| left:", os.listdir(d2)[:3], len(os.listdir(d2)))
        d3 = base()
        h0 = handles()
        for i in range(300):
            with mock.patch.object(atomicfile, "_replace", side_effect=PermissionError(13, "busy")):
                try:
                    atomicfile.atomic_write_text(os.path.join(d3, "r%d" % i), "S", private=True)
                except OSError:
                    pass
        P("rename fails x300: handle delta", handles() - h0, "| left:", len(os.listdir(d3)))
        from mokata.explain.safedir import SafeDir
        d4 = base(); sd = SafeDir(d4, None)
        h0 = handles()
        for i in range(300):
            with mock.patch.object(os, "fsync", side_effect=OSError(28, "nospace")):
                try:
                    sd.write("s%d" % i, b"x")
                except OSError:
                    pass
        P("safedir write fails x300: handle delta", handles() - h0, "| left:", len(os.listdir(d4)))
        # ok path: no leak
        d5 = base(); h0 = handles()
        for i in range(500):
            atomicfile.atomic_write_text(os.path.join(d5, "k%d" % i), "S", private=True)
        P("ok path x500: handle delta", handles() - h0)

    def _diskpart(self, letter, fs):
        vhd = os.path.join(os.environ.get("RUNNER_TEMP", "D:\\"), "vlc3-%s.vhdx" % fs)
        script = os.path.join(os.environ.get("RUNNER_TEMP", "D:\\"), "dp-%s.txt" % fs)
        with open(script, "w") as fh:
            fh.write('create vdisk file="%s" maximum=200 type=expandable\nselect vdisk file="%s"\n'
                     'attach vdisk\nconvert mbr\ncreate partition primary\nformat fs=%s quick label=V%s\n'
                     'assign letter=%s\n' % (vhd, vhd, fs, fs[:3], letter))
        r = subprocess.run(["diskpart", "/s", script], capture_output=True, text=True, timeout=180)
        P("diskpart", fs, r.returncode, r.stdout.strip().splitlines()[-3:])
        return os.path.exists(letter + ":\\")

    def test_e3_volumes_without_acls(self):
        from mokata.ide import usages as U
        for letter, fs in (("V", "fat32"), ("W", "exfat")):
            try:
                ok = self._diskpart(letter, fs)
            except Exception as e:
                P(fs, "diskpart failed", repr(e)); continue
            if not ok:
                P(fs, "no volume"); continue
            root = letter + ":\\"
            for lab, fn in (
                    ("create_private_file", lambda: os.close(winacl.create_private_file(root + "a"))),
                    ("make_private_dir", lambda: winacl.make_private_dir(root + "d")),
                    ("atomic private", lambda: atomicfile.atomic_write_text(root + "t.json", "S", private=True)),
                    ("spill", lambda: U.SpillWriter(root + "sp", "k").close())):
                try:
                    fn(); P(fs, lab, "OK", "| sddl", dacl_sddl(root + {"create_private_file": "a", "make_private_dir": "d", "atomic private": "t.json", "spill": "sp"}[lab]))
                except Exception as e:
                    P(fs, lab, "RAISED", type(e).__name__, e)
            try:
                from mokata.ide import snapshots as S
                S._FOLDS_CASE.clear()
                P(fs, "volume_folds_case", S.volume_folds_case(root))
            except Exception as e:
                P(fs, "folds", repr(e))

    def test_e4_smb_share(self):
        d = base()
        name = "vlc3share"
        r = subprocess.run(["net", "share", "%s=%s" % (name, d), "/grant:Everyone,FULL"],
                           capture_output=True, text=True)
        P("net share", r.returncode, r.stdout.strip()[-200:], r.stderr.strip()[-200:])
        unc = "\\\\localhost\\%s\\" % name
        for lab, fn, leaf in (
                ("create_private_file", lambda: os.close(winacl.create_private_file(unc + "a")), "a"),
                ("make_private_dir", lambda: winacl.make_private_dir(unc + "dd"), "dd"),
                ("atomic private", lambda: atomicfile.atomic_write_text(unc + "t.json", "S", private=True), "t.json")):
            try:
                fn(); P("smb", lab, "OK | local sddl", dacl_sddl(os.path.join(d, leaf)), "| unc sddl", dacl_sddl(unc + leaf))
            except Exception as e:
                P("smb", lab, "RAISED", type(e).__name__, e)
        subprocess.run(["net", "share", name, "/delete", "/y"], capture_output=True)

    def test_e5_case_sensitive_directory(self):
        from mokata.ide import snapshots as S
        d = base()
        S._FOLDS_CASE.clear()
        P("D: volume_folds_case", S.volume_folds_case(d))
        cs = os.path.join(d, "csrepo"); os.mkdir(cs)
        r = subprocess.run(["fsutil.exe", "file", "setCaseSensitiveInfo", cs, "enable"], capture_output=True, text=True)
        P("fsutil setCaseSensitiveInfo", r.returncode, r.stdout.strip(), r.stderr.strip())
        if r.returncode == 0:
            os.mkdir(os.path.join(cs, ".mokata"))
            P("disk truth in csrepo: .MOKATA exists?", os.path.exists(os.path.join(cs, ".MOKATA")))
            S._FOLDS_CASE.clear()
            P("volume_folds_case(csrepo)", S.volume_folds_case(cs), "| record .MOKATA/x ->",
              S._canonical_store_name(".MOKATA/x", cs))

    def test_e6_doctor_and_spool(self):
        import mokata_emit as E
        P("spool_refusal default", repr(E.spool_refusal(E.spool_dir(), os.path.expanduser("~"))))
        out = base()
        j = os.path.join(os.path.expanduser("~"), ".vlc3-junction")
        subprocess.run(["cmd", "/c", "mklink", "/J", j, out], capture_output=True)
        with mock.patch.dict(os.environ, {"MOKATA_HOOK_SPOOL": os.path.join(out, "sp")}):
            P("spool_refusal env out", E.spool_refusal(E.spool_dir(), os.path.expanduser("~"))[:120])
        env = {k: v for k, v in os.environ.items() if k not in ("USERPROFILE", "HOMEDRIVE", "HOMEPATH", "HOME")}
        r = subprocess.run([sys.executable, "-c", "import mokata_emit as E,os; print(repr(E.spool_refusal(E.spool_dir(), os.path.expanduser('~'))))"],
                           env=env, capture_output=True, text=True,
                           cwd=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
        P("no USERPROFILE refusal:", r.stdout.strip()[:160], r.stderr.strip()[-200:])
        os.rmdir(j)


if __name__ == "__main__":
    unittest.main()
