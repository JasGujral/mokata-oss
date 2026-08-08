"""RUN-ID-DRIFT (OSS #44) — one resolver, one answer, and an absent answer that says so.

The live report: five tracked runs drifted apart inside ONE session — one surface reading
`[2/7] complete` while calls stamped into "a different, unstarted run", and review verdicts
recorded against a third.

The mechanism, reproduced in `TestTheDrift` below: a run id IS a session id (`session.py`), minted
per PROCESS, so every process that registers mints its own run — and mokata then had TWO resolvers
answering "which run is this?".  `progress.find_active_run` SCANNED and PICKED ("the first
incomplete-checkpoint run"), which over `uuid4().hex` ids is a lexicographic, i.e. ARBITRARY, run;
`badge_run` resolved session-awarely but its own docstring said it "sits BESIDE `find_active_run`".
Beside-ness was the bug.

What this suite pins, in order:

  1. THE INVARIANT — for any repo state, every surface either names the SAME run or abstains. No two
     surfaces ever name DIFFERENT run ids.  (`TestOneAnswer`)
  2. CLASS 1 — "here is your run", "I cannot tell which", and "there is no run" have three
     representations, and the two unresolved ones say different things to the human.
  3. The rungs that make an answer possible where the scan had none: OWN (this process's own
     registered run) and UNSHIPPED (what soundly survives of `find_active_run` — its RETIREMENT
     half, which narrows; never its PICK half).
  4. The refusals: several live runs, or two genuine pipelines, resolve to NOTHING rather than to a
     guess — on the badge, on `progress`, and on the stage-mark WRITE that OSS #44 was reported on.
  5. Eager MCP registration, so the LIVE rung has something to narrow with in the window that has
     not yet called a mokata tool.
  6. Sticky pid, so a process sharing a pinned session id cannot erase a live sibling's liveness.

Business-level asserts on resolutions and rendered output, never on internals that don't decide
anything.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

import _support  # noqa: F401  (puts src/ on the path)

from mokata import progress, run_resolver as RR
from mokata import session_registry as SR
from mokata import session as S
from mokata.brainstorm import PIPELINE_PHASES
from mokata.cli import main
from mokata.config import Surface
from mokata.govern.resume import CHECKPOINT_PREFIX, PipelineCheckpoint
from mokata.init import init_repo
from mokata.progress_events import STAGE_ENTER, ProgressLog
from mokata.state import StateStore
from mokata.tdd_state import root_of_state_dir, state_dir


# --------------------------------------------------------------------------- fixtures
def _repo(d):
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _run(root, run_id, passed=()):
    """A run with a checkpoint on disk — what RUN-REG writes at protocol start."""
    cp = PipelineCheckpoint(Surface.load(root).state, run_id)
    cp.ensure_registered()
    for p in passed:
        cp.mark_passed(p)


def _live(root, run_id, passed=(), pid=None):
    """A run that is ALSO live: a registry entry with an alive pid rooted at this repo — what the
    MCP server writes for its window (eagerly, since this stage)."""
    _run(root, run_id, passed)
    store = StateStore(state_dir(root))
    reg = store.read(SR.SESSION_REGISTRY_KEY) or {"sessions": {}}
    reg.setdefault("sessions", {})[run_id] = {
        "session_id": run_id, "started_at": "2026-08-05T00:00:00Z",
        "pid": os.getpid() if pid is None else pid,
        "repo_root": os.path.realpath(root),
        "last_seen": "2026-08-05T00:00:00Z", "phase": None, "scope": None,
    }
    store.write(SR.SESSION_REGISTRY_KEY, reg)


def _evidence(root, run_id):
    """Pipeline EVIDENCE for `run_id` — an approved approach, i.e. "the pipeline is here"."""
    Surface.load(root).state.write("approved_approach__" + run_id, {
        "schema_version": 1, "phase": "brainstorm", "topic": "t",
        "approach": {"name": "A1", "summary": "x", "tradeoffs": [], "decisions": []},
        "answered_questions": [], "grounding": {}, "approver": "jas",
        "approved_at": "2026-08-05T00:00:00Z",
        "prior_art": {"ran": True, "approach": "A1", "findings": [], "verdict": "none"},
        "domains": []})


def _ship(surface, run_id):
    ProgressLog.from_surface(surface).append_event(STAGE_ENTER, "ship", run_id=run_id)


class _NoPin(unittest.TestCase):
    """`MOKATA_SESSION_ID` short-circuits the whole ladder, and a developer's shell may carry one."""

    def setUp(self):
        self._pin = os.environ.pop(RR.PIN_ENV, None)
        S.reset_for_test()

    def tearDown(self):
        if self._pin is not None:
            os.environ[RR.PIN_ENV] = self._pin
        S.reset_for_test()


# ============================================================ 1 · the drift, reproduced
class TestTheDrift(_NoPin):
    def test_the_scan_that_picked_is_gone(self):
        """`progress.find_active_run` — the scanner OSS #44 was reported on — no longer exists.

        Deleting it IS the fix: while it existed, any surface could reach for a second answer, and
        three of them did. This is the pin that stops it coming back under the same name."""
        self.assertFalse(hasattr(progress, "find_active_run"))

    def test_five_bare_runs_used_to_drift_and_now_do_not(self):
        """OSS #44's exact shape: five registered runs, one of them the real pipeline.

        The old scan returned the LEXICOGRAPHICALLY first incomplete run — reproduced verbatim below
        — so `progress` reported one run while the pipeline was another. Every surface now names the
        one resolved run, or abstains; none names a different one."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            for rid in ("aaa-bare", "bbb-bare", "ccc-bare", "ddd-bare"):
                _live(d, rid)
            _live(d, "zzz-pipeline", passed=PIPELINE_PHASES[:2])
            _evidence(d, "zzz-pipeline")           # the run the user is actually in

            # PIN THE BUG — the deleted scan picks the alphabetically-first UNSTARTED run.
            self.assertEqual(_old_find_active_run(surface.state), "aaa-bare")

            # NEW — every surface agrees on the pipeline, because EVIDENCE says where the work is.
            for label, got in _every_surface(d).items():
                self.assertEqual(got, "zzz-pipeline", f"{label} drifted")

    def test_the_stage_mark_lands_in_the_resolved_run(self):
        """"calls stamped into a different, unstarted run" — the writing half of the report.

        `mokata progress mark` used to stamp wherever the scan landed. It now stamps into the run
        every reader reports, which is the only way a `[2/7]` and a stage mark can mean one thing."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _live(d, "aaa-bare")
            _live(d, "zzz-pipeline", passed=PIPELINE_PHASES[:2])
            _evidence(d, "zzz-pipeline")

            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["progress", "mark", "develop", "--path", d]), 0)

            marks = [e for e in ProgressLog.from_surface(surface).read_events()
                     if e.get("type") == STAGE_ENTER]
            self.assertEqual([e["run_id"] for e in marks], ["zzz-pipeline"])
            self.assertEqual(progress.build_progress(surface.state, root=d).run_id, "zzz-pipeline")


# ============================================================ 2 · THE invariant
def _every_surface(root, session_id=None):
    """What each surface resolves, side by side — the table the fix is proved on.

    Deliberately reached through the SURFACES (the progress view, the badge, the verdict key, the
    spec's run-scoped store, the gate hook, the bundle) rather than by calling `resolve_run` six
    times, which would prove only that a function is deterministic."""
    from mokata import gate_hook, session_bundle
    from mokata.cli_commands.spec import _run_scoped_store
    from mokata.progress_events import _resolve_verdict_run
    surface = Surface.load(root)
    gate = gate_hook.resolve_run(root)
    _store, spec_run, _err = _run_scoped_store(surface)
    return {
        "progress": progress.build_progress(surface.state, root=root).run_id,
        "sessions (active)": next(
            (s.run_id for s in progress.list_sessions(surface.state, root=root) if s.active), None),
        "badge": RR.resolve_badge_run(root, session_id),
        "review verdict": _resolve_verdict_run(surface),
        "spec / evidence": spec_run,
        "gate hook": gate.run_id,
        "session bundle": session_bundle._resume_summary(surface.state, None, root=root)["run_id"],
    }


class TestOneAnswer(_NoPin):
    """★ THE INVARIANT. Every surface names the SAME run or abstains — never a different one.

    This is the property the stage exists for, and it is stronger than "the resolver is correct":
    a surface is allowed to show less (the badge declines a run this session is not attached to),
    but it is never allowed to name someone else's run. A second resolver could not guarantee this;
    one resolver plus display FILTERS can, because a filter subtracts and cannot substitute."""

    def _assert_agrees(self, root, session_id=None):
        named = {k: v for k, v in _every_surface(root, session_id).items() if v is not None}
        self.assertLessEqual(len(set(named.values())), 1,
                             f"surfaces named different runs: {named}")
        return next(iter(set(named.values())), None)

    def test_agree_on_a_single_run(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _live(d, "solo", passed=PIPELINE_PHASES[:1])
            self.assertEqual(self._assert_agrees(d), "solo")

    def test_agree_when_five_are_live(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            for i in range(5):
                _live(d, f"run-{i}")
            _evidence(d, "run-3")
            self.assertEqual(self._assert_agrees(d), "run-3")

    def test_agree_when_nothing_can_be_resolved(self):
        """Five bare runs, none of them this process's, none holding evidence: EVERY surface
        abstains. Abstention is agreement — what must never happen is two different names."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            for i in range(5):
                _live(d, f"run-{i}")
            self.assertIsNone(self._assert_agrees(d))

    def test_agree_after_a_clear_leaves_a_dead_run(self):
        """`/clear` writes no binding and the run stays on disk (P17). The badge declines it; the
        gate and the verdict key still name it. Declining is not disagreeing."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _run(d, "yesterday", passed=PIPELINE_PHASES[:1])     # on disk, NOT live
            self.assertIsNone(RR.resolve_badge_run(d, "fresh-session"))
            self.assertEqual(self._assert_agrees(d, "fresh-session"), "yesterday")


# ============================================================ 3 · Class 1
class TestAnAbsentAnswerSaysWhichKind(_NoPin):
    """An absent answer is not an answer, and the two kinds of absence are not each other.

    Before this stage every resolver returned `Optional[str]`, so "I cannot tell which of these five
    you mean" and "there is no run here at all" were the same value — and the surfaces that could
    not act on `None` filled it in by picking. That pick is the defect."""

    def test_no_runs_and_ambiguous_are_different_results(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            empty = RR.resolve_run(d)
            self.assertIsNone(empty.run_id)
            self.assertFalse(empty.ambiguous)
            self.assertEqual(empty.candidates, ())

            _live(d, "a")
            _live(d, "b")
            amb = RR.resolve_run(d)
            self.assertIsNone(amb.run_id)
            self.assertTrue(amb.ambiguous)
            self.assertEqual(amb.candidates, ("a", "b"))

    def test_the_two_absences_tell_the_human_different_things(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            self.assertIn("start one", RR.unresolved_reason(RR.resolve_run(d)))

            _live(d, "a")
            _live(d, "b")
            says = RR.unresolved_reason(RR.resolve_run(d))
            self.assertIn("will not guess", says)
            self.assertIn("--run", says)          # the remedy, named
            self.assertIn("a, b", says)           # the candidates, named

    def test_resolved_and_unresolved_never_share_a_representation(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _live(d, "solo")
            res = RR.resolve_run(d)
            self.assertTrue(res.resolved)
            self.assertFalse(res.ambiguous)
            self.assertEqual(res.run_id, "solo")

    def test_progress_says_which_kind_and_lists_the_candidates(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _live(d, "aaa")
            _live(d, "bbb")
            with redirect_stdout(io.StringIO()) as out:
                self.assertEqual(main(["progress", "--path", d]), 0)
            printed = out.getvalue()
            self.assertIn("will not guess", printed)
            self.assertIn("aaa", printed)
            self.assertIn("bbb", printed)
            self.assertNotIn("you are here", printed)   # NOT a run's pipeline strip


# ============================================================ 4 · the rungs
class TestTheOwnRung(_NoPin):
    """The rung the scanner structurally could not have: THIS PROCESS's own registered run.

    `find_active_run` saw a state store and nothing about its caller, so amid five live runs it
    scanned for someone else's. A process that registered a run IS that run's driver."""

    def test_a_process_resolves_its_own_run_among_five(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            for i in range(4):
                _live(d, f"other-{i}")
            S.set_for_test(S.Session("mine", "2026-08-05T00:00:00Z", 0.0, os.getpid()))
            _live(d, "mine")
            res = RR.resolve_run(d)
            self.assertEqual(res.run_id, "mine")
            self.assertEqual(res.basis, RR.BASIS_OWN)
            self.assertTrue(res.attached)

    def test_a_cli_process_that_registered_nothing_stays_silent(self):
        """The rung costs a bare `mokata progress` exactly one in-memory uuid: its minted id names
        nothing on disk, so it never claims a run it does not own."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _live(d, "a")
            _live(d, "b")
            self.assertIsNone(RR.resolve_run(d).run_id)

    def test_evidence_outranks_own(self):
        """RE-ENTRY's ruling, preserved: going back to `/brainstorm` registers a BARE checkpoint
        under this process's own id, so "my own run" is precisely the empty one. The pipeline the
        approach belongs to wins."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _run(d, "the-pipeline", passed=PIPELINE_PHASES[:2])
            _evidence(d, "the-pipeline")
            S.set_for_test(S.Session("re-entered", "2026-08-05T00:00:00Z", 0.0, os.getpid()))
            _live(d, "re-entered")                       # the bare re-entry checkpoint
            res = RR.resolve_run(d)
            self.assertEqual(res.run_id, "the-pipeline")
            self.assertEqual(res.basis, RR.BASIS_EVIDENCE)

    def test_own_does_not_break_the_two_pipeline_refusal(self):
        """R1's discipline, preserved against the new rung: with TWO genuine pipelines on disk,
        mokata refuses even when one of them is this process's own. "Mine" is a preference between
        two valid answers, which is the window-picking R1 forbade."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _run(d, "theirs", passed=PIPELINE_PHASES[:1])
            _evidence(d, "theirs")
            S.set_for_test(S.Session("mine", "2026-08-05T00:00:00Z", 0.0, os.getpid()))
            _run(d, "mine", passed=PIPELINE_PHASES[:1])
            _evidence(d, "mine")
            self.assertIsNone(RR.resolve_run(d).run_id)


class TestTheUnshippedRung(_NoPin):
    """What soundly survives of `find_active_run`: its RETIREMENT half, which NARROWS."""

    def test_finished_runs_are_excluded_so_the_live_one_is_forced(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _run(d, "last-week", passed=PIPELINE_PHASES)
            _run(d, "the-week-before", passed=PIPELINE_PHASES)
            _ship(surface, "last-week")
            _ship(surface, "the-week-before")
            _run(d, "today", passed=PIPELINE_PHASES[:1])
            res = RR.resolve_run(d)
            self.assertEqual(res.run_id, "today")
            self.assertEqual(res.basis, RR.BASIS_UNSHIPPED)

    def test_narrowing_cannot_pick_between_two_unfinished_runs(self):
        """The half that PICKED is gone. Excluding can force an answer; it can never choose one."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _run(d, "done", passed=PIPELINE_PHASES)
            _ship(surface, "done")
            _run(d, "one", passed=PIPELINE_PHASES[:1])
            _run(d, "two", passed=PIPELINE_PHASES[:1])
            self.assertIsNone(RR.resolve_run(d).run_id)

    def test_a_single_run_still_resolves_after_it_ships(self):
        """REVIEW-FIX.R1's guarantee against the new rung: the verdict key must SURVIVE
        `mokata progress mark ship`, or ship's own entry mark re-keys the read away from the run
        whose verdict was recorded. A one-run repo resolves at SINGLE, above the narrowing."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _run(d, "solo", passed=PIPELINE_PHASES)
            self.assertEqual(RR.resolve_run(d).run_id, "solo")
            _ship(surface, "solo")
            self.assertEqual(RR.resolve_run(d).run_id, "solo")     # still keyed


# ============================================================ 5 · the refusals that write nothing
class TestTheWritingSurfacesRefuse(_NoPin):
    def test_a_stage_mark_refuses_rather_than_stamping_a_guess(self):
        """A stage mark in the WRONG run is worse than an absent one: it is a false green a later
        reader trusts. Unresolvable ⇒ nothing is written, and the refusal names the remedy."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _live(d, "aaa")
            _live(d, "bbb")
            with redirect_stdout(io.StringIO()) as out:
                self.assertEqual(main(["progress", "mark", "develop", "--path", d]), 0)
            self.assertIn("not recording", out.getvalue())
            self.assertIn("--run", out.getvalue())
            self.assertEqual([e for e in ProgressLog.from_surface(surface).read_events()
                              if e.get("type") == STAGE_ENTER], [])

    def test_the_named_run_is_the_way_through(self):
        """The remedy the refusal names must actually work, or it is the P16 failure of naming a
        road out that errors."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _live(d, "aaa")
            _live(d, "bbb")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(["progress", "mark", "develop", "--path", d, "--run", "bbb"]), 0)
            marks = [e for e in ProgressLog.from_surface(surface).read_events()
                     if e.get("type") == STAGE_ENTER]
            self.assertEqual([e["run_id"] for e in marks], ["bbb"])

    def test_no_run_at_all_still_records_a_runless_mark(self):
        """The OTHER absence, handled differently on purpose. With no run on disk there is nothing
        to mis-attribute to, so a run-less `stage_enter` is honest observability — and it is what
        the badge's "no checkpoint but a log recorded a stage" path reads. Refusing here would break
        a working surface in the name of a defect that cannot occur."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            with redirect_stdout(io.StringIO()) as out:
                self.assertEqual(main(["progress", "mark", "develop", "--path", d]), 0)
            self.assertNotIn("not recording", out.getvalue())
            marks = [e for e in ProgressLog.from_surface(surface).read_events()
                     if e.get("type") == STAGE_ENTER]
            self.assertEqual(len(marks), 1)
            self.assertIsNone(marks[0]["run_id"])

    def test_resume_refuses_and_names_the_runs(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _live(d, "aaa")
            _live(d, "bbb")
            with redirect_stdout(io.StringIO()) as out:
                self.assertEqual(main(["resume", "--path", d]), 0)
            self.assertIn("no run to resume", out.getvalue())
            self.assertIn("will not guess", out.getvalue())


# ============================================================ 6 · eager MCP registration
class TestEagerRegistration(unittest.TestCase):
    """The LIVE rung can only narrow if every window is IN the registry.

    Before this stage the server registered on the FIRST TOOL CALL it served, while the comment
    above it claimed registry liveness was "a STRUCTURAL fact, not a user-dependent one". A window
    the user had opened but not yet asked mokata anything carried only its SessionStart hook's row —
    written by a process that exits within a second — so a sibling read it as dead and pruned it."""

    def test_the_server_registers_before_it_serves_anything(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            probe = (
                "import sys, mokata.mcp.server as S;"
                # The SDK is irrelevant to WHEN registration happens, and requiring it would make
                # this an environment test. Stub the availability check and `.run()` — the latter
                # stands in for serving, so registration is proved to have happened by the time the
                # server would start, without blocking this process on stdio.
                "S.mcp_available = lambda: True;"
                "S.build_server = lambda: type('X',(),{'run':lambda s: None})();"
                f"S.main(['--path', {d!r}]);"
                "import json, os;"
                "from mokata.tdd_state import state_dir;"
                "from mokata import session_registry as SR;"
                f"p = os.path.join(state_dir({d!r}), SR.SESSION_REGISTRY_KEY + '.json');"
                "print(len(json.load(open(p))['sessions']))"
            )
            env = dict(os.environ)
            env["PYTHONPATH"] = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
            proc = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                                  text=True, env=env, timeout=120)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout.strip(), "1",
                             "the MCP server did not register its window before serving")


# ============================================================ 7 · sticky pid
class TestStickyPid(unittest.TestCase):
    """`pid` is the field liveness is decided on, so a stomp erases a LIVE window's proof of life.

    Only reachable when two processes share one session id — i.e. when `MOKATA_SESSION_ID` is
    pinned across them, which the shipped wiring never does. Latent, not live (measured: the stomp
    does not reproduce without an explicit pin), and it stops being latent the day anything pins it.
    """

    def test_a_sibling_cannot_overwrite_a_live_owners_pid(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            S.reset_for_test()
            try:
                # the OWNER: a live pid (this process) recorded under a shared session id.
                S.set_for_test(S.Session("shared", "2026-08-05T00:00:00Z", 0.0, os.getpid()))
                SR.touch(surface)
                # a SIBLING process, same pinned session id, different pid — it must not take over.
                S.set_for_test(S.Session("shared", "2026-08-05T00:00:00Z", 0.0, os.getpid() + 1))
                SR.touch(surface, phase="develop")
            finally:
                S.reset_for_test()
            entry = StateStore(state_dir(d)).read(SR.SESSION_REGISTRY_KEY)["sessions"]["shared"]
            self.assertEqual(entry["pid"], os.getpid(), "a sibling stomped the live owner's pid")
            self.assertEqual(entry["phase"], "develop", "the sibling's own fields still update")

    def test_a_dead_owners_session_can_be_taken_over(self):
        """Ownership transfers when the recorded owner is GONE — otherwise a legitimately restarted
        pinned session could never re-register, which would be a worse failure than the stomp."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            store = StateStore(state_dir(d))
            store.write(SR.SESSION_REGISTRY_KEY, {"sessions": {"shared": {
                "session_id": "shared", "started_at": "2026-08-05T00:00:00Z",
                "pid": 999_999_999,                       # a pid that cannot be alive
                "repo_root": os.path.realpath(d), "last_seen": "2026-08-05T00:00:00Z"}}})
            S.reset_for_test()
            try:
                S.set_for_test(S.Session("shared", "2026-08-05T00:00:00Z", 0.0, os.getpid()))
                SR.touch(surface)
            finally:
                S.reset_for_test()
            entry = store.read(SR.SESSION_REGISTRY_KEY)["sessions"]["shared"]
            self.assertEqual(entry["pid"], os.getpid())

    def test_a_stomped_pid_would_have_erased_a_live_window(self):
        """Why it matters, stated as behaviour rather than as a field value: with the owner's pid
        overwritten by a sibling that has since exited, the LIVE narrowing loses the window."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _live(d, "a")
            _live(d, "mine")
            store = StateStore(state_dir(d))
            reg = store.read(SR.SESSION_REGISTRY_KEY)
            reg["sessions"]["mine"]["pid"] = 999_999_999      # what a stomp leaves behind
            store.write(SR.SESSION_REGISTRY_KEY, reg)
            self.assertEqual(RR.live_runs(d, {"a", "mine"}), ["a"])


# ============================================================ 8 · the state-dir round trip
class TestRootRecovery(unittest.TestCase):
    """`root_of_state_dir` is the DECLARED inverse of `state_dir`, so the read surfaces recover the
    repo one way instead of each doing its own path math."""

    def test_round_trips(self):
        for root in ("/tmp/x", "/a/b/c", os.path.abspath(".")):
            self.assertEqual(root_of_state_dir(state_dir(root)), root)

    def test_a_non_state_dir_recovers_nothing_rather_than_guessing(self):
        for bad in ("/tmp", "/tmp/state", "", "/a/.mokata/state"):
            self.assertIsNone(root_of_state_dir(bad))

    def test_a_surface_store_alone_still_resolves_the_run(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _live(d, "solo")
            self.assertEqual(progress.build_progress(surface.state).run_id, "solo")


# --------------------------------------------------------------------------- the deleted scan
def _old_find_active_run(store, phases=PIPELINE_PHASES):
    """`progress.find_active_run`, VERBATIM as it stood before this stage deleted it.

    Reconstructed here so the DEFECT stays pinned by the suite rather than by a memory of a build
    that no longer exists (the same discipline as `test_review_fix_r2._old_shipped_run_ids`). Note
    what it never asks: whose run this is."""
    for rid in progress.list_runs(store):
        if not PipelineCheckpoint(store, rid).is_complete(phases):
            return rid
    shipped = progress._shipped_run_ids(store)
    for rid in reversed(progress.list_runs_by_recency(store)):
        if rid not in shipped:
            return rid
    return None


if __name__ == "__main__":
    unittest.main()
