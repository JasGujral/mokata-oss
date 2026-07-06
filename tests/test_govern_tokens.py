"""F1/F2 — token/cost tracker and JIT graph-backed retrieval (retrieve by identifier,
not file dumps; show the context reduction)."""

import tempfile
import unittest

from _support import write_sample_repo

from mokata.detect import Detector
from mokata.govern import TokenTracker, jit_retrieve
from mokata.knowledge import KnowledgeLayer
from mokata.manifest import Manifest
from mokata.profiles import build_manifest_data
from mokata.router import Router


def grep_layer(root):
    router = Router(Manifest.from_dict(build_manifest_data("full", "0.1.0")),
                    Detector(overrides={"code-review-graph": False, "serena": False,
                                        "ripgrep": False}))
    return KnowledgeLayer.from_router(router, root=root)


class TestTokenTracker(unittest.TestCase):
    def test_tracks_tokens_and_cost(self):
        t = TokenTracker()
        t.add("call-1", input_text="a" * 40, output_text="b" * 80)
        self.assertGreater(t.total_input, 0)
        self.assertGreater(t.total_output, 0)
        self.assertGreater(t.cost(), 0.0)
        self.assertIn("token", t.report().lower())

    def test_explicit_token_counts_accepted(self):
        t = TokenTracker()
        t.add("c", input_tokens=100, output_tokens=50)
        self.assertEqual(t.total_input, 100)
        self.assertEqual(t.total_output, 50)


class TestCalibrationLogging(unittest.TestCase):
    """R11 — the tokenizer-free chars/4 estimate's safety margin becomes OBSERVABLE: log
    estimate-vs-actual to the ledger when a real count is available; log the estimate alone
    (never a fabricated actual) when it isn't. Logging never raises and never blocks the caller."""

    def _ledger(self):
        import os
        from mokata.govern import AuditLedger
        d = tempfile.mkdtemp()
        return AuditLedger(os.path.join(d, "ledger.jsonl"))

    def test_actual_over_margin_lands_with_drift(self):
        from mokata.govern import log_calibration
        led = self._ledger()
        rec = log_calibration(led, "bootstrap", estimate=100, actual=120)  # actual > estimate
        entries = led.entries()
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual(e["kind"], "token_calibration")
        self.assertEqual(e["context"], "bootstrap")
        self.assertEqual(e["estimate"], 100)
        self.assertEqual(e["actual"], 120)
        self.assertAlmostEqual(e["ratio"], 1.2)
        self.assertTrue(e["over_margin"])           # 120 > 100 -> chars/4 margin blown
        self.assertTrue(rec.over_margin)

    def test_actual_within_margin_not_flagged(self):
        from mokata.govern import log_calibration
        led = self._ledger()
        log_calibration(led, "bootstrap", estimate=100, actual=80)  # estimate ran high (good)
        e = led.entries()[0]
        self.assertEqual(e["actual"], 80)
        self.assertAlmostEqual(e["ratio"], 0.8)
        self.assertFalse(e["over_margin"])

    def test_no_actual_logs_estimate_alone_no_fabrication(self):
        from mokata.govern import log_calibration
        led = self._ledger()
        log_calibration(led, "bootstrap", estimate=100)   # no real count available
        e = led.entries()[0]
        self.assertEqual(e["estimate"], 100)
        self.assertNotIn("actual", e)                     # never fabricate an actual
        self.assertNotIn("ratio", e)
        self.assertNotIn("over_margin", e)

    def test_logging_never_raises_on_broken_ledger(self):
        from mokata.govern import log_calibration

        class Boom:
            def record(self, *a, **k):
                raise RuntimeError("disk full")

        # observability must never break the caller — swallow and return None
        self.assertIsNone(log_calibration(Boom(), "bootstrap", estimate=10, actual=10))

    def test_zero_estimate_degrades_clean(self):
        from mokata.govern import log_calibration
        led = self._ledger()
        log_calibration(led, "bootstrap", estimate=0, actual=5)   # no ZeroDivisionError
        e = led.entries()[0]
        self.assertFalse(e["over_margin"])


class TestJitRetrieval(unittest.TestCase):
    def test_retrieval_reduces_context_vs_dumping_files(self):
        with tempfile.TemporaryDirectory() as d:
            write_sample_repo(d)
            layer = grep_layer(d)
            result = jit_retrieve(layer, ["compute"])
            self.assertGreater(result.tokens_if_dumped, 0)
            self.assertLess(result.tokens_retrieved, result.tokens_if_dumped)
            self.assertGreater(result.saved, 0)
            self.assertGreater(result.saved_pct, 0)
            self.assertTrue(result.snippets)


if __name__ == "__main__":
    unittest.main()
