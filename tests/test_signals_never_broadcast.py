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

SRC = Path(__file__).resolve().parent.parent / "src" / "mokata"
POSIX = os.name != "nt"
#: Windows has no SIGKILL; the door must refuse before any signal number matters
KILL = getattr(signal, "SIGKILL", signal.SIGTERM)


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
                self.assertFalse(quiet_spawn.signal_group(v, KILL), repr(v))
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

    def test_the_process_doors_tree_end(self):
        """0.0.28 lane B: the language server, the test re-run, the merge suite, the baseline, the
        statusLine wrap and `poetry`/`pipenv` all end their trees through ONE handle,
        `quiet_spawn.Jobbed.end` (`lsp._kill_tree`, `review_context._kill_tree` and
        `crg_resolve._end_tree` were deleted). Fed every unsafe number: nothing is signalled."""
        for v in _unsafe_numbers():
            spy = _Spy()
            with mock.patch.object(os, "killpg", spy, create=True), \
                    mock.patch.object(os, "kill", spy):
                tree = quiet_spawn.Jobbed(self._fake_proc(v), None, False)
                tree.end()
                tree.end(leftovers_only=True)
                tree.close()
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
        with mock.patch("mokata.quiet_spawn.subprocess.Popen") as popen, \
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
    """Derived from the tree: outside `quiet_spawn`, no group signal at all and no pid signal other
    than the literal 0 (a liveness probe) — however `os` is spelled: `os.killpg`, an alias
    (`import os as _os`), a bare name (`from os import killpg as k`) or `getattr(os, "killpg")`."""

    @staticmethod
    def _offences(source: str, where: str):
        tree = ast.parse(source, filename=where)
        os_names, fn_names = {"os"}, {}               # local name -> "kill" / "killpg"
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name == "os":
                        os_names.add(a.asname or "os")
            elif isinstance(node, ast.ImportFrom) and node.module == "os":
                for a in node.names:
                    if a.name in ("kill", "killpg"):
                        fn_names[a.asname or a.name] = a.name
        bad = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f, name = node.func, None
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) \
                    and f.value.id in os_names and f.attr in ("kill", "killpg"):
                name = f.attr
            elif isinstance(f, ast.Name) and f.id in fn_names:
                name = fn_names[f.id]
            elif isinstance(f, ast.Call) and isinstance(f.func, ast.Name) and f.func.id == "getattr" \
                    and len(f.args) >= 2 and isinstance(f.args[1], ast.Constant) \
                    and f.args[1].value in ("kill", "killpg"):
                name = f.args[1].value
            if name == "killpg":
                bad.append(f"{where}:{node.lineno} killpg")
            elif name == "kill":
                sig = node.args[1] if len(node.args) > 1 else None
                if not (isinstance(sig, ast.Constant) and sig.value == 0):
                    bad.append(f"{where}:{node.lineno} kill")
        return bad

    def test_every_group_or_pid_signal_in_src_uses_the_door(self):
        bad = []
        for path in sorted(SRC.rglob("*.py")):
            if path.name == "quiet_spawn.py":
                continue
            bad += self._offences(path.read_text(encoding="utf-8"), str(path.relative_to(SRC)))
        self.assertEqual([], bad, "route these through quiet_spawn.signal_group / signal_pid")

    def test_the_sweep_sees_every_spelling(self):
        """The sweep's own control: each spelling of a raw signal is caught; a probe is not."""
        caught = {
            "import os\nos.killpg(1, 9)": 1,
            "import os as _os\n_os.killpg(p, 9)": 1,
            "from os import killpg\nkillpg(p, 9)": 1,
            "from os import killpg as k\nk(p, 9)": 1,
            "import os\ngetattr(os, 'killpg')(p, 9)": 1,
            "import os as _o\n_o.kill(p, 15)": 1,
            "from os import kill\nkill(p, sig)": 1,
            "import os\nos.kill(p, 0)": 0,
            "import os as _os\n_os.kill(p, 0)": 0,
        }
        for src, n in caught.items():
            self.assertEqual(n, len(self._offences(src, "x.py")), src)


class ProbeNeedsNtGuard(unittest.TestCase):
    """0.0.28 lane B (refute M8). `os.kill(pid, 0)` is a liveness PROBE on POSIX and
    `TerminateProcess` on Windows (`session_registry.py`, 0.0.23 verification B2). The sweep above
    accepts a literal-0 `os.kill`; this pins WHY that is safe: every one sits after a Windows early
    return in its own function, or — for a helper registered below — every call of it in its module
    does. A new probe without that guard reds here; nothing checked it before."""

    #: (path under src/mokata, function) -> why its callers, not itself, carry the guard.
    GUARDED_BY_CALLERS = {
        ("merge_lock.py", "_exists"):
            "called by `pid_start` only after its `os.name == \"nt\"` return, or inside its "
            "`sys.platform.startswith(\"linux\")` branch",
    }

    @staticmethod
    def _is_nt_test(test):
        for n in ast.walk(test):
            if isinstance(n, ast.Compare) and isinstance(n.left, ast.Attribute) \
                    and n.left.attr in ("name", "platform") and any(
                        isinstance(c, ast.Constant) and c.value in ("nt", "win32")
                        for c in n.comparators):
                return True
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
                    and n.func.id in ("_windows", "_nt", "_is_windows"):
                return True
        return False

    @staticmethod
    def _is_posix_only_test(test):
        for n in ast.walk(test):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                    and n.func.attr == "startswith" and n.args \
                    and isinstance(n.args[0], ast.Constant) \
                    and n.args[0].value in ("linux", "darwin"):
                return True
        return False

    @staticmethod
    def _terminates(stmts):
        if not stmts:
            return False
        last = stmts[-1]
        if isinstance(last, (ast.Return, ast.Raise)):
            return True
        if isinstance(last, ast.Try):
            return ProbeNeedsNtGuard._terminates(last.body) and all(
                ProbeNeedsNtGuard._terminates(h.body) for h in last.handlers)
        return False

    @classmethod
    def _guarded(cls, fn, line):
        """`line` in function `fn` is reached only off Windows."""
        for st in fn.body:
            if st.lineno >= line:
                break
            if isinstance(st, ast.If) and cls._is_nt_test(st.test) and cls._terminates(st.body):
                return True
        for n in ast.walk(fn):
            if isinstance(n, ast.If) and cls._is_posix_only_test(n.test) and any(
                    getattr(b, "lineno", 0) <= line <= getattr(b, "end_lineno", 0) for b in n.body):
                return True
        return False

    @classmethod
    def offences(cls, sources):
        """Pure: `{relpath: source}` -> the unguarded literal-0 `os.kill` probes."""
        bad = []
        for rel, src in sources.items():
            tree = ast.parse(src)
            fns = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

            def owner(line):
                inner = [f for f in fns if f.lineno <= line <= f.end_lineno]
                return min(inner, key=lambda f: f.end_lineno - f.lineno) if inner else None
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "kill" and len(node.args) > 1
                        and isinstance(node.args[1], ast.Constant) and node.args[1].value == 0):
                    continue
                fn = owner(node.lineno)
                if fn is not None and cls._guarded(fn, node.lineno):
                    continue
                if fn is not None and (rel, fn.name) in cls.GUARDED_BY_CALLERS:
                    calls = [c for c in ast.walk(tree) if isinstance(c, ast.Call)
                             and isinstance(c.func, ast.Name) and c.func.id == fn.name]
                    if calls and all(owner(c.lineno) is not None
                                     and cls._guarded(owner(c.lineno), c.lineno) for c in calls):
                        continue
                bad.append(f"{rel}:{node.lineno}")
        return bad

    def test_every_real_probe_is_guarded(self):
        sources = {str(p.relative_to(SRC)).replace(os.sep, "/"): p.read_text(encoding="utf-8")
                   for p in sorted(SRC.rglob("*.py"))}
        self.assertEqual([], self.offences(sources),
                         "a literal-0 os.kill reachable on Windows TERMINATES the process")
        probes = [k for k, v in sources.items() if "kill(" in v and ", 0)" in v]
        self.assertGreaterEqual(len(probes), 4, probes)     # the four real sites are swept

    def test_a_probe_without_the_guard_reds_and_each_guard_shape_acquits(self):
        bare = "import os\ndef alive(pid):\n    os.kill(pid, 0)\n    return True\n"
        late = ("import os\ndef alive(pid):\n    os.kill(pid, 0)\n"
                "    if os.name == 'nt':\n        return False\n")
        soft = ("import os\ndef alive(pid):\n    if os.name == 'nt':\n        print('x')\n"
                "    os.kill(pid, 0)\n")
        good = ("import os\ndef alive(pid):\n    if os.name == 'nt':\n        return False\n"
                "    os.kill(pid, 0)\n")
        tried = ("import os\ndef alive(pid):\n    if os.name == 'nt':\n        try:\n"
                 "            return 1\n        finally:\n            pass\n    os.kill(pid, 0)\n")
        self.assertEqual(["x.py:3"], self.offences({"x.py": bare}))
        self.assertEqual(["x.py:3"], self.offences({"x.py": late}))
        self.assertEqual(["x.py:5"], self.offences({"x.py": soft}))
        self.assertEqual([], self.offences({"x.py": good}))
        self.assertEqual([], self.offences({"x.py": tried}))
        helper = ("import os\ndef _exists(pid):\n    os.kill(pid, 0)\n"
                  "def pid_start(pid):\n    if os.name == 'nt':\n        return 1\n"
                  "    return _exists(pid)\n")
        self.assertEqual([], self.offences({"merge_lock.py": helper}))
        unguarded_caller = helper + "def other(pid):\n    return _exists(pid)\n"
        self.assertEqual(["merge_lock.py:3"], self.offences({"merge_lock.py": unguarded_caller}))
        self.assertEqual(["other.py:3"], self.offences({"other.py": helper}),
                         "the caller register is keyed by file and function, never by name alone")


class IntSubclassRefused(unittest.TestCase):
    """The door's int check is `type(n) is int`, pinned (0.0.27 SIGNAL-GUARD Low 2): an `int`
    SUBCLASS — whose `__index__`/value can be anything — is refused like a bool or a mock."""

    def test_an_int_subclass_never_reaches_the_os(self):
        class P(int):
            pass
        spy = _Spy()
        with mock.patch.object(os, "kill", spy), mock.patch.object(os, "killpg", spy, create=True):
            for v in (P(5), P(os.getpid() + 1), True, False, mock.MagicMock(), 0, 1, -1):
                self.assertFalse(quiet_spawn.signal_pid(v, signal.SIGTERM), repr(v))
                self.assertFalse(quiet_spawn.signal_group(v, KILL), repr(v))
        self.assertEqual([], spy.calls)


if __name__ == "__main__":
    unittest.main()
