"""Stage 50 — session lifecycle: list / resume / pause-resume (incl. mid-brainstorm).

Built on the existing infra (PipelineCheckpoint, progress.list_runs/find_active_run,
BrainstormSession + the StateStore) — surfacing + a small serialization addition, not a new
engine. Inviolables: listing/inspecting is read-only (no stat/counter bump); the HARD-GATE
and phase gates still apply on resume; human-gated writes; degrade-clean; local-first.
"""

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata import MOKATA_DIR
from mokata.brainstorm import (
    Approach,
    BrainstormGateError,
    BrainstormSession,
    clear_brainstorm_progress,
    restore_brainstorm_progress,
    save_brainstorm_progress,
)
from mokata.cli import main
from mokata.config import Surface
from mokata.govern import PipelineCheckpoint
from mokata.memory import MemoryStore
from mokata.progress import list_sessions
from mokata.state import StateStore


def run_cli(argv):
    buf = io.StringIO()
    old = sys.stdin
    sys.stdin = io.StringIO("")
    try:
        with redirect_stdout(buf):
            rc = main(argv)
    finally:
        sys.stdin = old
    return rc, buf.getvalue()


def _repo(d, profile="standard"):
    from mokata.init import init_repo
    init_repo(root=d, profile=profile, assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _in_progress_session():
    s = BrainstormSession("slugify")
    s.ask("unicode or ascii-only?")
    s.answer("ascii-only")
    s.ask("hyphen or underscore?")
    s.answer("hyphen")
    s.propose_approaches([
        Approach("regex", "strip via regex", pros=["tiny"], cons=["edge cases"]),
        Approach("library", "use a slug lib", pros=["robust"], cons=["a dependency"]),
    ])
    return s   # deliberately NOT approved — mid-stream


# ----------------------------------------------- mid-brainstorm checkpoint (the key gap)
class TestBrainstormCheckpoint(unittest.TestCase):
    def test_in_progress_session_round_trips(self):
        s = _in_progress_session()
        restored = BrainstormSession.from_dict(s.to_dict())
        self.assertEqual(restored.topic, "slugify")
        self.assertEqual([(q.text, q.answer) for q in restored.answered_questions],
                         [(q.text, q.answer) for q in s.answered_questions])
        self.assertEqual([a.name for a in restored.approaches], ["regex", "library"])
        self.assertFalse(restored.approved)

    def test_hard_gate_still_holds_after_restore(self):
        restored = BrainstormSession.from_dict(_in_progress_session().to_dict())
        self.assertFalse(restored.can_emit_spec)
        with self.assertRaises(BrainstormGateError):
            restored.handoff()                       # no spec/handoff until approval

    def test_save_then_restore_via_state_store(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(os.path.join(d, "state"))
            self.assertIsNone(restore_brainstorm_progress(store))   # nothing yet
            save_brainstorm_progress(_in_progress_session(), store)
            restored = restore_brainstorm_progress(store)
            self.assertIsNotNone(restored)
            self.assertEqual(len(restored.answered_questions), 2)
            self.assertEqual([a.name for a in restored.approaches], ["regex", "library"])
            self.assertTrue(clear_brainstorm_progress(store))
            self.assertIsNone(restore_brainstorm_progress(store))

    def test_approved_session_round_trips_with_choice(self):
        s = _in_progress_session()
        s.approve("jas", "regex")
        restored = BrainstormSession.from_dict(s.to_dict())
        self.assertTrue(restored.approved)
        self.assertEqual(restored.chosen.name, "regex")
        self.assertTrue(restored.can_emit_spec)
        self.assertEqual(restored.handoff().approach.name, "regex")   # gate satisfied


# ----------------------------------------------- sessions listing (read-only)
class TestSessions(unittest.TestCase):
    def test_list_sessions_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(list_sessions(StateStore(os.path.join(d, "state"))), [])

    def test_list_sessions_reports_progress_and_resume_point(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(os.path.join(d, "state"))
            PipelineCheckpoint(store, "run-a").mark_passed("brainstorm")
            sessions = list_sessions(store)
            self.assertEqual(len(sessions), 1)
            s = sessions[0]
            self.assertEqual(s.run_id, "run-a")
            self.assertEqual(s.last_passed, "brainstorm")
            self.assertEqual(s.resume_phase, "analysis")   # the phase after the last passed
            self.assertFalse(s.complete)
            self.assertTrue(s.active)

    def test_cmd_sessions_lists_and_degrades_empty(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            rc, out = run_cli(["sessions", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("no runs", out.lower())          # friendly empty
            PipelineCheckpoint(surface.state, "run-x").mark_passed("brainstorm")
            rc, out = run_cli(["sessions", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("run-x", out)
            self.assertIn("analysis", out)                 # resume point


# ----------------------------------------------- resume preview (read-only; gates hold)
class TestResume(unittest.TestCase):
    def test_cmd_resume_no_run_degrades_clean(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            rc, out = run_cli(["resume", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("no run to resume", out.lower())

    def test_cmd_resume_picks_last_passed_phase(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            cp = PipelineCheckpoint(surface.state, "run-y")
            cp.mark_passed("brainstorm")
            cp.mark_passed("analysis")
            rc, out = run_cli(["resume", "run-y", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("resume at: 'strawman'", out)    # phase after the last passed
            self.assertIn("gates hold", out.lower())

    def test_resume_and_sessions_are_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, profile="full")
            cp = PipelineCheckpoint(surface.state, "run-z")
            cp.mark_passed("brainstorm")
            before_reads = MemoryStore.from_surface(Surface.load(d)).stats.reads
            before_cp = surface.state.read("pipeline_run__run-z")
            run_cli(["sessions", "--path", d])
            run_cli(["resume", "run-z", "--path", d])
            after_reads = MemoryStore.from_surface(Surface.load(d)).stats.reads
            self.assertEqual(after_reads, before_reads)              # no counter bumped
            self.assertEqual(surface.state.read("pipeline_run__run-z"), before_cp)  # unchanged


# ----------------------------------------------- mid-brainstorm CLI resume
class TestBrainstormCliResume(unittest.TestCase):
    def test_brainstorm_resumes_in_progress_and_keeps_hard_gate(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            save_brainstorm_progress(_in_progress_session(), surface.state)
            rc, out = run_cli(["brainstorm", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("resuming in-progress", out.lower())
            self.assertIn("slugify", out)
            self.assertIn("regex", out)                    # candidate approach surfaced
            self.assertIn("hard-gate", out.lower())        # the gate still holds


if __name__ == "__main__":
    unittest.main()
