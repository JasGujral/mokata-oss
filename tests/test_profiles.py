"""K2 — profiles: minimal / standard / full + custom, each a deterministic set."""

import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.manifest import Manifest
from mokata.profiles import (
    CAPABILITY_LAYERS,
    DEFAULT_PROFILE,
    PROFILES,
    build_manifest_data,
    profile_enabled_set,
    profile_names,
)


class TestProfileRoster(unittest.TestCase):
    def test_all_four_profiles_exist(self):
        self.assertEqual(
            set(profile_names()), {"minimal", "standard", "full", "custom"}
        )

    def test_default_profile_is_standard(self):
        # Default profile is `standard` — lean, local, dependency-free. Users opt into
        # `full` (graph + all adapters) via `mokata init --profile full`.
        self.assertEqual(DEFAULT_PROFILE, "standard")


class TestDeterministicEnabledSets(unittest.TestCase):
    def test_minimal_enabled_set(self):
        s = profile_enabled_set("minimal")
        self.assertEqual(s["layers"], ("engine", "governance"))
        self.assertEqual(s["capabilities"], {})
        self.assertEqual(s["tools"], ())

    def test_standard_enabled_set(self):
        s = profile_enabled_set("standard")
        self.assertEqual(
            s["layers"], ("engine", "knowledge", "memory", "governance")
        )
        self.assertEqual(
            s["capabilities"],
            {"code_graph": ["ripgrep", "grep"], "memory_store": ["sqlite"]},
        )
        self.assertEqual(s["tools"], ("grep", "ripgrep", "sqlite"))

    def test_full_enabled_set(self):
        s = profile_enabled_set("full")
        self.assertEqual(
            s["layers"], ("engine", "knowledge", "memory", "governance")
        )
        self.assertEqual(
            s["capabilities"],
            {
                "code_graph": ["code-review-graph", "serena", "ripgrep", "grep"],
                "memory_store": ["native-memory", "obsidian", "sqlite"],
            },
        )
        self.assertEqual(
            s["tools"],
            ("code-review-graph", "grep", "native-memory", "obsidian",
             "ripgrep", "serena", "sqlite"),
        )

    def test_full_is_a_proper_superset_of_standard(self):
        std = profile_enabled_set("standard")
        full = profile_enabled_set("full")
        self.assertTrue(set(std["tools"]).issubset(set(full["tools"])))
        self.assertNotEqual(std["tools"], full["tools"])

    def test_enabled_set_is_deterministic(self):
        # Same input, same output — every time.
        self.assertEqual(
            profile_enabled_set("full"), profile_enabled_set("full")
        )

    def test_custom_is_a_deterministic_starting_set(self):
        s = profile_enabled_set("custom")
        # custom scaffolds everything wired, ready to hand-tune.
        self.assertEqual(s, profile_enabled_set("full"))

    def test_unknown_profile_raises(self):
        with self.assertRaises(ValueError):
            profile_enabled_set("nonsense")


class TestBuildManifestMatchesEnabledSet(unittest.TestCase):
    def _check(self, profile):
        s = profile_enabled_set(profile)
        data = build_manifest_data(profile, "0.1.0")
        m = Manifest.from_dict(data)  # must be schema-valid

        enabled_layers = tuple(
            sorted(n for n, on in
                   ((n, m.layer_enabled(n)) for n in m.layers) if on)
        )
        self.assertEqual(enabled_layers, tuple(sorted(s["layers"])))

        caps = {need: m.fallback_order(need) for need in m.capabilities}
        self.assertEqual(caps, s["capabilities"])

        self.assertEqual(tuple(sorted(m.tools)), s["tools"])
        return m

    def test_minimal(self):
        m = self._check("minimal")
        self.assertEqual(m.capabilities, {})

    def test_standard(self):
        self._check("standard")

    def test_full(self):
        self._check("full")

    def test_custom(self):
        m = self._check("custom")
        self.assertEqual(m.profile, "custom")

    def test_generated_capability_declares_its_layer(self):
        m = Manifest.from_dict(build_manifest_data("full", "0.1.0"))
        self.assertEqual(m.capability_layer("code_graph"), CAPABILITY_LAYERS["code_graph"])
        self.assertEqual(m.capability_layer("memory_store"), CAPABILITY_LAYERS["memory_store"])

    def test_generated_tools_carry_enabled_flag(self):
        m = Manifest.from_dict(build_manifest_data("full", "0.1.0"))
        for tid in m.tools:
            self.assertTrue(m.tool_enabled(tid))


if __name__ == "__main__":
    unittest.main()
