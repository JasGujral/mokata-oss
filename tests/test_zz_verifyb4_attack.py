"""Verifier B4 attack probe (Windows). Prints evidence lines starting with 'V4 '. Not shipped."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import _support  # noqa: F401

from mokata.init import init_repo


def _w(root, rel, text):
    p = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return p


def _set_command(root, cmd):
    mf = os.path.join(root, ".mokata", "manifest.json")
    with open(mf, encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("settings", {}).setdefault("baseline", {})["test_command"] = cmd
    with open(mf, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def say(*a):
    print("V4", *a, flush=True)


@unittest.skipUnless(os.name == "nt", "Windows probe")
class Attack(unittest.TestCase):

    def tmp(self, p="v4-"):
        d = os.path.realpath(tempfile.mkdtemp(prefix=p))
        self.addCleanup(lambda: shutil.rmtree(d, True))
        return d

    def root(self, cmd):
        d = self.tmp("v4root-")
        init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
        _set_command(d, cmd)
        return d

    def fake_npm(self, prefix, mark):
        _w(prefix, "npm.cmd", "@echo off\r\necho CMD>> \"%s\"\r\n" % mark)
        _w(prefix, "node_modules/npm/package.json",
           json.dumps({"name": "npm", "bin": {"npm": "bin/npm-cli.js"}}))
        _w(prefix, "node_modules/npm/bin/npm-cli.js",
           "require('fs').appendFileSync(%s, 'JS|' + process.argv.slice(2).join('|') + '\\n');"
           "process.exit(0);\n" % json.dumps(mark))

    # ---- (1) real layouts
    def test_a1_real_npm_npx_pnpm_yarn(self):
        from mokata.ide import review_context as RC
        root = self.root("npm test")
        say("which npm", shutil.which("npm"), "node", shutil.which("node"))
        for n in ("npm", "npx", "pnpm", "yarn", "corepack", "mvn", "gradle"):
            head, why = RC.resolve_runner(root, n, os.environ.get("PATH"))
            say("resolve", n, "->", head, "|", why)
        tc = RC.test_command(root)
        say("test_command npm", tc["runnable"], tc["note"])

    def test_a2_repo_planted_node_and_npm_never_chosen(self):
        from mokata.ide import review_context as RC
        node = shutil.which("node")
        root = self.root("npm test --")
        # repo plants node.exe (a copy of real node) + node_modules/npm + .bin/npm.cmd at its root
        shutil.copy(node, os.path.join(root, "node.exe"))
        mark = os.path.join(root, "M.txt")
        self.fake_npm(root, mark)
        _w(root, "node_modules/.bin/npm.cmd", "@echo off\r\n")
        tools = os.path.join(root, "tools")
        os.makedirs(tools)
        shutil.copy(node, os.path.join(tools, "node.exe"))
        self.fake_npm(tools, mark)
        path = os.pathsep.join([root, tools, os.path.join(root, "node_modules", ".bin"), ".",
                                "node_modules\\.bin", os.environ["PATH"]])
        old = os.getcwd()
        os.chdir(root)
        self.addCleanup(os.chdir, old)
        head, why = RC.resolve_runner(root, "npm", path)
        say("planted resolve ->", head, why)
        self.assertIsNotNone(head, why)
        for a in head:
            self.assertFalse(os.path.normcase(os.path.realpath(a)).startswith(
                os.path.normcase(root)), f"repo chose {a}")
        got = RC.run_tests(root, ["npm", "--version", "--"], ["t1"],
                           environ={**os.environ, "PATH": path})
        say("planted run ->", got["tests"][0]["state"], os.path.exists(mark))
        self.assertFalse(os.path.exists(mark), "repo's npm ran")

    def test_a3_junction_nvm_layout(self):
        """nvm-windows/fnm: the PATH entry is a junction/symlink to the versioned dir."""
        from mokata.ide import review_context as RC
        node = shutil.which("node")
        real = self.tmp("v4nvm-")
        ver = os.path.join(real, "v20")
        os.makedirs(ver)
        shutil.copy(node, os.path.join(ver, "node.exe"))
        mark = os.path.join(real, "M.txt")
        self.fake_npm(ver, mark)
        link = os.path.join(self.tmp("v4link-"), "nodejs")
        r = subprocess.run(["cmd", "/c", "mklink", "/J", link, ver], capture_output=True, text=True)
        say("mklink", r.returncode, r.stdout.strip(), r.stderr.strip())
        root = self.root("npm test --")
        path = os.pathsep.join([link, os.path.join(os.environ.get("SystemRoot", "C:\\Windows"),
                                                   "System32")])
        head, why = RC.resolve_runner(root, "npm", path)
        say("junction resolve ->", head, why)
        self.assertIsNotNone(head, why)
        got = RC.run_tests(root, ["npm", "test", "--"], ["t1"], environ={**os.environ, "PATH": path})
        with open(mark, encoding="utf-8") as fh:
            m = fh.read()
        say("junction run ->", got["tests"][0]["state"], repr(m))
        self.assertIn("JS|test|--|t1", m)

    # ---- argument quoting through node
    def test_a4_ids_round_trip_through_node(self):
        from mokata.ide import review_context as RC
        node = shutil.which("node")
        prefix = self.tmp("v4q-")
        mark = os.path.join(prefix, "M.txt")
        self.fake_npm(prefix, mark)
        root = self.root("npm test --")
        ids = ["a\\", "a\\\\", "t[x=1,y=2]", "C:\\x\\y.py::T::t", "a@b#c~d+e", "z/./x",
               "q\\\\\\", "w=a\\"]
        good = []
        for t in ids:
            try:
                RC.check_test_ids([t])
                good.append(t)
            except Exception as exc:          # noqa: BLE001
                say("id refused", repr(t), exc)
        for bad in ['a"b', "a%PATH%b", "a^b", "a&b", "a b", "a|b", "a<b", "a!b", "a`b", "a;b",
                    "a$b", "a(b)", "a'b"]:
            try:
                RC.check_test_ids([bad])
                say("id ACCEPTED", repr(bad))
            except Exception:                 # noqa: BLE001
                pass
        env = {**os.environ, "PATH": os.pathsep.join([prefix, os.path.dirname(node)])}
        got = RC.run_tests(root, ["npm", "test", "--"], good, environ=env)
        with open(mark, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        say("quoting states", [r["state"] for r in got["tests"]])
        for t, line in zip(good, lines):
            say("id", repr(t), "->", repr(line))
            self.assertEqual("JS|test|--|" + t, line)

    # ---- (3) explicit .cmd bypass variants
    def test_a5_explicit_shim_spellings(self):
        from mokata.ide import review_context as RC
        d = self.tmp("v4cmd-")
        mark = os.path.join(d, "HIT.txt")
        _w(d, "run.cmd", "@echo off\r\necho %%* >> \"%s\"\r\nexit /b 0\r\n" % mark)
        _w(d, "run.bat", "@echo off\r\necho %%* >> \"%s\"\r\nexit /b 0\r\n" % mark)
        base = os.path.join(d, "run")
        for spelled in (base + ".cmd", base + ".CmD", base + ".cmd.", base + ".cmd..",
                        base + ".bat.", base + ".cmd::$DATA", base):
            root = self.root(f'"{spelled}" x')
            tc = RC.test_command(root)
            if os.path.exists(mark):
                os.remove(mark)
            got = RC.run_tests(root, [spelled, "x"], ["t1"])
            hit = os.path.exists(mark)
            say("explicit", repr(spelled), "runnable", tc["runnable"], "run state",
                got["tests"][0]["state"], "BATCH RAN" if hit else "no batch",
                "|", (got["tests"][0].get("note") or "")[:160])

    def test_a6_bare_variants(self):
        from mokata.ide import review_context as RC
        root = self.root("npm test")
        for n in ("npm.cmd", "NPM", "Npm", "npm.exe", "npx"):
            head, why = RC.resolve_runner(root, n, os.environ.get("PATH"))
            say("bare", n, "->", head, "|", why)

    # ---- (4) merge_suite
    def test_a7_merge_suite_paths(self):
        """What the gated merge's full-suite runner executes for `npm test` and `mvn test`."""
        from test_p21_gated_merge import _Merge  # noqa
        from mokata import merge_suite as MS
        from mokata.ide import review_context as RC

        node = shutil.which("node")
        outer = self

        class M(_Merge):
            def runTest(self):
                pass
        m = M()
        m.setUp()
        self.addCleanup(m.tearDown)
        prefix = self.tmp("v4ms-")
        mark = os.path.join(prefix, "M.txt")
        self.fake_npm(prefix, mark)
        _w(prefix, "mvn.cmd", "@echo off\r\necho MVN %%* >> \"%s\"\r\nexit /b 0\r\n" % mark)
        path = os.pathsep.join([prefix, os.path.dirname(node),
                                os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "System32")])
        oldp = os.environ["PATH"]
        os.environ["PATH"] = path
        self.addCleanup(os.environ.__setitem__, "PATH", oldp)
        for cmd in ("npm test", "mvn -q test"):
            mf = os.path.join(m.main, ".mokata", "manifest.json")
            with open(mf, encoding="utf-8") as fh:
                data = json.load(fh)
            data["settings"]["baseline"]["test_command"] = cmd
            with open(mf, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            wt = m.session(name="s" + cmd[:3], sid="S" + cmd[:3], run="R" + cmd[:3])
            tc = RC.test_command(m.main)
            say("merge preflight", cmd, tc["runnable"], tc["note"][:200])
            if os.path.exists(mark):
                os.remove(mark)
            try:
                res = MS.run_full_suite(m.main, wt)
                say("merge run", cmd, res["passed"], res["exit_code"])
            except Exception as exc:          # noqa: BLE001
                say("merge run", cmd, "RAISED", type(exc).__name__, str(exc)[:300])
            if os.path.exists(mark):
                with open(mark, encoding="utf-8") as fh:
                    say("merge marker", cmd, repr(fh.read()))
            else:
                say("merge marker", cmd, "none")
        # cwd-first: a batch named like the runner in the PROCESS cwd
        cwdtool = self.tmp("v4cwd-")
        _w(cwdtool, "npm.bat", "@echo off\r\necho CWDBAT >> \"%s\"\r\n" % mark)
        old = os.getcwd()
        os.chdir(cwdtool)
        self.addCleanup(os.chdir, old)
        say("shutil.which npm with cwd planted:", shutil.which("npm", path=path))


if __name__ == "__main__":
    unittest.main()
