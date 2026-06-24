"""Generic toggle/settings store — designed so a future execution-mode setting
(E8, Stage 8) can be stored and read the same way. E8 itself is NOT built here."""

import unittest

from _support import sample_manifest_data

from mokata.manifest import Manifest
from mokata.profiles import build_manifest_data


class TestSettingsStore(unittest.TestCase):
    def test_generated_manifest_has_a_settings_block(self):
        m = Manifest.from_dict(build_manifest_data("standard", "0.1.0"))
        self.assertIsInstance(m.settings, dict)

    def test_setting_returns_default_when_absent(self):
        m = Manifest.from_dict(sample_manifest_data())
        self.assertIsNone(m.setting("execution_mode"))
        self.assertEqual(m.setting("execution_mode", "sequential"), "sequential")

    def test_setting_reads_a_stored_value(self):
        # Shape a *future* E8 setting would use; proving the store is generic, not
        # that E8 exists.
        data = sample_manifest_data()
        data["settings"] = {"execution_mode": "parallel", "fan_out": 4}
        m = Manifest.from_dict(data)
        self.assertEqual(m.setting("execution_mode"), "parallel")
        self.assertEqual(m.setting("fan_out"), 4)

    def test_settings_survive_validation(self):
        from mokata import schema

        data = sample_manifest_data()
        data["settings"] = {"anything": {"nested": True}, "count": 3}
        self.assertEqual(schema.validate_manifest(data), [])


if __name__ == "__main__":
    unittest.main()
