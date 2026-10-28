import unittest
from retry import retry


class Hidden(unittest.TestCase):
    def test_succeeds_after_failures(self):
        calls = []
        @retry(3)
        def f():
            calls.append(1)
            if len(calls) < 3:
                raise OSError("x")
            return "ok"
        self.assertEqual("ok", f())
        self.assertEqual(3, len(calls))
    def test_gives_up(self):
        @retry(2, exceptions=(OSError,))
        def f():
            raise OSError("boom")
        with self.assertRaises(OSError):
            f()
    def test_other_exception_not_retried(self):
        calls = []
        @retry(5, exceptions=(OSError,))
        def f():
            calls.append(1)
            raise KeyError("k")
        with self.assertRaises(KeyError):
            f()
        self.assertEqual(1, len(calls))
