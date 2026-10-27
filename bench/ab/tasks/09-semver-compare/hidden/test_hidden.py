import unittest
from semver import compare


class Hidden(unittest.TestCase):
    def test_core(self):
        self.assertEqual(-1, compare("1.2.3", "1.10.0"))
    def test_equal(self):
        self.assertEqual(0, compare("2.0.0", "2.0.0"))
    def test_prerelease(self):
        self.assertEqual(-1, compare("1.0.0-rc.1", "1.0.0"))
        self.assertEqual(-1, compare("1.0.0-rc.2", "1.0.0-rc.10"))
        self.assertEqual(1, compare("1.0.0-beta", "1.0.0-alpha"))
