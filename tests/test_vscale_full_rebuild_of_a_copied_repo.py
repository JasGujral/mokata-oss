"""0.0.27 V-SCALE — `mokata graph build --full` rebuilds a copied or renamed checkout.

Found on microsoft/TypeScript v7.0.2: after the checkout's directory was renamed, every
`mokata graph build --full` failed with code-review-graph's "the graph ... was built for a different
repository root ... Run `code-review-graph build` here" — and running exactly that failed the same
way, forever. A `cp -R repo repo2` reproduces it on any machine. The in-place rebuild opened the old
graph before writing a new one, and code-review-graph refuses a graph whose files are all under
another root that exists on the machine.

The rule now: `--full` over an existing index builds BESIDE it, into an empty directory, and swaps
the result in when it completes — the index in use serves (and survives a failure) until then.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata.knowledge import crg_build as B

CRG = shutil.which("code-review-graph")


def _complete_index(root, foreign_root):
    """A COMPLETE code-review-graph index whose files all live under `foreign_root`."""
    d = os.path.join(root, ".code-review-graph")
    os.makedirs(d, exist_ok=True)
    conn = sqlite3.connect(os.path.join(d, "graph.db"))
    conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
    conn.executemany("INSERT INTO metadata VALUES (?, ?)",
                     [("build_state", "complete"), ("last_updated", "2026-11-01T08:00:00"),
                      ("repo_root", foreign_root)])
    conn.commit()
    conn.close()


class FullRebuildNeverReopensTheOldGraph(unittest.TestCase):
    """What code-review-graph does with an old graph it is handed: refuses it when it belongs to
    another root. So a from-scratch rebuild must never hand it one."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vscale-full-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "copy")
        os.makedirs(os.path.join(self.root, ".mokata"))
        _complete_index(self.root, os.path.join(self.tmp, "original"))

    def runner(self, calls):
        def run(root, on_progress=None, incremental=False, data_dir=None):
            where = data_dir or os.path.join(root, B.INDEX_DIRNAME)
            calls.append((incremental, data_dir))
            if os.path.exists(os.path.join(where, B.DB_FILENAME)):
                return B.BuildResult(ok=False, error="the graph was built for a different "
                                                     "repository root")
            os.makedirs(where, exist_ok=True)
            conn = sqlite3.connect(os.path.join(where, B.DB_FILENAME))
            conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
            conn.executemany("INSERT INTO metadata VALUES (?, ?)",
                             [("build_state", "complete"), ("last_updated", "2026-11-03T09:00:00"),
                              ("repo_root", root)])
            conn.commit()
            conn.close()
            return B.BuildResult(ok=True)
        return run

    def test_a_full_rebuild_of_a_copied_checkout_succeeds_and_replaces_the_foreign_graph(self):
        calls = []
        res = B.build_now(self.root, full=True, runner=self.runner(calls))
        self.assertTrue(res.ok, res.error)
        self.assertEqual([(False, B.next_dir(self.root))], calls)     # from scratch, beside
        self.assertEqual(B.INDEX_BUILT, B.probe_index(self.root))
        self.assertEqual("2026-11-03T09:00:00", B._last_updated(B.db_path(self.root)))
        self.assertFalse(os.path.exists(os.path.join(B.next_dir(self.root), B.DB_FILENAME)))

    def test_a_failed_full_rebuild_keeps_the_index_in_use(self):
        def fails(root, on_progress=None, incremental=False, data_dir=None):
            return B.BuildResult(ok=False, error="boom")
        res = B.build_now(self.root, full=True, runner=fails)
        self.assertFalse(res.ok)
        self.assertEqual(B.INDEX_BUILT, B.probe_index(self.root))
        self.assertEqual("2026-11-01T08:00:00", B._last_updated(B.db_path(self.root)))


@unittest.skipUnless(CRG, "code-review-graph is not installed (the `full` extra)")
class TheRealToolOnACopiedRepo(unittest.TestCase):
    def setUp(self):
        # the suite switches the real tool off (`_support`); this test is about the real tool
        patch = mock.patch.dict(os.environ)
        patch.start()
        self.addCleanup(patch.stop)
        os.environ.pop("MOKATA_CRG", None)

    def test_cp_r_then_full_rebuild_answers_for_the_copy(self):
        tmp = tempfile.mkdtemp(prefix="vscale-cpr-")
        self.addCleanup(shutil.rmtree, tmp, True)
        a = os.path.join(tmp, "a")
        os.makedirs(os.path.join(a, ".mokata"))
        with open(os.path.join(a, "m.py"), "w", encoding="utf-8") as fh:
            fh.write("def a():\n    return b()\n\n\ndef b():\n    return 1\n")
        subprocess.run(["git", "init", "-q"], cwd=a, check=True, stdin=subprocess.DEVNULL)
        first = B.build_now(a)
        self.assertTrue(first.ok, first.error)
        b = os.path.join(tmp, "b")
        shutil.copytree(a, b)
        res = B.build_now(b, full=True)
        self.assertTrue(res.ok, res.error)
        conn = sqlite3.connect(f"file:{B.db_path(b)}?mode=ro", uri=True)
        try:
            paths = {r[0] for r in conn.execute("SELECT file_path FROM nodes")}
        finally:
            conn.close()
        want = os.path.realpath(os.path.join(b, "m.py"))
        self.assertTrue(paths and all(os.path.realpath(p) == want for p in paths), paths)


if __name__ == "__main__":                                  # pragma: no cover
    unittest.main()
