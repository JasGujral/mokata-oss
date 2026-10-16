"""0.0.23 F1 — SESSION-BIND-BY-ANCESTOR: the MCP server finds the Claude session it serves.

Live report (Jas, 2026-10-15): "in amend facing a lot of issues in session selection when resuming
all sessions using /resume from Claude." Grounded cause: the MCP server cannot see Claude Code's
`session_id` (#25642), so `consent._approval_run` resolved WITHOUT one, the BOUND rung never spoke,
and with two live pipelines the ladder went AMBIGUOUS -> the MCP's own process id -> `spec_amend`
blocked at `spec-persisted` (or keyed to the wrong run).

The fix: hooks (SessionStart / UserPromptSubmit) record the session per ANCESTOR pid; the MCP server
walks its own ancestors to the shared Claude process. Every test here runs on a PLANTED process
table (an injected lookup), so nothing depends on the real OS, a real Claude, or the platform.
"""
import contextlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import hook_cli
from mokata import run_intent as RI
from mokata import session as S
from mokata import session_ancestry as SA
from mokata.config import Surface
from mokata.govern.resume import PipelineCheckpoint
from mokata.init import init_repo
from mokata.run_resolver import (APPROACH_PREFIX, BASIS_AMBIGUOUS, BASIS_BOUND, BASIS_EVIDENCE,
                                 SPEC_PREFIX, resolve_run, resolve_run_for_process)
from mokata.state import StateStore
from mokata.tdd_state import state_dir

P = SA.ProcInfo

# A planted machine: one tmux server, two Claude processes under it, each with its MCP server.
#   1000 tmux  -> 1100 bash(pane A) -> 1110 claude A -> 1150 uv -> 1151 mcp A
#              -> 1200 bash(pane B) -> 1210 claude B -> 1251 mcp B
BASE = {
    1000: P(1, "t1000", 10.0),
    1100: P(1000, "t1100", 100.0),
    1110: P(1100, "t1110", 110.0),
    1150: P(1110, "t1150", 111.0),
    1151: P(1150, "t1151", 111.5),
    1200: P(1000, "t1200", 200.0),
    1210: P(1200, "t1210", 210.0),
    1251: P(1210, "t1251", 211.0),
}


class Machine:
    """A mutable process table + a hook runner that spawns and reaps the hook's own processes."""

    def __init__(self, procs=None):
        self.procs = dict(BASE if procs is None else procs)
        self._next = 5000

    def lookup(self, pid):
        return self.procs.get(pid)

    def hook(self, root, claude_pid, session_id, ts):
        """A hook firing in `claude_pid`: `sh -c` -> python, which records, then both exit."""
        sh, py = self._next, self._next + 1
        self._next += 2
        self.procs[sh] = P(claude_pid, f"t{sh}", ts - 0.2)
        self.procs[py] = P(sh, f"t{py}", ts - 0.1)
        try:
            return SA.record_session(root, session_id, lookup=self.lookup, pid=py, now=ts)
        finally:
            del self.procs[py], self.procs[sh]

    def mcp(self, root, pid):
        return resolve_run_for_process(root, lookup=self.lookup, pid=pid)


def _silent(*_a):
    pass


def _repo():
    d = tempfile.mkdtemp()
    init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
    return d


def _pipeline(d, rid, session):
    """A run with pipeline EVIDENCE (approved approach + emitted spec), bound to `session` by its
    own registration (the PostToolUse join `run_intent.bind_registered` performs)."""
    PipelineCheckpoint(Surface.load(d).state, rid).ensure_registered()
    store = StateStore(state_dir(d))
    store.write(APPROACH_PREFIX + rid, {"run_id": rid, "approach": "a"})
    store.write(SPEC_PREFIX + rid, {"title": rid, "criteria": [], "version": 1})
    RI.bind_registered(d, session, rid)


class _Base(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop("MOKATA_SESSION_ID", None)
        self.addCleanup(self.env.stop)
        self.addCleanup(S.reset_for_test)


class TwoLiveSessionsEachResolveTheirOwnRun(_Base):

    def test_each_mcp_resolves_the_run_of_the_session_its_claude_serves(self):
        d, m = _repo(), Machine()
        _pipeline(d, "runA", "S_A")
        _pipeline(d, "runB", "S_B")
        self.assertEqual(BASIS_AMBIGUOUS, resolve_run(d).basis,
                         "control: two evidence runs and no session id is ambiguous (the bug)")
        m.hook(d, 1110, "S_A", ts=300.0)
        m.hook(d, 1210, "S_B", ts=301.0)
        a, b = m.mcp(d, 1151), m.mcp(d, 1251)
        self.assertEqual(("runA", BASIS_BOUND), (a.run_id, a.basis))
        self.assertEqual(("runB", BASIS_BOUND), (b.run_id, b.basis))

    def test_a_resume_that_switches_session_in_one_process_is_followed(self):
        d, m = _repo(), Machine()
        _pipeline(d, "runA", "S_A")
        _pipeline(d, "runC", "S_C")
        m.hook(d, 1110, "S_A", ts=300.0)
        self.assertEqual("runA", m.mcp(d, 1151).run_id)
        m.hook(d, 1110, "S_C", ts=400.0)            # SessionStart(source=resume) in claude A
        self.assertEqual("runC", m.mcp(d, 1151).run_id,
                         "after /resume the next resolution follows the resumed session")
        m.hook(d, 1110, "S_A", ts=500.0)            # /resume back, then a prompt
        self.assertEqual("runA", m.mcp(d, 1151).run_id)

    def test_the_session_binding_alone_also_answers(self):
        """No claim, only the general binding (a resumed session bound at SessionStart)."""
        from mokata.run_resolver import bind_session_run
        d, m = _repo(), Machine()
        _pipeline(d, "runA", "S_A")
        _pipeline(d, "runB", "S_B")
        bind_session_run(d, "S_X", "runB")
        m.hook(d, 1110, "S_X", ts=300.0)
        self.assertEqual("runB", m.mcp(d, 1151).run_id)


class TheGuardsFailClosed(_Base):

    def _two(self):
        d = _repo()
        _pipeline(d, "runA", "S_A")
        _pipeline(d, "runB", "S_B")
        return d

    def test_a_recycled_pid_with_a_different_start_token_is_ignored(self):
        d, m = self._two(), Machine()
        m.hook(d, 1110, "S_A", ts=300.0)
        # claude A exits; its pid is reused by a new process that is now the MCP's parent.
        m.procs[1110] = P(1100, "t1110-reborn", 600.0)
        m.procs[1150] = P(1110, "t1150-reborn", 601.0)
        m.procs[1151] = P(1150, "t1151-reborn", 601.5)
        res = m.mcp(d, 1151)
        self.assertEqual(BASIS_AMBIGUOUS, res.basis,
                         "a recycled pid must not inherit the dead process's session")

    def test_a_shared_ancestor_never_names_another_claudes_session(self):
        """Claude B never wrote; claude A's write reached the shared tmux pid. MCP B must NOT
        resolve S_A through it — a live foreign branch hangs below that pid."""
        d, m = self._two(), Machine()
        m.hook(d, 1110, "S_A", ts=300.0)
        self.assertEqual(BASIS_AMBIGUOUS, m.mcp(d, 1251).basis)

    def test_a_shared_ancestor_written_by_an_EXITED_claude_never_names_its_session(self):
        """0.0.23 verification M1: claude A wrote (reaching tmux), then EXITED; claude B never
        wrote. Guard 2 sees no live foreign branch any more — the tmux entry must still not
        speak for B, because A's hook reached it THROUGH a Claude (guard 4)."""
        d, m = self._two(), Machine()
        m.hook(d, 1110, "S_A", ts=300.0)
        for pid in (1110, 1150, 1151, 1100):        # claude A and its pane exit
            del m.procs[pid]
        self.assertEqual(BASIS_AMBIGUOUS, m.mcp(d, 1251).basis)

    def test_a_dead_previous_claude_in_the_same_pane_does_not_speak_for_a_new_one(self):
        d, m = self._two(), Machine()
        m.hook(d, 1110, "S_A", ts=300.0)
        for pid in (1110, 1150, 1151):              # claude A exits
            del m.procs[pid]
        m.procs[1310] = P(1100, "t1310", 900.0)      # a NEW claude in pane A, no hook yet
        m.procs[1351] = P(1310, "t1351", 901.0)
        self.assertEqual(BASIS_AMBIGUOUS, m.mcp(d, 1351).basis)

    def test_no_map_is_byte_identical_to_the_plain_ladder(self):
        d, m = self._two(), Machine()
        self.assertEqual(resolve_run(d), m.mcp(d, 1151))
        d1 = _repo()
        _pipeline(d1, "runOnly", "S1")
        self.assertEqual(resolve_run(d1), m.mcp(d1, 1151))

    def test_no_process_lookup_is_byte_identical(self):
        d = self._two()
        Machine().hook(d, 1110, "S_A", ts=300.0)
        with mock.patch.object(SA, "system_lookup", return_value=None):
            self.assertEqual(resolve_run(d), resolve_run_for_process(d))

    def test_an_unbound_session_falls_back_unchanged(self):
        d, m = self._two(), Machine()
        m.hook(d, 1110, "S_NEVER_BOUND", ts=300.0)
        self.assertEqual(resolve_run(d), m.mcp(d, 1151))

    def test_explicit_and_pinned_still_outrank_the_session(self):
        d, m = self._two(), Machine()
        m.hook(d, 1110, "S_A", ts=300.0)
        self.assertEqual("named", resolve_run_for_process(d, run_id="named", lookup=m.lookup,
                                                          pid=1151).run_id)
        os.environ["MOKATA_SESSION_ID"] = "pinned"
        self.assertEqual("pinned", m.mcp(d, 1151).run_id)

    def test_re_entry_keeps_the_evidence_rung(self):
        """A bound BARE run + exactly one evidence run elsewhere = the RE-ENTRY shape: the spec
        being amended is the evidence run's, as before this stage."""
        d, m = _repo(), Machine()
        _pipeline(d, "runA", "S_A")
        PipelineCheckpoint(Surface.load(d).state, "runBare").ensure_registered()
        RI.bind_registered(d, "S_RE", "runBare")
        m.hook(d, 1110, "S_RE", ts=300.0)
        res = m.mcp(d, 1151)
        self.assertEqual(("runA", BASIS_EVIDENCE), (res.run_id, res.basis))


class TheMapIsNotRewrittenEveryTurn(_Base):
    """The per-prompt hook must not write per turn (the injection turn's byte-snapshot pin)."""

    def test_an_unchanged_fresh_mapping_is_not_rewritten(self):
        d, m = _repo(), Machine()
        self.assertTrue(m.hook(d, 1110, "S_A", ts=300.0))
        self.assertFalse(m.hook(d, 1110, "S_A", ts=310.0),
                         "same session, fresh stamp — the launcher shell is new, nothing else is")

    def test_a_resume_switch_writes_at_once(self):
        d, m = _repo(), Machine()
        m.hook(d, 1110, "S_A", ts=300.0)
        self.assertTrue(m.hook(d, 1110, "S_C", ts=301.0))

    def test_a_stale_stamp_is_refreshed(self):
        d, m = _repo(), Machine()
        m.hook(d, 1110, "S_A", ts=300.0)
        self.assertTrue(m.hook(d, 1110, "S_A", ts=300.0 + SA.REFRESH_SECS + 1))


class TheMapStaysSmallAndClean(_Base):

    def test_dead_pids_are_pruned_on_the_next_write(self):
        d, m = _repo(), Machine()
        m.hook(d, 1110, "S_A", ts=300.0)
        m.hook(d, 1210, "S_B", ts=301.0)
        pids = StateStore(state_dir(d)).read(SA.MAP_KEY)["pids"]
        self.assertIn("1210", pids)
        for pid in (1200, 1210, 1251):
            del m.procs[pid]
        m.hook(d, 1110, "S_A", ts=302.0)
        pids = StateStore(state_dir(d)).read(SA.MAP_KEY)["pids"]
        self.assertNotIn("1210", pids)
        self.assertNotIn("1200", pids)
        self.assertIn("1110", pids)

    def test_the_map_is_capped(self):
        d = _repo()
        procs = {9: P(1, "t9", 1.0)}
        for i in range(SA.MAX_ENTRIES + 20):
            procs[100 + i] = P(9, f"c{i}", 5.0)
        m = Machine(procs)
        for i in range(SA.MAX_ENTRIES + 20):
            m.hook(d, 100 + i, f"S{i}", ts=10.0 + i)
        self.assertLessEqual(len(StateStore(state_dir(d)).read(SA.MAP_KEY)["pids"]),
                             SA.MAX_ENTRIES)

    def test_the_map_is_not_a_run_candidate(self):
        from mokata.run_resolver import run_ids
        d, m = _repo(), Machine()
        m.hook(d, 1110, "S_A", ts=300.0)
        self.assertEqual(set(), run_ids(state_dir(d)))

    def test_writers_and_readers_never_raise(self):
        d = _repo()

        def boom(_pid):
            raise RuntimeError("x")
        self.assertFalse(SA.record_session(d, "S", lookup=boom, pid=1))
        self.assertIsNone(SA.session_for_process(d, lookup=boom, pid=1))
        self.assertFalse(SA.record_session(d, "", lookup=Machine().lookup, pid=1151))
        self.assertEqual([], SA.ancestors(None))

    def test_a_parent_younger_than_its_child_ends_the_walk(self):
        procs = {10: P(1, "t10", 50.0), 20: P(10, "t20", 40.0), 30: P(20, "t30", 60.0)}
        self.assertEqual([20], [p for p, _ in SA.ancestors(procs.get, 30)],
                         "pid 10 started after its child 20: a recycled parent, not an ancestor")


class TheHooksWriteTheMap(_Base):

    @staticmethod
    @contextlib.contextmanager
    def _stdin(payload):
        """The hook's payload, delivered the way Claude Code delivers it: on a PIPE. The hooks
        return early when stdin is a TTY, so a run from an interactive terminal (macOS/Windows/
        Linux alike) must not leak the runner's own TTY into the hook — found at the 0.0.23 cut."""
        piped = io.StringIO("")
        with mock.patch.object(hook_cli.sys, "stdin", piped), \
                mock.patch.object(hook_cli, "_read_stdin_bounded",
                                  return_value=json.dumps(payload)):
            yield

    def test_session_start_and_user_prompt_submit_record_the_session(self):
        d, m = _repo(), Machine()
        me = os.getpid()                                  # this test process plays the hook ...
        with mock.patch.object(SA, "system_lookup", return_value=m.lookup), \
                redirect_stdout(io.StringIO()):
            m.procs[me] = P(1110, "self", 299.0)
            with self._stdin({"session_id": "S_A", "cwd": d, "source": "resume"}):
                hook_cli.session_start_main([])
            self.assertIsNone(SA.session_for_process(d, lookup=m.lookup, pid=1151),
                              "while the writing hook is still ALIVE its branch is foreign")
            del m.procs[me]                               # ... and exits, as a hook does
            self.assertEqual("S_A", SA.session_for_process(d, lookup=m.lookup, pid=1151))
            m.procs[me] = P(1110, "self2", 299.5)
            with self._stdin({"session_id": "S_C", "cwd": d, "prompt": "hello"}):
                hook_cli.user_prompt_submit_main([])
            del m.procs[me]
        self.assertEqual("S_C", SA.session_for_process(d, lookup=m.lookup, pid=1151))

    def test_a_hook_without_a_session_id_writes_nothing(self):
        d, m = _repo(), Machine()
        m.procs[os.getpid()] = P(1110, "self", 299.0)
        with mock.patch.object(SA, "system_lookup", return_value=m.lookup):
            with self._stdin({"cwd": d, "prompt": "hello"}):
                hook_cli.user_prompt_submit_main([])
        self.assertIsNone(StateStore(state_dir(d)).read(SA.MAP_KEY))


class AmendEndToEndPicksTheBoundRun(_Base):
    """THE regression: `spec_amend` from an MCP process with two live pipelines in the repo.
    On the old code `_approval_run` cannot pass a session id -> ambiguous -> the MCP's own id
    -> `spec-persisted` block ("no spec is emitted for this run")."""

    C1 = [{"id": "AC1", "text": "the thing works"}]
    T1 = [{"name": "test_thing", "ac_ids": ["AC1"]}]
    C2 = C1 + [{"id": "AC2", "text": "and the other thing"}]
    T2 = T1 + [{"name": "test_other", "ac_ids": ["AC2"]}]

    def _enter(self, sid):
        S.set_for_test(S.Session(session_id=sid, started_at="2026-10-15T00:00:00Z",
                                 started_monotonic=0.0, pid=os.getpid()))

    def _emitted_pipeline(self, d, rid):
        from mokata import approval as A
        from mokata.govern.ledger import AuditLedger
        from mokata.mcp import tools_spec as TS
        self._enter(rid)
        surface = Surface.load(d)
        PipelineCheckpoint(surface.state, rid).ensure_registered()
        surface.state.write("approved_approach", {
            "schema_version": 1, "phase": "brainstorm", "topic": rid,
            "approach": {"name": "A1", "summary": "do it", "tradeoffs": [], "decisions": []},
            "answered_questions": [], "grounding": {}, "approver": "jas",
            "approved_at": "2026-10-15T00:00:00Z",
            "prior_art": {"ran": True, "approach": "A1", "findings": [], "verdict": "none"},
            "domains": []})
        out = TS.spec_emit(path=d, title=rid, criteria=self.C1, tests=self.T1, approach="A1")
        A.approve(d, out["proposal_id"], actor="human",
                  ledger=AuditLedger.from_mokata_dir(os.path.join(d, ".mokata")))
        done = TS.spec_emit(path=d, title=rid, criteria=self.C1, tests=self.T1, approach="A1",
                            proposal_id=out["proposal_id"])
        self.assertEqual("committed", done.get("status"), done)

    def test_amend_with_two_live_runs_reaches_the_human_gate_on_the_bound_run(self):
        from mokata.mcp import tools_spec as TS
        from mokata.mcp.consent import _approval_run
        d = _repo()
        self._emitted_pipeline(d, "runA")
        self._emitted_pipeline(d, "runB")
        RI.bind_registered(d, "S_A", "runA")
        RI.bind_registered(d, "S_B", "runB")
        self.assertEqual(BASIS_AMBIGUOUS, resolve_run(d).basis)

        # THIS process is MCP server B; the resumed session in claude B is S_B.
        m = Machine()
        m.procs[os.getpid()] = P(1210, "self", 211.0)
        self._enter("mcp-process-own-id")
        m.hook(d, 1210, "S_B", ts=400.0)
        with mock.patch.object(SA, "system_lookup", return_value=m.lookup):
            self.assertEqual("runB", _approval_run(d))
            out = TS.spec_amend(path=d, title="runB", criteria=self.C2, tests=self.T2,
                                approach="A1", reason="widen")
        self.assertNotEqual("spec-persisted", out.get("gate"), out)
        self.assertEqual("proposed", out.get("status"), out)

        # ... and claude A's MCP, same repo, resolves its own pipeline.
        m.procs[os.getpid()] = P(1110, "self", 111.0)
        m.hook(d, 1110, "S_A", ts=401.0)
        with mock.patch.object(SA, "system_lookup", return_value=m.lookup):
            self.assertEqual("runA", _approval_run(d))


if __name__ == "__main__":
    unittest.main()
