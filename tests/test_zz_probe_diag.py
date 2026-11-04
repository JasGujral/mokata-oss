import os, subprocess, tempfile, unittest

class Diag(unittest.TestCase):
    def test_stat_errors(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "src", "app"))
        open(os.path.join(d, "src", "app", "core.py"), "w").write("x")
        for name in ("src/app/core.py:3:x", "src/app/core.py:3:9999999999", "src/app/core.py:-3",
                     "src/app/core.py:x", "src/app/core.py:3", "src/app/nope.py:3", "aux.txt"):
            p = os.path.join(d, *name.split("/"))
            try:
                st = os.stat(p); print("DIAG stat", name, "OK ino", st.st_ino)
            except BaseException as e:
                print("DIAG stat", name, type(e).__name__, getattr(e, "winerror", None), getattr(e, "errno", None))
            print("DIAG realpath", name, os.path.realpath(p))

    def test_commit(self):
        d = os.path.realpath(tempfile.mkdtemp())
        def g(*a):
            r = subprocess.run(["git", "-C", d, *a], capture_output=True, text=True, stdin=subprocess.DEVNULL)
            print("DIAG git", a, r.returncode, repr(r.stdout[-300:]), repr(r.stderr[-500:]))
            return r
        g("init", "-q", "-b", "main"); g("config", "user.email", "t@t"); g("config", "user.name", "t")
        g("config", "commit.gpgsign", "false")
        with open(os.path.join(d, ".git", "config"), "a") as fh: fh.write("# MARK\n")
        os.makedirs(os.path.join(d, ".git", "hooks"), exist_ok=True)
        with open(os.path.join(d, ".git", "hooks", "pre-commit"), "w") as fh: fh.write("echo MARK\n")
        os.makedirs(os.path.join(d, "src")); open(os.path.join(d, "src", "app.py"), "w").write("x=1\n")
        g("add", "src"); g("status"); g("commit", "-q", "-m", "init")
        os.remove(os.path.join(d, ".git", "hooks", "pre-commit"))
        open(os.path.join(d, "src", "app.py"), "w").write("x=2\n")
        g("add", "src"); g("commit", "-q", "-m", "init2")

    def test_aux(self):
        d = os.path.realpath(tempfile.mkdtemp())
        def g(*a, inp=None):
            r = subprocess.run(["git", "-C", d, *a], capture_output=True, text=True, input=inp)
            print("DIAG git", a, r.returncode, repr(r.stdout[-300:]), repr(r.stderr[-500:]))
            return r
        g("init", "-q", "-b", "main"); g("config", "user.email", "t@t"); g("config", "user.name", "t")
        open(os.path.join(d, "r.txt"), "w").write("a\n"); g("add", "-A"); g("commit", "-qm", "a")
        open(os.path.join(d, "aux.txt"), "w").write("a\n"); g("add", "-A")
        b = g("hash-object", "-w", "--stdin", inp="x\n").stdout.strip()
        g("update-index", "--add", "--cacheinfo", "100644," + b + ",aux.txt"); g("commit", "-qm", "aux")
        g("ls-files")

    def test_crlf(self):
        print("DIAG autocrlf", subprocess.run(["git", "config", "--show-origin", "--get-all", "core.autocrlf"], capture_output=True, text=True).stdout)
