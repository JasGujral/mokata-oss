"""Verifier B5 attack probe (macOS + Windows). Prints evidence lines starting with 'V5 '. Not shipped.
Assertions are the CLAIMS under test; a failure is a refutation, printed with its evidence."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401

from mokata.init import init_repo

NT = os.name == "nt"


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
    print("V5", *a, flush=True)


def sysdirs():
    out = []
    for n in ("git", "node", "python"):
        w = shutil.which(n)
        if w:
            out.append(os.path.dirname(w))
    if NT:
        out.append(os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "System32"))
    else:
        out += ["/usr/bin", "/bin"]
    return out


class _Base(unittest.TestCase):
    def tmp(self, p="v5-"):
        d = os.path.realpath(tempfile.mkdtemp(prefix=p))
        self.addCleanup(lambda: shutil.rmtree(d, True))
        return d

    def env_clean(self):
        bak = {k: os.environ.pop(k) for k in ("VIRTUAL_ENV", "CONDA_PREFIX") if k in os.environ}
        self.addCleanup(os.environ.update, bak)

    def set_path(self, *dirs):
        old = os.environ.get("PATH", "")
        os.environ["PATH"] = os.pathsep.join(dirs)
        self.addCleanup(os.environ.__setitem__, "PATH", old)


class B1_PanelSpawns(_Base):
    """Every spawn during panel GETs (page/filter) — anything but git is a finding."""

    def test_b1_no_spawn_but_git_on_any_panel_get(self):
        import test_p7_ide_review_context as T
        self.env_clean()
        d = self.tmp("v5panel-")
        T.plant(d, tests={"t_ok": ["AC-1"]}, green=["t_ok"])
        T._set_command(d, "pytest -q")
        for f in ("pyproject.toml", "Pipfile", "pdm.lock", "uv.lock", "environment.yml"):
            _w(d, f, "[project]\nname='x'\n" if f == "pyproject.toml" else "\n")
        # user-PATH tools that log if ever started
        bindir = self.tmp("v5userbin-")
        log = os.path.join(bindir, "calls.log")
        for name in ("poetry", "pipenv", "uv", "pdm", "hatch", "conda", "pytest-not"):
            if NT:
                node = shutil.which("node")
                if node:
                    shutil.copy(node, os.path.join(bindir, name + ".exe"))
            else:
                _w(bindir, name, f"#!/bin/sh\necho \"$0 $*\" >> '{log}'\nexit 1\n")
                os.chmod(os.path.join(bindir, name), 0o755)
        self.set_path(bindir, *sysdirs())
        seen = []
        real = subprocess.Popen.__init__

        def spy(self_, args, *a, **k):
            seen.append(args if isinstance(args, (list, tuple)) else [args])
            return real(self_, args, *a, **k)
        c = T._client(d)
        with mock.patch.object(subprocess.Popen, "__init__", spy):
            for q in ("", "&page=2", "&status=red", "&status=green", "&ac=AC-1", "&tpage=2",
                      "&per=5"):
                r = c.get(f"/api/review/tests?run={T.RUN}{q}")
                self.assertEqual(200, r.status_code, r.text)
            n0 = len(seen)
            subprocess.run(["git", "--version"], capture_output=True)
            say("spy control (git --version seen):", len(seen) == n0 + 1)
            seen.pop()
        names = sorted({os.path.basename(str(a[0])).lower() for a in seen})
        say("panel GET spawn argv0 set:", names, "count", len(seen))
        say("user-PATH tool log exists:", os.path.exists(log))
        self.assertTrue(all(n in ("git", "git.exe") for n in names), names)
        self.assertFalse(os.path.exists(log))


class B2_EnvMissingRuntime(_Base):
    """The panel says runnable (poetry/pipenv note) — what does a click then record?"""

    def _go(self, with_poetry):
        import test_p7_ide_review_context as T
        from mokata import tdd_state
        self.env_clean()
        d = self.tmp("v5fr-")
        T.plant(d, tests={"t_ok": ["AC-1"]}, green=["t_ok"])
        T._set_command(d, "mk-v5-no-such-runner -q")
        _w(d, "pyproject.toml", "[project]\nname='x'\n")
        dirs = list(sysdirs())
        if with_poetry:
            b = self.tmp("v5poetry-")
            if NT:
                shutil.copy(shutil.which("node"), os.path.join(b, "poetry.exe"))
            else:
                _w(b, "poetry", "#!/bin/sh\nexit 1\n")
                os.chmod(os.path.join(b, "poetry"), 0o755)
            dirs.insert(0, b)
        self.set_path(*dirs)
        c = T._client(d)
        cmd = c.get(f"/api/review/tests?run={T.RUN}").json()["command"]
        say(f"[poetry={with_poetry}] panel:", cmd["runnable"], cmd["note"][:160])
        r = c.post("/api/review/tests/run", json={"run": T.RUN, "tests": ["t_ok"]}, headers=T.ORIGIN)
        say(f"[poetry={with_poetry}] POST", r.status_code)
        if r.status_code == 200:
            res = T.wait_job(c, r.json()["id"])["result"]
            say(f"[poetry={with_poetry}] result:", json.dumps(res["tests"]))
        red, green = tdd_state.load(T._store(d, T.RUN), T.RUN)
        p = c.get(f"/api/review/tests?run={T.RUN}").json()
        say(f"[poetry={with_poetry}] tdd red:", sorted(red), "totals:", p["totals"],
            "last_suite.passed:", (p.get("last_suite") or {}).get("passed"))
        self.assertNotIn("t_ok", red, "a test that never started was recorded RED")

    def test_b2a_no_poetry_no_pipenv_installed(self):
        self._go(False)

    def test_b2b_poetry_installed_but_env_lacks_runner(self):
        self._go(True)


class B3_AskToolPath(_Base):
    """_ask_tool: a repo-planted poetry/pipenv never runs whatever PATH spells."""

    def test_b3_planted_tools_never_run(self):
        from mokata import project_env
        self.env_clean()
        d = self.tmp("v5ask-")
        _w(d, "pyproject.toml", "[project]\nname='x'\n")
        mark = os.path.join(d, "PLANTED_RAN.txt")
        sub = os.path.join(d, "tools")
        os.makedirs(sub)
        for where in (d, sub):
            for name in ("poetry", "pipenv"):
                if NT:
                    shutil.copy(shutil.which("node"), os.path.join(where, name + ".exe"))
                else:
                    _w(where, name, f"#!/bin/sh\necho \"$0\" >> '{mark}'\nexit 1\n")
                    os.chmod(os.path.join(where, name), 0o755)
        if NT:
            # poetry.exe/pipenv.exe are node: argv `env info -p` -> runs ./env ; `--venv` is a node flag
            _w(d, "env", "require('fs').appendFileSync(%s, 'ran '+process.execPath+'\\n');" % json.dumps(mark))
            _w(sub, "env", "require('fs').appendFileSync(%s, 'ran '+process.execPath+'\\n');" % json.dumps(mark))
        outside = self.tmp("v5link-")
        link = os.path.join(outside, "lnk")
        made = False
        try:
            if NT:
                subprocess.run(["cmd", "/c", "mklink", "/J", link, sub], check=True,
                               capture_output=True)
            else:
                os.symlink(sub, link)
            made = True
        except Exception as exc:  # noqa: BLE001
            say("link not made", exc)
        flipped = d.swapcase() if not NT else d.upper()
        here = os.getcwd()
        os.chdir(d)
        self.addCleanup(os.chdir, here)
        paths = [".", "", "tools", "." + os.sep + "tools", d, sub, d + os.sep + ".." + os.sep +
                 os.path.basename(d), sub + os.sep, '"' + sub + '"']
        if made:
            paths.append(link)
        if os.path.isdir(flipped):
            paths.append(os.path.join(flipped, "tools"))
        for p in paths:
            full = os.pathsep.join([p] + sysdirs())
            os.environ["PATH"] = full
            got = project_env.resolve(d, None, {"PATH": full})
            ran = os.path.exists(mark)
            say("ask_tool PATH", repr(p), "->", got, "planted ran:", ran)
            if ran:
                with open(mark, encoding="utf-8") as fh:
                    say("   mark:", fh.read().strip()[:300])
                os.remove(mark)
                self.fail(f"planted tool ran for PATH entry {p!r}")


class B4_MetaChars(_Base):
    """test_command metacharacters: refused on Windows; literal when allowed; POSIX unchanged."""

    REFUSE = ["a && b", "a || b", "a | b", "a < b", "a > b", "a & b", "a ^ b", "a\nb", "a\rb",
              "pytest -q &mkdir X", "pytest;x"]
    LITERAL = ["%USERNAME%", "!PATH!", "\"quoted arg\"", "%%", "a=b"]

    def root(self, cmd):
        d = self.tmp("v5meta-")
        init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
        _set_command(d, cmd)
        return d

    def test_b4a_refused_on_nt(self):
        from mokata.ide import review_context as RC
        bad = []
        for c in self.REFUSE:
            got = RC.test_command(self.root(c), nt=True)
            if got["runnable"]:
                bad.append(c)
        say("nt=True runnable despite metachar:", bad)
        self.assertEqual([], bad)

    def test_b4b_posix_unchanged(self):
        """nt=False: lone & ^ newline are NOT newly refused (they were allowed before)."""
        from mokata.ide import review_context as RC
        res = {}
        for c in ("pytest -k a^b", "pytest -k 'x & y'", "pytest\n-q", "pytest -q"):
            got = RC.test_command(self.root(c), nt=False)
            res[c] = (got["runnable"], got["note"][:60])
        say("nt=False:", json.dumps(res))
        self.assertTrue(res["pytest -k a^b"][0] or "not an executable" in res["pytest -k a^b"][1])
        self.assertNotIn("shell syntax", res["pytest -k 'x & y'"][1])
        self.assertNotIn("shell syntax", res["pytest\n-q"][1])

    @unittest.skipUnless(NT, "Windows: argv must arrive literally (no cmd.exe expansion)")
    def test_b4c_literal_args_through_run_tests_and_merge(self):
        from mokata.ide import review_context as RC
        d = self.tmp("v5lit-")
        out = os.path.join(d, "ARGV.json")
        _w(d, "rec.py", "import sys,json\nopen(%r,'a').write(json.dumps(sys.argv[1:])+'\\n')\n" % out)
        cmd = '"%s" rec.py %s' % (sys.executable, " ".join(self.LITERAL))
        root = self.root(cmd)
        shutil.copy(os.path.join(d, "rec.py"), os.path.join(root, "rec.py"))
        got = RC.test_command(root)
        say("literal preflight:", got["runnable"], got["note"][:120])
        argv = got["argv"]
        RC.run_tests(root, argv, ["t1"])
        with open(out, encoding="utf-8") as fh:
            line = fh.read().strip()
        say("literal argv arrived:", line)
        self.assertNotIn(os.environ.get("USERNAME", "\0"), line.replace("%USERNAME%", ""))


class B5_MavenOverride(_Base):
    """Does `mokata worktree merge-override` (the remedy the refusal and the Known limitation name)
    actually let a Maven-style merge (a run bound, no runner suite) proceed?"""

    def test_b5_override_unblocks_a_bound_run_without_suite(self):
        from test_p21_gated_merge import _Merge  # noqa
        from mokata import merge_gate as MG
        from mokata import merge_suite as MS

        class M(_Merge):
            def runTest(self):
                pass
        m = M()
        m.setUp()
        self.addCleanup(m.tearDown)
        wt = m.session()
        m.review(wt)
        if NT:
            prefix = self.tmp("v5mvn-")
            mark = os.path.join(prefix, "M.txt")
            _w(prefix, "mvn.cmd", "@echo off\r\necho MVN>> \"%s\"\r\nexit /b 0\r\n" % mark)
            self.set_path(prefix, *sysdirs())
            mf = os.path.join(m.main, ".mokata", "manifest.json")
            with open(mf, encoding="utf-8") as fh:
                data = json.load(fh)
            data["settings"]["baseline"]["test_command"] = "mvn -q test"
            with open(mf, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            try:
                MS.run_full_suite(m.main, wt)
                say("mvn suite: ran (unexpected)")
            except MS.SuiteError as exc:
                say("mvn suite refused:", str(exc)[:400])
            say("mvn.cmd started:", os.path.exists(mark))
        before = MG.plan_merge(m.main, wt)
        say("plan before override ok:", before.ok, [b["code"] for b in before.blocked])
        rec = MG.file_override(m.main, wt, "maven ran in my terminal, all green", actor="jas")
        say("override filed:", rec["id"], "waives", rec.get("waives"))
        after = MG.plan_merge(m.main, wt)
        say("plan after override ok:", after.ok, [b["code"] for b in after.blocked])
        self.assertTrue(after.ok, "merge-override, the named remedy, does not unblock the merge: "
                        + json.dumps([b["code"] for b in after.blocked]))


if __name__ == "__main__":
    unittest.main()
