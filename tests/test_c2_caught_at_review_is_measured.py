"""0.0.22 stage C2 — `SL.S3`: review as a thin final gate, with a MEASURED caught-at-review rate.

Row (doc 84): `SL.S3` — the review gate existed; the "caught at review vs in the loop" signal did
not. Doc 110 criterion 2, clause ④: *review has a measured caught-at-review rate.*

In the loop: every run-state gate BLOCK is recorded as a `gate_block` event keyed to the run whose
state blocked (distinct `(gate, path)` — a retried write is one catch). At review: the verdict
carries `blocking`, the count of Blocking findings. The rate is `at_review / (at_review + in_loop)`;
a verdict without its count leaves the run NOT MEASURED, never zero (§7g).
"""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import gate_hook as G
from mokata import hook_cli
from mokata import progress_events as PE
from mokata.cli import main
from mokata.config import Surface
from mokata.govern.resume import PipelineCheckpoint
from mokata.init import init_repo
from mokata.run_resolver import APPROACH_PREFIX
from mokata.state import StateStore
from mokata.tdd_state import state_dir

IMPL = "src/app.py"
SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "mokata")


def _silent(_):
    pass


def _repo(d, *runs):
    init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
    surface = Surface.load(d)
    for rid in runs:
        PipelineCheckpoint(surface.state, rid).ensure_registered()
    if runs:
        from mokata import run_intent as RI
        RI.bind_registered(d, "S1", runs[0])     # S1 holds the first run (its own claim)
    return Surface.load(d)


def _block(d, path=IMPL, sid="S1"):
    with mock.patch.object(hook_cli, "_read_stdin_bounded", return_value=json.dumps(
            {"session_id": sid, "cwd": d, "tool_name": "Write",
             "tool_input": {"file_path": os.path.join(d, path), "content": "x"}})), \
            mock.patch("sys.stderr", new_callable=io.StringIO):
        return hook_cli.gate_guard_main([])


class SlS3AGateBlockIsAnInLoopCatchForItsRun(unittest.TestCase):

    def setUp(self):
        os.environ.pop("MOKATA_SESSION_ID", None)

    def test_sl_s3_a_block_is_recorded_against_the_run_whose_state_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, "r1")          # registered, still in brainstorm -> blocks
            self.assertEqual(G.BLOCK_EXIT, _block(d))
            events = [e for e in PE.ProgressLog.from_surface(surface).read_events(tail=None)
                      if e["type"] == PE.GATE_BLOCK]
            self.assertEqual(1, len(events))
            self.assertEqual("r1", events[0]["run_id"])
            self.assertEqual(G.GATE_PHASE, events[0]["data"]["gate"])
            self.assertEqual(IMPL, events[0]["data"]["path"])

    def test_a_block_in_a_window_that_holds_no_claim_is_not_that_runs_catch(self):
        """0.0.22 verification D-1, amended by INT-2a (B-F4): a window with no claim is no longer
        policed by a run ANOTHER session's registration holds (S1 holds r1 here) — so it is neither
        blocked by it nor credited to it. The bound session S1 is still blocked and credited."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, "r1")
            self.assertEqual(0, _block(d, sid="S-other"))
            self.assertEqual(0, PE.review_yield(surface, "r1").in_loop)
            self.assertEqual(G.BLOCK_EXIT, _block(d, sid="S1"))
            self.assertEqual(1, PE.review_yield(surface, "r1").in_loop)

    def test_the_outcome_carries_its_run_only_on_a_block(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "r1")
            self.assertEqual("r1", G.check_write(d, IMPL).run_id)
            self.assertIsNone(G.check_write(d, "tests/test_app.py").run_id)

    def test_a_retried_write_is_one_catch_and_a_new_file_is_another(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, "r1")
            for _ in range(3):
                _block(d)
            _block(d, path="src/other.py")
            self.assertEqual(2, PE.review_yield(surface, "r1").in_loop)

    def test_an_allowed_write_records_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)               # no run at all -> SI.1 floor, allowed
            self.assertEqual(0, _block(d))
            self.assertFalse([e for e in PE.ProgressLog.from_surface(surface).read_events(tail=None)
                              if e["type"] == PE.GATE_BLOCK])

    def test_the_block_projects_into_the_typed_stream_as_a_gate_decision(self):
        from mokata.events.store import query
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "r1")
            _block(d)
            rows = [r for r in query(d, types=["gate_decision"])
                    if r.data.get("decision") == "block"]
            self.assertTrue(rows, "a block never reached the typed event stream")
            self.assertEqual(G.GATE_PHASE, rows[-1].data["gate"])


class TheRateIsMeasuredAndNeverAZeroNobodyCounted(unittest.TestCase):

    def _verdict(self, surface, rid, blocking, passed=None):
        """A consistent verdict by default: it FAILS exactly when it raised a Blocking finding."""
        if passed is None:
            passed = not (isinstance(blocking, int) and not isinstance(blocking, bool)
                          and blocking > 0)
        PE.record_review_verdict(surface, passed=passed, independent=True, blocking=blocking,
                                 run_id=rid)

    def test_the_rate_is_review_over_review_plus_loop(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, "r1")
            for p in ("src/a.py", "src/b.py", "src/c.py"):
                PE.record_gate_block(state_dir(d), "r1", G.GATE_TDD, p)
            self._verdict(surface, "r1", 1, passed=False)
            self._verdict(surface, "r1", 0)
            y = PE.review_yield(surface, "r1")
            self.assertEqual((3, 1, 2, True), (y.in_loop, y.at_review, y.reviews, y.measured))
            self.assertAlmostEqual(0.25, y.rate)
            self.assertIn("1 of 4 (25%)", y.line())

    def test_a_verdict_without_its_count_is_not_measured_not_zero(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, "r1")
            PE.record_gate_block(state_dir(d), "r1", G.GATE_TDD, "src/a.py")
            self._verdict(surface, "r1", None)
            y = PE.review_yield(surface, "r1")
            self.assertFalse(y.measured)
            self.assertIsNone(y.rate)
            self.assertIn("not measured", y.line())

    def test_no_review_and_nothing_caught_are_two_different_answers(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, "r1", "r2")
            self.assertIn("no review verdict", PE.review_yield(surface, "r1").line())
            self._verdict(surface, "r2", 0)
            y = PE.review_yield(surface, "r2")
            self.assertTrue(y.measured)
            self.assertIsNone(y.rate)
            self.assertIn("nothing caught", y.line())

    def test_across_runs_only_measured_runs_are_summed(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, "r1", "r2", "r3")
            PE.record_gate_block(state_dir(d), "r1", G.GATE_TDD, "src/a.py")
            self._verdict(surface, "r1", 1)                  # measured: 1 at review, 1 in loop
            self._verdict(surface, "r2", 2)                  # measured: 2 at review
            PE.record_gate_block(state_dir(d), "r3", G.GATE_TDD, "src/z.py")
            self._verdict(surface, "r3", None)               # NOT measured: left out entirely
            y = PE.review_yield_all(surface)
            self.assertEqual((1, 3, 2), (y.in_loop, y.at_review, y.reviews))
            self.assertAlmostEqual(0.75, y.rate)

    def test_parallel_runs_never_share_their_figures(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, "rA", "rB")
            PE.record_gate_block(state_dir(d), "rA", G.GATE_TDD, "src/a.py")
            self._verdict(surface, "rB", 2)
            self.assertEqual((1, 0), (PE.review_yield(surface, "rA").in_loop,
                                      PE.review_yield(surface, "rA").at_review))
            self.assertEqual((0, 2), (PE.review_yield(surface, "rB").in_loop,
                                      PE.review_yield(surface, "rB").at_review))

    def test_a_negative_or_boolean_count_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, "r1")
            for bad in (-1, True, "3"):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    self._verdict(surface, "r1", bad)


class ReviewLibSweepAVerdictCannotContradictItsOwnCount(unittest.TestCase):
    """REVIEW-LIB-SWEEP — the one adopted practice that maps to a real gate (see the report)."""

    def test_review_lib_sweep_a_pass_with_a_blocking_finding_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, "r1")
            with self.assertRaises(ValueError):
                PE.record_review_verdict(surface, passed=True, independent=True, blocking=1,
                                         run_id="r1")
            self.assertIsNone(PE.latest_review_verdict_event(surface, run_id="r1"),
                              "a contradicted verdict was written — ship could accept it")

    def test_a_fail_that_names_nothing_is_refused(self):
        self.assertTrue(PE.verdict_contradiction(False, 0))
        self.assertEqual("", PE.verdict_contradiction(False, 2))
        self.assertEqual("", PE.verdict_contradiction(True, 0))
        self.assertEqual("", PE.verdict_contradiction(True, None), "no count, no check")

    def test_the_mcp_twin_refuses_the_same_way(self):
        from mokata.mcp.tools_read import review_record
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "r1")
            out = review_record(path=d, passed=True, blocking=2, run="r1")
            self.assertFalse(out["recorded"])
            self.assertFalse(out["satisfies_gate"])
            self.assertIn("cannot be recorded as PASSED", out["reason"])


class EverySurfaceSaysTheSameNumber(unittest.TestCase):

    def test_the_mcp_twins_record_and_report_it(self):
        from mokata.mcp.tools_read import review_record, review_status
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, "r1")
            PE.record_gate_block(state_dir(d), "r1", G.GATE_TDD, "src/a.py")
            self.assertTrue(review_record(path=d, failed=True, independent=True, blocking=3,
                                          run="r1")["recorded"])
            self.assertFalse(review_record(path=d, passed=True, blocking=-2, run="r1")["recorded"])
            self.assertTrue(review_record(path=d, passed=True, independent=True, blocking=0,
                                          run="r1")["recorded"])
            caught = review_status(path=d, run="r1")["caught"]
            self.assertEqual({"in_loop": 1, "at_review": 3, "measured": True},
                             {k: caught["run"][k] for k in ("in_loop", "at_review", "measured")})
            self.assertAlmostEqual(0.75, caught["run"]["rate"])
            self.assertEqual(PE.review_yield(surface, "r1").line(), caught["run"]["line"])

    def test_the_cli_records_with_blocking_and_prints_the_figure(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "r1")
            buf = io.StringIO()
            with redirect_stdout(buf):
                main(["progress", "record-review", "--failed", "--independent", "--blocking", "2",
                      "--run", "r1", "--path", d])
                main(["progress", "record-review", "--passed", "--independent", "--blocking", "0",
                      "--run", "r1", "--path", d])
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["progress", "review-status", "--run", "r1", "--path", d, "--caught"])
            self.assertEqual(0, rc)
            quiet = io.StringIO()
            with redirect_stdout(quiet):
                main(["progress", "review-status", "--run", "r1", "--path", d])
            self.assertEqual(1, len(quiet.getvalue().strip().splitlines()),
                             "the gate's one-line answer grew without --caught")
            self.assertIn("caught at review: 2 of 2 (100%)", buf.getvalue())
            self.assertIn("across runs:", buf.getvalue())

    def test_the_review_skill_tells_the_model_to_record_the_count(self):
        with io.open(os.path.join(SRC, "skills", "review", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("blocking=<how many Blocking findings you raised>", text)
        self.assertIn("NOT MEASURED", text)


if __name__ == "__main__":
    unittest.main()
