"""A5 — unified config + constitution surface."""

import json
import os
import tempfile
import unittest

from _support import sample_manifest_data

from mokata import CONSTITUTION_FILENAME, MANIFEST_FILENAME, MOKATA_DIR
from mokata.config import ConfigError, Constitution, Surface


def write_repo(root, with_constitution=True):
    mdir = os.path.join(root, MOKATA_DIR)
    os.makedirs(mdir, exist_ok=True)
    with open(os.path.join(mdir, MANIFEST_FILENAME), "w") as fh:
        json.dump(sample_manifest_data(), fh)
    if with_constitution:
        with open(os.path.join(mdir, CONSTITUTION_FILENAME), "w") as fh:
            fh.write("# c\n## Article 1 — x\n## Article 2 — y\n")


class TestSurface(unittest.TestCase):
    def test_not_initialized_raises(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(Surface.is_initialized(d))
            with self.assertRaises(ConfigError):
                Surface.load(d)

    def test_loads_manifest_and_constitution(self):
        with tempfile.TemporaryDirectory() as d:
            write_repo(d)
            self.assertTrue(Surface.is_initialized(d))
            surface = Surface.load(d)
            self.assertEqual(surface.manifest.profile, "standard")
            self.assertTrue(surface.constitution.present)
            # Two ## Article headings; the # title is not counted as an article.
            self.assertEqual(len(surface.constitution.articles()), 2)
            # one place: a router is wired straight off the surface.
            self.assertTrue(surface.router.has("memory_store"))

    def test_loads_without_constitution(self):
        with tempfile.TemporaryDirectory() as d:
            write_repo(d, with_constitution=False)
            surface = Surface.load(d)
            self.assertFalse(surface.constitution.present)
            self.assertEqual(surface.constitution.articles(), [])

    def test_broken_manifest_raises_configerror(self):
        with tempfile.TemporaryDirectory() as d:
            mdir = os.path.join(d, MOKATA_DIR)
            os.makedirs(mdir)
            with open(os.path.join(mdir, MANIFEST_FILENAME), "w") as fh:
                fh.write("{ broken json")
            with self.assertRaises(ConfigError):
                Surface.load(d)

    def test_constitution_articles_parsing(self):
        c = Constitution(text="# top\n## A\n### B\nplain line\n## C\n", path=None)
        # H1 'top' is the title, not an article; ## and ### count.
        self.assertEqual(c.articles(), ["A", "B", "C"])


if __name__ == "__main__":
    unittest.main()
