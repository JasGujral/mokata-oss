"""REVIEW-FIX.R4 — the review record's failure modes become LOUD and TRUTHFUL (0.0.16).

R1 made the verdict run-keyed and fail-closed; R2 fixed the window it is read through; R3 made ONE
truth source reachable from both surfaces. This stage fixes what the two surfaces SAY when that
truth source fails, which until now was the last place the cluster still lied.

Two defects, one theme — a gate that cannot report its own failure honestly is a gate people learn
to route around:

  (a) RECORD-FAIL WAS SILENT SUCCESS. `cmd_progress_record_review` printed "could not record the
      verdict ({exc}); continuing." and returned **0**. A review whose verdict could NOT be written
      exited GREEN, so nothing checking the exit code — a script, a skill, CI — could tell a
      recorded verdict from a lost one. That is precisely the failure the whole 6r loop exists to
      prevent (evidence over claims), made invisible in the one channel machines read.

  (b) READ-ERROR MASQUERADED AS ABSENT-VERDICT. `cmd_progress_review_status` printed "review hasn't
      run — run /mokata:review first ({exc})" and exited 2. Fail-closed was and remains CORRECT.
      The MESSAGE was the defect, twice over: it named a remedy (re-run review) that cannot fix a
      log which will not read, and it echoed `{exc}` — a parse fault quotes the offending line, and
      a `review_verdict` line carries FINDINGS text, which can quote project content.

  (b') …and the masquerade ran DEEPER than the message. `read_events_backward` is degrade-clean by
      design — it yields nothing on OSError and SKIPS an unparseable line — so an unreadable or
      damaged log never reached that `except` at all: it arrived at the gate as the SAME silence a
      genuinely un-reviewed run produces. Renaming the exception path alone would have left the
      real fault still answering "review hasn't run". `ProgressLog.read_fault` asks the log which
      it was, from inside `ship_review_gate`, so BOTH surfaces get the same two answers.

Business-level asserts: exit codes, printed lines, tool results — what a caller observes.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import contextlib
import io
import json
import os
import stat
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata.cli_commands.runviews import (
    RECORD_REVIEW_FAILED_EXIT,
    review_record_failed_line,
)
from mokata.config import Surface
from mokata.govern.resume import PipelineCheckpoint
from mokata.mcp import tools_read as TR
from mokata.progress_events import (
    FAULT_CORRUPT,
    FAULT_UNKNOWN,
    FAULT_UNREADABLE,
    REVIEW_VERDICT,
    ProgressLog,
    record_review_verdict,
    review_log_path,
    review_read_error_message,
    review_read_error_unblock,
    ship_review_gate,
)

PHASES = ("brainstorm", "analysis", "strawman", "pre_mortem", "probes",
          "completeness_gate", "emit")

# Planted in a corrupt log line. It stands in for what a real `review_verdict` line carries —
# FINDINGS text, which can quote project source. It must never reach an operator-facing answer.
CANARY = "LEAK-CANARY-findings-must-not-surface"

# The absent-verdict answer, verbatim. The read-error sentences QUOTE this phrase to unteach
# it ("this is NOT 'review hasn't run'"), so the negatives below must test for the whole
# line — the remedy — not the substring.
ABSENT_LINE = "review hasn't run — run /mokata:review first"


# --------------------------------------------------------------------------- fixtures
def _repo(d):
    from mokata.init import init_repo
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _persist_run(root, run_id, passed=PHASES):
    cp = PipelineCheckpoint(Surface.load(root).state, run_id)
    cp.ensure_registered()
    for p in passed:
        cp.mark_passed(p)
    return cp


def _cli(argv, cwd):
    """Run the CLI as ship/review do (a separate process's argv), returning (rc, stdout)."""
    from mokata.cli import main
    out = io.StringIO()
    old = os.getcwd()
    os.chdir(cwd)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = main(argv)
    finally:
        os.chdir(old)
    return rc, out.getvalue()


def _log(surface):
    return ProgressLog.from_surface(surface)


def _corrupt(surface, payload=CANARY):
    """Append a line that cannot be parsed, carrying the canary — a torn write, as a crash
    mid-append leaves behind, with findings-shaped content in the wreckage."""
    path = _log(surface).path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"type": "review_verdict", "data": {"findings": "%s"' % payload)
        fh.write("\n")
    return path


@contextlib.contextmanager
def _unreadable(path):
    """Make an existing file un-openable, and put it back afterwards."""
    before = stat.S_IMODE(os.stat(path).st_mode)
    os.chmod(path, 0o000)
    try:
        yield path
    finally:
        os.chmod(path, before)


class _NoPinnedSession(unittest.TestCase):
    """`MOKATA_SESSION_ID` pins run resolution — a pin leaking in from the environment would
    silently change what these scenarios resolve to (the R1/R2/R3 discipline)."""

    def setUp(self):
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("MOKATA_SESSION_ID", None)


def _is_root() -> bool:
    """True when the process can read/write through a mode it has no permission for.

    `os.geteuid` is POSIX-ONLY and this is evaluated at IMPORT time (a class decorator), so a bare
    call raises `AttributeError` on Windows and collapses the WHOLE module during unittest
    discovery — reddening every job that merely imports it, including the hooks-execute legs whose
    own steps pass. Windows has no euid: report "not root" and let the permission-mode tests run
    (or be skipped by their own guards)."""
    return getattr(os, "geteuid", lambda: 1)() == 0


@unittest.skipIf(_is_root(), "root can read a 0o000 file — the fault cannot be staged")
class _NeedsPermissions(_NoPinnedSession):
    pass


# ======================================================= THE regression
class TestReviewFixR4Regression(_NoPinnedSession):
    def test_review_fix_r4_regression(self):
        """Both defects, in one test, against the exact pre-R4 behaviour they replace.

        (a) a record that cannot be written no longer exits 0 pretending it did;
        (b) a log that cannot be read no longer answers with review's remedy — and the pre-R4
            sentence, reconstructed verbatim, is not what either surface says any more."""
        pre_r4_record = "mokata review: could not record the verdict (disk full); continuing."
        pre_r4_status = "review hasn't run — run /mokata:review first (boom)"

        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo")

            # ---- (a) record-fail is no longer silent success -------------------------------
            with mock.patch("mokata.progress_events.record_review_verdict",
                            side_effect=OSError("disk full")):
                rc, out = _cli(["progress", "record-review", "--passed", "--path", d], d)
            self.assertNotEqual(rc, 0, "a lost verdict still exited GREEN")
            self.assertEqual(rc, RECORD_REVIEW_FAILED_EXIT)
            self.assertNotEqual(out.strip(), pre_r4_record)
            self.assertIn("FAILED to record the verdict", out)
            self.assertIn("BLOCK as if review never ran", out)
            self.assertIn("disk full", out)             # the write fault IS named
            # …and nothing was written, so the gate is honestly still blocking
            self.assertTrue(ship_review_gate(surface, run_id="solo").blocks)

            # ---- (b) read-error no longer wears absent-verdict's clothes --------------------
            _corrupt(surface)
            rc, out = _cli(["progress", "review-status", "--path", d], d)
            self.assertEqual(rc, 2, "fail-closed must be unchanged")
            self.assertNotIn(pre_r4_status, out)
            self.assertNotIn(ABSENT_LINE, out)
            self.assertIn("could not be READ", out)
            with open(_log(surface).path, encoding="utf-8") as fh:
                self.assertIn(CANARY, fh.read())
            self.assertNotIn(CANARY, out, "the corrupt line's content leaked to the operator")


# ======================================================= 1 · CLI record-review: LOUD + non-zero
class TestCliRecordFailsLoudly(_NoPinnedSession):
    def _fail(self, d, argv=("progress", "record-review", "--passed")):
        with mock.patch("mokata.progress_events.record_review_verdict",
                        side_effect=OSError("read-only file system")):
            return _cli(list(argv) + ["--path", d], d)

    def test_a_failed_record_exits_non_zero(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "solo")
            rc, _ = self._fail(d)
            self.assertEqual(rc, RECORD_REVIEW_FAILED_EXIT)
            self.assertNotEqual(rc, 0)

    def test_the_exit_code_is_1_not_the_clusters_block_2(self):
        """Deliberate, and the distinction carries meaning: 2 is this cluster's BLOCK — a verdict
        about the CODE that routes a human to /mokata:review. A record failure is not a verdict
        about anything, and answering 2 would mis-route exactly the way defect (b) did."""
        self.assertEqual(RECORD_REVIEW_FAILED_EXIT, 1)
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "solo")
            rc_record, _ = self._fail(d)
            rc_block, _ = _cli(["progress", "review-status", "--path", d], d)
            self.assertEqual(rc_block, 2)               # the gate BLOCK code, untouched
            self.assertNotEqual(rc_record, rc_block)    # …and a record fault is not that

    def test_the_loud_line_names_the_consequence_and_the_remedy(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "solo")
            _rc, out = self._fail(d)
            self.assertIn("FAILED to record the verdict", out)
            self.assertIn("nothing was written", out)
            self.assertIn("BLOCK as if review never ran", out)
            self.assertIn("mokata progress record-review --passed|--failed --run <run id>", out)
            self.assertIn("mokata sessions", out)

    def test_the_write_faults_exc_is_named(self):
        """The asymmetry, asserted: a WRITE fault may echo `{exc}` because it carries filesystem
        detail and never log CONTENT. (The READ path may not — pinned below.)"""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "solo")
            _rc, out = self._fail(d)
            self.assertIn("read-only file system", out)

    def test_it_still_never_raises_at_the_caller(self):
        """The review skill's degrade-clean contract SURVIVES — relocated from exit-code silence
        to message truthfulness. No traceback, no exception out of `main`."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "solo")
            rc, out = self._fail(d)
            self.assertIsInstance(rc, int)
            self.assertNotIn("Traceback", out)

    def test_a_SUCCESSFUL_record_still_exits_zero_on_both_shapes(self):
        """The change is confined to the failure path: the recorded line and the run-LESS line
        (R1's honest-but-satisfies-nothing case) both still exit 0."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "solo")
            rc, out = _cli(["progress", "record-review", "--passed", "--independent",
                            "--path", d], d)
            self.assertEqual((rc, out),
                             (0, "mokata review: recorded verdict passed (independent) "
                                 "for run solo.\n"))
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "runA")
            _persist_run(d, "runB")                     # ambiguous -> run-less
            rc, out = _cli(["progress", "record-review", "--failed", "--path", d], d)
            self.assertEqual(rc, 0, "a run-less record is a SUCCESSFUL write, not a failure")
            self.assertIn("WITHOUT a run", out)

    def test_a_real_unwritable_log_exits_non_zero(self):
        """Not a mock: a directory where the log cannot be created."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo")
            state_root = os.path.dirname(_log(surface).path)
            os.makedirs(state_root, exist_ok=True)
            if _is_root():
                self.skipTest("root writes through a 0o500 directory")
            before = stat.S_IMODE(os.stat(state_root).st_mode)
            os.chmod(state_root, 0o500)                 # read+execute, no write
            try:
                rc, out = _cli(["progress", "record-review", "--passed", "--path", d], d)
            finally:
                os.chmod(state_root, before)
            self.assertEqual(rc, RECORD_REVIEW_FAILED_EXIT, out)
            self.assertIn("FAILED to record the verdict", out)


# ======================================================= 2 · CLI review-status: two answers
class TestCliReadErrorVsAbsentVerdict(_NoPinnedSession):
    def test_absent_verdict_is_byte_identical_to_before(self):
        """The answer that was always right stays exactly right — R3 pinned this line, and R4
        must not disturb it while giving the OTHER fault its own."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "solo")
            rc, out = _cli(["progress", "review-status", "--path", d], d)
            self.assertEqual(
                (rc, out),
                (2, "review hasn't run — run /mokata:review first"
                    "  → to unblock: run /mokata:review first\n"))

    def test_a_corrupt_log_is_a_DIFFERENT_answer(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo")
            absent_rc, absent_out = _cli(["progress", "review-status", "--path", d], d)
            _corrupt(surface)
            fault_rc, fault_out = _cli(["progress", "review-status", "--path", d], d)
            self.assertNotEqual(absent_out, fault_out)
            self.assertEqual((absent_rc, fault_rc), (2, 2))     # both BLOCK — fail-closed intact
            self.assertIn("could not be READ", fault_out)
            self.assertIn("damaged lines", fault_out)
            self.assertNotIn(ABSENT_LINE, fault_out)

    def test_the_read_error_names_the_log_and_how_to_repair_it(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo")
            path = _corrupt(surface)
            _rc, out = _cli(["progress", "review-status", "--path", d], d)
            self.assertIn(path, out)                            # the actual file, not a hint
            self.assertIn("repair the log, not the review", out)
            self.assertIn("re-running review alone will NOT fix", out)
            self.assertIn("→ to unblock:", out)

    def test_the_read_error_says_what_it_is_NOT(self):
        """The remedy must not merely be right — it must unteach the wrong one, because the wrong
        one is what operators read here for years."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo")
            _corrupt(surface)
            _rc, out = _cli(["progress", "review-status", "--path", d], d)
            self.assertIn("this is NOT 'review hasn't run'", out)

    def test_a_read_error_STILL_BLOCKS(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo")
            _corrupt(surface)
            rc, _ = _cli(["progress", "review-status", "--path", d], d)
            self.assertEqual(rc, 2)
            self.assertTrue(ship_review_gate(Surface.load(d), run_id="solo").blocks)

    def test_a_corrupt_log_does_not_hide_a_verdict_that_IS_readable(self):
        """The probe only speaks when the damage COULD have changed the answer: a run whose verdict
        is intact still gets its normal answer, torn neighbours and all."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo")
            _corrupt(surface)
            record_review_verdict(surface, passed=True, independent=True, run_id="solo")
            rc, out = _cli(["progress", "review-status", "--path", d], d)
            self.assertEqual((rc, out), (0, "review passed (independent ✓)\n"))

    def test_no_verdict_and_NO_log_at_all_is_absent_not_a_fault(self):
        """A repo that has never written an event is not broken — it genuinely has not reviewed."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo")
            self.assertFalse(os.path.exists(_log(surface).path))
            _rc, out = _cli(["progress", "review-status", "--path", d], d)
            self.assertIn("review hasn't run", out)

    def test_an_exception_on_the_read_reports_a_read_error_not_absent(self):
        """The `except` handler itself — a read that RAISED before any gate could be built."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "solo")
            with mock.patch("mokata.progress_events.ship_review_gate",
                            side_effect=OSError("boom")):
                rc, out = _cli(["progress", "review-status", "--path", d], d)
            self.assertEqual(rc, 2)
            self.assertIn("could not be READ", out)
            self.assertNotIn(ABSENT_LINE, out)
            self.assertNotIn("boom", out)               # …and no `{exc}`, per the safety bar


class TestCliUnreadableLog(_NeedsPermissions):
    def test_an_unopenable_log_names_THAT_fault(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo")
            record_review_verdict(surface, passed=True, independent=True, run_id="solo")
            with _unreadable(_log(surface).path):
                rc, out = _cli(["progress", "review-status", "--path", d], d)
            self.assertEqual(rc, 2)
            self.assertIn("could not be READ", out)
            self.assertIn("exists but could not be opened", out)
            self.assertNotIn(ABSENT_LINE, out)

    def test_an_unreadable_log_never_becomes_a_PASS(self):
        """The direction that matters: hiding a verdict must never invent one."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo")
            record_review_verdict(surface, passed=True, independent=True, run_id="solo")
            self.assertFalse(ship_review_gate(Surface.load(d), run_id="solo").blocks)
            with _unreadable(_log(surface).path):
                gate = ship_review_gate(Surface.load(d), run_id="solo")
            self.assertTrue(gate.blocks)
            self.assertFalse(gate.readable)
            self.assertFalse(gate.passed)


# ======================================================= 3 · SECRET-SAFETY: the leak canary
class TestNoRawExcOnTheReadPath(_NoPinnedSession):
    def test_the_corrupt_lines_content_never_surfaces_on_any_read_answer(self):
        """The bar R3 set for the MCP tool, now binding on the CLI. A parse fault can quote the
        offending line; a `review_verdict` line carries FINDINGS text, which can quote project
        source. The canary is planted IN the damage and must not appear anywhere an operator or a
        model can see it — CLI stdout, tool result, or rendered block."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo")
            path = _corrupt(surface)
            with open(path, encoding="utf-8") as fh:
                self.assertIn(CANARY, fh.read())                         # it IS on disk

            _rc, out = _cli(["progress", "review-status", "--path", d], d)
            self.assertNotIn(CANARY, out)
            for kw in ({}, {"response_format": "detailed"}, {"run": "solo"}):
                with self.subTest(call=kw):
                    st = TR.review_status(path=d, **kw)
                    self.assertNotIn(CANARY, json.dumps(st))

    def test_the_raising_read_path_echoes_no_exception_text(self):
        """A raised exception is the OTHER carrier: `json.JSONDecodeError` quotes the line it
        choked on. Neither surface may print it."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "solo")
            boom = ValueError(f"Expecting ',' delimiter: {CANARY}")
            with mock.patch("mokata.progress_events.ship_review_gate", side_effect=boom):
                _rc, out = _cli(["progress", "review-status", "--path", d], d)
                st = TR.review_status(path=d, response_format="detailed")
            self.assertNotIn(CANARY, out)
            self.assertNotIn(CANARY, json.dumps(st))

    def test_the_read_error_sentences_carry_only_a_kind_and_a_path(self):
        """The builders themselves, asserted directly: their whole input surface is a fixed fault
        vocabulary plus a path — there is no channel through which log bytes could enter."""
        for fault in (FAULT_UNREADABLE, FAULT_CORRUPT, FAULT_UNKNOWN):
            with self.subTest(fault=fault):
                msg = review_read_error_message(fault, "/tmp/x.jsonl")
                self.assertIn("/tmp/x.jsonl", msg)
                self.assertIn("could not be READ", msg)
                self.assertIn("this is NOT 'review hasn't run'", msg)
        self.assertNotEqual(review_read_error_message(FAULT_UNREADABLE, "/p"),
                            review_read_error_message(FAULT_CORRUPT, "/p"))

    def test_a_findings_bearing_verdict_still_never_leaks_through_a_read(self):
        """R3's non-disclosure contract, re-asserted with the new answer in play."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo")
            TR.review_record(path=d, failed=True, findings=CANARY)
            _corrupt(surface, payload="second-" + CANARY)
            _rc, out = _cli(["progress", "review-status", "--path", d], d)
            self.assertNotIn(CANARY, out)
            self.assertIn(CANARY, json.dumps(_verdicts(surface)))        # recorded, not disclosed


def _verdicts(surface):
    path = _log(surface).path
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for ln in fh.read().splitlines():
            if not ln.strip():
                continue
            try:
                e = json.loads(ln)
            except ValueError:
                continue
            if isinstance(e, dict) and e.get("type") == REVIEW_VERDICT:
                out.append(e)
    return out


# ======================================================= 4 · the MCP twins move in lockstep
class TestMcpTwins(_NoPinnedSession):
    def test_review_status_distinguishes_the_same_two_answers(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo")
            absent = TR.review_status(path=d)
            self.assertTrue(absent["blocks"])
            self.assertTrue(absent["readable"])
            self.assertFalse(absent["present"])
            self.assertEqual(absent["message"], "review hasn't run — run /mokata:review first")

            _corrupt(surface)
            fault = TR.review_status(path=d)
            self.assertTrue(fault["blocks"])            # still BLOCKS
            self.assertFalse(fault["readable"])         # …for a different, named reason
            self.assertFalse(fault["present"])
            self.assertIn("could not be READ", fault["message"])
            self.assertNotEqual(fault["message"], absent["message"])

    def test_both_surfaces_say_the_SAME_sentence_on_each_answer(self):
        """Cross-surface parity, the R3 way — one builder, not two copies."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo")
            for stage in ("absent", "corrupt"):
                if stage == "corrupt":
                    _corrupt(surface)
                with self.subTest(answer=stage):
                    st = TR.review_status(path=d, response_format="detailed")
                    rc, out = _cli(["progress", "review-status", "--path", d], d)
                    self.assertEqual(rc, 2)
                    self.assertEqual(st["block"], out.strip())
                    self.assertIn(st["message"], out)
                    self.assertIn(st["unblock"], out)

    def test_the_raising_path_matches_the_cli_sentence_too(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "solo")
            with mock.patch("mokata.progress_events.ship_review_gate",
                            side_effect=OSError("boom")):
                st = TR.review_status(path=d, response_format="detailed")
                rc, out = _cli(["progress", "review-status", "--path", d], d)
            self.assertEqual(rc, 2)
            self.assertFalse(st["readable"])
            self.assertTrue(st["blocks"])
            self.assertEqual(st["block"], out.strip())

    def test_readable_is_true_on_every_ordinary_answer(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "solo")
            self.assertTrue(TR.review_status(path=d)["readable"])            # absent
            TR.review_record(path=d, failed=True)
            self.assertTrue(TR.review_status(path=d)["readable"])            # a FAIL
            TR.review_record(path=d, passed=True, independent=True)
            st = TR.review_status(path=d)
            self.assertTrue(st["readable"])                                  # a PASS
            self.assertFalse(st["blocks"])

    def test_review_record_surfaces_the_same_loud_failure(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "solo")
            with mock.patch("mokata.progress_events.record_review_verdict",
                            side_effect=OSError("disk full")):
                res = TR.review_record(path=d, passed=True)
                rc, out = _cli(["progress", "record-review", "--passed", "--path", d], d)
            self.assertFalse(res["recorded"])
            self.assertEqual(res["status"], "error")
            self.assertFalse(res["satisfies_gate"])
            self.assertIn("disk full", res["reason"])
            # the SAME sentence the CLI prints as it exits non-zero — one builder, one wording
            self.assertEqual(res["message"], out.strip())
            self.assertEqual(rc, RECORD_REVIEW_FAILED_EXIT)

    def test_the_failed_record_message_is_the_shared_builder_verbatim(self):
        exc = OSError("disk full")
        self.assertEqual(
            review_record_failed_line(exc),
            "mokata review: FAILED to record the verdict (disk full) — nothing was written, so "
            "ship's review gate will BLOCK as if review never ran. Retry, or record it from the "
            "terminal: `mokata progress record-review --passed|--failed --run <run id>` "
            "(`mokata sessions` lists them).")

    def test_the_r3_comments_naming_this_stage_are_gone(self):
        """R3 deliberately left two handlers blurred with a comment naming REVIEW-FIX.R4 as the
        stage that would fix them. It did — so the promissory notes must not survive as if unpaid."""
        import mokata.mcp.tools_read as M
        with open(M.__file__, encoding="utf-8") as fh:
            text = fh.read()
        for stale in ("is REVIEW-FIX.R4's deliverable, not this stage's",
                      "is REVIEW-FIX.R4.)"):
            self.assertNotIn(stale, text, f"a stale R3 promissory comment survives: {stale!r}")


# ======================================================= 5 · the log's own fault probe
class TestReadFaultProbe(_NoPinnedSession):
    def test_absent_log_is_no_fault(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(ProgressLog(os.path.join(d, "nope.jsonl")).read_fault())

    def test_a_well_formed_log_is_no_fault(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            log = _log(surface)
            for i in range(20):
                log.append_event("stage_enter", "develop", run_id="r", data={"n": i})
            self.assertIsNone(log.read_fault())

    def test_a_torn_line_is_CORRUPT(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            log = _log(surface)
            log.append_event("stage_enter", "develop", run_id="r")
            with open(log.path, "a", encoding="utf-8") as fh:
                fh.write('{"event_id": "torn", "ts": ')
            self.assertEqual(log.read_fault(), FAULT_CORRUPT)

    def test_blank_lines_are_not_corruption(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            log = _log(surface)
            log.append_event("stage_enter", "develop", run_id="r")
            with open(log.path, "a", encoding="utf-8") as fh:
                fh.write("\n\n   \n")
            self.assertIsNone(log.read_fault())

    def test_a_cap_sliced_first_line_is_not_counted_as_damage(self):
        """The scan's own byte cap slices a line in half; the backward scan DROPS that fragment,
        so the probe must not report it as on-disk damage — the two read the same window."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            log = _log(surface)
            for i in range(40):
                log.append_event("stage_enter", "develop", run_id="r", data={"n": i})
            size = os.path.getsize(log.path)
            self.assertIsNone(log.read_fault(byte_cap=size // 2))

    def test_the_probe_never_raises(self):
        # a path that is a DIRECTORY: open() raises IsADirectoryError (an OSError), which the
        # probe must classify rather than propagate into a read-only gate.
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(ProgressLog(d).read_fault(), FAULT_UNREADABLE)

    def test_a_broken_surface_resolves_to_a_read_error_not_a_traceback(self):
        class Broken:
            pass
        self.assertEqual(review_log_path(Broken()), "progress-events.jsonl")


class TestReadFaultProbeUnreadable(_NeedsPermissions):
    def test_an_unopenable_log_is_UNREADABLE(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            log = _log(surface)
            log.append_event("stage_enter", "develop", run_id="r")
            with _unreadable(log.path):
                self.assertEqual(log.read_fault(), FAULT_UNREADABLE)


# ======================================================= 6 · NEGATIVES — nothing else moved
class TestNothingElseMoved(_NoPinnedSession):
    def test_marks_best_effort_posture_is_UNCHANGED(self):
        """Deliverable 5: `progress mark` keeps return-0-on-failure. It is OBSERVABILITY (a badge),
        not GATE EVIDENCE — do not unify it with record-review by symmetry."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "solo")
            rc, out = _cli(["progress", "mark", "review", "--path", d], d)
            self.assertEqual((rc, out), (0, "mokata progress: entered 'review' (run solo).\n"))
            with mock.patch("mokata.progress_events.ProgressLog.append_event",
                            side_effect=OSError("disk full")):
                rc, out = _cli(["progress", "mark", "develop", "--path", d], d)
            self.assertEqual(rc, 0, "mark's best-effort posture changed — it must not")
            self.assertIn("could not record 'develop'", out)
            self.assertIn("continuing", out)

    def test_the_reason_mark_keeps_its_posture_is_STATED_in_the_code(self):
        import mokata.cli_commands.runviews as RV
        with open(RV.__file__, encoding="utf-8") as fh:
            text = fh.read()
        idx = text.index("could not record '{args.stage}'")
        near = text[max(0, idx - 1200):idx]
        self.assertIn("REVIEW-FIX.R4", near)
        self.assertIn("OBSERVABILITY", near)
        self.assertIn("GATE EVIDENCE", near.replace("\n        # ", " "))
        self.assertIn("Do not \"fix\" this by symmetry.", near)

    def test_the_passing_and_failing_gate_answers_are_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "solo")
            _cli(["progress", "record-review", "--passed", "--independent", "--path", d], d)
            self.assertEqual(_cli(["progress", "review-status", "--path", d], d),
                             (0, "review passed (independent ✓)\n"))
            _cli(["progress", "record-review", "--passed", "--path", d], d)
            self.assertEqual(_cli(["progress", "review-status", "--path", d], d),
                             (0, "review passed (inline — not independent)\n"))
            _cli(["progress", "record-review", "--failed", "--path", d], d)
            rc, out = _cli(["progress", "review-status", "--path", d], d)
            self.assertEqual(rc, 2)
            self.assertIn("review failed — findings are unresolved", out)

    def test_the_run_less_refusal_is_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "runA")
            _persist_run(d, "runB")
            rc, out = _cli(["progress", "review-status", "--path", d], d)
            self.assertEqual(rc, 2)
            self.assertIn("no run to attribute it to", out)
            self.assertTrue(TR.review_status(path=d)["readable"])   # nothing was unreadable

    def test_a_status_read_still_writes_nothing_even_on_the_fault_path(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo")
            path = _corrupt(surface)
            before = os.stat(path).st_size
            for _ in range(3):
                _cli(["progress", "review-status", "--path", d], d)
                TR.review_status(path=d, response_format="detailed")
            self.assertEqual(os.stat(path).st_size, before)

    def test_the_gate_gained_readable_without_disturbing_any_other_field(self):
        from mokata.progress_events import ReviewGate
        g = ReviewGate(present=True, passed=True, independent=True, blocks=False,
                       message="m")                     # a pre-R4 construction, unchanged
        self.assertTrue(g.readable)                     # …defaults to "we read it fine"
        self.assertEqual((g.present, g.passed, g.independent, g.blocks, g.unblock),
                         (True, True, True, False, ""))

    def test_the_record_tool_gained_no_parameters(self):
        import inspect
        self.assertEqual(set(inspect.signature(TR.review_record).parameters),
                         {"path", "passed", "failed", "independent", "findings", "run"})
        self.assertEqual(set(inspect.signature(TR.review_status).parameters),
                         {"path", "run", "response_format"})

    def test_the_tool_registry_is_unchanged(self):
        from mokata.mcp import registry as REG
        # R4 itself added no tool; the total moved 58 -> 59 at WT-LIST (`worktree_list`) and
        # 59 -> 61 at M-4/R5 (`consolidate_proposals` + `consolidate`), which is what this now
        # pins. R4's own "nothing moved" property is the signature checks above.
        self.assertEqual(len(REG.TOOLS), 61)


# ======================================================= 7 · the callers state the new contract
class TestCallersSweep(unittest.TestCase):
    _REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _read(self, *parts):
        with open(os.path.join(self._REPO, *parts), encoding="utf-8") as fh:
            return fh.read()

    def test_the_review_skill_no_longer_calls_recording_best_effort(self):
        for rel in (("src", "mokata", "templates", "commands", "review.md"),
                    ("src", "mokata", "skills", "review", "SKILL.md")):
            with self.subTest(file=rel[-2]):
                text = self._read(*rel)
                self.assertNotIn("best-effort (if it fails, keep going)", text)
                self.assertIn("exits NON-ZERO", text)
                self.assertIn("do not read that as recorded", text)

    def test_the_ship_skill_handles_the_read_error_answer(self):
        for rel in (("src", "mokata", "templates", "commands", "ship.md"),
                    ("src", "mokata", "skills", "ship", "SKILL.md")):
            with self.subTest(file=rel[-2]):
                text = self._read(*rel)
                self.assertIn("could not be READ", text)
                self.assertIn("do NOT route the human to review", text)
                self.assertIn("the fault is the LOG", text)

    def test_the_shipped_skills_still_match_their_templates(self):
        """The drift guard this sweep must not break — regenerate, never hand-edit."""
        from pathlib import Path
        from mokata.agent_skills import skill_markdown
        templates = Path(self._REPO) / "src" / "mokata" / "templates" / "commands"
        for name in ("review", "ship"):
            with self.subTest(skill=name):
                shipped = self._read("src", "mokata", "skills", name, "SKILL.md")
                self.assertEqual(shipped, skill_markdown(name, templates))


if __name__ == "__main__":
    unittest.main()
