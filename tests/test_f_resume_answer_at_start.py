"""0.0.29 F (RESUME-RUN-ANSWER-ALWAYS-DERIVED-AT-START, HINT-VALID-UNTIL-GAPS; matrix #12-#14).

What must be true for the user:
  * A `/clear` or `/compact` inside one Claude Code process is briefed from the stored next-step
    answer when NOTHING it rested on has changed — no re-walk of the progress log (~145 ms at the
    8 MiB bound). Before, the session's own index row (which SessionStart rewrites before the
    briefing) was keyed by its bytes, so every start re-derived.
  * A stored answer is NEVER served as current when a fact it rested on moved: a process it found
    alive died (the Claude process, a run's registered MCP process — the LIVE rung and the
    ambiguity check included), a pid it found dead is alive again, the run pin
    (`MOKATA_SESSION_ID`) changed, or the session's own row changed what liveness reads from it
    (its end marker, its heartbeat window, the Claude pid it names).
  * The key is captured from the reads, not remembered: every `pid_alive` result `derive` obtained
    (a recorder around `session_registry.pid_alive`), every environment variable it read (a
    recording environ reds on one not keyed), and the row only through `liveness_read`.

Hit vs miss is counted (`derive` calls), never timed. Every wait is an event the test controls: a
child process blocking on its stdin is "alive" until the test closes that stdin and reaps it.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import time
import unittest
from contextlib import ExitStack
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from test_p14_next_step_hints import RUN, SID, T1, World
from test_f_resume_hint_one_derivation import _expected, _hook_read, _rungs
from mokata import bootstrap as B
from mokata import briefing_cache as BC
from mokata import hints as H
from mokata import session_index as SI
from mokata import session_registry as SR
from mokata.govern.resume import PipelineCheckpoint
from mokata.state import StateStore
from mokata.tdd_state import state_dir

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")


class _Child:
    """A real process that is alive until the test says otherwise (closes its stdin and reaps it)
    — the event, never a clock."""

    def __init__(self, tc):
        self.p = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"],
                                  stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)
        self.pid = self.p.pid
        tc.addCleanup(self.end)

    def end(self):
        if self.p.returncode is None:
            self.p.stdin.close()
            self.p.wait()


def _register(d, runs):
    """The MCP live-session registry: `{run: pid}` rooted at this repo."""
    StateStore(state_dir(d)).write(SR.SESSION_REGISTRY_KEY, {"sessions": {
        run: {"pid": pid, "repo_root": d, "session_id": run} for run, pid in runs.items()}})


def _rule(d, sid, now):
    return H.primary(H.derive(d, session=sid, now=now, rules=H.PIPELINE_ONLY))["rule"]


class HitsWhenNothingMoved(unittest.TestCase):
    """#12 — force: before 0.0.29 F the own row's bytes were in the key, so a `/clear` (which
    rewrites the row) derived again."""

    def test_a_clear_in_the_same_claude_process_is_served_from_the_store(self):
        w = World(self).approve().spec("AC-1").plan({"AC-1": [T1]})
        me = [os.getpid(), "tok"]                  # this process stands for the Claude process
        SI.on_start(w.d, SID, "startup", w.d, proc=me)
        now = time.time()
        ans = B.refresh_resume_hint(w.d, SID, now=now)
        self.assertIn([os.getpid(), True], ans["basis"]["pids"], "the pid fact is in the basis")
        for source in ("clear", "compact"):
            with self.subTest(source=source):
                SI.on_start(w.d, SID, source, w.d, proc=me)      # what SessionStart does first
                line, derived = _hook_read(w.d, SID, now)
                self.assertEqual(0, derived, "nothing moved: served from the store")
                self.assertEqual(_expected(w.d, SID, now), line)
                self.assertIn(f"Next step for run {RUN}", line)


class APidFlipMisses(unittest.TestCase):
    """#12a-c — force: a stored answer that rested on a process being alive (or dead)."""

    def _gate_world(self):
        w = World(self).approve()
        w.gate("blocked", gate="red-before-green")
        return w

    def test_12a_the_claude_process_in_the_basis_dies(self):
        w = self._gate_world()
        claude = _Child(self)
        SI.on_start(w.d, SID, "startup", w.d, proc=[claude.pid, "tok"])
        now = time.time()
        B.refresh_resume_hint(w.d, SID, now=now)
        self.assertEqual((_expected(w.d, SID, now), 0), _hook_read(w.d, SID, now))
        self.assertEqual(H.R_GATE, _rule(w.d, SID, now))
        claude.end()
        line, derived = _hook_read(w.d, SID, now)
        self.assertEqual(1, derived, "the Claude process died: the stored 'live' answer is stale")
        self.assertEqual(_expected(w.d, SID, now), line)
        self.assertEqual(H.R_GATE_ENDED, _rule(w.d, SID, now))

    def test_12b_a_runs_registered_mcp_process_dies(self):
        w = self._gate_world()
        now = time.time()
        SI.on_start(w.d, SID, "startup", w.d, proc=None, t=now - 3600)   # heartbeat not live
        mcp = _Child(self)
        _register(w.d, {RUN: mcp.pid})
        B.refresh_resume_hint(w.d, SID, now=now)
        self.assertEqual(H.R_GATE, _rule(w.d, SID, now), "live by the run's MCP process")
        self.assertEqual(0, _hook_read(w.d, SID, now)[1])
        mcp.end()
        line, derived = _hook_read(w.d, SID, now)
        self.assertEqual(1, derived)
        self.assertEqual(_expected(w.d, SID, now), line)
        self.assertEqual(H.R_GATE_ENDED, _rule(w.d, SID, now))

    def test_12c_a_dead_pid_reused_alive(self):
        w = self._gate_world()
        alive = {424242: False}
        SI.on_start(w.d, SID, "startup", w.d, proc=[424242, "tok"])
        now = time.time()
        with mock.patch.object(SR, "pid_alive", lambda pid: alive.get(pid, False)):
            B.refresh_resume_hint(w.d, SID, now=now)
            self.assertEqual(H.R_GATE_ENDED, _rule(w.d, SID, now))
            self.assertEqual(0, _hook_read(w.d, SID, now)[1])
            alive[424242] = True
            line, derived = _hook_read(w.d, SID, now)
            self.assertEqual(1, derived)
            self.assertEqual(_expected(w.d, SID, now), line)
            self.assertEqual(H.R_GATE, _rule(w.d, SID, now))


class TheEventStoreIsKeyedByItsAnswers(unittest.TestCase):
    """#12 (found on the real hook) — force: SessionStart appends its own event to the event store
    before the briefing, so keyed by the database file's stat the stored answer never held. The
    store is keyed by the queries `derive` made and the rows they answered."""

    def test_an_event_derive_never_asks_for_is_a_hit_one_it_does_is_a_miss(self):
        w = World(self).approve()
        w.gate("blocked", gate="red-before-green")
        SI.on_start(w.d, SID, "startup", w.d, proc=[os.getpid(), "tok"])
        now = time.time()
        ans = B.refresh_resume_hint(w.d, SID, now=now)
        self.assertTrue(ans["basis"]["events"], "fixture: derive queried the event store")
        self.assertEqual(0, _hook_read(w.d, SID, now)[1])
        w.gate("allowed", session="S-elsewhere", run="R-elsewhere")    # the file moves
        self.assertEqual(0, _hook_read(w.d, SID, now)[1], "a row no query returns moved the key")
        self.assertEqual(H.R_GATE, _rule(w.d, SID, now))
        w.gate("allowed", gate="red-before-green")                      # supersedes the block
        line, derived = _hook_read(w.d, SID, now)
        self.assertEqual(1, derived)
        self.assertEqual(_expected(w.d, SID, now), line)
        self.assertNotEqual(H.R_GATE, _rule(w.d, SID, now))

    def test_the_hit_path_fingerprint_is_hints_fingerprint(self):
        """`briefing_cache` restates `hints.events_print` (the hit path must not import `hints`);
        the two must agree on a real answer."""
        import inspect
        w = World(self).approve()
        w.gate("blocked")
        from mokata.events.store import store_for_root
        from mokata.events.schema import GATE_DECISION
        r = store_for_root(w.d).read_run_readonly(run_id=RUN, session_ids=[SID],
                                                  types=[GATE_DECISION], limit=H.EVENT_ROWS)
        entry = {"root": w.d, "events": [[RUN, [SID], GATE_DECISION, H.EVENT_ROWS,
                                          H.events_print(r)]]}
        self.assertTrue(BC._events_hold(entry, SID))
        self.assertIn("event_id", inspect.getsource(BC._events_hold))



class EveryEventFieldIsKeyed(unittest.TestCase):
    """force — verifier M2 (0.0.29 F round 2): an event query's answer is keyed by MORE than its
    row ids. The store answering the same (empty) rows but now `fault` (unreadable), `truncated`
    or `absent` is a different answer — derive says "unknown — mokata doctor" on a fault — so a
    stored answer that rested on a healthy store must miss. One cell per field, each flipped
    alone; the unflipped control holds."""

    FIELDS = ("fault", "truncated", "absent")

    def _entry(self):
        from mokata.events.store import store_for_root
        from mokata.events.schema import GATE_DECISION
        w = World(self).approve()
        w.gate("blocked")
        r = store_for_root(w.d).read_run_readonly(run_id=RUN, session_ids=[SID],
                                                  types=[GATE_DECISION], limit=H.EVENT_ROWS)
        return w, r, {"root": w.d, "events": [[RUN, [SID], GATE_DECISION, H.EVENT_ROWS,
                                                H.events_print(r)]]}

    def _now_answering(self, r, **flip):
        from types import SimpleNamespace
        from mokata.events import store as ES
        said = SimpleNamespace(rows=list(r.rows), truncated=bool(r.truncated),
                               absent=bool(r.absent), fault=bool(r.fault))
        for k, v in flip.items():
            setattr(said, k, v)
        fake = SimpleNamespace(read_run_readonly=lambda **_k: said)
        return mock.patch.object(ES, "store_for_root", lambda _root: fake)

    def test_the_unflipped_answer_holds(self):
        _w, r, entry = self._entry()
        with self._now_answering(r):
            self.assertTrue(BC._events_hold(entry, SID))

    def test_each_field_flipped_alone_misses(self):
        _w, r, entry = self._entry()
        stored = entry["events"][0][4]
        for field in self.FIELDS:
            with self.subTest(field=field), self._now_answering(r, **{field: not stored[field]}):
                self.assertFalse(BC._events_hold(entry, SID), f"{field} flipped and still a hit")

    def test_a_store_that_faults_after_the_store_is_never_served_as_a_hit(self):
        """End to end: answer stored on a healthy store; the store then faults on every read ->
        the hook derives (a miss), never serves the confident stored step."""
        from mokata.events.store import EventStore, RunRead
        w = World(self).approve()
        w.gate("blocked", gate="red-before-green")
        SI.on_start(w.d, SID, "startup", w.d, proc=[os.getpid(), "tok"])
        now = time.time()
        B.refresh_resume_hint(w.d, SID, now=now)
        self.assertEqual(0, _hook_read(w.d, SID, now)[1], "fixture: a hit before the fault")

        def faulting(self_, **_k):
            return RunRead(rows=[], fault="the event store could not be read (test)")
        with mock.patch.object(EventStore, "read_run_readonly", faulting):
            self.assertEqual(1, _hook_read(w.d, SID, now)[1],
                             "the event store faulted and the stored step was still served")


class TheRefresherStoresOnlyWhenTheFactsHeld(unittest.TestCase):
    """force — verifier M2 / V19: the refresher derives twice and stores the second answer only
    when the pid, env and event facts are those of the first (`bootstrap.refresh_resume_hint`'s
    `facts(ans) != first`). A fact that moves between the two reads -> nothing is stored; the
    unmoved control stores."""

    def _world(self):
        w = World(self).approve()
        w.gate("blocked", gate="red-before-green")
        SI.on_start(w.d, SID, "startup", w.d, proc=[os.getpid(), "tok"])
        return w

    def _refresh(self, w, moved):
        real = H.derive
        calls = [0]

        def derive(*a, **k):
            ans = real(*a, **k)
            calls[0] += 1
            if calls[0] == 2 and moved:
                basis = dict(ans.get("basis") or {})
                basis[moved] = ["moved", basis.get(moved)]
                ans = dict(ans, basis=basis)
            return ans
        with mock.patch.object(H, "derive", derive):
            B.refresh_resume_hint(w.d, SID, now=time.time())
        self.assertEqual(2, calls[0], "fixture: the refresher derived twice")
        return BC.read_resume(w.d)

    def test_unmoved_facts_store(self):
        self.assertIsNotNone(self._refresh(self._world(), None))

    def test_a_fact_that_moved_between_the_reads_stores_nothing(self):
        for fact in ("pids", "env", "events"):
            with self.subTest(fact=fact):
                self.assertIsNone(self._refresh(self._world(), fact),
                                  f"{fact} moved between the reads and the answer was stored")


class LiveRungPidFlipMisses(unittest.TestCase):
    """#12d — force: two runs and no binding, so `resolve_run`'s LIVE rung (or `_resolve`'s
    ambiguity check) decides by registry pids the session row never names."""

    def _two(self):
        w = World(self, run="R-a").approve()
        PipelineCheckpoint(w.store, "R-b").ensure_registered()
        w.store.write("approved_approach__R-b", {      # both unshipped: only LIVE can narrow
            "topic": "p14", "approach": {"name": "B"}, "approver": "jas",
            "approved_at": "2026-10-31T00:00:00Z"})
        _register(w.d, {"R-a": 515151, "R-b": 525252})
        return w

    def _flip(self, alive, before, after):
        w = self._two()
        now = time.time()
        with mock.patch.object(SR, "pid_alive", lambda pid: alive.get(pid, False)):
            B.refresh_resume_hint(w.d, SID, now=now)
            stored = _rule(w.d, SID, now)
            self.assertEqual(before, stored)
            self.assertEqual(0, _hook_read(w.d, SID, now)[1])
            alive.update(after)
            line, derived = _hook_read(w.d, SID, now)
            self.assertEqual(1, derived)
            self.assertEqual(_expected(w.d, SID, now), line)
            self.assertNotEqual(stored, _rule(w.d, SID, now), "fixture: the flip changes the answer")

    def test_the_live_rung_picked_runs_pid_dies(self):
        self._flip({515151: True, 525252: False}, H.C_SPEC, {515151: False})

    def test_the_ambiguity_check_flips(self):
        self._flip({515151: True, 525252: True}, H.R_AMBIGUOUS, {525252: False})


class PinChangeMisses(unittest.TestCase):
    """#12e — force: `MOKATA_SESSION_ID` selects the run before every other rung."""

    def test_a_changed_pin_misses_and_an_unchanged_one_hits(self):
        w = World(self, run="R-a").approve()
        PipelineCheckpoint(w.store, "R-b").ensure_registered()
        now = time.time()
        with mock.patch.dict(os.environ, {"MOKATA_SESSION_ID": "R-a"}):
            B.refresh_resume_hint(w.d, SID, now=now)
            self.assertEqual(0, _hook_read(w.d, SID, now)[1])
        with mock.patch.dict(os.environ, {"MOKATA_SESSION_ID": "R-b"}):
            line, derived = _hook_read(w.d, SID, now)
            self.assertEqual(1, derived)
            self.assertEqual(_expected(w.d, SID, now), line)
            self.assertIn("for run R-b", line)


class _RecordingEnviron(dict):
    def __init__(self, base):
        super().__init__(base)
        self.read = set()

    def __getitem__(self, k):
        self.read.add(k)
        return super().__getitem__(k)

    def get(self, k, default=None):
        self.read.add(k)
        return super().get(k, default)

    def __contains__(self, k):
        self.read.add(k)
        return super().__contains__(k)


class EveryReadIsKeyed(unittest.TestCase):
    """#12e — guard: every environment variable `derive` reads (in the one cached mode,
    `PIPELINE_ONLY`, on every rung of the ladder) is in the key. A new read that is not keyed
    reds here — the key is derived from the reads, not from a list someone remembered."""

    def test_derive_reads_only_keyed_variables(self):
        seen = set()
        for rung, (d, patches) in _rungs(self).items():
            with self.subTest(rung=rung), ExitStack() as st:
                for p in patches:
                    st.enter_context(p)
                rec = _RecordingEnviron(os.environ)
                with mock.patch.object(os, "environ", rec):
                    H.derive(d, session=SID, now=time.time(), rules=H.PIPELINE_ONLY)
                seen |= rec.read
        self.assertIn("MOKATA_SESSION_ID", seen, "the recording proxy saw nothing")
        self.assertEqual(set(), seen - set(H.KEYED_ENV), "variables derive read, not keyed")

    def test_the_home_is_keyed_under_every_name_either_os_reads(self):
        """The rung test above records only THIS host's reads: on Windows `expanduser` reads
        `USERPROFILE` (then `HOMEDRIVE`+`HOMEPATH`), never `HOME`. Both platforms' readers are run
        here, on every host, with the home unset and set, so a name dropped from the key reds on
        darwin too — not only on the Windows leg that found it."""
        import ntpath
        import posixpath
        for reader in (ntpath.expanduser, posixpath.expanduser):
            for drop in ((), ("USERPROFILE",), ("USERPROFILE", "HOME")):
                with self.subTest(reader=reader.__module__, drop=drop):
                    env = {k: v for k, v in os.environ.items() if k not in drop}
                    env.setdefault("HOMEDRIVE", "C:")
                    env.setdefault("HOMEPATH", "\\Users\\u")
                    rec = _RecordingEnviron(env)
                    with mock.patch.object(os, "environ", rec):
                        reader("~")
                    self.assertTrue(rec.read, "the recording proxy saw nothing")
                    self.assertEqual(set(), rec.read - set(H.KEYED_ENV))

    def test_no_module_binds_pid_alive_at_import(self):
        """The recorder wraps `session_registry.pid_alive`; a module that bound it at import time
        would probe around the recorder and its pid would silently leave the key."""
        # CORPUS: THE WORKING TREE. The question is "does any module of the product that would
        # RUN bind `pid_alive` at import?" — what runs is the disk (src/ is rsynced to the mirror
        # and imported from the checkout), tracked or not.
        bad = []
        for dirpath, _dirs, names in os.walk(os.path.join(SRC, "mokata")):
            for n in names:
                if not n.endswith(".py"):
                    continue
                path = os.path.join(dirpath, n)
                with open(path, encoding="utf-8") as fh:
                    tree = ast.parse(fh.read())
                inner = set()
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                        inner.update(id(s) for s in ast.walk(node))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and id(node) not in inner \
                            and (node.module or "").endswith("session_registry") \
                            and any(a.name == "pid_alive" for a in node.names):
                        bad.append(f"{os.path.relpath(path, SRC)}:{node.lineno}")
        self.assertEqual([], bad)


_INSIDE = [False]


class _RecordingRow(dict):
    def __init__(self, base, log):
        super().__init__(base)
        self.log = log

    def get(self, k, default=None):
        self.log.append((k, _INSIDE[0]))
        return super().get(k, default)

    def __getitem__(self, k):
        self.log.append((k, _INSIDE[0]))
        return super().__getitem__(k)


class TheProjectionIsTheKey(unittest.TestCase):
    """#13 — `resume_holds` called DIRECTLY (its `valid_until` gate is the caller's), so each
    projection fact has a cell only it can red."""

    def _stored(self, proc):
        w = World(self).approve()
        w.gate("blocked", gate="red-before-green")
        SI.on_start(w.d, SID, "startup", w.d, proc=proc)
        now = time.time()
        B.refresh_resume_hint(w.d, SID, now=now)
        entry = BC.read_resume(w.d)
        self.assertTrue(BC.resume_holds(entry, SID, now=now), "fixture: holds as stored")
        return w, entry, now

    def test_has_end_flipped_misses(self):
        w, entry, now = self._stored(proc=None)            # no pid: only the end marker moves
        SI.on_end(w.d, SID, "exit")
        self.assertFalse(BC.resume_holds(entry, SID, now=now))

    def test_heartbeat_live_flipped_misses(self):
        _w, entry, now = self._stored(proc=None)
        self.assertFalse(BC.resume_holds(entry, SID, now=now + SI.LIVE_SECS + 60))

    def test_the_named_claude_pid_changed_misses(self):
        """The rule's attack H1: the row now names another (live) Claude process. Re-probing the
        stored pid alone answers as before; the projection carries the pid the row names."""
        w, entry, now = self._stored(proc=[os.getpid(), "tok"])
        SI.on_start(w.d, SID, "resume", w.d, proc=[os.getppid(), "tok2"], t=now)
        self.assertFalse(BC.resume_holds(entry, SID, now=now))

    def test_session_live_reads_the_row_only_through_liveness_read(self):
        w = World(self).approve()
        SI.on_start(w.d, SID, "startup", w.d, proc=[os.getpid(), "tok"])
        log = []
        real_read, real_lr = SI.read, SI.liveness_read

        def lr(rec, now):
            _INSIDE[0] = True
            try:
                return real_lr(rec, now)
            finally:
                _INSIDE[0] = False
        with mock.patch.object(SI, "read", lambda root, sid: _RecordingRow(
                real_read(root, sid) or {}, log)), mock.patch.object(SI, "liveness_read", lr):
            H._session_live(w.d, [SID], RUN, time.time())
        self.assertTrue(log, "fixture: the row was read")
        self.assertEqual([], [k for k, inside in log if not inside],
                         "_session_live read a row field outside the projection")


class TheHitPathImportsNothingItNeedNot(unittest.TestCase):
    """#14 — a hit imports neither `hints` nor `session_registry` unless a pid is in the basis
    (then `session_registry` only, to re-probe)."""

    def _hit_modules(self, d):
        code = ("import sys, json; sys.path.insert(0, %r)\n"
                "from types import SimpleNamespace\n"
                "from mokata import bootstrap as B\n"
                "line = B._cached_resume_hint(SimpleNamespace(root=%r), %r)\n"
                "print(json.dumps([line, 'mokata.hints' in sys.modules, "
                "'mokata.session_registry' in sys.modules]))" % (SRC, d, SID))
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, env=dict(os.environ, PYTHONPATH=SRC))
        self.assertEqual(0, p.returncode, p.stderr[-2000:])
        return json.loads(p.stdout.strip().splitlines()[-1])

    def test_no_pid_in_the_basis(self):
        w = World(self).approve().spec("AC-1").plan({"AC-1": [T1]})
        SI.on_start(w.d, SID, "startup", w.d, proc=None)
        ans = B.refresh_resume_hint(w.d, SID)
        self.assertEqual([], ans["basis"]["pids"])
        line, hints, registry = self._hit_modules(w.d)
        self.assertRegex(line, r"\(derived [^()]* ago\)$", "fixture: a hit")
        self.assertEqual((False, False), (hints, registry))

    def test_a_pid_in_the_basis(self):
        w = World(self).approve().spec("AC-1").plan({"AC-1": [T1]})
        SI.on_start(w.d, SID, "startup", w.d, proc=[os.getpid(), "tok"])
        ans = B.refresh_resume_hint(w.d, SID)
        self.assertTrue(ans["basis"]["pids"])
        line, hints, registry = self._hit_modules(w.d)
        self.assertRegex(line, r"\(derived [^()]* ago\)$", "fixture: a hit")
        self.assertEqual((False, True), (hints, registry))


if __name__ == "__main__":
    unittest.main()
