"""0.0.27 P8 round 2 — the verifier's constructions (P8.verify.md F1-F7) and the edge cell
("sortable and filterable CSV"): each rule is stated in the test's docstring.

  F1 one long record costs time/memory LINEAR in its size, and past a stated bound the file is
     REFUSED in plain words — never a 12 GB request nobody can cancel.
  F2 a keyed diff of a file whose rows ALL changed streams both sides: no random read per row.
  F3 the memory figure a keyed diff states is true when keys repeat, and repeated keys are linear.
  F4 a table route never re-opens a repo file by a bare path: every open is the contained open.
  F6 a cell holding the old separator byte cannot make a changed row read "same".
  F7 on Cancel, the side files are closed BEFORE their directory is removed (Windows).
  Edge: a column filter and a sort run server-side over the WHOLE file as jobs; nothing partial.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
import tracemalloc
import unittest

import _support  # noqa: F401

from mokata.ide import tableview as T
from mokata.knowledge import cancel as K


def _w(d, name, data):
    p = os.path.join(d, name)
    with open(p, "wb") as fh:
        fh.write(data if isinstance(data, bytes) else data.encode())
    return p


class Fresh(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)


class F1LongRecord(Fresh):
    def test_a_long_line_is_split_in_linear_memory(self):
        """A 24 MB line must not cost 5x its size (the verifier saw 47x at 100 MB)."""
        p = _w(self.d, "one.csv", b"x" * (24 << 20))
        tracemalloc.start()
        try:
            with open(p, "rb") as fh:
                n = sum(len(x) for x in T._raw_lines(fh, "utf-8"))
            _cur, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(24 << 20, n)
        self.assertLess(peak, 3 * (24 << 20))

    def test_a_record_past_the_bound_is_refused_in_plain_words_not_read(self):
        """info() is not a job: it must answer fast, and say why, for a record over the bound."""
        old = T.RECORD_MAX
        T.RECORD_MAX = 1 << 20
        self.addCleanup(setattr, T, "RECORD_MAX", old)
        p = _w(self.d, "one.csv", b"id,v\n1," + b"y" * (5 << 20) + b"\n")
        t0 = time.time()
        with self.assertRaises(T.NotATable) as cm:
            T.Table(p).head()
        self.assertLess(time.time() - t0, 2.0)
        self.assertEqual("unsupported", cm.exception.state)
        self.assertIn("record", cm.exception.note)
        self.assertIn("MiB", cm.exception.note)

    def test_an_inline_read_is_bounded_by_bytes_not_only_by_records(self):
        """The header read answers from the first bytes; it does not walk 256 huge records."""
        rec = ("%d," + "z" * (3 << 20) + "\n")
        p = _w(self.d, "wide.csv", "id,v\n" + "".join(rec % i for i in range(40)))
        old = T.INLINE_BYTES
        T.INLINE_BYTES = 4 << 20
        self.addCleanup(setattr, T, "INLINE_BYTES", old)
        t = T.Table(p)
        t.head()
        self.assertLess(t.scanned, 8, "head() walked far more than its byte budget")
        self.assertEqual(["id", "v"], t.header)

    def test_a_long_record_counts_cancel(self):
        """Counting a file of long records checkpoints inside the splitter: Cancel stops it."""
        p = _w(self.d, "one.csv", b"a,b\n1," + b"y" * (6 << 20) + b"\n")
        flag = threading.Event()
        flag.set()
        with K.scope(flag), self.assertRaises(K.Cancelled):
            T.Table(p).count()


def _pair(d, old_rows, new_rows, header="id,v\n"):
    a = _w(d, "o.csv", header + "".join(old_rows))
    b = _w(d, "n.csv", header + "".join(new_rows))
    return T.Table(a), T.Table(b)


class F2Streaming(Fresh):
    def test_an_all_rows_changed_keyed_diff_makes_no_random_read_per_row(self):
        """Both sides stream once; row_at (a random read) is not called for an ordered file."""
        n = 30000
        o, nw = _pair(self.d, [f"{i},a\n" for i in range(n)], [f"{i},b\n" for i in range(n)])
        calls = []
        orig = T.Table.row_at
        T.Table.row_at = lambda self, off: calls.append(off) or orig(self, off)
        self.addCleanup(setattr, T.Table, "row_at", orig)
        s = T.diff_tables(o, nw, "id", "f2a")
        self.assertEqual(n, s["changed"])
        self.assertEqual([], calls)
        c = T.get_diff("f2a").page(n - 1, 1)[0]
        self.assertEqual((["29999", "a"], ["29999", "b"]), (c["old"], c["new"]))

    def test_removed_rows_stream_too_and_a_shuffled_file_is_still_right(self):
        n = 3000
        old = [f"{i},a\n" for i in range(n)]
        new = [f"{i},a\n" for i in reversed(range(0, n, 2))]          # half removed, order reversed
        o, nw = _pair(self.d, old, new)
        s = T.diff_tables(o, nw, "id", "f2b")
        self.assertEqual((n // 2, n // 2, 0), (s["removed"], s["same"], s["changed"]))
        dels = [c for c in T.get_diff("f2b").page(0, 5000) if c["op"] == "del"]
        self.assertEqual(["1", "a"], dels[0]["old"])
        self.assertEqual([int(c["old"][0]) for c in dels], sorted(int(c["old"][0]) for c in dels))


class F3Memory(Fresh):
    def test_memory_bytes_counts_the_repeated_keys_it_holds(self):
        n = 20000
        o, nw = _pair(self.d, [f",{i}\n" for i in range(n)], [f",{i}\n" for i in range(n)])
        s = T.diff_tables(o, nw, "id", "f3a")
        self.assertGreater(s["memory_bytes"], n * 40, "20,000 held rows reported as a few KB")
        self.assertEqual(n, s["same"])

    def test_repeated_keys_pair_in_linear_time(self):
        n = 300000
        o, nw = _pair(self.d, [f",{i % 7}\n" for i in range(n)], [f",{i % 7}\n" for i in range(n)])
        t0 = time.time()
        s = T.diff_tables(o, nw, "id", "f3b")
        self.assertEqual(n, s["same"])
        self.assertLess(time.time() - t0, 8.0, "pop(0) on a long list is quadratic")


class F6Digest(Fresh):
    def test_a_separator_byte_inside_a_cell_cannot_hide_a_change(self):
        o, nw = _pair(self.d, ["1,x\x1f|y\n"], ["1,x|\x1fy\n"], "id,a\n")
        o = T.Table(_w(self.d, "o2.csv", "id,a,b\n1,x\x1f,y\n"))
        nw = T.Table(_w(self.d, "n2.csv", "id,a,b\n1,x,\x1fy\n"))
        self.assertEqual(1, T.diff_tables(o, nw, "id", "f6")["changed"])


class F7Close(Fresh):
    def test_side_files_are_closed_before_their_directory_goes(self):
        import subprocess
        repo = self.d
        def git(*a):
            subprocess.run(["git", *a], cwd=repo, check=True, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        git("init", "-q", "-b", "main")
        git("config", "user.email", "t@example.invalid"); git("config", "user.name", "t")
        git("config", "commit.gpgsign", "false")
        n = 60000
        _w(repo, "d.csv", "id,v\n" + "".join(f"{i},a\n" for i in range(n)))
        git("add", "-A"); git("commit", "-q", "-m", "x")
        _w(repo, "d.csv", "id,v\n" + "".join(f"{i},b\n" for i in range(n)))
        opened = []
        orig_open = T._open_plain

        def tracking(path):
            fh = orig_open(path)
            opened.append(fh)
            return fh
        T._open_plain = tracking
        self.addCleanup(setattr, T, "_open_plain", orig_open)
        still_open = []
        real_td = tempfile.TemporaryDirectory

        class TD(real_td):
            def __exit__(self, *a):
                still_open.extend(f for f in opened if not f.closed)
                return super().__exit__(*a)
        old_td = T.tempfile.TemporaryDirectory
        T.tempfile.TemporaryDirectory = TD
        self.addCleanup(setattr, T.tempfile, "TemporaryDirectory", old_td)
        flag = threading.Event()
        threading.Timer(0.05, flag.set).start()
        with K.scope(flag), self.assertRaises(K.Cancelled):
            T.diff_files(repo, "d.csv", "d.csv", "HEAD", "HEAD", "id", "f7")
        self.assertTrue(opened, "the diff read nothing?")
        self.assertEqual([], still_open)


class F4Contained(Fresh):
    def _client(self):
        from starlette.testclient import TestClient
        from mokata.ide.server import AppState, build_app
        c = TestClient(build_app(AppState(self.d, "tok", 9999, None, False)),
                       base_url="http://127.0.0.1:9999", follow_redirects=False)
        c.get("/pair?t=tok")
        return c

    def test_a_route_read_never_opens_a_repo_file_by_a_bare_path(self):
        _w(self.d, "d.csv", "a,b\n1,2\n")
        c = self._client()
        plain = []
        orig = T._open_plain
        T._open_plain = lambda p: plain.append(p) or orig(p)
        self.addCleanup(setattr, T, "_open_plain", orig)
        for route in ("info", "rows", "count"):
            self.assertEqual(200, c.get(f"/api/table/{route}", params={"path": "d.csv"}).status_code)
        self.assertEqual([], plain, "a bare-path open skips the containment check")

    def test_a_file_swapped_for_a_symlink_out_of_the_repo_is_not_read(self):
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside, True)
        secret = _w(outside, "secret.csv", "SSN\n123-45-6789\n")
        _w(self.d, "d.csv", "a,b\n1,2\n")
        c = self._client()
        self.assertEqual(["a", "b"], c.get("/api/table/info", params={"path": "d.csv"}).json()["header"])
        os.remove(os.path.join(self.d, "d.csv"))
        try:
            os.symlink(secret, os.path.join(self.d, "d.csv"))
        except (OSError, NotImplementedError):
            self.skipTest("no symlinks here")
        for route in ("info", "rows"):
            r = c.get(f"/api/table/{route}", params={"path": "d.csv"}).json()
            self.assertNotIn("123-45-6789", str(r))
            self.assertNotEqual("ok", r.get("state"))


def _view(path, **kw):
    t = T.Table(path)
    return t, T.make_view(t, "v" + os.urandom(3).hex(), **kw)


class EdgeFilterSort(Fresh):
    def test_a_filter_scans_the_whole_file_and_says_how_many_matched(self):
        n = 50000
        p = _w(self.d, "f.csv", "id,name\n" + "".join(f"{i},{'hit' if i % 1000 == 7 else 'no'}{i}\n"
                                                      for i in range(n)))
        t, s = _view(p, fcol=1, fop="starts", fval="hit")
        self.assertEqual((50, n, True), (s["matched"], s["scanned"], s["complete"]))
        v = T.get_diff(s["id"])
        self.assertEqual(["7", "hit7"], v.page(0, 1)[0]["c"])
        self.assertEqual(49007, v.page(49, 1)[0]["n"])

    def test_filter_ops_numeric_empty_and_case(self):
        p = _w(self.d, "f.csv", "k,v\nA,1\nb,22\nc,\nd,x\ne,-3\n")
        def ids(**kw):
            _t, s = _view(p, **kw)
            return [c["c"][0] for c in T.get_diff(s["id"]).page(0, 99)]
        self.assertEqual(["A"], ids(fcol=0, fop="equals", fval="a"))
        self.assertEqual(["A"], ids(fcol=0, fop="starts", fval="a"))
        self.assertEqual(["b"], ids(fcol=0, fop="contains", fval="B"))
        self.assertEqual(["b"], ids(fcol=1, fop="gt", fval="10"))
        self.assertEqual(["e"], ids(fcol=1, fop="lt", fval="0"))
        self.assertEqual(["c"], ids(fcol=1, fop="empty", fval=""))
        self.assertEqual(["A", "b", "d", "e"], ids(fcol=1, fop="notempty", fval=""))
        self.assertEqual(["d"], ids(fcol=-1, fop="contains", fval="X"))

    def test_the_view_says_how_many_rows_had_no_number_for_a_numeric_filter(self):
        p = _w(self.d, "f.csv", "k,v\nA,1\nb,x\nc,\n")
        _t, s = _view(p, fcol=1, fop="gt", fval="0")
        self.assertEqual(1, s["not_numeric"])

    def test_sort_is_external_and_global_not_a_sort_of_the_loaded_rows(self):
        old = T.RUN_BYTES
        T.RUN_BYTES = 20000                                   # many small runs, then a merge
        self.addCleanup(setattr, T, "RUN_BYTES", old)
        import random
        rnd = random.Random(5)
        vals = [rnd.randrange(100000) for _ in range(30000)]
        p = _w(self.d, "s.csv", "id,v\n" + "".join(f"{i},{v}\n" for i, v in enumerate(vals)))
        _t, s = _view(p, scol=1, sdir=1)
        self.assertGreater(s["runs"], 5)
        got = [int(c["c"][1]) for c in T.get_diff(s["id"]).page(0, 2000)]
        self.assertEqual(sorted(vals)[:2000], got)
        last = T.get_diff(s["id"]).page(29999, 1)[0]
        self.assertEqual(max(vals), int(last["c"][1]))

    def test_sort_descending_keeps_empties_last_and_ties_in_file_order(self):
        p = _w(self.d, "s.csv", "id,v\n1,b\n2,\n3,B\n4,a\n5,\n6,10\n7,9\n")
        _t, s = _view(p, scol=1, sdir=-1)
        order = [c["c"][0] for c in T.get_diff(s["id"]).page(0, 99)]
        self.assertEqual(["6", "7", "1", "3", "4", "2", "5"][:2], order[:2])   # numbers first
        self.assertEqual(["2", "5"], order[-2:])
        self.assertEqual(["1", "3"], [x for x in order if x in ("1", "3")], "b and B tie: file order")
        _t, s = _view(p, scol=1, sdir=1)
        order = [c["c"][0] for c in T.get_diff(s["id"]).page(0, 99)]
        self.assertEqual(["7", "6"], order[:2])
        self.assertEqual(["2", "5"], order[-2:])

    def test_filter_and_sort_together_and_progress_and_cancel(self):
        n = 20000
        p = _w(self.d, "s.csv", "id,v\n" + "".join(f"{i},{(i * 7919) % 1000}\n" for i in range(n)))
        seen = []
        with K.reporting(lambda **kw: seen.append(kw)):
            _t, s = _view(p, scol=1, sdir=1, fcol=1, fop="lt", fval="10")
        self.assertTrue(seen)
        vs = [int(c["c"][1]) for c in T.get_diff(s["id"]).page(0, 99999)]
        self.assertEqual(sorted(vs), vs)
        self.assertTrue(all(v < 10 for v in vs))
        self.assertEqual(s["matched"], len(vs))
        flag = threading.Event()
        flag.set()
        with K.scope(flag), self.assertRaises(K.Cancelled):
            _view(p, scol=1, sdir=1)

    def test_the_routes_answer_their_contract_shapes(self):
        from mokata.ide import contract as C
        _w(self.d, "d.csv", "a,b\n3,x\n1,y\n2,z\n")
        c = F4Contained._client(self)
        con = C.load()
        by = {(r["path"], r["method"]): r for r in con["routes"]}
        r = c.get("/api/table/view", params={"path": "d.csv", "scol": "0", "sdir": "1",
                                              "sync_ms": "2000"})
        self.assertEqual(200, r.status_code, r.text)
        d = r.json()
        self.assertEqual([], C.validate(d, by[("/api/table/view", "GET")]["responses"]["200"]["schema"], con["schemas"]))
        pg = c.get("/api/table/view/rows", params={"id": d["id"], "from": "0", "count": "10"}).json()
        self.assertEqual([["1", "y"], ["2", "z"], ["3", "x"]], pg["rows"])
        self.assertEqual([1, 2, 0], pg["nums"])
        self.assertEqual([], C.validate(pg, by[("/api/table/view/rows", "GET")]["responses"]["200"]["schema"], con["schemas"]))
        self.assertEqual(400, c.get("/api/table/view", params={"path": "d.csv", "scol": "x"}).status_code)
        self.assertEqual(400, c.get("/api/table/view", params={"path": "d.csv", "fcol": "0", "fop": "zz"}).status_code)


if __name__ == "__main__":
    unittest.main()
