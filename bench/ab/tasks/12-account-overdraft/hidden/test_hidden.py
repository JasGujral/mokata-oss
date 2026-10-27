import unittest
from bank import Account


class Hidden(unittest.TestCase):
    def test_overdraft_refused(self):
        a = Account(10)
        with self.assertRaises(ValueError):
            a.withdraw(11)
        self.assertEqual(10, a.balance)
    def test_negative_amounts(self):
        a = Account(10)
        for op in (a.withdraw, a.deposit):
            with self.assertRaises(ValueError):
                op(-1)
    def test_normal(self):
        a = Account(10)
        a.withdraw(4); a.deposit(1)
        self.assertEqual(7, a.balance)
