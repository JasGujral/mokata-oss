"""O22 (0.0.26 cut, plugin route · windows-latest): "the hook ran on …/python3.exe, not the pinned
…/python.exe". The MCP server wrote the runtime record in text mode — CRLF on Windows — and the
hook shim's `read -r` kept the `\\r`, so the recorded interpreter never tested executable and every
plugin hook fell to rung 2. The record is written LF everywhere and the shim drops a stray `\\r`."""

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401

from mokata import __version__
from mokata import plugin_runtime as PR

HOOKS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "mokata", "hooks")


class TheRuntimeRecordIsReadBackOnEveryOS(unittest.TestCase):

    def setUp(self):
        self.data = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.data, True)

    def test_the_record_is_written_with_lf(self):
        env = {"CLAUDE_PLUGIN_DATA": self.data}
        path = PR.record_runtime(env, executable=sys.executable)
        with open(path, "rb") as fh:
            raw = fh.read()
        self.assertFalse(raw.endswith(b"\r\n"), raw)
        self.assertTrue(raw.endswith(b"\n"), raw)

    @unittest.skipUnless(shutil.which("sh"), "no POSIX shell")
    def test_the_shim_reads_a_crlf_record(self):
        fake = os.path.join(self.data, "py")
        with open(fake, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\n")
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IXUSR)
        with open(os.path.join(self.data, PR.runtime_record_name(__version__)), "wb") as fh:
            fh.write(fake.encode() + b"\r\n")
        script = (f'. "{HOOKS}/mokata-plugin-env.sh"\n_mk_version={__version__}\n'
                  'if _mk_pinned_python; then printf %s "$_mk_py"; else echo MISS; fi')
        p = subprocess.run([shutil.which("sh"), "-c", script], capture_output=True, text=True,
                           timeout=30, stdin=subprocess.DEVNULL,
                           env=dict(os.environ, CLAUDE_PLUGIN_DATA=self.data))
        self.assertEqual(fake, p.stdout, p.stderr)


if __name__ == "__main__":
    unittest.main()
