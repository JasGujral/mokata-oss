"""0.0.25 K4 — the post-answer freshness recheck costs one `stat` per FILE, not one hash per REFERENCE.

`recheck_after_answer` runs after every answer the layer gives. It hashed the file behind EVERY
reference: on a hub answer, 86k SHA-256 reads over 2.7k distinct files, per query. It now asks one
question per distinct file, and only a file whose (mtime, size, ctime) moved off its baseline record
is hashed. The verdicts are the same ones `stale_against` gives — including the attack review
finding 4-1 closed (a same-size write with its mtime restored), which the ctime still catches.

Mutations this module was checked against (each went RED, then was restored):
  M1 paths not de-duplicated (one verdict per reference)  -> test_a_changed_file_is_hashed_once
  M2 `stat_unchanged` skipped (hash everything)           -> test_an_unchanged_answer_hashes_nothing,
                                                             test_a_changed_file_is_hashed_once
  M3 `stat_unchanged` ignores ctime                       -> test_a_same_size_write_with_its_mtime_restored_is_still_caught
"""

import _support  # noqa: F401

import os
import tempfile
import time
import unittest
from unittest import mock

from mokata.knowledge import freshness as fr
from mokata.knowledge import index as index_mod
from mokata.knowledge.index import KnowledgeIndex
from mokata.knowledge.query import QueryResult, Reference
from mokata.state import StateStore

FILES = ("a.py", "b.py", "c.py")


class _Layer:
    primary = None
    fallback = None


@unittest.skipUnless(fr.CTIME_CLAUSE_CATCHES_A_RESTORED_WRITE,
                     "the stat short-cut is POSIX-only (ctime is creation time on Windows)")
class TestRecheckCost(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="k4-")
        for name in FILES:
            with open(os.path.join(self.root, name), "w", encoding="utf-8") as fh:
                fh.write(f"def {name[0]}():\n    return 1\n")
        self.store = StateStore(tempfile.mkdtemp(prefix="k4-state-"))
        self.ctl = fr.FreshnessController(self.root, state=self.store, session_id="k4-session")
        idx = KnowledgeIndex()
        idx.build(self.root, (".py",))
        self.ctl._save_index(idx)
        # 600 references over 3 files: the shape of a hub answer
        self.result = QueryResult(kind="callers", target="x", basis="structural", references=[
            Reference(FILES[i % 3], i + 1, "", None) for i in range(600)])

    def _hashes(self):
        calls = []
        real = index_mod.file_fingerprint

        def spy(path):
            calls.append(os.path.basename(path))
            return real(path)
        return calls, mock.patch.object(index_mod, "file_fingerprint", side_effect=spy)

    def test_an_unchanged_answer_hashes_nothing(self):
        calls, patch = self._hashes()
        with patch:
            self.assertFalse(self.ctl.recheck_after_answer(_Layer(), self.result))
        self.assertEqual(calls, [], "unchanged files were hashed")

    def test_a_changed_file_is_hashed_once(self):
        time.sleep(0.01)
        with open(os.path.join(self.root, "b.py"), "a", encoding="utf-8") as fh:
            fh.write("# edited out of band\n")
        calls, patch = self._hashes()
        with patch:
            self.assertTrue(self.ctl.recheck_after_answer(_Layer(), self.result))
        # one confirm hash + one reindex hash for b.py; a.py / c.py never hashed
        self.assertEqual(sorted(set(calls)), ["b.py"])
        self.assertLessEqual(len(calls), 2, calls)

    def test_a_same_size_write_with_its_mtime_restored_is_still_caught(self):
        path = os.path.join(self.root, "c.py")
        st = os.stat(path)
        time.sleep(0.02)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("def z():\n    return 1\n")           # same size, different content
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))  # mtime put back
        self.assertEqual(os.stat(path).st_size, st.st_size)
        self.assertTrue(self.ctl.recheck_after_answer(_Layer(), self.result))

    def test_the_verdicts_match_stale_against(self):
        with open(os.path.join(self.root, "a.py"), "a", encoding="utf-8") as fh:
            fh.write("# moved\n")
        os.remove(os.path.join(self.root, "c.py"))
        base = self.ctl._load_index()
        for rel in FILES:
            quick = (not fr.stat_unchanged(base, self.root, rel)
                     and fr.stale_against(base, self.root, rel))
            self.assertEqual(quick, fr.stale_against(base, self.root, rel), rel)


if __name__ == "__main__":
    unittest.main()
