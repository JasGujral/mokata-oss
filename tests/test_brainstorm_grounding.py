"""D6 — approaches are grounded in graph/memory WHEN present, and degrade cleanly
when absent (no graph/memory, or declared providers all missing)."""

import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.brainstorm import ground
from mokata.detect import Detector
from mokata.manifest import Manifest
from mokata.profiles import build_manifest_data
from mokata.router import Router


def absent_providers_manifest():
    """Capabilities declared, but every provider is an absent command (no floor)."""
    return {
        "manifest_version": 1,
        "mokata": {"version": "0.1.0"},
        "profile": "custom",
        "layers": {
            "engine": {"enabled": True},
            "knowledge": {"enabled": True},
            "memory": {"enabled": True},
            "governance": {"enabled": True},
        },
        "capabilities": {
            "code_graph": {"description": "g", "layer": "knowledge",
                           "fallback": ["ghostgraph"]},
            "memory_store": {"description": "m", "layer": "memory",
                             "fallback": ["ghostmem"]},
        },
        "tools": {
            "ghostgraph": {"provides": "code_graph", "kind": "mcp", "version": None,
                           "enabled": True,
                           "detect": {"type": "command", "name": "nope-graph-xyz"}},
            "ghostmem": {"provides": "memory_store", "kind": "external",
                         "version": None, "enabled": True,
                         "detect": {"type": "command", "name": "nope-mem-xyz"}},
        },
        "settings": {},
    }


class TestGroundingPresent(unittest.TestCase):
    def test_standard_profile_grounds_in_graph_and_memory(self):
        m = Manifest.from_dict(build_manifest_data("standard", "0.1.0"))
        g = ground(Router(m, Detector()))
        # standard resolves code_graph (grep floor) + memory_store (sqlite) locally.
        self.assertTrue(g.graph_available)
        self.assertTrue(g.memory_available)
        self.assertTrue(g.grounded)
        self.assertIsNotNone(g.graph_tool)
        self.assertIsNotNone(g.memory_tool)


class TestGroundingDegrades(unittest.TestCase):
    def test_minimal_profile_has_no_grounding_but_does_not_error(self):
        m = Manifest.from_dict(build_manifest_data("minimal", "0.1.0"))
        g = ground(Router(m, Detector()))   # minimal declares no capabilities
        self.assertFalse(g.graph_available)
        self.assertFalse(g.memory_available)
        self.assertFalse(g.grounded)
        # degradation is explicit, not silent
        self.assertTrue(any("graph" in n.lower() for n in g.notes))
        self.assertTrue(any("memory" in n.lower() for n in g.notes))

    def test_declared_but_absent_providers_degrade(self):
        m = Manifest.from_dict(absent_providers_manifest())
        g = ground(Router(m, Detector(overrides={"ghostgraph": False,
                                                  "ghostmem": False})))
        self.assertFalse(g.graph_available)
        self.assertFalse(g.memory_available)

    def test_no_router_is_fully_degraded(self):
        g = ground(None)
        self.assertFalse(g.grounded)
        self.assertTrue(g.notes)


if __name__ == "__main__":
    unittest.main()
