"""SI.2 — PERSISTED TDD RED/GREEN STATE PER RUN (0.0.13 seatbelt cluster, stage 1).

The bug this closes: `govern/tdd.py:TddGuard` — the executable form of the
`no-code-without-failing-test` gate (doc 85 §4) — kept RED/GREEN in two in-memory sets on the guard
INSTANCE. Exit the CLI, crash the window, or build a second guard, and the discipline reset: a RED
cycle's obligation to see the test fail evaporated, and post-restart writes proceeded as if the
methodology had never asked. The gate was real only while the process lived.

These tests pin the fix (survival, NOT new enforcement — SI.1 hook-enforces this state next stage):
  (a) THE regression: enter RED → `kill -9` the process → a FRESH process on the same run_id reads
      phase RED and still knows WHICH test it owes; the later GREEN persists the same way.
  (b) Two windows: window A's RED does not leak into window B's separate run — each run's phase is
      persisted independently (run_id == session_id, so the per-run key is session-scoped).
  (c) Cheap read for SI.1: `tdd_state.read_tdd_phase` answers from disk in ONE content read
      (spied `open`), with NO engine/govern/config import in its surface (asserted in a fresh
      interpreter), and degrades to `unset` rather than raising or guessing.
  (d) In-memory-as-cache: a transition made in process 1 is visible to a fresh reader process (the
      hook scenario) — the file is the truth, the sets are its cache.
  (e) Restart mid-GREEN and mid-unset each resume EXACTLY their state; a NEW run starts unset.
  (f) No enforcement change: a store-less guard is byte-identical to the pre-SI.2 object, and a
      store-backed guard blocks/allows identically within a live process.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import builtins
import json
import multiprocessing as mp
import os
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from mokata import session as S                                    # noqa: E402
from mokata import tdd_state as T                                  # noqa: E402
from mokata.config import STATE_DIRNAME, Surface                   # noqa: E402
from mokata.govern import RedBeforeGreenError, TddGuard            # noqa: E402

_CTX = mp.get_context("spawn")  # stable across POSIX/Windows; children re-import cleanly


def _repo(d):
    from mokata.init import init_repo
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _pin(sid):
    """Pin THIS 'window' to a stable session identity (the id survives a window restart)."""
    os.environ[S.SESSION_ID_ENV] = sid
    S.reset_for_test()


def _guard(root, sid):
    """A guard as a live window builds it: the session-scoped store + this session's run."""
    _pin(sid)
    return TddGuard(store=Surface.load(root).state)


# --------------------------------------------------------------- spawn-safe worker functions
def _red_then_hang(root, sid, test_id, ready):
    """A 'window' that enters RED and then hangs, so the parent can `kill -9` it MID-RED."""
    os.environ[S.SESSION_ID_ENV] = sid
    from mokata import session as _S
    from mokata.config import Surface as _Surface
    from mokata.govern import TddGuard as _TddGuard
    _S.reset_for_test()
    _TddGuard(store=_Surface.load(root).state).record_red(test_id)
    ready.put(True)
    while True:                                  # the parent SIGKILLs us here, mid-RED
        time.sleep(0.05)


def _guard_read_worker(root, sid, test_id, q):
    """A FRESH process on the same run: what does the guard resume?"""
    os.environ[S.SESSION_ID_ENV] = sid
    from mokata import session as _S
    from mokata.config import Surface as _Surface
    from mokata.govern import TddGuard as _TddGuard
    _S.reset_for_test()
    guard = _TddGuard(store=_Surface.load(root).state)
    q.put((guard.phase(), guard.owed(), guard.allow_implementation(test_id)))


def _record_worker(root, sid, red, green):
    """A FRESH process that records a transition on an existing run."""
    os.environ[S.SESSION_ID_ENV] = sid
    from mokata import session as _S
    from mokata.config import Surface as _Surface
    from mokata.govern import TddGuard as _TddGuard
    _S.reset_for_test()
    guard = _TddGuard(store=_Surface.load(root).state)
    for t in red:
        guard.record_red(t)
    for t in green:
        guard.record_green(t)


# The SI.1 hook scenario, run in a PRISTINE interpreter (a `spawn`ed multiprocessing child would
# re-import this test module — and with it the whole framework — so it could not measure the cheap
# read's import surface honestly). This is exactly what a PreToolUse hook does: boot, import the one
# module, answer from disk, exit.
_HOOK_SCRIPT = """
import json, sys
sys.path.insert(0, {src!r})
from mokata.tdd_state import read_tdd_phase
ph = read_tdd_phase({root!r}, run_id={run_id!r})
print(json.dumps({{
    "phase": ph.phase, "owed": list(ph.owed), "run_id": ph.run_id,
    "surface": sorted(m for m in sys.modules if m == "mokata" or m.startswith("mokata.")),
}}))
"""


def _hook_process(root, run_id):
    """Run the cheap read in a clean interpreter; return what it answered + what it imported."""
    env = dict(os.environ)
    env.pop(S.SESSION_ID_ENV, None)              # a hook inherits no pin unless SI.1 sets one
    src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
    out = subprocess.run(
        [sys.executable, "-c", _HOOK_SCRIPT.format(src=src, root=root, run_id=run_id)],
        capture_output=True, text=True, env=env, timeout=60, check=True,
    )
    return json.loads(out.stdout)


def _run(target, *args):
    p = _CTX.Process(target=target, args=args)
    p.start()
    p.join(60)
    return p


class _Base(unittest.TestCase):
    def setUp(self):
        os.environ.pop(S.SESSION_ID_ENV, None)
        S.reset_for_test()

    def tearDown(self):
        os.environ.pop(S.SESSION_ID_ENV, None)
        S.reset_for_test()


# ===================================================================== THE headline test
class TestRegression(_Base):
    def test_si_2_regression(self):
        """Enter RED → kill −9 → a fresh process on the SAME run_id still owes the failing test;
        the later GREEN persists identically. The discipline outlives the process."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            ready = _CTX.Queue()

            # --- window enters RED, then is killed −9 MID-RED (no cleanup, no flush, no atexit) ---
            child = _CTX.Process(target=_red_then_hang, args=(d, "sessRED", "test_login", ready))
            child.start()
            self.assertTrue(ready.get(timeout=60))
            child.kill()                       # SIGKILL on POSIX / TerminateProcess on Windows
            child.join(60)
            self.assertNotEqual(child.exitcode, 0)   # it did NOT exit cleanly

            # --- a FRESH process, same run_id: RED survives, and it knows WHAT it owes ---
            q = _CTX.Queue()
            _run(_guard_read_worker, d, "sessRED", "test_login", q)
            phase, owed, allowed = q.get(timeout=60)
            self.assertEqual(phase, T.PHASE_RED)
            self.assertEqual(owed, ["test_login"])
            self.assertTrue(allowed)           # the RED is on record → implementation is allowed

            # ... and the cheap read (SI.1's path) says the same thing from disk
            self.assertEqual(T.read_tdd_phase(d, run_id="sessRED").phase, T.PHASE_RED)
            self.assertEqual(T.read_tdd_phase(d, run_id="sessRED").owed, ("test_login",))

            # --- the test now PASSES: GREEN persists the same way, and survives the same way ---
            _run(_record_worker, d, "sessRED", (), ("test_login",))
            after = T.read_tdd_phase(d, run_id="sessRED")
            self.assertEqual(after.phase, T.PHASE_GREEN)
            self.assertEqual(after.owed, ())
            self.assertEqual(after.red, ("test_login",))
            self.assertEqual(after.green, ("test_login",))

            q2 = _CTX.Queue()
            _run(_guard_read_worker, d, "sessRED", "test_login", q2)
            phase2, owed2, allowed2 = q2.get(timeout=60)
            self.assertEqual((phase2, owed2, allowed2), (T.PHASE_GREEN, [], True))


# ===================================================================== two windows
class TestTwoWindows(_Base):
    def test_window_a_red_does_not_leak_into_window_b(self):
        """Session scoping holds: A's RED is invisible to B's separate run, and both persist."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)

            guard_a = _guard(d, "winA")
            guard_a.record_red("test_a")

            guard_b = _guard(d, "winB")                     # a different window == a different run
            self.assertEqual(guard_b.phase(), T.PHASE_UNSET)
            self.assertEqual(guard_b.owed(), [])
            self.assertFalse(guard_b.allow_implementation("test_a"))   # A's RED is NOT B's licence
            with self.assertRaises(RedBeforeGreenError):
                guard_b.guard_implementation("test_a")

            guard_b.record_red("test_b")

            # both persisted, independently, under their own run keys
            self.assertEqual(T.read_tdd_phase(d, run_id="winA").owed, ("test_a",))
            self.assertEqual(T.read_tdd_phase(d, run_id="winB").owed, ("test_b",))
            state = os.path.join(d, ".mokata", "temp_local", STATE_DIRNAME)
            self.assertTrue(os.path.exists(os.path.join(state, "tdd_phase__winA.json")))
            self.assertTrue(os.path.exists(os.path.join(state, "tdd_phase__winB.json")))

    def test_ambiguous_runs_never_guess(self):
        """With two windows holding TDD state and no run named, the cheap read answers `unset`
        rather than leaking one window's RED into the other (SI.1 must pass/pin the run_id)."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _guard(d, "winA").record_red("test_a")
            _guard(d, "winB").record_red("test_b")
            os.environ.pop(S.SESSION_ID_ENV, None)

            ph = T.read_tdd_phase(d)                       # no run_id, no pin, two candidates
            self.assertEqual(ph.phase, T.PHASE_UNSET)
            self.assertIsNone(ph.run_id)
            self.assertEqual(ph.owed, ())


# ===================================================================== the cheap read (SI.1)
class TestCheapRead(_Base):
    def test_one_content_read(self):
        """The SI.1 read function opens exactly ONE file: the run's phase. No lock, no scan."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _guard(d, "sessX").record_red("test_x")

            opened = []
            real_open = builtins.open

            def _spy(file, *a, **kw):
                opened.append(str(file))
                return real_open(file, *a, **kw)

            builtins.open = _spy
            try:
                ph = T.read_tdd_phase(d, run_id="sessX")
            finally:
                builtins.open = real_open

            self.assertEqual(ph.phase, T.PHASE_RED)
            self.assertEqual(len(opened), 1, opened)
            self.assertTrue(opened[0].endswith(os.path.join("state", "tdd_phase__sessX.json")))

    def test_import_surface_is_cheap(self):
        """A hook is a separate short-lived process with a latency budget: importing the cheap-read
        module must NOT drag in the engine, the govern package, config/manifest/router, memory, or
        the CLI. Measured in a PRISTINE interpreter — the real hook's situation."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _guard(d, "sessH").record_red("test_h")

            got = _hook_process(d, "sessH")
            self.assertEqual((got["phase"], got["owed"], got["run_id"]),
                             (T.PHASE_RED, ["test_h"], "sessH"))

            surface = got["surface"]
            forbidden = [m for m in surface if m.split(".")[1:2] and m.split(".")[1] in {
                "engine", "govern", "config", "manifest", "router", "detect", "memory",
                "knowledge", "cli", "brainstorm", "skills", "mcp",
            }]
            self.assertEqual(forbidden, [], f"cheap read pulled in heavy modules: {surface}")
            # the WHOLE surface: the package itself + the MS.S1/MS.S6 state primitives. (Resolving
            # a run_id from the pin/scan adds `mokata.session` — a stdlib-only constants module —
            # lazily; with an explicit run_id even that is not paid.)
            #
            # D5 added `mokata.errors` + `mokata.degrade`: `oslock.LockTimeout` is now a
            # `MokataError`, so the taxonomy rides in with it. Both are STDLIB-ONLY leaf modules
            # (degrade imports `sys`/`dataclasses`/`typing` and nothing else; its run_mode /
            # team_health / dsn imports are all function-local, deliberately), so the hook's
            # latency budget is untouched and the `forbidden` assertion above — the one that
            # encodes the actual rule: no engine, govern, config, memory, knowledge or CLI —
            # still passes unchanged. The pin is widened by exactly the two cheap modules, not
            # relaxed.
            self.assertEqual(
                surface, ["mokata", "mokata.atomicfile", "mokata.degrade", "mokata.errors",
                          "mokata.oslock", "mokata.state", "mokata.tdd_state"],
            )

    def test_degrades_clean_and_never_raises(self):
        """Absent / corrupt / unresolvable state reads as `unset` — a hook must fail OPEN."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            self.assertEqual(T.read_tdd_phase(d, run_id="nobody").phase, T.PHASE_UNSET)
            self.assertEqual(T.read_tdd_phase(d).phase, T.PHASE_UNSET)          # no runs at all

            _guard(d, "sessC").record_red("test_c")
            path = os.path.join(d, ".mokata", "temp_local", STATE_DIRNAME, "tdd_phase__sessC.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{ this is not json")
            self.assertEqual(T.read_tdd_phase(d, run_id="sessC").phase, T.PHASE_UNSET)

            with open(path, "w", encoding="utf-8") as fh:                       # wrong shape
                json.dump({"run_id": "sessC", "red": "test_c", "green": 7}, fh)
            self.assertEqual(T.read_tdd_phase(d, run_id="sessC").phase, T.PHASE_UNSET)

            self.assertEqual(T.read_tdd_phase("/no/such/repo").phase, T.PHASE_UNSET)

    def test_run_id_resolution_order(self):
        """Explicit arg > MOKATA_SESSION_ID pin > the sole run with TDD state."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _guard(d, "sessOnly").record_red("test_only")

            os.environ.pop(S.SESSION_ID_ENV, None)
            self.assertEqual(T.read_tdd_phase(d).run_id, "sessOnly")            # sole run

            os.environ[S.SESSION_ID_ENV] = "sessOnly"                           # the pin
            self.assertEqual(T.read_tdd_phase(d).owed, ("test_only",))

            os.environ[S.SESSION_ID_ENV] = "somethingElse"                      # pin beats scan
            self.assertEqual(T.read_tdd_phase(d).phase, T.PHASE_UNSET)
            self.assertEqual(T.read_tdd_phase(d, run_id="sessOnly").phase, T.PHASE_RED)  # arg wins


# ===================================================================== memory is a cache
class TestInMemoryIsCache(_Base):
    def test_transition_in_one_process_is_visible_to_a_fresh_reader(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _run(_record_worker, d, "sessP", ("test_p",), ())          # process 1 records RED

            self.assertEqual(T.read_tdd_phase(d, run_id="sessP").owed, ("test_p",))
            guard = _guard(d, "sessP")                                 # process 2 (this one)
            self.assertTrue(guard.allow_implementation("test_p"))

    def test_a_live_guard_sees_another_process_transition(self):
        """The sets are a CACHE: a guard already constructed re-reads the persisted truth, so a RED
        recorded by a sibling process lands in this one's answers without a restart."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            guard = _guard(d, "sessL")
            self.assertFalse(guard.allow_implementation("test_l"))     # nothing owed yet

            _run(_record_worker, d, "sessL", ("test_l",), ())          # the sibling records RED

            self.assertTrue(guard.allow_implementation("test_l"))      # ... seen, no restart
            self.assertEqual(guard.phase(), T.PHASE_RED)

    def test_a_second_guard_in_the_same_process_resumes_the_run(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _guard(d, "sessS").record_red("test_s")
            self.assertEqual(_guard(d, "sessS").owed(), ["test_s"])    # a NEW guard object resumes


# ===================================================================== restart semantics
class TestRestartSemantics(_Base):
    def test_restart_mid_red_resumes_red(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _guard(d, "sessR").record_red("test_r")
            del_guard = _guard(d, "sessR")                             # "restart": a fresh guard
            self.assertEqual(del_guard.phase(), T.PHASE_RED)
            self.assertEqual(del_guard.owed(), ["test_r"])

    def test_restart_mid_green_resumes_green(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            g = _guard(d, "sessG")
            g.record_red("test_g")
            g.record_green("test_g")
            self.assertEqual(_guard(d, "sessG").phase(), T.PHASE_GREEN)
            self.assertEqual(_guard(d, "sessG").owed(), [])
            self.assertEqual(T.read_tdd_phase(d, run_id="sessG").phase, T.PHASE_GREEN)

    def test_restart_mid_unset_resumes_unset(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _guard(d, "sessU")                                         # a run that recorded nothing
            self.assertEqual(_guard(d, "sessU").phase(), T.PHASE_UNSET)
            self.assertEqual(T.read_tdd_phase(d, run_id="sessU").phase, T.PHASE_UNSET)

    def test_a_new_run_starts_unset(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _guard(d, "sessOld").record_red("test_old")
            fresh = _guard(d, "sessNew")                               # a NEW run == a clean slate
            self.assertEqual(fresh.phase(), T.PHASE_UNSET)
            with self.assertRaises(RedBeforeGreenError):
                fresh.guard_implementation("test_old")

    def test_partial_green_still_owes_the_rest(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            g = _guard(d, "sessM")
            g.record_red("test_1")
            g.record_red("test_2")
            g.record_green("test_1")
            self.assertEqual(_guard(d, "sessM").phase(), T.PHASE_RED)
            self.assertEqual(_guard(d, "sessM").owed(), ["test_2"])


# ===================================================================== no enforcement change
class TestNoEnforcementChange(_Base):
    def test_store_less_guard_is_unchanged(self):
        """The pre-SI.2 object, byte for byte: memory-only, no run, no file."""
        guard = TddGuard()
        self.assertIsNone(guard.run_id)
        with self.assertRaises(RedBeforeGreenError):
            guard.guard_implementation("test_login")
        guard.record_red("test_login")
        guard.guard_implementation("test_login")            # now allowed, no raise
        self.assertTrue(guard.allow_implementation("test_login"))
        self.assertFalse(guard.allow_implementation("test_other"))

    def test_persisted_guard_blocks_and_allows_identically(self):
        """Within one live process the store-backed guard's verdicts are what they always were."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            guard = _guard(d, "sessE")
            with self.assertRaises(RedBeforeGreenError):
                guard.guard_implementation("test_e")
            guard.record_red("test_e")
            guard.guard_implementation("test_e")
            self.assertFalse(guard.allow_implementation("test_never_written"))

    def test_ledger_events_are_unchanged(self):
        class _Led:
            def __init__(self):
                self.events = []

            def record(self, kind, **kw):
                self.events.append((kind, kw))

        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _pin("sessLed")
            led = _Led()
            guard = TddGuard(ledger=led, store=Surface.load(d).state)
            guard.record_red("test_x")
            guard.record_green("test_x")
            try:
                guard.guard_implementation("test_y")
            except RedBeforeGreenError:
                pass
            guard.guard_implementation("test_x")
            self.assertEqual(
                led.events,
                [("tdd", {"event": "red", "test": "test_x"}),
                 ("tdd", {"event": "green", "test": "test_x"}),
                 ("tdd", {"event": "blocked", "test": "test_y", "gate": "no-code-without-failing-test"}),
                 ("tdd", {"event": "allowed", "test": "test_x", "gate": "no-code-without-failing-test"})],
            )

    def test_bugflow_reproducer_red_persists(self):
        """`BugFlow`'s reproducer-before-fix gate rides the same guard — with a store, the
        reproducer's RED survives a restart too."""
        from mokata.modes import Bug, BugFlow, ReproRequiredError

        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _pin("sessBug")
            store = Surface.load(d).state
            flow = BugFlow(Bug("B1", "crash on save"), store=store)
            with self.assertRaises(ReproRequiredError):
                flow.start_fix()
            flow.reproduce("test_crash_on_save")

            self.assertEqual(T.read_tdd_phase(d, run_id="sessBug").owed, ("bug:B1",))
            # a fresh process on the same run still has the reproducer on record
            resumed = BugFlow(Bug("B1", "crash on save"), store=Surface.load(d).state)
            self.assertTrue(resumed.guard.allow_implementation("bug:B1"))


# ===================================================================== the persisted contract
class TestPersistedShape(_Base):
    def test_value_shape_and_key(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            g = _guard(d, "sessV")
            g.record_red("test_b")
            g.record_red("test_a")
            g.record_green("test_a")
            g.record_red("test_a")                        # a re-record is idempotent

            path = os.path.join(d, ".mokata", "temp_local", STATE_DIRNAME, "tdd_phase__sessV.json")
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertEqual(data, {"run_id": "sessV", "red": ["test_a", "test_b"],
                                    "green": ["test_a"]})
            self.assertEqual(T.state_key("sessV"), "tdd_phase__sessV")

    def test_state_dir_matches_config(self):
        """The cheap read spells the state dir as a literal (to stay import-cheap) — it must equal
        the owner's."""
        self.assertEqual(T.STATE_DIRNAME, STATE_DIRNAME)
        self.assertEqual(T.state_dir("/repo"),
                         os.path.join("/repo", ".mokata", "temp_local", "state"))

    def test_key_is_not_session_scoped_by_name_rewrite(self):
        """The run id is IN the key, so a SessionScopedStore passes it through verbatim (like the
        `pipeline_run__` checkpoints) — no double-scoping."""
        from mokata.session_state import SESSION_SCOPED_KEYS
        self.assertNotIn(T.state_key("abc"), SESSION_SCOPED_KEYS)
        self.assertFalse(any(k.startswith(T.TDD_STATE_PREFIX) for k in SESSION_SCOPED_KEYS))

    def test_phase_of(self):
        self.assertEqual(T.phase_of([], []), T.PHASE_UNSET)
        self.assertEqual(T.phase_of([], ["a"]), T.PHASE_UNSET)      # GREEN with no RED owes nothing
        self.assertEqual(T.phase_of(["a"], []), T.PHASE_RED)
        self.assertEqual(T.phase_of(["a", "b"], ["a"]), T.PHASE_RED)
        self.assertEqual(T.phase_of(["a"], ["a", "b"]), T.PHASE_GREEN)


if __name__ == "__main__":
    unittest.main()
