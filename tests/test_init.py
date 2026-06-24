"""A7 — `mokata init` onboarding."""

import os
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata import CONSTITUTION_FILENAME, MANIFEST_FILENAME, MOKATA_DIR
from mokata.config import Surface
from mokata.detect import Detector
from mokata.init import init_repo, plan_init, render_plan
from mokata.manifest import Manifest


def silent(_):  # swallow init's stdout in tests
    pass


class TestInit(unittest.TestCase):
    def test_clean_repo_produces_valid_config(self):
        with tempfile.TemporaryDirectory() as d:
            result = init_repo(root=d, profile="standard", assume_yes=True, out=silent)
            self.assertFalse(result.aborted)
            manifest_path = os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME)
            const_path = os.path.join(d, MOKATA_DIR, CONSTITUTION_FILENAME)
            self.assertTrue(os.path.exists(manifest_path))
            self.assertTrue(os.path.exists(const_path))
            # Produced manifest must load + validate.
            m = Manifest.load(manifest_path)
            self.assertEqual(m.profile, "standard")
            # And the unified surface loads cleanly off it.
            surface = Surface.load(d)
            self.assertTrue(surface.router.has("code_graph"))

    def test_minimal_profile_has_no_capabilities(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="minimal", assume_yes=True, out=silent)
            m = Manifest.load(os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME))
            self.assertEqual(m.capabilities, {})
            self.assertFalse(m.layer_enabled("knowledge"))
            self.assertTrue(m.layer_enabled("engine"))

    def test_refuses_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=silent)
            result = init_repo(root=d, profile="full", assume_yes=True, out=silent)
            self.assertTrue(result.aborted)
            self.assertIn("already exists", result.message)
            # Original profile untouched.
            m = Manifest.load(os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME))
            self.assertEqual(m.profile, "standard")

    def test_force_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=silent)
            result = init_repo(
                root=d, profile="full", assume_yes=True, force=True, out=silent
            )
            self.assertFalse(result.aborted)
            m = Manifest.load(os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME))
            self.assertEqual(m.profile, "full")

    def test_human_gate_abort_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            result = init_repo(
                root=d,
                profile="standard",
                assume_yes=False,
                confirm=lambda prompt: False,  # user declines
                out=silent,
            )
            self.assertTrue(result.aborted)
            self.assertFalse(
                os.path.exists(os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME))
            )

    def test_human_gate_accept_writes(self):
        with tempfile.TemporaryDirectory() as d:
            result = init_repo(
                root=d,
                profile="standard",
                assume_yes=False,
                confirm=lambda prompt: True,  # user approves
                out=silent,
            )
            self.assertFalse(result.aborted)
            self.assertTrue(
                os.path.exists(os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME))
            )

    def test_plan_detects_whole_catalog(self):
        plan = plan_init(root=".", profile="standard",
                         detector=Detector(overrides={"sqlite": True}))
        # sqlite3 is always importable -> present; render includes it.
        self.assertIn("sqlite", plan.detected)
        rendered = render_plan(plan)
        self.assertIn("Detected tools", rendered)
        self.assertIn("code_graph", rendered)

    def test_unknown_profile_raises(self):
        with self.assertRaises(ValueError):
            plan_init(root=".", profile="nonsense")


if __name__ == "__main__":
    unittest.main()
