"""0.0.23 cut — the Windows CI legs (mirror PR "Release 0.0.23"), pinned on every OS.

Two product defects only Windows showed:

* `ATOMIC-REPLACE-WINDOWS` — `os.replace` onto a file another process holds open raises
  `PermissionError` (WinError 5) on Windows; POSIX has no such window. Two processes bumping memory
  stats (`test_ms_s6_rmw_toctou`) lost a write. The rename is now retried briefly on Windows only.
* `SETTINGS-PYTHON-MIXED-SEPARATORS` — a relative `settings.python` (`tools/py`) was joined onto a
  Windows root as `...\\tools/py`; the env path in reports is now the platform's own spelling.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import shutil
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import atomicfile, project_env


class TheReplaceIsRetriedOnWindowsOnly(unittest.TestCase):

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.tmp = os.path.join(self.d, "t")
        self.dst = os.path.join(self.d, "f")
        with open(self.tmp, "w", encoding="utf-8") as fh:
            fh.write("new")

    def _flaky(self, failures):
        real = os.replace
        calls = {"n": 0}

        def fake(a, b):
            calls["n"] += 1
            if calls["n"] <= failures:
                raise PermissionError(5, "Access is denied")
            return real(a, b)
        return fake, calls

    def test_a_held_target_is_retried_and_lands_on_windows(self):
        fake, calls = self._flaky(3)
        with mock.patch.object(atomicfile.os, "replace", side_effect=fake):
            atomicfile._replace(self.tmp, self.dst, delay=0, is_windows=True)
        self.assertEqual(calls["n"], 4)
        with open(self.dst, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "new")

    def test_the_budget_is_finite_and_the_error_is_raised_unchanged(self):
        fake, calls = self._flaky(10 ** 6)
        with mock.patch.object(atomicfile.os, "replace", side_effect=fake):
            with self.assertRaises(PermissionError):
                atomicfile._replace(self.tmp, self.dst, retries=3, delay=0, is_windows=True)
        self.assertEqual(calls["n"], 4)

    def test_posix_never_retries(self):
        fake, calls = self._flaky(1)
        with mock.patch.object(atomicfile.os, "replace", side_effect=fake):
            with self.assertRaises(PermissionError):
                atomicfile._replace(self.tmp, self.dst, delay=0, is_windows=False)
        self.assertEqual(calls["n"], 1)

    def test_atomic_write_text_goes_through_the_retry(self):
        fake, calls = self._flaky(2)
        with mock.patch.object(atomicfile.os, "name", "nt"), \
                mock.patch.object(atomicfile, "REPLACE_RETRY_SECS", 0), \
                mock.patch.object(atomicfile.os, "replace", side_effect=fake):
            atomicfile.atomic_write_text(self.dst, "whole")
        with open(self.dst, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "whole")


class TheSettingsPathIsThePlatformsOwnSpelling(unittest.TestCase):

    def test_a_relative_forward_slash_setting_is_normalised(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        env = os.path.join(root, "tools", "py")
        os.makedirs(os.path.join(env, "Scripts" if os.name == "nt" else "bin"))
        with open(os.path.join(env, "pyvenv.cfg"), "wb") as fh:
            fh.write(b"home = /usr/bin\n")
        found = project_env.resolve(root, "tools/py", {}, ask_tools=False)
        self.assertEqual(found.path, env)
        self.assertEqual(found.path, os.path.normpath(found.path))


if __name__ == "__main__":
    unittest.main()
