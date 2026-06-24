"""I4 — lethal-trifecta mitigation: when system access + private data + an outbound
action coexist, the outbound action is GATED behind explicit human approval."""

import os
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.govern import (
    AuditLedger,
    OutboundRequest,
    TrifectaGuard,
    TrifectaState,
    detect_trifecta,
)


def send(dest="https://example.com/webhook", payload="report"):
    return OutboundRequest(action="http_post", destination=dest, payload=payload)


class TestTrifectaDetection(unittest.TestCase):
    def test_all_three_is_the_trifecta(self):
        self.assertTrue(detect_trifecta(True, True, True))
        self.assertFalse(detect_trifecta(True, True, False))
        self.assertFalse(detect_trifecta(False, True, True))
        self.assertTrue(TrifectaState(True, True, True).active)


class TestTrifectaGate(unittest.TestCase):
    def test_blocked_without_approval(self):
        with tempfile.TemporaryDirectory() as d:
            guard = TrifectaGuard(ledger=AuditLedger(os.path.join(d, "l.jsonl")))
            decision = guard.gate_outbound(send(), TrifectaState(True, True, True),
                                           confirm=lambda _t: False)
            self.assertFalse(decision.allowed)
            self.assertTrue(decision.gated)

    def test_allowed_after_explicit_approval(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            guard = TrifectaGuard(ledger=led)
            decision = guard.gate_outbound(send(), TrifectaState(True, True, True),
                                           assume_yes=True)
            self.assertTrue(decision.allowed)
            self.assertTrue(decision.gated)
            self.assertIn("outbound", [e["kind"] for e in led.entries()])

    def test_no_gate_when_trifecta_inactive(self):
        guard = TrifectaGuard()
        decision = guard.gate_outbound(send(), TrifectaState(True, False, True))
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.gated)


if __name__ == "__main__":
    unittest.main()
