"""A1 (0.0.19 stage 01) — `CRG-READLINE-UNBOUNDED`: the transport read is bounded, the two
failures are TOLD APART, and a timed-out session is killed instead of reused.

Closes #45, #46 and #53's hang half.

WHAT WAS WRONG. `_McpStdioSession._rpc` wrote a JSON-RPC request and then called
`proc.stdout.readline()` with nothing bounding it. `code-review-graph serve` starting and going
quiet is not a process that failed — `poll()` says alive, the pipe is open — so the read simply
never returned and the whole MCP server wedged. The class stored `self.timeout` and applied it on
its OTHER transport path (`_default_run_cli`) the entire time: a separated pair, not a missing
feature. And the class docstring promised *"any transport failure raises CrgUnavailable so the
layer degrades to the AST floor"*, a guarantee a hang cannot reach, because a hang raises nothing.

THREE PROPERTIES THIS FILE PINS, and they are different properties:

  1  THE BOUND EXISTS — driven by a REAL subprocess that accepts a request and deliberately says
     nothing (doc 85 §7e: a `Mock` that raises `TimeoutError` proves the HANDLER, and the handler
     was never the defect; the bound was). Every hang test here spawns a genuine Python process
     over genuine pipes and is bounded at well under a second.
  2  THE TWO FAILURES DO NOT SHARE A REPRESENTATION (doc 85 §7g) — a transport that DIED and a
     transport that is ALIVE AND MUTE produce different exception types and different
     `failure_class` values, end to end, all the way to what the user is told. This is the test
     that reds if someone later "simplifies" `CrgTimeout` into a subclass of `CrgUnavailable`.
  3  A TIMED-OUT SESSION IS DEAD, NOT REUSABLE — the highest-severity item in the stage and the
     one in none of the filed issues. The request was written and its reply was never read, so a
     surviving process hands the NEXT call the PREVIOUS call's answer: a silently wrong graph
     answer. `TestTheSessionIsKilled` proves the second call gets its OWN answer, and
     `test_a1_the_desync_assertion_is_load_bearing` proves that assertion can actually SEE the
     desync by defeating the kill and watching the wrong answer arrive.

HOW THE FAKE SERVER IS SPAWNED, and why it is this shape. `_McpStdioSession` builds a fixed argv
`[command, "serve", "--repo", root]`, so a fake server has to be reachable as `command` plus the
literal word `serve`. Each test therefore writes its server into a temp dir as a file NAMED
`serve` and runs with `command = sys.executable` and the cwd set to that dir — `python serve
--repo <dir>`. That keeps production untouched (no test-only seam, no patched `Popen`) and works
on Windows as well as POSIX, which matters here: the bound is a THREAD rather than `select`
precisely because `select` on a pipe does not work on Windows.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import gc
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
import warnings
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import degrade
from mokata.bounded_io import (READ_ANSWERED, READ_CLOSED, READ_ERROR, READ_TIMEOUT,
                               BoundedStderrTail, read_bounded, read_line_bounded)
from mokata.degrade import FAILURE_TIMEOUT, FAILURE_UNREACHABLE, reset_degrade_notices
from mokata.errors import DegradedCapability, failure_class_of
from mokata.knowledge import CodeReviewGraphBackend, KnowledgeLayer
from mokata.knowledge.ast_backend import AstBackend
from mokata.knowledge.crg_client import (CodeReviewGraphClient, CrgTimeout, CrgUnavailable,
                                         _McpStdioSession)
from mokata.knowledge.grep_backend import GrepBackend
from mokata.knowledge.query import BackendError

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "mokata")

#: Short on purpose: a slow test is a test people stop running. Every bound here is sub-second.
BOUND = 0.6

#: How long the "slow" server holds a reply. Comfortably past BOUND so the timeout is not a race,
#: and short enough that the desync tests stay a few seconds.
SLOW = 1.6


# --------------------------------------------------------------------------------- the fake server
_SERVER = '''\
import json, sys, time

MODE = {mode!r}
NOISE = {noise!r}


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\\n")
    sys.stdout.flush()


def rows(target):
    payload = {{"results": [{{"qualified_name": target, "name": target,
                             "file_path": target.lower() + ".py", "line_start": 1,
                             "kind": "function", "is_test": False}}]}}
    return {{"content": [{{"type": "text", "text": json.dumps(payload)}}]}}


if NOISE:
    sys.stderr.write(NOISE + "\\n")
    sys.stderr.flush()

for raw in sys.stdin:
    raw = raw.strip()
    if not raw:
        continue
    msg = json.loads(raw)
    method = msg.get("method")
    if method == "initialize":
        if MODE == "mute-on-initialize":
            continue                      # ALIVE and saying nothing — the whole defect
        send({{"jsonrpc": "2.0", "id": msg.get("id"),
              "result": {{"protocolVersion": "2024-11-05"}}}})
        continue
    if method and method.startswith("notifications/"):
        continue
    if method == "tools/call":
        target = ((msg.get("params") or {{}}).get("arguments") or {{}}).get("target", "")
        if MODE == "mute":
            continue                      # ALIVE and saying nothing
        if MODE == "grandchild":
            # a worker that INHERITS the transport pipes and outlives the server (code-review-graph's
            # ProcessPoolExecutor workers do exactly this), then silence
            import subprocess
            gc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(100000)"])
            with open("gc.pid", "w", encoding="utf-8") as fh:
                fh.write(str(gc.pid))
            continue
        if MODE == "slow-alpha" and target == "ALPHA":
            time.sleep({slow})            # answers, but far too late for the bound
            send({{"jsonrpc": "2.0", "id": msg.get("id"), "result": rows(target)}})
            # ...and then a progress notification, which is an ordinary thing for an MCP server
            # to put on stdout. It stands for ANY unread line a timed-out exchange leaves behind.
            send({{"jsonrpc": "2.0", "method": "notifications/progress",
                  "params": {{"progress": 1}}}})
            continue
        send({{"jsonrpc": "2.0", "id": msg.get("id"), "result": rows(target)}})
        continue
    send({{"jsonrpc": "2.0", "id": msg.get("id"), "result": {{}}}})
'''


class FakeServerCase(unittest.TestCase):
    """Writes a real fake server and runs with cwd set to it, so `[sys.executable, "serve", ...]`
    — the argv production actually builds — reaches it on every platform."""

    MODE = "healthy"
    NOISE = ""

    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.mkdtemp(prefix="a1-crg-")
        with open(os.path.join(self._tmp, "serve"), "w", encoding="utf-8") as fh:
            fh.write(_SERVER.format(mode=self.MODE, noise=self.NOISE, slow=SLOW))
        os.chdir(self._tmp)
        self._sessions = []
        reset_degrade_notices()

    def tearDown(self):
        for session in self._sessions:
            try:
                session.close()
            except Exception:
                pass
        os.chdir(self._cwd)
        reset_degrade_notices()
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def session(self, timeout=BOUND):
        s = _McpStdioSession(sys.executable, root=self._tmp, timeout=timeout)
        self._sessions.append(s)
        return s

    def client(self, timeout=BOUND):
        # 0.0.26 O13: the fake server stands for a BUILT index — this module grades the transport.
        c = CodeReviewGraphClient(name=sys.executable, root=self._tmp, timeout=timeout,
                                  index_probe=lambda _root: True)
        self.addCleanup(lambda: getattr(c, "_session", None) and c._session.close())
        return c


# ------------------------------------------------------------------- 1 · THE BOUND (deliverable 1)
class TestARealHangIsBounded(FakeServerCase):
    """§7e — a REAL subprocess that accepts the request and says nothing. A mocked hang would
    prove the handler; the bound is the whole defect."""

    MODE = "mute"

    def test_a1_regression_a_mute_server_does_not_hang_the_caller(self):
        session = self.session()
        started = time.monotonic()
        with self.assertRaises(CrgTimeout) as ctx:
            session.call("query_graph_tool", {"pattern": "callers_of", "target": "ALPHA",
                                              "repo_root": self._tmp})
        elapsed = time.monotonic() - started
        # The BUSINESS fact: the run came back. Before A1 this call never returned at all.
        self.assertLess(elapsed, 8.0, "the bounded read did not return")
        self.assertIn("did not answer", str(ctx.exception))


class TestAHangDuringHandshakeIsBounded(FakeServerCase):
    """The handshake read is the FIRST unbounded read a user meets — `_ensure` calls `_rpc`
    before any tool call ever runs — so it needs its own pin, not the tool-call one."""

    MODE = "mute-on-initialize"

    def test_a1_regression_a_server_mute_on_initialize_does_not_hang(self):
        session = self.session()
        started = time.monotonic()
        with self.assertRaises(CrgTimeout):
            session.call("query_graph_tool", {"pattern": "callers_of", "target": "ALPHA"})
        self.assertLess(time.monotonic() - started, 8.0)


class TestTheBoundedReaderIsShared(unittest.TestCase):
    """Deliverable 1 — ONE reusable helper, used from both stdio clients. The tree must end with
    fewer bounded readers than it started with, not three."""

    def test_a1_no_unbounded_read_of_a_subprocess_pipe_survives_in_src(self):
        """The predicate is a SUBPROCESS PIPE, not every `readline()`. A file on disk cannot go
        quiet — `repo_identity.py` reads two lines out of a file and could not hang if it tried.
        A pipe held by another process is the whole hazard, and it is what this greps for."""
        offenders = []
        # CORPUS: THE WORKING TREE. This asks what mokata SHIPS, and `sync-public.sh` mirrors
        # with `rsync`, which copies the working tree — an untracked `.py` under `src/` really is
        # published, and an untracked one is exactly where a hurried second bounded reader would
        # appear. The index would be blind to the file most likely to break the rule.
        for root, _dirs, files in os.walk(SRC):
            for name in sorted(files):
                if not name.endswith(".py"):
                    continue
                path = os.path.join(root, name)
                with open(path, "r", encoding="utf-8") as fh:
                    for n, line in enumerate(fh, 1):
                        if re.search(r"\.std(out|err)\.read(line)?\(", line):
                            offenders.append(f"{os.path.relpath(path, SRC)}:{n}: {line.strip()}")
        self.assertEqual(offenders, [],
                         "every subprocess-pipe read in src/ goes through `bounded_io`; an "
                         "unbounded one is the A1 defect coming back:\n" + "\n".join(offenders))

    def test_a1_both_stdio_clients_use_the_one_helper(self):
        for rel in ("mcp_admin.py", os.path.join("knowledge", "crg_client.py")):
            with self.subTest(module=rel):
                with open(os.path.join(SRC, rel), "r", encoding="utf-8") as fh:
                    text = fh.read()
                self.assertIn("read_line_bounded", text)
                self.assertIn("bounded_io", text)

    def test_a1_the_helper_gives_four_outcomes_four_names(self):
        """§7g at its smallest: answered / timed out / closed / raised are four facts. Collapsing
        any two is how "the server said nothing" became indistinguishable from "the server hung
        up" in the first place."""
        self.assertEqual(read_bounded(lambda: "a line\n", 1.0).outcome, READ_ANSWERED)
        self.assertEqual(read_bounded(lambda: "", 1.0).outcome, READ_CLOSED)
        self.assertEqual(read_bounded(lambda: (_ for _ in ()).throw(OSError("gone")), 1.0).outcome,
                         READ_ERROR)
        slow = read_bounded(lambda: time.sleep(5) or "late", 0.2)
        self.assertEqual(slow.outcome, READ_TIMEOUT)
        self.assertTrue(slow.timed_out)
        self.assertFalse(slow.answered)

    def test_a1_the_bound_is_a_thread_not_select(self):
        """Windows: `select`/`poll` on a pipe does not work there and every current mokata user
        is on Windows. The prior art (`mcp_admin`, the Windows E2E probe) is a thread for exactly
        this reason; a POSIX-only bound would be a regression dressed as a fix."""
        with open(os.path.join(SRC, "bounded_io.py"), "r", encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("threading.Thread", text)
        self.assertNotIn("import select", text)
        self.assertNotIn("select.select", text)


# --------------------------------------------------------- 2 · THE SIBLING (deliverables 2 and 4)
class TestTheTwoFailuresAreDistinct(FakeServerCase):
    """§7g, stated as the property: a DIED transport and a MUTE transport differ in TYPE and in
    CLASS. This is the test that fails if someone makes `CrgTimeout` a subclass."""

    MODE = "mute"

    def test_a1_crg_timeout_is_a_sibling_never_a_subclass(self):
        self.assertTrue(issubclass(CrgTimeout, DegradedCapability))
        self.assertFalse(issubclass(CrgTimeout, CrgUnavailable),
                         "a subclass would be absorbed by any `except CrgUnavailable` and the "
                         "two facts would re-collapse without leaving a mark (doc 85 §7g)")
        self.assertFalse(issubclass(CrgUnavailable, CrgTimeout))

    def test_a1_the_failure_classes_differ(self):
        self.assertEqual(CrgTimeout.failure_class, FAILURE_TIMEOUT)
        self.assertEqual(CrgUnavailable.failure_class, FAILURE_UNREACHABLE)
        self.assertNotEqual(CrgTimeout.failure_class, CrgUnavailable.failure_class)

    def test_a1_a_died_transport_and_a_mute_transport_raise_different_types(self):
        """End to end, on two REAL subprocesses: one that cannot start at all, one that starts
        and goes quiet."""
        dead = _McpStdioSession("mokata-no-such-command-a1", root=self._tmp, timeout=BOUND)
        with self.assertRaises(CrgUnavailable) as died:
            dead.call("query_graph_tool", {"pattern": "callers_of", "target": "ALPHA"})
        with self.assertRaises(CrgTimeout) as mute:
            self.session().call("query_graph_tool", {"pattern": "callers_of", "target": "ALPHA"})
        self.assertNotIsInstance(died.exception, CrgTimeout)
        self.assertNotIsInstance(mute.exception, CrgUnavailable)
        self.assertNotEqual(failure_class_of(died.exception), failure_class_of(mute.exception))

    def test_a1_the_distinction_survives_the_backend_wrap(self):
        """The adapter wraps ANY client failure in `BackendError` — deliberately broad, because
        the client is a bring-your-own-tool boundary. The wrap used to be LOSSY, so a mute graph
        arrived at the degrade layer wearing "unreachable"."""
        for exc, expected in ((CrgTimeout("mute"), FAILURE_TIMEOUT),
                              (CrgUnavailable("died"), FAILURE_UNREACHABLE),
                              (RuntimeError("an injected double, unclassed"),
                               FAILURE_UNREACHABLE)):
            with self.subTest(cause=type(exc).__name__):
                backend = CodeReviewGraphBackend(name="crg", root=self._tmp,
                                                 client=_Raiser(exc))
                with self.assertRaises(BackendError) as ctx:
                    backend.query("callers", "ALPHA")
                self.assertEqual(failure_class_of(ctx.exception), expected)


class _Raiser:
    """A client that fails one stated way. It never fails OPEN: an unrecognised call raises."""

    supports_semantic = False

    def __init__(self, exc):
        self.exc = exc

    def query(self, kind, target, root, depth=1):
        raise self.exc

    def semantic(self, query, root, kind=None, limit=20):
        raise self.exc


# ----------------------------------------------------- 3 · THE DESYNCHRONISED SESSION (deliv. 3)
class TestTheSessionIsKilled(FakeServerCase):
    """🔴 The highest-severity item in the stage, and in none of the filed issues."""

    MODE = "slow-alpha"

    def test_a1_regression_the_second_call_gets_its_own_answer(self):
        """Assert on the VALUE RETURNED TO THE CALLER, not on internals: ask about ALPHA (which
        times out), then ask about BETA, and get BETA."""
        client = self.client()
        with self.assertRaises(CrgTimeout):
            client.query("callers", "ALPHA", root=self._tmp)
        time.sleep(SLOW + 0.6)          # the old process's late reply is now on the wire
        rows = client.query("callers", "BETA", root=self._tmp)
        self.assertEqual([r["symbol"] for r in rows], ["BETA"],
                         "the second query received the FIRST query's answer — a silently wrong "
                         "graph answer, the one outcome the client promises never happens")

    def test_a1_the_desync_assertion_is_load_bearing(self):
        """DEFEAT THE KILL and watch the wrong answer arrive. Without this, the test above would
        pass just as happily against a session that never desynchronised in the first place, and
        would be grading nothing (doc 85 §7f)."""
        session = self.session()
        original = _McpStdioSession.close
        _McpStdioSession.close = lambda self: None      # the pre-fix behaviour, exactly
        try:
            with self.assertRaises(CrgTimeout):
                session.call("query_graph_tool", {"pattern": "callers_of", "target": "ALPHA"})
            time.sleep(SLOW + 0.6)
            leaked = session.call("query_graph_tool", {"pattern": "callers_of", "target": "BETA"})
        finally:
            _McpStdioSession.close = original
            session.close()
        self.assertNotIn("BETA", json.dumps(leaked),
                         "the defeat did not reproduce the desync, so the regression test above "
                         "is not grading the kill")

    def test_a1_the_timed_out_process_is_dead(self):
        """Half one of the kill: the PROCESS is terminated and forgotten."""
        session = self.session()
        with self.assertRaises(CrgTimeout):
            session.call("query_graph_tool", {"pattern": "callers_of", "target": "ALPHA"})
        self.assertIsNone(session._proc, "the session still holds its process")

    def test_a1_the_cached_session_is_dropped(self):
        """Half two, and it is a DIFFERENT half: the CONVERSATION — the object holding the
        request-id counter and the dead pipes — is cached on the CLIENT, and survives `close()`
        untouched. Graded separately from the process kill on purpose (doc 85 §7f)."""
        client = self.client()
        with self.assertRaises(CrgTimeout):
            client.query("callers", "ALPHA", root=self._tmp)
        self.assertIsNone(getattr(client, "_session", None),
                          "the desynchronised session is still cached on the client")

    def test_a1_the_next_call_starts_a_new_process(self):
        client = self.client()
        with self.assertRaises(CrgTimeout):
            client.query("callers", "ALPHA", root=self._tmp)
        client.query("callers", "BETA", root=self._tmp)
        self.assertIsNotNone(client._session._proc)


# --------------------------------------------------- 5 · LOUD AT THE DEGRADE LAYER (deliverable 5)
class TestTheDegradeLayerSaysWhich(unittest.TestCase):
    """The user's graph query is still ANSWERED — from the floor — and the run says which of the
    two things went wrong. That pair is the whole P22 answer for this row."""

    def setUp(self):
        reset_degrade_notices()
        self._tmp = tempfile.mkdtemp(prefix="a1-layer-")
        with open(os.path.join(self._tmp, "sample.py"), "w", encoding="utf-8") as fh:
            fh.write("def alpha():\n    pass\n\n\ndef beta():\n    alpha()\n")

    def tearDown(self):
        reset_degrade_notices()
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _layer(self, exc):
        primary = CodeReviewGraphBackend(name="code-review-graph", root=self._tmp,
                                         client=_Raiser(exc))
        floor = AstBackend(root=self._tmp, grep=GrepBackend(root=self._tmp))
        return KnowledgeLayer(primary=primary, fallback=floor)

    def test_a1_regression_a_timeout_degrades_to_the_floor_loudly_and_is_classed_timeout(self):
        out = io.StringIO()
        result = self._layer(CrgTimeout("serve did not answer")).callers("alpha")
        degrade.note_degraded  # (imported for the reader; the layer calls it itself)
        notices = degrade.emitted_notices()
        self.assertEqual([n.failure_class for n in notices], [FAILURE_TIMEOUT],
                         "a graph that is ALIVE AND MUTE must not be reported as unreachable")
        self.assertEqual(notices[0].subsystem, "code-graph")
        # the fallback still falls back — the user's question was ANSWERED
        self.assertNotEqual(result.backend, "code-review-graph")
        self.assertTrue(result.degraded)
        rendered = notices[0].render()
        self.assertIn("did not answer in time", rendered)
        self.assertNotIn("unreachable", rendered)
        del out

    def test_a1_an_unreachable_graph_still_reads_unreachable(self):
        """The other side of §7g: splitting the class must not relabel the failure that was
        already classified correctly."""
        self._layer(CrgUnavailable("serve is not running")).callers("alpha")
        notices = degrade.emitted_notices()
        self.assertEqual([n.failure_class for n in notices], [FAILURE_UNREACHABLE])
        self.assertIn("unreachable", notices[0].render())

    def test_a1_the_two_notices_do_not_read_the_same(self):
        self._layer(CrgTimeout("mute")).callers("alpha")
        timeout_text = degrade.emitted_notices()[0].render()
        reset_degrade_notices()
        self._layer(CrgUnavailable("died")).callers("alpha")
        unreachable_text = degrade.emitted_notices()[0].render()
        self.assertNotEqual(timeout_text, unreachable_text)

    def test_a1_the_timeout_fix_names_something_a_user_can_do(self):
        self._layer(CrgTimeout("mute")).callers("alpha")
        fix = degrade.emitted_notices()[0].remediation
        self.assertIn("code-review-graph", fix)
        self.assertNotIn("graph adopt", fix,
                         "re-adopting a tool that is running perfectly well is the wrong remedy")

    def test_a1_the_notice_reaches_doctor(self):
        """`emitted_notices()` is how `mokata doctor` answers "what degraded this session?" — a
        notice that only ever printed into a scrollback is not loud, it is momentary."""
        self._layer(CrgTimeout("mute")).callers("alpha")
        self.assertEqual([n.subsystem for n in degrade.emitted_notices()], ["code-graph"])


class TestSessionCloseReleasesItsPipes(FakeServerCase):
    """`CRG-SESSION-CLOSE-LEAKS-FDS` (0.0.28 lane A). Reaping the server did not close the three
    pipes `Popen` opened on it, so each closed session leaked three descriptors until the garbage
    collector found them. A REAL subprocess: a mocked pipe would prove the handler, not the leak."""

    MODE = "healthy"

    def _opened(self):
        session = self.session()
        session._ensure()
        return session, session._proc

    @staticmethod
    def _released(session):
        """The read pipes are closed by daemon closers the instant their readers let go; join them
        (no timeout: a closer that never finishes is a hang the runner's progress check sees)."""
        for closer in getattr(session, "_closers", []):
            if closer is not None:
                closer.join()

    def test_every_pipe_is_closed_when_the_session_closes(self):
        session, proc = self._opened()
        self.assertFalse(proc.stdin.closed)
        session.close()
        self._released(session)
        self.assertTrue(proc.stdin.closed, "stdin pipe left open")
        self.assertTrue(proc.stdout.closed, "stdout pipe left open")
        self.assertTrue(proc.stderr.closed, "stderr pipe left open")
        self.assertIsNotNone(proc.poll(), "the server process was not reaped")

    def test_no_ResourceWarning_is_raised_when_a_closed_session_is_collected(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            session, proc = self._opened()
            session.close()
            self._released(session)
            del session, proc
            gc.collect()
        leaks = [str(w.message) for w in caught if issubclass(w.category, ResourceWarning)]
        self.assertEqual([], leaks)

    def test_a_pipe_that_is_already_broken_does_not_stop_the_close(self):
        """The server may have died with bytes still buffered on stdin; closing that pipe raises
        `BrokenPipeError` (an `OSError`). The close must still finish and release the others."""
        from mokata.knowledge.crg_client import _close_quietly

        class _Broken:
            def close(self):
                raise BrokenPipeError(32, "Broken pipe")

        class _Gone:
            def close(self):
                raise ValueError("I/O operation on closed file")

        _close_quietly(_Broken())
        _close_quietly(_Gone())
        _close_quietly(None)

    def test_closing_twice_is_harmless(self):
        session, proc = self._opened()
        session.close()
        self._released(session)
        session.close()
        self._released(session)
        self.assertTrue(proc.stdout.closed)


class TestCloseDoesNotWaitForAPipeAGrandchildHolds(FakeServerCase):
    """0.0.28 lane A, verifier H1. A pipe's write end held by a surviving grandchild never gives EOF,
    and a reader parked on it holds the stream's lock — so closing that stream from the closing
    thread BLOCKS for the grandchild's lifetime (measured 25.4 s vs 1.1 s, and forever for a
    long-lived worker). The rule: the thread that reads a pipe owns closing it; `close()` never
    waits on a pipe somebody else may hold."""

    MODE = "grandchild"

    def test_a_timeout_returns_at_the_read_bound_while_a_grandchild_holds_the_pipes(self):
        import signal
        import threading
        from mokata import quiet_spawn
        session = self.session()
        outcome = {}

        def run():
            try:
                session.call("query_graph_tool", {"pattern": "callers_of", "target": "X"})
            except BaseException as exc:            # carried to the asserting thread
                outcome["exc"] = exc

        # BEHAVIOURAL, not clock-based: a `close()` that waits on its closers (bounded or not)
        # is caught by watching who gets joined, whatever the wait costs.
        from mokata.knowledge import crg_client
        joined_closers = []
        real_join = threading.Thread.join

        def spy(thread, *a, **k):
            if getattr(thread, "_target", None) is crg_client._close_quietly:
                joined_closers.append(thread)
            return real_join(thread, *a, **k)

        patcher = mock.patch.object(threading.Thread, "join", spy)
        patcher.start()
        self.addCleanup(patcher.stop)
        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(BOUND * 50)                     # relative to the configured bound, not a new clock
        pid_file = os.path.join(self._tmp, "gc.pid")
        grandchild = None
        if os.path.exists(pid_file):
            with open(pid_file, encoding="utf-8") as fh:
                grandchild = int(fh.read())

        def reap():
            if grandchild is not None:
                try:
                    quiet_spawn.signal_pid(grandchild, getattr(signal, "SIGKILL", signal.SIGTERM))
                except OSError:                     # already gone: released earlier by the test
                    pass
        self.addCleanup(reap)
        hung = worker.is_alive()
        if hung:
            reap()                                  # release the blocked close so the suite ends
            worker.join()
        self.assertIsNotNone(grandchild, "the fake server never spawned its grandchild")
        self.assertFalse(hung, "close() waited on a pipe the grandchild still holds")
        self.assertIsInstance(outcome.get("exc"), CrgTimeout)
        patcher.stop()
        self.assertEqual([], joined_closers, "close() waited on a closer thread")
        closers = [c for c in session._closers if c is not None]
        self.assertTrue(closers)
        # the pipes are still held at the moment close() returned: the closers are still parked...
        self.assertTrue(any(c.is_alive() for c in closers),
                        "the closers finished while the grandchild still held the pipes")
        # ...and the moment the holder goes, they exit (no timeout: a hang here is a hang)
        reap()
        for c in closers:
            real_join(c)
            self.assertFalse(c.is_alive())


# ------------------------------------------------------------- 6 · THE SERVER'S OWN ACCOUNT (d. 6)
class TestStderrIsCapturedNotDiscarded(FakeServerCase):
    MODE = "mute"
    NOISE = "code-review-graph: index lock held, waiting"

    def test_a1_regression_the_servers_own_words_reach_the_failure(self):
        """`stderr=DEVNULL` threw away the one thing the reporters of #45/#46/#53 needed."""
        with self.assertRaises(CrgTimeout) as ctx:
            self.session().call("query_graph_tool", {"pattern": "callers_of", "target": "ALPHA"})
        self.assertIn("index lock held", str(ctx.exception))


class TestASecretInStderrIsWithheld(FakeServerCase):
    """Deliverable 6's safety half. The tail is FOREIGN text — mokata did not choose what is in
    it — so it is checked with mokata's OWN scanner and dropped whole if anything credential-
    shaped is in it. Built by concatenation so this file carries no literal credential."""

    MODE = "mute"
    SECRET = "p4ssw" + "MEQ8" + "LONGsecret"
    NOISE = "crg: connecting with " + "postgres://svc:" + SECRET + "@db.internal:5432/graph"

    def test_a1_no_secret_value_reaches_the_exception_the_notice_or_the_log(self):
        with self.assertRaises(CrgTimeout) as ctx:
            self.session().call("query_graph_tool", {"pattern": "callers_of", "target": "ALPHA"})
        message = str(ctx.exception)
        self.assertNotIn(self.SECRET, message)
        self.assertNotIn("db.internal", message)
        self.assertIn("withheld", message)
        # and it must not reach the user-facing notice either
        backend = CodeReviewGraphBackend(name="crg", root=self._tmp,
                                         client=_Raiser(ctx.exception))
        floor = AstBackend(root=self._tmp, grep=GrepBackend(root=self._tmp))
        KnowledgeLayer(primary=backend, fallback=floor).callers("alpha")
        for notice in degrade.emitted_notices():
            self.assertNotIn(self.SECRET, notice.render())
            self.assertNotIn(self.SECRET, notice.detail)

    def test_a1_the_withholding_is_load_bearing(self):
        """Prove the scanner is what withheld it, not an accident of the tail being empty."""
        from mokata.govern.secrets import scan
        self.assertTrue(scan(text=self.NOISE), "the fixture is not actually secret-shaped, so "
                                               "the test above proves nothing")


class TestTheStderrTailIsBounded(unittest.TestCase):
    def test_a1_a_chatty_server_cannot_fill_the_pipe_or_the_message(self):
        """An unread `PIPE` fills its ~64 KiB buffer and blocks the server on its next write — a
        brand-new hang inside the fix for a hang. The tail DRAINS, and caps both ways."""
        proc = subprocess.Popen(
            [sys.executable, "-c",
             "import sys\n"
             "for i in range(20000): sys.stderr.write('noise line %d\\n' % i)\n"
             "sys.stderr.flush()\n"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, bufsize=1)
        tail = BoundedStderrTail(proc.stderr)
        self.addCleanup(proc.stderr.close)
        self.assertEqual(proc.wait(timeout=20), 0,
                         "the subprocess blocked writing to an undrained pipe")
        time.sleep(0.3)
        text = tail.tail()
        self.assertLessEqual(len(text), 600)
        self.assertIn("noise line 19999", text, "the tail kept the OLDEST lines, not the newest")


# -------------------------------------------------------------- 7 · THE DOCSTRING (deliverable 7)
class TestTheDocstringNamesBothExits(unittest.TestCase):
    def test_a1_the_session_docstring_states_both_exits_and_what_each_carries(self):
        text = _McpStdioSession.__doc__ or ""
        self.assertIn("CrgUnavailable", text)
        self.assertIn("CrgTimeout", text)
        self.assertNotIn("Any transport failure raises CrgUnavailable", text,
                         "that sentence promised a degrade the hang could not reach")

    def test_a1_the_sibling_docstrings_say_what_distinguishes_them(self):
        self.assertIn("§7g", CrgTimeout.__doc__ or "")
        self.assertIn("did not answer", (CrgTimeout.__doc__ or "").lower())
        self.assertIn("COULD NOT ANSWER", CrgUnavailable.__doc__ or "")


# ---------------------------------------------------------------------- NEGATIVE: the healthy path
class TestTheHealthyPathIsUnchanged(FakeServerCase):
    MODE = "healthy"

    def test_a1_a_server_that_answers_inside_the_bound_behaves_exactly_as_before(self):
        rows = self.client().query("callers", "ALPHA", root=self._tmp)
        self.assertEqual([r["symbol"] for r in rows], ["ALPHA"])
        self.assertEqual([r["path"] for r in rows], ["alpha.py"])
        self.assertEqual([r["line"] for r in rows], [1])

    def test_a1_the_healthy_path_emits_no_notice(self):
        self.client().query("callers", "ALPHA", root=self._tmp)
        self.assertEqual(degrade.emitted_notices(), [])

    def test_a1_the_healthy_path_spawns_no_extra_process(self):
        """One session, one process, across many calls — the bound must not have turned every
        call into a fresh spawn."""
        client = self.client()
        client.query("callers", "ALPHA", root=self._tmp)
        first = client._session._proc.pid
        for target in ("BETA", "GAMMA", "DELTA"):
            client.query("callers", target, root=self._tmp)
        self.assertEqual(client._session._proc.pid, first)

    def test_a1_the_healthy_path_raises_nothing(self):
        session = self.session()
        payload = session.call("query_graph_tool", {"pattern": "callers_of", "target": "ALPHA"})
        self.assertEqual(payload["results"][0]["qualified_name"], "ALPHA")


class TestTheUntouchedPathsAreUntouched(unittest.TestCase):
    """The prompt names these explicitly; asserted, not assumed."""

    def test_a1_default_run_cli_still_bounds_with_the_stored_timeout(self):
        """`_default_run_cli` was already bounded and is untouched — it is the OTHER half of the
        separated pair, and the evidence that the attribute was always there to be used."""
        seen = {}
        real = subprocess.run

        def spy(*args, **kwargs):
            seen.update(kwargs)
            class R:
                returncode, stdout, stderr = 0, "code-review-graph 2.3.6", ""
            return R()

        subprocess.run = spy
        # 0.0.26 O15 — the client runs only a RESOLVED binary (mokata's env / MOKATA_CRG); this
        # test is about the bound, so it names one rather than depending on the venv.
        from unittest import mock
        from mokata import detect
        try:
            with mock.patch.object(detect, "find_command",
                                   lambda n: "/opt/crg/bin/code-review-graph"):
                client = CodeReviewGraphClient(name="code-review-graph", timeout=17.0)
                self.assertEqual(client.version(), "2.3.6")
        finally:
            subprocess.run = real
        self.assertEqual(seen.get("timeout"), 17.0)

    def test_a1_the_injected_call_tool_path_is_untouched(self):
        """Every existing unit test injects `call_tool` and never reaches the transport. That
        path must not have changed at all — and note what it means for the prior-stage pins
        (doc 85 §7e): before this stage the transport had NO test coverage whatsoever."""
        calls = []

        def fake(tool, params):
            calls.append((tool, params))
            return {"results": [{"qualified_name": params.get("target"), "file_path": "x.py",
                                 "line_start": 3}]}

        client = CodeReviewGraphClient(name="code-review-graph", call_tool=fake)
        rows = client.query("callers", "ALPHA", root=".")
        self.assertEqual(calls[0][0], "query_graph_tool")
        self.assertEqual(rows[0]["symbol"], "ALPHA")
        self.assertIsNone(getattr(client, "_session", None),
                          "an injected transport must never build a stdio session")

    def test_a1_the_version_handshake_is_untouched(self):
        from mokata.knowledge.crg_client import CrgVersionSkew, check_version_string
        self.assertEqual(check_version_string("2.3.6"), "2.3.6")
        with self.assertRaises(CrgVersionSkew):
            check_version_string("3.0.0")
        with self.assertRaises(CrgUnavailable):
            check_version_string("")


if __name__ == "__main__":
    unittest.main()
