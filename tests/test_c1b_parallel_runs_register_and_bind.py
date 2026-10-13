"""0.0.22 stage C1 (second half) — RUN-REG structural, ruling R-C1a option C, PARALLEL-SAFE.

Row (doc 84): `RUN-REG` structural half / `RUN-REG-ENFORCED-BY-PROSE-ALONE`. Ruled by Jas
2026-10-09 ("agree"), with a standing constraint: **the design must support multiple parallel runs.**

A session that ASKS for a pipeline (a typed `/mokata:brainstorm`, or the model invoking the
brainstorm skill) records an intent; the gate then blocks implementation writes in THAT session
until its own `session_save(register=true)` comes back; a PostToolUse hook binds the session to the
run id that call returned — the MCP's own id, so there is one identity. A session that never asked
is policed exactly as before (hand-editing stays free).
"""
import io
import json
import os
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import gate_hook as G
from mokata import hook_cli
from mokata import run_intent as RI
from mokata.govern.resume import PipelineCheckpoint
from mokata.init import init_repo
from mokata.config import Surface
from mokata.run_resolver import APPROACH_PREFIX, read_binding
from mokata.state import StateStore
from mokata.tdd_state import state_dir

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMPL = "src/app.py"


def _silent(_):
    pass


def _repo(d, *runs):
    init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
    surface = Surface.load(d)
    for rid in runs:
        PipelineCheckpoint(surface.state, rid).ensure_registered()
    return d


def _approve(d, rid):
    StateStore(state_dir(d)).write(APPROACH_PREFIX + rid, {"run_id": rid, "approach": "a"})


def _stdin(payload):
    return mock.patch.object(hook_cli, "_read_stdin_bounded", return_value=json.dumps(payload))


def _bind_via_hook(d, sid, rid, tool="mcp__mokata__session_save"):
    """The PostToolUse envelope as Claude Code sends it: `tool_response` holding the MCP's answer."""
    resp = [{"type": "text", "text": json.dumps({"ok": True, "registered": rid})}]
    with _stdin({"session_id": sid, "cwd": d, "tool_name": tool,
                 "tool_input": {"register": True}, "tool_response": resp}):
        return hook_cli.run_bind_main([])


class RunRegStructuralAnAskedForPipelineWaitsForItsRun(unittest.TestCase):

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop("MOKATA_SESSION_ID", None)
        self.addCleanup(self.env.stop)

    def test_run_reg_structural_an_open_intent_blocks_instead_of_failing_open(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            self.assertTrue(G.check_write(d, IMPL, session_id="S1").allowed,
                            "control: no intent, no run -> the SI.1 floor (allow)")
            RI.record_intent(d, "S1", "prompt:brainstorm")
            out = G.check_write(d, IMPL, session_id="S1")
            self.assertFalse(out.allowed, "an asked-for pipeline with no run failed open")
            self.assertEqual(G.GATE_RUN_REG, out.gate)
            self.assertIn("register=true", out.reason)
            self.assertIn("/clear", out.reason)

    def test_a_session_that_never_asked_is_untouched(self):
        """The positive-trigger contract: another window's intent never polices this one."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            RI.record_intent(d, "S1", "prompt:brainstorm")
            self.assertTrue(G.check_write(d, IMPL, session_id="S2").allowed)
            self.assertTrue(G.check_write(d, IMPL).allowed, "no session id: today's gate")

    def test_an_expired_intent_no_longer_holds_the_session(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            RI.record_intent(d, "S1", "prompt:brainstorm")
            later = time.time() + RI.INTENT_TTL_SECS + 1
            self.assertEqual((RI.CLAIM_NONE, None), RI.session_claim(d, "S1", now=later))

    def test_tests_and_non_code_stay_writable_while_waiting(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            RI.record_intent(d, "S1", "prompt:brainstorm")
            self.assertTrue(G.check_write(d, "tests/test_app.py", session_id="S1").allowed)
            self.assertTrue(G.check_write(d, "README.md", session_id="S1").allowed)


class TheSessionIsBoundToTheRunItsOwnRegistrationReturned(unittest.TestCase):

    def setUp(self):
        os.environ.pop("MOKATA_SESSION_ID", None)

    def test_the_post_tool_use_hook_binds_and_the_gate_then_polices_that_run(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            RI.record_intent(d, "S1", "prompt:brainstorm")
            self.assertEqual(0, _bind_via_hook(d, "S1", "rA"))
            self.assertEqual((RI.CLAIM_BOUND, "rA"), RI.session_claim(d, "S1"))
            self.assertEqual("rA", read_binding(d, "S1"), "the badge binding did not flip")
            out = G.check_write(d, IMPL, session_id="S1")
            self.assertFalse(out.allowed)
            self.assertEqual(G.GATE_PHASE, out.gate,
                             "bound, the session is policed by its run's phase (brainstorm), "
                             "not by the registration precondition")

    def test_one_identity_the_bound_id_is_the_one_the_mcp_registered(self):
        """The real `session_save` answer, fed back as the PostToolUse `tool_response`."""
        from mokata.mcp.tools_read import session_save
        from mokata.session import current_run_id
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            out = session_save(path=d, register=True)
            self.assertEqual(current_run_id(), out["registered"])
            resp = [{"type": "text", "text": json.dumps(out, default=str)}]
            with _stdin({"session_id": "S1", "cwd": d, "tool_name": "mcp__mokata__session_save",
                         "tool_response": resp}):
                hook_cli.run_bind_main([])
            self.assertEqual((RI.CLAIM_BOUND, current_run_id()), RI.session_claim(d, "S1"))

    def test_both_server_names_bind_and_nothing_else_does(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA", "rB")
            _bind_via_hook(d, "S1", "rA", tool="mcp__plugin_mokata_mokata__session_save")
            self.assertEqual((RI.CLAIM_BOUND, "rA"), RI.session_claim(d, "S1"))
            for tool in ("mcp__other__session_save", "mcp__mokata__session_list", "Write"):
                with self.subTest(tool=tool):
                    _bind_via_hook(d, "S2", "rB", tool=tool)
                    self.assertEqual(RI.CLAIM_NONE, RI.session_claim(d, "S2")[0])

    def test_a_response_without_a_registration_binds_nothing(self):
        self.assertIsNone(RI.registered_run("mcp__mokata__session_save", {"ok": True}))
        self.assertIsNone(RI.registered_run("mcp__mokata__session_save", "not json"))
        self.assertEqual("r9", RI.registered_run("mcp__mokata__session_save",
                                                 {"content": [{"text": '{"registered": "r9"}'}]}))

    def test_asking_again_reopens_the_claim(self):
        """A new /mokata:brainstorm in a bound session: its edits now belong to the NEW run."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            _bind_via_hook(d, "S1", "rA")
            RI.record_intent(d, "S1", "prompt:brainstorm")
            self.assertEqual((RI.CLAIM_OPEN, None), RI.session_claim(d, "S1"))


class ParallelRunsEachSessionIsPolicedByItsOwnRun(unittest.TestCase):
    """The standing constraint (Jas 2026-10-09): multiple parallel runs."""

    def setUp(self):
        os.environ.pop("MOKATA_SESSION_ID", None)

    def test_parallel_runs_two_sessions_two_runs_two_verdicts(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA", "rB")
            _approve(d, "rA")
            _approve(d, "rB")
            # Control — BEFORE: with two pipelines and no claim, the gate cannot tell the windows
            # apart and fails open for both (the guarantee against wrong-window blocking).
            self.assertTrue(G.check_write(d, IMPL).allowed)
            self.assertIn("two or more", G.check_write(d, IMPL).reason)
            for sid, rid in (("S1", "rA"), ("S2", "rB")):
                RI.record_intent(d, sid, "prompt:brainstorm")
                _bind_via_hook(d, sid, rid)
            # rA still has no spec; rB gets one emitted — so the two runs are in different states.
            StateStore(state_dir(d)).write(G.SPEC_PREFIX + "rB", {"run_id": "rB"})
            a = G.check_write(d, IMPL, session_id="S1")
            b = G.check_write(d, IMPL, session_id="S2")
            self.assertFalse(a.allowed)
            self.assertEqual(G.GATE_SPEC, a.gate, "S1 was not policed by ITS run (rA: no spec)")
            self.assertNotEqual((a.allowed, a.gate), (b.allowed, b.gate),
                                "the two sessions got one verdict: one is borrowing the other's run")

    def test_an_unbound_session_never_borrows_a_bound_one(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            _bind_via_hook(d, "S1", "rA")
            RI.record_intent(d, "S2", "prompt:brainstorm")
            out = G.check_write(d, IMPL, session_id="S2")
            self.assertEqual(G.GATE_RUN_REG, out.gate,
                             "S2 resolved S1's run (the repo's only run) instead of waiting")

    def test_the_session_start_guess_does_not_widen_enforcement(self):
        """SessionStart binds a new session to the single live run — a GUESS. The gate must not
        police by it: only a claim this session's own registration produced counts."""
        from mokata.run_resolver import bind_session_run
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA", "rB")
            _approve(d, "rA")
            _approve(d, "rB")
            bind_session_run(d, "S3", "rA")
            self.assertEqual(G.check_write(d, IMPL).allowed,
                             G.check_write(d, IMPL, session_id="S3").allowed)


class TheIntentIsRecordedOnBothWaysToAskForAPipeline(unittest.TestCase):

    def setUp(self):
        os.environ.pop("MOKATA_SESSION_ID", None)

    def test_a_typed_slash_command_records_the_intent(self):
        for prompt, want in (("/mokata:brainstorm a cache layer", "brainstorm"),
                             ("  /brainstorm", "brainstorm"), ("please brainstorm this", None),
                             ("/mokata:develop", None), ("/mokata:brainstormx", None)):
            with self.subTest(prompt=prompt):
                self.assertEqual(want, RI.pipeline_in_prompt(prompt))

    def test_the_user_prompt_submit_hook_records_it(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            with _stdin({"session_id": "S1", "cwd": d, "prompt": "/mokata:brainstorm x"}), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(0, hook_cli.user_prompt_submit_main([]))
            self.assertEqual(RI.CLAIM_OPEN, RI.session_claim(d, "S1")[0])

    def test_the_model_invoking_the_skill_records_it_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            with _stdin({"session_id": "S1", "cwd": d, "tool_name": "Skill",
                         "tool_input": {"skill": "mokata:brainstorm"}}):
                self.assertEqual(0, hook_cli.gate_guard_main([]))
            self.assertEqual(RI.CLAIM_OPEN, RI.session_claim(d, "S1")[0])
            with _stdin({"session_id": "S2", "cwd": d, "tool_name": "Skill",
                         "tool_input": {"skill": "mokata:review"}}):
                self.assertEqual(0, hook_cli.gate_guard_main([]))
            self.assertEqual(RI.CLAIM_NONE, RI.session_claim(d, "S2")[0])

    def test_the_gate_hook_passes_the_session_through(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            RI.record_intent(d, "S1", "prompt:brainstorm")
            with _stdin({"session_id": "S1", "cwd": d, "tool_name": "Write",
                         "tool_input": {"file_path": os.path.join(d, IMPL), "content": "x"}}), \
                    mock.patch("sys.stderr", new_callable=io.StringIO) as err:
                self.assertEqual(G.BLOCK_EXIT, hook_cli.gate_guard_main([]))
            self.assertIn(G.GATE_RUN_REG, err.getvalue())


class TheWiringIsDerived(unittest.TestCase):

    def test_the_plugin_and_setup_wire_the_same_matcher_the_reader_uses(self):
        from mokata.harness_setup import expected_hook_wiring
        wiring = expected_hook_wiring()
        post = {e["subcommand"]: e["matcher"] for e in wiring["PostToolUse"]}
        self.assertEqual(RI.REGISTER_TOOL_RE, post.get("run-bind"))
        pre = {e["subcommand"]: e["matcher"] for e in wiring["PreToolUse"]}
        self.assertIn("Skill", pre["gate-guard"].split("|"))
        with open(os.path.join(ROOT, "src", "mokata", "hooks", "hooks.json"),
                  encoding="utf-8") as fh:
            plugin = json.load(fh)["hooks"]
        bind = [e for e in plugin["PostToolUse"] if "run-bind" in e["hooks"][0]["command"]]
        self.assertEqual([RI.REGISTER_TOOL_RE], [e["matcher"] for e in bind])
        gate = [e for e in plugin["PreToolUse"] if "gate-guard" in e["hooks"][0]["command"]]
        self.assertEqual(pre["gate-guard"], gate[0]["matcher"])

    def test_the_run_reg_gate_is_not_overridable(self):
        """There is no run to file an override against; the way out is to register or /clear."""
        self.assertNotIn(G.GATE_RUN_REG, G.GATES)


if __name__ == "__main__":
    unittest.main()
