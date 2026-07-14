"""SS.S0 — `session_save`: THE UNGATED SAVE ENTRY POINT (0.0.13 SS cluster, stage 1).

The bug this closes: the whole save/resume stack reads state that NOTHING in production
writes — `save_brainstorm_progress` / `PipelineCheckpoint.mark_passed` / `persist_approach`
are orphans (tests + the playbook self-test only), so an interrupted brainstorm/pipeline is
UNRECOVERABLE today despite the machinery to recover it existing.

SS.S0 adds the one ungated entry point that snapshots the CURRENT session's full in-flight
state THROUGH those orphaned persistence functions, via MS.S1 atomic writes under the MS.S2
session scope, writing EXACTLY what the existing resume stack reads back:

  (headline) mid-brainstorm (approved approach + progress) → `session_save` → simulate a
      kill −9 (in-memory state cleared, fresh process) → the EXISTING resume path
      (`restore_brainstorm_progress` / `load_approved_approach` / `PipelineCheckpoint`)
      rehydrates EXACTLY what was saved.
  (orphans wired) the save routes through save_brainstorm_progress / persist_approach /
      mark_passed — they now HAVE a production caller (monkeypatch-spy through the save path).
  (ungated honesty) no approve/confirm/assume_yes param; no WriteGate submission; the MCP
      tool is a registered READ tool (ungated, auto-safe — like the session-family already),
      never a gated write.
  (empty / double-save) an empty session → an honest empty result (not an error); saving
      twice → the second snapshot wins atomically; both valid.
  (isolation) window A's save never captures window B's scoped state (MS.S2 scope).
  (secret-safety) the result carries keys + counts only, never state CONTENT; the saved
      files live under temp_local (footprint invariant).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import inspect
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

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

from mokata import session_save as SAVE  # the module under test (SS.S0)


def _repo(d, profile="standard"):
    init_repo(root=d, profile=profile, assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _pin(sid):
    """Pin THIS 'window' to a stable session identity (the id survives a window restart)."""
    os.environ[S.SESSION_ID_ENV] = sid
    S.reset_for_test()


def _mid_brainstorm(topic="add a caching layer"):
    """An APPROVED mid-brainstorm session (answered question + candidate approaches + a
    chosen/approved one) — the exact 'approved approach + progress' the headline saves."""
    s = BrainstormSession(topic)
    s.ask("read/write ratio?")
    s.answer("read-heavy")
    s.propose_approaches([
        Approach("cache-aside", "read-through cache", pros=["simple"], cons=["stale"]),
        Approach("write-through", "writes via cache", pros=["fresh"], cons=["slow writes"]),
    ])
    approve_with_lenses(s, "jas", "cache-aside")
    return s


def _in_progress(topic):
    """A NOT-approved in-progress session (progress only, no approval)."""
    s = BrainstormSession(topic)
    s.ask("q?")
    s.answer("a")
    return s


def run_cli(argv):
    buf = io.StringIO()
    old = sys.stdin
    sys.stdin = io.StringIO("")
    try:
        with redirect_stdout(buf):
            from mokata.cli import main
            rc = main(argv)
    finally:
        sys.stdin = old
    return rc, buf.getvalue()


class _Base(unittest.TestCase):
    def setUp(self):
        os.environ.pop(S.SESSION_ID_ENV, None)
        S.reset_for_test()

    def tearDown(self):
        os.environ.pop(S.SESSION_ID_ENV, None)
        S.reset_for_test()


# ===================================================================== THE headline test
class TestRegression(_Base):
    def test_ss_s0_regression(self):
        """Save mid-brainstorm state, simulate a kill −9, and prove the EXISTING resume path
        rehydrates EXACTLY what was saved — the data-loss headline."""
        with tempfile.TemporaryDirectory() as d:
            _pin("sessX")
            surface = _repo(d)
            s = _mid_brainstorm()

            res = SAVE.save_session(surface, brainstorm=s.to_dict(),
                                    passed=["brainstorm", "analysis"])
            self.assertFalse(res.empty)

            # --- simulate kill −9: in-memory identity + all objects gone, fresh process ---
            del s
            del surface
            _pin("sessX")                       # the resumed window keeps its identity
            fresh = Surface.load(d)

            # brainstorm progress rehydrates through the EXISTING reader, byte-exact
            bs = restore_brainstorm_progress(fresh.state)
            self.assertIsNotNone(bs)
            self.assertEqual(bs.topic, "add a caching layer")
            self.assertEqual(
                [(q.text, q.answer) for q in bs.answered_questions],
                [("read/write ratio?", "read-heavy")],
            )
            self.assertTrue(bs.approved)

            # approved approach rehydrates through the EXISTING downstream reader
            ho = load_approved_approach(fresh.state)
            self.assertIsNotNone(ho)
            self.assertEqual(ho.approach.name, "cache-aside")
            self.assertEqual(ho.approver, "jas")

            # pipeline checkpoint rehydrates through the EXISTING resume machinery
            cp = PipelineCheckpoint(fresh.state, S.current_run_id())
            self.assertEqual(cp.passed, ["brainstorm", "analysis"])
            # resumes at the first phase AFTER the last one saved (whatever the spine calls it)
            from mokata.brainstorm import PIPELINE_PHASES
            self.assertEqual(cp.resume_phase(),
                             PIPELINE_PHASES[PIPELINE_PHASES.index("analysis") + 1])


# ===================================================================== orphans wired
class TestOrphansWired(_Base):
    def test_save_routes_through_all_three_orphans(self):
        """The save path is a REAL production caller of each formerly-orphaned function."""
        with tempfile.TemporaryDirectory() as d:
            _pin("sessO")
            surface = _repo(d)
            s = _mid_brainstorm()

            seen = set()
            orig_sbp = SAVE.save_brainstorm_progress
            orig_pa = SAVE.persist_approach
            orig_mp = PipelineCheckpoint.mark_passed

            def spy_sbp(session, store):
                seen.add("save_brainstorm_progress")
                return orig_sbp(session, store)

            def spy_pa(session, store, **kw):
                seen.add("persist_approach")
                return orig_pa(session, store, **kw)

            def spy_mp(self, phase):
                seen.add("mark_passed")
                return orig_mp(self, phase)

            SAVE.save_brainstorm_progress = spy_sbp
            SAVE.persist_approach = spy_pa
            PipelineCheckpoint.mark_passed = spy_mp
            try:
                SAVE.save_session(surface, brainstorm=s.to_dict(), passed=["brainstorm"])
            finally:
                SAVE.save_brainstorm_progress = orig_sbp
                SAVE.persist_approach = orig_pa
                PipelineCheckpoint.mark_passed = orig_mp

            self.assertEqual(
                seen, {"save_brainstorm_progress", "persist_approach", "mark_passed"})

    def test_save_writes_only_under_temp_local(self):
        """Footprint invariant: persist_approach is called WITHOUT plans_dir, so the save
        touches only the temp_local state store — never `.mokata/plans/`."""
        with tempfile.TemporaryDirectory() as d:
            _pin("sessF")
            surface = _repo(d)
            SAVE.save_session(surface, brainstorm=_mid_brainstorm().to_dict(),
                              passed=["brainstorm"])
            self.assertIn("temp_local", surface.state.root)
            self.assertTrue(os.path.exists(
                surface.state.path(BRAINSTORM_PROGRESS_KEY)))
            self.assertTrue(os.path.exists(
                surface.state.path(APPROACH_STATE_KEY)))
            self.assertTrue(os.path.exists(
                surface.state.path(CHECKPOINT_PREFIX + S.current_run_id())))
            # the plans dir (committable, NOT temp_local) is untouched by a save
            self.assertFalse(os.path.exists(surface.plans_dir)
                             and os.listdir(surface.plans_dir))


# ===================================================================== ungated honesty
class TestUngated(_Base):
    def test_no_approval_shaped_param(self):
        sig = inspect.signature(SAVE.save_session)
        for banned in ("approve", "confirm", "assume_yes", "yes"):
            self.assertNotIn(banned, sig.parameters)

    def test_no_writegate_submission(self):
        from mokata import govern as G
        with tempfile.TemporaryDirectory() as d:
            _pin("sessG")
            surface = _repo(d)
            called = []
            orig = G.WriteGate.submit
            G.WriteGate.submit = lambda self, *a, **k: called.append(1)
            try:
                SAVE.save_session(surface, brainstorm=_mid_brainstorm().to_dict(),
                                  passed=["brainstorm"])
            finally:
                G.WriteGate.submit = orig
            self.assertEqual(called, [])

    def test_mcp_tool_is_a_registered_read_tool(self):
        from mokata import mcp_server as M
        self.assertIn("session_save", M.tool_names())
        self.assertIn("session_save", M.read_tool_names())
        self.assertNotIn("session_save", M.write_tool_names())

    def test_mcp_tool_signature_is_ungated(self):
        from mokata.mcp import tools_read as TR
        sig = inspect.signature(TR.session_save)
        for banned in ("approve", "confirm", "assume_yes", "yes"):
            self.assertNotIn(banned, sig.parameters)


# ===================================================================== empty / double-save
class TestEmptyAndDouble(_Base):
    def test_empty_session_is_an_honest_empty_result(self):
        with tempfile.TemporaryDirectory() as d:
            _pin("sessE")
            surface = _repo(d)
            res = SAVE.save_session(surface)      # nothing in-flight, nothing staged
            self.assertTrue(res.empty)
            self.assertEqual(res.to_dict()["saved"], {})
            self.assertTrue(res.to_dict()["ok"])  # empty is honest, NOT an error

    def test_double_save_last_wins(self):
        with tempfile.TemporaryDirectory() as d:
            _pin("sessD")
            surface = _repo(d)
            SAVE.save_session(surface, brainstorm=_in_progress("first").to_dict())
            r2 = SAVE.save_session(surface, brainstorm=_in_progress("second").to_dict())
            self.assertFalse(r2.empty)
            bs = restore_brainstorm_progress(surface.state)
            self.assertEqual(bs.topic, "second")   # the second snapshot wins atomically


# ===================================================================== session isolation
class TestIsolation(_Base):
    def test_one_window_save_never_captures_another_windows_state(self):
        with tempfile.TemporaryDirectory() as d:
            _pin("A")
            sA = _repo(d)
            SAVE.save_session(sA, brainstorm=_in_progress("A-topic").to_dict())

            _pin("B")
            sB = Surface.load(d)
            self.assertIsNone(restore_brainstorm_progress(sB.state))  # A's scope is invisible
            SAVE.save_session(sB, brainstorm=_in_progress("B-topic").to_dict())

            _pin("A")
            self.assertEqual(
                restore_brainstorm_progress(Surface.load(d).state).topic, "A-topic")
            _pin("B")
            self.assertEqual(
                restore_brainstorm_progress(Surface.load(d).state).topic, "B-topic")


# ===================================================================== secret-safety
class TestSecretSafety(_Base):
    def test_result_is_counts_only_never_content(self):
        with tempfile.TemporaryDirectory() as d:
            _pin("sessS")
            surface = _repo(d)
            s = _mid_brainstorm()
            res = SAVE.save_session(surface, brainstorm=s.to_dict(),
                                    passed=["brainstorm"])
            blob = json.dumps(res.to_dict())
            # NO state content may leak into the summary
            for secret in ("add a caching layer", "read-heavy", "cache-aside",
                           "read/write ratio?", "read-through cache"):
                self.assertNotIn(secret, blob)
            # counts ARE present (proof it summarised, not dumped)
            saved = res.to_dict()["saved"]
            self.assertIn(BRAINSTORM_PROGRESS_KEY, saved)
            self.assertIn(APPROACH_STATE_KEY, saved)
            self.assertEqual(saved[BRAINSTORM_PROGRESS_KEY]["answered"], 1)
            self.assertEqual(saved[BRAINSTORM_PROGRESS_KEY]["approaches"], 2)


# ===================================================================== MCP + CLI surfaces
class TestSurfaces(_Base):
    def test_mcp_tool_saves_and_reports(self):
        from mokata.mcp import tools_read as TR
        with tempfile.TemporaryDirectory() as d:
            _pin("sessM")
            _repo(d)
            out = TR.session_save(path=d, brainstorm=_mid_brainstorm().to_dict(),
                                  passed=["brainstorm"])
            self.assertFalse(out["empty"])
            self.assertIn(BRAINSTORM_PROGRESS_KEY, out["saved"])

    def test_cli_session_save_empty_is_clean(self):
        with tempfile.TemporaryDirectory() as d:
            _pin("sessC")
            _repo(d)
            rc, out = run_cli(["session", "save", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("session", out.lower())


if __name__ == "__main__":
    unittest.main()
