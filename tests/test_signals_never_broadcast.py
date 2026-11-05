"""mokata never sends a signal to "everyone": no group kill or pid kill reaches 0, 1, a negative
number, a non-integer, or mokata's own process / group.

The 0.0.27 release CI lost a Linux runner every run. A test spawned a language server through a
mocked `Popen`, so the server's `pid` was a test double whose integer value is 1; tearing it down
called `os.killpg(1, SIGKILL)`. On Linux `killpg(1, sig)` is `kill(-1, sig)`: SIGKILL to every
process the user owns, the CI runner agent included (macOS refuses it, so it passed there). The
same call from a real IDE fed a bad pid would kill the user's whole session. The rule is held at
the one door (`quiet_spawn.signal_group` / `signal_pid`) and every kill path goes through it.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import _support  # noqa: F401  (puts src/ on the path)

import ast
import io
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from mokata import quiet_spawn
from mokata.ide import lsp as L
from mokata.ide import review_context as RC

SRC = Path(__file__).resolve().parent.parent / "src" / "mokata"
POSIX = os.name != "nt"


def _unsafe_numbers():
    """Every value that must never be a signal target: the broadcast/self numbers, a test double
    whose int is 1, a bool (an int subclass), and mokata's own pid and group."""
    vals = [0, 1, -1, -12345, True, mock.MagicMock(), "123", 12.0, None, os.getpid()]
    if POSIX:
        vals.append(os.getpgrp())
    return vals


class _Spy:
    """Records os.killpg / os.kill calls and sends NOTHING (a refused value must not reach the OS,
    and a wrong one must not kill this test run)."""

    def __init__(self):
        self.calls = []

    def __call__(self, target, sig):
        self.calls.append((target, sig))


class TheDoorRefusesEveryUnsafeTarget(unittest.TestCase):

    def test_a_group_signal_never_reaches_a_broadcast_or_our_own_group(self):
        spy = _Spy()
        with mock.patch.object(os, "killpg", spy, create=True):
            for v in _unsafe_numbers():
                self.assertFalse(quiet_spawn.signal_group(v, signal.SIGKILL), repr(v))
        self.assertEqual([], spy.calls)

    def test_a_pid_signal_never_reaches_a_broadcast_or_ourselves(self):
        spy = _Spy()
        with mock.patch.object(os, "kill", spy):
            for v in _unsafe_numbers():
                if POSIX and v == os.getpgrp() and v != os.getpid():
                    continue                          # our group's number may be another pid
                self.assertFalse(quiet_spawn.signal_pid(v, signal.SIGTERM), repr(v))
        self.assertEqual([], spy.calls)

    @unittest.skipUnless(POSIX, "process groups are POSIX")
    def test_a_real_group_of_ours_is_still_ended(self):
        """The control: the guard does not stop mokata ending a group it started."""
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"],
                                start_new_session=True)
        try:
            self.assertTrue(quiet_spawn.signal_group(proc.pid, signal.SIGKILL))
            self.assertEqual(-signal.SIGKILL, proc.wait(timeout=60))
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


class EveryKillPathGoesThroughTheDoor(unittest.TestCase):
    """The product's kill paths, fed the CI's exact value (a mocked pid) and the other unsafe
    numbers: nothing reaches os.killpg / os.kill."""

    def _fake_proc(self, pid):
        proc = mock.MagicMock()
        proc.pid = pid
        proc.poll.return_value = 0                  # already exited: only the group is left
        proc.wait.return_value = 0
        return proc

    def test_the_language_server_tree_kill(self):
        for v in _unsafe_numbers():
            spy = _Spy()
            with mock.patch.object(os, "killpg", spy, create=True):
                L._kill_tree(self._fake_proc(v), v, None)
            self.assertEqual([], spy.calls, repr(v))

    def test_the_test_runner_tree_kill(self):
        for v in _unsafe_numbers():
            spy = _Spy()
            with mock.patch.object(os, "killpg", spy, create=True):
                RC._kill_tree(self._fake_proc(v), leftovers_only=True)
            self.assertEqual([], spy.calls, repr(v))

    def test_the_ide_stop_by_pid(self):
        from mokata.ide import launch
        for v in (0, 1, -1, os.getpid()):
            spy = _Spy()
            with mock.patch.object(os, "kill", spy), \
                    mock.patch.object(launch, "running", lambda root: None):
                launch._stop(v, "/nonexistent", wait=0.01)
            self.assertEqual([], spy.calls, repr(v))

    @unittest.skipUnless(POSIX, "the terminal is a POSIX pty")
    def test_the_terminal_close(self):
        from mokata.ide import terminal as TM
        for v in _unsafe_numbers():
            spy = _Spy()
            host = TM.Terminal.__new__(TM.Terminal)   # the close path only, no pty
            host.proc = mock.MagicMock()
            host.proc.pid = v
            host.proc.poll.return_value = 0
            host._closed = False
            host._cond = __import__("threading").Condition()
            host._reader = mock.MagicMock()
            host.master = -1                          # os.close(-1) raises OSError: swallowed
            host.exit_code = 0
            with mock.patch.object(os, "killpg", spy, create=True):
                host.close()
            self.assertEqual([], spy.calls, repr(v))

    def test_the_ci_case_a_server_spawned_through_a_mocked_popen_is_torn_down_safely(self):
        """The exact 0.0.27 sequence: `Popen` mocked, the server started, then torn down."""
        spy = _Spy()
        d = tempfile.mkdtemp()
        with mock.patch("mokata.ide.lsp.subprocess.Popen") as popen, \
                mock.patch("mokata.ide.lsp._resume_process"), \
                mock.patch.object(os, "killpg", spy, create=True):
            popen.return_value.stdin = io.BytesIO()
            popen.return_value.stdout = io.BytesIO()
            popen.return_value.stderr = io.BytesIO()
            popen.return_value.poll.return_value = 0
            popen.return_value.wait.return_value = 0
            srv = L.Server(d, "python", "fake", ["x"], clock=time.monotonic)
            srv.request = lambda *a, **k: {"capabilities": {}}
            srv.notify = lambda *a, **k: None
            try:
                srv.start()
            except Exception:
                pass
            srv.kill("test")
            time.sleep(0.5)                          # the exit watcher's own teardown
        self.assertEqual([], spy.calls)


class NoOtherDoorExists(unittest.TestCase):
    """Derived from the tree: outside `quiet_spawn`, no `os.killpg(...)` at all, and no
    `os.kill(...)` with a signal other than the literal 0 (a liveness probe)."""

    def test_every_group_or_pid_signal_in_src_uses_the_door(self):
        bad = []
        for path in sorted(SRC.rglob("*.py")):
            if path.name == "quiet_spawn.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name) and node.func.value.id == "os"):
                    continue
                name = node.func.attr
                if name == "killpg":
                    bad.append(f"{path.relative_to(SRC)}:{node.lineno} os.killpg")
                elif name == "kill":
                    sig = node.args[1] if len(node.args) > 1 else None
                    if not (isinstance(sig, ast.Constant) and sig.value == 0):
                        bad.append(f"{path.relative_to(SRC)}:{node.lineno} os.kill")
        self.assertEqual([], bad, "route these through quiet_spawn.signal_group / signal_pid")


if __name__ == "__main__":
    unittest.main()
