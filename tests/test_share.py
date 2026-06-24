"""J3 — shareable stack manifests: export the current manifest; import + apply a shared
one in one step (validated before apply; durable apply is human-gated)."""

import copy
import os
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata import MANIFEST_FILENAME, MOKATA_DIR
from mokata.config import Surface
from mokata.init import init_repo
from mokata.share import apply_manifest, export_manifest, load_shared, validate_shared


def silent(_):
    pass


def exported(profile="standard"):
    d = tempfile.mkdtemp()
    init_repo(root=d, profile=profile, assume_yes=True, out=silent)
    return export_manifest(Surface.load(d))


class TestExport(unittest.TestCase):
    def test_export_returns_valid_shareable_manifest(self):
        data = exported("standard")
        self.assertEqual(validate_shared(data), [])
        self.assertEqual(data["profile"], "standard")

    def test_export_writes_a_file(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=silent)
            dest = os.path.join(d, "mokata-stack.json")
            export_manifest(Surface.load(d), dest=dest)
            self.assertTrue(os.path.exists(dest))
            self.assertEqual(validate_shared(load_shared(dest)), [])


class TestImportApply(unittest.TestCase):
    def test_apply_on_a_clean_repo(self):
        data = exported("full")
        with tempfile.TemporaryDirectory() as d:
            result = apply_manifest(d, data, assume_yes=True)
            self.assertTrue(result.applied)
            self.assertEqual(Surface.load(d).manifest.profile, "full")

    def test_invalid_manifest_is_rejected(self):
        bad = copy.deepcopy(exported("standard"))
        del bad["capabilities"]
        with tempfile.TemporaryDirectory() as d:
            result = apply_manifest(d, bad, assume_yes=True)
            self.assertFalse(result.applied)
            self.assertTrue(result.errors)
            self.assertFalse(os.path.exists(
                os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME)))

    def test_overwrite_requires_force(self):
        data = exported("standard")
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="minimal", assume_yes=True, out=silent)
            blocked = apply_manifest(d, data, assume_yes=True)
            self.assertFalse(blocked.applied)
            self.assertTrue(blocked.aborted)
            forced = apply_manifest(d, data, assume_yes=True, force=True)
            self.assertTrue(forced.applied)
            self.assertEqual(Surface.load(d).manifest.profile, "standard")

    def test_apply_is_human_gated(self):
        data = exported("standard")
        with tempfile.TemporaryDirectory() as d:
            result = apply_manifest(d, data, confirm=lambda _t: False)
            self.assertTrue(result.aborted)
            self.assertFalse(os.path.exists(
                os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME)))


if __name__ == "__main__":
    unittest.main()
