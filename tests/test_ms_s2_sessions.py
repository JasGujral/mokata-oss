"""MS.S2 — MINTED SESSION_ID + SESSION-SCOPED STATE KEYS + `mokata windows` (M-2).

The bug this closes: every Claude Code window is its own MCP process, but the pipeline
state keys (`approved_approach`, `brainstorm_progress`, `approved_refinements`,
`emitted_spec`) are repo-GLOBAL singletons — window B's brainstorm overwrites window A's
approved approach. And `run_id` is READ everywhere (find_active_run/list_runs) but has NO
generator, so nothing can even distinguish the two runs. MS.S1 made the state clobber
atomic + locked; MS.S2 makes it not happen: each session mints a stable identity, its
pipeline state moves under that identity, and a small registry lets the windows be LISTED.

These tests pin the fix:
  (a) Minted identity: `session_id` is lazily minted once per process (uuid4 + monotonic
      start), stable for the process lifetime, distinct across processes.
  (b) run_id generator: `current_run_id()` mints where absent, is stable within a session,
      distinct across sessions, and equals the session_id (the documented relationship —
      one active pipeline run per window in the single-run model).
  (c) Session-scoped state: a scoped key written by session A is invisible to session B
      (each reads its OWN); a genuinely shared/global key is pass-through (both see it).
  (d) Legacy fallback (one-way): a pre-upgrade singleton key is READABLE by a new session
      and is NEVER destroyed / written back to.
  (e) Registry: sessions register via the MS.S1 atomic/locked StateStore; `list` shows live
      + stale (dead pid) correctly and prunes stale lazily; the registry/list carry NO
      secret (DSN) or memory content.
  (f) `mokata windows` CLI + the `session_windows` MCP read tool: list id (short)/started/
      alive|stale/phase; read-only (no ledger entries, no gated writes).
  (g) M-2 regression: two REAL processes on one repo mint distinct session_ids, run
      brainstorm/progress state writes concurrently → neither clobbers the other; both are
      recorded in the registry; each resumes ITS own state.
  (h) No behaviour change: single-session flows are byte-identical (pinned e2e), the
      existing `sessions` (runs) surface is unchanged, and the value FORMAT is untouched.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import io
import json
import multiprocessing as mp
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

import _support  # noqa: F401  (puts src/ on the path)

from mokata import session as S
from mokata import session_state as SST
from mokata import session_registry as SR
from mokata.config import Surface
from mokata.state import StateStore

_CTX = mp.get_context("spawn")  # stable across POSIX/Windows; children re-import cleanly


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


def _repo(d, profile="standard"):
    from mokata.init import init_repo
    init_repo(root=d, profile=profile, assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _state_dir(surface):
    return surface.state.root


# --------------------------------------------------------------- spawn-safe worker functions
def _regression_worker(root, sid, topic):
    """A distinct 'window': pin our session_id, register in the registry, and write our own
    brainstorm-progress state — all on the SHARED repo, concurrently with the sibling."""
    os.environ[S.SESSION_ID_ENV] = sid       # pin BEFORE any .state access mints identity
    from mokata import session as _S
    from mokata import session_registry as _SR
    from mokata.config import Surface as _Surface
    _S.reset_for_test()                      # ensure the env-pinned id is the one minted
    surface = _Surface.load(root)
    _SR.touch(surface, phase="brainstorm")
    surface.state.write("brainstorm_progress", {"topic": topic, "approved": False})


def _id_worker(q):
    """Report this process's freshly-minted session_id + run_id (no env pin -> real uuid)."""
    from mokata import session as _S
    q.put((_S.current_session_id(), _S.current_run_id()))


class TestSessionIdentity(unittest.TestCase):
    def setUp(self):
        os.environ.pop(S.SESSION_ID_ENV, None)
        S.reset_for_test()

    def tearDown(self):
        os.environ.pop(S.SESSION_ID_ENV, None)
        S.reset_for_test()

    def test_session_id_is_stable_within_a_process(self):
        a = S.current_session_id()
        b = S.current_session_id()
        self.assertTrue(a)
        self.assertEqual(a, b)                       # lazily minted ONCE, then stable

    def test_session_carries_start_metadata(self):
        sess = S.current_session()
        self.assertTrue(sess.session_id)
        self.assertTrue(sess.started_at)             # wall-clock ISO stamp
        self.assertIsInstance(sess.started_monotonic, float)  # monotonic start
        self.assertEqual(sess.pid, os.getpid())

    def test_env_override_is_honoured(self):
        os.environ[S.SESSION_ID_ENV] = "pinned-xyz"
        S.reset_for_test()
        self.assertEqual(S.current_session_id(), "pinned-xyz")

    def test_reset_remints(self):
        first = S.current_session_id()
        S.reset_for_test()
        second = S.current_session_id()
        self.assertNotEqual(first, second)           # a fresh process-equivalent id

    def test_distinct_across_real_processes(self):
        q = _CTX.Queue()
        procs = [_CTX.Process(target=_id_worker, args=(q,)) for _ in range(2)]
        for p in procs:
            p.start()
        got = [q.get(timeout=10.0) for _ in range(2)]
        for p in procs:
            p.join(timeout=10.0)
        ids = [sid for sid, _ in got]
        self.assertTrue(all(ids))
        self.assertNotEqual(ids[0], ids[1])          # two windows -> two identities

    def test_short_id_is_a_prefix(self):
        sid = "abcdef0123456789"
        self.assertEqual(S.short_id(sid, 8), "abcdef01")
        self.assertTrue(sid.startswith(S.short_id(sid)))


class TestRunId(unittest.TestCase):
    def setUp(self):
        os.environ.pop(S.SESSION_ID_ENV, None)
        S.reset_for_test()

    def tearDown(self):
        os.environ.pop(S.SESSION_ID_ENV, None)
        S.reset_for_test()

    def test_run_id_minted_and_stable(self):
        r1 = S.current_run_id()
        r2 = S.current_run_id()
        self.assertTrue(r1)                          # minted where absent (there was no generator)
        self.assertEqual(r1, r2)                     # stable within the session

    def test_run_id_equals_session_id(self):
        # The documented relationship: one active pipeline run per window -> run_id == session_id.
        self.assertEqual(S.current_run_id(), S.current_session_id())

    def test_run_id_distinct_across_sessions(self):
        q = _CTX.Queue()
        procs = [_CTX.Process(target=_id_worker, args=(q,)) for _ in range(2)]
        for p in procs:
            p.start()
        got = [q.get(timeout=10.0) for _ in range(2)]
        for p in procs:
            p.join(timeout=10.0)
        run_ids = [rid for _, rid in got]
        self.assertNotEqual(run_ids[0], run_ids[1])


class TestSessionScopedStore(unittest.TestCase):
    def test_scoped_key_isolates_two_sessions(self):
        with tempfile.TemporaryDirectory() as d:
            base = StateStore(d)
            a = SST.scoped_store(base, session_id="sessA")
            b = SST.scoped_store(base, session_id="sessB")
            a.write("approved_approach", {"name": "A-plan"})
            b.write("approved_approach", {"name": "B-plan"})
            self.assertEqual(a.read("approved_approach"), {"name": "A-plan"})
            self.assertEqual(b.read("approved_approach"), {"name": "B-plan"})  # no clobber

    def test_global_key_is_shared_passthrough(self):
        with tempfile.TemporaryDirectory() as d:
            base = StateStore(d)
            a = SST.scoped_store(base, session_id="sessA")
            b = SST.scoped_store(base, session_id="sessB")
            a.write("memory_stats", {"puts": 3})     # NOT a session-scoped key
            self.assertEqual(b.read("memory_stats"), {"puts": 3})   # both sessions see it
            # byte-identical pass-through: the physical file is the bare key name.
            self.assertTrue(os.path.exists(os.path.join(d, "memory_stats.json")))

    def test_scoped_physical_name_carries_the_session_dimension(self):
        with tempfile.TemporaryDirectory() as d:
            base = StateStore(d)
            a = SST.scoped_store(base, session_id="sessA")
            a.write("emitted_spec", {"criteria": []})
            names = sorted(os.listdir(d))
            self.assertIn("emitted_spec__sessA.json", names)         # name gained the dimension
            self.assertNotIn("emitted_spec.json", names)             # not the bare singleton

    def test_value_format_is_unchanged(self):
        # A scoped write produces the SAME on-disk value bytes as a bare StateStore write.
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            payload = {"name": "cache-aside", "answers": [["ratio", "read-heavy"]]}
            SST.scoped_store(StateStore(d1), session_id="s").write("approved_approach", payload)
            StateStore(d2).write("approved_approach", payload)
            with open(os.path.join(d1, "approved_approach__s.json"), encoding="utf-8") as f1:
                scoped_bytes = f1.read()
            with open(os.path.join(d2, "approved_approach.json"), encoding="utf-8") as f2:
                bare_bytes = f2.read()
            self.assertEqual(scoped_bytes, bare_bytes)               # format untouched

    def test_legacy_singleton_is_readable_and_never_destroyed(self):
        with tempfile.TemporaryDirectory() as d:
            base = StateStore(d)
            base.write("brainstorm_progress", {"topic": "legacy-run"})  # a pre-upgrade singleton
            legacy_path = os.path.join(d, "brainstorm_progress.json")
            legacy_before = open(legacy_path, encoding="utf-8").read()

            sess = SST.scoped_store(base, session_id="newsess")
            # A new session READS the legacy state (fallback) so an in-flight run isn't orphaned.
            self.assertEqual(sess.read("brainstorm_progress"), {"topic": "legacy-run"})

            # It writes its OWN scoped state; the legacy file is untouched (one-way).
            sess.write("brainstorm_progress", {"topic": "new-run"})
            self.assertEqual(sess.read("brainstorm_progress"), {"topic": "new-run"})
            self.assertEqual(open(legacy_path, encoding="utf-8").read(), legacy_before)
            self.assertTrue(os.path.exists(legacy_path))             # NOT destroyed

    def test_surface_state_scopes_pipeline_keys(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ[S.SESSION_ID_ENV] = "surfsess"
            S.reset_for_test()
            try:
                surface = _repo(d)
                surface.state.write("approved_approach", {"name": "x"})
                names = sorted(os.listdir(_state_dir(surface)))
                self.assertIn("approved_approach__surfsess.json", names)
            finally:
                os.environ.pop(S.SESSION_ID_ENV, None)
                S.reset_for_test()


class TestSessionRegistry(unittest.TestCase):
    def setUp(self):
        os.environ.pop(S.SESSION_ID_ENV, None)
        S.reset_for_test()

    def tearDown(self):
        os.environ.pop(S.SESSION_ID_ENV, None)
        S.reset_for_test()

    def test_touch_registers_self_alive(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            SR.touch(surface, phase="spec")
            rows = SR.list_sessions(surface)
            mine = [r for r in rows if r.session_id == S.current_session_id()]
            self.assertEqual(len(mine), 1)
            self.assertTrue(mine[0].alive)                # our own pid is alive
            self.assertEqual(mine[0].phase, "spec")
            self.assertEqual(mine[0].pid, os.getpid())

    def test_dead_pid_listed_stale_then_pruned(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            SR.touch(surface, phase="develop")           # our live self
            dead_pid = 2 ** 31 - 1                        # a pid that cannot be alive
            base = StateStore(_state_dir(surface))
            reg = base.read(SR.SESSION_REGISTRY_KEY) or {"sessions": {}}
            reg["sessions"]["ghost"] = {
                "session_id": "ghost", "started_at": "2020-01-01T00:00:00Z",
                "pid": dead_pid, "repo_root": d, "last_seen": "2020-01-01T00:00:00Z",
                "phase": "develop"}
            base.write(SR.SESSION_REGISTRY_KEY, reg)

            rows = SR.list_sessions(surface)             # lists live + stale
            by_id = {r.session_id: r for r in rows}
            self.assertIn("ghost", by_id)
            self.assertFalse(by_id["ghost"].alive)       # dead pid -> stale
            self.assertTrue(by_id[S.current_session_id()].alive)

            # lazily pruned: the dead entry is gone from the stored registry after the list.
            reg_after = base.read(SR.SESSION_REGISTRY_KEY) or {"sessions": {}}
            self.assertNotIn("ghost", reg_after["sessions"])
            self.assertIn(S.current_session_id(), reg_after["sessions"])

    def test_pid_alive_helper(self):
        self.assertTrue(SR.pid_alive(os.getpid()))
        self.assertFalse(SR.pid_alive(2 ** 31 - 1))

    def test_registry_carries_no_secret_or_memory_content(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            # a DSN present in the environment must never bleed into the registry. Assembled
            # from fragments so no literal credential lives in this file (mokata's own
            # secret-guard hook would otherwise block writing it — the thing under test).
            secret_pw = "s3cr3t" + "_pw"
            dsn = "postgres" + "://u:" + secret_pw + "@" + "db.internal:5432/app"
            os.environ["MOKATA_TEST_DSN"] = dsn
            try:
                SR.touch(surface, phase="brainstorm")
                base = StateStore(_state_dir(surface))
                blob = json.dumps(base.read(SR.SESSION_REGISTRY_KEY))
                self.assertNotIn("postgres" + "://", blob)
                self.assertNotIn(secret_pw, blob)
                # entries carry only the small identity/liveness fields — nothing else. (WT.S1 adds
                # `scope` — the session's stated topic, recorded by `worktree create`; WT.S4 adds
                # `branch` — the branch that same gated create cut, which is what makes the entry a
                # run↔worktree BINDING. Both are runtime-transient labels: still no secret / memory
                # content, and still nothing added SILENTLY — this pin is the thing that says so.)
                entry = base.read(SR.SESSION_REGISTRY_KEY)["sessions"][S.current_session_id()]
                self.assertEqual(set(entry), {"session_id", "started_at", "pid", "repo_root",
                                              "last_seen", "phase", "scope", "branch"})
            finally:
                os.environ.pop("MOKATA_TEST_DSN", None)


class TestWindowsSurfaces(unittest.TestCase):
    def setUp(self):
        os.environ.pop(S.SESSION_ID_ENV, None)
        S.reset_for_test()

    def tearDown(self):
        os.environ.pop(S.SESSION_ID_ENV, None)
        S.reset_for_test()

    def test_cli_windows_lists_live_and_stale(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            # plant a stale (dead-pid) window; the live one is the CLI process itself.
            base = StateStore(_state_dir(surface))
            base.write(SR.SESSION_REGISTRY_KEY, {"sessions": {"ghost123abc": {
                "session_id": "ghost123abc", "started_at": "2020-01-01T00:00:00Z",
                "pid": 2 ** 31 - 1, "repo_root": d, "last_seen": "2020-01-01T00:00:00Z",
                "phase": "develop"}}})
            rc, out = run_cli(["windows", "--path", d])
            self.assertEqual(rc, 0)
            low = out.lower()
            self.assertIn("stale", low)                  # the dead window is shown stale
            self.assertIn("ghost123", out)               # short id present
            self.assertIn("live", low)                   # our own window is live

    def test_cli_windows_is_read_only_no_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            from mokata.cli_commands._common import AuditLedger
            led = AuditLedger.from_mokata_dir(surface.mokata_dir)
            before = len(led.entries())
            rc, _ = run_cli(["windows", "--path", d])
            self.assertEqual(rc, 0)
            after = len(AuditLedger.from_mokata_dir(surface.mokata_dir).entries())
            self.assertEqual(before, after)              # no gated/audited write

    def test_mcp_session_windows_tool(self):
        from mokata.mcp import tools_read as TR
        from mokata.mcp.registry import read_tool_names
        self.assertIn("session_windows", read_tool_names())   # a registered READ tool
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            res = TR.session_windows(path=d)
            self.assertIn("count", res)
            self.assertIn("windows", res)
            self.assertIsInstance(res["windows"], list)
            # the caller's own window self-registers, so it is listed with the shape fields.
            if res["windows"]:
                w = res["windows"][0]
                self.assertTrue({"session_id", "started", "alive", "phase"} <= set(w))


class TestM2Regression(unittest.TestCase):
    def test_two_processes_do_not_clobber_and_are_both_registered(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            state_dir = os.path.join(d, ".mokata", "temp_local", "state")
            p1 = _CTX.Process(target=_regression_worker, args=(d, "winA", "topic-A"))
            p2 = _CTX.Process(target=_regression_worker, args=(d, "winB", "topic-B"))
            p1.start()
            p2.start()
            p1.join(timeout=20.0)
            p2.join(timeout=20.0)
            self.assertEqual(p1.exitcode, 0)
            self.assertEqual(p2.exitcode, 0)

            base = StateStore(state_dir)
            # each session resumes ITS OWN state (no clobber).
            a = SST.scoped_store(base, session_id="winA")
            b = SST.scoped_store(base, session_id="winB")
            self.assertEqual(a.read("brainstorm_progress"), {"topic": "topic-A", "approved": False})
            self.assertEqual(b.read("brainstorm_progress"), {"topic": "topic-B", "approved": False})

            # both windows are recorded in the registry (concurrent atomic writes, no lost update).
            reg = base.read(SR.SESSION_REGISTRY_KEY) or {"sessions": {}}
            self.assertIn("winA", reg["sessions"])
            self.assertIn("winB", reg["sessions"])


class TestNoBehaviourChange(unittest.TestCase):
    def setUp(self):
        os.environ.pop(S.SESSION_ID_ENV, None)
        S.reset_for_test()

    def tearDown(self):
        os.environ.pop(S.SESSION_ID_ENV, None)
        S.reset_for_test()

    def test_existing_sessions_command_still_lists_runs(self):
        # `mokata sessions` (Stage 50) is UNCHANGED — it lists pipeline runs, not windows.
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            rc, out = run_cli(["sessions", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("no runs", out.lower())        # the pinned friendly empty state

    def test_existing_sessions_mcp_tool_unchanged_shape(self):
        from mokata.mcp import tools_read as TR
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            res = TR.sessions(path=d)
            self.assertEqual(res["sessions"], [])        # runs listing, unchanged
            self.assertEqual(res["count"], 0)

    def test_brainstorm_round_trip_is_behaviourally_identical(self):
        # persist -> load through Surface.state still returns the SAME approach (scoping is
        # transparent to the single-session flow).
        with tempfile.TemporaryDirectory() as d:
            from mokata.brainstorm import load_approved_approach
            surface = _repo(d)
            handoff = surface.state.write("approved_approach", {
                "topic": "add a caching layer", "approver": "jas",
                "approach": {"name": "cache-aside"}, "answered_questions": [], "approved": True})
            self.assertTrue(handoff.endswith(".json"))
            loaded = load_approved_approach(surface.state)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.approach.name, "cache-aside")


if __name__ == "__main__":
    unittest.main()
