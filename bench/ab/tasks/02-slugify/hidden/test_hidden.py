import unittest
from text_utils import slugify


class Hidden(unittest.TestCase):
    def test_basic(self):
        self.assertEqual("hello-world", slugify("Hello, World!"))
    def test_runs(self):
        self.assertEqual("a-b", slugify("a   --  b"))
    def test_edges(self):
        self.assertEqual("x", slugify("--x--"))
    def test_empty(self):
        self.assertEqual("", slugify("!!!"))
