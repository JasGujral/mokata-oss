import os, subprocess, tempfile, unittest

class Crlf(unittest.TestCase):
    def test_clone_with_the_pin_config_keeps_lf(self):
        tmp = os.path.realpath(tempfile.mkdtemp())
        up = os.path.join(tmp, "up"); os.makedirs(up)
        def g(cwd, *a):
            r = subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
            if r.returncode: print("DIAG fail", a, r.stderr)
            return r
        g(up, "init", "-q"); g(up, "config", "user.email", "t@t"); g(up, "config", "user.name", "t")
        with open(os.path.join(up, "a.py"), "wb") as fh: fh.write(b"one\n")
        g(up, "add", "-A"); g(up, "commit", "-q", "-m", "first")
        sha = g(up, "rev-parse", "HEAD").stdout.strip()
        g(up, "config", "uploadpack.allowAnySHA1InWant", "true")
        url = "file://" + up.replace(os.sep, "/")
        for tag, conf in (("plain", []), ("pinned", [["config", "core.autocrlf", "false"], ["config", "core.eol", "lf"]])):
            dest = os.path.join(tmp, tag); os.makedirs(dest)
            for args in (["init", "-q"], *conf, ["remote", "add", "origin", url], ["fetch", "-q", "--depth", "1", "origin", sha],
                         ["-c", "advice.detachedHead=false", "checkout", "-q", "--detach", "FETCH_HEAD"]):
                g(dest, *args)
            print("DIAG", tag, open(os.path.join(dest, "a.py"), "rb").read())
        self.assertEqual(b"one\n", open(os.path.join(tmp, "pinned", "a.py"), "rb").read())
