import unittest
from lru import LRUCache


class Hidden(unittest.TestCase):
    def test_evicts_lru(self):
        c = LRUCache(2)
        c.put(1, 1); c.put(2, 2); c.get(1); c.put(3, 3)
        self.assertEqual(-1, c.get(2))
        self.assertEqual(1, c.get(1))
    def test_update(self):
        c = LRUCache(1)
        c.put("a", 1); c.put("a", 2)
        self.assertEqual(2, c.get("a"))
    def test_missing(self):
        self.assertEqual(-1, LRUCache(1).get("x"))
