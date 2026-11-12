"""0.0.28 lane I1 — the MCP transport, driven over real stdio (design note I §3b, §5).

Jas, 2026-11-05: "i want mcp to be test properly - it hangs in claude session a lot while cli
always work". Three hang mechanisms were reproduced on master, each in the transport — where the D3
eval (`_serve(spec.fn)`, no stdio, no event loop) cannot see them:

  H1 `MCP-STDIN-INHERITED`  fd 0 (the JSON-RPC input) was never parked; a child that read one line
                            swallowed a request, never answered (5 of 5 trials).
  H2 `MCP-SHARED-POOL`      tool workers held the transport's thread tokens; with 45 slow calls the
                            server could not READ (`ping` 7 s).
  H3 `MCP-STDERR-WEDGE`     one INFO log line per request on stderr; a client that never drains
                            stderr froze the server at call ~1500.

Every class drives the REAL server process through `tests/_mcp_stdio.py`, whose verdict is the
server's own contract (LOST / WEDGED / DIED against each tool's reply budget), never a test-chosen
timeout. Scale-tier sizes run with `MOKATA_SCALE_TIER=1` (wave 5 / the probe), not in default CI.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import glob
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest

import _support  # noqa: F401  (puts src/ on the path)
import _mcp_sdk
import _mcp_stdio as H

from mokata.mcp import status as ST  # noqa: E402

SCALE = os.environ.get("MOKATA_SCALE_TIER") == "1"


def _rm(d):
    shutil.rmtree(d, ignore_errors=True)


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                   stdin=subprocess.DEVNULL,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))


def _git_repo():
    repo = H.make_repo(prefix="i1-git-")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _wait_for(predicate, srv, rid=None):
    """Wait for a fact the server will produce, by progress: the server must stay alive; when
    `rid` is given, the call that produces the fact must not have answered without it; and with no
    call to watch, the fact is owed within the server's own reply budget (`smallest_budget`, the
    contract number — not a short wall clock). Without that last bound a fact that never comes
    (a child blocked forever reading the protocol pipe, as the dropped-park mutant produces) was a
    test that never ended instead of a red one."""
    owed_by = time.perf_counter() + H.smallest_budget()
    while not predicate():
        if srv.p.poll() is not None:
            raise AssertionError(f"the server exited\n{srv.diagnostics()}")
        if rid is not None and srv.reply(rid) is not None and not predicate():
            raise AssertionError(f"call {rid} answered without producing the fact\n"
                                 f"{srv.diagnostics()}")
        if rid is None and time.perf_counter() > owed_by:
            raise AssertionError("the fact did not appear within the server's reply budget\n"
                                 f"{srv.diagnostics()}")
        time.sleep(0.02)


def _shim_body(real_git, log):
    """A `git` that reads ITS stdin (as a hook, a credential helper or `core.fsmonitor` can) for
    subcommands that never take stdin, logs what it read and the prompt env, then runs real git."""
    return f'''
import json, os, subprocess, sys
NO_STDIN = {{"rev-parse", "worktree", "status", "log", "branch", "ls-files", "symbolic-ref",
            "for-each-ref", "rev-list", "merge-base", "config", "remote", "describe", "show-ref"}}
argv = sys.argv[1:]
sub = next((a for a in argv if not a.startswith("-") and "=" not in a), "")
got = None
if sub in NO_STDIN:
    try:
        got = len(sys.stdin.buffer.readline())
    except (OSError, ValueError, AttributeError):
        got = -1
env = {{k: os.environ.get(k) for k in ("GIT_TERMINAL_PROMPT", "GIT_ASKPASS",
                                      "SSH_ASKPASS_REQUIRE", "GIT_SSH_COMMAND")}}
with open({log!r}, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({{"sub": sub, "stdin_bytes": got, "env": env}}) + "\\n")
sys.exit(subprocess.run([{real_git!r}, *argv]).returncode)
'''


def _make_git_shim(d, log):
    """A `git` on PATH that reads stdin. POSIX: an executable script. Windows: a real `git.exe` (a
    `.cmd` would never be found — CreateProcess searches PATH for `.exe` only), built the way pip
    builds console scripts (distlib's launcher + the script)."""
    real = shutil.which("git")
    if real is None:
        raise unittest.SkipTest("git is required")
    body = _shim_body(real, log)
    if os.name != "nt":
        path = os.path.join(d, "git")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"#!{sys.executable}\n{body}")
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        return d
    try:
        from pip._vendor.distlib.scripts import ScriptMaker
    except ImportError:                                   # pragma: no cover - pip ships it
        raise unittest.SkipTest("pip's distlib launcher is needed to build a git.exe shim")
    src = tempfile.mkdtemp(prefix="i1-shimsrc-")
    try:
        with open(os.path.join(src, "git.py"), "w", encoding="utf-8") as fh:
            fh.write(f"#!python\n{body}")
        maker = ScriptMaker(src, d)
        maker.executable = sys.executable
        maker.make("git.py")
    finally:
        _rm(src)
    if not os.path.exists(os.path.join(d, "git.exe")):
        raise AssertionError(f"no git.exe shim was built in {os.listdir(d)}")
    return d


def _shim_env(shim_dir, **extra):
    env = {"PATH": shim_dir + os.pathsep + os.environ.get("PATH", "")}
    env.update(extra)
    return env


def _read_log(log):
    if not os.path.exists(log):
        return []
    with open(log, encoding="utf-8") as fh:
        return [json.loads(ln) for ln in fh if ln.strip()]


@_mcp_sdk.requires_mcp_sdk
class TestI1_ChildNeverSeesProtocolStdin(unittest.TestCase):
    """H1. A tool's child reads stdin while 5 requests are in flight: 0 LOST, and the child reads
    EOF. FIRING CONTROL (mutant): drop the fd-0 park and a status request is swallowed (LOST). On
    Windows this is the GATE that the NUL + `SetStdHandle` park works."""

    def test_I1_a_child_reading_stdin_never_steals_a_request(self):
        repo = H.make_repo()
        self.addCleanup(_rm, repo)
        iterations = 1000 if SCALE else 3
        for n in range(iterations):
            d = tempfile.mkdtemp(prefix="i1-child-")
            self.addCleanup(_rm, d)
            srv = H.McpStdio(repo, env={"I_STDIN_CHILD": d}, launcher=True)
            try:
                first = srv.tool_start("rules", {"path": repo})
                _wait_for(lambda: glob.glob(os.path.join(d, "ready-*")), srv, first)
                # One at a time first: each send is then its own read on the pipe, so a child
                # blocked in read() is the EARLIEST waiter for every request after the first —
                # the theft is not left to a race the server's reader might win. Then 5 at once.
                rest = []
                for _ in range(5):
                    rid = srv.tool_start("status", {"path": repo})
                    verdict = srv.settle([rid])
                    self.assertTrue(verdict.ok, f"iteration {n}: {verdict!r}")
                    rest.append(rid)
                burst = [srv.tool_start("status", {"path": repo}) for _ in range(5)]
                verdict = srv.settle([first] + burst)
                self.assertTrue(verdict.ok, f"iteration {n}: {verdict!r}")
                rest += burst
                _wait_for(lambda: glob.glob(os.path.join(d, "read-*")), srv)
                reads = glob.glob(os.path.join(d, "read-*"))
                with open(reads[0], encoding="utf-8") as fh:
                    self.assertEqual(fh.read(), "0", "the child read protocol bytes, not EOF")
                for rid in rest:
                    self.assertEqual(sorted(H.payload(srv.reply(rid))), sorted(
                        H.payload(srv.reply(rest[0]))))
            finally:
                srv.close()


@_mcp_sdk.requires_mcp_sdk
class TestI1_RealSpawnSitesUnderStdinReadingGit(unittest.TestCase):
    """The real spawn sites (`worktree_list`, `doctor`, `recall`, `query`) with a stdin-reading
    `git` first on PATH, each beside 5 status calls: 0 LOST, and every git the server started read
    EOF. Windows: the shim is a real `git.exe` (a `.cmd` is never found by CreateProcess)."""

    def test_I1_real_spawn_sites_never_feed_a_child_the_protocol(self):
        repo = _git_repo()
        self.addCleanup(_rm, repo)
        shim = tempfile.mkdtemp(prefix="i1-shim-")
        self.addCleanup(_rm, shim)
        log = os.path.join(shim, "git.log")
        _make_git_shim(shim, log)
        srv = H.McpStdio(repo, env=_shim_env(shim))
        try:
            calls = [("worktree_list", {}), ("doctor", {}), ("recall", {"query": "f1"}),
                     ("query", {"target": "f1"})]
            ids = []
            for name, args in calls:
                ids.append(srv.tool_start(name, dict(args, path=repo)))
                ids.extend(srv.tool_start("status", {"path": repo}) for _ in range(5))
            verdict = srv.settle(ids)
        finally:
            srv.close()
        self.assertTrue(verdict.ok, repr(verdict))
        entries = _read_log(log)
        read = [e for e in entries if e["stdin_bytes"] is not None]
        self.assertTrue(read, f"no git call through the shim read stdin: {entries}")
        self.assertEqual([e for e in read if e["stdin_bytes"] != 0], [],
                         "a git child read the protocol input")


@_mcp_sdk.requires_mcp_sdk
class TestI1_ParkPrecedesEveryStartupChild(unittest.TestCase):
    """A child started by startup registration reads EOF and cannot eat `initialize`. FIRING
    CONTROL (mutant): move the park after registration and the child reads the `initialize`
    line — the harness's initialize is LOST/WEDGED."""

    def test_I1_a_startup_child_cannot_read_initialize(self):
        repo = H.make_repo()
        self.addCleanup(_rm, repo)
        d = tempfile.mkdtemp(prefix="i1-startup-")
        self.addCleanup(_rm, d)
        srv = H.McpStdio(repo, env={"I_STARTUP_CHILD": d}, launcher=True)
        try:
            _wait_for(lambda: glob.glob(os.path.join(d, "read-*")), srv)
            with open(glob.glob(os.path.join(d, "read-*"))[0], encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "0")
            self.assertIsInstance(srv.tool("status", {"path": repo}), dict)
        finally:
            srv.close()


@_mcp_sdk.requires_mcp_sdk
class TestI1_BadByteNeverKillsReader(unittest.TestCase):
    """An invalid UTF-8 byte on stdin: at most a parse error for that line, and the server keeps
    serving. FIRING CONTROL (mutant): drop `errors="replace"` and the reader dies (DIED)."""

    def test_I1_an_invalid_byte_never_takes_the_reader_down(self):
        repo = H.make_repo()
        self.addCleanup(_rm, repo)
        srv = H.McpStdio(repo)
        try:
            srv.send_raw(b'{"jsonrpc": "2.0", "id": "bad", "method": "ping", "x": "\xff\xfe"}\n')
            out = srv.tool("status", {"path": repo})
        finally:
            srv.close()
        self.assertIsInstance(out, dict)
        self.assertEqual(out.get("profile"), "standard")


@_mcp_sdk.requires_mcp_sdk
class TestI1_ChildPromptsDisabled(unittest.TestCase):
    """A git child sees every terminal/askpass prompt disabled (what fd 0 cannot cover: `/dev/tty`).
    The test strips the four variables from the server's env, so it grades the server's own setup."""

    @unittest.skipIf(os.name == "nt", "N/A on Windows per the note's matrix (no /dev/tty; and an "
                     "empty env value cannot be represented there — `GIT_ASKPASS=` reaches the "
                     "child as unset)")
    def test_I1_git_children_never_prompt(self):
        repo = _git_repo()
        self.addCleanup(_rm, repo)
        shim = tempfile.mkdtemp(prefix="i1-prompt-")
        self.addCleanup(_rm, shim)
        log = os.path.join(shim, "git.log")
        _make_git_shim(shim, log)
        srv = _StrippedEnvStdio(repo, _shim_env(shim))
        try:
            srv.tool("worktree_list", {"path": repo})
        finally:
            srv.close()
        entries = _read_log(log)
        self.assertTrue(entries, "no git call went through the shim")
        for e in entries:
            self.assertEqual(e["env"]["GIT_TERMINAL_PROMPT"], "0")
            self.assertEqual(e["env"]["GIT_ASKPASS"], "")
            self.assertEqual(e["env"]["SSH_ASKPASS_REQUIRE"], "never")
            self.assertIn("BatchMode=yes", e["env"]["GIT_SSH_COMMAND"] or "")


class _StrippedEnvStdio(H.McpStdio):
    """The harness with the four prompt variables REMOVED from the server's env (an explicit env,
    never a patch of this process's)."""

    _STRIP = ("GIT_TERMINAL_PROMPT", "GIT_ASKPASS", "SSH_ASKPASS_REQUIRE", "GIT_SSH_COMMAND")

    def __init__(self, repo, env):
        super().__init__(repo, env=env)

    def _spawn_env(self, e):
        for k in self._STRIP:
            e.pop(k, None)
        return e


@_mcp_sdk.requires_mcp_sdk
class TestI1_StdoutBelongsToProtocol(unittest.TestCase):
    """A print, a raw fd-1 write and a child's stdout stay OUT of the JSON-RPC stream (O14, moved
    here onto the shared harness)."""

    def test_I1_stray_stdout_never_reaches_the_client(self):
        repo = H.make_repo()
        self.addCleanup(_rm, repo)
        srv = H.McpStdio(repo, env={"I_POLLUTE": "1"}, launcher=True)
        try:
            first = srv.tool("rules", {"path": repo})
            second = srv.tool("status", {"path": repo})
            marker = srv.tool("rules", {"path": repo})
            _wait_for(lambda: srv.stderr_text().count("POLLUTE-child") >= 2, srv)
        finally:
            srv.close()
        self.assertEqual(srv.garbage, [], "non-JSON lines reached the client's stdout")
        for out in (first, second, marker):
            self.assertIsInstance(out, dict)
        err = srv.stderr_text()
        for m in ("POLLUTE-print", "POLLUTE-fd", "POLLUTE-child"):
            self.assertIn(m, err, f"{m} should be harmless stderr noise")


@_mcp_sdk.requires_mcp_sdk
class TestI1_TransportHasItsOwnThreads(unittest.TestCase):
    """H2. 200 slow calls are in flight (each blocked until the test opens a gate); then `ping` and
    `status` are both answered BEFORE any slow call. FIRING CONTROLS (mutants): drop `limiter=`, or
    make the tool limiter finite (64) — the transport cannot read, and the ping is answered only
    after the slow calls give up their threads (or is WEDGED)."""

    def test_I1_ping_and_status_never_wait_on_tool_threads(self):
        repo = H.make_repo()
        self.addCleanup(_rm, repo)
        d = tempfile.mkdtemp(prefix="i1-gate-")
        self.addCleanup(_rm, d)
        gate = os.path.join(d, "open")
        n = 1000 if SCALE else 200
        srv = H.McpStdio(repo, env={"I_GATE": gate, "I_GATE_TOOL": "query"}, launcher=True)
        try:
            slow = [srv.tool_start("query", {"path": repo, "target": f"t{i}"}) for i in range(n)]
            ping = srv.start("ping")
            status = srv.tool_start("status", {"path": repo})
            verdict = srv.settle([ping, status])
            ping_latency = srv.latency(ping)
            answered_slow = [i for i in slow if srv.reply(i) is not None]
            with open(gate, "w", encoding="utf-8"):
                pass
            rest = srv.settle(slow)
        finally:
            srv.close()
        self.assertTrue(verdict.ok, repr(verdict))
        self.assertEqual(answered_slow, [], "a slow call answered before ping/status — the "
                                            "transport waited on tool threads")
        # Under a shared pool the ping can only be read once a slow call gives its thread back,
        # i.e. at that call's reply budget — so a ping as slow as the budget waited on tools.
        self.assertLess(ping_latency, H.smallest_budget(),
                        f"ping took {ping_latency:.1f}s, a tool's whole reply budget")
        self.assertTrue(rest.ok, repr(rest))
        self.assertEqual(H.payload(srv.reply(status)).get("profile"), "standard")


@_mcp_sdk.requires_mcp_sdk
class TestI1_JoinAndSerialiseUnderOwnLimiter(unittest.TestCase):
    """O14's join semantics hold under the infinite limiter: 500 parallel IDENTICAL calls run the
    body once and all get its answer. (Write serialisation per repo is graded in-process by
    `test_o14_mcp_stability.TestR_O14_RunningIsJoinedNeverRepeated`.)"""

    def test_I1_identical_parallel_calls_run_once(self):
        repo = H.make_repo()
        self.addCleanup(_rm, repo)
        d = tempfile.mkdtemp(prefix="i1-join-")
        self.addCleanup(_rm, d)
        gate, count = os.path.join(d, "open"), os.path.join(d, "runs")
        srv = H.McpStdio(repo, env={"I_GATE": gate, "I_GATE_TOOL": "query", "I_COUNT": count},
                         launcher=True)
        try:
            ids = [srv.tool_start("query", {"path": repo, "target": "f1"}) for _ in range(500)]
            ping = srv.start("ping")
            self.assertTrue(srv.settle([ping]).ok)
            with open(gate, "w", encoding="utf-8"):
                pass
            verdict = srv.settle(ids)
        finally:
            srv.close()
        self.assertTrue(verdict.ok, repr(verdict))
        with open(count, encoding="utf-8") as fh:
            self.assertEqual(len(fh.read().split()), 1, "identical calls ran the body more than once")
        first = H.payload(srv.reply(ids[0]))
        self.assertTrue(all(H.payload(srv.reply(i)) == first for i in ids))


@_mcp_sdk.requires_mcp_sdk
class TestI1_StderrSilentPerRequest(unittest.TestCase):
    """H3. Steady-state serving writes NOTHING to stderr: a sweep of every read tool plus five write
    tools grows stderr by zero bytes. The window is fenced by two marker calls (the polluting
    launcher's `rules`) so the check never races the stderr reader. FIRING CONTROL (mutant):
    `log_level` INFO — one "Processing request" line per call."""

    WRITES = (("remember", {"subject": "s", "value": "v"}),
              ("config_set", {"key": "settings.mcp.test_timeout_seconds", "value": "90"}),
              ("spec_emit", {"title": "t", "criteria": [{"id": "AC1", "text": "x"}],
                             "tests": [{"name": "test_x", "ac_ids": ["AC1"]}],
                             "tasks": [{"id": "T1", "title": "x", "ac_ids": ["AC1"],
                                        "scope": ["src/m1.py"]}],
                             "scope": {"authorized": ["src/m1.py"], "deferred": []}}),
              ("progress_mark", {"stage": "spec"}),
              ("review_record", {"outcome": "passed"}))

    def test_I1_serving_writes_nothing_to_stderr(self):
        repo = H.make_repo()
        self.addCleanup(_rm, repo)
        srv = H.McpStdio(repo, env={"I_POLLUTE": "1"}, launcher=True)
        try:
            tools = srv.tools_list()
            srv.tool("rules", {"path": repo})
            _wait_for(lambda: srv.stderr_text().count("POLLUTE-child") >= 1, srv)
            before = srv.stderr_text()
            reads = [t for t in tools if (t.get("annotations") or {}).get("readOnlyHint")
                     and t["name"] not in _NOT_SWEPT and t["name"] != "rules"]
            for t in reads:
                srv.tool(t["name"], H.required_call(t, repo))
            for name, args in self.WRITES:
                srv.tool(name, dict(args, path=repo))
            srv.tool("rules", {"path": repo})
            _wait_for(lambda: srv.stderr_text().count("POLLUTE-child") >= 2, srv)
            after = srv.stderr_text()
        finally:
            srv.close()
        window = after[len(before):]
        window = window[:window.index("POLLUTE-print")]
        self.assertEqual(window, "", f"stderr grew while serving {len(reads)} reads + "
                                     f"{len(self.WRITES)} writes:\n{window[:1500]}")


@_mcp_sdk.requires_mcp_sdk
class TestI1_NonDrainingStderrNeverWedges(unittest.TestCase):
    """H3, the user-visible shape: a client that never reads stderr makes 5,000 requests and the
    server never WEDGES (on master it froze at ~1,500, when the stderr pipe filled)."""

    def test_I1_a_client_that_never_drains_stderr_is_still_served(self):
        repo = H.make_repo()
        self.addCleanup(_rm, repo)
        total = 20000 if SCALE else 5000
        srv = H.McpStdio(repo, drain_stderr=False)
        try:
            sent = 0
            while sent < total:
                batch = []
                for k in range(250):
                    batch.append(srv.tool_start("status", {"path": repo}) if k % 10 == 0
                                 else srv.start("ping"))
                verdict = srv.settle(batch)
                self.assertTrue(verdict.ok, f"after {sent} requests: {verdict!r}")
                sent += len(batch)
        finally:
            srv.close()


@_mcp_sdk.requires_mcp_sdk
class TestI1_LargePayloadFraming(unittest.TestCase):
    """#45. A 1 MB and a 32 MB field: the reply is no larger than the request + 64 KB (a proposal
    carries its content ONCE, bounded), and `ping` stays live. FIRING CONTROL (mutant): re-add the
    `preview` echo — the reply is twice the request."""

    def test_I1_large_fields_frame_and_never_echo(self):
        repo = H.make_repo()
        self.addCleanup(_rm, repo)
        sizes = (1, 32, 200) if SCALE else (1, 32)
        srv = H.McpStdio(repo)
        try:
            for mb in sizes:
                value = "abc " * (mb * 1024 * 256)
                args = {"path": repo, "subject": f"big-{mb}", "value": value}
                request_bytes = len(json.dumps({"name": "remember", "arguments": args}))
                rid = srv.tool_start("remember", args)
                ping = srv.start("ping")
                verdict = srv.settle([rid, ping])
                self.assertTrue(verdict.ok, f"{mb} MB: {verdict!r}")
                reply_bytes = len(json.dumps(srv.reply(rid)))
                self.assertLessEqual(reply_bytes, request_bytes + 64 * 1024,
                                     f"{mb} MB request answered with {reply_bytes} bytes")
                out = H.payload(srv.reply(rid))
                self.assertEqual(out.get("status"), ST.PROPOSED, str(out)[:300])
                self.assertEqual(out.get("preview_length", 0) > 0, True)
        finally:
            srv.close()


@_mcp_sdk.requires_mcp_sdk
class TestI1_StartupNeverWaitsOnRegistry(unittest.TestCase):
    """With the session-registry lock held by another process, `initialize` answers before that
    lock's own bounded wait could have expired: registration is off the startup path."""

    def test_I1_initialize_answers_while_the_registry_is_locked(self):
        from mokata.oslock import DEFAULT_TIMEOUT, file_lock
        repo = H.make_repo()
        self.addCleanup(_rm, repo)
        state = os.path.join(repo, ".mokata", "temp_local", "state")
        os.makedirs(state, exist_ok=True)
        lock = os.path.join(state, ".session_registry.json.lock")
        held, release = threading.Event(), threading.Event()

        def holder():
            with file_lock(lock):
                held.set()
                release.wait()

        t = threading.Thread(target=holder, daemon=True)
        t.start()
        held.wait()
        try:
            srv = H.McpStdio(repo, initialize=False)
            try:
                rid = srv.start("initialize", {
                    "protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "i1", "version": "0"}})
                verdict = srv.settle([rid])
                latency = srv.latency(rid)
            finally:
                srv.close()
        finally:
            release.set()
            t.join()
        self.assertTrue(verdict.ok, repr(verdict))
        self.assertLess(latency, DEFAULT_TIMEOUT,
                        f"initialize waited {latency:.1f}s — the registry lock's own bound is "
                        f"{DEFAULT_TIMEOUT}s, so it waited on registration")


@_mcp_sdk.requires_mcp_sdk
class TestI1_ReviewRecordUnderLoad(unittest.TestCase):
    """#46's shape: `review_record` while 50 slow calls run is answered before any of them."""

    def test_I1_review_record_is_answered_under_load(self):
        repo = H.make_repo()
        self.addCleanup(_rm, repo)
        d = tempfile.mkdtemp(prefix="i1-review-")
        self.addCleanup(_rm, d)
        gate = os.path.join(d, "open")
        srv = H.McpStdio(repo, env={"I_GATE": gate, "I_GATE_TOOL": "query"}, launcher=True)
        try:
            slow = [srv.tool_start("query", {"path": repo, "target": f"t{i}"}) for i in range(50)]
            rid = srv.tool_start("review_record", {"path": repo, "outcome": "passed"})
            verdict = srv.settle([rid])
            early = [i for i in slow if srv.reply(i) is not None]
            with open(gate, "w", encoding="utf-8"):
                pass
            self.assertTrue(srv.settle(slow).ok)
        finally:
            srv.close()
        self.assertTrue(verdict.ok, repr(verdict))
        self.assertEqual(early, [])
        out = H.payload(srv.reply(rid))
        self.assertNotEqual(out.get("reason_code"), "invalid_input", out)
        self.assertNotEqual(out.get("status"), ST.ERROR, out)


# Tools a schema-built sweep does not call: `baseline` runs the repo's test suite.
_NOT_SWEPT = frozenset({"baseline"})


@_mcp_sdk.requires_mcp_sdk
class TestI1_EveryToolOverStdio(unittest.TestCase):
    """Every registered tool, over stdio, with a call built from its schema alone: exactly one
    answer each, a structured result (never the SDK's raw text), and never `error` — a valid call on
    an initialized repo is not a mokata fault. Also: nothing reached stdout but JSON-RPC."""

    def test_I1_every_registered_tool_answers_once_over_stdio(self):
        from mokata.mcp.registry import tool_names
        repo = _git_repo()
        self.addCleanup(_rm, repo)
        srv = H.McpStdio(repo)
        try:
            tools = srv.tools_list()
            self.assertEqual(sorted(t["name"] for t in tools), sorted(tool_names()))
            ids = {}
            for t in tools:
                if t["name"] in _NOT_SWEPT:
                    continue
                ids[srv.tool_start(t["name"], H.required_call(t, repo))] = t["name"]
            verdict = srv.settle(ids)
            replies = {name: srv.reply(rid) for rid, name in ids.items()}
        finally:
            srv.close()
        self.assertTrue(verdict.ok, repr(verdict))
        self.assertEqual(srv.garbage, [])
        bad = {}
        for name, msg in replies.items():
            out = H.payload(msg)
            if not isinstance(out, dict) or "_text" in out or "_rpc_error" in out:
                bad[name] = str(out)[:200]
            elif out.get("status") == ST.ERROR:
                bad[name] = f"error: {out.get('reason')}"
            elif out.get("reason_code") == "invalid_input" and not H.world_refusal(name, out):
                bad[name] = f"invalid_input on a schema-built call: {out.get('field')}"
        self.assertEqual(bad, {})


@_mcp_sdk.requires_mcp_sdk
class TestI1_ParityWithCliTwin(unittest.TestCase):
    """The MCP tool and its CLI twin (`parity.SURFACE_MATRIX`) agree on the semantic fields, for the
    twins with a JSON form: the same version/profile from `status`, the same tiers from `rules`."""

    def _cli(self, repo, *argv):
        env = dict(os.environ, PYTHONPATH=H.SRC)
        out = subprocess.run([sys.executable, "-m", "mokata", *argv], cwd=repo, env=env,
                             capture_output=True, text=True, stdin=subprocess.DEVNULL)
        return out

    def test_I1_mcp_answers_what_the_cli_answers(self):
        from mokata import parity
        self.assertIn("status", parity.SURFACE_MATRIX["status"].mcp_read)
        repo = H.make_repo()
        self.addCleanup(_rm, repo)
        srv = H.McpStdio(repo)
        try:
            status = srv.tool("status", {"path": repo})
            doctor = srv.tool("doctor", {"path": repo})
        finally:
            srv.close()
        cli = self._cli(repo, "status", "--path", repo)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertIn(status["version"], cli.stdout)
        self.assertIn(status["profile"], cli.stdout)
        cli_doc = self._cli(repo, "doctor", "--path", repo)
        self.assertEqual(cli_doc.returncode == 0, bool(doctor.get("ok")),
                         f"MCP doctor ok={doctor.get('ok')} but CLI exited {cli_doc.returncode}")


if __name__ == "__main__":
    unittest.main()
