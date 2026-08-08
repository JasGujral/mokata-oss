"""B-LIFE — completed-run display retirement (release 0.0.14, re-groom #11, live report 2026-07-15).

The bug: a FINISHED run rendered as CURRENT status forever. `progress.find_active_run` fell back to
the most-recent run even when done, and the statusline badge kept its strip for a bound finished run.
Accurate about the PAST, presented as the PRESENT.

Ship-based completion (spec amendment #2, coordinator directive — ledgered doc 02 B-LIFE row): a
complete pipeline CHECKPOINT means the spec emitted and the user is AT `develop` — a HEALTHY ACTIVE
state, NOT a finished run (develop → review → ship are tracked in the progress-event LOG, not the
checkpoint). "Finished" is therefore keyed on the terminal END-OF-RUN signal in the log —
`stage_enter: ship` (the strongest evidence available: `STAGE_PASS` is defined but never written and
there is no ship-completion event; see the report's Verified-from-code list).

The fix is DISPLAY-only (P17 — run state is NEVER deleted, pruned, or rewritten beyond one additive
`completed_at` stamp):
  * `PipelineCheckpoint.mark_completed` stamps `completed_at` when the run reaches `ship` (wired into
    `mokata progress mark ship`); additive JSON key, old `{run_id, passed}` readers unaffected.
  * `find_active_run` — incomplete-checkpoint runs keep priority; a spec-emitted (complete-checkpoint)
    run STAYS active; only a SHIPPED run is excluded.
  * `build_progress` with no active run but a most-recent shipped run reports
    "last run '<id>' completed <when> — no active run" + the existing PH-GATE.S0 recovery guidance.
  * `run_resolver.resolve_badge_run` retires a bound / live-narrowed SHIPPED run -> clean badge.
  * Explicit run_id / resume paths still render a shipped run in full (the P17 negative).

Legacy boundary (accepted, no heuristics/backfill): a run that truly finished BEFORE this log existed
(or whose log was pruned) has no `stage_enter: ship` — it is NOT retired, it stays displayable. That
is the honest boundary; `test_legacy_finished_without_ship_event_stays_active` pins it.
"""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

import _support  # noqa: F401  (puts src/ on the path)

from mokata import session_registry as SR
from mokata.run_resolver import bind_session_run, resolve_badge_run
from mokata.brainstorm import PIPELINE_PHASES
from mokata.cli import main
from mokata.config import Surface
from mokata.govern.resume import CHECKPOINT_PREFIX, PipelineCheckpoint
from mokata.init import init_repo
from mokata.progress import (
    NO_RUN_MESSAGE,
    build_progress,
    build_stage_badge,
    list_runs,
    render_progress,
)
from mokata.progress_events import STAGE_ENTER, ProgressLog
from mokata.state import StateStore
from mokata.tdd_state import state_dir


def _silent(_):
    pass


def _store(d):
    # RUN-ID-DRIFT — the REAL state-dir layout (`<d>/.mokata/temp_local/state`), because resolution
    # is now rooted at the repo: `build_progress(store, root=d)` must find the same directory the
    # checkpoints were written to.
    return StateStore(state_dir(d))


def _active(d, store):
    """The run the progress surface reports as current — THE resolver plus B-LIFE display
    retirement, which is where `find_active_run`'s retirement half now lives."""
    return build_progress(store, root=d).run_id


def _checkpoint(store, run_id, passed_phases):
    """A run with `passed_phases` on disk (via mark_passed, the real per-gate path)."""
    cp = PipelineCheckpoint(store, run_id)
    if not passed_phases:
        store.write(CHECKPOINT_PREFIX + run_id, {"run_id": run_id, "passed": []})
    for p in passed_phases:
        cp.mark_passed(p)
    return cp


def _ship(store, run_id):
    """Record the terminal ship signal for `run_id` EXACTLY as `mokata progress mark ship`
    (`cmd_progress_mark`) does: append a `stage_enter: ship` event + stamp `completed_at`."""
    ProgressLog.from_state_dir(store.root).append_event(STAGE_ENTER, "ship", run_id=run_id)
    PipelineCheckpoint(store, run_id).mark_completed()


def _repo(d, profile="standard"):
    init_repo(root=d, profile=profile, assume_yes=True, out=_silent)
    return Surface.load(d)


def _persist_run(root, run_id, passed=()):
    """A run persisted on disk (NOT live — no registry entry)."""
    cp = PipelineCheckpoint(Surface.load(root).state, run_id)
    cp.ensure_registered()
    for p in passed:
        cp.mark_passed(p)


def _register_live_run(root, run_id, passed=()):
    """A run that is persisted AND live (an alive pid rooted at this repo in the MS.S2 registry)."""
    _persist_run(root, run_id, passed)
    store = StateStore(state_dir(root))
    reg = store.read(SR.SESSION_REGISTRY_KEY) or {"sessions": {}}
    reg.setdefault("sessions", {})[run_id] = {
        "session_id": run_id, "started_at": "2026-07-15T00:00:00Z",
        "pid": os.getpid(), "repo_root": os.path.realpath(root),
        "last_seen": "2026-07-15T00:00:00Z", "phase": None, "scope": None,
    }
    store.write(SR.SESSION_REGISTRY_KEY, reg)


# =========================================================== the completed_at stamp (ship-keyed)
class TestCompletedStamp(unittest.TestCase):
    def test_spec_emitted_run_is_not_stamped(self):
        # a complete CHECKPOINT (spec emitted) is AT develop — NOT finished, so no completed_at.
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            _checkpoint(store, "r1", list(PIPELINE_PHASES))
            self.assertIsNone(store.read(CHECKPOINT_PREFIX + "r1").get("completed_at"))

    def test_ship_stamps_completed_at(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            _checkpoint(store, "r1", list(PIPELINE_PHASES))
            _ship(store, "r1")                              # reaches ship -> stamped
            data = store.read(CHECKPOINT_PREFIX + "r1")
            self.assertIn("completed_at", data)
            self.assertTrue(data["completed_at"])

    def test_stamp_is_additive_and_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            _checkpoint(store, "r1", list(PIPELINE_PHASES))
            cp = PipelineCheckpoint(store, "r1")
            self.assertTrue(cp.mark_completed())            # first stamp
            first = store.read(CHECKPOINT_PREFIX + "r1")["completed_at"]
            self.assertFalse(PipelineCheckpoint(store, "r1").mark_completed())   # idempotent
            data = store.read(CHECKPOINT_PREFIX + "r1")
            self.assertEqual(data["completed_at"], first)   # not re-stamped
            # additive: the pre-existing keys are byte-identical; old readers see a complete run
            self.assertEqual(data["run_id"], "r1")
            self.assertEqual(data["passed"], list(PIPELINE_PHASES))
            self.assertTrue(PipelineCheckpoint(store, "r1").is_complete())

    def test_cli_progress_mark_ship_stamps_via_the_real_funnel(self):
        # exercise the single writer (cmd_progress_mark) end to end.
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            surface = Surface.load(d)
            _checkpoint(surface.state, "run-a", list(PIPELINE_PHASES))
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["progress", "mark", "ship", "--path", d])
            self.assertEqual(rc, 0)
            self.assertTrue(surface.state.read(CHECKPOINT_PREFIX + "run-a").get("completed_at"))


# ====================================================== the resolver + display: ship-based retirement
class TestFindActiveRetirement(unittest.TestCase):
    def test_shipped_run_is_not_active(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            _checkpoint(store, "done", list(PIPELINE_PHASES))
            _ship(store, "done")
            self.assertIsNone(_active(d, store))            # shipped -> retired, no fallback

    def test_spec_emitted_unshipped_run_stays_active(self):
        # THE conflation pin: a complete checkpoint with no ship signal is AT develop -> ACTIVE.
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            _checkpoint(store, "at-develop", list(PIPELINE_PHASES))
            self.assertEqual(_active(d, store), "at-develop")

    def test_new_incomplete_after_shipped_wins(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            _checkpoint(store, "aaa-done", list(PIPELINE_PHASES))
            _ship(store, "aaa-done")
            _checkpoint(store, "zzz-live", list(PIPELINE_PHASES[:1]))
            # RUN-ID-DRIFT — this is now the UNSHIPPED NARROWING, not a pick: the shipped run is
            # excluded and exactly one candidate is left, so the answer is forced rather than
            # chosen. Two UNSHIPPED runs here would resolve to nothing (see test_run_id_drift).
            self.assertEqual(_active(d, store), "zzz-live")

    def test_incomplete_only_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            _checkpoint(store, "live", list(PIPELINE_PHASES[:2]))
            self.assertEqual(_active(d, store), "live")

    def test_legacy_finished_without_ship_event_stays_active(self):
        # binding #5 — a truly-finished run with NO ship event in the log is NOT retired (honest
        # boundary: no heuristic decides it shipped). It remains displayable as the active run.
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            _checkpoint(store, "legacy", list(PIPELINE_PHASES))   # complete, no ship event
            self.assertEqual(_active(d, store), "legacy")


# =========================================================== build_progress: the completed message
class TestCompletedMessage(unittest.TestCase):
    def test_shipped_run_reports_finished_then_not_current(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            _checkpoint(store, "run-42", list(PIPELINE_PHASES))
            _ship(store, "run-42")
            p = build_progress(store)                       # no explicit run_id
            self.assertFalse(p.active)
            self.assertIn("run-42", p.message)
            self.assertIn("completed", p.message.lower())
            self.assertIn("no active run", p.message.lower())
            # keeps the PH-GATE.S0 recovery guidance (how to start / resume a tracked run)
            self.assertIn("/mokata:resume", p.message)
            self.assertIn("/mokata:brainstorm", p.message)

    def test_message_carries_the_timestamp_when_present(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            _checkpoint(store, "run-42", list(PIPELINE_PHASES))
            _ship(store, "run-42")
            when = store.read(CHECKPOINT_PREFIX + "run-42")["completed_at"]
            self.assertIn(when, build_progress(store).message)

    def test_shipped_without_timestamp_is_graceful(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            # a run whose log records ship but whose checkpoint predates the completed_at stamp
            _checkpoint(store, "old", list(PIPELINE_PHASES))
            ProgressLog.from_state_dir(store.root).append_event(STAGE_ENTER, "ship", run_id="old")
            self.assertIsNone(store.read(CHECKPOINT_PREFIX + "old").get("completed_at"))
            p = build_progress(store)
            self.assertFalse(p.active)
            self.assertIn("old", p.message)
            self.assertIn("completed", p.message.lower())   # "completed" without a when — no crash
            self.assertTrue(render_progress(p))             # renders, never raises

    def test_no_runs_at_all_is_the_plain_no_run_message(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(build_progress(_store(d)).message, NO_RUN_MESSAGE)

    def test_progress_cli_reports_completed_after_ship(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            surface = Surface.load(d)
            _checkpoint(surface.state, "run-42", list(PIPELINE_PHASES))
            _ship(surface.state, "run-42")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["progress", "--path", d])
            out = buf.getvalue()
            self.assertEqual(rc, 0)
            self.assertIn("completed", out.lower())
            self.assertIn("no active run", out.lower())
            self.assertNotIn("you are here", out)           # NOT the live pipeline strip


# =========================================================== the conflation negative (binding #6)
class TestSpecEmittedStaysActive(unittest.TestCase):
    def test_at_develop_run_is_active_in_progress_and_badge(self):
        """A spec-emitted (complete-checkpoint) run with NO ship signal is AT develop — it must stay
        ACTIVE in BOTH surfaces: `mokata progress` shows the run, and the badge sits at ›develop‹.
        This pins the emit≠finished conflation forever (the 23 downstream badge tests depend on it)."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _register_live_run(d, "run-a", passed=list(PIPELINE_PHASES))   # complete, not shipped
            # progress: the run is active (the full pipeline strip, not the completed message)
            p = build_progress(Surface.load(d).state, root=d)
            self.assertTrue(p.active)
            self.assertEqual(p.run_id, "run-a")
            # badge: sits at develop (session-resolved), never retired
            badge = build_stage_badge(Surface.load(d), session_id="sessB")
            self.assertIn("›develop‹", badge)


# =========================================================== the P17 negative: explicit id shows today
class TestExplicitRunStillShowsToday(unittest.TestCase):
    def test_explicit_shipped_run_renders_full_pipeline(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            _checkpoint(store, "done", list(PIPELINE_PHASES))
            _ship(store, "done")
            p = build_progress(store, run_id="done")        # EXPLICIT id — retirement does not apply
            self.assertTrue(p.active)
            self.assertTrue(p.complete)
            self.assertEqual(p.done, len(PIPELINE_PHASES))
            self.assertTrue(all(s.status == "done" for s in p.steps))
            self.assertIn("run complete", render_progress(p).lower())


# =========================================================== badge: retire SHIPPED runs only
class TestBadgeRetirement(unittest.TestCase):
    def test_bound_shipped_run_gives_clean_badge(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "done", passed=list(PIPELINE_PHASES))
            _ship(StateStore(state_dir(d)), "done")
            bind_session_run(d, "sessB", "done")
            self.assertIsNone(resolve_badge_run(d, "sessB"))       # tier (i) retired
            self.assertEqual(build_stage_badge(Surface.load(d), session_id="sessB"), "mokata")

    def test_live_narrowed_shipped_run_gives_clean_badge(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _register_live_run(d, "done", passed=list(PIPELINE_PHASES))
            _ship(StateStore(state_dir(d)), "done")
            self.assertIsNone(resolve_badge_run(d, "unbound"))     # tier (ii) retired
            self.assertEqual(build_stage_badge(Surface.load(d), session_id="unbound"), "mokata")

    def test_bound_unshipped_run_still_shows_byte_identical(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _register_live_run(d, "live", passed=["brainstorm"])
            bind_session_run(d, "sessB", "live")
            self.assertEqual(resolve_badge_run(d, "sessB"), "live")
            self.assertIn("›spec‹", build_stage_badge(Surface.load(d), session_id="sessB"))


# =========================================================== resume works · nothing deleted
class TestResumeAndPreservation(unittest.TestCase):
    def test_resume_of_shipped_run_works_and_deletes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            _checkpoint(store, "done", list(PIPELINE_PHASES))
            _ship(store, "done")
            before = store.read(CHECKPOINT_PREFIX + "done")
            # explicit-id (the resume view's read) still renders the shipped run in full
            self.assertTrue(build_progress(store, run_id="done").complete)
            after = store.read(CHECKPOINT_PREFIX + "done")
            self.assertEqual(before, after)                 # not rewritten by a read
            # not deleted, not pruned — the run still exists on disk and in the run list
            self.assertIsNotNone(store.read(CHECKPOINT_PREFIX + "done"))
            self.assertIn("done", list_runs(store))
            self.assertTrue(os.path.exists(
                os.path.join(state_dir(d), CHECKPOINT_PREFIX + "done.json")))


if __name__ == "__main__":
    unittest.main()
