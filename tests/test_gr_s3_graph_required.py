"""GR.S3 — `graph.required` gate: the core refusal verdict + settings + override + notice.

The differentiator (D1) flips on here: when `settings.graph.required` is true (DEFAULT), a
DEGRADED blast radius is REFUSED as decision input, not silently presented. This file covers the
pure gate machinery — `check_graph_required` (the verdict), the default-true settings read, the
session-scoped ledgered `--allow-degraded` override, and the once-per-repo upgrade notice. The
per-consumer wiring (Lens-1 / spec-check / domains) + MCP parity live in
`test_gr_s3_consumers.py`.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import tempfile
import unittest

from mokata.govern import graph_required as GR


class TestVerdict(unittest.TestCase):
    """`check_graph_required` — the gate verdict (doc 85 §3: a `*Outcome` from `check_*`)."""

    def test_degraded_plus_required_refuses(self):
        out = GR.check_graph_required(
            degraded=True, required=True, overridden=False, consumer="blast radius (Lens 1)")
        self.assertTrue(out.refused)
        self.assertFalse(out.allowed)

    def test_not_required_never_refuses(self):
        # explicit graph.required=false → today's behavior, no refusal.
        out = GR.check_graph_required(
            degraded=True, required=False, overridden=False, consumer="spec-check")
        self.assertFalse(out.refused)
        self.assertTrue(out.allowed)

    def test_not_degraded_never_refuses(self):
        # a real graph / AST answered with evidence (degraded=False) → the healthy negative.
        out = GR.check_graph_required(
            degraded=False, required=True, overridden=False, consumer="blast radius (Lens 1)")
        self.assertFalse(out.refused)

    def test_override_lifts_the_refusal_but_marking_survives(self):
        # honesty survives the override: allowed, but STILL marked degraded (caveat not stripped).
        out = GR.check_graph_required(
            degraded=True, required=True, overridden=True, consumer="spec-check")
        self.assertFalse(out.refused)
        self.assertTrue(out.allowed)
        self.assertTrue(out.degraded)

    def test_refusal_message_is_informative_and_names_two_roads(self):
        out = GR.check_graph_required(
            degraded=True, required=True, overridden=False, consumer="blast radius (Lens 1)",
            backend="grep", mentions=7, files=3, targets=["pay"])
        msg = out.render()
        self.assertIn("REFUSED", msg)
        self.assertIn("graph adopt", msg)          # road 1: adopt a real graph
        self.assertIn("allow-degraded", msg)        # road 2: the ledgered escape
        self.assertNotIn("Traceback", msg)          # never a stack trace

    def test_refusal_cites_ast_zero_and_lexical_mention_count(self):
        # the GR.S1 hand-off: empty-AST-evidence reaches here as degraded=True — the refusal must
        # cite AST-zero + the lexical-floor mention count (backend == "ast").
        out = GR.check_graph_required(
            degraded=True, required=True, overridden=False, consumer="blast radius (Lens 1)",
            backend="ast", mentions=5, files=2, targets=["handle"])
        msg = out.render()
        self.assertIn("AST", msg)
        self.assertIn("5", msg)                     # the lexical mention count
        self.assertIn("handle", msg)                # the target it could not resolve

    def test_grep_floor_message_does_not_claim_ast(self):
        out = GR.check_graph_required(
            degraded=True, required=True, overridden=False, consumer="spec-check",
            backend="grep", mentions=4, files=1)
        self.assertNotIn("AST floor found", out.render())


class TestUpgradeNotice(unittest.TestCase):
    """The loud one-time notice explaining the default changed on upgrade (design decision #3)."""

    def test_notice_fires_once_then_never_again(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, ".mokata"), exist_ok=True)
            first = GR.fire_upgrade_notice_once(root)
            self.assertIsNotNone(first)
            self.assertIn("graph.required", first)
            self.assertIsNone(GR.fire_upgrade_notice_once(root))   # never repeats
            self.assertIsNone(GR.fire_upgrade_notice_once(root))

    def test_refusal_render_carries_the_notice_when_supplied(self):
        out = GR.check_graph_required(
            degraded=True, required=True, overridden=False, consumer="spec-check",
            notice="NOTE: graph.required is now on by default.")
        self.assertIn("graph.required is now on by default", out.render())


class TestSettingDefault(unittest.TestCase):
    """`settings.graph.required` reads TRUE by default — no migration write on upgrade."""

    def test_absent_setting_reads_true(self):
        class _M:
            def setting(self, key, default=None):
                return {}.get(key, default)        # empty settings block (an upgraded manifest)

        class _S:
            manifest = _M()
        self.assertTrue(GR.graph_required_enabled(_S()))

    def test_explicit_false_is_respected(self):
        class _M:
            def setting(self, key, default=None):
                return {"required": False} if key == "graph" else default

        class _S:
            manifest = _M()
        self.assertFalse(GR.graph_required_enabled(_S()))

    def test_broken_manifest_fails_open_to_required(self):
        class _S:
            manifest = None
        self.assertTrue(GR.graph_required_enabled(_S()))


class TestSessionOverride(unittest.TestCase):
    """The ledgered `--allow-degraded` escape: session-scoped (run_id), fail-CLOSED read."""

    def _surface(self, root):
        from mokata.config import Surface
        return Surface.load(root)

    def test_write_then_read_back_within_the_session(self):
        with tempfile.TemporaryDirectory() as root:
            self._init(root)
            surface = self._surface(root)
            self.assertFalse(GR.read_degraded_override(root, "run-A"))
            GR.write_degraded_override(surface, "run-A", reason="ci needs it", actor="jas")
            self.assertTrue(GR.read_degraded_override(root, "run-A"))

    def test_override_is_session_scoped(self):
        with tempfile.TemporaryDirectory() as root:
            self._init(root)
            surface = self._surface(root)
            GR.write_degraded_override(surface, "run-A", reason="x", actor="jas")
            self.assertFalse(GR.read_degraded_override(root, "run-B"))   # a new run has none

    def test_override_writes_a_ledger_record_with_reason(self):
        with tempfile.TemporaryDirectory() as root:
            self._init(root)
            surface = self._surface(root)
            GR.write_degraded_override(surface, "run-A", reason="deliberate", actor="jas")
            from mokata.govern import AuditLedger
            entries = AuditLedger.from_mokata_dir(surface.mokata_dir).entries()
            rec = [e for e in entries if e.get("kind") == GR.GRAPH_OVERRIDE_KIND]
            self.assertEqual(len(rec), 1)
            self.assertEqual(rec[0]["reason"], "deliberate")
            self.assertTrue(rec[0]["degraded"])

    def _init(self, root):
        from mokata.init import init_repo
        init_repo(root=root, profile="standard", assume_yes=True, out=lambda _: None)


if __name__ == "__main__":
    unittest.main()
