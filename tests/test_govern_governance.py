"""F3/F4/F5/F6 — active token governance: sub-agent handback caps, output-density
compression, savings/budget, and prompt-cache awareness. Built on F1 TokenTracker + F2
retrieval."""

import os
import tempfile
import unittest

from _support import sample_manifest_data, write_sample_repo

from mokata.bootstrap import estimate_tokens
from mokata.config import Surface
from mokata.detect import Detector
from mokata.execmode import PARALLEL, ExecutionChoice, Task, TaskResult, run_tasks
from mokata.govern import (
    AuditLedger,
    BudgetReport,
    CachePrefix,
    OutputDensity,
    SavingsTracker,
    budget_statusline,
    build_stable_prefix,
    cap_summary,
    compress_output,
    density_enabled,
    is_cache_stable,
    prefix_fingerprint,
    stable_prefix_for,
)
from mokata.init import init_repo
from mokata.knowledge import KnowledgeLayer
from mokata.manifest import Manifest
from mokata.govern import jit_retrieve
from mokata.profiles import build_manifest_data
from mokata.router import Router


# --- F3: sub-agent context isolation (capped handback) --------------------------
class TestHandbackCap(unittest.TestCase):
    def test_large_handback_is_capped_to_a_summary(self):
        h = cap_summary("x" * 4000, cap_tokens=50)   # ~1000 tokens of raw context
        self.assertTrue(h.capped)
        self.assertLessEqual(h.tokens, 50)
        self.assertGreater(h.original_tokens, 50)

    def test_small_handback_passes_through(self):
        h = cap_summary("short answer", cap_tokens=50)
        self.assertFalse(h.capped)
        self.assertEqual(h.summary, "short answer")

    def test_execmode_handback_path_is_capped(self):
        class Runner:
            def run(self, task):
                raw = "verbose output " * 500    # heavy raw context
                return TaskResult(task.id, True, "s", output=raw, input_tokens=1,
                                  output_tokens=estimate_tokens(raw),
                                  seen_context=task.context)
        res = run_tasks([Task("a", "x", context="c")],
                        ExecutionChoice(PARALLEL, isolation=True),
                        runner=Runner(), handback_cap=40)
        self.assertLessEqual(estimate_tokens(res.results[0].summary), 40)


# --- F4: output-density compression --------------------------------------------
VERBOSE = "alpha\n\n\n\nbeta\nbeta\nbeta\n   \n\ngamma   \ngamma   \n"


class TestCompression(unittest.TestCase):
    def test_compression_reduces_tokens(self):
        self.assertLess(estimate_tokens(compress_output(VERBOSE)),
                        estimate_tokens(VERBOSE))

    def test_density_toggle_defaults_off(self):
        m = Manifest.from_dict(build_manifest_data("standard", "0.1.0"))
        self.assertFalse(density_enabled(m))
        data = build_manifest_data("standard", "0.1.0")
        data["settings"]["governance"] = {"output_density": True}
        self.assertTrue(density_enabled(Manifest.from_dict(data)))

    def test_output_density_passthrough_when_disabled(self):
        self.assertEqual(OutputDensity(False).compress(VERBOSE), VERBOSE)
        self.assertLess(estimate_tokens(OutputDensity(True).compress(VERBOSE)),
                        estimate_tokens(VERBOSE))


# --- F5: savings + budget -------------------------------------------------------
class TestBudget(unittest.TestCase):
    def test_record_and_report(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            st = SavingsTracker(ledger=led)
            st.record("dump-vs-jit", baseline_tokens=100, actual_tokens=30)
            report = st.report()
            self.assertEqual(report.saved, 70)
            self.assertAlmostEqual(report.pct, 70.0)
            self.assertIn("saved", budget_statusline(report).lower())
            self.assertIn("savings", [e["kind"] for e in led.entries()])

    def test_report_from_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            SavingsTracker(ledger=led).record("a", 100, 40)
            report = BudgetReport.from_ledger(led)
            self.assertEqual(report.saved, 60)

    def test_record_retrieval_savings(self):
        with tempfile.TemporaryDirectory() as d:
            write_sample_repo(d)
            router = Router(Manifest.from_dict(build_manifest_data("full", "0.1.0")),
                            Detector(overrides={"code-review-graph": False,
                                                "serena": False, "ripgrep": False}))
            layer = KnowledgeLayer.from_router(router, root=d)
            result = jit_retrieve(layer, ["compute"])
            st = SavingsTracker()
            st.record_retrieval(result)
            self.assertEqual(st.report().saved, result.saved)


# --- F6: prompt-cache awareness -------------------------------------------------
class TestCacheStability(unittest.TestCase):
    def test_identical_prefixes_are_stable(self):
        a = build_stable_prefix(["constitution", "rules"])
        b = build_stable_prefix(["constitution", "rules"])
        self.assertTrue(is_cache_stable(a, b))
        self.assertEqual(prefix_fingerprint(a), prefix_fingerprint(b))

    def test_changed_prefix_is_not_stable(self):
        self.assertFalse(is_cache_stable(build_stable_prefix(["x"]),
                                         build_stable_prefix(["x", "z"])))

    def test_surface_prefix_is_stable_across_runs(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
            p1 = stable_prefix_for(Surface.load(d))
            p2 = stable_prefix_for(Surface.load(d))
            self.assertIsInstance(p1, CachePrefix)
            self.assertEqual(p1.fingerprint(), p2.fingerprint())


if __name__ == "__main__":
    unittest.main()
