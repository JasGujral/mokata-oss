"""A1 — manifest schema + load/validate."""

import copy
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

from _support import sample_manifest_data

from mokata import schema
from mokata.manifest import Manifest, ManifestError


class TestSchema(unittest.TestCase):
    def test_valid_manifest_passes(self):
        self.assertTrue(schema.is_valid(sample_manifest_data()))
        self.assertEqual(schema.validate_manifest(sample_manifest_data()), [])

    def test_missing_required_top_level_key_fails(self):
        data = sample_manifest_data()
        del data["capabilities"]
        errs = schema.validate_manifest(data)
        self.assertTrue(any("capabilities" in e for e in errs))

    def test_unsupported_manifest_version_fails(self):
        data = sample_manifest_data()
        data["manifest_version"] = 99
        errs = schema.validate_manifest(data)
        self.assertTrue(any("unsupported" in e for e in errs))

    def test_bad_detect_type_fails(self):
        data = sample_manifest_data()
        data["tools"]["grep"]["detect"]["type"] = "telepathy"
        errs = schema.validate_manifest(data)
        self.assertTrue(any("detect.type" in e for e in errs))

    def test_command_detect_requires_name(self):
        data = sample_manifest_data()
        data["tools"]["graphtool"]["detect"] = {"type": "command"}
        errs = schema.validate_manifest(data)
        self.assertTrue(any("detect.name is required" in e for e in errs))

    def test_capability_referencing_unknown_tool_fails(self):
        data = sample_manifest_data()
        data["capabilities"]["code_graph"]["fallback"].append("ghost")
        errs = schema.validate_manifest(data)
        self.assertTrue(any("unknown tool 'ghost'" in e for e in errs))

    def test_provides_mismatch_fails(self):
        data = sample_manifest_data()
        # grep claims it provides memory_store but it's listed under code_graph.
        data["tools"]["grep"]["provides"] = "memory_store"
        errs = schema.validate_manifest(data)
        self.assertTrue(any("provides" in e for e in errs))

    def test_bad_tool_kind_fails(self):
        data = sample_manifest_data()
        data["tools"]["grep"]["kind"] = "wizardry"
        errs = schema.validate_manifest(data)
        self.assertTrue(any("kind" in e for e in errs))


class TestOptionalJsonschemaDegradation(unittest.TestCase):
    """The optional jsonschema pass must degrade on absent / old / incompatible
    installs — only the built-in validator is authoritative."""

    @staticmethod
    def _fake_absent():
        # Mapping a name to None in sys.modules makes `import name` raise ImportError.
        return {"jsonschema": None}

    @staticmethod
    def _fake_incompatible_no_validator():
        # jsonschema 3.2.0-style: module imports fine but lacks Draft202012Validator.
        mod = types.ModuleType("jsonschema")
        # Deliberately no Draft202012Validator attribute.
        return {"jsonschema": mod}

    @staticmethod
    def _fake_validator_raises():
        # Present-but-broken: the attribute exists but blows up when used.
        mod = types.ModuleType("jsonschema")

        class _Boom:
            def __init__(self, *_a, **_k):
                raise RuntimeError("incompatible jsonschema internals")

        mod.Draft202012Validator = _Boom
        return {"jsonschema": mod}

    @staticmethod
    def _fake_modern():
        # Modern-style: a working validator that finds no extra errors.
        mod = types.ModuleType("jsonschema")

        class _Validator:
            def __init__(self, _schema):
                pass

            def iter_errors(self, _data):
                return iter(())

        mod.Draft202012Validator = _Validator
        return {"jsonschema": mod}

    def test_absent_does_not_crash(self):
        with mock.patch.dict(sys.modules, self._fake_absent()):
            self.assertEqual(schema.validate_manifest(sample_manifest_data()), [])

    def test_incompatible_no_validator_degrades(self):
        # The original bug: AttributeError on Draft202012Validator. Must now degrade.
        with mock.patch.dict(sys.modules, self._fake_incompatible_no_validator()):
            # Valid manifest still validates clean...
            self.assertEqual(schema.validate_manifest(sample_manifest_data()), [])
            # ...and a broken one is still caught by the authoritative built-in pass.
            bad = sample_manifest_data()
            del bad["capabilities"]
            errs = schema.validate_manifest(bad)
            self.assertTrue(any("capabilities" in e for e in errs))

    def test_present_but_validator_raises_degrades(self):
        with mock.patch.dict(sys.modules, self._fake_validator_raises()):
            self.assertEqual(schema.validate_manifest(sample_manifest_data()), [])
            bad = sample_manifest_data()
            bad["tools"]["grep"]["kind"] = "wizardry"
            self.assertTrue(any("kind" in e for e in schema.validate_manifest(bad)))

    def test_modern_present_runs_without_crash(self):
        with mock.patch.dict(sys.modules, self._fake_modern()):
            self.assertEqual(schema.validate_manifest(sample_manifest_data()), [])
            bad = sample_manifest_data()
            del bad["mokata"]
            self.assertTrue(schema.validate_manifest(bad))  # built-in still flags it


class TestManifestLoad(unittest.TestCase):
    def test_from_dict_roundtrip(self):
        m = Manifest.from_dict(sample_manifest_data())
        self.assertEqual(m.profile, "standard")
        self.assertEqual(m.mokata_version, "0.1.0")
        self.assertIn("code_graph", m.capabilities)
        self.assertEqual(m.fallback_order("code_graph"), ["graphtool", "grep"])

    def test_from_dict_invalid_raises(self):
        data = sample_manifest_data()
        del data["mokata"]
        with self.assertRaises(ManifestError):
            Manifest.from_dict(data)

    def test_load_missing_file_raises(self):
        with self.assertRaises(ManifestError):
            Manifest.load("/no/such/manifest.json")

    def test_load_bad_json_raises(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "manifest.json")
            with open(p, "w") as fh:
                fh.write("{ not json ")
            with self.assertRaises(ManifestError):
                Manifest.load(p)

    def test_load_valid_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "manifest.json")
            with open(p, "w") as fh:
                json.dump(sample_manifest_data(), fh)
            m = Manifest.load(p)
            self.assertEqual(m.profile, "standard")

    def test_unknown_capability_fallback_raises(self):
        m = Manifest.from_dict(sample_manifest_data())
        with self.assertRaises(ManifestError):
            m.fallback_order("nonexistent_need")

    def test_to_json_is_valid_manifest(self):
        m = Manifest.from_dict(sample_manifest_data())
        reparsed = json.loads(m.to_json())
        self.assertTrue(schema.is_valid(reparsed))

    def test_deep_copy_independence(self):
        # Mutating the loaded manifest's data must not affect a fresh sample.
        data = sample_manifest_data()
        m = Manifest.from_dict(copy.deepcopy(data))
        m.data["profile"] = "changed"
        self.assertEqual(sample_manifest_data()["profile"], "standard")


if __name__ == "__main__":
    unittest.main()
