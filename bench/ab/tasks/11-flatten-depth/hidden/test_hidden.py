import unittest
from flatten import flatten


class Hidden(unittest.TestCase):
    def test_full(self):
        self.assertEqual([1, 2, 3, 4], flatten([1, [2, [3, (4,)]]]))
    def test_depth(self):
        self.assertEqual([1, 2, [3]], flatten([1, [2, [3]]], depth=1))
    def test_strings(self):
        self.assertEqual(["ab", "c"], flatten([["ab"], "c"]))
