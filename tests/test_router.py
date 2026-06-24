"""A2 — capability router + A3 graceful degradation."""

import unittest

from _support import sample_manifest_data

from mokata.detect import Detector
from mokata.manifest import Manifest, ManifestError
from mokata.router import Router


class TestRouter(unittest.TestCase):
    def setUp(self):
        self.manifest = Manifest.from_dict(sample_manifest_data())

    def test_resolves_to_preferred_when_present(self):
        # Force the preferred provider present.
        router = Router(self.manifest, Detector(overrides={"graphtool": True}))
        r = router.resolve("code_graph")
        self.assertEqual(r.tool, "graphtool")
        self.assertTrue(r.available)
        self.assertFalse(r.degraded)
        self.assertEqual(r.preferred, "graphtool")

    def test_degrades_to_fallback_when_preferred_absent(self):
        # graphtool's real command does not exist -> falls back to grep (always).
        router = Router(self.manifest, Detector(overrides={"graphtool": False}))
        r = router.resolve("code_graph")
        self.assertEqual(r.tool, "grep")
        self.assertTrue(r.available)
        self.assertTrue(r.degraded)
        self.assertEqual(r.preferred, "graphtool")
        self.assertIn("fallback", r.reason)

    def test_attempted_chain_is_recorded(self):
        router = Router(self.manifest, Detector(overrides={"graphtool": False}))
        r = router.resolve("code_graph")
        self.assertEqual(r.attempted, [("graphtool", False), ("grep", True)])

    def test_unavailable_when_no_provider_present(self):
        # A manifest whose only provider is an absent command (no always fallback).
        data = sample_manifest_data()
        data["capabilities"]["code_graph"]["fallback"] = ["graphtool"]
        data["tools"] = {
            "graphtool": data["tools"]["graphtool"],
            "sqlite": data["tools"]["sqlite"],
        }
        manifest = Manifest.from_dict(data)
        router = Router(manifest, Detector(overrides={"graphtool": False}))
        r = router.resolve("code_graph")
        self.assertIsNone(r.tool)
        self.assertFalse(r.available)
        self.assertTrue(r.degraded)
        self.assertIn("no declared provider", r.reason)

    def test_unknown_need_raises(self):
        router = Router(self.manifest)
        with self.assertRaises(ManifestError):
            router.resolve("teleportation")

    def test_has_is_false_for_unknown_need(self):
        router = Router(self.manifest)
        self.assertFalse(router.has("teleportation"))

    def test_resolve_all_covers_every_capability(self):
        router = Router(self.manifest, Detector(overrides={"graphtool": False}))
        needs = {r.need for r in router.resolve_all()}
        self.assertEqual(needs, {"code_graph", "memory_store"})

    def test_summary_strings(self):
        router = Router(self.manifest, Detector(overrides={"graphtool": False}))
        self.assertIn("degraded", router.resolve("code_graph").summary())
        router2 = Router(self.manifest, Detector(overrides={"graphtool": True}))
        self.assertEqual(
            router2.resolve("code_graph").summary(), "code_graph -> graphtool"
        )


if __name__ == "__main__":
    unittest.main()
