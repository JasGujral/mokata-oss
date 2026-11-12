"""O25 (0.0.26 cut, Windows review) — the code-review-graph version probe is bounded for REAL.

`subprocess.run(capture_output=True, timeout=1)` kills only the direct child. On Windows a `.cmd`
shim's real program survives it, keeps the pipes open, and `run`'s post-kill `communicate()` (no
bound) waits for it — measured by reading CPython: an hour on the hermetic test's sleeper. The
probe now runs through the process door (0.0.28 lane B, `quiet_spawn.run_jobbed`): output in temp
files, and on the timeout the WHOLE tree is ended — a Job Object on Windows, the process group on
POSIX — so the grandchild does not merely stop blocking, it is gone.

Planted with a launcher that starts a grandchild holding stdout far past the bound: a POSIX shell
script, and on Windows a `.cmd` override (cmd.exe → python → python), the 0.0.26 shape exactly."""

import os
import stat
import sys
import tempfile
import time
import unittest

import _support  # noqa: F401

from mokata import crg_resolve as CR

#: The bound a dead process gets to be seen dead: the test returns the moment it is.
GONE_S = 30.0


def _alive(pid):
    if os.name == "nt":
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        h = k32.OpenProcess(0x1000, False, int(pid))          # QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            k32.GetExitCodeProcess(h, ctypes.byref(code))
            return code.value == 259                           # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    else:
        try:
            os.kill(int(pid), 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


def _gone(pid):
    end = time.monotonic() + GONE_S
    while time.monotonic() < end:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return not _alive(pid)


def _launcher(d, body_py):
    """A `code-review-graph` that runs `body_py` through a shell launcher (POSIX `sh`, Windows
    `.cmd`), so the probe's direct child is NOT the program that holds the pipe."""
    script = os.path.join(d, "body.py")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(body_py)
    if os.name == "nt":
        shim = os.path.join(d, "code-review-graph.cmd")
        with open(shim, "w", encoding="utf-8", newline="\r\n") as fh:
            fh.write(f'@"{sys.executable}" "{script}" %*\n')
        return shim
    shim = os.path.join(d, "code-review-graph")
    with open(shim, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f'#!/bin/sh\n"{sys.executable}" "{script}" "$@"\n')
    os.chmod(shim, os.stat(shim).st_mode | stat.S_IXUSR)
    return shim


class TheProbeReturnsWithinItsBound(unittest.TestCase):

    def test_a_grandchild_holding_stdout_does_not_hold_the_probe_and_is_ended(self):
        d = tempfile.mkdtemp()
        pidfile = os.path.join(d, "grandchild.pid")
        shim = _launcher(d, (
            "import subprocess, sys, time\n"
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
            f"open({pidfile!r}, 'w').write(str(p.pid))\n"
            "time.sleep(120)\n"))
        t = time.monotonic()
        version, why = CR.probe_version(shim, timeout=3.0)
        self.assertIsNone(version)
        self.assertIn("did not answer within 3 s", why)
        res = CR._checked(CR.CrgResolution(origin="MOKATA_CRG"), shim, version, why)
        self.assertIn("did not report a version", res.refused)
        self.assertIn("did not answer within 3 s", res.refused)
        self.assertLess(time.monotonic() - t, 3.0 + GONE_S, "the probe waited for the orphan")
        with open(pidfile, encoding="utf-8") as fh:
            grandchild = int(fh.read())
        self.assertTrue(_gone(grandchild), "the probe's tree outlived its timeout")

    def test_a_quick_answer_is_still_read(self):
        d = tempfile.mkdtemp()
        shim = _launcher(d, "print('code-review-graph 2.3.9')\n")
        self.assertEqual(("2.3.9", ""), CR.probe_version(shim, timeout=GONE_S))

    def test_a_refusal_says_itself_not_did_not_report_a_version(self):
        from unittest import mock
        d = tempfile.mkdtemp()
        shim = _launcher(d, "print('code-review-graph 2.3.9')\n")
        boom = OSError("mokata could not hold this process in a Windows Job Object (test)")
        with mock.patch("mokata.quiet_spawn.run_jobbed", side_effect=boom):
            CR._PROBED.clear()
            version, why = CR.probe_version(shim, timeout=GONE_S)
        CR._PROBED.clear()
        self.assertIsNone(version)
        self.assertIn("Windows Job Object", why)
        res = CR._checked(CR.CrgResolution(origin="MOKATA_CRG"), shim, version, why)
        self.assertIn("Windows Job Object", res.refused)
        self.assertNotIn("did not report a version", res.refused)


if __name__ == "__main__":
    unittest.main()
