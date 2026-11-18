"""0.0.29 H-a ROUND-3 VERIFIER attacks (refuting verifier). Real subprocesses, real CLI.
Goal: no path applies `doctor --fix` without a typed answer at a real TTY (a deliberate pty is out of scope)."""
from __future__ import annotations

import ast
import os
import stat
import subprocess
import sys
import threading
import unittest
from pathlib import Path

import _support  # noqa: F401

import test_h_a_verify_r2_attacks as R2

SRC = Path(__file__).resolve().parent.parent / "src"


class NoApplyWithoutATypedTtyAnswer(R2._Weak):
    def _cli(self, *argv, stdin=subprocess.DEVNULL, env_extra=None, strip=False, input=None, shell=None):
        env = dict(os.environ, HOME=self.home, USERPROFILE=self.home, PYTHONPATH=str(SRC), **(env_extra or {}))
        if strip:                       # the agent strips its own markers
            env.pop("CLAUDECODE", None)     # the one marker (H-a: Jas 2026-11-16 "Drop")
        if shell:
            return subprocess.run(shell.format(py=sys.executable, root=self.root), shell=True, env=env,
                                  capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
        cmd = [sys.executable, "-m", "mokata", "doctor", "--fix", *argv, "--path", self.root]
        if input is not None:
            return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=120, input=input)
        return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=120, stdin=stdin)

    def _nothing(self, r, before):
        self.assertEqual(before, self._snapshot(), r.stdout + r.stderr)
        self.assertNotEqual(0, r.returncode, r.stdout + r.stderr)

    def test_every_non_tty_stdin_with_markers_stripped(self):
        B_, _ = self._weak(copies=1)
        before = self._snapshot()
        tmp = Path(self.home) / "answers.txt"
        tmp.write_text("y\ny\ny\ny\n", encoding="utf-8")
        before = self._snapshot()
        for label, kw in (("devnull", {}), ("pipe-y", {"stdin": subprocess.PIPE, "input": "y\ny\ny\n"}),
                          ("file", {"stdin": tmp.open("rb")})):
            with self.subTest(label):
                r = self._cli(strip=True, **kw)
                self.assertIn("needs your answer typed at a terminal", r.stdout)
                self._nothing(r, before)

    def test_yes_pipe_and_heredoc_shell_forms(self):
        self._weak(copies=1)
        before = self._snapshot()
        for sh in ("yes | {py} -m mokata doctor --fix --path {root}",
                   "{py} -m mokata doctor --fix --path {root} <<< y",
                   "printf 'y\\ny\\n' | {py} -m mokata doctor --fix --path {root}",
                   "{py} -m mokata doctor --fix --path {root} < /dev/null"):
            with self.subTest(sh):
                r = self._cli(strip=True, shell=sh)
                self._nothing(r, before)

    def test_stdout_stderr_redirected_stdin_not_tty(self):
        self._weak()
        before = self._snapshot()
        r = self._cli(strip=True, shell="{py} -m mokata doctor --fix --path {root} >/dev/null 2>&1 </dev/null")
        self._nothing(r, before)

    def test_yes_spellings_are_argparse_errors(self):
        self._weak()
        before = self._snapshot()
        for sp in ("--yes", "-y", "--assume-yes", "--ye", "--no-confirm", "--force", "-f"):
            with self.subTest(sp):
                r = self._cli(sp, strip=True)
                self.assertEqual(2, r.returncode, r.stdout + r.stderr)
                self.assertEqual(before, self._snapshot())

    def test_env_vars_that_might_preanswer(self):
        self._weak()
        before = self._snapshot()
        env = {k: "1" for k in ("MOKATA_YES", "MOKATA_ASSUME_YES", "ASSUME_YES", "YES", "CI", "MOKATA_NONINTERACTIVE",
                                "MOKATA_APPROVE", "MOKATA_AUTO_APPROVE", "PYTHONINSPECT")}
        r = self._cli(strip=True, env_extra=env)
        self.assertIn("needs your answer typed at a terminal", r.stdout)
        if os.name == "nt":
            # 0.0.29 H-a xplat: PYTHONINSPECT hands the exit to the INTERPRETER's REPL, which on Windows
            # trusts `isatty()` on NUL, reads EOF and exits 0 — the code mokata raised is printed, not
            # returned. Nothing changed and mokata refused with 1: that is the property, so say it.
            self.assertEqual(before, self._snapshot(), r.stdout + r.stderr)
            self.assertIn("SystemExit: 1", r.stderr)
            return
        self._nothing(r, before)

    def test_mcp_doctor_tool_has_no_apply_surface(self):
        import inspect
        from mokata.mcp import tools_read
        sig = inspect.signature(tools_read.doctor)
        self.assertEqual({"path", "response_format"}, set(sig.parameters))
        names = [n for n in dir(tools_read) if "fix" in n.lower()]
        self.assertEqual([], names)

    def test_nothing_but_cmd_fix_reaches_apply_fix(self):
        hits = []
        for p in SRC.rglob("*.py"):
            t = p.read_text(encoding="utf-8")
            if "apply_fix(" in t:
                hits.append(p.name)
        self.assertEqual(["diagnostics.py"], sorted(hits))
        d = (SRC / "mokata/cli_commands/diagnostics.py").read_text(encoding='utf-8')
        self.assertEqual(1, d.count("apply_fix(root, steps") )

    def test_tty_check_precedes_every_read_and_apply_in_cmd_fix(self):
        from mokata.cli_commands import diagnostics
        import inspect
        src = inspect.getsource(diagnostics.cmd_fix)
        self.assertLess(src.index("_stdin_is_tty"), src.index("read_yes_no"))
        self.assertLess(src.index("read_yes_no"), src.index("apply_fix"))
        self.assertNotIn("/dev/tty", src)

    def test_answer_stream_is_the_checked_stream(self):
        # read_yes_no checks sys.stdin.isatty() and reads with input() -> sys.stdin: same stream.
        from mokata import prompt
        import inspect
        s = inspect.getsource(prompt.read_yes_no)
        self.assertIn("_stdin_is_tty()", s)
        self.assertNotIn("/dev/tty", s)
        self.assertNotIn("open(", s)

    def test_agent_shell_single_definition(self):
        offenders = []
        for p in SRC.rglob("*.py"):
            t = p.read_text(encoding="utf-8")
            if "CLAUDECODE" in t and p.name != "prompt.py":
                offenders.append(p.relative_to(SRC).as_posix())
        # Was a FINDING (Low): the IDE window's two checks spelled the marker themselves. H-a merge with
        # wave 2 (builder): `approve_codes` and `mokata ide --terminal` route through `prompt.agent_shell`;
        # no file but prompt.py names a marker any more.
        self.assertEqual([], sorted(offenders))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFO")
    def test_fifo_after_plan_nonblocking_plan_and_rebake(self):
        from mokata import harness_setup as HS
        B_, files = self._weak()
        os.unlink(files[0])
        os.mkfifo(files[0])
        res = {}

        def run():
            res["d"] = HS.file_digest(files[0])
            res["b"] = HS.baked_tokens(Path(files[0]))
            res["f"] = HS.foreign_hooks(Path(files[0]))
            try:
                HS.rebake_token(Path(files[0]), {"token": "x" * 40, "port": 1}, self.root, expect="0" * 64)
            except Exception as e:  # noqa: BLE001
                res["e"] = type(e).__name__
            try:
                HS.rebake_token(Path(files[0]), {"token": "x" * 40, "port": 1}, self.root)
            except Exception as e:  # noqa: BLE001
                res["e2"] = type(e).__name__
        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(30)
        if t.is_alive():
            try:
                os.close(os.open(files[0], os.O_WRONLY | os.O_NONBLOCK))
            except OSError:
                pass
            self.fail("FIFO hung")
        self.assertIsNone(res["d"])
        self.assertEqual([], res["b"])
        self.assertEqual([], res["f"])
        self.assertIn("e", res)
        self.assertIn("e2", res)
        self.assertTrue(stat.S_ISFIFO(os.lstat(files[0]).st_mode))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFO")
    def test_symlink_to_fifo_and_dir_do_not_hang(self):
        from mokata import harness_setup as HS
        p = Path(self.root) / "x.json"
        os.mkfifo(p)
        t0 = Path(self.root) / "ln.json"
        os.symlink(p, t0)
        res = {}
        th = threading.Thread(target=lambda: res.update(d=HS.file_digest(t0), b=HS.baked_tokens(t0)), daemon=True)
        th.start()
        th.join(20)
        self.assertFalse(th.is_alive(), "symlink-to-FIFO hung")
        d = Path(self.root) / "adir.json"
        d.mkdir()
        self.assertIsNone(HS.file_digest(d))
        self.assertEqual([], HS.baked_tokens(d))


class AstSweepAliasAndAbsoluteImports(unittest.TestCase):
    """Extra import spellings against the builder's sweep (`sites_in`). Each is an unguarded reader."""

    def _sites(self, code):
        import test_h4_fix_is_gated_and_rotates as B
        return B.RefusedRecordNeverTracebacks.sites_in("x/probe.py", code)

    def test_more_spellings_are_seen(self):
        probes = {
            "from-ide-server-as": "from mokata.ide import server as s\ndef f(r):\n    return s.read_record(r)\n",
            "pkg-attr-chain": "from mokata import ide\ndef f(r):\n    return ide.server.read_record(r)\n",
            "relative-bus-as": "from ..ide import bus as b\ndef f(r):\n    return b.read_bridge(r)\n",
            "import-bus-as": "import mokata.ide.bus as b\ndef f(r):\n    return b.read_bridge(r)\n",
            "lazy-inside-func": "def f(r):\n    from mokata.ide.server import read_record as rr\n    return rr(r)\n",
            "lambda": "from mokata.ide.server import read_record\ng = lambda r: read_record(r)\n",
            "method-ctx": "from mokata.ide.server import read_record\nclass C:\n    def m(s, r):\n        return read_record(r)\n",
        }
        for label, code in probes.items():
            with self.subTest(label):
                self.assertTrue(self._sites(code), label)

    @unittest.expectedFailure     # FINDING Low: pins the gap
    def test_assignment_alias_is_seen(self):
        # rebinding the function to a new name before calling it
        code = "from mokata.ide.server import read_record\nfn = read_record\ndef f(r):\n    return fn(r)\n"
        self.assertTrue(self._sites(code), "FINDING (Low): an assignment alias escapes the sweep")


if __name__ == "__main__":
    unittest.main()
