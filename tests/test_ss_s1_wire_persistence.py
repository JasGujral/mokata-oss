"""SS.S1 — WIRE THE PIPELINE MOMENTS TO PERSISTENCE (0.0.13 SS cluster, stage 2).

SS.S0 built the manual save; the pipeline still forgot by default — the three formerly-orphaned
persistence functions (`save_brainstorm_progress` / `persist_approach` / `mark_passed`) had A
caller (`session_save`) but not the RIGHT callers. SS.S1 adds the ONE production seam —
`session_flow.SessionFlow` — that fires them AUTOMATICALLY at the pipeline's coarse decision
moments, reachable from the surface the real agent flow hits (the `session_save` MCP tool routes
THROUGH it), so an approved approach / passed gate / brainstorm milestone survives a kill by
DEFAULT — the user never calls `session_save` by hand.

The anti-orphan chain this proves (agent moment → MCP tool → orchestrator → orphan):
  * (c) milestone   → `session_save` tool → `SessionFlow.checkpoint(brainstorm=…)` →
                      `save_session` → `save_brainstorm_progress`.
  * (a) approval    → `session_save` tool → `SessionFlow.checkpoint(brainstorm=approved)` →
                      `save_session` → `persist_approach` (ONLY after the HARD-GATE).
  * (b) gate-pass   → `session_save` tool → `SessionFlow.checkpoint(passed=[…])` →
                      `save_session` → `mark_passed`  (AND double-wired: `run_pipeline` marks
                      each passed gate directly, run_id from `current_run_id()`).

Every wired moment is DEGRADE-CLEAN (a persist failure warns once, secret-free, never blocks the
moment) and UNGATED (a local save — the binding guard), and the HARD-GATE survives the round-trip
(restoring never marks anything approved; per-turn autosave stays SS.S2's, not SS.S1's).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import io
import json
import os
import sys
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)
from _support import approve_with_lenses

from mokata import session as S
from mokata.brainstorm import (
    APPROACH_STATE_KEY,
    BRAINSTORM_PROGRESS_KEY,
    Approach,
    BrainstormSession,
    load_approved_approach,
    restore_brainstorm_progress,
)
from mokata.config import Surface
from mokata.govern.resume import CHECKPOINT_PREFIX, PipelineCheckpoint
from mokata.init import init_repo

from mokata import session_flow as FLOW          # the module under test (SS.S1)
from mokata.mcp import tools_read as TR           # the agent-facing MCP surface


def _repo(d, profile="standard"):
    init_repo(root=d, profile=profile, assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _pin(sid):
    os.environ[S.SESSION_ID_ENV] = sid
    S.reset_for_test()


def _mid_brainstorm(topic="add a caching layer"):
    """An APPROVED mid-brainstorm session (the exact 'approved approach + progress' the headline
    saves) — with an anchor + synthesis so the coarse milestones are all present."""
    s = BrainstormSession(topic)
    s.set_anchor(topic)
    s.ask("read/write ratio?")
    s.answer("read-heavy")
    s.update_synthesis(goal="cache reads without staleness")
    s.propose_approaches([
        Approach("cache-aside", "read-through cache", pros=["simple"], cons=["stale"]),
        Approach("write-through", "writes via cache", pros=["fresh"], cons=["slow writes"]),
    ])
    approve_with_lenses(s, "jas", "cache-aside")
    return s


def _in_progress(topic="explore X"):
    """A NOT-approved in-progress session (progress only, no approval)."""
    s = BrainstormSession(topic)
    s.set_anchor(topic)
    s.ask("q?")
    s.answer("a")
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


# ============================================================ THE headline — ZERO manual saves
class TestRegressionZeroManualSaves(_Base):
    def test_ss_s1_regression(self):
        """Run the interactive flow to approval + pass a phase gate DRIVING THROUGH THE MCP
        SURFACE (the way the agent does, per the skill Contract) — with NO manual `save_session`
        anywhere — then simulate a kill −9 and prove the EXISTING resume path rehydrates the
        approved approach, the milestones, and the passed checkpoint."""
        with tempfile.TemporaryDirectory() as d:
            _pin("sessX")
            surface = _repo(d)

            # The agent builds the brainstorm and CHECKPOINTS at each coarse milestone via the
            # `session_save` MCP tool — never `SAVE.save_session(...)` directly.
            s = BrainstormSession("add a caching layer")
            s.set_anchor("add a caching layer")
            TR.session_save(path=d, brainstorm=s.to_dict())                 # (c) anchor
            s.ask("read/write ratio?")
            s.answer("read-heavy")
            s.update_synthesis(goal="cache reads without staleness")
            TR.session_save(path=d, brainstorm=s.to_dict())                 # (c) synthesis
            s.propose_approaches([
                Approach("cache-aside", "read-through cache", pros=["simple"], cons=["stale"]),
                Approach("write-through", "writes via cache", pros=["fresh"], cons=["slow"]),
            ])
            TR.session_save(path=d, brainstorm=s.to_dict())                 # (c) approaches
            approve_with_lenses(s, "jas", "cache-aside")
            TR.session_save(path=d, brainstorm=s.to_dict())                 # (a) approval
            TR.session_save(path=d, passed=["brainstorm", "analysis"])      # (b) gate-pass

            # --- simulate kill −9: in-memory state + objects gone, fresh process ---
            del s
            del surface
            _pin("sessX")                       # the resumed window keeps its identity
            fresh = Surface.load(d)

            # brainstorm progress + milestones rehydrate through the EXISTING reader
            bs = restore_brainstorm_progress(fresh.state)
            self.assertIsNotNone(bs)
            self.assertEqual(bs.topic, "add a caching layer")
            self.assertEqual(bs.anchor, "add a caching layer")
            self.assertEqual([a.name for a in bs.approaches], ["cache-aside", "write-through"])
            self.assertEqual(
                [(q.text, q.answer) for q in bs.answered_questions],
                [("read/write ratio?", "read-heavy")])
            self.assertTrue(bs.approved)

            # approved approach rehydrates through the EXISTING downstream reader
            ho = load_approved_approach(fresh.state)
            self.assertIsNotNone(ho)
            self.assertEqual(ho.approach.name, "cache-aside")
            self.assertEqual(ho.approver, "jas")

            # pipeline checkpoint rehydrates through the EXISTING resume machinery
            cp = PipelineCheckpoint(fresh.state, S.current_run_id())
            self.assertEqual(cp.passed, ["brainstorm", "analysis"])
            from mokata.brainstorm import PIPELINE_PHASES
            self.assertEqual(cp.resume_phase(),
                             PIPELINE_PHASES[PIPELINE_PHASES.index("analysis") + 1])


# ============================================================ per-moment (key + atomic + degrade)
class TestPerMoment(_Base):
    def test_milestone_persists_only_progress(self):
        with tempfile.TemporaryDirectory() as d:
            _pin("sM")
            surface = _repo(d)
            FLOW.SessionFlow(surface).milestone(_in_progress().to_dict())
            self.assertTrue(os.path.exists(surface.state.path(BRAINSTORM_PROGRESS_KEY)))
            # a not-approved milestone never writes an approval key
            self.assertFalse(os.path.exists(surface.state.path(APPROACH_STATE_KEY)))

    def test_approval_persists_progress_and_approach(self):
        with tempfile.TemporaryDirectory() as d:
            _pin("sA")
            surface = _repo(d)
            FLOW.SessionFlow(surface).milestone(_mid_brainstorm().to_dict())
            self.assertTrue(os.path.exists(surface.state.path(BRAINSTORM_PROGRESS_KEY)))
            self.assertTrue(os.path.exists(surface.state.path(APPROACH_STATE_KEY)))

    def test_gate_passed_persists_exactly_its_checkpoint(self):
        with tempfile.TemporaryDirectory() as d:
            _pin("sG")
            surface = _repo(d)
            FLOW.SessionFlow(surface).gate_passed("brainstorm")
            rid = S.current_run_id()
            self.assertTrue(os.path.exists(surface.state.path(CHECKPOINT_PREFIX + rid)))
            self.assertEqual(PipelineCheckpoint(surface.state, rid).passed, ["brainstorm"])

    def test_persist_failure_warns_once_and_moment_still_succeeds(self):
        """A persist failure (fs error injected) warns ONCE and the moment still succeeds — the
        wiring is degrade-clean: it never blocks or raises out of the pipeline moment."""
        with tempfile.TemporaryDirectory() as d:
            _pin("sF")
            surface = _repo(d)
            boom_calls = []

            def boom(*a, **k):
                boom_calls.append(1)
                raise OSError("disk full")

            orig = FLOW.save_session
            warned = io.StringIO()
            FLOW.save_session = boom
            try:
                flow = FLOW.SessionFlow(surface, warn=warned.write)
                # the moment returns cleanly (None on degrade), never raises
                self.assertIsNone(flow.milestone(_in_progress().to_dict()))
                self.assertIsNone(flow.milestone(_in_progress().to_dict()))  # 2nd time: no re-warn
            finally:
                FLOW.save_session = orig
            self.assertEqual(len(boom_calls), 2)                 # both moments attempted
            self.assertEqual(warned.getvalue().count("\n"), 1)   # but warned exactly ONCE


# ============================================================ gate integrity (P2 + HARD-GATE)
class TestGateIntegrity(_Base):
    def test_approval_persisted_only_after_the_gate_passed(self):
        """A not-approved session persists progress but NEVER an approval key; only an approved
        session writes `approved_approach` — persistence never fabricates an approval."""
        with tempfile.TemporaryDirectory() as d:
            _pin("sI1")
            surface = _repo(d)
            FLOW.SessionFlow(surface).milestone(_in_progress().to_dict())
            self.assertIsNone(load_approved_approach(surface.state))

    def test_persisted_approach_round_trips_with_hard_gate_flags(self):
        with tempfile.TemporaryDirectory() as d:
            _pin("sI2")
            surface = _repo(d)
            FLOW.SessionFlow(surface).milestone(_mid_brainstorm().to_dict())
            ho = load_approved_approach(surface.state)
            self.assertIsNotNone(ho)
            self.assertEqual(ho.approach.name, "cache-aside")
            self.assertEqual(ho.approver, "jas")
            self.assertTrue(ho.approved_at)                      # the gate stamp survives

    def test_restore_never_marks_anything_approved(self):
        """HARD-GATE survives the persistence round-trip: rehydrating an in-progress session
        never flips it to approved — restoring progress is not an approval."""
        with tempfile.TemporaryDirectory() as d:
            _pin("sI3")
            surface = _repo(d)
            FLOW.SessionFlow(surface).milestone(_in_progress().to_dict())
            bs = restore_brainstorm_progress(surface.state)
            self.assertFalse(bs.approved)
            self.assertIsNone(load_approved_approach(surface.state))


# ================================================ per-turn autosave WIRED (SS.S2; supersedes SS.S1)
class TestPerTurnAutosaveWired(_Base):
    """SS.S2 wired `SessionFlow.turn()`, so SS.S1's coarse-only assertions here are SUPERSEDED: a
    bare in-memory answer with no checkpoint still writes nothing, but a per-turn AUTOSAVE now
    persists the running progress (the ≤1-turn loss bound). The full per-turn contract lives in
    `test_ss_s2_autosave.py`; these two pins just re-point what SS.S1 previously forbade."""

    def test_per_turn_autosave_persists_the_answered_turn(self):
        """A per-turn autosave after ONE answered turn persists the running progress (was:
        'answering a single question writes nothing')."""
        with tempfile.TemporaryDirectory() as d:
            _pin("sT1")
            surface = _repo(d)
            s = BrainstormSession("topic")
            s.set_anchor("topic")
            s.ask("q?")
            s.answer("a")                                        # one answered turn
            FLOW.SessionFlow(surface).turn(s.to_dict())          # per-turn autosave (SS.S2)
            self.assertTrue(os.path.exists(surface.state.path(BRAINSTORM_PROGRESS_KEY)))
            bs = restore_brainstorm_progress(surface.state)
            self.assertEqual([(q.text, q.answer) for q in bs.answered_questions], [("q?", "a")])

    def test_per_turn_hook_is_wired_and_ungated_progress_only(self):
        """The per-turn hook is WIRED now (no longer raises) and stays UNGATED progress-only — an
        in-progress turn persists progress but never an approval key (was: 'a designed-but-unwired
        seam that raises NotImplementedError')."""
        with tempfile.TemporaryDirectory() as d:
            _pin("sT2")
            surface = _repo(d)
            res = FLOW.SessionFlow(surface).turn(_in_progress().to_dict())
            self.assertIsNotNone(res)
            self.assertTrue(os.path.exists(surface.state.path(BRAINSTORM_PROGRESS_KEY)))
            self.assertFalse(os.path.exists(surface.state.path(APPROACH_STATE_KEY)))


# ============================================================ (b) double-wired into run_pipeline
class TestDoubleWireRunPipeline(_Base):
    def test_run_pipeline_marks_each_passed_gate(self):
        from mokata.engine import AcceptanceCriterion, Spec, TestRef, run_pipeline
        from mokata.state import StateStore

        def handoff():
            s = BrainstormSession("slugify")
            s.ask("unicode or ascii-only?")
            s.answer("ascii-only")
            s.propose_approaches([
                Approach("regex", "strip via regex", pros=["tiny"], cons=["edge"]),
                Approach("library", "use a slug lib", pros=["robust"], cons=["dep"]),
            ])
            approve_with_lenses(s, "jas", "regex")
            return s.handoff()

        with tempfile.TemporaryDirectory() as d:
            _pin("sDW")
            store = StateStore(os.path.join(d, "state"))
            spec = Spec("slugify", [AcceptanceCriterion("AC-1", "lowercase"),
                                    AcceptanceCriterion("AC-2", "strip punct")])
            tests = [TestRef("t1", ["AC-1"]), TestRef("t2", ["AC-2"])]
            run = run_pipeline(handoff(), spec, tests, store=store)
            # output unchanged — the full run still emits
            self.assertTrue(run.ok)
            self.assertIsNotNone(run.emitted)
            # …and the passed gates left a live checkpoint (run_id from current_run_id())
            cp = PipelineCheckpoint(store, S.current_run_id())
            self.assertIn("brainstorm", cp.passed)
            self.assertIn("completeness_gate", cp.passed)
            self.assertIn("emit", cp.passed)


# ============================================================ anti-orphan: reachable from agent
class TestAntiOrphanWiring(_Base):
    def test_session_save_tool_routes_through_the_orchestrator(self):
        """The agent-facing `session_save` MCP tool routes THROUGH `SessionFlow` — the seam is
        reachable from the surface the real flow hits, not a better-tested orphan."""
        seen = []
        orig = FLOW.SessionFlow.checkpoint

        def spy(self, **kw):
            seen.append(kw)
            return orig(self, **kw)

        with tempfile.TemporaryDirectory() as d:
            _pin("sAO")
            _repo(d)
            FLOW.SessionFlow.checkpoint = spy
            try:
                out = TR.session_save(path=d, brainstorm=_mid_brainstorm().to_dict(),
                                      passed=["brainstorm"])
            finally:
                FLOW.SessionFlow.checkpoint = orig
            self.assertEqual(len(seen), 1)                       # routed through the orchestrator
            self.assertIn(BRAINSTORM_PROGRESS_KEY, out["saved"])
            self.assertIn(APPROACH_STATE_KEY, out["saved"])

    def test_brainstorm_skill_contract_instructs_the_agent_to_checkpoint(self):
        """The Contract change is what converts 'code seam exists' → 'production caller exists':
        the brainstorm skill instructs the agent to checkpoint milestones + the approval."""
        from mokata.skill_contracts import CONTRACTS
        from mokata.agent_skills import skill_markdown
        from pathlib import Path
        import mokata as M

        can_text = " ".join(CONTRACTS["brainstorm"].can).lower()
        self.assertIn("checkpoint", can_text)

        templates = Path(M.__file__).parent / "templates" / "commands"
        md = skill_markdown("brainstorm", templates).lower()
        self.assertIn("session_save", md)                        # names the tool to call
        self.assertIn("checkpoint", md)


# ============================================================ secret-safety (warn carries no state)
class TestSecretSafety(_Base):
    def test_warn_output_carries_no_state_content(self):
        with tempfile.TemporaryDirectory() as d:
            _pin("sS")
            surface = _repo(d)

            def boom(*a, **k):
                raise OSError("disk full")

            orig = FLOW.save_session
            warned = io.StringIO()
            FLOW.save_session = boom
            try:
                FLOW.SessionFlow(surface, warn=warned.write).milestone(
                    _mid_brainstorm().to_dict())
            finally:
                FLOW.save_session = orig
            blob = warned.getvalue()
            for secret in ("add a caching layer", "read-heavy", "cache-aside",
                           "read/write ratio?", "read-through cache"):
                self.assertNotIn(secret, blob)


if __name__ == "__main__":
    unittest.main()
