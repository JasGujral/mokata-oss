"""C8/C5 at the command path — `mokata memory` is read-only: it surfaces active items,
the read/write ratio, and any pending healing proposals, without committing anything."""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.cli import main
from mokata.config import Surface
from mokata.init import init_repo
from mokata.memory import MemoryItem, MemoryStore


def silent(_):
    pass


class TestMemoryCLI(unittest.TestCase):
    def _seed(self, d):
        init_repo(root=d, profile="full", assume_yes=True, out=silent)
        store = MemoryStore.from_surface(Surface.load(d))
        store.remember(MemoryItem.create("db.engine", "postgres"), assume_yes=True)
        store.close()

    def test_memory_stats_reported(self):
        with tempfile.TemporaryDirectory() as d:
            self._seed(d)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["memory", "--path", d])
            out = buf.getvalue()
            self.assertEqual(rc, 0)
            self.assertIn("postgres", out)
            self.assertIn("read/write", out.lower())

    def test_memory_is_read_only_and_commits_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            self._seed(d)
            before = len(MemoryStore.from_surface(Surface.load(d)).all_active())
            with redirect_stdout(io.StringIO()):
                main(["memory", "--path", d])
            after = len(MemoryStore.from_surface(Surface.load(d)).all_active())
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
