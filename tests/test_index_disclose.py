"""INDEX-DISCLOSE — the index says what it covers.

`mokata index` indexes Python only. What the user is TOLD must say so: the languages indexed and
how many source files were skipped as unsupported — in the CLI output and in the MCP
`index_status` tool. Three repos: mixed-language, Python-only, no Python.

SPDX-License-Identifier: Apache-2.0
"""
from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

import _support  # noqa: F401  (puts src/ on the path)
from mokata.cli import main
from mokata.init import init_repo
from mokata.knowledge.index import KnowledgeIndex
from mokata.mcp import tools_read as M


def _write(root, rel, text="x = 1\n"):
    p = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)


class _Base(unittest.TestCase):
    def repo(self, files):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        root = d.name
        for rel in files:
            _write(root, rel)
        init_repo(root=root, profile="minimal", assume_yes=True, out=lambda _m: None)
        return root

    def run_index(self, root):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["index", "--path", root])
        self.assertEqual(rc, 0)
        return buf.getvalue()


class TestMixedLanguageRepo(_Base):
    FILES = ["a.py", "b.py", "web/app.ts", "web/util.js", "web/more.js", "svc/main.go",
             "README.md"]

    def test_cli_names_python_and_counts_the_skipped_files_by_language(self):
        out = self.run_index(self.repo(self.FILES))
        self.assertIn("languages indexed: python;", out)
        self.assertIn("4 file(s) skipped as unsupported", out)
        self.assertIn("1 go", out)
        self.assertIn("3 javascript", out)     # .ts and .js are one language in the table

    def test_mcp_status_reports_the_same(self):
        root = self.repo(self.FILES)
        self.run_index(root)
        res = M.index_status(path=root)
        self.assertEqual(res["languages_indexed"], ["python"])
        self.assertEqual(res["unsupported_files"], 4)
        self.assertEqual(res["unsupported_by_language"], {"javascript": 3, "go": 1})
        self.assertIn("4 file(s) skipped as unsupported", res["coverage"])

    def test_rerun_after_the_index_exists_still_discloses(self):
        root = self.repo(self.FILES)
        self.run_index(root)
        out = self.run_index(root)             # the incremental (diff + reindex) path
        self.assertIn("4 file(s) skipped as unsupported", out)


class TestUncountedLanguages(_Base):
    """Python plus C/Ruby: '0 skipped' must not read as 'nothing else is here'."""
    FILES = ["a.py", "lib/x.c", "lib/y.cpp", "app.rb", "k.kt", "s.swift", "p.php", "c.cs"]

    def test_cli_names_the_set_it_counts(self):
        out = self.run_index(self.repo(self.FILES))
        self.assertIn("0 file(s) skipped as unsupported", out)
        self.assertIn("counted only for javascript, go, rust, java", out)
        self.assertIn("other languages are neither indexed nor counted", out)

    def test_mcp_names_the_set_it_counts(self):
        root = self.repo(self.FILES)
        self.run_index(root)
        res = M.index_status(path=root)
        self.assertEqual(res["unsupported_counted_languages"], ["javascript", "go", "rust", "java"])
        self.assertIn("neither indexed nor counted", res["coverage"])

    def test_counted_set_is_derived_from_the_language_table(self):
        from mokata.languages import LANGUAGES
        cov = KnowledgeIndex().coverage()
        self.assertEqual(cov["counted"], [n for n in LANGUAGES if n != "python"])


class TestPythonOnlyRepo(_Base):
    def test_zero_skipped_is_stated_without_a_breakdown(self):
        out = self.run_index(self.repo(["a.py", "pkg/b.py", "notes.txt"]))
        self.assertIn("languages indexed: python; 0 file(s) skipped as unsupported. ", out)
        self.assertNotIn("0 file(s) skipped as unsupported (", out)

    def test_mcp_status(self):
        root = self.repo(["a.py"])
        self.run_index(root)
        res = M.index_status(path=root)
        self.assertEqual(res["languages_indexed"], ["python"])
        self.assertEqual(res["unsupported_files"], 0)


class TestNoPythonRepo(_Base):
    FILES = ["src/a.ts", "src/b.ts", "main.go"]

    def test_cli_says_nothing_was_indexed(self):
        out = self.run_index(self.repo(self.FILES))
        self.assertIn("languages indexed: none;", out)
        self.assertIn("3 file(s) skipped as unsupported", out)
        self.assertNotIn("languages indexed: python", out)

    def test_mcp_status(self):
        root = self.repo(self.FILES)
        self.run_index(root)
        res = M.index_status(path=root)
        self.assertEqual(res["languages_indexed"], [])
        self.assertEqual(res["unsupported_files"], 3)

    def test_unbuilt_status_still_says_python_only(self):
        res = M.index_status(path=self.repo(self.FILES))
        self.assertFalse(res["built"])
        self.assertIn("Python files only", res["note"])


class TestCountedInTheOneWalk(unittest.TestCase):
    def test_vendor_and_hidden_dirs_are_not_counted_and_walk_is_single(self):
        with tempfile.TemporaryDirectory() as root:
            for rel in ("a.py", "app.ts", "node_modules/x/y.js", ".hidden/z.go"):
                _write(root, rel)
            idx = KnowledgeIndex()
            idx.build(root)
            self.assertEqual(idx.unsupported, {"javascript": 1})   # pruned dirs never counted

    def test_abandoned_walk_leaves_no_stale_counts(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "a.py")
            _write(root, "b.ts")
            idx = KnowledgeIndex()
            idx.build(root)
            self.assertEqual(idx.unsupported, {"javascript": 1})
            gen = idx._iter_files(root, (".py",))
            next(gen)
            self.assertEqual(idx.unsupported, {})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
