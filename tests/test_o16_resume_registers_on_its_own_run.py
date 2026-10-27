"""O16 — RESUME-REGISTERS-ON-ANOTHER-SESSIONS-RUN (Jas 2026-10-27: "registering session on later
resume").

The MCP server cannot see Claude Code's session id, so it resolves its run through
`_session_bound_run`. That function carried a RE-ENTRY carve-out: when the session's own run is
still BARE (brainstorm before approve) and exactly one OTHER run holds pipeline evidence, hand
over to that other run — written for a fresh window re-entering an idle pipeline.

Measured before the fix, the carve-out also fired for:
  * a LIVE parallel window's run   → this session's approve/spec writes landed on its run,
  * a SHIPPED run                  → writes refused or attached to a finished pipeline,
  * a RESUMED conversation         → "later resume": the session that registered run A came back
                                     and the MCP side resolved it to someone else's run,
while the hook side (`resolve_run(session_id=…)`) said A all along — the two halves disagreed.

Each case is a business-level test: it fails if the carve-out is restored as it was.
"""
from __future__ import annotations

import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import run_intent as RI
from mokata import run_resolver as RR
from mokata import session_index as SI
from mokata.state import StateStore
from mokata.tdd_state import state_dir

A, B = "runA00000001", "runB00000002"


def _repo(b_shipped=False, s_at=2000.0, t_at=1000.0):
    root = tempfile.mkdtemp(prefix="mokata-o16-resume-")
    os.makedirs(os.path.join(root, ".mokata"))
    with open(os.path.join(root, ".mokata", "manifest.json"), "w", encoding="utf-8") as fh:
        fh.write('{"schema": 1}')
    st = StateStore(state_dir(root))
    for rid in (A, B):
        st.write(RR.CHECKPOINT_PREFIX + rid, {"run_id": rid})
    st.write(RR.SPEC_PREFIX + B, {"run_id": B})          # T's run is past spec: it holds evidence
    if b_shipped:                                          # B's ship-completion event
        from mokata.progress_events import RUN_FINISHED, ProgressLog
        ProgressLog.from_state_dir(state_dir(root)).append_event(
            RUN_FINISHED, "ship", run_id=B, data={"landing": "merged"})
    RI.bind_registered(root, "S", A)                       # S's OWN registration (run-bind hook)
    RI.bind_registered(root, "T", B)
    for rid, at in ((A, s_at), (B, t_at)):                 # when each run was registered
        st.write(RR.CHECKPOINT_PREFIX + rid, {"run_id": rid, "passed": [], "created_at": at})
    return root


class TheSessionKeepsTheRunItRegistered(unittest.TestCase):

    def test_a_LIVE_parallel_window_never_lends_its_run(self):
        root = _repo()
        SI.on_start(root, "T", "startup", root)            # T is live: open segment, fresh row
        self.assertEqual(A, RR._session_bound_run(root, "S"))

    def test_a_RESUMED_session_continues_its_own_run(self):
        """The reported bug: S comes back days later; T's pipeline has since ended."""
        root = _repo()
        SI.on_start(root, "T", "startup", root)
        SI.on_end(root, "T", "exit")
        RI.mark_resumed(root, "S")                         # O10's record of a kept-id resume
        self.assertEqual(A, RR._session_bound_run(root, "S"))

    def test_a_SHIPPED_pipeline_is_nobodys_re_entry_target(self):
        root = _repo(b_shipped=True)
        self.assertEqual(A, RR._session_bound_run(root, "S"))

    def test_the_mcp_side_and_the_hook_side_agree_after_a_resume(self):
        root = _repo()
        RI.mark_resumed(root, "S")
        hook = RR.resolve_run(root, session_id="S").run_id
        self.assertEqual(hook, RR._session_bound_run(root, "S"))


class AWindowLeftOpenKeepsItsRun(unittest.TestCase):
    """O18 (Jas 2026-10-27: "or left the claude chat open for long - maybe connection broke"):
    S registered A, went idle (no resume, no live heartbeat); ANOTHER window then started B,
    reached spec and closed. S comes back and amends: B is not a pipeline S re-entered — it was
    started after S's own registration — so S keeps A."""

    def test_a_pipeline_started_after_my_registration_is_not_my_re_entry_target(self):
        root = _repo(s_at=1000.0, t_at=2000.0)
        SI.on_start(root, "T", "startup", root)
        SI.on_end(root, "T", "exit")
        self.assertEqual(A, RR._session_bound_run(root, "S"))
        self.assertEqual(A, RR.resolve_run(root, session_id="S").run_id,
                         "the hook side agrees")


class AnOldPipelineNeverLooksNew(unittest.TestCase):
    """The independent review of O18's first cut: claim `at` is re-stamped by an idempotent
    re-registration and by a resume's carry, so an OLD pipeline looked newer than the fresh window
    re-entering it — and so is any per-claim time. The comparison reads the RUN's write-once
    checkpoint `created_at`; a legacy checkpoint has no age and keeps the old carve-out."""

    def test_the_owner_re_registering_its_run_does_not_make_it_new(self):
        root = _repo(s_at=1000.0, t_at=500.0)
        self.assertIsNone(RR._session_bound_run(root, "S"), "control: re-entry hands over")
        RI.bind_registered(root, "T", B)                   # session_save(register, run_id=B) again
        self.assertIsNone(RR._session_bound_run(root, "S"))

    def test_a_resume_of_the_owner_under_a_new_id_does_not_make_it_new(self):
        root = _repo(s_at=1000.0, t_at=500.0)
        RI.carry_claim(root, "T2", "T", now=1500.0)
        self.assertIsNone(RR._session_bound_run(root, "S"))

    def test_another_session_registering_the_old_run_does_not_make_it_new(self):
        root = _repo(s_at=1000.0, t_at=500.0)
        RI.bind_registered(root, "U", B)
        self.assertIsNone(RR._session_bound_run(root, "S"))

    def test_the_owner_moving_away_and_back_does_not_make_it_new(self):
        root = _repo(s_at=1000.0, t_at=500.0)
        RI.bind_registered(root, "T", A + "x")
        RI.bind_registered(root, "T", B)
        self.assertIsNone(RR._session_bound_run(root, "S"))

    def test_a_legacy_checkpoint_has_no_age_and_keeps_the_old_carve_out(self):
        root = _repo(s_at=1000.0, t_at=500.0)
        st = StateStore(state_dir(root))
        st.write(RR.CHECKPOINT_PREFIX + B, {"run_id": B, "passed": []})   # no created_at
        RI.record_intent(root, "T", "slash")                              # creator moved on
        RI.bind_registered(root, "U", B)                                  # a LATER claimant
        self.assertIsNone(RR._session_bound_run(root, "S"), "a claimant's time is not B's age")

    def test_registering_stamps_the_checkpoint_once(self):
        from mokata.config import Surface
        from mokata.govern.resume import PipelineCheckpoint
        from mokata.init import init_repo
        d = tempfile.mkdtemp()
        init_repo(root=d, profile="standard", assume_yes=True, out=lambda *a: None)
        cp = PipelineCheckpoint(Surface.load(d).state, "runZ")
        self.assertTrue(cp.ensure_registered())
        first = StateStore(state_dir(d)).read(RR.CHECKPOINT_PREFIX + "runZ")["created_at"]
        cp2 = PipelineCheckpoint(Surface.load(d).state, "runZ")
        self.assertFalse(cp2.ensure_registered())
        cp2.mark_passed("approve")
        after = StateStore(state_dir(d)).read(RR.CHECKPOINT_PREFIX + "runZ")
        self.assertEqual((first, ["approve"]), (after["created_at"], after["passed"]))

    def test_a_run_first_written_by_mark_passed_is_stamped_too(self):
        st = StateStore(state_dir(_repo()))
        from mokata.govern.resume import PipelineCheckpoint
        PipelineCheckpoint(st, "runE").mark_passed("brainstorm")       # emit / phase-gate path
        self.assertIn("created_at", st.read(RR.CHECKPOINT_PREFIX + "runE"))

    def test_an_object_loaded_before_registration_never_erases_the_age(self):
        st = StateStore(state_dir(_repo()))
        from mokata.govern.resume import PipelineCheckpoint
        stale = PipelineCheckpoint(st, "runY")
        PipelineCheckpoint(st, "runY").ensure_registered()
        first = st.read(RR.CHECKPOINT_PREFIX + "runY")["created_at"]
        stale.mark_passed("brainstorm")
        self.assertEqual(first, st.read(RR.CHECKPOINT_PREFIX + "runY").get("created_at"))


class TheReEntryCarveOutStillHolds(unittest.TestCase):
    """The control: the shape the carve-out was written for must still hand over — a FRESH
    window (never resumed) re-entering an idle, unshipped pipeline whose session has ended."""

    def test_a_fresh_window_re_entering_an_idle_pipeline_resolves_to_that_pipeline(self):
        root = _repo()
        SI.on_start(root, "T", "startup", root)
        SI.on_end(root, "T", "exit")
        self.assertIsNone(RR._session_bound_run(root, "S"))
        self.assertEqual(B, RR.resolve_run(root).run_id)


if __name__ == "__main__":                                 # pragma: no cover
    unittest.main()
