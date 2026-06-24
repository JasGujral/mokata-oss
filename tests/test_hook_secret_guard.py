"""G4 + I1 — the shipped sync SECURITY hook: blocks (exit 2) when a secret is present,
passes (exit 0) otherwise."""

import os
import subprocess
import sys
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

HOOK = os.path.join(os.path.dirname(__file__), "..", "hooks", "secret_guard.py")


class TestSecretGuardHook(unittest.TestCase):
    def test_blocks_with_exit_2_on_secret(self):
        proc = subprocess.run(
            [sys.executable, HOOK, "--text", "KEY='AKIAIOSFODNN7EXAMPLE'"],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)   # sync security hook BLOCKS

    def test_passes_with_exit_0_on_clean_content(self):
        proc = subprocess.run(
            [sys.executable, HOOK, "--text", "just some ordinary code"],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
