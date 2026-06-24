"""K7 — config as a committed, reviewable artifact.

The manifest must round-trip through a real git commit unchanged:
write -> commit -> reload -> validate identically.
"""

import os
import shutil
import subprocess
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata import MANIFEST_FILENAME, MOKATA_DIR
from mokata import schema
from mokata.init import init_repo
from mokata.manifest import Manifest


def silent(_):
    pass


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@unittest.skipUnless(shutil.which("git"), "git is required for this test")
class TestConfigRoundTripsThroughCommit(unittest.TestCase):
    def _make_repo(self, d):
        _git("init", cwd=d)
        _git("config", "user.email", "test@mokata.local", cwd=d)
        _git("config", "user.name", "mokata test", cwd=d)

    def test_round_trip(self):
        for profile in ("minimal", "standard", "full", "custom"):
            with self.subTest(profile=profile):
                with tempfile.TemporaryDirectory() as d:
                    self._make_repo(d)
                    init_repo(root=d, profile=profile, assume_yes=True, out=silent)

                    manifest_path = os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME)
                    with open(manifest_path, encoding="utf-8") as fh:
                        on_disk_before = fh.read()
                    data_before = Manifest.load(manifest_path).data

                    # commit the artifact
                    _git("add", "-A", cwd=d)
                    _git("commit", "-m", "mokata config", cwd=d)

                    # what's committed is byte-identical to what we wrote
                    committed = subprocess.run(
                        ["git", "show", f"HEAD:{MOKATA_DIR}/{MANIFEST_FILENAME}"],
                        cwd=d, check=True, capture_output=True, text=True,
                    ).stdout
                    self.assertEqual(committed, on_disk_before)

                    # reload from the working tree (restored from the commit) + validate
                    _git("checkout", "--", ".", cwd=d)
                    reloaded = Manifest.load(manifest_path)
                    self.assertEqual(reloaded.data, data_before)
                    self.assertEqual(schema.validate_manifest(reloaded.data), [])
                    self.assertEqual(reloaded.profile, profile)


if __name__ == "__main__":
    unittest.main()
