"""0.0.22 stage D4 — `subagent-contract`: HP.S3 + H-3 (+ SUBAGENT-REVIEW-INSTRUCTED observed).

HP.S3 — a subagent in a pipeline run has a CONTRACT, enforced on its own tool calls: a read-only
subagent cannot write, and a subagent cannot start a subagent past `max_recursion_depth`. Every
refusal is ledgered `subagent_contract_unhonored`.
H-3 — the subagent hands back a `HandbackReport`; one that returns none is sent back once, then let
go and ledgered. `cap_summary` keeps head + tail and is the fallback only.
The review that says it was independent is checked against a subagent seen to finish.
Parallel runs: each session's subagents carry that session's own run.
"""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import hook_cli
from mokata import progress_events as PE
from mokata import run_intent as RI
from mokata import subagent_contract as SC
from mokata.cli import main
from mokata.config import Surface
from mokata.govern import cap_summary
from mokata.govern.ledger import AuditLedger
from mokata.govern.resume import PipelineCheckpoint
from mokata.init import init_repo
from mokata.tdd_state import state_dir

GOOD = ("I checked the parser.\n\nHANDBACK\nstatus: done\nsummary: the parser is fine\n"
        "files: none\ntests: python -m unittest test_parser → 4 passed\nopen: none\n")


def _silent(_):
    pass


def _repo(d, *runs, subagents=None):
    init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
    if subagents is not None:
        p = os.path.join(d, ".mokata", "manifest.json")
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
        data.setdefault("settings", {})["subagents"] = subagents
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    surface = Surface.load(d)
    for rid in runs:
        PipelineCheckpoint(surface.state, rid).ensure_registered()
    return Surface.load(d)


def _hook(fn, payload):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(hook_cli, "_read_stdin_bounded", return_value=json.dumps(payload)), \
            redirect_stdout(out), redirect_stderr(err):
        code = fn([])
    return code, out.getvalue(), err.getvalue()


def _start(d, sid, aid, atype):
    return _hook(hook_cli.subagent_start_main,
                 {"cwd": d, "session_id": sid, "agent_id": aid, "agent_type": atype,
                  "hook_event_name": "SubagentStart"})


def _tool(d, sid, tool, tool_input, aid=None):
    payload = {"cwd": d, "session_id": sid, "tool_name": tool, "tool_input": tool_input}
    if aid:
        payload["agent_id"] = aid
    return _hook(hook_cli.gate_guard_main, payload)


def _stop(d, sid, aid, message, active=False):
    return _hook(hook_cli.subagent_stop_main,
                 {"cwd": d, "session_id": sid, "agent_id": aid, "stop_hook_active": active,
                  "last_assistant_message": message, "hook_event_name": "SubagentStop"})


def _kinds(d):
    return [e.get("kind") for e in AuditLedger.from_mokata_dir(os.path.join(d, ".mokata")).entries()]


class H3TheHandbackIsAReportNotATranscript(unittest.TestCase):

    def test_a_well_formed_report_is_read_as_fields(self):
        report, why = SC.parse_handback(GOOD)
        self.assertEqual("", why)
        self.assertEqual(("done", "the parser is fine", ()), (report.status, report.summary,
                                                              report.files))
        self.assertIn("4 passed", report.tests)

    def test_a_fenced_report_and_listed_files_are_read(self):
        text = "```\nHANDBACK\n- status: partial\n- summary: half done\n- files: a.py, b.py\n```"
        report, _ = SC.parse_handback(text)
        self.assertEqual(("a.py", "b.py"), report.files)

    def test_what_cannot_be_read_is_refused_with_its_reason(self):
        for text, why in (("all good, done!", "no `HANDBACK`"),
                          ("HANDBACK\nstatus: finished\nsummary: x", "status must be one of"),
                          ("HANDBACK\nstatus: done", "summary is missing"),
                          ("HANDBACK\nstatus: blocked\nsummary: stuck\nopen: none",
                           "must say what is open"),
                          ("", "empty")):
            with self.subTest(text=text):
                report, got = SC.parse_handback(text)
                self.assertIsNone(report)
                self.assertIn(why, got)

    def test_cap_summary_keeps_the_head_AND_the_tail(self):
        text = "HEAD " + "x" * 4000 + " CONCLUSION"
        h = cap_summary(text, cap_tokens=50)
        self.assertTrue(h.capped)
        self.assertTrue(h.summary.startswith("HEAD"))
        self.assertTrue(h.summary.endswith("CONCLUSION"), "the conclusion was cut off")
        self.assertLessEqual(h.tokens, 50)
        self.assertFalse(cap_summary(text, cap_tokens=50, tail=False).summary.endswith("CONCLUSION"))

    def test_the_orchestrator_hands_back_the_report_and_falls_back_to_the_cap(self):
        from mokata.execmode import orchestrator as O
        from mokata.execmode.tasks import Task, TaskResult
        task = Task("t1", "do it")
        for output, expect in ((GOOD, "done · no files"), ("plain " * 400, "[handback capped]")):
            with self.subTest(expect=expect):
                res = TaskResult(task_id="t1", ok=True, summary="", output=output)
                with mock.patch.object(O, "_execute", return_value=res):
                    got = O._run_one(task, mock.Mock(isolation=False), None, None, None,
                                     O.TokenTracker(), handback_cap=40)
                self.assertIn(expect, got.summary)


class HpS3TheContractIsEnforcedOnTheSubagentsOwnCalls(unittest.TestCase):

    def setUp(self):
        os.environ.pop("MOKATA_SESSION_ID", None)

    def test_a_pipeline_session_contracts_its_subagent_and_tells_it(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            RI.bind_registered(d, "S1", "rA")
            code, out, _ = _start(d, "S1", "a1", "Explore")
            self.assertEqual(0, code)
            ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
            self.assertIn("READ-ONLY", ctx)
            self.assertIn("HANDBACK", ctx)
            self.assertEqual("rA", SC.read_contract(d, "a1")["run_id"])

    def test_a_session_in_no_pipeline_sees_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            code, out, _ = _start(d, "S9", "z1", "Explore")
            self.assertEqual((0, ""), (code, out))
            self.assertIsNone(SC.read_contract(d, "z1"))
            self.assertEqual((0, "", ""), _stop(d, "S9", "z1", "no report at all"))

    def test_a_read_only_subagent_cannot_write_and_the_refusal_is_ledgered(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            RI.bind_registered(d, "S1", "rA")
            _start(d, "S1", "a1", "Explore")
            _start(d, "S1", "g1", "general-purpose")
            target = {"file_path": os.path.join(d, "notes.md"), "content": "x"}
            code, _, err = _tool(d, "S1", "Write", target, aid="a1")
            self.assertEqual(hook_cli.BLOCK_EXIT, code)
            self.assertIn(f"BLOCKED [{SC.GATE_READ_ONLY}]", err)
            self.assertIn(SC.LEDGER_KIND, _kinds(d))
            self.assertEqual(0, _tool(d, "S1", "Write", target, aid="g1")[0],
                             "a writer subagent is not read-only")
            self.assertEqual(hook_cli.BLOCK_EXIT,
                             _tool(d, "S1", "Bash", {"command": "sed -i s/a/b/ ./notes.md"},
                                   aid="a1")[0], "a shell write is a write")
            # ⚠ BOUNDED BY THE PARSER: a BARE operand (`notes.md`, no separator) is not one
            # `bash_write_targets` judges, so it passes here. That side door is D5's to close.
            self.assertEqual(0, _tool(d, "S1", "Bash", {"command": "ls -la"}, aid="a1")[0])

    def test_an_uncontracted_subagent_is_unpoliced(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            target = {"file_path": os.path.join(d, "notes.md"), "content": "x"}
            self.assertEqual(0, _tool(d, "S9", "Write", target, aid="never-started")[0])

    def test_depth_a_subagent_cannot_start_a_subagent(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            RI.bind_registered(d, "S1", "rA")
            _start(d, "S1", "g1", "general-purpose")
            code, _, err = _tool(d, "S1", "Agent", {"prompt": "go", "subagent_type": "Explore"},
                                 aid="g1")
            self.assertEqual(hook_cli.BLOCK_EXIT, code)
            self.assertIn(SC.GATE_DEPTH, err)
            self.assertEqual(0, _tool(d, "S1", "Agent", {"prompt": "go"})[0],
                             "the main conversation may start one")

    def test_depth_zero_forbids_subagents_in_a_run_only(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA", subagents={"max_recursion_depth": 0})
            RI.bind_registered(d, "S1", "rA")
            self.assertEqual(hook_cli.BLOCK_EXIT, _tool(d, "S1", "Task", {"prompt": "go"})[0])
            self.assertEqual(0, _tool(d, "S9", "Task", {"prompt": "go"})[0],
                             "a session in no run is never policed")

    def test_bad_settings_are_refused_with_the_reason_and_the_default_kept(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, subagents={"max_recursion_depth": 9, "read_only_types": "Explore",
                                "max_depht": 1})
            cfg = SC.subagent_settings(d)
            self.assertEqual((SC.DEFAULT_MAX_DEPTH, SC.DEFAULT_READ_ONLY_TYPES),
                             (cfg.max_recursion_depth, cfg.read_only_types))
            self.assertEqual(3, len(cfg.problems), cfg.problems)

    def test_the_hot_path_spawn_list_is_the_contracts(self):
        self.assertEqual(SC.SPAWN_TOOLS, hook_cli._SPAWN_TOOLS)


class H3TheStopReadsTheHandback(unittest.TestCase):

    def setUp(self):
        os.environ.pop("MOKATA_SESSION_ID", None)

    def test_no_report_sends_it_back_once_then_lets_go_and_ledgers(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            RI.bind_registered(d, "S1", "rA")
            _start(d, "S1", "g1", "general-purpose")
            code, out, _ = _stop(d, "S1", "g1", "all done!")
            self.assertEqual(0, code)
            decision = json.loads(out)
            self.assertEqual("block", decision["decision"])
            self.assertIn("HANDBACK", decision["reason"])
            code, out, _ = _stop(d, "S1", "g1", "still no report", active=True)
            self.assertEqual((0, ""), (code, out), "never held in a loop")
            self.assertIn(SC.LEDGER_KIND, _kinds(d))
            [e] = PE.subagents_finished(state_dir(d), "rA")
            self.assertIn("handback_missing", e["data"])

    def test_a_hook_wired_twice_records_one_finish(self):
        """0.0.22 live check — plugin hooks AND `mokata init`'s project hooks both fire, so one
        subagent's SubagentStop arrives twice; it is still one finished subagent."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            RI.bind_registered(d, "S1", "rA")
            _start(d, "S1", "g1", "general-purpose")
            report = "done.\n\nHANDBACK\nstatus: done\nsummary: listed files\nfiles: none\ntests: n/a\nopen: none"
            _stop(d, "S1", "g1", report)
            _stop(d, "S1", "g1", report)
            self.assertEqual(1, len(PE.subagents_finished(state_dir(d), "rA")))

    def test_a_harness_that_sends_no_final_message_is_never_looped(self):
        """0.0.22 E1 — an older Claude Code may omit `last_assistant_message`; mokata cannot ask
        for a report it has no way to read, so it records the gap and lets the subagent go."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            RI.bind_registered(d, "S1", "rA")
            _start(d, "S1", "g1", "general-purpose")
            code, out, _ = _hook(hook_cli.subagent_stop_main,
                                 {"cwd": d, "session_id": "S1", "agent_id": "g1",
                                  "hook_event_name": "SubagentStop"})
            self.assertEqual((0, ""), (code, out))
            [e] = PE.subagents_finished(state_dir(d), "rA")
            self.assertIn("sends no final message", e["data"]["handback_missing"])
            self.assertNotIn(SC.LEDGER_KIND, _kinds(d), "the harness's gap is not the subagent's")

    def test_a_good_report_is_recorded_against_the_run(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            RI.bind_registered(d, "S1", "rA")
            _start(d, "S1", "g1", "general-purpose")
            self.assertEqual((0, "", ""), _stop(d, "S1", "g1", GOOD))
            [e] = PE.subagents_finished(state_dir(d), "rA")
            self.assertEqual("done", e["data"]["handback"]["status"])

    def test_parallel_runs_each_count_their_own_subagents(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA", "rB")
            RI.bind_registered(d, "S1", "rA")
            RI.bind_registered(d, "S2", "rB")
            _start(d, "S1", "a1", "general-purpose")
            _start(d, "S2", "b1", "general-purpose")
            _stop(d, "S1", "a1", GOOD)
            _stop(d, "S2", "b1", GOOD)
            self.assertEqual(["a1"], [e["data"]["agent_id"]
                                      for e in PE.subagents_finished(state_dir(d), "rA")])
            self.assertEqual(["b1"], [e["data"]["agent_id"]
                                      for e in PE.subagents_finished(state_dir(d), "rB")])


class TheIndependentReviewIsObservedNotClaimed(unittest.TestCase):

    def setUp(self):
        os.environ.pop("MOKATA_SESSION_ID", None)

    def test_an_unobserved_claim_is_credited_as_inline_not_blocked(self):
        """The D4 ruling: blocking an unobserved claim would be stricter than an honest inline
        review (which ships); crediting it is the gap. So it ships, AS INLINE, and says why."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, "rA")
            PE.record_review_verdict(surface, passed=True, independent=True, blocking=0,
                                     run_id="rA")
            gate = PE.ship_review_gate(surface, run_id="rA")
            self.assertFalse(gate.blocks)
            self.assertIs(False, gate.observed)
            self.assertIs(False, gate.independent, "an unobserved claim is not credited")
            self.assertEqual(PE.UNOBSERVED_INDEPENDENT_MESSAGE, gate.message)
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(0, main(["progress", "review-status", "--run", "rA",
                                          "--path", d]))
            self.assertIn("no subagent of this run was observed", buf.getvalue())

    def test_a_subagent_that_finished_AFTER_the_verdict_does_not_count(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, "rA")
            RI.bind_registered(d, "S1", "rA")
            ev = PE.record_review_verdict(surface, passed=True, independent=True, blocking=0,
                                          run_id="rA")
            _start(d, "S1", "late", "general-purpose")
            with mock.patch.object(PE, "_now_iso", return_value="9999-01-01T00:00:00Z"):
                _stop(d, "S1", "late", GOOD)
            self.assertTrue(ev["ts"] < "9999")
            self.assertIs(False, PE.ship_review_gate(surface, run_id="rA").observed)

    def test_a_subagent_seen_to_finish_makes_it_observed(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, "rA")
            RI.bind_registered(d, "S1", "rA")
            _start(d, "S1", "r1", "general-purpose")
            _stop(d, "S1", "r1", GOOD)
            PE.record_review_verdict(surface, passed=True, independent=True, blocking=0,
                                     run_id="rA")
            gate = PE.ship_review_gate(surface, run_id="rA")
            self.assertEqual((True, True, "review passed (independent ✓)"),
                             (gate.observed, gate.independent, gate.message))

    def test_an_inline_review_has_nothing_to_observe(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, "rA")
            PE.record_review_verdict(surface, passed=True, independent=False, blocking=0,
                                     run_id="rA")
            self.assertIsNone(PE.ship_review_gate(surface, run_id="rA").observed)


class TheHooksAreWired(unittest.TestCase):

    def test_hooks_json_wires_both_events_and_the_spawn_matcher(self):
        here = os.path.dirname(os.path.abspath(__file__))
        p = os.path.join(here, "..", "src", "mokata", "hooks", "hooks.json")
        with open(p, encoding="utf-8") as fh:
            hooks = json.load(fh)["hooks"]
        self.assertIn("subagent-start", json.dumps(hooks["SubagentStart"]))
        self.assertIn("subagent-stop", json.dumps(hooks["SubagentStop"]))
        gate = [e for e in hooks["PreToolUse"] if "gate-guard" in json.dumps(e)][0]
        self.assertIn("Agent", gate["matcher"].split("|"))
        self.assertIn("Task", gate["matcher"].split("|"))


if __name__ == "__main__":
    unittest.main()
