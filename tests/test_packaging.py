"""J1 — marketplace + plugin manifests for Claude Code packaging: they exist, parse,
and validate; the validator flags missing required fields."""

import json
import os
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.packaging import (
    MARKETPLACE_PATH,
    PLUGIN_MANIFEST_PATH,
    validate_marketplace,
    validate_plugin,
)

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _load(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return json.load(fh)


class TestShippedManifests(unittest.TestCase):
    def test_plugin_manifest_exists_and_validates(self):
        path = os.path.join(ROOT, PLUGIN_MANIFEST_PATH)
        self.assertTrue(os.path.exists(path), "plugin.json missing")
        self.assertEqual(validate_plugin(_load(PLUGIN_MANIFEST_PATH)), [])

    def test_marketplace_manifest_exists_and_validates(self):
        path = os.path.join(ROOT, MARKETPLACE_PATH)
        self.assertTrue(os.path.exists(path), "marketplace.json missing")
        self.assertEqual(validate_marketplace(_load(MARKETPLACE_PATH)), [])

    def test_plugin_has_install_update_metadata(self):
        data = _load(PLUGIN_MANIFEST_PATH)
        self.assertIn("version", data)        # update metadata
        self.assertEqual(data["name"], "mokata")
        self.assertEqual(data.get("license"), "Apache-2.0")

    def test_marketplace_lists_mokata(self):
        data = _load(MARKETPLACE_PATH)
        names = [p["name"] for p in data["plugins"]]
        self.assertIn("mokata", names)


class TestValidators(unittest.TestCase):
    def test_plugin_requires_name_and_version(self):
        self.assertTrue(any("name" in e for e in validate_plugin({"version": "1"})))
        self.assertTrue(any("version" in e for e in validate_plugin({"name": "x"})))

    def test_marketplace_requires_plugins(self):
        self.assertTrue(any("plugins" in e for e in validate_marketplace({"name": "m"})))

    def test_marketplace_plugin_entry_requires_name_and_source(self):
        errs = validate_marketplace({"name": "m", "plugins": [{"description": "x"}]})
        self.assertTrue(errs)


if __name__ == "__main__":
    unittest.main()
