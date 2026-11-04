"""Verifier B6 attack probe (Windows). Prints evidence lines starting with 'V6 '. Not shipped."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest

import _support  # noqa: F401


def say(*a):
    print("V6", *a, flush=True)


def _w(root, name, text):
    p = os.path.join(root, name)
    with open(p, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return p


@unittest.skipUnless(os.name == "nt", "Windows probe")
class Attack(unittest.TestCase):
    def tmp(self, prefix):
        d = os.path.realpath(tempfile.mkdtemp(prefix=prefix))
        self.addCleanup(lambda: shutil.rmtree(d, True))
        return d

    def test_v6_1_trailing_dot_spellings_are_never_recorded_red(self):
        """Report: 'Covers trailing-dot spellings.' A runner that never ran must not reach the
        TDD red set nor fail last_suite."""
        import test_p7_ide_review_context as T
        from mokata import tdd_state
        from mokata.ide import review_context as RC
        from mokata.test_runs import read_test_record
        bad = []
        for suffix in (".cmd.", ".cmd..", ".bat.", ".cmd"):
            d = self.tmp("v6dot-")
            T.plant(d, tests={"t_ok": ["AC-1"]}, green=["t_ok"], suite=(True, ["t_ok"]))
            mark = os.path.join(d, "HIT.txt")
            _w(d, "run.cmd", "@echo off\r\necho %%* >> \"%s\"\r\nexit /b 0\r\n" % mark)
            _w(d, "run.bat", "@echo off\r\necho %%* >> \"%s\"\r\nexit /b 0\r\n" % mark)
            spelled = os.path.join(d, "run" + suffix)
            T._set_command(d, f'"{spelled}" -q')
            tc = RC.test_command(d)
            before = read_test_record(d, T.RUN)["last_suite"]
            got = RC.run_tests(d, [spelled, "-q"], ["t_ok"],
                               on_done=lambda res, done: RC.record_rerun(d, T.RUN, "x", res, done))
            red, green = tdd_state.load(T._store(d, T.RUN), T.RUN)
            after = read_test_record(d, T.RUN)["last_suite"]
            t = got["tests"][0]
            say("dot", repr(suffix), "preflight runnable", tc["runnable"], "| state", t["state"],
                "rc", t.get("rc"), "| batch ran", os.path.exists(mark), "| red set", sorted(red),
                "| last_suite.passed", (after or {}).get("passed"), "changed", before != after,
                "|", (t.get("note") or "")[:120], "| out", repr(got["output"][-160:]))
            if "t_ok" in red:
                bad.append(suffix)
        self.assertEqual([], bad, "a runner that never ran was recorded RED")

    def test_v6_2_preflight_claim_on_windows(self):
        """The poetry/pipenv claim only with a real poetry.exe/pipenv.exe on an absolute PATH entry;
        a .cmd poetry or a repo-planted one does not count; nothing is started."""
        from mokata.ide import review_context as RC
        from mokata.init import init_repo
        where = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "where.exe")
        out = []
        for label, kind, inrepo in (("none", None, False), ("poetry.exe", "poetry.exe", False),
                                    ("pipenv.exe", "pipenv.exe", False),
                                    ("poetry.cmd only", "poetry.cmd", False),
                                    ("poetry.exe in repo", "poetry.exe", True)):
            d = self.tmp("v6pre-")
            init_repo(root=d, profile="standard", assume_yes=True, out=lambda *a, **k: None)
            import test_p7_ide_review_context as T
            T._set_command(d, "mk-v6-no-such-runner -q")
            _w(d, "pyproject.toml", "[project]\nname='x'\n")
            ub = os.path.join(d, "tools") if inrepo else self.tmp("v6ub-")
            os.makedirs(ub, exist_ok=True)
            if kind and kind.endswith(".exe"):
                shutil.copy(where, os.path.join(ub, kind))
            elif kind:
                _w(ub, kind, "@echo off\r\nexit /b 0\r\n")
            old = os.environ["PATH"]
            sysdir = os.path.dirname(where)
            os.environ["PATH"] = ub + os.pathsep + sysdir
            try:
                got = RC.test_command(d)
            finally:
                os.environ["PATH"] = old
            claim = "poetry/pipenv environment" in got["note"]
            say("pre", label, "runnable", got["runnable"], "claim", claim, "|", got["note"][:110])
            out.append((label, got["runnable"], claim))
        self.assertEqual([("none", False, False), ("poetry.exe", True, True),
                          ("pipenv.exe", True, True), ("poetry.cmd only", False, False),
                          ("poetry.exe in repo", False, False)], out)


if __name__ == "__main__":
    unittest.main()
