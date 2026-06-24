"""A3 — tool-presence detection."""

import os
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.detect import Detector


class TestDetector(unittest.TestCase):
    def test_always_is_present(self):
        d = Detector()
        self.assertTrue(d.is_present("x", {"detect": {"type": "always"}}))

    def test_python_module_present_and_absent(self):
        d = Detector()
        self.assertTrue(
            d.is_present("sqlite", {"detect": {"type": "python_module",
                                               "name": "sqlite3"}})
        )
        self.assertFalse(
            d.is_present("nope", {"detect": {"type": "python_module",
                                             "name": "no_such_module_xyz_123"}})
        )

    def test_command_present_and_absent(self):
        d = Detector()
        # `sh` exists on any POSIX system the tests run on.
        self.assertTrue(
            d.is_present("sh", {"detect": {"type": "command", "name": "sh"}})
        )
        self.assertFalse(
            d.is_present(
                "ghost", {"detect": {"type": "command",
                                     "name": "definitely-not-real-cmd-xyz"}}
            )
        )

    def test_path_present_and_absent(self):
        # Distinct tool_ids: the Detector caches by id (one id -> one tool), so reusing
        # an id across two different defs would (correctly) return the cached result.
        d = Detector()
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(
                d.is_present("p_present", {"detect": {"type": "path", "name": tmp}})
            )
        self.assertFalse(
            d.is_present(
                "p_absent", {"detect": {"type": "path", "name": "/no/such/path/xyz"}}
            )
        )

    def test_overrides_win_over_real_detection(self):
        d = Detector(overrides={"sh": False, "ghost": True})
        self.assertFalse(
            d.is_present("sh", {"detect": {"type": "command", "name": "sh"}})
        )
        self.assertTrue(
            d.is_present("ghost", {"detect": {"type": "command", "name": "nope-xyz"}})
        )

    def test_unknown_strategy_is_absent_not_error(self):
        d = Detector()
        self.assertFalse(d.is_present("x", {"detect": {"type": "tea-leaves"}}))

    def test_detect_all_returns_full_map(self):
        d = Detector()
        tools = {
            "a": {"detect": {"type": "always"}},
            "b": {"detect": {"type": "command", "name": "nope-xyz"}},
        }
        result = d.detect_all(tools)
        self.assertEqual(result, {"a": True, "b": False})

    def test_caching_returns_stable_result(self):
        d = Detector(cache=True)
        td = {"detect": {"type": "always"}}
        first = d.is_present("k", td)
        second = d.is_present("k", td)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
