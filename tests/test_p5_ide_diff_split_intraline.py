"""0.0.27 P5 — `ID-DIFF-SPLIT` + `ID-DIFF-INTRALINE`: how the review screen SHOWS a diff.

Each test states the user-visible rule it protects. The rules:
  * intra-line marks show WHICH tokens of a changed line changed — a renamed word is marked whole,
    a tab that became spaces is marked, a CRLF->LF change is marked (never two identical lines);
  * the work is bounded by the rows in view and its cost is measured BEFORE it runs: a batch of
    ordinary lines answers at once; a batch over the bound (a minified 5 MB line) is a visible job
    with progress in tokens (done <= total) and Cancel; a gap the algorithm could not align is
    said to be ONE span (`basis: "span"`), never presented as exact;
  * "ignore whitespace" is truthful: it says how many line pairs it hid, never hides a real
    change, never hides a change of the file's final newline, and never hides an indentation
    change in a language where indentation is code (Python, YAML, Makefile) — those are kept and
    counted; a file whose only change was whitespace stays listed and says so;
  * line endings are shown as they are: a CRLF line keeps its "\\r", a bare "\\r" inside a line
    does not break the line numbering, and "no newline at end of file" is recorded per side;
  * expanding context is served in PAGES from the new side (working tree, a revision, a
    snapshot, a session's worktree), reads only as far as the page, refuses binary content and a
    path outside the repository, and every file says where its new side is read from;
  * a rename keeps both paths, so each side of a split view cites the right file.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
import tracemalloc
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.ide import diffs as D
from mokata.ide import diffview as DV

ORIGIN = {"Origin": "http://127.0.0.1:9999"}


def _git(d, *a):
    subprocess.run(["git", "-C", d, *a], check=True, capture_output=True,
                   stdin=subprocess.DEVNULL)


def _write(d, rel, data):
    p = os.path.join(d, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as fh:
        fh.write(data.encode("utf-8") if isinstance(data, str) else data)


def _repo(d):
    _git(d, "init", "-q", "-b", "main")
    _git(d, "config", "user.email", "t@t")
    _git(d, "config", "user.name", "t")
    _git(d, "config", "core.autocrlf", "false")


def _client(root, demo=False):
    from starlette.testclient import TestClient
    from mokata.ide.server import AppState, build_app
    state = AppState(root, "tok", 9999, None, demo)
    c = TestClient(build_app(state), base_url="http://127.0.0.1:9999")
    c.get("/pair?t=tok")
    c.state = state
    return c


def _marked(text, ranges):
    return [text[s:e] for s, e in ranges]


def _file(facts, path):
    return next(f for f in facts["files"] if f["path"] == path)


# =================================================================================== intra-line
class IntraLineMarksTheChangedTokens(unittest.TestCase):

    def test_a_renamed_word_is_marked_whole_and_the_rest_of_the_line_is_not(self):
        """`count` -> `counter`: the reader sees the WORD changed, not a stray `er`."""
        r = DV.intraline("    total = count + 1", "    total = counter + 1")
        self.assertEqual(["count"], _marked("    total = count + 1", r["old"]))
        self.assertEqual(["counter"], _marked("    total = counter + 1", r["new"]))
        self.assertEqual("token", r["basis"])

    def test_two_separate_edits_are_two_marks(self):
        a, b = "f(alpha, beta, gamma)", "f(alpha, BETA, gamma, delta)"
        r = DV.intraline(a, b)
        self.assertEqual(["beta"], _marked(a, r["old"]))
        self.assertIn("BETA", _marked(b, r["new"]))
        self.assertTrue(any("delta" in m for m in _marked(b, r["new"])))
        self.assertNotIn("gamma", "".join(_marked(b, r["new"])))

    def test_a_tab_that_became_spaces_is_marked(self):
        r = DV.intraline("a\tb", "a    b")
        self.assertEqual(["\t"], _marked("a\tb", r["old"]))
        self.assertEqual(["    "], _marked("a    b", r["new"]))

    def test_a_crlf_to_lf_change_is_marked_not_shown_as_two_identical_lines(self):
        r = DV.intraline("x = 1\r", "x = 1")
        self.assertEqual(["\r"], _marked("x = 1\r", r["old"]))
        self.assertEqual([], r["new"])

    def test_equal_lines_mark_nothing(self):
        self.assertEqual({"old": [], "new": [], "basis": "same", "tokens": 0},
                         DV.intraline("same", "same"))

    def test_markup_in_a_line_is_only_ever_offsets(self):
        """The answer is character ranges — never HTML: a `<script>` in a diff stays text."""
        a, b = "<script>alert(1)</script>", "<script>alert(2)</script>"
        r = DV.intraline(a, b)
        self.assertEqual(["1"], _marked(a, r["old"]))
        for s, e in r["old"] + r["new"]:
            self.assertIsInstance(s, int)
            self.assertIsInstance(e, int)

    def test_a_gap_with_nothing_to_align_says_span_never_exact(self):
        """Two long lines with no token in common past the trim: one span, and the answer SAYS
        it is a span — the page labels it, it is never shown as an exact token diff."""
        a = " ".join("a%d" % (i % 7) for i in range(600))
        b = " ".join("b%d" % (i % 5) for i in range(600))
        r = DV.intraline(a, b)
        self.assertEqual("span", r["basis"])
        self.assertTrue(r["old"] and r["new"])


class AHugeLineIsBoundedByItsTokens(unittest.TestCase):

    def test_a_5mb_minified_line_with_one_edit_answers_in_linear_time(self):
        """Measured on the input (the line), not a timer: the common head and tail are trimmed in
        one pass, so one edit in a 5 MB line costs a handful of tokens."""
        base = ("var a=function(b){return b+1};" * 170000)[:5_000_000]
        new = base[:2_500_000] + "CHANGED" + base[2_500_007:]
        t0 = time.perf_counter()
        r = DV.intraline(base, new)
        took = time.perf_counter() - t0
        self.assertLess(DV.middle_tokens(base, new), 10)
        self.assertEqual("token", r["basis"])
        self.assertEqual(1, len(r["new"]))
        s, e = r["new"][0]
        self.assertIn("CHANGED", new[s:e])
        self.assertLess(took, 5.0, "one edit in a 5 MB line must not be super-linear")

    def test_memory_for_a_large_rewrite_stays_proportional_to_the_line(self):
        """2 MB of rewritten line: token ids and an offsets array, not strings and tuples."""
        import random
        rnd = random.Random(5)
        words = ["var", "a", "b", "(", ")", "{", "}", ";", "=", "+", "1", "x"]
        a = "".join(rnd.choice(words) for _ in range(150000))
        b = "".join(rnd.choice(words) for _ in range(150000))
        tracemalloc.start()
        try:
            DV.intraline(a, b)
            _cur, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 60 * len(a) + 8_000_000, peak)


class TheCostDecidesJobOrNot(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()
        _repo(cls.d)
        _write(cls.d, "a.txt", "x\n")
        _git(cls.d, "add", "-A")
        _git(cls.d, "commit", "-q", "-m", "a")
        cls.c = _client(cls.d)

    @classmethod
    def tearDownClass(cls):
        cls.c.state.jobs.close()
        shutil.rmtree(cls.d, ignore_errors=True)

    def post(self, body):
        return self.c.post("/api/diff/intraline", json=body, headers=ORIGIN)

    def test_ordinary_lines_answer_at_once_with_their_cost(self):
        r = self.post({"pairs": [["    count = 1", "    counter = 1"]]})
        self.assertEqual(200, r.status_code)
        d = r.json()
        self.assertEqual(1, len(d["pairs"]))
        self.assertLessEqual(d["cost"], DV.SYNC_TOKENS)
        self.assertIsNone(d["job"])

    def test_a_batch_over_the_bound_is_a_job_with_true_progress_and_its_answer(self):
        """A rewritten minified line is visible work: 202 + a job whose progress is in tokens
        (done <= total), and the page that waited gets the finished answer by naming the job."""
        a = " ".join("t%d" % i for i in range(DV.SYNC_TOKENS))
        b = " ".join("u%d" % i for i in range(DV.SYNC_TOKENS))
        r = self.post({"pairs": [[a, b]]})
        self.assertEqual(202, r.status_code, r.text[:200])
        h = r.json()
        self.assertEqual("diff.intraline", h["kind"])
        job = self.c.state.jobs.get(h["job"])
        self.assertTrue(job.finished.wait(60))
        snap = job.snapshot()
        self.assertEqual("done", snap["state"])
        self.assertEqual("tokens", snap["unit"])
        self.assertLessEqual(snap["done"], snap["total"])
        again = self.post({"pairs": [[a, b]], "job": h["job"]})
        self.assertEqual(200, again.status_code)
        self.assertEqual(snap["total"], again.json()["cost"])
        self.assertEqual("span", again.json()["pairs"][0]["basis"])

    def test_cancel_stops_the_work(self):
        from mokata.knowledge import cancel as C
        flag = __import__("threading").Event()
        flag.set()
        with C.scope(flag):
            with self.assertRaises(C.Cancelled):
                DV.batch([("a b c", "a x c")])

    def test_a_malformed_pair_is_refused(self):
        self.assertEqual(400, self.post({"pairs": [["one side only"]]}).status_code)
        self.assertEqual(400, self.post({"pairs": [[1, 2]]}).status_code)
        self.assertEqual(400, self.post({"pairs": [["a", "b"]], "sync_ms": "0"}).status_code)


# =================================================================================== line endings
class LineEndingsAreShownAsTheyAre(unittest.TestCase):

    def test_git_output_keeps_crlf_and_a_bare_cr_does_not_break_numbering(self):
        text = ("diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1,3 +1,3 @@\n a\n-b\r\n+b\n"
                " c\rstill c\n")
        f = D.parse_unified(text)[0]
        lines = [(ln.op, ln.old, ln.new, ln.text) for ln in f.hunks[0].lines]
        self.assertEqual([(" ", 1, 1, "a"), ("-", 2, None, "b\r"), ("+", None, 2, "b"),
                          (" ", 3, 3, "c\rstill c")], lines)

    def test_no_newline_at_end_of_file_is_recorded_per_side(self):
        text = ("diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1,2 +1,2 @@\n a\n-b\n"
                "\\ No newline at end of file\n+b\n")
        f = D.parse_unified(text)[0]
        self.assertEqual([2, None], f.hunks[0].noeol)
        self.assertEqual([2, None], D.DiffSet("t", [f]).facts()["files"][0]["hunks"][0]["noeol"])

    def test_a_snapshot_diff_of_a_crlf_only_change_is_not_empty(self):
        """Two texts that differ only in line endings: the reader sees the change (they used to
        get a changed file with no lines)."""
        f = D.text_diff("x.txt", "a\r\nb\r\n", "a\nb\n")
        self.assertEqual(2, f.removed)
        self.assertEqual(2, f.added)
        self.assertIn("a\r", [ln.text for h in f.hunks for ln in h.lines])

    def test_a_snapshot_diff_of_a_dropped_final_newline_is_a_change(self):
        f = D.text_diff("x.txt", "a\nb\n", "a\nb")
        self.assertEqual(1, f.removed)
        self.assertEqual(1, f.added)
        self.assertEqual([None, 2], f.hunks[0].noeol)

    def test_text_diff_headers_and_numbers_match_git(self):
        f = D.text_diff("x", "1\n2\n3\n4\n5\n6\n7\n8\n9\n", "1\n2\n3\n4\nfive\n6\n7\n8\n9\n")
        self.assertEqual("@@ -2,7 +2,7 @@", f.hunks[0].header)
        self.assertEqual(("-", 5, None, "5"), tuple(vars(f.hunks[0].lines[3]).values()))
        self.assertEqual("@@ -0,0 +1,2 @@", D.text_diff("n", "", "a\nb\n").hunks[0].header)


# =================================================================================== whitespace
class IgnoreWhitespaceIsTruthful(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.d = d = tempfile.mkdtemp()
        _repo(d)
        _write(d, "web/app.js", "function f() {\n  return 1;\n}\nconst x = 2;\n")
        _write(d, "src/m.py", "def f():\n    if a:\n        b()\n    c()\n")
        _write(d, "src/crlf.txt", "one\r\ntwo\r\n")
        _write(d, "src/tail.txt", "end\n")
        _write(d, "src/only.js", "a();\nb();\n")
        _write(d, "bin.dat", b"\0\1\2")
        _git(d, "add", "-A")
        _git(d, "commit", "-q", "-m", "base")
        # re-indent (whitespace) + one real change
        _write(d, "web/app.js", "function f() {\n    return 1;\n}\nconst x = 3;\n")
        # Python: the indentation change moves c() into the if — code, not whitespace
        _write(d, "src/m.py", "def f():\n    if a:\n        b()\n        c()\n")
        _write(d, "src/crlf.txt", "one\ntwo\n")
        _write(d, "src/tail.txt", "end")
        _write(d, "src/only.js", "a( );\nb();\n")
        _write(d, "bin.dat", b"\0\1\3")
        cls.c = _client(d)

    @classmethod
    def tearDownClass(cls):
        cls.c.state.jobs.close()
        shutil.rmtree(cls.d, ignore_errors=True)

    def live(self, ws):
        r = self.c.get("/api/live" + ("?ws=" + ws if ws else ""))
        self.assertEqual(200, r.status_code, r.text[:300])
        return r.json()

    def test_shown_by_default_and_said_so(self):
        d = self.live("")
        self.assertEqual("show", d["ws"])
        self.assertEqual(1, _file(d, "web/app.js")["added"] - 1)

    def test_a_reindent_is_hidden_and_counted_and_the_real_change_stays(self):
        f = _file(self.live("ignore"), "web/app.js")
        self.assertEqual(1, f["ws_hidden"])
        self.assertEqual(1, f["added"])
        self.assertEqual(1, f["removed"])
        texts = [ln[3] for h in f["hunks"] for ln in h["lines"] if ln[0] != " "]
        self.assertEqual(["const x = 2;", "const x = 3;"], texts)

    def test_python_indentation_is_never_hidden_and_is_counted_as_kept(self):
        f = _file(self.live("ignore"), "src/m.py")
        self.assertEqual(0, f["ws_hidden"])
        self.assertEqual(1, f["ws_kept"])
        self.assertEqual(1, f["added"])

    def test_a_crlf_only_file_is_whitespace_and_stays_listed(self):
        d = self.live("ignore")
        f = _file(d, "src/crlf.txt")
        self.assertTrue(f["ws_only"])
        self.assertEqual(2, f["ws_hidden"])
        self.assertEqual([], f["hunks"])
        self.assertEqual(0, f["added"] + f["removed"])

    def test_a_dropped_final_newline_is_never_hidden(self):
        f = _file(self.live("ignore"), "src/tail.txt")
        self.assertEqual(0, f["ws_hidden"])
        self.assertEqual(1, f["added"])

    def test_inner_whitespace_only_is_hidden(self):
        f = _file(self.live("ignore"), "src/only.js")
        self.assertTrue(f["ws_only"])
        self.assertEqual(1, f["ws_hidden"])

    def test_binary_is_untouched_and_the_totals_add_up(self):
        d = self.live("ignore")
        self.assertTrue(_file(d, "bin.dat")["binary"])
        self.assertEqual(sum(f["ws_hidden"] for f in d["files"]), d["ws_hidden"])
        self.assertEqual(sum(f["added"] for f in d["files"]), d["added"])
        self.assertEqual("ignore", d["ws"])

    def test_a_file_with_no_lines_keeps_the_counts_it_came_with(self):
        # binary / too-large: git's own counts are all the reader has; ignore-whitespace has no
        # lines to judge, so it must not re-derive (and zero) them
        from mokata.ide import diffview as DV
        facts = {"files": [{"path": "big.bin", "binary": True, "too_large": True, "hunks": [],
                            "added": 7, "removed": 3}]}
        got = DV.apply_ws(facts, "ignore")["files"][0]
        self.assertEqual((7, 3, 0), (got["added"], got["removed"], got["ws_hidden"]))
        self.assertNotIn("ws_only", got)

    def test_every_diff_route_takes_ws_and_refuses_nonsense(self):
        self.assertEqual(400, self.c.get("/api/live?ws=all").status_code)
        self.assertEqual(200, self.c.get("/api/compare?base=HEAD&head=main&ws=ignore").status_code)
        self.assertEqual(400, self.c.post("/api/pr", json={"url": "", "ws": "x"},
                                          headers=ORIGIN).status_code)

    def test_a_big_reindent_is_linear(self):
        """10,000 re-indented lines (an agent re-formatting a file) are hidden in one pass."""
        lines = [[" ", 0, 0, "x"]]
        dels = [["-", i, None, "  v%d();" % i] for i in range(1, 10001)]
        adds = [["+", None, i, "    v%d();" % i] for i in range(1, 10001)]
        facts = {"files": [{"path": "a.js", "hunks": [{"header": "@@", "lines": lines + dels + adds}],
                            "added": 10000, "removed": 10000}]}
        t0 = time.perf_counter()
        out = DV.ignore_whitespace(facts)
        self.assertLess(time.perf_counter() - t0, 2.0)
        self.assertEqual(10000, out["ws_hidden"])
        self.assertTrue(out["files"][0]["ws_only"])


# =================================================================================== context pages
class ContextComesInPages(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.d = d = tempfile.mkdtemp()
        _repo(d)
        body = "".join("line %d\r\n" % i for i in range(1, 201))
        _write(d, "src/big.txt", body)
        _write(d, "src/old_name.py", "".join("v%d = %d\n" % (i, i) for i in range(1, 61)))
        _write(d, "img.bin", b"\0" * 100 + b"\n" * 50)
        _write(d, "src/huge.txt", "".join("h%d\n" % i for i in range(1, 1201)))
        _git(d, "add", "-A")
        _git(d, "commit", "-q", "-m", "base")
        _write(d, "src/big.txt", body.replace("line 100\r\n", "line hundred\r\n"))
        _git(d, "checkout", "-q", "-b", "topic")
        _git(d, "mv", "src/old_name.py", "src/new_name.py")
        _write(d, "src/new_name.py", "".join("v%d = %d\n" % (i, i if i != 30 else 99)
                                              for i in range(1, 61)))
        _git(d, "commit", "-q", "-am", "rename")
        _git(d, "checkout", "-q", "main")
        _write(d, "src/big.txt", body.replace("line 100\r\n", "line hundred\r\n"))
        cls.c = _client(d)

    @classmethod
    def tearDownClass(cls):
        cls.c.state.jobs.close()
        shutil.rmtree(cls.d, ignore_errors=True)

    def ctx(self, **q):
        r = self.c.get("/api/diff/context", params=q)
        return r.status_code, r.json()

    def test_a_page_of_the_working_tree_keeps_crlf_and_says_where_the_next_page_starts(self):
        st, d = self.ctx(path="src/big.txt", start=10, count=5)
        self.assertEqual(200, st)
        self.assertEqual(["line %d\r" % i for i in range(10, 15)], d["lines"])
        self.assertEqual(15, d["next"])
        self.assertFalse(d["eof"])

    def test_the_last_page_says_eof(self):
        _st, d = self.ctx(path="src/big.txt", start=198, count=20)
        self.assertEqual(3, len(d["lines"]))
        self.assertTrue(d["eof"])
        self.assertIsNone(d["next"])

    def test_a_page_is_at_most_page_max(self):
        self.assertLessEqual(DV.PAGE_MAX, 500)
        _st, d = self.ctx(path="src/huge.txt", start=1, count=100000)
        self.assertEqual(DV.PAGE_MAX, len(d["lines"]))
        self.assertEqual(DV.PAGE_MAX + 1, d["next"])
        # the reader caps itself too: a caller that is not this route gets a page, not the file
        got = DV.context_page(self.d, "src/huge.txt", 1, 100000)
        self.assertEqual(DV.PAGE_MAX, len(got["lines"]))
        self.assertFalse(got["eof"])

    def test_a_page_at_a_revision_reads_git(self):
        _st, d = self.ctx(path="src/big.txt", start=100, count=1, rev="HEAD")
        self.assertEqual(["line 100\r"], d["lines"])
        _st, d = self.ctx(path="src/big.txt", start=100, count=1)
        self.assertEqual(["line hundred\r"], d["lines"])

    def test_binary_and_absent_say_unavailable(self):
        self.assertEqual("unavailable", self.ctx(path="img.bin", start=1)[1]["state"])
        self.assertEqual("unavailable", self.ctx(path="src/nope.txt", start=1)[1]["state"])
        self.assertEqual("unavailable", self.ctx(path="src/nope.txt", start=1, rev="HEAD")[1]["state"])

    def test_outside_the_repo_and_bad_revisions_are_refused(self):
        self.assertEqual(400, self.ctx(path="../x", start=1)[0])
        self.assertEqual(400, self.ctx(path="src/big.txt", rev="-x")[0])
        self.assertEqual(400, self.ctx(path="src/big.txt", rev="HEAD", snap="s")[0])
        self.assertEqual(400, self.ctx(path="src/big.txt", session="../../etc")[0])

    def test_a_session_with_no_worktree_says_so(self):
        _st, d = self.ctx(path="src/big.txt", start=1, session="no-such-session")
        self.assertEqual("unavailable", d["state"])

    def test_a_page_deep_in_a_big_file_reads_only_as_far_as_the_page(self):
        """Lazily: the reader stops at the page, so line 10 of a 50 MB file costs ~nothing."""
        p = os.path.join(self.d, "huge.txt")
        with open(p, "wb") as fh:
            fh.write(b"x" * 100 + b"\n")
            fh.write((b"y" * 99 + b"\n") * 500000)
        tracemalloc.start()
        try:
            got = DV.context_page(self.d, "huge.txt", 10, 5)
            _cur, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(5, len(got["lines"]))
        self.assertLess(peak, 2_000_000)

    def test_every_file_says_where_its_new_side_is(self):
        live = self.c.get("/api/live").json()
        self.assertEqual({"work": "1"}, _file(live, "src/big.txt")["ctx"])
        cmp_ = self.c.get("/api/compare?base=main&head=topic").json()
        f = _file(cmp_, "src/new_name.py")
        self.assertEqual("renamed", f["status"])
        self.assertEqual("src/old_name.py", f["old_path"])
        self.assertEqual({"rev": "topic"}, f["ctx"])
        self.assertEqual("main", cmp_["base"])
        _st, d = self.ctx(path="src/new_name.py", start=1, count=2, rev="topic")
        self.assertEqual(["v1 = 1", "v2 = 2"], d["lines"])

    def test_a_snapshot_side_is_read_from_the_snapshot(self):
        from mokata.ide import snapshots as S
        sid = "sess-p5"
        before = S.capture(self.d, "src/big.txt", S.BEFORE, sid, "")
        after = S.capture(self.d, "src/big.txt", S.AFTER, sid, "")
        if before is None or after is None:
            self.skipTest("snapshots are not captured here")
        ds = D.write_diff(self.d, after.id)
        self.assertEqual({"snap": after.id}, ds.files[0].ctx)
        _st, d = self.ctx(path="src/big.txt", start=100, count=1, snap=after.id)
        self.assertEqual(["line hundred\r"], d["lines"])
        # a snapshot of big.txt is never served as the lines of another file
        _st, d = self.ctx(path="src/new_name.py", start=1, count=1, snap=after.id)
        self.assertEqual(("unavailable", []), (d["state"], d["lines"]))


if __name__ == "__main__":
    unittest.main()
