"""SS.S2 — PER-TURN AUTOSAVE: LOSE ≤1 TURN (0.0.13 SS cluster, stage 3).

SS.S1 wired persistence at COARSE milestones only, so a crash mid-Q&A still lost every answer
since the last milestone. SS.S2 wires the designed-but-unwired `SessionFlow.turn()` seam so the
running brainstorm state is persisted after EACH answered turn — one atomic session-scoped write
(the SS.S0 path), degrade-clean, UNGATED. The loss bound becomes a guarantee: a kill −9 loses at
most the single in-flight turn (P17).

A "turn" = one answered question (`BrainstormSession.answer()` resolving the pending question). The
per-turn autosave fires right after each answered turn; `to_dict()` carries every answered turn, so
resume rehydrates everything through the last saved turn.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import io
import json
import os
import tempfile
import unittest
from pathlib import Path

import _support  # noqa: F401  (puts src/ on the path)

import mokata as M
from mokata import session as S
from mokata.agent_skills import skill_markdown
from mokata.brainstorm import (
    APPROACH_STATE_KEY,
    BRAINSTORM_PROGRESS_KEY,
    BrainstormSession,
    restore_brainstorm_progress,
)
from mokata.config import Surface
from mokata.init import init_repo
from mokata.skill_contracts import CONTRACTS

from mokata import session_flow as FLOW          # the module under test (SS.S2 wires turn())
from mokata.mcp import tools_read as TR           # the agent-facing MCP surface


def _repo(d, profile="standard"):
    init_repo(root=d, profile=profile, assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _pin(sid):
    os.environ[S.SESSION_ID_ENV] = sid
    S.reset_for_test()


def _in_progress(topic="explore X", n=1):
    """A NOT-approved in-progress session with `n` answered turns (progress only, no approval)."""
    s = BrainstormSession(topic)
    s.set_anchor(topic)
    for i in range(1, n + 1):
        s.ask(f"q{i}?")
        s.answer(f"a{i}")
    return s


class _Base(unittest.TestCase):
    def setUp(self):
        os.environ.pop(S.SESSION_ID_ENV, None)
        S.reset_for_test()
        FLOW.reset_persist_warnings()

    def tearDown(self):
        os.environ.pop(S.SESSION_ID_ENV, None)
        S.reset_for_test()
        FLOW.reset_persist_warnings()


# =================================================== THE loss-bound regression (lose ≤1 turn)
class TestLossBound(_Base):
    def test_ss_s2_regression(self):
        """Simulate a brainstorm of k answered turns each per-turn autosaved, then a turn k+1 that
        is asked+answered in memory but crashes BEFORE its autosave. A kill −9 → the EXISTING
        resume path rehydrates EVERYTHING through turn k; only the in-flight turn k+1 is lost."""
        for k in (1, 2, 5):
            with self.subTest(k=k):
                with tempfile.TemporaryDirectory() as d:
                    sid = f"ssK{k}"
                    _pin(sid)
                    surface = _repo(d)

                    s = BrainstormSession("add a caching layer")
                    s.set_anchor("add a caching layer")
                    # k full answered turns, each followed by a per-turn autosave — NO milestone.
                    for i in range(1, k + 1):
                        s.ask(f"q{i}?")
                        s.answer(f"a{i}")
                        FLOW.SessionFlow(surface).turn(s.to_dict())

                    # turn k+1 is IN FLIGHT: asked + answered in memory, crash before its autosave.
                    s.ask(f"q{k + 1}?")
                    s.answer(f"a{k + 1}")

                    # --- kill −9: in-memory state + objects gone, fresh process ---
                    del s
                    del surface
                    _pin(sid)                       # the resumed window keeps its identity
                    fresh = Surface.load(d)

                    bs = restore_brainstorm_progress(fresh.state)
                    self.assertIsNotNone(bs)
                    answered = [(q.text, q.answer) for q in bs.answered_questions]
                    # everything through turn k survived; the in-flight turn k+1 is lost (≤1 turn)
                    self.assertEqual(answered,
                                     [(f"q{i}?", f"a{i}") for i in range(1, k + 1)])
                    # the loss bound is exactly one turn — never more
                    self.assertEqual(len(answered), k)


# =================================================== cheapness (exactly one write, no network)
class TestCheapness(_Base):
    def test_turn_save_is_exactly_one_state_file_write(self):
        """A per-turn autosave is ONE state-file write (in-progress → only `brainstorm_progress`);
        no registry churn, no reread-verify write, no probe."""
        from mokata.session_state import SessionScopedStore
        with tempfile.TemporaryDirectory() as d:
            _pin("sC1")
            surface = _repo(d)
            writes = []
            orig = SessionScopedStore.write

            def counting_write(self, name, data):
                writes.append(name)
                return orig(self, name, data)

            SessionScopedStore.write = counting_write
            try:
                FLOW.SessionFlow(surface).turn(_in_progress(n=1).to_dict())
            finally:
                SessionScopedStore.write = orig
            self.assertEqual(writes, [BRAINSTORM_PROGRESS_KEY])  # exactly ONE state-file write

    def test_turn_save_does_no_network(self):
        """Zero network on the turn-save path: if any socket is opened the save would raise — it
        must complete cleanly with sockets poisoned."""
        import socket
        with tempfile.TemporaryDirectory() as d:
            _pin("sC2")
            surface = _repo(d)
            orig = socket.socket

            def boom_socket(*a, **k):
                raise AssertionError("turn save must not touch the network")

            socket.socket = boom_socket
            try:
                res = FLOW.SessionFlow(surface).turn(_in_progress(n=1).to_dict())
            finally:
                socket.socket = orig
            self.assertIsNotNone(res)
            self.assertTrue(os.path.exists(surface.state.path(BRAINSTORM_PROGRESS_KEY)))


# =================================================== race (two rapid turn saves, last wins)
class TestRace(_Base):
    def test_two_rapid_turn_saves_last_wins_atomically(self):
        """Two rapid turn saves for the same session → a VALID file, last wins (MS.S1's atomic
        replace under the cross-process lock guarantees no torn read and last-writer-wins)."""
        with tempfile.TemporaryDirectory() as d:
            _pin("sR")
            surface = _repo(d)
            flow = FLOW.SessionFlow(surface)
            flow.turn(_in_progress(n=2).to_dict())              # first save: 2 answered turns
            flow.turn(_in_progress(n=4).to_dict())              # second save: 4 answered turns
            bs = restore_brainstorm_progress(surface.state)
            self.assertIsNotNone(bs)                             # never a torn / half write
            self.assertEqual(len(bs.answered_questions), 4)      # last write wins


# =================================================== degrade-clean (warn once, retry next turn)
class TestDegradeClean(_Base):
    def test_turn_save_failure_warns_once_and_conversation_continues(self):
        """A per-turn autosave fs failure warns ONCE, never raises out of the conversation, and the
        NEXT turn save retries once the disk recovers."""
        with tempfile.TemporaryDirectory() as d:
            _pin("sD")
            surface = _repo(d)

            def boom(*a, **k):
                raise OSError("disk full")

            orig = FLOW.save_session
            warned = io.StringIO()
            flow = FLOW.SessionFlow(surface, warn=warned.write)
            FLOW.save_session = boom
            try:
                self.assertIsNone(flow.turn(_in_progress(n=1).to_dict()))   # degrade → None
                self.assertIsNone(flow.turn(_in_progress(n=2).to_dict()))   # 2nd: no re-warn
            finally:
                FLOW.save_session = orig
            self.assertEqual(warned.getvalue().count("\n"), 1)              # warned exactly ONCE

            # the disk recovers — the next turn save RETRIES and succeeds
            res = flow.turn(_in_progress(n=3).to_dict())
            self.assertIsNotNone(res)
            self.assertTrue(os.path.exists(surface.state.path(BRAINSTORM_PROGRESS_KEY)))


# =================================================== MCP surface (turn flag; SS.S1 unchanged)
class TestMcpSurface(_Base):
    def test_session_save_turn_flag_routes_through_turn(self):
        """The least-surface change: `session_save(..., turn=True)` routes through
        `SessionFlow.turn()`; the default (`turn=False`) still routes through `.checkpoint()` —
        SS.S1's behavior is byte-identical when the flag is unused."""
        turn_calls = []
        orig_turn = FLOW.SessionFlow.turn

        def turn_spy(self, brainstorm):
            turn_calls.append(brainstorm)
            return orig_turn(self, brainstorm)

        with tempfile.TemporaryDirectory() as d:
            _pin("sMT")
            _repo(d)
            FLOW.SessionFlow.turn = turn_spy
            try:
                out = TR.session_save(path=d, brainstorm=_in_progress(n=1).to_dict(), turn=True)
                # default path is unchanged: it does NOT route through turn() (SS.S1 behavior)
                TR.session_save(path=d, brainstorm=_in_progress(n=1).to_dict())
            finally:
                FLOW.SessionFlow.turn = orig_turn
            self.assertEqual(len(turn_calls), 1)                 # only turn=True hit turn(); default did not
            self.assertIn(BRAINSTORM_PROGRESS_KEY, out["saved"])

    def test_turn_save_result_is_counts_only(self):
        """Secret-safety: the turn-save result is counts-only — it never echoes topic/question/
        answer content."""
        with tempfile.TemporaryDirectory() as d:
            _pin("sMS")
            _repo(d)
            s = BrainstormSession("add a caching layer")
            s.set_anchor("add a caching layer")
            s.ask("read/write ratio?")
            s.answer("read-heavy")
            out = TR.session_save(path=d, brainstorm=s.to_dict(), turn=True)
            blob = json.dumps(out)
            for secret in ("add a caching layer", "read-heavy", "read/write ratio?"):
                self.assertNotIn(secret, blob)
            # never an approval key from a mid-Q&A turn save
            self.assertNotIn(APPROACH_STATE_KEY, out.get("saved", {}))


# =================================================== agent instruction (new per-turn contract)
class TestAgentInstruction(_Base):
    def test_brainstorm_instruction_says_checkpoint_each_answered_turn(self):
        """The Contract + template now instruct the agent to checkpoint EACH answered turn (cheap,
        atomic) in addition to the coarse milestones — the instruction the per-turn wiring backs."""
        can_text = " ".join(CONTRACTS["brainstorm"].can).lower()
        self.assertIn("each answered turn", can_text)
        self.assertIn("session_save", can_text)

        templates = Path(M.__file__).parent / "templates" / "commands"
        md = skill_markdown("brainstorm", templates).lower()
        self.assertIn("each answered turn", md)
        self.assertIn("session_save", md)


if __name__ == "__main__":
    unittest.main()
