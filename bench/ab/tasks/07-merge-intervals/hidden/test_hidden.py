import unittest
from intervals import merge


class Hidden(unittest.TestCase):
    def test_overlap(self):
        self.assertEqual([[1, 6], [8, 10]], merge([[8, 10], [1, 3], [2, 6]]))
    def test_touching(self):
        self.assertEqual([[1, 5]], merge([[1, 2], [2, 5]]))
    def test_empty(self):
        self.assertEqual([], merge([]))
