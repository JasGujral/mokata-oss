"""0.0.26 O12b SESSIONSTART-BUDGET — the SessionStart briefing meets the `context` kind's 150 ms.

Business rules — what must be true for the user:
  * Opening a session waits on mokata's SessionStart hook. Its slow facts — the git status, the
    due intentions, the optional-dependency check, the hook-wiring and skills-menu checks, the MCP
    reachability check, the structure summary — are served from a cache that ONE detached refresher
    keeps current, so the hook answers inside 150 ms (p50, cold process, normalised) on a repo
    whose tree is too big to summarise inline.
  * Nothing is silently omitted: a fact with no answer yet SAYS it is being checked in the
    background; a stale answer says WHEN it was taken and that it is being refreshed.
  * The project's rules, the resume hint and the "changed since last session" line are NEVER
    served stale: they are cached only against exactly what they read and re-derived on a change.
  * The calibration ledger write and the next session's diff baseline move to the refresher — and
    when no refresher could start, SessionStart still does them itself.
  * In-process callers (tests, the IDE) keep the synchronous derivation; nothing leaks between calls
    (the front-door flag, and the dirty-track hook-time stamp that froze P33's heartbeat).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import io
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)
from _support import sqlite_disk_ok

import mokata_emit
from mokata import briefing_cache as BC
from mokata import hook_cli, perf
from mokata.config import Surface
from mokata.init import init_repo
from mokata.quiet_spawn import hidden

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")


def _silent(*_a, **_k):
    pass


def _repo(d: str, files: int = 0) -> None:
    subprocess.run(["git", "init", "-q", d], check=True, capture_output=True,
                   stdin=subprocess.DEVNULL, **hidden())
    for n in range(files):
        with open(os.path.join(d, f"m{n}.py"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("def f(x):\n    return x\n")
    init_repo(root=d, profile="standard", assume_yes=True, out=_silent)


def _front_door(d: str, sid: str = "S-o12b") -> str:
    """The SessionStart hook as Claude Code reaches it: through the `mokata_emit` front door."""
    payload = json.dumps({"session_id": sid, "cwd": d, "hook_event_name": "SessionStart",
                          "source": "startup"})
    out = io.StringIO()
    saved, sys.stdin = sys.stdin, io.StringIO(payload)
    try:
        with redirect_stdout(out):
            mokata_emit.main(["session-start"])
    finally:
        sys.stdin = saved
    return json.loads(out.getvalue())["hookSpecificOutput"]["additionalContext"]


_SLOW = ("mokata.live_context.git_line", "mokata.hook_cli._intentions_due",
         "mokata.hook_cli._mode_dependencies_missing", "mokata.hook_cli._mcp_reachability_warning",
         "mokata.bootstrap._wiring_drift_line", "mokata.skills_visibility.briefing_offer",
         "mokata.knowledge.layer.graph_structure_line")


@unittest.skipUnless(sqlite_disk_ok(), "sandbox cannot back an on-disk SQLite DB")
class TheFrontDoorServesTheSlowLinesFromTheCache(unittest.TestCase):

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.d = os.path.realpath(self._t.name)
        _repo(self.d, files=0)
        self.spawned = []
        self.patches = [mock.patch("mokata_emit.spawn_mokata_emit",
                                   side_effect=lambda a, cwd: self.spawned.append(a) or True),
                        mock.patch("mokata.knowledge.structure_cache._small_tree",
                                   return_value=False)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self._t.cleanup()

    def _no_slow_work(self):
        return [mock.patch(t, side_effect=AssertionError(f"{t} ran on session start"))
                for t in _SLOW]

    def test_R_O12b_no_answer_yet_is_SAID_and_one_refresher_is_started(self):
        stack = self._no_slow_work()
        for p in stack:
            p.start()
        try:
            ctx = _front_door(self.d)
        finally:
            for p in stack:
                p.stop()
        for name in (BC.GIT, BC.INTENTIONS, BC.MCP, BC.MODE_CLI, BC.WIRING):
            self.assertIn(BC.PRODUCERS[name].missing, ctx, name)
        self.assertEqual(1, len(self.spawned))
        args = self.spawned[0]
        self.assertEqual([BC.REFRESH_FLAG, self.d, "cli"], args[:3])
        self.assertTrue(args[3].isdigit(), "the briefing's estimate rides to the refresher")
        self.assertEqual("capture", args[4], "the diff baseline is captured by the refresher")

    def test_fresh_answers_are_served_as_they_are(self):
        BC.store(self.d, {BC.GIT: "- git: main · clean", BC.INTENTIONS: "mokata: 1 intention(s) due",
                          BC.MCP: None, BC.MODE_CLI: None, BC.WIRING: None,
                          BC.skills_name(): None})
        stack = self._no_slow_work()
        for p in stack:
            p.start()
        try:
            ctx = _front_door(self.d)
        finally:
            for p in stack:
                p.stop()
        self.assertIn("- git: main · clean", ctx)
        self.assertIn("mokata: 1 intention(s) due", ctx)
        self.assertNotIn("background", ctx.split("Code structure")[0].replace(
            BC.PRODUCERS[BC.GIT].missing, ""))

    def test_a_stale_answer_says_when_it_was_taken(self):
        BC.store(self.d, {BC.GIT: "- git: main · clean"}, now=time.time() - 10 * 60)
        state, line = BC.lookup(self.d, BC.GIT)
        self.assertEqual(BC.STALE, state)
        self.assertEqual("- git: main · clean — checked 10 min ago, re-checking", line)

    def test_a_stale_NOTHING_TO_REPORT_states_its_age_never_silence(self):
        """An old absence of a warning is a claim too: it says how old it is."""
        BC.store(self.d, {BC.WIRING: None, BC.MCP: None, BC.MODE_CLI: None, BC.INTENTIONS: None,
                          BC.skills_name(): None, BC.GIT: None}, now=time.time() - 7 * 3600 - 5)
        for name in (BC.WIRING, BC.MCP, BC.MODE_CLI, BC.INTENTIONS, BC.skills_name(), BC.GIT):
            state, line = BC.lookup(self.d, name)
            self.assertEqual(BC.STALE, state, name)
            self.assertEqual(BC.PRODUCERS[name].nothing + " — checked 7 h ago, re-checking",
                             line, name)

    def test_ages_read_as_people_say_them(self):
        self.assertEqual("under a minute", BC.age_of(5))
        self.assertEqual("12 min", BC.age_of(12 * 60 + 3))
        self.assertEqual("3 h", BC.age_of(3 * 3600 + 59))
        self.assertEqual("2 d", BC.age_of(2 * 86400 + 1))

    def test_a_moved_input_makes_an_answer_stale(self):
        BC.store(self.d, {BC.MODE_CLI: "mokata: install X"})
        self.assertEqual(BC.FRESH, BC.lookup(self.d, BC.MODE_CLI)[0])
        manifest = os.path.join(self.d, ".mokata", "manifest.json")
        st = os.stat(manifest)
        os.utime(manifest, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
        self.assertEqual(BC.STALE, BC.lookup(self.d, BC.MODE_CLI)[0])

    def test_in_process_callers_keep_the_synchronous_derivation(self):
        payload = json.dumps({"session_id": "S-in", "cwd": self.d,
                              "hook_event_name": "SessionStart", "source": "startup"})
        with mock.patch("mokata.live_context.git_line", return_value="- git: SYNC") as g:
            from mokata.hook_cli import run_with_stdin
            with redirect_stdout(io.StringIO()) as out:
                run_with_stdin(["session-start"], payload)
        g.assert_called()
        self.assertIn("- git: SYNC", out.getvalue())
        self.assertEqual([], self.spawned)
        self.assertFalse(hook_cli.FRONT_DOOR, "the front-door flag leaked out of its call")


@unittest.skipUnless(sqlite_disk_ok(), "sandbox cannot back an on-disk SQLite DB")
class TheMustBeCurrentLinesAreNeverServedStale(unittest.TestCase):

    def test_R_O12b_rules_come_from_the_cache_only_while_the_store_is_unchanged(self):
        from mokata import bootstrap as B
        with tempfile.TemporaryDirectory() as d:
            d = os.path.realpath(d)
            _repo(d)
            s = Surface.load(d)
            B._cached_rule_lines(s, 5)          # the first read creates the store's files
            calls = []
            real = B._rule_lines_or_none

            def counting(surface, n):
                calls.append(1)
                return real(surface, n)
            with mock.patch.object(B, "_rule_lines_or_none", side_effect=counting):
                first = B._cached_rule_lines(s, 5)
                self.assertEqual(first, B._cached_rule_lines(s, 5))
                self.assertEqual(1, len(calls), "an unchanged store was read again")
                from mokata.memory import MemoryStore
                from mokata.memory.item import MemoryItem
                MemoryStore.from_surface(s, knowledge=False).backend.put(
                    MemoryItem.create("never-push-main", "never push to main", kind="rule",
                                      id="r1"))
                now = B._cached_rule_lines(s, 5)
            self.assertEqual(2, len(calls), "a changed store was served from the cache")
            self.assertTrue(any("never-push-main" in ln or "never push" in ln for ln in now), now)

    def test_a_failed_rules_read_is_not_cached(self):
        from mokata import bootstrap as B
        with tempfile.TemporaryDirectory() as d:
            d = os.path.realpath(d)
            _repo(d)
            s = Surface.load(d)
            with mock.patch.object(B, "_rule_lines_or_none", return_value=None):
                self.assertEqual([], B._cached_rule_lines(s, 5))
            with mock.patch.object(B, "_rule_lines_or_none", return_value=["- r"]) as again:
                self.assertEqual(["- r"], B._cached_rule_lines(s, 5))
            again.assert_called_once()

    def test_the_resume_hint_is_re_derived_when_a_run_file_changes(self):
        from mokata import bootstrap as B
        from mokata.tdd_state import state_dir
        with tempfile.TemporaryDirectory() as d:
            d = os.path.realpath(d)
            _repo(d)
            s = Surface.load(d)
            with mock.patch.object(B, "build_resume_hint", return_value="A") as h:
                self.assertEqual("A", B._cached_resume_hint(s))
                self.assertEqual("A", B._cached_resume_hint(s))
                self.assertEqual(1, h.call_count)
                os.makedirs(state_dir(d), exist_ok=True)
                with open(os.path.join(state_dir(d), "pipeline_run__r9.json"), "w",
                          encoding="utf-8") as fh:
                    fh.write("{}")
                h.return_value = "B"
                self.assertEqual("B", B._cached_resume_hint(s))
                # a hook-written file (the session map) does NOT force a re-derive
                with open(os.path.join(state_dir(d), "session_ancestry.json"), "w",
                          encoding="utf-8") as fh:
                    fh.write("{}")
                self.assertEqual("B", B._cached_resume_hint(s))
                self.assertEqual(2, h.call_count)


@unittest.skipUnless(sqlite_disk_ok(), "sandbox cannot back an on-disk SQLite DB")
class TheRefresherDoesTheDeferredWork(unittest.TestCase):

    def test_it_logs_the_estimate_captures_the_baseline_and_stores_the_answers(self):
        with tempfile.TemporaryDirectory() as d:
            d = os.path.realpath(d)
            _repo(d)
            with mock.patch.object(BC, "compute", return_value={BC.GIT: "- git: X"}), \
                    mock.patch("mokata.govern.log_bootstrap_calibration") as cal, \
                    mock.patch("mokata.visibility.capture_session_snapshot") as cap, \
                    mock.patch("mokata.knowledge.layer.graph_structure_line", return_value="S"):
                self.assertEqual(0, BC.refresh_main([d, "cli", "321", "capture"]))
            cal.assert_called_once()
            self.assertEqual(321, cal.call_args[0][1])
            cap.assert_called_once()
            self.assertEqual((BC.FRESH, "- git: X"), BC.lookup(d, BC.GIT))
            self.assertFalse(os.path.exists(os.path.join(BC.cache_dir(d), BC.LOCK_FILENAME)))

    def test_with_no_refresher_session_start_does_it_itself(self):
        with tempfile.TemporaryDirectory() as d:
            d = os.path.realpath(d)
            _repo(d)
            with mock.patch.object(BC, "start_refresh", return_value=False), \
                    mock.patch("mokata.visibility.capture_session_snapshot") as cap, \
                    mock.patch("mokata.govern.log_bootstrap_calibration") as cal:
                _front_door(d)
            cap.assert_called_once()
            cal.assert_called_once()


class NothingLeaksBetweenInProcessCalls(unittest.TestCase):

    def test_R_O12b_the_dirty_track_hook_time_stamp_does_not_outlive_its_call(self):
        """The P33 heartbeat flake: `emit` left `MOKATA_HOOK_T_NS` set on os.environ, so every
        later in-process hook read that frozen instant as "now"."""
        with tempfile.TemporaryDirectory() as d:
            payload = json.dumps({"session_id": "S", "cwd": d, "hook_event_name": "PostToolUse",
                                  "tool_name": "Write",
                                  "tool_input": {"file_path": os.path.join(d, "a.py")}})
            env = {"MOKATA_HOOK_SPOOL": os.path.join(d, "spool")}
            for prior in (None, "123"):
                with mock.patch.dict(os.environ, env), \
                        mock.patch("mokata_emit._read_stdin_bytes",
                                   return_value=payload.encode()), \
                        mock.patch("mokata_emit._spawn_drainer", return_value=True):
                    if prior is None:
                        os.environ.pop(mokata_emit.HOOK_TIME_ENV, None)
                    else:
                        os.environ[mokata_emit.HOOK_TIME_ENV] = prior
                    mokata_emit.emit(["dirty-track"])
                    self.assertEqual(prior, os.environ.get(mokata_emit.HOOK_TIME_ENV))


# ======================================================================================
# BUDGET: cold process through the front door, a tree too big to summarise inline
# ======================================================================================

@unittest.skipUnless(sqlite_disk_ok(), "sandbox cannot back an on-disk SQLite DB")
class TheR_O12b_SessionStartMeetsItsBudget(unittest.TestCase):
    """HOOK-BUDGET's method: a fresh `python -m mokata_emit session-start` per call, two warm-ups,
    the bare-interpreter floor interleaved and divided out, `perf.relax_factor()` for CI, up to
    three batches. Each call's refresher is let finish (untimed — it is off the harness's clock),
    exactly as the drain is."""

    N = 9
    BATCHES = 3

    @classmethod
    def setUpClass(cls):
        cls._t = tempfile.TemporaryDirectory()
        cls.d = os.path.join(os.path.realpath(cls._t.name), "repo")
        os.makedirs(cls.d)
        _repo(cls.d, files=600)            # past the inline-summary bound: the cached path

    @classmethod
    def tearDownClass(cls):
        cls._wait(cls.d)
        cls._t.cleanup()

    @staticmethod
    def _wait(d):
        lock = os.path.join(BC.cache_dir(d), BC.LOCK_FILENAME)
        t = time.monotonic()
        while not os.path.exists(lock) and time.monotonic() - t < 1.0:
            time.sleep(0.01)
        while os.path.exists(lock) and time.monotonic() - t < 60:
            time.sleep(0.02)

    def _batch(self):
        env = dict(os.environ, PYTHONPATH=SRC, CLAUDE_PROJECT_DIR=self.d)
        env.pop("CLAUDE_PLUGIN_ROOT", None)
        xs, floor, outs = [], [], []
        for i in range(self.N + 2):
            body = json.dumps({"session_id": f"S-{i}-{time.time_ns()}", "cwd": self.d,
                               "hook_event_name": "SessionStart", "source": "startup"})
            t0 = time.perf_counter()
            p = subprocess.run([sys.executable, "-m", "mokata_emit", "session-start"], input=body,
                               cwd=self.d, env=env, text=True, encoding="utf-8",
                               capture_output=True, timeout=60, **hidden())
            t1 = time.perf_counter()
            self._wait(self.d)
            t2 = time.perf_counter()
            subprocess.run([sys.executable, "-c", "pass"], stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=60, **hidden())
            if i >= 2:
                xs.append((t1 - t0) * 1000.0)
                floor.append((time.perf_counter() - t2) * 1000.0)
                outs.append(p.stdout)
        return statistics.median(xs), statistics.median(floor), outs

    def test_R_O12b_session_start_answers_inside_150ms(self):
        budget = perf.HOOK_BUDGET_TARGETS_MS[perf.HOOK_KINDS["session-start"]]
        self.assertEqual(150.0, budget)
        relax = perf.relax_factor()
        seen = []
        for _ in range(self.BATCHES):
            p50, fl, outs = self._batch()
            self.assertTrue(all("- git:" in o and "Code structure" in o for o in outs),
                            "every timed briefing must carry its (cached) facts")
            seen.append((p50 / perf.interpreter_factor(fl), p50, fl))
            if seen[-1][0] <= budget * relax:
                break
        best = min(seen)
        sys.stderr.write(f"\n[O12b] session-start @ 600 files: p50 {best[0]:.1f} normalised "
                         f"({best[1]:.1f} raw, floor {best[2]:.1f}), budget {budget * relax:g}\n")
        self.assertLessEqual(best[0], budget * relax, seen)


if __name__ == "__main__":
    unittest.main()
