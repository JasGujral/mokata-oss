import unittest
from words import top_k


class Hidden(unittest.TestCase):
    def test_basic(self):
        self.assertEqual([("a", 3), ("b", 2)], top_k("a b a B a c", 2))
    def test_ties(self):
        self.assertEqual([("x", 1), ("y", 1)], top_k("y x", 2))
    def test_empty(self):
        self.assertEqual([], top_k("", 3))
