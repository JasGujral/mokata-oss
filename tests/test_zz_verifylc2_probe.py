"""VERIFIER-ONLY probe v2 (probe branch only). Prints VLC2: lines; asserts nothing that gates."""
import os, sys, time, shutil, subprocess, tempfile, threading, unittest
from unittest import mock
import _support  # noqa
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from mokata import winacl, atomicfile

ICACLS = os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "System32", "icacls.exe")


def P(*a):
    print("VLC2:", *a, flush=True)


def acl(p):
    try:
        return subprocess.run([ICACLS, p], capture_output=True, text=True).stdout.strip().replace("\n", " | ")
    except Exception as e:
        return repr(e)


def base():
    root = os.environ.get("GITHUB_WORKSPACE") or os.getcwd()     # D:\a\... : outside the profile
    return tempfile.mkdtemp(prefix="vlc2-", dir=root)


def handle_sddl(fd):
    """The DACL of an OPEN handle, as SDDL (what the file's ACL was when we look through it)."""
    import ctypes, msvcrt
    from ctypes import wintypes
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    ker = ctypes.WinDLL("kernel32", use_last_error=True)
    vp = ctypes.c_void_p
    adv.GetSecurityInfo.argtypes = [vp, ctypes.c_int, wintypes.DWORD, vp, vp, ctypes.POINTER(vp), vp,
                                    ctypes.POINTER(vp)]
    adv.GetSecurityInfo.restype = wintypes.DWORD
    adv.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        vp, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_wchar_p), vp]
    ker.LocalFree.argtypes = [vp]
    h = msvcrt.get_osfhandle(fd)
    dacl, sd = vp(), vp()
    rc = adv.GetSecurityInfo(vp(h), 1, 0x4, None, None, ctypes.byref(dacl), None, ctypes.byref(sd))
    if rc:
        return "ERR%d" % rc
    s = ctypes.c_wchar_p()
    adv.ConvertSecurityDescriptorToStringSecurityDescriptorW(sd, 1, 0x4, ctypes.byref(s), None)
    out = s.value
    ker.LocalFree(sd)
    return out


def set_sddl(path, sddl):
    import ctypes
    from ctypes import wintypes
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    vp = ctypes.c_void_p
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        ctypes.c_wchar_p, wintypes.DWORD, ctypes.POINTER(vp), vp]
    adv.GetSecurityDescriptorDacl.argtypes = [vp, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(vp),
                                              ctypes.POINTER(wintypes.BOOL)]
    adv.SetNamedSecurityInfoW.argtypes = [ctypes.c_wchar_p, ctypes.c_int, wintypes.DWORD, vp, vp, vp, vp]
    sd = vp()
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(sd), None)
    a, b, dacl = wintypes.BOOL(), wintypes.BOOL(), vp()
    adv.GetSecurityDescriptorDacl(sd, ctypes.byref(a), ctypes.byref(dacl), ctypes.byref(b))
    return adv.SetNamedSecurityInfoW(path, 1, 0x4 | 0x80000000, None, None, dacl, None)


@unittest.skipUnless(os.name == "nt", "windows")
class VLC2(unittest.TestCase):
    def test_1_handle_keeps_access_after_dacl_change(self):
        d = base()
        p = os.path.join(d, "h.txt")
        with open(p, "w") as fh:
            fh.write("SECRET-BEFORE")
        fd = os.open(p, os.O_RDONLY)
        rc = set_sddl(p, "D:P")          # protected, NO entries: nobody may open it now
        try:
            open(p).read()
            fresh = "fresh open SUCCEEDED"
        except OSError as e:
            fresh = "fresh open refused: %r" % e
        os.lseek(fd, 0, 0)
        P("handle-retention: set rc", rc, "|", fresh, "| read via the OLD handle:", os.read(fd, 100))
        os.close(fd)
        set_sddl(p, "D:P(A;;FA;;;%s)" % winacl.current_user_sid())

    def test_2_race_private_temp_opened_under_inherited_acl(self):
        """A watcher opens every new file in the folder as fast as it can and records the DACL it
        saw THROUGH ITS HANDLE at open time; after the writer finishes, the watcher reads the
        content through that same handle."""
        d = base()
        P("race folder ACL", acl(d))
        stop = threading.Event()
        held = {}

        def watch():
            while not stop.is_set():
                try:
                    names = os.listdir(d)
                except OSError:
                    continue
                for n in names:
                    if n in held or not n.startswith(".tmp-"):
                        continue
                    try:
                        fd = os.open(os.path.join(d, n), os.O_RDONLY)
                    except OSError:
                        continue
                    held[n] = (fd, handle_sddl(fd))
        t = threading.Thread(target=watch, daemon=True)
        t.start()
        N = 3000
        for i in range(N):
            atomicfile.atomic_write_text(os.path.join(d, "secret%d.json" % (i % 20)), "TOKEN-%06d" % i,
                                         private=True)
        time.sleep(0.5)
        stop.set()
        t.join(5)
        inherited, leaked = 0, 0
        sample = None
        for n, (fd, sddl) in held.items():
            if sddl and "D:P(" not in sddl:
                inherited += 1
                os.lseek(fd, 0, 0)
                data = os.read(fd, 100)
                if data.startswith(b"TOKEN-"):
                    leaked += 1
                    sample = sample or (n, sddl, data)
            os.close(fd)
        P("race: %d private writes, watcher caught %d temp files, %d opened while the ACL was still "
          "INHERITED, %d of those then read the secret through the old handle; sample=%r"
          % (N, len(held), inherited, leaked, sample))

    def test_3_ordinary_writes_keep_inherited(self):
        d = base()
        a = os.path.join(d, ".gitignore")
        atomicfile.atomic_write_text(a, "x\n")
        ctl = os.path.join(d, "control")
        open(ctl, "w").close()
        P("ordinary atomic", acl(a))
        P("control       ", acl(ctl))
        from mokata.state import StateStore
        try:
            s = StateStore(d)
            s.set("k", {"v": 1}) if hasattr(s, "set") else None
        except Exception as e:
            P("statestore", repr(e))
        for root, _ds, fs in os.walk(d):
            for f in fs:
                if f.endswith(".json"):
                    P("state file", f, acl(os.path.join(root, f)))
        s2 = os.path.join(d, "priv.json")
        atomicfile.atomic_write_text(s2, "S", private=True)
        P("private atomic", acl(s2))
        atomicfile.atomic_write_text(s2, "S2")                 # ordinary write OVER a private file
        P("ordinary write over a private file", acl(s2))

    def test_4_failure_paths(self):
        d = base()
        with mock.patch.object(winacl, "owner_only", side_effect=OSError(5, "x")):
            try:
                atomicfile.atomic_write_text(os.path.join(d, "a.json"), "S", private=True)
            except OSError:
                pass
        P("acl-fail leftovers", os.listdir(d))
        real = os.fsync
        with mock.patch.object(os, "fsync", side_effect=OSError(28, "nospace")):
            try:
                atomicfile.atomic_write_text(os.path.join(d, "b.json"), "S", private=True)
            except OSError:
                pass
        P("write-fail leftovers", os.listdir(d))
        with mock.patch.object(atomicfile, "_replace", side_effect=PermissionError(13, "busy")):
            try:
                atomicfile.atomic_write_text(os.path.join(d, "c.json"), "S", private=True)
            except OSError:
                pass
        P("rename-fail leftovers", os.listdir(d))

    def test_5_spool_under_profile_edges(self):
        import mokata_emit as E
        home = os.path.expanduser("~")
        P("home", home, "USERPROFILE", os.environ.get("USERPROFILE"))
        inside = os.path.join(home, ".mokata", "vlc2-spool")
        P("inside", E.under_profile(inside, home))
        P("case-flipped inside", E.under_profile(inside.upper(), home.lower()))
        import ctypes
        buf = ctypes.create_unicode_buffer(600)
        ctypes.windll.kernel32.GetShortPathNameW(home, buf, 600)
        P("8.3 home", buf.value, E.under_profile(os.path.join(buf.value, ".mokata", "x"), home))
        out = base()
        j = os.path.join(home, "vlc2-junction")
        r = subprocess.run(["cmd", "/c", "mklink", "/J", j, out], capture_output=True, text=True)
        P("mklink", r.returncode, r.stdout.strip(), r.stderr.strip())
        P("junction out of profile ->", E.under_profile(os.path.join(j, "spool"), home))
        try:
            E.spool_entry(os.path.join(j, "spool"), ["x"], b"payload")
            P("junction spool: WRITTEN", os.listdir(os.path.join(out, "spool")))
        except OSError as e:
            P("junction spool: refused", e)
        P("other drive", E.under_profile("D:\\x\\spool", home), "C root", E.under_profile("C:\\spool", home))
        P("UNC", E.under_profile("\\\\localhost\\C$\\Users\\x", home))
        # USERPROFILE unset (and the HOMEDRIVE/HOMEPATH fallback too)
        env = {k: v for k, v in os.environ.items() if k not in ("USERPROFILE", "HOMEDRIVE", "HOMEPATH", "HOME")}
        code = ("import os,sys; sys.path.insert(0, r'%s'); import mokata_emit as E; "
                "print('expanduser', os.path.expanduser('~')); print('spool_dir', E.spool_dir()); "
                "print('under', E.under_profile(E.spool_dir(), os.path.expanduser('~')))"
                % os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
        cwd = base()
        r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, cwd=cwd)
        P("no USERPROFILE:", r.stdout.strip().replace("\n", " | "), r.stderr.strip()[-300:])
        shutil.rmtree(inside, True)
        os.rmdir(j)

    def test_6_spill_in_a_preexisting_folder(self):
        from mokata.ide import usages as U
        d = base()
        sp = os.path.join(d, "spill")
        os.mkdir(sp)                          # someone else made it: inherited ACL
        w = U.SpillWriter(sp, "k")
        w.row([0], ["a"])
        w.close()
        P("pre-existing spill dir", acl(sp))
        P("spill file in it", acl(w.path))
        w.discard()

    def test_7_server_and_bridge_records_in_a_governed_repo(self):
        from mokata.ide import bus, server, statedir
        d = base()
        with mock.patch.object(statedir, "governed", return_value=True):
            try:
                server._write_record(d, {"token": "T" * 40})
                bus.ensure_bridge(d)
                P("server record", server.record_path(d), acl(server.record_path(d)))
                P("bridge record", acl(bus.bridge_path(d)))
                P("record dir", acl(os.path.dirname(server.record_path(d))))
            except Exception as e:
                P("governed records", repr(e))

    def test_8_trust_and_bundle(self):
        from mokata.ide import trust
        from mokata import session_transport as ST
        d = base()
        try:
            trust._write(d, {"x": 1})
            P("trust record", trust.record_path(d), acl(trust.record_path(d)))
        except Exception as e:
            P("trust", repr(e))
        t = ST.LocalTransport(d)
        p = t.write_bundle("tag1", "{}")
        P("bundle", p, acl(p))
        dst = os.path.join(base(), "moved.json")
        shutil.move(p, dst)
        P("bundle after a same-volume move to a shared folder", acl(dst), "| folder", acl(os.path.dirname(dst)))


if __name__ == "__main__":
    unittest.main()
