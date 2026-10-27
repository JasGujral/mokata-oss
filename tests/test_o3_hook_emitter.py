"""0.0.26 O3 — HOOK-BUDGET in a mokata repo: the stdlib-only observe-hook emitter (ruling U13).

Business rules — what must be true for the user:
  * The `mokata-hook` command Claude Code waits on is a front door that, for the observability hooks
    (`dirty-track`, `stop`, `session-end`, `pre-compact`), imports ONLY the standard library — no
    `mokata.*`, nothing third-party — writes the event to an owner-only per-user spool and returns.
    That is what lets an observe hook meet doc 109's 50 ms p95 inside a mokata repo.
  * `run-bind` is NOT deferred: the binding it writes is what the gate reads on the next tool call.
    With nothing registered it answers on the standard library; a registration binds before it returns.
  * Nothing is lost or re-read: a detached drainer runs the SAME handler on the SAME payload, argv and
    cwd, in arrival order; the AFTER snapshot is the file AS IT WAS AT HOOK TIME. An unwritable spool,
    an oversize entry or a drainer that cannot be started runs the work inline instead.
  * Nothing that answers is deferred: the gates and the context hooks, and every misconfiguration
    exit, are still `mokata.hook_cli.main`'s, synchronously.
  * One bad entry never wedges the spool; a live drainer is never doubled; a dead one's lock is
    taken over.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

import mokata_emit as E
from mokata import harvest as H
from mokata import perf
from mokata.init import init_repo
from mokata.knowledge.freshness import read_dirty
from mokata.quiet_spawn import hidden

ROOT = Path(__file__).resolve().parents[1]
SRC = str(ROOT / "src")


def _silent(*_a, **_k):
    pass


def _git(*args: str) -> None:
    subprocess.run(["git", *args], check=True, capture_output=True, stdin=subprocess.DEVNULL,
                   **hidden())


def _wait_drained(spool: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not os.path.isdir(spool) or not os.listdir(spool):
            return
        time.sleep(0.02)
    raise AssertionError(f"spool not drained: {os.listdir(spool)}")


def _stdlib(name: str) -> bool:
    return name.split(".")[0] in sys.stdlib_module_names or name in ("__main__", "_frozen_importlib",
                                                                      "_frozen_importlib_external")


class _Repo(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.t = os.path.realpath(self._t.name)
        self.repo = os.path.join(self.t, "repo")
        os.makedirs(os.path.join(self.repo, "src"))
        _git("init", "-q", self.repo)
        with open(os.path.join(self.repo, "src", "a.py"), "w", encoding="utf-8") as fh:
            fh.write("x = 1\n")
        init_repo(root=self.repo, profile="standard", assume_yes=True, out=_silent)
        self.spool = os.path.join(self.t, "spool")
        self.env = dict(os.environ, PYTHONPATH=SRC, MOKATA_HOOK_SPOOL=self.spool,
                        CLAUDE_PROJECT_DIR=self.repo)

    def tearDown(self):
        _wait_drained(self.spool)
        # An empty spool means the work is done, not that the detached drainer has EXITED: on
        # Windows its open handle on the spool dir refuses the delete for a moment (WinError 32,
        # 0.0.26 CI). Give it up to 30 s to go, then leave the temp dir rather than fail the test.
        deadline = time.monotonic() + 30
        while True:
            try:
                self._t.cleanup()
                return
            except PermissionError:
                if time.monotonic() > deadline:
                    return
                time.sleep(0.2)

    def _payload(self, **kw) -> str:
        base = {"session_id": "S1", "cwd": self.repo, "transcript_path": "/dev/null"}
        base.update(kw)
        return json.dumps(base)

    def _hook(self, *argv: str, stdin: str = "", code: str = None) -> subprocess.CompletedProcess:
        cmd = [sys.executable, "-c", code, *argv] if code else [sys.executable, "-m", "mokata_emit",
                                                                *argv]
        return subprocess.run(cmd, input=stdin, cwd=self.repo, env=self.env, text=True,
                              encoding="utf-8", capture_output=True, timeout=60, **hidden())


# ======================================================================================
# 1 · the hot path's import closure is the standard library alone
# ======================================================================================

#: Runs the front door IN this process for one observe hook, then reports what got imported.
_CLOSURE = ("import sys\nbefore = set(sys.modules)\nimport mokata_emit\n"
            "rc = mokata_emit.main(sys.argv[1:])\n"
            "after = sorted(set(sys.modules) - before)\n"
            "import json as _j\n"
            "sys.stderr.write('\\nMODS ' + _j.dumps(after))\n"
            "raise SystemExit(rc)")


def _windows_spawn_closure(env, cwd):
    """On Windows the drainer is started with `subprocess.Popen` + `quiet_spawn.detached()`
    (CREATE_NO_WINDOW — `mokata_emit._spawn_drainer`'s documented Windows branch; POSIX uses
    `os.posix_spawn` and imports neither). The modules THAT costs, measured in a fresh interpreter
    the same way the hook's closure is, so the allowance is derived, never typed. Empty off Windows."""
    if sys.platform != "win32":
        return frozenset()
    p = subprocess.run([sys.executable, "-c",
                        "import sys\nbefore = set(sys.modules)\nimport subprocess\n"
                        "from mokata.quiet_spawn import detached\n"
                        "print(repr(sorted(set(sys.modules) - before)))"],
                       env=env, cwd=cwd, text=True, capture_output=True, timeout=60,
                       stdin=subprocess.DEVNULL, **hidden())
    return frozenset(ast.literal_eval(p.stdout))


class TheEmitterImportsOnlyTheStandardLibrary(_Repo):

    def test_importing_the_module_loads_no_mokata_and_nothing_third_party(self):
        """A fresh interpreter: `sys.modules` before vs after `import mokata_emit` (interpreter and
        `site` start-up — `sitecustomize` and friends — are in `before`, not charged to us)."""
        p = subprocess.run([sys.executable, "-c",
                            "import sys\nbefore = set(sys.modules)\nimport mokata_emit\n"
                            "after = sorted(set(sys.modules) - before)\nprint(repr(after))"],
                           env=self.env, cwd=self.t, text=True, capture_output=True, timeout=60,
                           stdin=subprocess.DEVNULL, **hidden())
        self.assertEqual(0, p.returncode, p.stderr)
        names = ast.literal_eval(p.stdout)
        self.assertIn("mokata_emit", names)
        mine = [n for n in names if n != "mokata_emit"]
        self.assertEqual([], [n for n in mine if n.split(".")[0] == "mokata"])
        self.assertEqual([], [n for n in mine if not _stdlib(n)])

    def test_every_observe_hook_in_a_mokata_repo_runs_on_the_standard_library_alone(self):
        observe = sorted(s for s, k in perf.HOOK_KINDS.items() if k == "observe")
        self.assertEqual(5, len(observe))            # the list is perf's, not the emitter's own
        spawn = _windows_spawn_closure(self.env, self.t)
        for sub in observe:
            for extra in ([], ["--via-plugin"]):
                p = self._hook(sub, *extra, stdin=self._payload(hook_event_name="Stop"),
                               code=_CLOSURE)
                self.assertEqual(0, p.returncode, (sub, p.stderr))
                self.assertEqual("", p.stdout, sub)
                loaded = [m for m in json.loads(p.stderr.rsplit("MODS ", 1)[1])
                          if m not in spawn]
                self.assertEqual([], [m for m in loaded if m.split(".")[0] == "mokata"], sub)
                self.assertEqual([], [m for m in loaded if not _stdlib(m) and m != "mokata_emit"],
                                 sub)
                # the expensive stdlib the engine pays for is not paid here either
                for heavy in ("dataclasses", "typing", "inspect", "subprocess"):
                    self.assertNotIn(heavy, loaded, (sub, heavy))
        _wait_drained(self.spool)

    def test_the_observe_hooks_do_not_even_load_json(self):
        """The payload is read with the C scanner and the spool header is NUL-framed: `json` pulls in
        `re`, `enum`, `functools` — ~10 ms of the 50."""
        spawn = _windows_spawn_closure(self.env, self.t)
        for sub in ("dirty-track", "stop", "run-bind"):
            p = self._hook(sub, "--via-plugin",
                           stdin=self._payload(tool_input={"file_path": "src/a.py"}),
                           code=_CLOSURE)
            loaded = [m for m in json.loads(p.stderr.rsplit("MODS ", 1)[1]) if m not in spawn]
            self.assertNotIn("json", loaded, sub)
            self.assertNotIn("re", loaded, sub)


# ======================================================================================
# 2 · the work still happens — same handler, same payload — just off the harness's clock
# ======================================================================================

class TheDrainerDoesTheWork(_Repo):

    def test_an_emitted_write_lands_in_the_dirty_set(self):
        p = self._hook("dirty-track", "--via-plugin", stdin=self._payload(
            hook_event_name="PostToolUse", tool_name="Write",
            tool_input={"file_path": os.path.join(self.repo, "src", "a.py"), "content": "x"}))
        self.assertEqual(0, p.returncode, p.stderr)
        _wait_drained(self.spool)
        self.assertEqual(["src/a.py"], read_dirty(self.repo, session_id="S1"))

    def test_an_emitted_stop_in_a_bound_session_writes_the_draft(self):
        from mokata import run_intent as RI
        from mokata.config import Surface
        from mokata.govern.resume import PipelineCheckpoint
        PipelineCheckpoint(Surface.load(self.repo).state, "r1").ensure_registered()
        self.assertTrue(RI.bind_registered(self.repo, "S1", "r1"))
        p = self._hook("stop", "--via-plugin", stdin=self._payload(
            hook_event_name="Stop", last_assistant_message="all green"))
        self.assertEqual(0, p.returncode, p.stderr)
        _wait_drained(self.spool)
        drafts = H.drafts(self.repo)
        self.assertEqual(1, len(drafts))
        self.assertIn("- last word: all green", drafts[0].value)

    def test_the_entry_carries_argv_cwd_and_the_claude_environment(self):
        os.makedirs(self.spool)
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/p", "MOKATA_X": "1",
                                          "OTHER": "no"}):
            path = E.spool_entry(self.spool, ["stop", "--via-plugin"], b'{"a": "\xc3\xa9"}')
        argv, cwd, env, payload, observed = E.read_entry(path)
        os.remove(path)
        self.assertEqual({}, observed)
        self.assertEqual(["stop", "--via-plugin"], argv)
        self.assertEqual(os.getcwd(), cwd)
        self.assertEqual("/p", env["CLAUDE_PROJECT_DIR"])
        self.assertEqual("1", env["MOKATA_X"])
        self.assertNotIn("OTHER", env)
        self.assertEqual('{"a": "é"}', payload)


# ======================================================================================
# 3 · nothing that answers is deferred
# ======================================================================================

class NothingThatAnswersIsDeferred(unittest.TestCase):

    def test_the_deferred_set_is_the_observe_kind_minus_run_bind(self):
        self.assertEqual(("run-bind",), E.SYNC_OBSERVE)
        self.assertEqual(set(E.DEFERRED),
                         {s for s, k in perf.HOOK_KINDS.items() if k == "observe"} - {"run-bind"})

    def test_gates_and_context_hooks_go_straight_to_the_engine(self):
        for sub in ("secret-guard", "gate-guard", "subagent-stop", "session-start",
                    "user-prompt-submit", "subagent-start", "statusline"):
            with mock.patch("mokata.hook_cli.main", return_value=7) as engine, \
                    mock.patch.object(E, "emit") as emit:
                self.assertEqual(7, E.main([sub, "--x"]))
            engine.assert_called_once_with([sub, "--x"])
            emit.assert_not_called()

    def test_a_secret_is_still_blocked_through_the_front_door(self):
        body = json.dumps({"tool_name": "Write", "tool_input": {
            "file_path": "a.py", "content": "k = 'AKIAIOSFODNN7EXAMPLE'\n"}})
        p = subprocess.run([sys.executable, "-m", "mokata_emit", "secret-guard"], input=body,
                           env=dict(os.environ, PYTHONPATH=SRC), text=True, capture_output=True,
                           timeout=60, **hidden())
        self.assertEqual(2, p.returncode, p.stderr)

    def test_misconfiguration_still_exits_one(self):
        with mock.patch("sys.stderr"):
            self.assertEqual(1, E.main([]))
            self.assertEqual(1, E.main(["no-such-hook"]))


# ======================================================================================
# 4 · the spool and its drainer: order, no doubling, no wedging, never lost
# ======================================================================================

class TheSpoolIsSafe(unittest.TestCase):

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.spool = os.path.join(self._t.name, "spool")
        os.makedirs(self.spool)

    def tearDown(self):
        self._t.cleanup()

    def _entry(self, sub: str, body: str) -> str:
        return E.spool_entry(self.spool, [sub], body.encode())

    def test_entries_run_in_arrival_order_and_are_removed(self):
        for i in range(5):
            self._entry("stop", str(i))
        seen = []
        with mock.patch("mokata.hook_cli.run_with_stdin",
                        side_effect=lambda argv, payload, obs=None: seen.append(payload)):
            self.assertEqual(0, E.drain(self.spool))
        self.assertEqual(["0", "1", "2", "3", "4"], seen)
        self.assertEqual([], os.listdir(self.spool))

    def test_a_live_drainer_is_not_doubled_and_a_dead_ones_lock_is_taken_over(self):
        self._entry("stop", "x")
        os.mkdir(os.path.join(self.spool, E.LOCK_NAME))
        with mock.patch("mokata.hook_cli.run_with_stdin") as run:
            E.drain(self.spool)
            run.assert_not_called()                     # fresh lock: someone else is on it
            old = time.time() - E.LOCK_STALE_SECS - 5
            os.utime(os.path.join(self.spool, E.LOCK_NAME), (old, old))
            E.drain(self.spool)
            run.assert_called_once()                    # stale lock: taken over
        self.assertEqual([], os.listdir(self.spool))

    def test_a_crashed_entry_is_dropped_not_retried_and_a_garbage_one_is_skipped(self):
        open(os.path.join(self.spool, "00000000000000000001-1-stop.work"), "wb").close()
        with open(os.path.join(self.spool, "00000000000000000002-1-stop.hook"), "wb") as fh:
            fh.write(b"not a spool entry")
        self._entry("stop", "good")
        seen = []
        with mock.patch("mokata.hook_cli.run_with_stdin",
                        side_effect=lambda argv, payload, obs=None: seen.append(payload)):
            E.drain(self.spool)
        self.assertEqual(["good"], seen)
        self.assertEqual([], os.listdir(self.spool))

    def test_a_handler_that_raises_does_not_stop_the_drain(self):
        self._entry("stop", "a")
        self._entry("stop", "b")
        seen = []

        def boom(argv, payload, obs=None):
            seen.append(payload)
            raise RuntimeError("x")

        with mock.patch("mokata.hook_cli.run_with_stdin", side_effect=boom):
            self.assertEqual(0, E.drain(self.spool))
        self.assertEqual(["a", "b"], seen)

    def test_an_unwritable_spool_runs_the_hook_inline(self):
        with mock.patch.object(E, "spool_entry", side_effect=OSError("ro")), \
                mock.patch.object(E, "_read_stdin_bytes", return_value=b'{"x": 1}'), \
                mock.patch("mokata.hook_cli.run_with_stdin") as run:
            self.assertEqual(0, E.main(["dirty-track"]))
        run.assert_called_once_with(["dirty-track"], '{"x": 1}', {})

    def test_no_drainer_means_the_spool_is_drained_inline(self):
        with mock.patch.dict(os.environ, {E.SPOOL_ENV: self.spool}), \
                mock.patch.object(E, "_spawn_drainer", return_value=False), \
                mock.patch.object(E, "_read_stdin_bytes", return_value=b"{}"), \
                mock.patch("mokata.hook_cli.run_with_stdin") as run:
            self.assertEqual(0, E.main(["stop"]))
        run.assert_called_once_with(["stop"], "{}", {})
        self.assertEqual([], os.listdir(self.spool))

    def test_the_drainer_is_launched_by_path_never_from_the_users_cwd(self):
        argv = E.drainer_argv(self.spool)
        self.assertEqual(sys.executable, argv[0])
        self.assertTrue(os.path.isabs(argv[1]) and argv[1].endswith("__main__.py"))
        self.assertNotIn("-m", argv)
        self.assertNotIn("-c", argv)



# ======================================================================================
# 5 · run-bind is synchronous: the binding exists when the hook returns
# ======================================================================================

class RunBindIsSynchronous(_Repo):

    def test_a_registration_is_bound_before_the_hook_returns_and_nothing_is_spooled(self):
        from mokata import run_intent as RI
        from mokata.config import Surface
        from mokata.govern.resume import PipelineCheckpoint
        PipelineCheckpoint(Surface.load(self.repo).state, "r1").ensure_registered()
        p = self._hook("run-bind", "--via-plugin", stdin=self._payload(
            hook_event_name="PostToolUse", tool_name="mcp__mokata__session_save",
            tool_input={"register": True},
            tool_response=[{"type": "text", "text": json.dumps({"registered": "r1"})}]))
        self.assertEqual(0, p.returncode, p.stderr)
        # no wait, no drain: the gate's NEXT read must already see it
        self.assertEqual((RI.CLAIM_BOUND, "r1"), RI.session_claim(self.repo, "S1"))
        self.assertFalse(os.path.isdir(self.spool) and os.listdir(self.spool))

    def test_the_stdlib_registration_reader_is_run_intents(self):
        from mokata import run_intent as RI
        import re
        rx = re.compile(RI.REGISTER_TOOL_RE)
        for name in E.REGISTER_TOOLS:
            self.assertTrue(rx.match(name), name)
        for name in ("mcp__mokata__session_save_x", "mcp__plugin_x_mokata__session_save",
                     "session_save", "", "mcp__mokata__session_load"):
            self.assertFalse(rx.match(name), name)
            self.assertNotIn(name, E.REGISTER_TOOLS)
        shapes = [None, {}, [], "x", {"registered": ""}, {"registered": " r9 "},
                  {"a": {"b": [{"registered": "r2"}]}}, [{"text": '{"registered": "r3"}'}],
                  [{"text": "registered but not json"}], {"registered": 5},
                  {"d": {"d": {"d": {"d": {"d": {"d": {"d": {"registered": "deep"}}}}}}}},
                  '{"registered": "r4", "x": [1, 2]}', [{"text": '{"nested": "{\\"registered\\": \\"r5\\"}"}'}]]
        for tool in E.REGISTER_TOOLS + ("Write",):
            for resp in shapes:
                self.assertEqual(RI.registered_run(tool, resp),
                                 E.registered_run({"tool_name": tool, "tool_response": resp}),
                                 (tool, resp))


# ======================================================================================
# 6 · the AFTER snapshot is the file at HOOK time — the drainer never re-reads the tree
# ======================================================================================

class TheSnapshotIsTakenAtHookTime(_Repo):

    def test_write_A_hook_fires_file_becomes_B_before_the_drain_the_after_is_A(self):
        from mokata.ide import snapshots as S
        target = os.path.join(self.repo, "src", "a.py")
        with open(target, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("A = 1\n")
        os.makedirs(os.path.join(self.spool, E.LOCK_NAME))     # a "live drainer": nothing spawns
        p = self._hook("dirty-track", "--via-plugin", stdin=self._payload(
            hook_event_name="PostToolUse", tool_name="Write",
            tool_input={"file_path": target, "content": "A = 1\n"}))
        self.assertEqual(0, p.returncode, p.stderr)
        self.assertEqual(1, len(E.pending(self.spool)))
        with open(target, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("B = 2\n")                              # the tree moves on before the drain
        os.rmdir(os.path.join(self.spool, E.LOCK_NAME))
        E.drain(self.spool)
        after = [x for x in S.timeline(self.repo) if x.kind == S.AFTER and x.path == "src/a.py"]
        self.assertEqual(1, len(after))
        self.assertEqual("A = 1\n", S.content_of(self.repo, after[0]))
        self.assertEqual(["src/a.py"], read_dirty(self.repo, session_id="S1"))

    def test_a_spooled_hook_with_no_observation_stores_no_snapshot_rather_than_read_the_tree(self):
        from mokata.ide import snapshots as S
        from mokata import hook_cli
        body = self._payload(hook_event_name="PostToolUse", tool_name="Write",
                             tool_input={"file_path": os.path.join(self.repo, "src", "a.py")})
        cwd = os.getcwd()
        try:
            os.chdir(self.repo)
            hook_cli.run_with_stdin(["dirty-track"], body, {})
        finally:
            os.chdir(cwd)
        self.assertEqual([], [x for x in S.timeline(self.repo) if x.kind == S.AFTER])

    def test_a_too_large_file_is_recorded_too_large_and_its_bytes_are_never_spooled(self):
        big = os.path.join(self.t, "big.bin")
        with open(big, "wb") as fh:
            fh.write(b"x" * (E.MAX_SNAPSHOT_BYTES + 1))
        real, (state, size, _t, data) = E.observe_file(big)
        self.assertEqual(("too-large", E.MAX_SNAPSHOT_BYTES + 1, None), (state, size, data))
        _real, (state, _s, _t, data) = E.observe_file(os.path.join(self.t, "missing"))
        self.assertEqual(("absent", None), (state, data))

    def test_the_bounds_are_the_snapshot_stores(self):
        from mokata import gate_hook
        from mokata.ide import snapshots as S
        self.assertEqual(S.MAX_FILE_BYTES, E.MAX_SNAPSHOT_BYTES)
        self.assertEqual(gate_hook._PATH_KEYS, E.PATH_KEYS)

    def test_an_oversize_entry_runs_inline_and_never_touches_the_spool(self):
        target = os.path.join(self.repo, "src", "a.py")
        self.env["MOKATA_X_UNUSED"] = "1"
        code = ("import sys, mokata_emit as E\nE.MAX_ENTRY_BYTES = 10\n"
                "raise SystemExit(E.main(sys.argv[1:]))")
        p = self._hook("dirty-track", stdin=self._payload(
            hook_event_name="PostToolUse", tool_name="Write",
            tool_input={"file_path": target, "content": "x" * 100}), code=code)
        self.assertEqual(0, p.returncode, p.stderr)
        self.assertFalse(os.path.isdir(self.spool) and os.listdir(self.spool))
        self.assertEqual(["src/a.py"], read_dirty(self.repo, session_id="S1"))   # done, inline

    @unittest.skipIf(os.name == "nt", "POSIX permission bits")
    def test_spool_entries_are_owner_only_and_deleted_after_the_drain(self):
        os.makedirs(os.path.join(self.spool, E.LOCK_NAME))
        self._hook("stop", stdin=self._payload(hook_event_name="Stop"))
        (name,) = E.pending(self.spool)
        self.assertEqual(0o600, os.stat(os.path.join(self.spool, name)).st_mode & 0o777)
        self.assertEqual(0, os.stat(self.spool).st_mode & 0o077)
        os.rmdir(os.path.join(self.spool, E.LOCK_NAME))
        E.drain(self.spool)
        self.assertEqual([], os.listdir(self.spool))


# ======================================================================================
# 7 · arrival order, per session, even when the clock ties or emitters race
# ======================================================================================

class ArrivalOrderIsKept(unittest.TestCase):

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.spool = os.path.join(self._t.name, "spool")

    def tearDown(self):
        self._t.cleanup()

    def _drained(self):
        seen = []
        with mock.patch("mokata.hook_cli.run_with_stdin",
                        side_effect=lambda argv, payload, obs=None: seen.append(payload)):
            E.drain(self.spool)
        return seen

    def test_a_clock_that_ties_never_reorders_or_overwrites(self):
        with mock.patch.object(E.time, "time_ns", return_value=1_000):
            for i in range(4):
                E.spool_entry(self.spool, ["stop"], b"S1-%d" % i)
        self.assertEqual(4, len(E.pending(self.spool)))
        self.assertEqual(["S1-0", "S1-1", "S1-2", "S1-3"], self._drained())

    def test_two_sessions_interleaved_from_separate_processes_keep_each_sessions_order(self):
        code = ("import sys, mokata_emit as E\n"
                "E.spool_entry(sys.argv[1], ['stop'], sys.argv[2].encode())")
        env = dict(os.environ, PYTHONPATH=SRC)
        for body in ("S1-a", "S2-a", "S1-b", "S2-b", "S1-c"):
            subprocess.run([sys.executable, "-c", code, self.spool, body], env=env, check=True,
                           capture_output=True, timeout=60, stdin=subprocess.DEVNULL, **hidden())
        seen = self._drained()
        self.assertEqual(["S1-a", "S1-b", "S1-c"], [x for x in seen if x.startswith("S1")])
        self.assertEqual(["S2-a", "S2-b"], [x for x in seen if x.startswith("S2")])

    def test_racing_emitters_lose_no_entry(self):
        code = ("import sys, mokata_emit as E\n"
                "E.spool_entry(sys.argv[1], ['stop'], sys.argv[2].encode())")
        env = dict(os.environ, PYTHONPATH=SRC)
        procs = [subprocess.Popen([sys.executable, "-c", code, self.spool, f"P{i}"], env=env,
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, **hidden())
                 for i in range(12)]
        for pr in procs:
            self.assertEqual(0, pr.wait(timeout=60))
        self.assertEqual(sorted(f"P{i}" for i in range(12)), sorted(self._drained()))


# ======================================================================================
# 8 · Windows: the drainer goes through quiet_spawn — hidden console, own process group
# ======================================================================================

class TheWindowsSpawnIsQuietSpawns(unittest.TestCase):

    def test_win32_flags_are_new_group_plus_no_window_never_detached_process(self):
        import subprocess as sp
        with tempfile.TemporaryDirectory() as spool, \
                mock.patch.object(os, "name", "nt"), \
                mock.patch.object(sp, "Popen") as popen:
            self.assertTrue(E._spawn_drainer(spool))
        kw = popen.call_args.kwargs
        self.assertEqual(0x00000200 | 0x08000000, kw["creationflags"])
        self.assertFalse(kw["creationflags"] & 0x00000008, "DETACHED_PROCESS is the B-01 flash bug")
        for stream in ("stdin", "stdout", "stderr"):
            self.assertEqual(sp.DEVNULL, kw[stream])
        self.assertTrue(kw["close_fds"])
        self.assertEqual(spool, kw["cwd"])
        self.assertEqual(E.drainer_argv(spool), popen.call_args.args[0])

    def test_the_flags_have_one_source(self):
        from mokata import quiet_spawn
        with mock.patch.object(quiet_spawn, "_windows", return_value=True):
            self.assertEqual({"creationflags": 0x00000200 | 0x08000000}, quiet_spawn.detached())
        with mock.patch.object(quiet_spawn, "_windows", return_value=False):
            self.assertEqual({"start_new_session": True}, quiet_spawn.detached())
        src = (Path(E.__file__)).read_text(encoding="utf-8")
        self.assertIn("from mokata.quiet_spawn import detached", src)
        self.assertNotIn("0x08000000", src)


# ======================================================================================
# 9 · the stdlib JSON reader is json's
# ======================================================================================

class TheStdlibJsonReaderIsJsons(unittest.TestCase):

    def test_it_agrees_with_json_and_refuses_what_json_refuses(self):
        for text in ('{"a": [1, 2.5, "\\u00e9", null, true, false, {"b": -3e2}]}', "  []  ", '"x"',
                     "0", '{"cwd": "C:\\\\x"}'):
            self.assertEqual(json.loads(text), E.loads(text), text)
        for bad in ("", "{", '{"a": 1} x', "[1,]", "{'a': 1}"):
            with self.assertRaises(ValueError, msg=bad):
                json.loads(bad)
            with self.assertRaises(ValueError, msg=bad):
                E.loads(bad)


if __name__ == "__main__":
    unittest.main()
