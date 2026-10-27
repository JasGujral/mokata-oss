import unittest
from duration import parse_duration


class Hidden(unittest.TestCase):
    def test_combo(self):
        self.assertEqual(5400, parse_duration("1h30m"))
    def test_all(self):
        self.assertEqual(3723, parse_duration("1h2m3s"))
    def test_seconds(self):
        self.assertEqual(45, parse_duration("45s"))
    def test_bad(self):
        for s in ("", "abc", "10", "1x"):
            with self.assertRaises(ValueError):
                parse_duration(s)
