import unittest

from shop.cart import Cart
from shop.report import receipt


class TestCart(unittest.TestCase):
    def test_total(self):
        c = Cart()
        c.add("apple", 2)
        c.add("melon")
        self.assertEqual(c.total(), 4.0)

    def test_receipt(self):
        c = Cart()
        c.add("pear", 2)
        self.assertIn("TOTAL: 1.50", receipt(c))


if __name__ == "__main__":
    unittest.main()
