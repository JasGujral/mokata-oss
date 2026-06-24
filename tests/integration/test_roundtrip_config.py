"""Stage 20 — config round-trips: init -> reload, and export -> import.

Proves a committed stack reloads to the same enabled set in a later session, that a stack
exported from one repo applies cleanly to another, and that an invalid shared manifest is
rejected with nothing written.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import copy
import os
import tempfile
import unittest

from _support import write_sample_repo  # noqa: F401  (import = path-shim side effect)

from mokata import MANIFEST_FILENAME, MOKATA_DIR
from mokata.config import Surface
from mokata.init import init_repo
from mokata.profiles import profile_enabled_set
from mokata.share import apply_manifest, export_manifest, load_shared


def _silent(_):
    pass


def _init(d, profile="standard"):
    init_repo(root=d, profile=profile, assume_yes=True, out=_silent)
    return Surface.load(d)


class TestInitReloadRoundTrip(unittest.TestCase):
    def test_reloaded_surface_matches_the_profile(self):
        for profile in ("minimal", "standard", "full"):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as d:
                _init(d, profile)
                reloaded = Surface.load(d)                  # a fresh session
                expected = profile_enabled_set(profile)

                self.assertEqual(reloaded.manifest.profile, profile)
                enabled_layers = sorted(
                    name for name in reloaded.manifest.layers
                    if reloaded.manifest.layer_enabled(name))
                self.assertEqual(enabled_layers, sorted(expected["layers"]))
                self.assertEqual(sorted(reloaded.manifest.tools),
                                 sorted(expected["tools"]))


class TestExportImportRoundTrip(unittest.TestCase):
    def test_export_then_import_applies_on_a_fresh_repo(self):
        with tempfile.TemporaryDirectory() as src, \
                tempfile.TemporaryDirectory() as dst:
            surface = _init(src, "full")
            shared = os.path.join(src, "mokata-stack.json")
            export_manifest(surface, dest=shared)
            self.assertTrue(os.path.exists(shared))

            res = apply_manifest(dst, load_shared(shared), assume_yes=True)
            self.assertTrue(res.applied, res.message)
            self.assertTrue(os.path.exists(
                os.path.join(dst, MOKATA_DIR, MANIFEST_FILENAME)))

            # the applied stack loads and matches the exported profile
            self.assertEqual(Surface.load(dst).manifest.profile, "full")

    def test_invalid_manifest_is_rejected_and_nothing_is_written(self):
        with tempfile.TemporaryDirectory() as src, \
                tempfile.TemporaryDirectory() as dst:
            surface = _init(src, "standard")
            bad = copy.deepcopy(export_manifest(surface))
            del bad["capabilities"]                         # structurally invalid

            # force=True proves rejection is the VALIDATION, not the overwrite guard
            res = apply_manifest(dst, bad, assume_yes=True, force=True)
            self.assertFalse(res.applied)
            self.assertTrue(res.errors)
            self.assertFalse(os.path.exists(
                os.path.join(dst, MOKATA_DIR, MANIFEST_FILENAME)))


if __name__ == "__main__":
    unittest.main()
