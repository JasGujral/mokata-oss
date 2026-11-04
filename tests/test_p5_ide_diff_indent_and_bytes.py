"""0.0.27 P5 round 2 — ignore-whitespace judges indentation by what the file IS, and a context
page is bounded in BYTES as well as lines (ID-DIFF-INTRALINE).

  * Indentation is hidden only where the file is KNOWN to be a language in which leading
    whitespace is not code (a brace language, markup, data). Everything else — an unknown
    extension, no extension, a shebang naming python/make — keeps its indentation changes and
    counts them (`ws_kept`). When unsure, keep: hiding a Python block move as "no change" is the
    one failure this feature may never have.
  * A context page never carries more than `LINE_BYTES` of one line nor about `PAGE_BYTES` in all.
    A line longer than that is CUT and the page says so (`cut: [[index, total bytes]]`); lines
    before the page are skipped in bounded chunks, never read whole into memory.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import shutil
import tempfile
import tracemalloc
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.ide import diffview as DV
from test_p5_ide_diff_split_intraline import _client, _file, _git, _repo, _write


def _facts(path, lines):
    return {"files": [{"path": path, "hunks": [{"header": "@@ -1,9 +1,9 @@", "lines": lines}]}]}


# ============================================================================ indentation (M1)
class IndentationIsJudgedByWhatTheFileIs(unittest.TestCase):

    def test_files_where_indentation_is_code_keep_it(self):
        for name in ("src/m.py", "a.pyi", "ci.yml", ".gitlab-ci.yml", "k8s.yaml", "K8S.YAML",
                     "Makefile", "makefile", "GNUmakefile", "rules.mk", "Makefile.am",
                     "makefile.inc", "build.mak", "Snakefile", "rules.smk", "fast.pyx", "fast.pxd",
                     "Justfile", "player.gd", "main.nim", "a.hs", "app.coffee", "x.sass",
                     "view.pug", "notes.md", "README.rst", "data.txt", "bin/script", "Dockerfile",
                     "conf.cfg"):
            with self.subTest(name=name):
                self.assertTrue(DV.indent_significant(name), name)

    def test_files_known_to_ignore_indentation_may_hide_it(self):
        for name in ("web/app.js", "a.mjs", "b.ts", "c.tsx", "d.go", "e.rs", "f.c", "g.cpp",
                     "h.java", "i.cs", "j.json", "k.css", "l.html", "m.xml", "n.sh", "o.rb",
                     "p.php", "q.sql", "r.toml", "s.kt", "t.swift"):
            with self.subTest(name=name):
                self.assertFalse(DV.indent_significant(name), name)

    def test_no_indentation_significant_interpreter_is_on_the_indentation_free_list(self):
        """The list that lets "Ignore whitespace" hide a re-indent may never hold a language where
        indentation is code - under ANY spelling of its name (`python3`, `pypy3`, `python3.11`
        all reach the list as `python`, so a versioned spelling on the list is also dead weight)."""
        significant = {"python", "python2", "python3", "py", "pypy", "pypy3", "ipython", "make",
                       "gmake", "bmake", "nim", "coffee", "haml", "slim", "yaml", "cython",
                       "snakemake", "gdscript", "godot", "just", "mojo"}
        listed = set(DV.INDENT_FREE_INTERPRETERS)
        self.assertEqual(set(), listed & significant, sorted(listed & significant))
        for name in listed:
            with self.subTest(interpreter=name):
                self.assertEqual(name, DV._VERSION_TAIL.sub("", name),
                                 "a name carrying a version tail can never match: _interpreter strips it")
                self.assertFalse(DV.indent_significant("bin/x", f"#!/usr/bin/env {name}"), name)
        for spelled in ("python", "python3", "python3.12", "pypy3", "make", "gmake"):
            with self.subTest(shebang=spelled):
                self.assertTrue(DV.indent_significant("bin/x", f"#!/usr/bin/{spelled}"), spelled)

    def test_a_shebang_decides_before_the_name(self):
        self.assertTrue(DV.indent_significant("bin/script", "#!/usr/bin/env python3"))
        self.assertTrue(DV.indent_significant("bin/script", "#!/usr/bin/python3.11 -u"))
        self.assertTrue(DV.indent_significant("tool.sh", "#!/usr/bin/env -S python3 -u"))
        self.assertTrue(DV.indent_significant("bin/build", "#!/usr/bin/make -f"))
        self.assertFalse(DV.indent_significant("bin/run", "#!/bin/bash"))
        self.assertFalse(DV.indent_significant("bin/run", "#!/usr/bin/env node"))
        # env's own flags and VAR=value words are not the interpreter; a version suffix is not part
        # of its name — a free interpreter written either way is still recognised
        self.assertFalse(DV.indent_significant("bin/run", "#!/usr/bin/env -S bash -e"))
        self.assertFalse(DV.indent_significant("bin/run", "#!/usr/bin/env LC_ALL=C perl"))
        self.assertFalse(DV.indent_significant("bin/run", "#!/usr/bin/ruby3.2"))
        self.assertTrue(DV.indent_significant("bin/run", "#!/opt/weird/interp"),
                        "an interpreter nobody listed: unsure, so keep")
        self.assertTrue(DV.indent_significant("bin/run", "not a shebang"))

    def test_a_python_shebang_file_with_no_extension_keeps_its_block_move(self):
        lines = [[" ", 1, 1, "#!/usr/bin/env python3"], [" ", 2, 2, "if x:"],
                 [" ", 3, 3, "    run()"], ["-", 4, None, "stop()"],
                 ["+", None, 4, "    stop()"]]
        f = DV.ignore_whitespace(_facts("bin/script", lines))["files"][0]
        self.assertEqual((0, 1), (f["ws_hidden"], f["ws_kept"]))
        self.assertFalse(f["ws_only"])

    def test_a_bash_shebang_at_the_top_lets_a_reindent_go(self):
        lines = [[" ", 1, 1, "#!/bin/bash"], ["-", 2, None, "  echo hi"],
                 ["+", None, 2, "    echo hi"]]
        f = DV.ignore_whitespace(_facts("bin/run", lines))["files"][0]
        self.assertEqual((1, 0), (f["ws_hidden"], f["ws_kept"]))

    def test_with_no_first_line_in_view_an_extensionless_file_keeps_indentation(self):
        lines = [["-", 40, None, "  echo hi"], ["+", None, 40, "    echo hi"]]
        f = DV.ignore_whitespace(_facts("bin/run", lines))["files"][0]
        self.assertEqual((0, 1), (f["ws_hidden"], f["ws_kept"]))

    def test_a_blank_line_whose_spaces_changed_is_whitespace_even_in_python(self):
        lines = [["-", 3, None, "    "], ["+", None, 3, ""]]
        f = DV.ignore_whitespace(_facts("src/m.py", lines))["files"][0]
        self.assertEqual((1, 0), (f["ws_hidden"], f["ws_kept"]))

    def test_a_carriage_return_is_not_indentation(self):
        lines = [["-", 3, None, "\rfoo()"], ["+", None, 3, "foo()"]]
        f = DV.ignore_whitespace(_facts("src/m.py", lines))["files"][0]
        self.assertEqual((1, 0), (f["ws_hidden"], f["ws_kept"]))

    def test_through_the_route_the_files_the_verifier_named_are_kept(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        _repo(d)
        old = {"script": "#!/usr/bin/env python3\nif x:\n    run()\nstop()\n",
               "Snakefile": "rule a:\n    input: 'x'\nshell: 'y'\n",
               "a.pyx": "def f():\n    pass\nreturn 1\n",
               "Makefile.am": "all:\n\tcc x\nfoo:\n",
               "makefile.inc": "all:\n\tcc x\nfoo:\n",
               "Justfile": "build:\n    cargo b\ntest:\n",
               "a.gd": "func f():\n    pass\nreturn\n"}
        for name, text in old.items():
            _write(d, name, text)
        _git(d, "add", "-A")
        _git(d, "commit", "-q", "-m", "base")
        for name, text in old.items():
            lines = text.split("\n")
            lines[2] = ("\t" if name.startswith(("Makefile", "makefile")) else "    ") + lines[2]
            _write(d, name, "\n".join(lines))
        c = _client(d)
        self.addCleanup(c.state.jobs.close)
        facts = c.get("/api/live?ws=ignore").json()
        for name in old:
            with self.subTest(name=name):
                f = _file(facts, name)
                self.assertEqual(0, f["ws_hidden"], name)
                self.assertEqual(1, f["ws_kept"], name)
                self.assertFalse(f["ws_only"], name)


# ================================================================================ bytes (M2)
class AContextPageIsBoundedInBytes(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.d = d = tempfile.mkdtemp()
        _repo(d)
        cls.huge = 24 * 1024 * 1024
        cls.long = DV.LINE_BYTES * 3 + 7
        body = (b"first\n" + b"x" * cls.huge + b"\n" + b"y" * cls.long + b"\nlast\n"
                + "é".encode("utf-8") * (DV.LINE_BYTES // 2 + 5) + b"\n")
        _write(d, "big.txt", body)
        _write(d, "wide.txt", "".join("%05d" % i + "z" * (DV.LINE_BYTES - 7) + "\n"
                                      for i in range(60)))
        _git(d, "add", "-A")
        _git(d, "commit", "-q", "-m", "base")
        _write(d, "big.txt", body + b"tail\n")
        cls.c = _client(d)

    @classmethod
    def tearDownClass(cls):
        cls.c.state.jobs.close()
        shutil.rmtree(cls.d, ignore_errors=True)

    def page(self, **q):
        r = self.c.get("/api/diff/context", params=q)
        self.assertEqual(200, r.status_code, r.text[:300])
        self.assertLess(len(r.content), DV.PAGE_BYTES * 2 + 65536, "the body is bounded")
        return r.json()

    def test_a_long_line_is_cut_and_the_page_says_so(self):
        for side in ({}, {"rev": "HEAD"}):
            with self.subTest(side=side):
                d = self.page(path="big.txt", start=3, count=2, **side)
                self.assertEqual(2, len(d["lines"]))
                self.assertLessEqual(len(d["lines"][0].encode("utf-8")), DV.LINE_BYTES)
                self.assertTrue(d["lines"][0].startswith("y" * 100))
                self.assertEqual([[0, self.long]], d["cut"])
                self.assertEqual("last", d["lines"][1])

    def test_a_huge_line_is_cut_too(self):
        d = self.page(path="big.txt", start=2, count=1)
        self.assertEqual([[0, self.huge]], d["cut"])
        self.assertEqual(DV.LINE_BYTES, len(d["lines"][0]))

    def test_a_cut_never_splits_a_character(self):
        d = self.page(path="big.txt", start=5, count=1)
        self.assertTrue(d["cut"])
        self.assertNotIn("\ufffd", d["lines"][0])
        self.assertEqual({"é"}, set(d["lines"][0]))

    def test_a_cut_through_a_multibyte_character_drops_only_that_character(self):
        for whole in ("é", "€", "😀"):
            b = ("ab" + whole).encode("utf-8")
            for k in range(1, len(whole.encode("utf-8"))):
                with self.subTest(ch=whole, keep=k):
                    self.assertEqual("ab", DV._cut_text(b[:2 + k]))
            self.assertEqual("ab" + whole, DV._cut_text(b))

    def test_a_short_line_is_not_cut(self):
        d = self.page(path="big.txt", start=1, count=1)
        self.assertEqual((["first"], []), (d["lines"], d["cut"]))

    def test_skipping_a_huge_line_does_not_hold_it(self):
        for side in ({}, {"rev": "HEAD"}):
            with self.subTest(side=side):
                tracemalloc.start()
                try:
                    got = DV.context_page(self.d, "big.txt", 4, 1, **side)
                    _cur, peak = tracemalloc.get_traced_memory()
                finally:
                    tracemalloc.stop()
                self.assertEqual(["last"], got["lines"])
                self.assertLess(peak, 8 * 1024 * 1024, "a skipped 24 MB line was held whole")

    def test_a_page_stops_at_its_byte_budget_and_says_where_to_go_on(self):
        d = self.page(path="wide.txt", start=1, count=60)
        n = len(d["lines"])
        self.assertGreaterEqual(n, 1)
        self.assertLess(n, 60, "a page of 60 x LINE_BYTES must stop at PAGE_BYTES")
        self.assertLessEqual(sum(len(x) for x in d["lines"]), DV.PAGE_BYTES + DV.LINE_BYTES)
        self.assertFalse(d["eof"])
        self.assertEqual(1 + n, d["next"])
        self.assertEqual([], d["cut"])
        d2 = self.page(path="wide.txt", start=d["next"], count=1)
        self.assertTrue(d2["lines"][0].startswith("%05d" % n))

    def test_the_bounds_are_stated_not_tiny(self):
        self.assertGreaterEqual(DV.LINE_BYTES, 16 * 1024)
        self.assertGreaterEqual(DV.PAGE_BYTES, 8 * DV.LINE_BYTES)
        self.assertEqual(json.loads(json.dumps(self.page(path="big.txt", start=1, count=1)))["cut"],
                         [])


class IntraLineIsBoundedByTheBody(unittest.TestCase):

    def test_a_body_past_the_bound_is_refused_not_read(self):
        from mokata.ide.features import diffview as F
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        _repo(d)
        c = _client(d)
        self.addCleanup(c.state.jobs.close)
        big = "a" * (F.BODY_MAX // 2 + 10)
        r = c.post("/api/diff/intraline", json={"pairs": [[big, big + "b"]]},
                   headers={"origin": "http://127.0.0.1:9999"})
        self.assertEqual(413, r.status_code)


if __name__ == "__main__":
    unittest.main()
