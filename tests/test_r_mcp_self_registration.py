"""R-MCP — MCP RUN SELF-REGISTRATION (0.0.14, Phase-1 rider on SI.1).

The SI.1 limitation this closes (doc 84, line 52): the gate hook's window-identity resolution
fails OPEN whenever two or more mokata runs have state in one repo, because the MS.S2 live-session
registry could not soundly disambiguate them — the MCP process self-registered ONLY if the user
happened to call the `session_windows` tool. Enforcement therefore depended on `mokata windows`
having been run.

This stage makes registration STRUCTURAL: the MCP server self-registers its run (session_id/run_id
+ pid + repo_root + last_seen) on the FIRST tool call and refreshes on every subsequent call, so
the registry becomes a sound disambiguator. The gate hook then narrows the ambiguous candidate set
to registry entries that are pid-alive AND rooted at this repo — exactly one survivor enforces;
zero or two-or-more still fail open. Wrong-window blocking stays STRUCTURALLY impossible (two live
windows on one repo are still ambiguous).

Deliverable -> test map (every numbered deliverable has at least one named test):
  (1) server self-registers on first tool call, refreshes via touch(), never at import, and a
      registration failure is D5-classed + swallowed (never breaks the tool call):
        test_first_tool_call_self_registers · test_subsequent_call_refreshes_via_touch ·
        test_no_registration_without_a_tool_call · test_registration_failure_is_d5_classed_and_swallowed
  (2) hook narrows the AMBIGUOUS case to the one live registered run and enforces:
        test_r_mcp_regression
  (3) two live windows on one repo stay ambiguous -> fail open (wrong-window block impossible):
        test_two_live_windows_same_repo_fail_open
  (4) budget: narrowing adds at most one registry read on the ambiguous path; the happy path
      (pinned / single candidate) reads nothing new:
        test_happy_paths_never_touch_the_registry
  (5) no behaviour change: single-candidate + pinned enforcement byte-identical; schema unchanged:
        test_single_candidate_unchanged · test_pinned_run_unchanged ·
        test_registry_entry_schema_unchanged
  Negatives: test_dead_pid_stale_entry_is_not_enforced · test_no_tool_call_yet_is_byte_identical
  Secret-safety: test_registry_entry_carries_no_secret
  Prior-stage pin: test_prior_stage_suites_are_green (SI.1/SI.2/MS.S2/GR.S1 imports resolve).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from mokata import gate_hook as G                                  # noqa: E402
from mokata import tdd_state as T                                  # noqa: E402
from mokata import session as S                                    # noqa: E402
from mokata import session_registry as SR                          # noqa: E402
from mokata import degrade as D                                    # noqa: E402
from mokata.config import Surface                                  # noqa: E402
from mokata.mcp import server as MS                                # noqa: E402
from mokata.mcp import tools_read as TR                            # noqa: E402
from mokata.state import StateStore                                # noqa: E402

# Readable, low-entropy run ids (a run id is just a state-key suffix / registry key).
RUN_A = "run-alpha"
RUN_B = "run-bravo"
DEAD_PID = 2 ** 31 - 1                    # a pid that cannot be alive (mirrors test_ms_s2)


# --------------------------------------------------------------------------- fixtures / helpers
def _repo(d):
    from mokata.init import init_repo
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _raw_store(root):
    return StateStore(G.state_dir(root))


def _approve(root, run):
    _raw_store(root).write(G.APPROACH_PREFIX + run, {"approach": "the chosen one"})


def _emit_spec(root, run):
    _raw_store(root).write(G.SPEC_PREFIX + run, {"criteria": [{"id": "AC1", "text": "it works"}]})


def _record_red(root, run, test_id="test_login"):
    _raw_store(root).write(T.state_key(run), T.to_state(run, red=[test_id], green=[]))


def _registry(root):
    return _raw_store(root).read(SR.SESSION_REGISTRY_KEY) or {"sessions": {}}


def _self_register(root, run_id, tool=TR.sessions):
    """Do what the MCP server does on a tool call: pin this run's identity and invoke a tool
    THROUGH the production self-registration wrapper. Registers `run_id` with THIS live pid."""
    os.environ[S.SESSION_ID_ENV] = run_id
    S.reset_for_test()
    try:
        result = MS._with_registration(tool)(path=root)
        # MCP-R.D0 · R5: self-registration is now FIRE-AND-FORGET (off the served path), so the
        # registry side effect is asynchronous. Drain it here — while env is still pinned — so these
        # tests can observe it deterministically (the production hook path reads the registry later,
        # after the write has long landed; this removes the inherent read-after-spawn race in-test).
        MS._await_registrations()
        return result
    finally:
        os.environ.pop(S.SESSION_ID_ENV, None)
        S.reset_for_test()


def _plant_window(root, run_id, pid, repo_root=None):
    """Plant a registry entry directly (a foreign/dead window we do not control)."""
    base = _raw_store(root)
    reg = base.read(SR.SESSION_REGISTRY_KEY) or {"sessions": {}}
    reg["sessions"][run_id] = {
        "session_id": run_id, "started_at": "2020-01-01T00:00:00Z", "pid": pid,
        "repo_root": repo_root or os.path.abspath(root),
        "last_seen": "2020-01-01T00:00:00Z", "phase": "spec", "scope": None}
    base.write(SR.SESSION_REGISTRY_KEY, reg)


def _envelope(path, cwd, session_id="cc-session-1"):
    return json.dumps({
        "session_id": session_id, "transcript_path": "/tmp/transcript.jsonl", "cwd": cwd,
        "hook_event_name": "PreToolUse", "tool_name": "Write",
        "tool_input": {"file_path": path, "content": "def login(): return True\n"}})


def _hook(path, cwd, session_id="cc-session-1", env=None, timeout=30):
    """Invoke the hook the way Claude Code does — a REAL subprocess, envelope on stdin, exit code +
    stderr the only outputs. The pin is stripped so the hook takes the ambiguous->narrow path."""
    e = dict(os.environ)
    e.pop("MOKATA_SESSION_ID", None)
    e["PYTHONPATH"] = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
    if env:
        e.update(env)
    proc = subprocess.run(
        [sys.executable, "-c",
         "import sys; from mokata.hook_cli import gate_guard_main; sys.exit(gate_guard_main([]))"],
        input=_envelope(path, cwd, session_id), text=True, capture_output=True,
        env=e, timeout=timeout, cwd=cwd)
    return proc.returncode, proc.stderr


class _Base(unittest.TestCase):
    def setUp(self):
        os.environ.pop(S.SESSION_ID_ENV, None)
        S.reset_for_test()
        D.reset_degrade_notices()

    def tearDown(self):
        os.environ.pop(S.SESSION_ID_ENV, None)
        S.reset_for_test()
        D.reset_degrade_notices()


# ======================================================================================
# (1) the MCP server self-registers structurally
# ======================================================================================

class TestSelfRegistration(_Base):

    def test_first_tool_call_self_registers(self):
        """A single tool call, through the server's registration seam, records THIS window live."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            self.assertEqual(_registry(d)["sessions"], {})       # nothing before any tool call
            _self_register(d, RUN_A)
            entry = _registry(d)["sessions"].get(RUN_A)
            self.assertIsNotNone(entry, "the first tool call did not self-register")
            self.assertEqual(entry["pid"], os.getpid())          # our own (alive) pid
            self.assertEqual(os.path.realpath(entry["repo_root"]), os.path.realpath(d))
            self.assertTrue(SR.pid_alive(entry["pid"]))

    def test_subsequent_call_refreshes_via_touch(self):
        """Registration is once-per-process then refreshed: two calls -> still exactly one entry."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _self_register(d, RUN_A)
            _self_register(d, RUN_A)
            sessions = _registry(d)["sessions"]
            self.assertEqual([k for k in sessions if k == RUN_A], [RUN_A])   # upsert, not a dup
            self.assertEqual(len(sessions), 1)

    def test_no_registration_without_a_tool_call(self):
        """Never at import/startup: an initialized repo with no tool call has an empty registry —
        so 'no tool call yet' behaviour is byte-identical to before this stage."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            self.assertEqual(_registry(d)["sessions"], {})

    def test_registration_failure_is_d5_classed_and_swallowed(self):
        """A registry failure must NEVER break a tool call, and must be D5 note_degraded (loud),
        not silent."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            orig = SR.touch
            SR.touch = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
            try:
                result = MS._with_registration(TR.sessions)(path=d)
                MS._await_registrations()   # R5: drain the fire-and-forget registration so its
                                            # swallowed failure + D5 notice are observable here
            finally:
                SR.touch = orig
            self.assertIsInstance(result, dict)                  # the tool STILL returned
            self.assertIn("sessions", result)
            self.assertTrue(any(n.subsystem == "session-registry" for n in D.emitted_notices()),
                            "a swallowed registration failure was not D5-classed")
            self.assertEqual(_registry(d)["sessions"], {})       # and it really did not register


# ======================================================================================
# (2) THE regression — the ambiguous case now enforces against the one live run
# ======================================================================================

class TestRMcpRegression(_Base):

    def test_r_mcp_regression(self):
        """Two runs have on-disk state (ambiguous on old code -> fail OPEN, exit 0). One of them
        (RUN_A) is a LIVE registered window; RUN_B is only on disk. After R-MCP the hook narrows to
        RUN_A and ENFORCES its gate. RUN_A is mid-violation (spec emitted, no failing test) so the
        narrowed enforcement is a BLOCK (exit 2). This test fails on the pre-stage code (exit 0)."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _approve(d, RUN_A), _emit_spec(d, RUN_A)                 # A: would BLOCK (no RED)
            _approve(d, RUN_B), _emit_spec(d, RUN_B), _record_red(d, RUN_B)   # B: would ALLOW
            _self_register(d, RUN_A)                                 # A is the live MCP window

            # sanity: on disk this is ambiguous (two candidates); the registry breaks the tie.
            self.assertEqual(set(G._run_ids(G.state_dir(d))), {RUN_A, RUN_B})

            code, err = _hook(os.path.join(d, "src", "auth.py"), d)  # NO pin -> narrow path
            self.assertEqual(code, 2, f"the ambiguous case failed open instead of enforcing: {err!r}")
            self.assertIn(G.GATE_TDD, err)                          # RUN_A's owed gate, not RUN_B's


# ======================================================================================
# (3) wrong-window blocking stays structurally impossible
# ======================================================================================

class TestWrongWindowImpossible(_Base):

    def test_two_live_windows_same_repo_fail_open(self):
        """Two LIVE registered windows on the same repo, both with state -> two survivors -> still
        ambiguous -> fail OPEN + notice. There is no code path that enforces one window's state
        against the other's editor."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _approve(d, RUN_A), _emit_spec(d, RUN_A)                 # A would BLOCK
            _approve(d, RUN_B), _emit_spec(d, RUN_B), _record_red(d, RUN_B)   # B would ALLOW
            _self_register(d, RUN_A)                                 # both windows LIVE...
            _self_register(d, RUN_B)                                 # ...on the same repo

            self.assertTrue(G.resolve_run(d).ambiguous)             # picked NEITHER
            code, err = _hook(os.path.join(d, "src", "auth.py"), d)
            self.assertEqual(code, 0, f"the hook guessed a window and blocked: {err!r}")
            self.assertIn("gates are OFF", err)                     # the honest once-per-window notice


# ======================================================================================
# negative cases
# ======================================================================================

class TestNegatives(_Base):

    def test_dead_pid_stale_entry_is_not_enforced(self):
        """A dead-pid registry entry must never enforce (no blocking against a corpse). RUN_A is
        LIVE and mid-violation; RUN_B is a DEAD registered window with a failing test on record.
        Narrowing drops the corpse -> the sole survivor RUN_A enforces -> BLOCK. Had the hook
        enforced the corpse RUN_B (RED on record) it would have ALLOWED (exit 0)."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _approve(d, RUN_A), _emit_spec(d, RUN_A)                 # A live, would BLOCK
            _approve(d, RUN_B), _emit_spec(d, RUN_B), _record_red(d, RUN_B)   # B dead, would ALLOW
            _self_register(d, RUN_A)                                 # A alive
            _plant_window(d, RUN_B, pid=DEAD_PID)                    # B a corpse

            self.assertEqual([r for r in G._live_runs(d, {RUN_A, RUN_B})], [RUN_A])
            code, err = _hook(os.path.join(d, "src", "auth.py"), d)
            self.assertEqual(code, 2, f"enforced a corpse or failed open: {err!r}")
            self.assertIn(G.GATE_TDD, err)

    def test_no_tool_call_yet_is_byte_identical(self):
        """No MCP tool call has run -> no registry entries -> the ambiguous case fails OPEN with the
        once-per-window notice, exactly as before this stage."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _approve(d, RUN_A), _emit_spec(d, RUN_A)
            _approve(d, RUN_B), _emit_spec(d, RUN_B)
            self.assertEqual(_registry(d)["sessions"], {})          # nothing registered
            code, err = _hook(os.path.join(d, "src", "auth.py"), d)
            self.assertEqual(code, 0, f"a repo with no registered windows blocked: {err!r}")
            self.assertIn("gates are OFF", err)


# ======================================================================================
# (4) budget — narrowing is on the ambiguous path only
# ======================================================================================

class TestBudget(_Base):

    def test_happy_paths_never_touch_the_registry(self):
        """The pinned-run and single-candidate resolutions must read NOTHING new: `_live_runs` is
        only consulted when there are 2+ on-disk candidates. Booby-trap it to prove it isn't."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            orig = G._live_runs
            G._live_runs = lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("registry read on the happy path"))
            try:
                # pinned run
                self.assertEqual(G.resolve_run(d, run_id=RUN_A).run_id, RUN_A)
                # single on-disk candidate
                _approve(d, RUN_A)
                self.assertEqual(G.resolve_run(d).run_id, RUN_A)
            finally:
                G._live_runs = orig


# ======================================================================================
# (5) no behaviour change + schema
# ======================================================================================

class TestNoBehaviourChange(_Base):

    def test_single_candidate_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _approve(d, RUN_A), _emit_spec(d, RUN_A)                 # one run, no RED
            code, err = _hook(os.path.join(d, "src", "auth.py"), d)  # no pin, no registry
            self.assertEqual(code, 2, f"the sole run did not enforce: {err!r}")
            self.assertIn(G.GATE_TDD, err)

    def test_pinned_run_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _approve(d, RUN_A), _emit_spec(d, RUN_A)
            _approve(d, RUN_B), _emit_spec(d, RUN_B), _record_red(d, RUN_B)
            self.assertEqual(_hook(os.path.join(d, "src", "auth.py"), d,
                                   env={"MOKATA_SESSION_ID": RUN_A})[0], 2)
            self.assertEqual(_hook(os.path.join(d, "src", "auth.py"), d,
                                   env={"MOKATA_SESSION_ID": RUN_B})[0], 0)

    def test_registry_entry_schema_unchanged(self):
        """R-MCP reuses MS.S2's _ENTRY_FIELDS — no new fields."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _self_register(d, RUN_A)
            entry = _registry(d)["sessions"][RUN_A]
            self.assertEqual(set(entry), set(SR._ENTRY_FIELDS))


# ======================================================================================
# secret-safety
# ======================================================================================

class TestSecretSafety(_Base):

    def test_registry_entry_carries_no_secret(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            secret_pw = "s3cr3t" + "_pw"
            dsn = "postgres" + "://u:" + secret_pw + "@" + "db.internal:5432/app"
            os.environ["MOKATA_TEST_DSN"] = dsn
            try:
                _self_register(d, RUN_A)
                blob = json.dumps(_registry(d))
                self.assertNotIn("postgres" + "://", blob)
                self.assertNotIn(secret_pw, blob)
                self.assertEqual(set(_registry(d)["sessions"][RUN_A]), set(SR._ENTRY_FIELDS))
            finally:
                os.environ.pop("MOKATA_TEST_DSN", None)


# ======================================================================================
# prior-stage pin
# ======================================================================================

class TestPriorStages(_Base):

    def test_prior_stage_suites_are_green(self):
        """Sanity that the surfaces R-MCP rides remain importable and shaped as depended on."""
        from mokata.knowledge import ast_backend  # noqa: F401  (GR.S1)
        self.assertEqual(SR._ENTRY_FIELDS[:6],
                         ("session_id", "started_at", "pid", "repo_root", "last_seen", "phase"))
        self.assertTrue(hasattr(G, "resolve_run") and hasattr(G, "check_write"))
        self.assertTrue(callable(getattr(MS, "build_server", None)))


if __name__ == "__main__":
    unittest.main()
