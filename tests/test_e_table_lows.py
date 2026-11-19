"""0.0.29 E `IDE-CSV-DIFF-LIMITS` (partial) — the CSV view's F8, F9, F10, F10b.

  * F8  — a count over a file that changed while it was counted never says complete;
  * F9  — a UTF-16 LE file (byte-order mark) whose first character is U+0000 opens as UTF-16,
          not refused as UTF-32 (its first four bytes ARE the UTF-32 LE mark); real UTF-32 is
          still refused in plain words;
  * F10 — formula look-alikes (leading spaces, full-width ＝＋－＠) are guarded on export; a plain
          number with spaces is not;
  * F10b — NOT done: the 413 needs a probe the contract test cannot send (32 MiB body);
          filed as TABLE-EXPORT-413-UNPROBEABLE (doc 84).
The 64 MiB record bound, the linear 1M-row reversed diff, casefold collation and no regex filter
stay disclosed limits (ruling D7).
"""
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.ide import tableview as T
from mokata.knowledge import cancel


def _file(data: bytes, name="t.csv"):
    p = os.path.join(tempfile.mkdtemp(prefix="e-tbl-"), name)
    with open(p, "wb") as fh:
        fh.write(data)
    return p


class F8ACountOfAChangingFile(unittest.TestCase):

    def test_an_append_during_the_count_is_never_complete(self):
        p = _file(b"a,b\n" + b"1,2\n" * (T.STRIDE * 40))
        t = T.Table(p)
        appended = []

        def rep(**_kw):
            if not appended:
                appended.append(1)
                with open(p, "ab") as fh:
                    fh.write(b"3,4\n" * 10)
        with cancel.reporting(rep):
            out = t.count()
        self.assertFalse(out["complete"])
        self.assertIsNone(out["rows"])
        self.assertIn("changed while it was counted", out["changed"])

    def test_an_unchanged_file_is_counted_complete(self):
        p = _file(b"a,b\n" + b"1,2\n" * 1000)
        out = T.Table(p).count()
        self.assertTrue(out["complete"])
        self.assertEqual(out["rows"], 1000)
        self.assertNotIn("changed", out)


class F9Utf16StartingWithNul(unittest.TestCase):

    def test_utf16_le_whose_first_character_is_u0000_opens(self):
        text = "\x00a,b\n1,2\n3,4\n"
        t = T.Table(_file(b"\xff\xfe" + text.encode("utf-16-le")))
        self.assertEqual(t.enc, "utf-16-le")
        self.assertEqual(t.page(0, 10)["rows"][-1], ["3", "4"])

    def test_real_utf32_is_still_refused_by_name(self):
        for bom, enc in ((b"\xff\xfe\x00\x00", "utf-32-le"), (b"\x00\x00\xfe\xff", "utf-32-be")):
            with self.assertRaises(T.NotATable) as cm:
                T.Table(_file(bom + "a,b\n1,2\n".encode(enc)))
            self.assertIn("UTF-32", cm.exception.note)


class F10FormulaLookAlikes(unittest.TestCase):

    def test_look_alikes_are_guarded(self):
        for cell in (" =1+1", "  +cmd", "　@SUM(A1)", "＝1+1", "＋1", "－x",
                     "＠x", "=1", "\tx", "\rx"):
            self.assertEqual(T.guard_cell(cell), "'" + cell, repr(cell))

    def test_numbers_and_plain_text_are_not(self):
        for cell in ("-12.5", " -3", "+4e2", "abc", "", " text", "a=b"):
            self.assertEqual(T.guard_cell(cell), cell, repr(cell))


if __name__ == "__main__":
    unittest.main()
