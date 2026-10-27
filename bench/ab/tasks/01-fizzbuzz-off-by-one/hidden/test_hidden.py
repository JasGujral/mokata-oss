import unittest
from fizz import fizzbuzz


class Hidden(unittest.TestCase):
    def test_length(self):
        self.assertEqual(15, len(fizzbuzz(15)))
    def test_last(self):
        self.assertEqual("FizzBuzz", fizzbuzz(15)[-1])
    def test_zero(self):
        self.assertEqual([], fizzbuzz(0))
    def test_one(self):
        self.assertEqual(["1"], fizzbuzz(1))
