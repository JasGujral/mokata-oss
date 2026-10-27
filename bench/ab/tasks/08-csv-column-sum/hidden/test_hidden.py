import unittest
import os, tempfile
from csvsum import column_sum


class Hidden(unittest.TestCase):
    def _csv(self, text):
        fd, p = tempfile.mkstemp(suffix=".csv")
        with os.fdopen(fd, "w", newline="") as fh:
            fh.write(text)
        self.addCleanup(os.remove, p)
        return p
    def test_skips_bad(self):
        self.assertEqual(3.5, column_sum(self._csv("a,b\n1,x\n,y\nz,y\n2.5,y\n"), "a"))
    def test_unknown(self):
        with self.assertRaises(KeyError):
            column_sum(self._csv("a\n1\n"), "nope")
