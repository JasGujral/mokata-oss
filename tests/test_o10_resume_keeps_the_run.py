"""0.0.26 O10 — IDE-RESUME-LOSES-RUN: a resumed session keeps the run it was in.

User report (Jas 2026-10-21): "when I resume an old session I left in between in a mokata cycle, the
IDE is not showing develop-stage changes", then "resume after develop or brainstorm both cause
issues". Every per-session fact (run claim, snapshot run, IDE session row) is keyed by Claude Code's
`session_id`, and a resume can come back under a NEW id (`--fork-session`, `/branch`, older
`--resume`) or keep it with its MCP process gone (current `--resume`).

The matrix: resume at EACH stage boundary (after brainstorm, after spec, after test, mid-develop),
in BOTH shapes (A: new id named by the transcript, B: id kept, no live MCP), in a repo where a
SECOND pipeline (another window's run, with a spec) also has state — the shape in which a lost
binding fails the gate OPEN and the IDE picks the other run. For each: the resumed session is
bound to the same run, the snapshot carries it, the Plan view opens on it, the lifecycle shows the
stages entered before the break plus the resumed session's new marks, the browser and time-lapse
join the session to its predecessor, and the phase gate polices the ORIGINAL run.
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
from _plan import (call_planned, commit_planned, emit_planned,  # noqa: F401,E402  (R2)
                   planned, sealed, with_tasks)

from mokata import gate_hook as G
from mokata import hook_cli
from mokata import run_intent as RI
from mokata import run_resolver as RR
from mokata import session_resume as SR
from mokata import tdd_state as T
from mokata.config import Surface
from mokata.govern.resume import PipelineCheckpoint
from mokata.ide import plans as P
from mokata.ide import session_views as V
from mokata.ide import sessions as SS
from mokata.ide import snapshots as S
from mokata.init import init_repo
from mokata.progress_events import STAGE_ENTER, ProgressLog
from mokata.state import StateStore

R, OTHER = "runR", "runOTHER"
S1, S2 = "sess-one", "sess-two"
STAGES = ("after-brainstorm", "after-spec", "after-test", "mid-develop")


def _store(d):
    return StateStore(T.state_dir(d))


def _hook(fn, payload):
    # 0.0.26 O15 — the hook reads its payload only off a NON-terminal stdin; the test owns that
    # fact instead of inheriting it (at a real terminal all 12 tests here failed).
    import _support
    with _support.stdin_is(_support.NoTtyStdin()), \
            mock.patch.object(hook_cli, "_read_stdin_bounded", return_value=json.dumps(payload)), \
            redirect_stdout(io.StringIO()):
        return fn([])


def _write(d, sid, rel, text):
    """A native Write as Claude Code runs it: gate-guard, the write, dirty-track. Returns rc."""
    env = {"cwd": d, "session_id": sid, "tool_name": "Write",
           "tool_input": {"file_path": os.path.join(d, rel), "content": text}}
    with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
        rc = _hook(hook_cli.gate_guard_main, env)
    if rc == 0:
        os.makedirs(os.path.dirname(os.path.join(d, rel)), exist_ok=True)
        with open(os.path.join(d, rel), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        _hook(hook_cli.dirty_track_main, env)
    return rc


def _transcript(d, *sids):
    path = os.path.join(d, "transcript.jsonl")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for sid in sids:
            fh.write(json.dumps({"type": "user", "sessionId": sid, "message": "x"}) + "\n")
    return path


def _resume(d, sid, transcript=None, source="resume"):
    payload = {"cwd": d, "session_id": sid, "source": source}
    if transcript:
        payload["transcript_path"] = transcript
    return _hook(hook_cli.session_start_main, payload)


def _repo(d, stage):
    """S1 ran a pipeline on R up to `stage`; another window's pipeline OTHER (with a spec) shares
    the repo. Stage-enter events for R are on record, as the pipeline would have left them."""
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    surface = Surface.load(d)
    for rid in (R, OTHER):
        PipelineCheckpoint(surface.state, rid).ensure_registered()
    st = _store(d)
    st.write(G.APPROACH_PREFIX + OTHER, {"approach": "theirs"})
    st.write(G.SPEC_PREFIX + OTHER, sealed({"criteria": [{"id": "AC1", "text": "theirs"}]}))
    log = ProgressLog.from_state_dir(T.state_dir(d))
    RI.record_intent(d, S1, "prompt:brainstorm")
    RI.bind_registered(d, S1, R)
    st.write(G.APPROACH_PREFIX + R, {"approach": "ours", "topic": "our plan"})
    log.append_event(STAGE_ENTER, "brainstorm", run_id=R)
    if stage != "after-brainstorm":
        st.write(G.SPEC_PREFIX + R, sealed({"criteria": [{"id": "AC1", "text": "ours"}]}))
        log.append_event(STAGE_ENTER, "spec", run_id=R)
    if stage in ("after-test", "mid-develop"):
        st.write(T.state_key(R), T.to_state(R, red=["test_ours"], green=[]))
        log.append_event(STAGE_ENTER, "test", run_id=R)
    _write(d, S1, "tests/test_ours.py", "def test_ours():\n    assert False\n")
    if stage == "mid-develop":
        log.append_event(STAGE_ENTER, "develop", run_id=R)
        _write(d, S1, "src/ours.py", "x = 1\n")


def _expected_gate(stage):
    """What the ORIGINAL run's state says about an implementation write at this boundary."""
    return {"after-brainstorm": G.GATE_SPEC, "after-spec": G.GATE_TDD}.get(stage)


class _Matrix:
    """The stage-by-stage resume matrix; `NEW_ID` picks shape A (True) or B (False)."""
    NEW_ID = True

    def setUp(self):
        os.environ.pop("MOKATA_SESSION_ID", None)

    def _run_stage(self, d, stage):
        _repo(d, stage)
        if self.NEW_ID:
            sid = S2
            _resume(d, sid, _transcript(d, S1, S1, S1))
        else:
            sid = S1
            _resume(d, sid)                          # no MCP process is alive for R
        return sid

    def _assert_stage(self, d, stage, sid):
        # 1. the resumed session is bound to the SAME run (one identity across the break)
        self.assertEqual((RI.CLAIM_BOUND, R), RI.session_claim(d, sid), stage)
        self.assertEqual(R, RR.read_binding(d, sid), f"{stage}: badge binding")
        self.assertEqual(R, RR._session_bound_run(d, sid), f"{stage}: MCP-side stamps resolve R")

        # 2. the phase gate polices R — not fail-open on two runs, not blocked by an open intent
        out = G.check_write(d, "src/new.py", session_id=sid)
        want = _expected_gate(stage)
        if want:
            self.assertFalse(out.allowed, f"{stage}: the gate failed OPEN after resume")
            self.assertEqual((want, R), (out.gate, out.run_id), stage)
            self.assertEqual(2, _write(d, sid, "src/new.py", "y = 2\n"), stage)
        else:
            self.assertTrue(out.allowed, stage)
            self.assertIsNone(out.notice, f"{stage}: allowed by the ambiguity fail-open, "
                                          "not by R's failing test")
            self.assertIn("failing test is on record", out.reason, stage)
            self.assertEqual(0, _write(d, sid, "src/new.py", "y = 2\n"), stage)
        # a write the session makes after the break, in every stage
        self.assertEqual(0, _write(d, sid, "tests/test_more.py", "def test_more():\n    pass\n"))

        # 3. every snapshot the resumed session took carries R
        mine = S.timeline(d, session=sid)
        self.assertTrue(mine, stage)
        self.assertEqual({R}, {x.run for x in mine}, f"{stage}: a resumed write lost its run")

        # 4. the Plan view opens on R's plan/spec for the resumed session, and lists it under R
        runs = P.local_runs(d, focus=sid)
        self.assertEqual(R, runs["default"], f"{stage}: the Plan view opened another run")
        self.assertIn(sid, {r["id"]: r for r in runs["runs"]}[R]["sessions"], stage)
        shown = P.plan(d, runs["default"])
        self.assertEqual(R, shown["run"], stage)
        self.assertNotEqual("absent", shown["approach"]["state"], f"{stage}: the plan is empty")

        # 5. the lifecycle keeps the stages entered before the break AND marks the resume
        lc = P.lifecycle(d, R)
        entered = {s["stage"] for s in lc["stages"] if s["entered"]}
        self.assertIn("brainstorm", entered, stage)
        if stage != "after-brainstorm":
            self.assertIn("spec", entered, stage)
        kinds = [m["kind"] for m in lc["marks"]]
        self.assertIn("resumed", kinds, f"{stage}: the break is not on the run's lifecycle")
        if want:
            self.assertIn("gate_block", kinds, f"{stage}: the resumed session's block was not "
                                               "credited to R")
        self.assertIn(sid, lc["sessions"], stage)

        # 6. the browser and the time-lapse join the resumed session to its predecessor
        rows = {r["id"]: r for r in V.browser(d)["sessions"]}
        self.assertEqual(R, rows[sid]["run"], stage)
        tl = V.timelapse(d, sid)
        self.assertEqual(R, tl["run"], stage)
        kinds = [st["kind"] for st in tl["steps"]]
        self.assertIn("resume", kinds, f"{stage}: no resume marker in the time-lapse")
        paths_before = [st.get("path") for st in tl["steps"][:kinds.index("resume")]]
        paths_after = [st.get("path") for st in tl["steps"][kinds.index("resume"):]]
        self.assertIn("tests/test_ours.py", paths_before, f"{stage}: the pre-break work is missing")
        self.assertIn("tests/test_more.py", paths_after, stage)
        if self.NEW_ID:
            self.assertEqual(S1, rows[sid]["resumed_from"], stage)
            # P33 (R11): the chain is ONE row — the predecessor folds into the resumed session.
            self.assertEqual([sid, S1], rows[sid]["chain"], stage)
            self.assertNotIn(S1, rows, stage)
            self.assertEqual([S2, S1], tl["joined"], stage)
        else:
            self.assertEqual(1, len(rows[sid]["resumes"]), stage)

    def test_resume_after_brainstorm(self):
        with tempfile.TemporaryDirectory() as d:
            self._assert_stage(d, "after-brainstorm", self._run_stage(d, "after-brainstorm"))

    def test_resume_after_spec(self):
        with tempfile.TemporaryDirectory() as d:
            self._assert_stage(d, "after-spec", self._run_stage(d, "after-spec"))

    def test_resume_after_test(self):
        with tempfile.TemporaryDirectory() as d:
            self._assert_stage(d, "after-test", self._run_stage(d, "after-test"))

    def test_resume_mid_develop(self):
        with tempfile.TemporaryDirectory() as d:
            self._assert_stage(d, "mid-develop", self._run_stage(d, "mid-develop"))


class ResumeUnderANewSessionId(_Matrix, unittest.TestCase):
    """Case A — `--fork-session` / `/branch` / older `--resume`: a NEW id, the transcript names S1."""
    NEW_ID = True

    def test_fork_source_carries_too(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "after-test")
            _resume(d, S2, _transcript(d, S1), source="fork")
            self.assertEqual((RI.CLAIM_BOUND, R), RI.session_claim(d, S2))


class ResumeUnderTheSameSessionId(_Matrix, unittest.TestCase):
    """Case B — current `--resume`/`--continue`: the id is kept and R's MCP process is gone."""
    NEW_ID = False

    def test_a_live_run_in_another_window_never_rebinds_the_resumed_session(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "mid-develop")
            with mock.patch.object(RR, "live_runs", return_value=[OTHER]):
                _resume(d, S1)
            self.assertEqual(R, RR.read_binding(d, S1), "the one-live-run guess re-pointed a "
                                                         "session that holds its own claim")


class TheBrainstormIntentAcrossAResume(unittest.TestCase):
    """A resume between `/mokata:brainstorm` (OPEN intent) and `session_save(register=true)`."""

    def setUp(self):
        os.environ.pop("MOKATA_SESSION_ID", None)

    def _open(self, d, at):
        init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
        PipelineCheckpoint(Surface.load(d).state, OTHER).ensure_registered()
        _store(d).write(G.SPEC_PREFIX + OTHER, sealed({"criteria": [{"id": "AC1", "text": "theirs"}]}))
        _store(d).write(RI.INTENT_PREFIX + S1, {"at": at, "via": "prompt:brainstorm"})

    def test_a_new_id_carries_the_open_intent_and_is_policed_until_it_registers(self):
        with tempfile.TemporaryDirectory() as d:
            self._open(d, time.time())
            _resume(d, S2, _transcript(d, S1))
            out = G.check_write(d, "src/x.py", session_id=S2)
            self.assertEqual((False, G.GATE_RUN_REG), (out.allowed, out.gate),
                             "the resumed brainstorm went unpoliced (or fell to OTHER's run)")
            RI.bind_registered(d, S2, R)             # its registration comes back
            self.assertEqual((RI.CLAIM_BOUND, R), RI.session_claim(d, S2))

    def test_the_carried_intent_expires_with_the_original_never_later(self):
        with tempfile.TemporaryDirectory() as d:
            origin = time.time() - RI.INTENT_TTL_SECS + 60
            self._open(d, origin)
            _resume(d, S2, _transcript(d, S1))
            self.assertEqual(RI.CLAIM_OPEN, RI.session_claim(d, S2)[0])
            later = origin + RI.INTENT_TTL_SECS + 1
            self.assertEqual((RI.CLAIM_NONE, None), RI.session_claim(d, S2, now=later),
                             "a resume extended how long the pipeline holds the session hostage")

    def test_the_same_id_keeps_its_open_intent_and_a_clear_holds_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            self._open(d, time.time())
            _resume(d, S1)
            self.assertEqual(G.GATE_RUN_REG, G.check_write(d, "src/x.py", session_id=S1).gate)
            # `/clear` starts a session with a new id and no transcript link: it holds nothing
            _resume(d, "after-clear", None, source="clear")
            self.assertEqual((RI.CLAIM_NONE, None), RI.session_claim(d, "after-clear"))


class NeverBorrowedNeverGuessed(unittest.TestCase):
    """The parallel-run rules (run_intent's docstring): the only join is the transcript."""

    def setUp(self):
        os.environ.pop("MOKATA_SESSION_ID", None)

    def test_no_transcript_no_carry_even_with_one_obvious_run(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "mid-develop")
            _resume(d, S2)
            self.assertEqual((RI.CLAIM_NONE, None), RI.session_claim(d, S2))

    def test_a_fresh_startup_never_inherits(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "mid-develop")
            _resume(d, S2, _transcript(d, S1), source="startup")
            self.assertEqual((RI.CLAIM_NONE, None), RI.session_claim(d, S2))

    def test_a_session_with_its_own_claim_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "mid-develop")
            RI.bind_registered(d, S2, OTHER)
            _resume(d, S2, _transcript(d, S1))
            self.assertEqual((RI.CLAIM_BOUND, OTHER), RI.session_claim(d, S2))

    def test_the_newest_predecessor_with_a_claim_wins(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "mid-develop")
            RI.bind_registered(d, "sess-older", OTHER)
            _resume(d, S2, _transcript(d, "sess-older", S1, "sess-unclaimed"))
            self.assertEqual((RI.CLAIM_BOUND, R), RI.session_claim(d, S2))

    def test_a_ghost_run_is_not_carried(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "mid-develop")
            os.remove(os.path.join(T.state_dir(d), RR.CHECKPOINT_PREFIX + R + ".json"))
            _resume(d, S2, _transcript(d, S1))
            self.assertEqual((RI.CLAIM_NONE, None), RI.session_claim(d, S2))

    def test_a_fault_while_carrying_is_said_never_raised(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "mid-develop")
            with mock.patch.object(RI, "session_claim", side_effect=RuntimeError("boom")), \
                    mock.patch("mokata.degrade.note_degraded") as said:
                self.assertIsNone(SR.on_session_start(d, S2, "resume", _transcript(d, S1)))
            self.assertTrue(said.called, "a lost resume was silent")
            self.assertEqual("run-registration", said.call_args[0][0])

    def test_the_transcript_read_is_bounded_to_its_two_ends(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.jsonl")
            pad = json.dumps({"type": "x", "sessionId": "", "pad": "p" * 1000}) + "\n"
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps({"sessionId": "head-one"}) + "\n")
                fh.write(pad * 200)
                fh.write(json.dumps({"sessionId": "middle-one"}) + "\n")
                fh.write(pad * 200)
                fh.write(json.dumps({"sessionId": "tail-one"}) + "\n")
            got = SR.predecessors(path, "me")
            self.assertEqual(["tail-one", "head-one"], got)
            self.assertEqual([], SR.predecessors(os.path.join(d, "missing.jsonl"), "me"))


if __name__ == "__main__":
    unittest.main()
