"""A6 + H5 — typed adapter contract: declare which capabilities a tool provides, reason
about coverage/gaps (A6), and validate a third-party adapter against the contract (H5)."""

import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.adapters import AdapterContract, negotiate, validate_adapter


class TestAdapterValidation(unittest.TestCase):
    def test_valid_adapter_passes(self):
        self.assertEqual(validate_adapter(
            {"name": "crg", "provides": ["code_graph"], "kind": "mcp"}), [])

    def test_missing_provides_rejected(self):
        errs = validate_adapter({"name": "x", "kind": "mcp"})
        self.assertTrue(any("provides" in e for e in errs))

    def test_bad_kind_rejected(self):
        errs = validate_adapter({"name": "x", "provides": ["y"], "kind": "wizardry"})
        self.assertTrue(any("kind" in e for e in errs))

    def test_blank_name_rejected(self):
        errs = validate_adapter({"name": "", "provides": ["y"]})
        self.assertTrue(any("name" in e for e in errs))

    def test_from_dict_roundtrip(self):
        a = AdapterContract.from_dict(
            {"name": "crg", "provides": ["code_graph"], "kind": "mcp"})
        self.assertEqual(a.name, "crg")
        self.assertEqual(a.provides, ["code_graph"])
        self.assertEqual(AdapterContract.from_dict(a.to_dict()).to_dict(), a.to_dict())


class TestNegotiation(unittest.TestCase):
    def test_reports_coverage_and_gaps(self):
        adapters = [
            AdapterContract("graph", ["code_graph"], "mcp"),
            AdapterContract("mem", ["memory_store"], "external"),
        ]
        report = negotiate(["code_graph", "memory_store", "episodic_search"], adapters)
        self.assertEqual(report.covered["code_graph"], ["graph"])
        self.assertEqual(report.covered["memory_store"], ["mem"])
        self.assertEqual(report.gaps, ["episodic_search"])     # unmet need
        self.assertFalse(report.fully_covered)

    def test_full_coverage(self):
        adapters = [AdapterContract("both", ["a", "b"], "external")]
        report = negotiate(["a", "b"], adapters)
        self.assertTrue(report.fully_covered)
        self.assertEqual(report.gaps, [])


if __name__ == "__main__":
    unittest.main()
