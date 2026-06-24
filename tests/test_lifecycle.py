"""K6 — clean uninstall / state reset: remove mokata's state without residue, gated and
preview-able (reversible-aware)."""

import os
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata import MOKATA_DIR
from mokata.config import Surface
from mokata.govern import plan_reset, reset_state
from mokata.init import init_repo
from mokata.memory import MemoryItem, MemoryStore


def silent(_):
    pass


def seed(d):
    init_repo(root=d, profile="full", assume_yes=True, out=silent)
    store = MemoryStore.from_surface(Surface.load(d))
    store.remember(MemoryItem.create("k", "v"), assume_yes=True)
    store.close()
    return os.path.join(d, MOKATA_DIR)


class TestReset(unittest.TestCase):
    def test_plan_reset_has_no_side_effects(self):
        with tempfile.TemporaryDirectory() as d:
            mdir = seed(d)
            plan = plan_reset(d)                  # full uninstall
            self.assertTrue(plan.targets)
            self.assertTrue(os.path.exists(mdir))  # preview did not delete anything

    def test_uninstall_removes_everything(self):
        with tempfile.TemporaryDirectory() as d:
            mdir = seed(d)
            self.assertTrue(os.path.exists(mdir))
            result = reset_state(d, assume_yes=True)
            self.assertFalse(os.path.exists(mdir))     # no residue
            self.assertTrue(result.removed)

    def test_reset_is_gated(self):
        with tempfile.TemporaryDirectory() as d:
            mdir = seed(d)
            result = reset_state(d, confirm=lambda _t: False)
            self.assertTrue(result.aborted)
            self.assertTrue(os.path.exists(mdir))      # declined -> nothing removed

    def test_keep_config_preserves_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            mdir = seed(d)
            reset_state(d, keep_config=True, assume_yes=True)
            self.assertTrue(os.path.exists(os.path.join(mdir, "manifest.json")))
            self.assertFalse(os.path.exists(os.path.join(mdir, "memory")))

    def test_backup_is_reversible(self):
        with tempfile.TemporaryDirectory() as d:
            mdir = seed(d)
            backup = os.path.join(d, "mokata-backup")
            reset_state(d, assume_yes=True, backup_dir=backup)
            self.assertFalse(os.path.exists(mdir))      # removed
            self.assertTrue(os.path.exists(backup))     # but recoverable


if __name__ == "__main__":
    unittest.main()
