"""K1 — per-layer / per-tool toggles, ENFORCED at routing time.

Stage 1 left `layer_enabled()` descriptive. These tests pin the Stage 2 contract:
a disabled layer's capabilities disappear from the router; a disabled tool is
treated as absent and the router degrades to the next fallback.
"""

import unittest

from _support import sample_manifest_data

from mokata.detect import Detector
from mokata.manifest import Manifest
from mokata.router import Router


def toggle_manifest():
    """A manifest whose capabilities declare an owning layer and whose tools carry
    explicit `enabled` flags — the shape Stage 2 produces and enforces."""
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
            "code_graph": {
                "description": "structural queries",
                "layer": "knowledge",
                "fallback": ["primary", "grep"],
            },
            "memory_store": {
                "description": "where memory lives",
                "layer": "memory",
                "fallback": ["sqlite"],
            },
        },
        "tools": {
            "primary": {
                "provides": "code_graph",
                "kind": "mcp",
                "version": None,
                "enabled": True,
                "detect": {"type": "command", "name": "definitely-not-real-cmd-xyz"},
            },
            "grep": {
                "provides": "code_graph",
                "kind": "builtin",
                "version": None,
                "enabled": True,
                "detect": {"type": "always"},
            },
            "sqlite": {
                "provides": "memory_store",
                "kind": "library",
                "version": None,
                "enabled": True,
                "detect": {"type": "python_module", "name": "sqlite3"},
            },
        },
    }


class TestLayerToggles(unittest.TestCase):
    def test_disabled_layer_capabilities_removed_from_router(self):
        data = toggle_manifest()
        data["layers"]["knowledge"]["enabled"] = False
        m = Manifest.from_dict(data)
        router = Router(m, Detector(overrides={"primary": True}))

        needs = {r.need for r in router.resolve_all()}
        # code_graph belongs to the disabled 'knowledge' layer -> gone, no error.
        self.assertEqual(needs, {"memory_store"})

    def test_disabled_layer_resolve_all_raises_nothing(self):
        data = toggle_manifest()
        data["layers"]["memory"]["enabled"] = False
        data["layers"]["knowledge"]["enabled"] = False
        m = Manifest.from_dict(data)
        router = Router(m, Detector())
        # Every capability's layer is off -> empty, still no exception.
        self.assertEqual(router.resolve_all(), [])

    def test_enabled_layer_capabilities_present(self):
        m = Manifest.from_dict(toggle_manifest())
        router = Router(m, Detector(overrides={"primary": True}))
        needs = {r.need for r in router.resolve_all()}
        self.assertEqual(needs, {"code_graph", "memory_store"})

    def test_capability_enabled_helper(self):
        data = toggle_manifest()
        data["layers"]["knowledge"]["enabled"] = False
        m = Manifest.from_dict(data)
        self.assertFalse(m.capability_enabled("code_graph"))
        self.assertTrue(m.capability_enabled("memory_store"))

    def test_capability_without_declared_layer_is_enabled(self):
        # Backward-compat: a capability with no `layer` (Stage 1 manifests) is not
        # layer-gated and stays enabled.
        m = Manifest.from_dict(sample_manifest_data())
        self.assertTrue(m.capability_enabled("code_graph"))
        self.assertEqual(
            {r.need for r in Router(m).resolve_all()},
            {"code_graph", "memory_store"},
        )

    def test_resolve_of_disabled_layer_capability_is_unavailable_not_error(self):
        data = toggle_manifest()
        data["layers"]["knowledge"]["enabled"] = False
        m = Manifest.from_dict(data)
        r = Router(m, Detector(overrides={"primary": True})).resolve("code_graph")
        self.assertFalse(r.available)
        self.assertIsNone(r.tool)
        self.assertIn("knowledge", r.reason)
        self.assertIn("disabled", r.reason)


class TestToolToggles(unittest.TestCase):
    def test_disabled_tool_makes_router_skip_to_fallback(self):
        data = toggle_manifest()
        # primary is present in the environment, but the user turned it off.
        data["tools"]["primary"]["enabled"] = False
        m = Manifest.from_dict(data)
        router = Router(m, Detector(overrides={"primary": True}))
        r = router.resolve("code_graph")
        # Disabled -> treated as absent -> degrade to grep.
        self.assertEqual(r.tool, "grep")
        self.assertTrue(r.available)
        self.assertTrue(r.degraded)

    def test_disabled_tool_recorded_as_absent_in_attempted_chain(self):
        data = toggle_manifest()
        data["tools"]["primary"]["enabled"] = False
        m = Manifest.from_dict(data)
        router = Router(m, Detector(overrides={"primary": True}))
        r = router.resolve("code_graph")
        self.assertEqual(r.attempted, [("primary", False), ("grep", True)])

    def test_tool_enabled_helper_defaults_true(self):
        # A tool with no `enabled` key (Stage 1 manifests) defaults to enabled.
        m = Manifest.from_dict(sample_manifest_data())
        self.assertTrue(m.tool_enabled("grep"))

    def test_disabling_only_provider_makes_capability_unavailable(self):
        data = toggle_manifest()
        data["tools"]["sqlite"]["enabled"] = False
        m = Manifest.from_dict(data)
        r = Router(m, Detector()).resolve("memory_store")
        self.assertFalse(r.available)
        self.assertIsNone(r.tool)
        self.assertEqual(r.attempted, [("sqlite", False)])


if __name__ == "__main__":
    unittest.main()
