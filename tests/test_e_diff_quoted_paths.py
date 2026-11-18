"""0.0.29 E `IDE-DIFF-CONTEXT-LOWS` (first half) — a path git QUOTES in a diff header (a non-ASCII
byte, `"`, `\\`, a tab) is shown and cited as the real path, never as `"a/\\303\\251.txt"`.

`"`, `\\` and tab are not legal in a Windows file name, so on Windows only the non-ASCII cell runs
against real git; the parser cells (literal git output) run everywhere.
"""
import os
import shutil
import subprocess
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.ide import diffs as D

NAMES = ["é.txt", "日本/ü.md"] + ([] if os.name == "nt" else
                                  ['a"b.txt', "tab\there.txt", "back\\slash.txt"])


def _git(cwd, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e.invalid")
    env.pop("GIT_CONFIG_" + "PARAMETERS", None)
    return subprocess.run(["git", "-c", "core.quotepath=true", *args], cwd=cwd, check=True,
                          capture_output=True, env=env)


class TheParser(unittest.TestCase):

    def test_c_quoted_headers_and_renames_are_the_real_paths(self):
        text = ('diff --git "a/\\303\\251.txt" "b/\\303\\251.txt"\n'
                'index 1..2 100644\n--- "a/\\303\\251.txt"\n+++ "b/\\303\\251.txt"\n'
                '@@ -1 +1 @@\n-x\n+y\n'
                'diff --git "a/q\\"t.txt" "b/q\\"t.txt"\n@@ -1 +1 @@\n-x\n+y\n'
                'diff --git "a/t\\tb\\\\c.txt" "b/t\\tb\\\\c.txt"\n@@ -1 +1 @@\n-x\n+y\n'
                'diff --git a/plain name.txt b/plain name.txt\n@@ -1 +1 @@\n-x\n+y\n'
                'diff --git a/old.txt "b/n\\303\\251w.txt"\nsimilarity index 90%\n'
                'rename from old.txt\nrename to "n\\303\\251w.txt"\n'
                'diff --git "a/\\303\\251-old" "b/\\303\\251-new"\nsimilarity index 90%\n'
                'rename from "\\303\\251-old"\nrename to "\\303\\251-new"\n')
        files = D.parse_unified(text)
        self.assertEqual([f.path for f in files],
                         ["é.txt", 'q"t.txt', "t\tb\\c.txt", "plain name.txt", "néw.txt", "é-new"])
        self.assertEqual(files[4].old_path, "old.txt")
        self.assertEqual(files[5].old_path, "é-old")
        self.assertEqual(files[5].status, "renamed")
        self.assertEqual([ln.new for ln in files[0].hunks[0].lines if ln.op == "+"], [1])

    def test_unquote_leaves_an_unquoted_path_and_odd_escapes_alone(self):
        self.assertEqual(D._unquote_c("a/b c.txt"), "a/b c.txt")
        self.assertEqual(D._unquote_c('"a\\303"'), "a�")          # a cut UTF-8 sequence
        self.assertEqual(D._unquote_c('"x\\qy"'), "x\\qy")


@unittest.skipUnless(shutil.which("git"), "needs git")
class RealGit(unittest.TestCase):

    def test_the_live_diff_names_every_quoted_path_as_it_is_on_disk(self):
        root = tempfile.mkdtemp(prefix="e-quoted-")
        _git(root, "init", "-q")
        for rel in NAMES:
            p = os.path.join(root, *rel.split("/"))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("one\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "x")
        out = _git(root, "diff", "HEAD").stdout                # nothing yet; quoting proven below
        self.assertEqual(out, b"")
        for rel in NAMES:
            with open(os.path.join(root, *rel.split("/")), "w", encoding="utf-8") as fh:
                fh.write("two\n")
        raw = _git(root, "diff", "HEAD").stdout.decode("utf-8")
        self.assertIn('"a/\\303\\251.txt"', raw)                # git really quoted it
        got = sorted(f.path for f in D.live(root, mokata_files=True).files)
        self.assertEqual(got, sorted(NAMES))


if __name__ == "__main__":
    unittest.main()
