"""0.0.22 stage D1 — `compaction-proof`: CP.S1 + H-1b.

Doc 110 criterion 5, clause 1: *a gate survives compaction with its banner, hard rules and pipeline
position intact.* The gates never forget (their state is on disk); the MODEL does. PreCompact writes
this session's anchor; SessionStart after the compaction re-states THIS session's run, position and
the obligation that decides its next implementation write, and says whether anything moved.
Session-keyed, so parallel runs each get their own (standing constraint, 2026-10-09).
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
from mokata import reanchor as RA
from mokata import run_intent as RI
from mokata.config import Surface
from mokata.govern.resume import PipelineCheckpoint
from mokata.init import init_repo
from mokata.run_resolver import APPROACH_PREFIX, SPEC_PREFIX
from mokata.state import StateStore
from mokata.tdd_state import record, state_dir

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _silent(_):
    pass


def _repo(d, *runs):
    init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
    surface = Surface.load(d)
    for rid in runs:
        PipelineCheckpoint(surface.state, rid).ensure_registered()
    return d


def _bind(d, sid, rid):
    RI.bind_registered(d, sid, rid)


def _stdin(payload):
    return mock.patch.object(hook_cli, "_read_stdin_bounded", return_value=json.dumps(payload))


def _session_start(d, sid, source):
    buf = io.StringIO()
    with mock.patch.object(hook_cli, "_payload_from_stdin",
                           return_value={"cwd": d, "session_id": sid, "source": source}), \
            redirect_stdout(buf):
        hook_cli.session_start_main([])
    return buf.getvalue()


class CpS1TheSessionIsReAnchoredAfterCompaction(unittest.TestCase):

    def setUp(self):
        os.environ.pop("MOKATA_SESSION_ID", None)

    def test_cp_s1_after_compaction_the_briefing_names_this_sessions_run_and_obligation(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            _bind(d, "S1", "rA")
            out = _session_start(d, "S1", "compact")
            self.assertIn("re-anchored after compaction", out)
            self.assertIn("rA", out)
            self.assertIn("approach-approval", out, "the obligation that decides the next write")
            self.assertIn("hard rule", out)

    def test_the_next_write_line_follows_the_gates_own_order(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            _bind(d, "S1", "rA")
            store = StateStore(state_dir(d))
            nxt = lambda: RA.next_write(d, "S1", RA.position(d, "S1"))  # noqa: E731
            self.assertIn("BLOCKED [approach-approval]", nxt())
            store.write(APPROACH_PREFIX + "rA", {"run_id": "rA"})
            self.assertIn("BLOCKED [spec-persisted]", nxt())
            store.write(SPEC_PREFIX + "rA", {"run_id": "rA"})
            self.assertIn("BLOCKED [no-code-without-failing-test]", nxt())
            record(store, "rA", red=["tests/test_a.py::t"])
            self.assertIn("ALLOWED", nxt())

    def test_the_next_write_line_is_the_gates_verdict_in_the_states_a_ladder_got_wrong(self):
        """0.0.22 verification D-2: an open amend, an override in force, a finished run."""
        from mokata.spec_scope import amend_key
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            _bind(d, "S1", "rA")
            store = StateStore(state_dir(d))
            store.write(APPROACH_PREFIX + "rA", {"run_id": "rA"})
            store.write(SPEC_PREFIX + "rA", {"run_id": "rA"})
            record(store, "rA", red=["tests/test_a.py::t"])
            store.write(amend_key("rA"), {"status": "open", "from_version": 1, "to_version": 2})
            line = RA.build_reanchor(d, "S1", "compact")
            self.assertIn("BLOCKED [spec-scope]", line, "an open amend blocks; the line must say so")
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            _bind(d, "S1", "rA")
            store = StateStore(state_dir(d))
            store.write(G.override_key("rA"), {"scopes": [G.GATE_PHASE], "reason": "spike"})
            line = RA.next_write(d, "S1", RA.position(d, "S1"))
            self.assertIn("ALLOWED", line, "an override in force lets the write through")
            self.assertIn("override of approach-approval", line)

    def test_the_next_write_line_agrees_with_what_the_gate_actually_does(self):
        """The re-anchor must not describe a gate the gate hook would not apply."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            _bind(d, "S1", "rA")
            StateStore(state_dir(d)).write(APPROACH_PREFIX + "rA", {"run_id": "rA"})
            out = G.check_write(d, os.path.join(d, RA.PROBE_NEW_FILE), session_id="S1")
            self.assertFalse(out.allowed)
            self.assertIn(f"BLOCKED [{out.gate}]", RA.next_write(d, "S1", RA.position(d, "S1")))

    def test_an_open_claim_is_re_anchored_too(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            RI.record_intent(d, "S1", "prompt:brainstorm")
            self.assertIn("run-registration", _session_start(d, "S1", "compact"))

    def test_startup_clear_and_an_unclaimed_session_are_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            _bind(d, "S1", "rA")
            for source in ("startup", "clear"):
                with self.subTest(source=source):
                    self.assertNotIn("re-anchored", _session_start(d, "S1", source))
            self.assertNotIn("re-anchored", _session_start(d, "S9", "compact"))
            self.assertIsNone(RA.build_reanchor(d, "S9", "compact"))

    def test_overrides_in_force_are_restated(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            _bind(d, "S1", "rA")
            StateStore(state_dir(d)).write(G.override_key("rA"),
                                           {"scopes": [G.GATE_PHASE], "reason": "spike"})
            self.assertIn("overrides in force (ledgered): approach-approval",
                          RA.build_reanchor(d, "S1", "compact"))


class H1bThePreCompactAnchorMakesTheRoundTripCheckable(unittest.TestCase):

    def setUp(self):
        os.environ.pop("MOKATA_SESSION_ID", None)

    def test_h1b_the_anchor_is_written_and_the_round_trip_reads_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            _bind(d, "S1", "rA")
            with _stdin({"session_id": "S1", "cwd": d, "hook_event_name": "PreCompact"}):
                self.assertEqual(0, hook_cli.pre_compact_main([]))
            self.assertTrue(StateStore(state_dir(d)).exists(RA.ANCHOR_PREFIX + "S1"))
            self.assertIn("unchanged (pre-compaction anchor matches)",
                          RA.build_reanchor(d, "S1", "compact"))

    def test_a_moved_position_is_named(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            _bind(d, "S1", "rA")
            RA.write_anchor(d, "S1")
            StateStore(state_dir(d)).write(APPROACH_PREFIX + "rA", {"run_id": "rA"})
            self.assertIn("MOVED (approach)", RA.build_reanchor(d, "S1", "compact"))

    def test_no_anchor_is_said_rather_than_implied(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            _bind(d, "S1", "rA")
            self.assertIn("no pre-compaction anchor was recorded",
                          RA.build_reanchor(d, "S1", "compact"))

    def test_an_unclaimed_session_writes_no_anchor(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            RA.write_anchor(d, "S9")
            self.assertFalse(StateStore(state_dir(d)).exists(RA.ANCHOR_PREFIX + "S9"))


class ParallelRunsEachCompactionIsReAnchoredToItsOwnRun(unittest.TestCase):

    def setUp(self):
        os.environ.pop("MOKATA_SESSION_ID", None)

    def test_parallel_runs_two_sessions_two_positions(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA", "rB")
            _bind(d, "S1", "rA")
            _bind(d, "S2", "rB")
            StateStore(state_dir(d)).write(APPROACH_PREFIX + "rB", {"run_id": "rB"})
            a = RA.build_reanchor(d, "S1", "compact")
            b = RA.build_reanchor(d, "S2", "compact")
            self.assertIn("rA", a)
            self.assertNotIn("rB", a)
            self.assertIn("approach-approval", a)
            self.assertIn("rB", b)
            self.assertIn("spec-persisted", b)


class TheWiringIsDerived(unittest.TestCase):

    def test_setup_and_the_plugin_both_wire_pre_compact(self):
        from mokata.harness_setup import expected_hook_wiring
        self.assertEqual([{"subcommand": "pre-compact", "matcher": None}],
                         expected_hook_wiring()["PreCompact"])
        with open(os.path.join(ROOT, "src", "mokata", "hooks", "hooks.json"),
                  encoding="utf-8") as fh:
            plugin = json.load(fh)["hooks"]
        self.assertIn("pre-compact", plugin["PreCompact"][0]["hooks"][0]["command"])
        self.assertIn("pre-compact", hook_cli._SUBCOMMANDS)


if __name__ == "__main__":
    unittest.main()
