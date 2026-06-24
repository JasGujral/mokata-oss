"""K5 — `mokata doctor`: diagnose the manifest/config (missing providers, broken
adapters, role conflicts, bad trust levels)."""

import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.config import Constitution, Surface
from mokata.detect import Detector
from mokata.govern import diagnose
from mokata.manifest import Manifest


def surface_with_problems():
    data = {
        "manifest_version": 1, "mokata": {"version": "0.1.0"}, "profile": "custom",
        "layers": {"engine": {"enabled": True}, "knowledge": {"enabled": True},
                   "memory": {"enabled": True}, "governance": {"enabled": True}},
        "capabilities": {
            "code_graph": {"description": "g", "layer": "knowledge",
                           "fallback": ["toolA", "toolB"]},   # 2 providers (conflict)
        },
        "tools": {
            "toolA": {"provides": "code_graph", "kind": "mcp", "version": None,
                      "enabled": True, "detect": {"type": "command", "name": "nope-a"}},
            "toolB": {"provides": "code_graph", "kind": "cli", "version": None,
                      "enabled": True, "detect": {"type": "command", "name": "nope-b"}},
        },
        "settings": {"trust": {"toolA": "bogus-level"}},
    }
    m = Manifest.from_dict(data)
    det = Detector(overrides={"toolA": False, "toolB": False})   # both absent
    return Surface(m, Constitution("", None), root=".", detector=det)


class TestDoctor(unittest.TestCase):
    def test_flags_missing_provider(self):
        report = diagnose(surface_with_problems())
        self.assertFalse(report.ok)
        self.assertTrue(any(f.code == "missing-provider" for f in report.findings))

    def test_flags_role_conflict(self):
        report = diagnose(surface_with_problems())
        conflicts = [f for f in report.findings if f.code == "role-conflict"]
        self.assertTrue(conflicts)
        self.assertIn("code_graph", conflicts[0].detail)

    def test_flags_bad_trust_level(self):
        report = diagnose(surface_with_problems())
        self.assertTrue(any(f.code == "bad-trust" for f in report.findings))

    def test_clean_manifest_is_ok(self):
        from mokata.profiles import build_manifest_data
        m = Manifest.from_dict(build_manifest_data("standard", "0.1.0"))
        surface = Surface(m, Constitution("# c\n## A\n", None), root=".",
                          detector=Detector())
        report = diagnose(surface)
        # standard resolves code_graph (grep floor) + memory_store (sqlite) -> no errors
        self.assertTrue(report.ok)


if __name__ == "__main__":
    unittest.main()
