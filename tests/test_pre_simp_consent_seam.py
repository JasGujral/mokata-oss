"""PRE-SIMP (0.0.15) — the consent boundary is ONE module, and a gated write still behaves.

The tools_write split moves the SI.3 consent boundary into `mcp/consent.py`. This pins two things:

  1. `mcp/consent.py` is the SINGLE definition site of the consent helpers — `def _consent(` (and
     the WriteGate driver `_gated_write`) live there and NOWHERE ELSE among the write-tool modules,
     so there is exactly one gate every tool routes through.
  2. Behaviour parity on one gated write: `remember` is PROPOSE-ONLY without an approval (it stages
     a proposal and writes nothing), and commits once through the full human round-trip — the same
     SI.3 contract as before the split.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import tempfile
import unittest

import _support  # noqa: F401 - puts src/ on the path

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "mokata")

_WRITE_TOOL_MODULES = ("tools_write", "tools_memory", "tools_share", "tools_session",
                       "tools_team", "tools_spec", "tools_config")


class TestConsentIsOneModule(unittest.TestCase):
    def test_consent_module_exposes_the_boundary(self):
        from mokata.mcp import consent
        for name in ("_trust", "_policy", "_consent", "_require", "_refused", "_propose",
                     "_record", "_gated_write"):
            self.assertTrue(callable(getattr(consent, name, None)),
                            f"consent.{name} is missing from the consent boundary")

    def test_consent_is_defined_only_in_consent_py(self):
        # `def _consent(` / `def _gated_write(` must appear in consent.py and in NO write-tool module
        # — one gate, one place.
        boundary = open(os.path.join(SRC, "mcp", "consent.py"), encoding="utf-8").read()
        self.assertIn("def _consent(", boundary)
        self.assertIn("def _gated_write(", boundary)
        for mod in _WRITE_TOOL_MODULES:
            text = open(os.path.join(SRC, "mcp", f"{mod}.py"), encoding="utf-8").read()
            self.assertNotIn("def _consent(", text,
                             f"mcp/{mod}.py redefines _consent — the boundary must live once")
            self.assertNotIn("def _gated_write(", text,
                             f"mcp/{mod}.py redefines _gated_write — the boundary must live once")


class TestGatedWriteBehaviourParity(unittest.TestCase):
    def setUp(self):
        if not _support.sqlite_disk_ok():
            self.skipTest("sandbox overlay FS can't back on-disk SQLite (B4) — unrelated to code")
        from mokata.init import init_repo
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        init_repo(root=self.root, profile="standard", assume_yes=True, out=lambda *_a: None)

    def tearDown(self):
        self._tmp.cleanup()

    def test_remember_is_propose_only_without_an_approval(self):
        from mokata.mcp import tools_write as TW
        out = TW.remember(path=self.root, subject="db", value="postgres")
        self.assertEqual(out["status"], "proposed")
        self.assertFalse(out["committed"])
        self.assertTrue(out.get("proposal_id"), "a proposal must be staged for a human to approve")

    def test_remember_commits_through_the_full_human_round_trip(self):
        from mokata.mcp import tools_write as TW
        out = _support.mcp_commit(TW.remember, path=self.root, subject="db", value="postgres")
        self.assertEqual(out["status"], "committed")
        self.assertTrue(out["committed"])


if __name__ == "__main__":
    unittest.main()
