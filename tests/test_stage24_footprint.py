"""Stage 24 Part D — contain mokata's footprint under .mokata/, with a committed/transient
split inside it.

Invariant: everything mokata creates as its own data lives under .mokata/. Inside it,
committable config (manifest.json, constitution.md, .gitignore, an exported stack) sits at
the root; everything transient/runtime (pipeline state, the SQLite memory store, the audit
ledger, the freshness index, caches) lives under .mokata/temp_local/, which a committed
.mokata/.gitignore keeps out of version control.
"""

import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import MOKATA_DIR, TEMP_LOCAL_DIRNAME
from mokata.config import Surface
from mokata.execmode import SEQUENTIAL, ExecutionChoice
from mokata.init import init_repo
from mokata.memory import MemoryItem, MemoryStore
from mokata.playbook import run_playbook
from mokata.profiles import profile_names

# Files that are committable config and so are allowed at the .mokata/ root.
COMMITTED_AT_ROOT = {"manifest.json", "constitution.md", ".gitignore",
                     "mokata-stack.json"}


def _silent(_):
    pass


def _all_paths_outside_mokata(root):
    """Every path directly under `root` that is NOT the .mokata/ dir."""
    return [name for name in os.listdir(root) if name != MOKATA_DIR]


class TestInitFootprint(unittest.TestCase):
    def test_init_creates_nothing_outside_mokata_every_profile(self):
        for profile in profile_names():
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as d:
                init_repo(root=d, profile=profile, assume_yes=True, out=_silent)
                self.assertEqual(_all_paths_outside_mokata(d), [],
                                 f"{profile}: init wrote outside .mokata/")

    def test_init_root_holds_only_committable_config(self):
        for profile in profile_names():
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as d:
                init_repo(root=d, profile=profile, assume_yes=True, out=_silent)
                mdir = os.path.join(d, MOKATA_DIR)
                for name in os.listdir(mdir):
                    self.assertIn(name, COMMITTED_AT_ROOT,
                                  f"{profile}: unexpected file at .mokata/ root: {name}")
                # the committed essentials are present
                self.assertTrue(os.path.exists(os.path.join(mdir, "manifest.json")))
                self.assertTrue(os.path.exists(os.path.join(mdir, "constitution.md")))

    def test_gitignore_committed_and_ignores_temp_local(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            gi = os.path.join(d, MOKATA_DIR, ".gitignore")
            self.assertTrue(os.path.exists(gi))
            with open(gi, encoding="utf-8") as fh:
                body = fh.read()
            self.assertIn("temp_local/", body)

    def test_init_does_not_pre_create_temp_local(self):
        # init writes only committed config; no runtime data yet.
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            self.assertFalse(
                os.path.exists(os.path.join(d, MOKATA_DIR, TEMP_LOCAL_DIRNAME)))


class TestFullRunFootprint(unittest.TestCase):
    def _run(self, d, profile="full"):
        init_repo(root=d, profile=profile, assume_yes=True, out=_silent)
        surface = Surface.load(d)
        run_playbook(surface, ExecutionChoice(SEQUENTIAL))
        # a human-gated memory write, so the SQLite store is materialized too
        store = MemoryStore.from_surface(Surface.load(d))
        store.remember(MemoryItem.create("decision", "use sqlite"), assume_yes=True)
        store.close()
        return surface

    def test_full_run_writes_nothing_outside_mokata(self):
        with tempfile.TemporaryDirectory() as d:
            self._run(d)
            self.assertEqual(_all_paths_outside_mokata(d), [],
                             "a full run wrote mokata data outside .mokata/")

    def test_transient_data_lives_under_temp_local_not_root(self):
        with tempfile.TemporaryDirectory() as d:
            self._run(d)
            mdir = os.path.join(d, MOKATA_DIR)
            tl = os.path.join(mdir, TEMP_LOCAL_DIRNAME)

            # the runtime stores landed under temp_local/, never at the .mokata/ root
            self.assertTrue(os.path.exists(
                os.path.join(tl, "memory", "memory.db")))
            self.assertTrue(os.path.exists(
                os.path.join(tl, "audit", "ledger.jsonl")))
            self.assertTrue(os.path.isdir(os.path.join(tl, "state")))
            for transient in ("memory", "audit", "state"):
                self.assertFalse(
                    os.path.exists(os.path.join(mdir, transient)),
                    f"transient '{transient}/' must be under temp_local/, not .mokata/ root")

            # the .mokata/ root holds only committable config + temp_local/
            for name in os.listdir(mdir):
                self.assertTrue(
                    name == TEMP_LOCAL_DIRNAME or name in COMMITTED_AT_ROOT,
                    f"unexpected entry at .mokata/ root after a run: {name}")


class TestExportFootprint(unittest.TestCase):
    def test_export_default_lands_under_mokata_root(self):
        import io
        from contextlib import redirect_stdout
        from mokata.cli import main
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["export", "--path", d])
            self.assertEqual(rc, 0)
            # default destination is committable config at the .mokata/ root
            self.assertTrue(os.path.exists(
                os.path.join(d, MOKATA_DIR, "mokata-stack.json")))
            # nothing mokata-owned escaped to the repo root
            self.assertEqual(_all_paths_outside_mokata(d), [])

    def test_explicit_export_path_still_honored(self):
        from mokata.share import export_manifest
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            dest = os.path.join(d, "shared", "my-stack.json")
            export_manifest(Surface.load(d), dest=dest)
            self.assertTrue(os.path.exists(dest))


if __name__ == "__main__":
    unittest.main()
