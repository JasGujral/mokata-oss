"""VERIFIER-ONLY probe (probe branch only). Prints VLC: lines; asserts nothing that gates."""
import os, sys, time, shutil, subprocess, tempfile, unittest
from unittest import mock
import _support  # noqa
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from mokata import winacl, atomicfile

ICACLS = os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "System32", "icacls.exe")
def P(*a):
    print("VLC:", *a, flush=True)
def acl(p):
    try:
        return subprocess.run([ICACLS, p], capture_output=True, text=True).stdout.strip().replace("\n", " | ")
    except Exception as e:
        return repr(e)
def base():
    # a dir OUTSIDE the profile, like a repo on D:\a
    root = os.environ.get("GITHUB_WORKSPACE") or os.getcwd()
    d = tempfile.mkdtemp(prefix="vlc-", dir=root)
    return d

@unittest.skipUnless(os.name == "nt", "windows")
class VLC(unittest.TestCase):
    def test_1_where(self):
        P("whoami", subprocess.run(["whoami", "/groups"], capture_output=True, text=True).stdout.count("\n"))
        d = base(); P("workspace-dir ACL", acl(d))
        P("profile ACL", acl(os.path.expanduser("~")))
        P("tempdir ACL", acl(tempfile.gettempdir()))

    def test_2_cost(self):
        d = base()
        N = 5000
        t = time.perf_counter()
        for i in range(N):
            atomicfile.atomic_write_text(os.path.join(d, "f%d.json" % (i % 50)), "x" * 200)
        with_acl = time.perf_counter() - t
        with mock.patch.object(winacl, "owner_only", lambda p: None):
            t = time.perf_counter()
            for i in range(N):
                atomicfile.atomic_write_text(os.path.join(d, "g%d.json" % (i % 50)), "x" * 200)
            without = time.perf_counter() - t
        P("atomic %d writes: with ACL %.3fs (%.3f ms/w), without %.3fs (%.3f ms/w), delta %.3f ms/w" % (
            N, with_acl, with_acl * 1000 / N, without, without * 1000 / N, (with_acl - without) * 1000 / N))
        f = os.path.join(d, "raw")
        open(f, "w").close()
        t = time.perf_counter()
        for _ in range(N):
            winacl.owner_only(f)
        P("raw owner_only x%d on a file: %.3f ms each" % (N, (time.perf_counter() - t) * 1000 / N))
        # a directory with many inheriting children: SpillWriter calls owner_only(directory) every time
        dd = os.path.join(d, "many"); os.mkdir(dd)
        for i in range(3000):
            open(os.path.join(dd, "c%d" % i), "w").close()
        t = time.perf_counter(); winacl.owner_only(dd); first = time.perf_counter() - t
        t = time.perf_counter(); winacl.owner_only(dd); second = time.perf_counter() - t
        P("owner_only(dir with 3000 inheriting children): first %.1f ms, again %.1f ms" % (first * 1000, second * 1000))
        P("child ACL after dir owner_only", acl(os.path.join(dd, "c1")))

    def test_3_owner_semantics(self):
        d = base()
        p = os.path.join(d, "t.json")
        atomicfile.atomic_write_text(p, "one")
        P("after write1", acl(p))
        atomicfile.atomic_write_text(p, "two")             # replace over an owner-only target
        P("rewrite over owner-only target ok:", open(p).read())
        tmp = os.path.join(d, "plain"); open(tmp, "w").write("three")
        os.replace(tmp, p); P("plain replace over owner-only ok:", open(p).read(), acl(p))
        os.rename(p, p + ".r"); P("rename owner-only ok"); os.remove(p + ".r"); P("delete owner-only ok")
        od = os.path.join(d, "od"); os.mkdir(od); winacl.owner_only(od)
        c = os.path.join(od, "c"); open(c, "w").write("c"); os.rename(c, c + "2"); os.remove(c + "2")
        os.mkdir(os.path.join(od, "sub")); open(os.path.join(od, "sub", "x"), "w").write("x")
        shutil.rmtree(od); P("owner-only dir: child create/rename/delete + rmtree ok, exists:", os.path.exists(od))

    def test_4_order(self):
        """size of the file at the moment owner_only is called: >0 = content before the ACL."""
        seen = []
        real = winacl.owner_only
        def spy(p):
            try:
                sz = os.path.getsize(p) if os.path.isfile(p) else "dir"
            except OSError as e:
                sz = repr(e)
            seen.append((os.path.basename(p), sz)); real(p)
        from mokata.ide import bus, server, usages as U
        from mokata.explain import safedir
        d = base()
        with mock.patch.object(winacl, "owner_only", spy):
            with mock.patch("mokata.ide.statedir.governed", return_value=False), \
                 mock.patch("mokata.ide.statedir.user_home", return_value=d):
                server._write_record(d, {"token": "T" * 40}); P("server", seen); seen.clear()
                bus.ensure_bridge(d); P("bridge", seen); seen.clear()
            atomicfile.atomic_write_text(os.path.join(d, "a.json"), "S" * 40); P("atomic", seen); seen.clear()
            w = U.SpillWriter(os.path.join(d, "spill"), "k"); w.row([0], ["a"]); w.close(); P("spill", seen); seen.clear()
            with safedir.open_chain(d, ["store"]) as sd:
                sd.write("f.json", b"{}")
            P("safedir", seen)

    def test_5_owner_rights_restricted_dir(self):
        """A share/folder whose OWNER RIGHTS ACE grants Modify (no WRITE_DAC): did writes work before, do they now?"""
        d = base()
        r = os.path.join(d, "restricted"); os.mkdir(r)
        user = os.environ["USERDOMAIN"] + "\\" + os.environ["USERNAME"]
        out = subprocess.run([ICACLS, r, "/inheritance:r", "/grant:r", "*S-1-3-4:(OI)(CI)(M)",
                              "/grant:r", user + ":(OI)(CI)(M)"], capture_output=True, text=True)
        P("icacls set", out.returncode, out.stdout.strip()[:200], out.stderr.strip()[:200])
        P("restricted ACL", acl(r))
        p = os.path.join(r, "state.json")
        with mock.patch.object(winacl, "owner_only", lambda x: None):
            try:
                atomicfile.atomic_write_text(p, "pre-lane"); P("restricted: atomic write WITHOUT acl OK")
            except Exception as e:
                P("restricted: atomic write WITHOUT acl FAILED", repr(e))
        try:
            atomicfile.atomic_write_text(p, "post-lane"); P("restricted: atomic write WITH acl OK", acl(p))
        except Exception as e:
            P("restricted: atomic write WITH acl FAILED", repr(e))
        try:
            from mokata.state import StateStore  # noqa
        except Exception:
            pass

    def test_6_hook_spool(self):
        import mokata_emit as E
        sp = os.path.join(os.path.expanduser("~"), ".mokata", "hook-spool-vlc")
        try:
            p = E.spool_entry(sp, ["x"], b"payload")
            P("default-style spool dir ACL", acl(sp)); P("spool entry ACL", acl(p))
        finally:
            shutil.rmtree(sp, True)
        other = os.path.join(base(), "spool")
        p = E.spool_entry(other, ["x"], b"payload")
        P("MOKATA_HOOK_SPOOL outside profile: entry ACL", acl(p))

if __name__ == "__main__":
    unittest.main()
