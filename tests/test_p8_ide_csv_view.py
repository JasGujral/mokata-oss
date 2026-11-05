"""0.0.27 P8 — ID-CSV-VIEW: delimited text opens as a table; a changed one gets a cell diff.

Business rules:
  * A CSV with quoted newlines, CRLF, a BOM, doubled quotes reads as the right rows and columns.
  * A ragged row is shown and flagged; non-UTF-8 bytes are replaced and flagged.
  * A file is paged by byte offset: a deep page is the right rows, found without reading it whole.
  * Other delimiters are sniffed; the extension's wins when the file agrees.
  * Cells are text; formula cells are shown literally and copy/export neutralises them.
  * Table diff: added/removed rows and changed cells, by key or by position.
  * Every route answers its contract shape.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest

import _support  # noqa: F401

from mokata.ide import contract as C
from mokata.ide import tableview as T

TOKEN, PORT = "tok", 9999
BASE = f"http://127.0.0.1:{PORT}"


def _w(d, name, data):
    p = os.path.join(d, name)
    with open(p, "wb") as fh:
        fh.write(data if isinstance(data, bytes) else data.encode())
    return p


class Fresh(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)


class Reading(Fresh):
    def test_quotes_newlines_crlf_and_bom_read_as_the_right_cells(self):
        p = _w(self.d, "a.csv", b'\xef\xbb\xbfid,note\r\n1,"a ""q"" b"\r\n2,"l1\r\nl2"\r\n')
        t = T.open_table(p)
        self.assertEqual(["id", "note"], t.info()["header"])
        pg = t.page(0, 10)
        self.assertEqual([["1", 'a "q" b'], ["2", "l1\r\nl2"]], pg["rows"])
        self.assertEqual(2, t.count()["rows"])

    def test_ragged_and_non_utf8_rows_are_shown_and_flagged(self):
        p = _w(self.d, "a.csv", b"a,b\n1\n2,caf\xe9\n")
        pg = T.open_table(p).page(0, 10)
        self.assertEqual({"0": ["ragged"], "1": ["bad"]}, pg["flags"])
        self.assertIn("�", pg["rows"][1][1])

    def test_a_deep_page_is_the_right_rows_after_the_count(self):
        n = 30000
        p = _w(self.d, "big.csv", "id,v\n" + "".join(f"{i},x{i}\n" for i in range(n)))
        t = T.open_table(p)
        self.assertEqual(n, t.count()["rows"])
        self.assertEqual("x25000", t.page(25000, 3)["rows"][0][1])
        self.assertEqual(3, len(t.page(n - 3, 50)["rows"]))

    def test_a_page_beyond_the_index_says_not_indexed_instead_of_scanning(self):
        n = 400000
        p = _w(self.d, "big.csv", "id\n" + "".join(f"{i}\n" for i in range(n)))
        pg = T.open_table(p).page(390000, 5)
        self.assertFalse(pg["indexed"])

    def test_delimiters_are_sniffed(self):
        self.assertEqual(";", T.open_table(_w(self.d, "s.csv", "a;b;c\n1;2;3\n")).delim)
        self.assertEqual("\t", T.open_table(_w(self.d, "t.txt", "a\tb\n1\t2\n")).delim)
        self.assertEqual(",", T.open_table(_w(self.d, "c.csv", "a,b\n1,2\n")).delim)

    def test_binary_is_refused(self):
        with self.assertRaises(T.NotATable):
            T.open_table(_w(self.d, "b.csv", b"a\0b\n"))


class Encodings(Fresh):
    """The file may come from an old Mac (bare CR) or from Excel's "Unicode text" (UTF-16 + BOM)."""

    def test_bare_cr_line_ends_are_records_not_one_giant_row(self):
        p = _w(self.d, "mac.csv", b'id,note\r1,a\r2,"x\ry"\r3,c\r')
        t = T.open_table(p)
        self.assertEqual([["1", "a"], ["2", "x\ry"], ["3", "c"]], t.page(0, 10)["rows"])
        self.assertEqual(3, t.count()["rows"])

    def test_a_crlf_split_across_the_read_chunk_is_still_one_line_end(self):
        body = "id,v\r\n" + "".join(f"{i},{'x' * 40}\r\n" for i in range(60000))
        t = T.open_table(_w(self.d, "big.csv", body))
        self.assertEqual(60000, t.count()["rows"])
        self.assertEqual(0, t.summary()["ragged"])

    def test_utf16_with_a_bom_is_decoded_in_either_byte_order(self):
        text = 'id,name\r\n1,"Zoë ""Z"""\r\n2,"l1\r\nl2"\r\n3,日本語\r\n'
        for enc, bom in (("utf-16-le", b"\xff\xfe"), ("utf-16-be", b"\xfe\xff")):
            t = T.open_table(_w(self.d, enc + ".csv", bom + text.encode(enc)))
            self.assertEqual(["id", "name"], t.info()["header"], enc)
            self.assertEqual(enc, t.info()["encoding"])
            self.assertEqual([["1", 'Zoë "Z"'], ["2", "l1\r\nl2"], ["3", "日本語"]],
                             t.page(0, 10)["rows"], enc)
            self.assertEqual(3, t.count()["rows"], enc)
            self.assertEqual(0, t.summary()["bad_utf8"], enc)

    def test_a_deep_page_of_a_utf16_file_is_the_right_rows(self):
        body = "id,v\n" + "".join(f"{i},ĀāŁ{i}\n" for i in range(5000))
        t = T.open_table(_w(self.d, "u.tsv", b"\xff\xfe" + body.encode("utf-16-le")))
        self.assertEqual(5000, t.count()["rows"])
        self.assertEqual([["4321", "ĀāŁ4321"]], t.page(4321, 1)["rows"])

    def test_a_utf16_character_whose_bytes_look_like_a_newline_is_not_a_line_end(self):
        # U+0A41 is bytes 41 0A in little-endian; followed by U+0100 (00 01) the pair "0A 00" sits
        # across a character boundary and must not split the record.
        t = T.open_table(_w(self.d, "m.csv", b"\xff\xfe" + "a,b\n\u0a41\u0100,2\n".encode("utf-16-le")))
        self.assertEqual([["\u0a41\u0100", "2"]], t.page(0, 5)["rows"])

    def test_utf32_and_bomless_utf16_are_refused_in_plain_words_never_shown_as_garbage(self):
        with self.assertRaises(T.NotATable) as a:
            T.open_table(_w(self.d, "w.csv", b"\xff\xfe\x00\x00" + "a,b\n".encode("utf-32-le")))
        self.assertEqual("unsupported", a.exception.state)
        self.assertIn("UTF-32", a.exception.note)
        with self.assertRaises(T.NotATable) as b:
            T.open_table(_w(self.d, "n.csv", "a,b\n1,2\n".encode("utf-16-le")))
        self.assertEqual("binary", b.exception.state)
        self.assertIn("UTF-8", b.exception.note)


class Safety(unittest.TestCase):
    def test_formula_cells_are_neutralised_on_export_and_numbers_are_not(self):
        out = T.to_text([["=HYPERLINK(\"x\")", "+1+1", "@s", "-5", "ok", "\tx"]], "csv")
        self.assertTrue(out.startswith("\"'=HYPERLINK"))
        self.assertIn("'+1+1", out)
        self.assertIn("'@s", out)
        self.assertIn(",-5,", out)
        self.assertIn("'\tx", out)

    def test_cells_come_back_as_text_in_json(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        p = _w(d, "x.csv", "a\n<script>alert(1)</script>\n=1+1\n")
        self.assertEqual("<script>alert(1)</script>", T.open_table(p).page(0, 5)["rows"][0][0])


class Diff(Fresh):
    def _pair(self):
        a = _w(self.d, "o.csv", "id,n\n1,a\n2,b\n3,c\n")
        b = _w(self.d, "n.csv", "id,n\n1,a\n2,B\n4,d\n")
        return T.Table(a), T.Table(b)

    def test_by_key_finds_added_removed_and_changed_cells(self):
        o, n = self._pair()
        s = T.diff_tables(o, n, "id", "k1")
        self.assertEqual((1, 1, 1, 1), (s["added"], s["removed"], s["changed"], s["same"]))
        chg = [c for c in T.get_diff("k1").page(0, 10) if c["op"] == "chg"][0]
        self.assertEqual([1], chg["changed"])
        self.assertEqual(["2", "b"], chg["old"])

    def test_by_position_compares_row_to_row(self):
        o, n = self._pair()
        s = T.diff_tables(o, n, "", "p1")
        self.assertEqual(2, s["changed"])

    def test_a_repeated_key_pairs_in_order_and_is_counted(self):
        o = T.Table(_w(self.d, "o.csv", "k,v\na,1\na,2\nb,3\n"))
        n = T.Table(_w(self.d, "n.csv", "k,v\na,1\na,9\na,4\n"))
        s = T.diff_tables(o, n, "k", "dup1")
        self.assertEqual(1, s["duplicate_keys"])
        self.assertEqual((1, 1, 1, 1), (s["added"], s["removed"], s["changed"], s["same"]))
        ops = [(c["op"], c["old_row"], c["new_row"]) for c in T.get_diff("dup1").page(0, 10)]
        self.assertEqual([("chg", 1, 1), ("add", None, 2), ("del", 2, None)], ops)

    def test_an_added_column_does_not_make_every_row_changed(self):
        o = T.Table(_w(self.d, "o.csv", "id\n1\n2\n"))
        n = T.Table(_w(self.d, "n.csv", "id,x\n1,\n2,\n"))
        self.assertEqual(0, T.diff_tables(o, n, "id", "c1")["changed"])


class Progress(Fresh):
    def test_a_diff_reports_progress_and_cancel_stops_it_leaving_no_spool(self):
        from mokata.knowledge import cancel as K
        import threading
        n = 30000
        o = T.Table(_w(self.d, "o.csv", "id,v\n" + "".join(f"{i},a\n" for i in range(n))))
        nw = T.Table(_w(self.d, "n.csv", "id,v\n" + "".join(f"{i},b\n" for i in range(n))))
        seen = []
        with K.reporting(lambda **kw: seen.append(kw)):
            T.diff_tables(o, nw, "id", "pg1")
        self.assertTrue(seen and all(k in seen[0] for k in ("done", "total", "unit", "note")))
        flag = threading.Event()
        flag.set()
        with K.scope(flag), self.assertRaises(K.Cancelled):
            T.diff_tables(o, nw, "id", "pg2")
        self.assertIsNone(T.get_diff("pg2"), "a cancelled diff is not kept")
        self.assertNotIn("pg2", T._PENDING, "and leaves no half-built spool behind")

    def test_a_keyed_diff_says_what_memory_it_held(self):
        o = T.Table(_w(self.d, "o.csv", "id\n1\n2\n"))
        n = T.Table(_w(self.d, "n.csv", "id\n1\n3\n"))
        s = T.diff_tables(o, n, "id", "mem1")
        self.assertIn("holds the old side's 2 keys", s["memory"])
        self.assertGreater(s["memory_bytes"], 0)
        self.assertEqual((1, 1), (s["added"], s["removed"]))


class Routes(Fresh):
    def _client(self):
        from starlette.testclient import TestClient
        from mokata.ide.server import AppState, build_app
        c = TestClient(build_app(AppState(self.d, TOKEN, PORT, None, False)), base_url=BASE,
                       follow_redirects=False)
        c.get(f"/pair?t={TOKEN}")
        return c

    def test_routes_answer_their_contract_shapes(self):
        _w(self.d, "d.csv", "a,b\n1,2\n3,4\n")
        c = self._client()
        con = C.load()
        by = {(r["path"], r["method"]): r for r in con["routes"]}
        for path in ("/api/table/info", "/api/table/rows", "/api/table/count"):
            r = c.get(path, params={"path": "d.csv"})
            self.assertEqual(200, r.status_code, path)
            schema = by[(path, "GET")]["responses"]["200"]["schema"]
            self.assertEqual([], C.validate(r.json(), schema, con["schemas"]), path)
        self.assertEqual(400, c.get("/api/table/rows", params={"path": "../x.csv"}).status_code)
        r = c.post("/api/table/export", json={"rows": [["=1", "b"]], "format": "csv"},
                   headers={"Origin": BASE})
        self.assertEqual("'=1,b\n", r.json()["text"])

    def _repo(self):
        import subprocess
        def git(*a):
            subprocess.run(["git", *a], cwd=self.d, check=True, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        git("init", "-q", "-b", "main")
        git("config", "user.email", "t@example.invalid")
        git("config", "user.name", "t")
        git("config", "commit.gpgsign", "false")
        _w(self.d, "d.csv", "id,n\n1,a\n2,b\n3,c\n")
        git("add", "-A")
        git("commit", "-q", "-m", "one")
        _w(self.d, "d.csv", "id,n\n1,a\n2,=B\n4,d\n")

    def test_the_diff_routes_answer_their_contract_shapes_end_to_end(self):
        self._repo()
        c = self._client()
        con = C.load()
        by = {(r["path"], r["method"]): r for r in con["routes"]}
        r = c.get("/api/table/diff", params={"path": "d.csv", "key": "id", "sync_ms": "2000"})
        self.assertEqual(200, r.status_code, r.text)
        d = r.json()
        schema = by[("/api/table/diff", "GET")]["responses"]["200"]["schema"]
        self.assertEqual([], C.validate(d, schema, con["schemas"]))
        self.assertEqual((1, 1, 1, 1), tuple(d["summary"][k] for k in ("added", "removed", "changed", "same")))
        self.assertIn("old side's 3 keys", d["summary"]["memory"])
        r2 = c.get("/api/table/diff/rows", params={"id": d["id"], "from": "1", "count": "5"})
        self.assertEqual(2, len(r2.json()["changes"]))
        self.assertEqual([], C.validate(r2.json(), by[("/api/table/diff/rows", "GET")]["responses"]["200"]["schema"], con["schemas"]))
        bad = c.get("/api/table/diff", params={"path": "d.csv", "key": "nope", "sync_ms": "2000"}).json()
        self.assertEqual("bad-key", bad["state"])

    def test_a_utf16_file_answers_info_and_a_utf32_file_answers_a_plain_note(self):
        _w(self.d, "u.csv", b"\xff\xfe" + "a,b\r\n1,2\r\n".encode("utf-16-le"))
        _w(self.d, "w.csv", b"\xff\xfe\x00\x00" + "a,b\n".encode("utf-32-le"))
        c = self._client()
        self.assertEqual(["a", "b"], c.get("/api/table/info", params={"path": "u.csv"}).json()["header"])
        w = c.get("/api/table/info", params={"path": "w.csv"}).json()
        self.assertEqual("unsupported", w["state"])
        self.assertIn("UTF-32", w["note"])

    def test_every_table_route_is_in_the_contract(self):
        paths = {r["path"] for r in C.load()["routes"]}
        for p in ("/api/table/info", "/api/table/rows", "/api/table/count", "/api/table/diff",
                  "/api/table/diff/rows", "/api/table/export"):
            self.assertIn(p, paths)


if __name__ == "__main__":
    unittest.main()
