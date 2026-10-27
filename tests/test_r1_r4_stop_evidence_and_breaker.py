"""0.0.26 Lane R — R1 (Stop/SubagentStop evidence gate, fail-closed) + R4 (3-fix breaker, 5-round
review loop). Business-level: a simulated Claude Code session drives the REAL hook mains with the
payloads Claude Code sends, against a real initialised mokata repo with a bound run.

Exit criterion 12 (doc 113 §6): "Stop gate fails closed; breaker trips at 3" — each has a firing
test here (`test_R1_a_hook_exception_fails_closed`, `test_R4_three_identical_failing_fixes_trip_
the_breaker`), plus round 6 of review escalating (`test_R4_round_6_of_review_escalates`).
"""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)
from _plan import (call_planned, commit_planned, emit_planned,  # noqa: F401,E402  (R2)
                   planned, sealed, with_tasks)

from mokata import gate_hook
from mokata import hook_cli
from mokata import loop_guard as LG
from mokata import progress_events as PE
from mokata import run_intent as RI
from mokata import stop_gate as SG
from mokata import tdd_state
from mokata import test_runs as TR
from mokata.cli import main as cli_main
from mokata.config import Surface
from mokata.govern.ledger import AuditLedger
from mokata.govern.resume import PipelineCheckpoint
from mokata.init import init_repo
from mokata.quiet_spawn import hidden
from mokata.tdd_state import state_dir

DONE = "Implemented the parser change. All tests pass — the feature is done."
HANDBACK_UNTESTED = ("Changed the parser.\n\nHANDBACK\nstatus: done\nsummary: parser fixed\n"
                     "files: src/parser.py\ntests: not run\nopen: none\n")
HANDBACK_TESTED = ("Changed the parser.\n\nHANDBACK\nstatus: done\nsummary: parser fixed\n"
                   "files: src/parser.py\ntests: python -m unittest test_parser → 4 passed, 0 failed"
                   "\nopen: none\n")


def _silent(_):
    pass


def _hook(fn, payload):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(hook_cli, "_read_stdin_bounded", return_value=json.dumps(payload)), \
            redirect_stdout(out), redirect_stderr(err):
        code = fn([])
    return code, out.getvalue(), err.getvalue()


def _answer(out):
    return json.loads(out) if out.strip() else None


def _cli(*argv):
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(io.StringIO()):
        code = cli_main(list(argv))
    return code, out.getvalue()


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


class _Run(unittest.TestCase):
    """A mokata repo with run `r1` (spec emitted: AC-1, AC-2) bound to Claude session `S1`."""

    ACS = ("AC-1", "AC-2")

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.d = os.path.realpath(self._t.name)
        init_repo(root=self.d, profile="standard", assume_yes=True, out=_silent)
        surface = Surface.load(self.d)
        PipelineCheckpoint(surface.state, "r1").ensure_registered()
        surface.state.write("emitted_spec__r1", sealed({
            "title": "parser", "criteria": [{"id": a, "text": f"behaviour {a}"} for a in self.ACS],
            "approach": "a", "domains": []}))
        RI.bind_registered(self.d, "S1", "r1")
        self.surface = Surface.load(self.d)

    def tearDown(self):
        self._t.cleanup()

    def tests_for(self, *acs, passed=True):
        """RECORD a test run proving `acs` (each AC → its own test, recorded GREEN when passed) —
        exactly what `mokata progress test-run --passed --test … --ac AC=…` writes."""
        tests = [f"test_{a.replace('-', '_').lower()}" for a in acs]
        TR.record_test_run(self.d, "r1", passed=passed, command="python -m unittest",
                           tests=tests, ac_map={a: [t] for a, t in zip(acs, tests)})

    def stop(self, message=DONE, active=False, sid="S1"):
        return _hook(hook_cli.stop_gate_main,
                     {"cwd": self.d, "session_id": sid, "hook_event_name": "Stop",
                      "stop_hook_active": active, "last_assistant_message": message})

    def kinds(self):
        return [e.get("kind") for e in
                AuditLedger.from_mokata_dir(os.path.join(self.d, ".mokata")).entries()]


# ======================================================================================
# R1 — the Stop evidence gate
# ======================================================================================

class R1StopEvidenceGate(_Run):

    def test_R1_a_done_stop_with_an_untested_AC_is_blocked_with_the_named_reason(self):
        self.tests_for("AC-1")
        code, out, _ = self.stop()
        self.assertEqual(0, code)
        answer = _answer(out)
        self.assertEqual("block", answer["decision"])
        self.assertIn("stop-evidence", answer["reason"])
        self.assertIn("no test is recorded for acceptance criteria AC-2", answer["reason"])
        self.assertNotIn("AC-1", answer["reason"].split("criteria", 1)[1].split(".")[0])
        self.assertIn("mokata gate override stop-evidence", answer["reason"])

    def test_R1_a_done_stop_the_record_backs_is_let_through_silently(self):
        self.tests_for("AC-1", "AC-2")
        self.assertIsNone(_answer(self.stop()[1]))

    def test_R1_a_test_still_RED_blocks_a_done_stop(self):
        self.tests_for("AC-1", "AC-2")
        tdd_state.record(self.surface.state, "r1", red={"test_edge_case"})
        answer = _answer(self.stop()[1])
        self.assertEqual("block", answer["decision"])
        self.assertIn("still RED", answer["reason"])
        self.assertIn("test_edge_case", answer["reason"])
        tdd_state.record(self.surface.state, "r1", green={"test_edge_case"})
        self.assertIsNone(_answer(self.stop()[1]))

    def test_R1_only_a_completion_claim_is_policed(self):
        self.tests_for("AC-1")
        for msg in ("Should I write the test for AC-2 next?",
                    "I'm blocked: the fixture needs a database. What's left to be done is AC-2.",
                    ""):
            with self.subTest(msg=msg):
                self.assertIsNone(_answer(self.stop(message=msg)[1]))

    def test_R1_a_session_not_attached_to_the_run_is_never_policed(self):
        self.tests_for("AC-1")
        self.assertIsNone(_answer(self.stop(sid="S-other")[1]))

    def test_R1_outside_develop_nothing_is_policed(self):
        self.tests_for("AC-1")
        self.surface.state.delete("emitted_spec__r1")
        self.assertIsNone(_answer(self.stop()[1]))

    def test_R1_a_hook_exception_fails_closed(self):
        self.tests_for("AC-1", "AC-2")
        with mock.patch.object(SG, "decide", side_effect=RuntimeError("boom")):
            first = _answer(self.stop()[1])
            again = _answer(self.stop(active=True)[1])
        self.assertEqual("block", first["decision"], "a crashing check let an unverified stop out")
        self.assertIn("RuntimeError: boom", first["reason"])
        # …but never a trap: Claude Code's own re-entry flag lets it through, loudly.
        self.assertNotIn("decision", again)
        self.assertIn("UNVERIFIED", again["systemMessage"])

    def test_R1_an_unreadable_record_fails_closed(self):
        self.tests_for("AC-1", "AC-2")
        _write(os.path.join(state_dir(self.d), "tdd_phase__r1.json"), "{ torn")
        answer = _answer(self.stop()[1])
        self.assertEqual("block", answer["decision"])
        self.assertIn("could not verify", answer["reason"])

    def test_R1_a_mapped_AC_whose_test_never_passed_is_unproven(self):
        TR.seed_ac_map(self.d, "r1", [type("T", (), {"name": "test_a", "ac_ids": ["AC-1"]})(),
                                      type("T", (), {"name": "test_b", "ac_ids": ["AC-2"]})()])
        self.tests_for("AC-1")
        answer = _answer(self.stop()[1])
        self.assertIn("no test recorded PASSING for acceptance criteria AC-2", answer["reason"])
        TR.record_test_run(self.d, "r1", passed=True, command="pytest", tests=["test_b"])
        self.assertIsNone(_answer(self.stop()[1]))

    def test_R1_a_suite_run_older_than_the_last_write_is_not_evidence(self):
        import time
        from mokata.working_set import record_touched
        self.tests_for("AC-1", "AC-2")
        record_touched(self.d, "r1", "src/parser.py", at=time.time() + 5)
        answer = _answer(self.stop()[1])
        self.assertEqual("block", answer["decision"])
        self.assertIn("OLDER than the run's last write", answer["reason"])
        TR.record_test_run(self.d, "r1", passed=True, command="pytest", at=time.time() + 10)
        self.assertIsNone(_answer(self.stop()[1]))

    def test_R1_a_failed_last_run_and_no_run_at_all_both_block(self):
        TR.seed_ac_map(self.d, "r1", [])
        self.assertIn("no test run is recorded", _answer(self.stop()[1])["reason"])
        self.tests_for("AC-1", "AC-2")
        TR.record_test_run(self.d, "r1", passed=False, command="pytest")
        self.assertIn("last recorded test run FAILED", _answer(self.stop()[1])["reason"])

    def test_R1_in_review_a_missing_review_verdict_blocks(self):
        self.tests_for("AC-1", "AC-2")
        PE.ProgressLog.from_surface(self.surface).append_event(PE.STAGE_ENTER, "review",
                                                               run_id="r1")
        answer = _answer(self.stop()[1])
        self.assertIn("no review verdict is recorded", answer["reason"])
        PE.record_review_verdict(self.surface, passed=True, independent=False, blocking=0,
                                 run_id="r1")
        self.assertIsNone(_answer(self.stop()[1]))

    def test_R1_the_emit_seeds_the_AC_map_the_stop_gate_reads(self):
        """Both emit surfaces call `seed_ac_map` once completeness passes — pinned at the source,
        so the planned map cannot silently stop being recorded."""
        import inspect
        from mokata.engine import emit
        from mokata.mcp import tools_spec
        self.assertIn("seed_ac_map(_repo_root(store), run_id, tests)",
                      inspect.getsource(emit.spec_commit))
        self.assertIn("spec_commit(store, spec, run_id=run_id, tests=tests)",
                      inspect.getsource(emit))
        self.assertIn("spec_commit(store, spec, run_id=run_id, tests=test_refs)",
                      inspect.getsource(tools_spec))

    def test_R1_the_spec_commit_lands_the_planned_map(self):
        from mokata.engine.emit import spec_commit
        from mokata.engine.spec import AcceptanceCriterion, Spec, TestRef
        spec = Spec("parser", [AcceptanceCriterion("AC-1", "x"), AcceptanceCriterion("AC-2", "y")])
        spec_commit(self.surface.state, with_tasks(spec), run_id="r1",
                    tests=[TestRef("test_a", ["AC-1"]), TestRef("test_b", ["AC-2"])])
        self.assertEqual({"AC-1": ["test_a"], "AC-2": ["test_b"]},
                         TR.read_test_record(self.d, "r1")["planned"])

    def test_R1_the_cli_records_a_test_run(self):
        code, out = _cli("progress", "test-run", "--passed", "--command", "pytest -q",
                         "--test", "test_ac_1", "--ac", "AC-1=test_ac_1", "--ac", "AC-2=test_ac_1",
                         "--path", self.d, "--run", "r1")
        self.assertEqual(0, code, out)
        self.assertIn("PASSED recorded", out)
        self.assertIsNone(_answer(self.stop()[1]))
        self.assertEqual(1, _cli("progress", "test-run", "--passed", "--ac", "AC-1",
                                 "--path", self.d, "--run", "r1")[0])

    def test_R1_the_stop_path_never_walks_the_repo(self):
        """Recorded state only — the cost cannot grow with the repository (Jas: multi-million-line
        repos). A repo walk on the stop path is a red, not a slow pass."""
        self.tests_for("AC-1", "AC-2")
        # (the run resolver's bounded scandir of the STATE dir is not a repo walk and stays)
        with mock.patch("os.walk", side_effect=AssertionError("the stop path walked the repo")):
            self.assertIsNone(_answer(self.stop()[1]))
            self.tests_for("AC-1", passed=False)
            self.assertEqual("block", _answer(self.stop()[1])["decision"])

    def test_R1_never_a_trap_the_fourth_consecutive_stop_is_released_loudly_and_ledgered(self):
        self.tests_for("AC-1")
        seen = [_answer(self.stop(active=i > 0)[1]) for i in range(SG.MAX_STOP_BLOCKS + 1)]
        self.assertEqual(["block"] * SG.MAX_STOP_BLOCKS,
                         [a.get("decision") for a in seen[:SG.MAX_STOP_BLOCKS]])
        self.assertIn("(3/3)", seen[2]["reason"])
        released = seen[-1]
        self.assertNotIn("decision", released)
        self.assertIn("UNVERIFIED after 3 blocks", released["systemMessage"])
        self.assertIn("AC-2", released["systemMessage"])
        self.assertIn(SG.LEDGER_KIND, self.kinds())
        # a FRESH stop (the human's next turn) starts a new streak — blocked again
        self.assertEqual("block", _answer(self.stop(active=False)[1])["decision"])

    def test_R1_a_block_is_an_in_loop_catch_on_the_run(self):
        self.tests_for("AC-1")
        self.stop()
        blocks = [e for e in PE.ProgressLog.from_surface(self.surface).read_events(tail=None)
                  if e.get("type") == PE.GATE_BLOCK]
        self.assertEqual(["stop-evidence"], [e["data"]["gate"] for e in blocks])
        self.assertEqual({"r1"}, {e["run_id"] for e in blocks})

    def test_R1_the_human_override_is_the_escape_hatch(self):
        self.tests_for("AC-1")
        code, out = _cli("gate", "override", "stop-evidence", "--reason", "spike, no tests",
                         "--yes", "--path", self.d, "--run", "r1")
        self.assertEqual(0, code, out)
        self.assertIsNone(_answer(self.stop()[1]))
        self.assertIn("gate_override", self.kinds())

    def test_R1_the_stop_gate_is_wired_synchronously_beside_the_deferred_harvest(self):
        from mokata.harness_setup import _EXPECTED_HOOK_WIRING
        from mokata_emit import DEFERRED
        here = os.path.dirname(os.path.abspath(hook_cli.__file__))
        with open(os.path.join(here, "hooks", "hooks.json"), encoding="utf-8") as fh:
            stop = json.load(fh)["hooks"]["Stop"]
        commands = [h["command"].split()[1] for b in stop for h in b["hooks"]]
        self.assertEqual(["stop", "stop-gate"], commands)
        self.assertIn({"script": "stop_gate.py", "matcher": None}, _EXPECTED_HOOK_WIRING["Stop"])
        self.assertNotIn("stop-gate", DEFERRED, "a deferred hook cannot block a stop")
        # the gate ids are literals in the hook module (import surface) — pinned to their owners
        self.assertEqual((SG.GATE_STOP_EVIDENCE, LG.GATE_FIX_BREAKER, LG.GATE_REVIEW_LOOP),
                         gate_hook.LOOP_GATES)
        self.assertTrue(set(gate_hook.LOOP_GATES) <= set(gate_hook.OVERRIDABLE))

    def test_R1_the_claim_path_stays_off_the_engine_and_progress_imports(self):
        """HOOK-BUDGET: the Stop gate runs synchronously on the harness's clock. Its claim path
        (resolve, read the recorded state) must not load `mokata.engine` (~90 ms: the whole
        pipeline package) or `mokata.progress` (~50 ms) — measured in the R14 report."""
        import subprocess
        import sys
        self.tests_for("AC-1")
        src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
        code = ("import sys, json; from mokata.stop_gate import decide; "
                f"d = decide({self.d!r}, {{'session_id': 'S1', 'last_assistant_message': "
                f"'All tests pass.'}}); print(json.dumps([d.block, "
                "'mokata.engine' in sys.modules, 'mokata.progress' in sys.modules]))")
        out = subprocess.run([sys.executable, "-c", code], stdin=subprocess.DEVNULL,
                             capture_output=True, text=True,
                             env=dict(os.environ, PYTHONPATH=src), timeout=120, **hidden())
        self.assertEqual([True, False, False], json.loads(out.stdout.strip() or "null"),
                         out.stderr)

    def test_R1_claims_done_reads_claims_not_words(self):
        for text, want in (("All tests pass.", True), ("✓ develop done — 2 ACs green.", True),
                           ("The implementation is complete.", True),
                           ("Ready for review.", True), ("I have implemented AC-1.", True),
                           ("Is everything done?", False), ("What remains to be done: AC-2", False),
                           ("Next I'll write the failing test.", False), (None, False)):
            with self.subTest(text=text):
                self.assertIs(want, SG.claims_done(text))


class R1SubagentStopEvidence(_Run):

    def _contract(self, aid="A1"):
        _hook(hook_cli.subagent_start_main, {"cwd": self.d, "session_id": "S1", "agent_id": aid,
                                             "agent_type": "general-purpose",
                                             "hook_event_name": "SubagentStart"})

    def _sstop(self, message, active=False, aid="A1"):
        return _hook(hook_cli.subagent_stop_main,
                     {"cwd": self.d, "session_id": "S1", "agent_id": aid,
                      "stop_hook_active": active, "last_assistant_message": message,
                      "hook_event_name": "SubagentStop"})

    def test_R1_a_subagent_done_with_implementation_and_no_test_is_sent_back(self):
        self._contract()
        answer = _answer(self._sstop(HANDBACK_UNTESTED)[1])
        self.assertEqual("block", answer["decision"])
        self.assertIn("stop-evidence", answer["reason"])
        self.assertIn("src/parser.py", answer["reason"])
        # on re-entry it is let go, and the miss is ledgered — never a loop
        self.assertIsNone(_answer(self._sstop(HANDBACK_UNTESTED, active=True)[1]))
        rows = [e for e in AuditLedger.from_mokata_dir(os.path.join(self.d, ".mokata")).entries()
                if e.get("decision") == "done-untested"]
        self.assertEqual(1, len(rows))

    def test_R1_a_subagent_done_with_a_passing_test_is_let_go(self):
        self._contract()
        self.assertIsNone(_answer(self._sstop(HANDBACK_TESTED)[1]))

    def test_R1_a_subagent_whose_tests_failed_cannot_hand_back_done(self):
        self._contract()
        failed = HANDBACK_TESTED.replace("4 passed, 0 failed", "3 passed, 1 failed")
        self.assertEqual("block", _answer(self._sstop(failed)[1])["decision"])

    def test_R1_a_subagent_stop_crash_fails_closed_once(self):
        self._contract()
        with mock.patch("mokata.subagent_contract.finish_contract", side_effect=OSError("disk")):
            first = _answer(self._sstop(HANDBACK_TESTED)[1])
            again = _answer(self._sstop(HANDBACK_TESTED, active=True)[1])
        self.assertEqual("block", first["decision"])
        self.assertIn("OSError: disk", first["reason"])
        self.assertIn("UNVERIFIED", again["systemMessage"])


# ======================================================================================
# R4 — the 3-fix breaker and the 5-round review loop
# ======================================================================================

class R4FixBreaker(_Run):

    def _impl_write(self):
        return _hook(hook_cli.gate_guard_main,
                     {"cwd": self.d, "session_id": "S1", "tool_name": "Write",
                      "hook_event_name": "PreToolUse",
                      "tool_input": {"file_path": os.path.join(self.d, "src", "parser.py"),
                                     "content": "x = 1\n"}})

    def setUp(self):
        super().setUp()
        tdd_state.record(self.surface.state, "r1", red={"test_parser"})   # the TDD licence

    def test_R4_three_identical_failing_fixes_trip_the_breaker(self):
        sigs = ("FAILED tests/test_parser.py::test_x - AssertionError at 0x7f3a2c",
                "failed   tests/test_parser.py::test_x - assertionerror at 0x55e1d0",
                "FAILED tests/test_parser.py::test_x - AssertionError at 0x1")
        for i, sig in enumerate(sigs[:2]):
            row = LG.record_fix_attempt(self.d, "r1", sig, passed=False)
            self.assertEqual((i + 1, False), (row["attempts"], row["tripped"]))
            self.assertEqual(0, self._impl_write()[0], "the breaker tripped before three")
        row = LG.record_fix_attempt(self.d, "r1", sigs[2], passed=False)
        self.assertEqual((3, True), (row["attempts"], row["tripped"]))
        code, _, err = self._impl_write()
        self.assertEqual(2, code, "the fourth blind fix was let through")
        self.assertIn("fix-breaker", err)
        self.assertIn("/mokata:debug", err)

    def test_R4_only_a_NEW_APPROACH_clears_a_tripped_breaker(self):
        """Research ruling: at 3 the writes stay blocked until a NEW approach is approved through
        approach-approval — a `--passed` does not clear a tripped breaker; other failures never add
        up; the trip is ledgered."""
        self.surface.state.write("approved_approach__r1", {"approach": {"name": "first"}})
        for n in range(2):
            LG.record_fix_attempt(self.d, "r1", f"other failure {n}", passed=False)
            LG.record_fix_attempt(self.d, "r1", "the failure", passed=False)
        self.assertEqual([], LG.tripped_failures(self.d, "r1"))
        LG.record_fix_attempt(self.d, "r1", "the failure", passed=True)     # untripped: clears
        for _ in range(3):
            LG.record_fix_attempt(self.d, "r1", "the failure", passed=False)
        self.assertEqual([("the failure", 3)], LG.tripped_failures(self.d, "r1"))
        self.assertIn(LG.LEDGER_KIND, self.kinds())
        LG.record_fix_attempt(self.d, "r1", "the failure", passed=True)     # tripped: stays
        self.assertEqual(2, self._impl_write()[0])
        self.assertIn("NEW approach", self._impl_write()[2])
        self.surface.state.write("approved_approach__r1", {"approach": {"name": "second"}})
        self.assertEqual([], LG.tripped_failures(self.d, "r1"))
        self.assertEqual(0, self._impl_write()[0])

    def test_R4_the_cli_records_and_says_escalate_at_three(self):
        args = ("progress", "fix-attempt", "--failure", "test_parser fails", "--failed",
                "--path", self.d, "--run", "r1")
        self.assertEqual(0, _cli(*args)[0])
        self.assertEqual(0, _cli(*args)[0])
        code, out = _cli(*args)
        self.assertEqual(3, code)
        self.assertIn("BREAKER TRIPPED", out)
        self.assertEqual(1, _cli("progress", "fix-attempt", "--failure", "  ", "--failed",
                                 "--path", self.d, "--run", "r1")[0])

    def test_R4_the_human_override_lifts_the_breaker(self):
        for _ in range(3):
            LG.record_fix_attempt(self.d, "r1", "the failure", passed=False)
        self.assertEqual(2, self._impl_write()[0])
        _cli("gate", "override", "fix-breaker", "--reason", "I'll take it", "--yes",
             "--path", self.d, "--run", "r1")
        self.assertEqual(0, self._impl_write()[0])

    def test_R4_a_tripped_breaker_lets_the_session_stop_to_escalate(self):
        self.tests_for("AC-1")                          # AC-2 untested: would block a "done"
        self.assertEqual("block", _answer(self.stop()[1])["decision"])
        for _ in range(3):
            LG.record_fix_attempt(self.d, "r1", "the failure", passed=False)
        self.assertIsNone(_answer(self.stop()[1]))


class R4ReviewLoopCap(_Run):

    def _verdict(self, passed):
        return PE.record_review_verdict(self.surface, passed=passed, independent=False,
                                        blocking=0 if passed else 1, run_id="r1")

    def test_R4_round_6_of_review_escalates(self):
        for _ in range(LG.REVIEW_ROUND_CAP):
            self._verdict(False)
        with self.assertRaises(PE.ReviewLoopCapped) as ctx:
            self._verdict(True)
        self.assertIn("round 6 escalates", str(ctx.exception))
        # the CLI says ESCALATE with its own exit code; the MCP tool answers status "escalate"
        code, out = _cli("progress", "record-review", "--failed", "--blocking", "1",
                         "--path", self.d, "--run", "r1")
        self.assertEqual(3, code)
        self.assertIn("ESCALATE", out)
        from mokata.mcp.tools_read import review_record
        res = review_record(path=self.d, passed=True, independent=False, blocking=0,
                                 run="r1")
        self.assertEqual(("escalate", False), (res["status"], res["recorded"]))
        verdicts = [e for e in PE.ProgressLog.from_surface(self.surface).read_events(tail=None)
                    if e.get("type") == PE.REVIEW_VERDICT]
        self.assertEqual(LG.REVIEW_ROUND_CAP, len(verdicts), "round 6 was recorded")
        rulings = [e for e in AuditLedger.from_mokata_dir(os.path.join(self.d, ".mokata")).entries()
                   if e.get("kind") == LG.LEDGER_KIND]
        self.assertEqual({"review-loop-capped"}, {e.get("decision") for e in rulings})

    def test_R4_a_pass_resets_the_round_count(self):
        for _ in range(LG.REVIEW_ROUND_CAP - 1):
            self._verdict(False)
        self._verdict(True)
        for _ in range(LG.REVIEW_ROUND_CAP):
            self._verdict(False)
        with self.assertRaises(PE.ReviewLoopCapped):
            self._verdict(False)

    def test_R4_the_human_override_continues_the_loop(self):
        for _ in range(LG.REVIEW_ROUND_CAP):
            self._verdict(False)
        _cli("gate", "override", "review-loop", "--reason", "one more round", "--yes",
             "--path", self.d, "--run", "r1")
        self.assertTrue(self._verdict(True)["data"]["passed"])

    def test_R4_the_skills_say_when_to_stop(self):
        from mokata.skills import get_skill, render_skill
        develop = render_skill(get_skill("develop"))
        debug = render_skill(get_skill("debug"))
        review = render_skill(get_skill("review"))
        for text in (develop, debug):
            self.assertIn("mokata progress fix-attempt", text)
            self.assertIn("THREE failed fix attempts", text)
        self.assertIn("FIVE", review)
        self.assertIn("review-loop", review)
        self.assertIn("ROUND 4, raise the reviewer one model tier", review)
        self.assertIn("DEFER Minor findings", review)
        self.assertIn("mokata progress test-run", develop)
        self.assertIn("NEW approach", develop)


if __name__ == "__main__":
    unittest.main()
