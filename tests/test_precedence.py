"""H6 — conflict/overlap resolution: when two tools claim the same role, the manifest's
declared precedence (fallback order) resolves it, and the router honors it deterministically."""

import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.adapters import (
    declared_precedence,
    overlapping_capabilities,
    resolve_conflict,
)
from mokata.detect import Detector
from mokata.manifest import Manifest
from mokata.router import Router


def two_provider_manifest(order=("toolA", "toolB")):
    return {
        "manifest_version": 1,
        "mokata": {"version": "0.1.0"},
        "profile": "custom",
        "layers": {"engine": {"enabled": True}, "knowledge": {"enabled": True},
                   "memory": {"enabled": True}, "governance": {"enabled": True}},
        "capabilities": {
            "code_graph": {"description": "g", "layer": "knowledge",
                           "fallback": list(order)},
            "memory_store": {"description": "m", "layer": "memory",
                             "fallback": ["sqlite"]},
        },
        "tools": {
            "toolA": {"provides": "code_graph", "kind": "mcp", "version": None,
                      "enabled": True, "detect": {"type": "command", "name": "a-cmd"}},
            "toolB": {"provides": "code_graph", "kind": "cli", "version": None,
                      "enabled": True, "detect": {"type": "command", "name": "b-cmd"}},
            "sqlite": {"provides": "memory_store", "kind": "library", "version": None,
                       "enabled": True,
                       "detect": {"type": "python_module", "name": "sqlite3"}},
        },
        "settings": {},
    }


class TestPrecedence(unittest.TestCase):
    def test_overlap_detected(self):
        m = Manifest.from_dict(two_provider_manifest())
        overlaps = overlapping_capabilities(m)
        self.assertEqual(overlaps["code_graph"], ["toolA", "toolB"])
        self.assertNotIn("memory_store", overlaps)        # single provider, no overlap

    def test_declared_precedence_is_the_fallback_order(self):
        m = Manifest.from_dict(two_provider_manifest(("toolB", "toolA")))
        self.assertEqual(declared_precedence(m, "code_graph"), ["toolB", "toolA"])

    def test_resolve_conflict_picks_highest_precedence_present(self):
        m = Manifest.from_dict(two_provider_manifest())
        self.assertEqual(resolve_conflict(m, "code_graph", {"toolA", "toolB"}), "toolA")
        self.assertEqual(resolve_conflict(m, "code_graph", {"toolB"}), "toolB")

    def test_router_honors_precedence_deterministically(self):
        both_present = Detector(overrides={"toolA": True, "toolB": True})
        m = Manifest.from_dict(two_provider_manifest(("toolA", "toolB")))
        self.assertEqual(Router(m, both_present).resolve("code_graph").tool, "toolA")
        # flip precedence in the manifest -> router deterministically follows
        m2 = Manifest.from_dict(two_provider_manifest(("toolB", "toolA")))
        self.assertEqual(Router(m2, both_present).resolve("code_graph").tool, "toolB")


if __name__ == "__main__":
    unittest.main()
