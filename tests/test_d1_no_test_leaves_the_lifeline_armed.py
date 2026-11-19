"""0.0.29 wave-1 bookend — `TEST-LEAVES-THE-LIFELINE-ARMED`.

Lane B's `quiet_spawn.enable_lifeline()` is process-global by design: `mokata-mcp` calls it once
and every door tree after it runs under `_lifeline.py`. `test_l8_plugin_route` drives
`server.main([])` in-process (serving mocked), which arms it for the REST OF THE TEST PROCESS — and
`test_p24`'s exact-kwargs row then saw `pass_fds` it never asked for. Each lane was green alone; the
merged order put l8 before p24. The test door now disarms the lifeline after any test that armed it,
so no later test runs under a wrapper it did not ask for. Rows are labelled guard/force.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest

import _support  # noqa: F401  (the door under test)

TESTS = os.path.dirname(os.path.abspath(__file__))

_CHILD = (
    "import sys, unittest; sys.path.insert(0, %r)\n"
    "import _support\n"
    "from mokata import quiet_spawn as Q\n"
    "class Arms(unittest.TestCase):\n"
    "    def test_arms(self):\n"
    "        Q.enable_lifeline()\n"
    "r = unittest.TextTestRunner(stream=open('/dev/null', 'w')).run(\n"
    "    unittest.defaultTestLoader.loadTestsFromTestCase(Arms))\n"
    "print('ARMED_AFTER', bool(Q._LIFELINE), 'OK', r.wasSuccessful(), flush=True)\n"
) % TESTS


_CHILD_LIVE = (
    "import sys, unittest; sys.path.insert(0, %r)\n"
    "import _support\n"
    "from mokata import quiet_spawn as Q\n"
    "class Leaves(unittest.TestCase):\n"
    "    def test_leaves(self):\n"
    "        with Q._LIVE_LOCK:\n"
    "            Q._LIVE.add(object())\n"
    "r = unittest.TextTestRunner(stream=open('/dev/null', 'w')).run(\n"
    "    unittest.defaultTestLoader.loadTestsFromTestCase(Leaves))\n"
    "print('LIVE_AFTER', len(Q._LIVE), 'OK', r.wasSuccessful(), flush=True)\n"
) % TESTS


@unittest.skipIf(os.name == "nt", "the lifeline is POSIX-only (Windows uses KILL_ON_JOB_CLOSE)")
class NoTestLeavesTheLifelineArmed(unittest.TestCase):

    def test_force_a_test_that_arms_it_leaves_it_disarmed(self):
        """force — a test arms the process lifeline and returns; the next test must not inherit
        it."""
        out = subprocess.run([sys.executable, "-c", _CHILD], capture_output=True, text=True,
                             timeout=300)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("ARMED_AFTER False OK True", out.stdout)

    def test_force_a_tree_left_in_the_registry_is_forgotten_after_the_test(self):
        """force — a test adds a (mock) tree to the door's live registry and returns."""
        out = subprocess.run([sys.executable, "-c", _CHILD_LIVE], capture_output=True, text=True,
                             timeout=300)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("LIVE_AFTER 0 OK True", out.stdout)

    def test_guard_l8_drives_server_main_without_arming_it(self):
        """guard — the known instance: `test_l8`'s in-process `server.main([])` stubs the arming."""
        with open(os.path.join(TESTS, "test_l8_plugin_route.py"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue('"mokata.quiet_spawn.enable_lifeline"' in text,
                        "test_l8's in-process server.main([]) no longer stubs the arming")


if __name__ == "__main__":
    unittest.main()
