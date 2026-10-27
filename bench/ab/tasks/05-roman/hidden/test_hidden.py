import unittest
from roman import from_roman, to_roman


class Hidden(unittest.TestCase):
    def test_to(self):
        self.assertEqual("MCMXCIV", to_roman(1994))
    def test_from(self):
        self.assertEqual(2024, from_roman("MMXXIV"))
    def test_round_trip(self):
        for n in (1, 4, 9, 40, 3999):
            self.assertEqual(n, from_roman(to_roman(n)))
    def test_bad(self):
        for bad in (0, 4000):
            with self.assertRaises(ValueError):
                to_roman(bad)
        with self.assertRaises(ValueError):
            from_roman("IIII")
