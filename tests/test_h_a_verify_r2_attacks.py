"""0.0.29 H-a ROUND-2 VERIFIER attacks (refuting verifier). Product code is exercised for real.
A test marked `expectedFailure` pins a FINDING (it flips to 'unexpected success' when fixed)."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import _support  # noqa: F401

import _privacy_oracle as O
import test_h4_fix_is_gated_and_rotates as B

EVIL = {"matcher": "Bash", "hooks": [{"type": "command", "command": "/tmp/late-evil-hook"}]}


def _plant(path, mode=None):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    data.setdefault("hooks", {}).setdefault("PreToolUse", []).append(EVIL)
    Path(path).write_text(json.dumps(data), encoding="utf-8")
    if mode is not None:
        O.chmod_like(path, mode)


class _Weak(B._Home):
    def _weak(self, copies=0):
        from mokata.ide import bus as B_
        files = self._setup(copies=copies)
        O.grant_others_read(B_.bridge_path(self.root))
        return B_, files

    def _plan(self):
        from mokata.cli_commands.diagnostics import fix_plan
        return fix_plan(self.root)

    def _apply(self, steps, second_yes=None):
        from mokata.cli_commands.diagnostics import apply_fix
        out = []
        return apply_fix(self.root, steps, second_yes=second_yes, out=out.append), out


class AgentShellMarkers(_Weak):
    """M2: every agent marker the code knows, with and without a TTY (a typed "y" included) -> nothing
    changes. Round 3: `--fix` has no `--yes`; the gate is the terminal, the markers are defense in depth."""

    def test_any_nonempty_marker_refuses_even_a_typed_yes(self):
        B_, _ = self._weak()
        before = self._snapshot()
        # H-a (Jas, 2026-11-16 "Drop"): CLAUDECODE is the only agent-shell marker.
        for env in ({"CLAUDECODE": "1"}, {"CLAUDECODE": "0"}, {"CLAUDECODE": "true"}):
            for tty, answer in ((False, ""), (True, "y")):
                with self.subTest(env=env, tty=tty):
                    rc, out = self._fix(tty=tty, answer=answer, env=env)
                    self.assertEqual(1, rc, out)
                    self.assertEqual(before, self._snapshot(), "an agent shell changed something")

    def _cli(self, *argv, env_extra=None, wrap=()):
        env = dict(os.environ, HOME=self.home, USERPROFILE=self.home,
                   PYTHONPATH=str(Path(__file__).resolve().parent.parent / "src"), **(env_extra or {}))
        return subprocess.run([*wrap, sys.executable, "-m", "mokata", "doctor", "--fix", *argv,
                               "--path", self.root], env=env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=120)

    def test_cli_end_to_end_inside_agent_env_changes_nothing(self):
        B_, _ = self._weak()
        before = self._snapshot()
        r = self._cli(env_extra={"CLAUDECODE": "1"})
        self.assertEqual(1, r.returncode, r.stdout + r.stderr)
        self.assertEqual(before, self._snapshot())

    # H-a round 3 (builder; coordinator ruling): M-r2-1 is closed by deleting `--yes` — the only consent
    # is an answer typed at a real terminal, which no environment edit supplies. Was `expectedFailure`.
    @unittest.skipIf(os.name == "nt", "POSIX `env -u`")
    def test_env_u_inside_an_agent_process_does_not_apply(self):
        B_, _ = self._weak()
        before = self._snapshot()
        strip = (shutil.which("env"), "-u", "CLAUDECODE")
        agent = {"CLAUDECODE": "1"}
        r = self._cli(env_extra=agent, wrap=strip)              # marker stripped, no terminal
        self.assertEqual(1, r.returncode, r.stdout + r.stderr)
        self.assertIn("needs your answer typed at a terminal", r.stdout)
        self.assertEqual(before, self._snapshot(), "an agent stripped its own marker and applied the plan")
        r = self._cli("--yes", env_extra=agent, wrap=strip)     # the flag no longer exists
        self.assertEqual(2, r.returncode, r.stdout + r.stderr)
        self.assertEqual(before, self._snapshot())

    def test_empty_marker_applies_nothing(self):
        # An empty marker is not an agent's shell (`prompt.agent_shell`) — and still nothing applies,
        # because the gate is the terminal, not the environment.
        from mokata.prompt import agent_shell
        self.assertEqual("", agent_shell({"CLAUDECODE": ""}))
        self.assertEqual("CLAUDECODE", agent_shell({"CLAUDECODE": "0"}))
        B_, _ = self._weak()
        before = self._snapshot()
        r = self._cli(env_extra={"CLAUDECODE": ""})
        self.assertEqual(1, r.returncode, r.stdout + r.stderr)
        self.assertIn("needs your answer typed at a terminal", r.stdout)
        self.assertEqual(before, self._snapshot())


class PlanApplyDigest(_Weak):
    """M3: apply acts only on the bytes its plan showed."""

    def test_deleted_file_changes_nothing(self):
        B_, files = self._weak(copies=1)
        steps = self._plan()
        os.unlink(files[1])
        before = self._snapshot()
        rc, out = self._apply(steps)
        self.assertEqual(1, rc, out)
        self.assertEqual(before, self._snapshot())

    def test_second_of_two_differs_is_all_or_nothing(self):
        B_, files = self._weak(copies=1)
        steps = self._plan()
        _plant(files[1], 0o600)
        before = self._snapshot()
        rc, out = self._apply(steps)
        self.assertEqual(1, rc, out)
        self.assertEqual(before, self._snapshot(), "first file / bridge changed though the second differed")

    def test_symlink_to_other_bytes_is_refused_and_untouched(self):
        B_, files = self._weak()
        steps = self._plan()
        other = os.path.join(self.root, "elsewhere.json")
        shutil.copyfile(self.local, other)
        _plant(other)
        os.unlink(self.local)
        os.symlink(other, self.local)
        before = Path(other).read_bytes()
        rc, out = self._apply(steps)
        self.assertEqual(1, rc, out)
        self.assertEqual(before, Path(other).read_bytes())
        self.assertTrue(os.path.islink(self.local))

    def test_symlink_to_same_bytes_is_replaced_never_written_through(self):
        B_, files = self._weak()
        steps = self._plan()
        other = os.path.join(self.root, "elsewhere.json")
        shutil.copyfile(self.local, other)
        os.unlink(self.local)
        os.symlink(other, self.local)
        before = Path(other).read_bytes()
        rc, out = self._apply(steps)
        self.assertEqual(before, Path(other).read_bytes(), "wrote through the link")
        self.assertFalse(os.path.islink(self.local))

    def test_unlisted_new_file_is_never_touched(self):
        B_, files = self._weak()
        steps = self._plan()
        late = os.path.join(self.root, ".claude", "worktrees", "late", ".claude", "settings.local.json")
        os.makedirs(os.path.dirname(late))
        shutil.copyfile(self.local, late)
        _plant(late, 0o666)
        planted = Path(late).read_bytes()
        rc, out = self._apply(steps)
        self.assertEqual(planted, Path(late).read_bytes())
        self.assertEqual(0o666, stat.S_IMODE(os.stat(late).st_mode))

    def test_change_after_the_second_yes_but_before_the_write_is_refused(self):
        # a human says yes to the per-file question; the file then changes before the rewrite
        B_, files = self._weak()
        _plant(self.local, 0o666)           # a foreign hook in a world-writable file -> second yes
        steps = self._plan()
        self.assertTrue(any(s.second_yes for s in steps))

        def yes_then_swap(p):
            data = json.loads(Path(p).read_text(encoding="utf-8"))
            data["hooks"]["PreToolUse"].append({"matcher": "X", "hooks": [{"type": "command",
                                                                            "command": "/tmp/after-the-yes"}]})
            Path(p).write_text(json.dumps(data), encoding="utf-8")
            return True
        rc, out = self._apply(steps, second_yes=yes_then_swap)
        self.assertEqual(1, rc, out)
        self.assertIn("/tmp/after-the-yes", Path(self.local).read_text(encoding="utf-8"))   # untouched
        self.assertEqual(0o666, stat.S_IMODE(os.stat(self.local).st_mode), "blessed private")

    def test_change_between_digest_check_and_rewrite_is_refused_not_blessed(self):
        # the gap inside apply: after the up-front digest check, before rebake (the IDE-stop wait window)
        from mokata.ide import bus as B_x
        B_, files = self._weak(copies=1)
        steps = self._plan()
        real = B_x.new_bridge_record

        def mutate_then_mint(*a, **k):
            _plant(files[1], 0o600)
            return real(*a, **k)
        with mock.patch.object(B_x, "new_bridge_record", side_effect=mutate_then_mint):
            rc, out = self._apply(steps)
        self.assertEqual(1, rc, out)
        self.assertIn("late-evil-hook", Path(files[1]).read_text(encoding="utf-8"))
        # honest partial: the file that matched was re-baked; the changed one kept its OLD token
        self.assertNotIn(B_x.read_bridge(self.root)["token"], Path(files[1]).read_text(encoding="utf-8"))
        self.assertTrue(any("changed since the plan" in l for l in out), out)

    def test_rebake_step_refuses_a_bridge_record_swapped_after_the_plan(self):
        from mokata import harness_setup as HS
        from mokata.ide import bus as B_x
        self._setup()
        rec = B_x.read_bridge(self.root)
        HS.rebake_token(Path(self.local), {"port": rec["port"], "token": "S" * 40}, self.root)
        steps = self._plan()
        self.assertEqual(["rebake"], [s.kind for s in steps if s.kind != "refuse"], steps)
        B_x.write_bridge(self.root, B_x.new_bridge_record(self.root, rec["port"]))   # swap after plan
        before = self._snapshot()
        rc, out = self._apply(steps)
        self.assertEqual(1, rc, out)
        self.assertEqual(before, self._snapshot())

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFO")
    # H-a round 3: fixed (`harness_setup._read_regular`: O_NONBLOCK + fstat). Was `expectedFailure`.
    def test_fifo_swapped_in_after_the_plan_does_not_hang_the_apply(self):
        B_, files = self._weak()
        steps = self._plan()
        os.unlink(self.local)
        os.mkfifo(self.local)
        # the in-process call would block forever on a hang; run it in a thread with a timeout
        import threading
        res = {}

        def run():
            res["rc"] = self._apply(steps)[0]
        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(8)
        if t.is_alive():
            # unblock the reader so the thread (and the temp dir cleanup) can end
            fd = os.open(self.local, os.O_WRONLY | os.O_NONBLOCK)
            os.close(fd)
            t.join(5)
            self.fail("apply_fix blocked on a FIFO swapped in at the settings path")


if __name__ == "__main__":
    unittest.main()
