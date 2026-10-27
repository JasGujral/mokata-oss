"""O25 (0.0.26 cut, Windows review) — the code-review-graph version probe is bounded for REAL.

`subprocess.run(capture_output=True, timeout=1)` kills only the direct child. On Windows a `.cmd`
shim's real program survives it, keeps the pipes open, and `run`'s post-kill `communicate()` (no
bound) waits for it — measured by reading CPython: an hour on the hermetic test's sleeper. The
probe now writes to temp files and ends the whole tree, so a surviving grandchild blocks nothing.
Planted here with a POSIX shim whose grandchild holds stdout far past the bound."""

import os
import stat
import tempfile
import time
import unittest

import _support  # noqa: F401

from mokata import crg_resolve as CR


@unittest.skipIf(os.name == "nt", "the planted shim is a POSIX script")
class TheProbeReturnsWithinItsBound(unittest.TestCase):

    def test_a_grandchild_holding_stdout_does_not_hold_the_probe(self):
        d = tempfile.mkdtemp()
        shim = os.path.join(d, "code-review-graph")
        with open(shim, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("#!/bin/sh\nsleep 20 &\nsleep 20\n")
        os.chmod(shim, os.stat(shim).st_mode | stat.S_IXUSR)
        t = time.monotonic()
        self.assertIsNone(CR.probe_version(shim, timeout=1.0))
        self.assertLess(time.monotonic() - t, 8.0, "the probe waited for the orphaned child")

    def test_a_quick_answer_is_still_read(self):
        d = tempfile.mkdtemp()
        shim = os.path.join(d, "code-review-graph")
        with open(shim, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("#!/bin/sh\necho 'code-review-graph 2.3.9'\n")
        os.chmod(shim, os.stat(shim).st_mode | stat.S_IXUSR)
        self.assertEqual("2.3.9", CR.probe_version(shim, timeout=5.0))


if __name__ == "__main__":
    unittest.main()
