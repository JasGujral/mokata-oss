"""Stage 67 — wall-clock latency budget (it-feels-instant).

Each hot, every-turn operation (statusline, SessionStart briefing, the per-PreToolUse secret
scan, the grep-floor query, memory recall, `status`) has a wall-clock budget; these tests assert
each stays under it on a realistic fixture. ROBUST by design: generous ceilings (≥100× headroom),
a warmup + median-of-N, an auto-relax for CI, and a `MOKATA_PERF_SKIP` escape hatch — so a slow
runner never false-fails. Plus: the bench helper reports sane timings, and the hot ops are
behaviour-stable (a future perf optimization can't silently change output).

This is WALL-CLOCK latency, distinct from the token budget (F5 `mokata budget`).
"""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from _support import sample_manifest_data, write_sample_repo  # noqa: F401  (path-fix side-effect)

from mokata import perf
from mokata.config import Surface
from mokata.govern.secrets import scan
from mokata.knowledge.grep_backend import GrepBackend
from mokata.memory import CONTEXT, MemoryItem, MemoryStore

_SKIP = os.environ.get("MOKATA_PERF_SKIP")


def _fixture(d):
    """A realistic, initialized repo: a manifest, a small code repo (for the grep query), and a
    populated memory store (for recall)."""
    from mokata.init import init_repo
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    write_sample_repo(d)
    surface = Surface.load(d)
    store = MemoryStore.from_surface(surface)
    for i in range(30):
        item = MemoryItem.create(f"convention {i}",
                                 f"prefer compute() over helper() for case {i}", mtype=CONTEXT)
        try:
            store.remember(item, assume_yes=True)
        except Exception:
            break
    return surface


class TestHotPathsWithinBudget(unittest.TestCase):
    @unittest.skipIf(_SKIP, "MOKATA_PERF_SKIP set — perf assertions disabled for this runner")
    def test_each_hot_op_under_budget(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _fixture(d)
            results = perf.run_benchmarks(surface, repeat=9, warmup=3)
            self.assertTrue(results, "no hot ops were benchmarked")
            measured = {r.name for r in results}
            # the every-turn paths the budget is really about must all be present
            for must in ("statusline", "briefing", "secret_scan", "grep_query", "recall",
                         "status"):
                self.assertIn(must, measured, f"hot op not benchmarked: {must}")
            for r in results:
                self.assertTrue(r.within_budget,
                                f"OVER BUDGET — {r.render()} (effective ≤ "
                                f"{r.effective_budget_ms:.0f} ms)")

    def test_every_benchmarked_op_has_a_positive_budget(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _fixture(d)
            for r in perf.run_benchmarks(surface, repeat=3, warmup=1):
                self.assertGreater(r.budget_ms, 0.0, f"{r.name} has no budget")
                self.assertEqual(r.budget_ms, perf.LATENCY_BUDGETS_MS[r.name])


class TestBenchHelperReportsTimings(unittest.TestCase):
    def test_measure_returns_ordered_triple(self):
        med, mn, mx = perf.measure(lambda: sum(range(1000)), repeat=5, warmup=1)
        self.assertGreaterEqual(med, 0.0)
        self.assertLessEqual(mn, med)
        self.assertLessEqual(med, mx)

    def test_results_carry_sane_timings(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _fixture(d)
            results = perf.run_benchmarks(surface, repeat=5, warmup=1)
            for r in results:
                self.assertEqual(r.runs, 5)
                self.assertGreater(r.median_ms, 0.0)
                self.assertLessEqual(r.min_ms, r.median_ms)
                self.assertLessEqual(r.median_ms, r.max_ms)
                self.assertGreater(r.headroom_x, 1.0)   # within budget => headroom > 1×

    def test_render_report_lists_ops_and_verdict(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _fixture(d)
            report = perf.render_report(perf.run_benchmarks(surface, repeat=3, warmup=1))
            self.assertIn("mokata bench", report)
            self.assertIn("statusline", report)
            self.assertIn("within budget", report)


class TestRobustnessControls(unittest.TestCase):
    def test_relax_factor_env_override(self):
        saved = os.environ.get("MOKATA_PERF_RELAX")
        try:
            os.environ["MOKATA_PERF_RELAX"] = "5"
            self.assertEqual(perf.relax_factor(), 5.0)
            os.environ["MOKATA_PERF_RELAX"] = "garbage"
            self.assertGreaterEqual(perf.relax_factor(), 1.0)   # bad value -> safe default
        finally:
            if saved is None:
                os.environ.pop("MOKATA_PERF_RELAX", None)
            else:
                os.environ["MOKATA_PERF_RELAX"] = saved

    def test_relax_widens_the_effective_budget(self):
        r = perf.BenchResult("x", 5, median_ms=10.0, min_ms=9.0, max_ms=11.0,
                             budget_ms=20.0, relax=3.0)
        self.assertEqual(r.effective_budget_ms, 60.0)
        self.assertTrue(r.within_budget)

    def test_unbudgeted_op_never_fails(self):
        r = perf.BenchResult("x", 5, median_ms=999.0, min_ms=999.0, max_ms=999.0, budget_ms=0.0)
        self.assertTrue(r.within_budget)


class TestBehaviourUnchanged(unittest.TestCase):
    """A perf optimization must not change behaviour — the hot ops are deterministic. (No path
    was over budget this stage, so nothing was optimized; this guards future changes.)"""

    def test_secret_scan_is_deterministic(self):
        a = [(f.layer, f.rule_id) for f in scan(text=perf._SCAN_SAMPLE)]
        b = [(f.layer, f.rule_id) for f in scan(text=perf._SCAN_SAMPLE)]
        self.assertEqual(a, b)
        # the realistic sample carries NO secret (so the scan does full work, no false positive)
        self.assertEqual(a, [])

    def test_grep_query_is_stable(self):
        with tempfile.TemporaryDirectory() as d:
            write_sample_repo(d)
            g = GrepBackend(d)
            r1 = sorted((r.path, r.line, r.symbol) for r in g.query("callers", "compute").references)
            r2 = sorted((r.path, r.line, r.symbol) for r in g.query("callers", "compute").references)
            self.assertEqual(r1, r2)
            self.assertTrue(r1, "the sample repo should have callers of compute")

    def test_briefing_text_is_byte_stable(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _fixture(d)
            from mokata.bootstrap import build_bootstrap
            self.assertEqual(build_bootstrap(surface).text, build_bootstrap(surface).text)


class TestBenchCli(unittest.TestCase):
    def test_bench_degrades_clean_when_uninitialized(self):
        from mokata.cli import cmd_bench

        class A:
            pass
        a = A()
        with tempfile.TemporaryDirectory() as d:
            a.path = d
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_bench(a)
            self.assertEqual(rc, 0)
            self.assertIn("not initialized", buf.getvalue())

    def test_bench_runs_and_reports_on_an_initialized_repo(self):
        from mokata.cli import cmd_bench

        class A:
            pass
        a = A()
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            a.path = d
            a.repeat = 3
            a.ascii = True
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_bench(a)
            out = buf.getvalue()
            self.assertIn("mokata bench", out)
            self.assertIn("statusline", out)
            self.assertEqual(rc, 0)   # all paths within budget on a sane machine


if __name__ == "__main__":
    unittest.main()
