"""0.0.29 wave-1 bookend — `SUITE-HANGS-ON-A-HOST-STDIN-THAT-NEVER-ENDS`.

The wave-1 full suite, run in the Claude app's terminal (stdin = the terminal), hung for nine hours
in `test_b_ver_version_parity`. Two pre-existing facts met:

* `test_b3_the_plugin_stands_alone` calls `hook_cli.main(["stop", "--via-plugin"])` without a stdin
  double, so `_read_stdin_bounded` leaves a daemon thread blocked in `read()` on the real fd 0
  (bounded for the hook, not for the thread);
* `mokata.mcp.server.main` parks fd 0 FIRST (`_park_stdin` -> `dup2(devnull, 0)`), and on macOS a
  `dup2` onto a descriptor another thread is blocked reading waits for that read.

Every earlier run fed the suite a stdin that ends (a pipe, `/dev/null`), so the read returned at
once. The class fix is the fourth member of the PATH / `MOKATA_CRG` / `GIT_*` family in
`tests/_support.py`: the host's stdin never reaches a test process — fd 0 is pointed at the null
device at import. At the DESCRIPTOR, deliberately not the `sys.stdin` object: the tty-prompt sweep
(`_tty_prompt_sweep.py`) decides "ambient stdin" by the identity of `sys.stdin`, so replacing the
object would blind `test_a36` while making it pass.

Each row is labelled guard (pins the shipped state) or force (drives the failure).

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest

import _support

TESTS = os.path.dirname(os.path.abspath(__file__))

# A child whose stdin is a pipe we never close: exactly the "never ends" host stdin. It imports the
# door, then reads stdin the way a hook does. With the door it reads EOF at once; without it, it
# blocks until the parent closes the pipe — so the parent's bound is the RED signal, not a clock in
# the child.
_CHILD = (
    "import sys; sys.path.insert(0, %r)\n"
    "import _support, os\n"
    "obj_before = id(sys.stdin)\n"
    "data = sys.stdin.read()\n"
    "print('READ', repr(data), 'SAME_OBJECT', id(sys.stdin) == obj_before, flush=True)\n"
) % TESTS


class TheHostStdinNeverReachesATestProcess(unittest.TestCase):

    def _spawn_on_a_stdin_that_never_ends(self):
        """The child's stdin is the read end of a pipe whose write end THIS process holds open
        until the child has answered — `communicate()` would close a `PIPE` stdin and hand the
        child the EOF this test exists to prove the door supplies."""
        r, w = os.pipe()
        try:
            proc = subprocess.Popen([sys.executable, "-c", _CHILD], stdin=r,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        finally:
            os.close(r)
        try:
            out, err = proc.communicate(timeout=120)   # bound on the PARENT only
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            self.fail("a test process blocked reading the host's stdin: the door let fd 0 through")
        finally:
            os.close(w)
        return proc, out, err

    def test_force_a_stdin_that_never_ends_reads_eof_after_the_door(self):
        """force — the host stdin is an open pipe with a live writer and no data."""
        proc, out, err = self._spawn_on_a_stdin_that_never_ends()
        self.assertEqual(proc.returncode, 0, err)
        self.assertIn("READ ''", out)

    def test_guard_the_door_works_at_the_descriptor_not_the_object(self):
        """guard — `sys.stdin` keeps its identity (the tty sweep keys on it); fd 0 is the null
        device in this process."""
        _, out, _ = self._spawn_on_a_stdin_that_never_ends()
        self.assertIn("SAME_OBJECT True", out)
        self.assertEqual(os.stat(0).st_ino if os.name != "nt" else 0,
                         os.stat(os.devnull).st_ino if os.name != "nt" else 0)

    def test_guard_the_door_records_that_it_ran(self):
        """guard — the door is in `_support`, beside the GIT_* scrub, not in a run script."""
        self.assertTrue(_support.HERMETIC_STDIN)


if __name__ == "__main__":
    unittest.main()
