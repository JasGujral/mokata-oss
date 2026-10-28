"""0.0.26 Lane R — R7: the A/B benchmark harness (with mokata vs without), with no model call.

The harness's every step — corpus, fresh workdir, condition prepare, agent, hidden suite, scoring,
medians, report, replay — runs here on the DRY agent (the reference solution for one arm). A live
run spends the operator's own Claude login and is refused under CI.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bench.ab import harness as H          # noqa: E402
from bench.ab.__main__ import main as ab_main   # noqa: E402
from mokata.quiet_spawn import hidden      # noqa: E402


class R7TheCorpus(unittest.TestCase):

    def test_R7_twelve_tasks_each_a_real_discriminator(self):
        tasks = H.load_tasks()
        self.assertGreaterEqual(len(tasks), 12)
        self.assertEqual([], H.check_corpus(tasks),
                         "every hidden suite must FAIL on repo/ and PASS on reference/")

    def test_R7_the_corpus_hash_names_exactly_the_corpus(self):
        tasks = H.load_tasks()
        self.assertEqual(H.corpus_hash(tasks), H.corpus_hash(H.load_tasks()))
        self.assertNotEqual(H.corpus_hash(tasks), H.corpus_hash(tasks[:-1]))


class R7ScoringIsTheRecordedTestOutcome(unittest.TestCase):

    def test_R7_unittest_output_is_parsed_into_passed_of_total(self):
        ok = H.parse_unittest("....\n------\nRan 4 tests in 0.01s\n\nOK\n")
        self.assertEqual((4, 4, True), (ok.passed, ok.total, ok.solved))
        bad = H.parse_unittest("Ran 5 tests in 0.1s\n\nFAILED (failures=2, errors=1)\n")
        self.assertEqual((2, 5, False), (bad.passed, bad.total, bad.solved))
        broken = H.parse_unittest("ImportError: no module named fizz\n")
        self.assertEqual((0, 0, False), (broken.passed, broken.total, broken.solved),
                         "a suite that cannot import is unsolved, never a pass")


class R7TheDryRunExercisesTheWholePipeline(unittest.TestCase):

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.out = os.path.join(self._t.name, "out")

    def tearDown(self):
        self._t.cleanup()

    def test_R7_dry_run_end_to_end_medians_raw_data_and_replay(self):
        tasks = H.load_tasks(only=["01-fizzbuzz-off-by-one", "07-merge-intervals"])
        prepared = []
        rows = H.run_matrix(tasks, repeats=2, agent=H.DryAgent(), out_dir=self.out,
                            prepare=True,
                            setup_runner=lambda argv, cwd: prepared.append(argv) or 0)
        self.assertEqual(8, len(rows))                       # 2 tasks × 2 arms × 2 repeats
        self.assertTrue(prepared, "the mokata arm's condition setup was not applied")
        self.assertTrue(all(r.solved for r in rows if r.arm == "mokata"))
        self.assertFalse(any(r.solved for r in rows if r.arm == "bare"))
        meta = H.make_meta(tasks, H.DryAgent(), 2)
        summary = H.write_report(self.out, rows, meta)
        self.assertEqual(1.0, summary["overall"]["mokata"]["solve_rate"])
        self.assertEqual(0.0, summary["overall"]["bare"]["solve_rate"])
        self.assertEqual(1.0, summary["delta"]["solve_rate"])
        report = open(os.path.join(self.out, "RESULTS.md"), encoding="utf-8").read()
        self.assertIn("DRY RUN — not a measurement", report)
        self.assertIn("publishable: NO", report)
        # replay: the summary is re-derived from the committed raw rows alone
        raw = os.path.join(self.out, "raw.jsonl")
        self.assertEqual(8, sum(1 for _ in open(raw, encoding="utf-8")))
        self.assertEqual(summary, H.summarize(H.read_raw(raw)))
        self.assertEqual(0, ab_main(["summarize", "--raw", raw]))

    def test_R7_a_contaminated_row_is_excluded_and_counted(self):
        rows = [H.Row("t", "bare", 1, False, 0, 3, 0.0, 1.0, 2, 0),
                H.Row("t", "mokata", 1, True, 3, 3, 1.0, 1.0, 2, 0,
                      contamination=["bare condition ran WITH mokata present"])]
        s = H.summarize(rows)
        self.assertEqual(1, s["contaminated"])
        self.assertNotIn("mokata", s["overall"])

    def test_R7_publishable_only_for_a_live_clean_run_with_enough_repeats(self):
        rows = [H.Row("t", a, 1, True, 1, 1, 1.0, 1.0, 1, 0) for a in H.ARMS]
        meta = {"agent": "claude-code", "repeats": H.MIN_PUBLISHABLE_REPEATS}
        self.assertIn("publishable: yes", H.render_results(H.summarize(rows), meta))
        self.assertIn("publishable: NO", H.render_results(H.summarize(rows),
                                                          dict(meta, repeats=1)))


class R7TheLiveArmIsTheOperatorsOwnLoginAndOptIn(unittest.TestCase):

    def test_R7_a_live_run_is_refused_in_CI_and_without_yes(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"CI": "1"}):
            self.assertEqual(2, ab_main(["run", "--agent", "live", "--yes", "--out", d]))
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CI", None)
            self.assertEqual(2, ab_main(["run", "--agent", "live", "--out", d]))

    def test_R7_the_live_agent_scrubs_every_api_key_variable(self):
        from bench.g2.agent import SCRUBBED_ENV, scrubbed_env
        env = scrubbed_env({"ANTHROPIC_API_KEY": "sk-x", "PATH": "/bin"})
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertIn("ANTHROPIC_API_KEY", SCRUBBED_ENV)
        src = open(os.path.join(ROOT, "bench", "ab", "harness.py"), encoding="utf-8").read()
        self.assertIn("from bench.g2.agent import ClaudeCodeAgent", src)
        self.assertNotIn("os.environ[\"ANTHROPIC", src)

    def test_R7_the_cli_runs_a_dry_matrix_with_no_model_call(self):
        with tempfile.TemporaryDirectory() as d:
            out = subprocess.run([sys.executable, "-m", "bench.ab", "run", "--repeats", "1",
                                  "--only", "02-slugify", "--out", d], cwd=ROOT,
                                 capture_output=True, text=True, timeout=300,
                                 stdin=subprocess.DEVNULL,
                                 env=dict(os.environ, PYTHONPATH=ROOT + os.pathsep
                                          + os.path.join(ROOT, "src"), PATH=""),
                                 **hidden())
            self.assertEqual(0, out.returncode, out.stderr)
            meta = json.load(open(os.path.join(d, "run.json"), encoding="utf-8"))
            self.assertEqual("dry", meta["agent"])
            self.assertIn("no LLM judge", meta["scoring"])


if __name__ == "__main__":
    unittest.main()
