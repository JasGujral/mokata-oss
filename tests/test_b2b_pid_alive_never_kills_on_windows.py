"""0.0.23 verification B2 — `session_registry.pid_alive` must never call `os.kill` on Windows, where
`os.kill(pid, 0)` is TerminateProcess: the probe would end the process it asks about.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import session_registry as SR


class WindowsProbeLooksOnly(unittest.TestCase):

    def test_on_nt_os_kill_is_never_called(self):
        with mock.patch.object(SR.os, "name", "nt"), \
                mock.patch.object(SR.os, "kill", side_effect=AssertionError("os.kill on nt")), \
                mock.patch.object(SR, "_pid_alive_windows", return_value=True) as probe:
            self.assertTrue(SR.pid_alive(4242))
        probe.assert_called_once_with(4242)

    def test_posix_still_uses_the_signal_zero_probe(self):
        if os.name == "nt":
            self.skipTest("POSIX arm")
        self.assertTrue(SR.pid_alive(os.getpid()))
        self.assertFalse(SR.pid_alive(0))


if __name__ == "__main__":
    unittest.main()
