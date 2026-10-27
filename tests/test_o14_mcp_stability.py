"""O14 MCP-STABILITY (0.0.26 release blocker) — the MCP server, driven over real stdio.

Jas, 2026-10-22: "stability of mokata, specially for registering specs — it fails at times; CLI
works fast but MCP gets stuck quite often", and "especially when amending the spec at later
stages". Measured before this stage (mcp 1.30, the raw stdio harness below):

  * FastMCP runs a SYNC tool function ON THE EVENT LOOP and `_serve` was sync, so one slow tool
    froze the whole server: on the K bed, eight parallel calls ALL answered at 7.6 s and an MCP
    `ping` took 7.6 s; on django a `spec_emit` redeem waited out a `query` first. The CLI runs one
    command; the server ran a queue.
  * A retried `spec_amend` redeem (the client's retry of a reply it lost) RE-RAN the regression
    before the consent check refused the burnt approval: the amendment that had just landed was
    re-opened (v2 -> v3), every passed checkpoint wiped, and the run left regressed with nothing to
    approve. 100% reproducible.
  * The redeem re-ran gate 2's blast-radius lens from scratch, so a widen that passed at propose
    could be refused at redeem — after the human approved it — when the second run crossed 20 s.
  * A call past the 60 s budget answered `timed_out` + "nothing was half-committed" while the
    orphaned worker went on to commit; the retry it invited then started the work a SECOND time.
  * Anything reaching fd 1 (a print, a child process without captured stdout) went into the
    JSON-RPC stream.

Each class below fails if its fix is reverted (firing controls are named per test).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)
import _mcp_sdk  # noqa: E402

from mokata.mcp import server as MS  # noqa: E402
from mokata.mcp import status as ST  # noqa: E402

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))

# A launcher that serves the REAL server, optionally with one tool slowed (to make the event-loop
# question measurable on a tiny repo) and one tool that pollutes fd 1 three different ways.
_LAUNCHER = r"""
import functools, os, subprocess, sys, time
from mokata.mcp import registry, server
slow = float(os.environ.get("O14_SLOW", "0") or 0)
def _slowed(orig):
    @functools.wraps(orig)
    def run(*a, **k):
        time.sleep(slow)
        return orig(*a, **k)
    return run
def _noisy(orig):
    @functools.wraps(orig)
    def run(*a, **k):
        print("POLLUTE-print")
        os.write(1, b"POLLUTE-fd\n")
        subprocess.run([sys.executable, "-c", "print('POLLUTE-child')"])
        return orig(*a, **k)
    return run
for spec in registry.TOOLS:
    if spec.name == "lat_check" and slow:
        spec.fn = _slowed(spec.fn)
    if spec.name == "rules" and os.environ.get("O14_POLLUTE"):
        spec.fn = _noisy(spec.fn)
sys.exit(server.main(sys.argv[1:]))
"""


class _Stdio:
    """Raw JSON-RPC over the server's stdio — what Claude Code sends, including parallel calls."""

    def __init__(self, repo, run_id, env=None):
        e = dict(os.environ, PYTHONPATH=SRC, MOKATA_SESSION_ID=run_id)
        e.update(env or {})
        self.p = subprocess.Popen([sys.executable, "-c", _LAUNCHER, "--path", repo], cwd=repo,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, env=e)
        self.lock = threading.Lock()
        self.pending, self.results, self.garbage, self.stderr = {}, {}, [], []
        self.nid = 0
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._err, daemon=True).start()
        self.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "o14-test", "version": "0"}}, timeout=120)
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _read(self):
        for line in self.p.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                self.garbage.append(line[:200])
                continue
            ev = self.pending.pop(msg.get("id"), None)
            if ev is not None:
                self.results[msg["id"]] = (msg, time.perf_counter())
                ev.set()

    def _err(self):
        for line in self.p.stderr:
            self.stderr.append(line.decode("utf-8", "replace"))

    def _send(self, msg):
        self.p.stdin.write((json.dumps(msg) + "\n").encode())
        self.p.stdin.flush()

    def start(self, method, params=None):
        with self.lock:
            self.nid += 1
            ev = threading.Event()
            self.pending[self.nid] = ev
            msg = {"jsonrpc": "2.0", "id": self.nid, "method": method}
            if params is not None:
                msg["params"] = params
            self._send(msg)
            return self.nid, ev, time.perf_counter()

    def wait(self, handle, timeout=120):
        rid, ev, sent = handle
        if not ev.wait(timeout):
            return None, None
        msg, at = self.results.pop(rid)
        return msg, at - sent

    def call(self, method, params=None, timeout=120):
        return self.wait(self.start(method, params), timeout)

    def tool_start(self, name, args):
        return self.start("tools/call", {"name": name, "arguments": args})

    def tool(self, name, args, timeout=120):
        msg, dt = self.call("tools/call", {"name": name, "arguments": args}, timeout)
        return _payload(msg), dt

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(15)
        except Exception:  # noqa: BLE001 - test teardown
            self.p.kill()
            self.p.wait(5)
        for stream in (self.p.stdout, self.p.stderr):
            try:
                stream.close()
            except Exception:  # noqa: BLE001 - test teardown
                pass


def _payload(msg):
    if msg is None:
        return None
    res = msg.get("result") or {}
    sc = res.get("structuredContent")
    if isinstance(sc, dict):
        return sc.get("result", sc) if set(sc) == {"result"} else sc
    for c in res.get("content", []):
        if c.get("type") == "text":
            try:
                return json.loads(c["text"])
            except ValueError:
                return {"_text": c["text"]}
    return {"_raw": msg}


def _repo():
    d = tempfile.mkdtemp(prefix="o14-")
    os.makedirs(os.path.join(d, "src"))
    for i in range(6):
        with open(os.path.join(d, "src", f"m{i}.py"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(f"def f{i}(x):\n    return x + {i}\n")
    from mokata.init import init_repo
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda *_a: None)
    return d


def _spec(n_ac, files, reason=None):
    d = {"title": "adders add",
         "criteria": [{"id": f"AC{i}", "text": f"f{i} adds {i} to its input"}
                      for i in range(1, n_ac + 1)],
         "tests": [{"name": f"test_f{i}_adds", "ac_ids": [f"AC{i}"]} for i in range(1, n_ac + 1)],
         "scope": {"authorized": list(files), "deferred": []},
         # R2 — the task plan, one task per criterion, each inside the authorized scope
         "tasks": [{"id": f"T{i}", "title": f"implement f{i}", "ac_ids": [f"AC{i}"],
                    "scope": [files[min(i - 1, len(files) - 1)]]} for i in range(1, n_ac + 1)]}
    if reason:
        d["reason"] = reason
    return d


def _approve(repo, pid, run_id):
    """The human's out-of-band act, from ANOTHER process (this test, not the server)."""
    from mokata import approval
    from mokata.govern.ledger import AuditLedger
    with mock.patch.dict(os.environ, {"MOKATA_SESSION_ID": run_id}):
        approval.approve(repo, pid, actor="o14-human",
                         ledger=AuditLedger.from_mokata_dir(os.path.join(repo, ".mokata")))


_WRITER = r"""
import sys, time
from mokata.config import Surface
from mokata import tdd_state as T
from mokata.govern.ledger import AuditLedger
s = Surface.load(sys.argv[1]); L = AuditLedger.from_mokata_dir(s.mokata_dir)
end = time.time() + float(sys.argv[3])
n = 0
while time.time() < end:
    T.record(s.state, sys.argv[2], red=["test_f1_adds"]); L.record("o14_hook_probe", i=n); n += 1
print(n)
"""


def _amend_record(repo, run_id):
    from mokata.config import Surface
    from mokata.engine.amend import amend_key
    from mokata.mcp.consent import _evidence_store
    with mock.patch.dict(os.environ, {"MOKATA_SESSION_ID": run_id}):
        store, rid = _evidence_store(Surface.load(repo), repo)
        return store.read(amend_key(rid)) or {}, (store.read("emitted_spec") or {})


@_mcp_sdk.requires_mcp_sdk
class TestR_O14_TheLoopIsFree(unittest.TestCase):
    """While one tool works, the server keeps answering. FIRING CONTROL: register `_serve(...)`
    instead of `_offloop(_serve(...))` in `build_server` and the ping waits out the slow call
    (measured: ping == the slow call's duration)."""

    SLOW = 3.0

    def test_R_O14_ping_and_parallel_calls_do_not_wait_for_a_slow_tool(self):
        repo = _repo()
        srv = _Stdio(repo, "run-o14-loop", env={"O14_SLOW": str(self.SLOW)})
        try:
            slow = srv.tool_start("lat_check", {"path": repo})
            time.sleep(0.3)
            _, ping = srv.call("ping", timeout=60)
            quick = [srv.tool_start("status", {"path": repo}) for _ in range(4)]
            quick_lat = [srv.wait(h, 60)[1] for h in quick]
            msg, slow_lat = srv.wait(slow, 60)
        finally:
            srv.close()
            shutil.rmtree(repo, ignore_errors=True)
        self.assertIsNotNone(msg, "the slow call itself must still answer")
        self.assertGreaterEqual(slow_lat, self.SLOW)
        self.assertLess(ping, 1.0, f"ping waited {ping:.2f}s behind a {self.SLOW}s tool")
        self.assertTrue(all(q is not None and q < self.SLOW - 0.5 for q in quick_lat),
                        f"parallel status calls serialised behind the slow tool: {quick_lat}")
        self.assertEqual(srv.garbage, [])


@_mcp_sdk.requires_mcp_sdk
class TestR_O14_StdoutBelongsToTheProtocol(unittest.TestCase):
    """A print, a raw fd-1 write and a child process's stdout all stay OUT of the JSON-RPC stream.
    FIRING CONTROL: make `_run_stdio` call `server.run()` and all three lines land in `garbage`."""

    def test_R_O14_stray_stdout_never_reaches_the_client(self):
        repo = _repo()
        srv = _Stdio(repo, "run-o14-out", env={"O14_POLLUTE": "1"})
        try:
            first, _ = srv.tool("rules", {"path": repo})
            second, _ = srv.tool("status", {"path": repo})
        finally:
            srv.close()
            shutil.rmtree(repo, ignore_errors=True)
        self.assertEqual(srv.garbage, [], "non-JSON lines reached the client's stdout")
        self.assertIsInstance(first, dict)
        self.assertIsInstance(second, dict)
        err = "".join(srv.stderr)
        for marker in ("POLLUTE-print", "POLLUTE-fd", "POLLUTE-child"):
            self.assertIn(marker, err, f"{marker} should be harmless stderr noise")


@_mcp_sdk.requires_mcp_sdk
class TestR_O14_SpecRegistrationUnderStress(unittest.TestCase):
    """emit -> amend (develop-stage, RED on record) -> retried redeems -> amend again, with parallel
    reads and a concurrent hook-like writer on the same run state, over real stdio.

    FIRING CONTROLS: drop the `_retry_of_redeemed` guard in `spec_amend` and the amend record reads
    `open` (v2 -> v3) after the retries; revert `_offloop` and the redeem latency bound fails."""

    def test_R_O14_spec_registration_is_idempotent_and_never_wedges(self):
        repo = _repo()
        run = "run-o14-stress"
        srv = _Stdio(repo, run)
        writer = subprocess.Popen([sys.executable, "-c", _WRITER, repo, run, "25"],
                                  env=dict(os.environ, PYTHONPATH=SRC, MOKATA_SESSION_ID=run),
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        reads = ("status", "spec_show", "progress", "audit", "doctor", "rules")
        lat = {}

        def flow(tool, args):
            prop, dt = srv.tool(tool, dict(args, path=repo))
            lat.setdefault(tool + ":propose", []).append(dt)
            self.assertEqual(prop.get("status"), "proposed", prop)
            _approve(repo, prop["proposal_id"], run)
            noise = [srv.tool_start(n, {"path": repo}) for n in reads]
            res, dt = srv.tool(tool, dict(args, path=repo, proposal_id=prop["proposal_id"]))
            lat.setdefault(tool + ":redeem", []).append(dt)
            for h in noise:
                self.assertIsNotNone(srv.wait(h, 120)[0], "a parallel read got no response")
            self.assertTrue(res.get("committed"), res)
            return prop["proposal_id"], res

        def stage(name):
            res, _ = srv.tool("progress_mark", {"path": repo, "stage": name})
            self.assertEqual(res.get("status"), "recorded", f"{name}: {res}")

        try:
            flow("spec_emit", _spec(1, ["src/m1.py"]))
            stage("develop")                       # develop-stage: the plan hash is attested
            sealed_v1 = _amend_record(repo, run)[1].get("plan_hash")
            self.assertTrue(sealed_v1)
            a2 = _spec(2, ["src/m1.py", "src/m2.py"], reason="need f2")
            pid, res = flow("spec_amend", a2)
            self.assertEqual(res.get("version"), 2)
            # the client retries the redeem twice, in parallel (a lost reply + a double fire)
            retries = [srv.tool_start("spec_amend", dict(a2, path=repo, proposal_id=pid))
                       for _ in range(2)]
            for h in retries:
                again = _payload(srv.wait(h, 120)[0])
                self.assertFalse(again.get("committed"), "one approval, one write")
                self.assertTrue(again.get("already_applied"), again)
            record, spec = _amend_record(repo, run)
            self.assertEqual(record.get("status"), "closed", f"retry re-opened the amend: {record}")
            self.assertEqual(spec.get("version"), 2)
            self.assertEqual([t["id"] for t in spec.get("tasks", [])], ["T1", "T2"])
            sealed_v2 = spec.get("plan_hash")
            self.assertTrue(sealed_v2)
            self.assertNotEqual(sealed_v2, sealed_v1, "the amendment re-seals the plan")
            stage("develop")                       # the re-sealed plan is the approved one
            stage("review")
            _, res = flow("spec_amend", _spec(3, ["src/m1.py", "src/m2.py", "src/m3.py"],
                                              reason="need f3"))
            self.assertEqual(res.get("version"), 3)
            record, spec = _amend_record(repo, run)
            self.assertEqual(record.get("status"), "closed")
            self.assertEqual(len(spec.get("criteria", [])), 3)
            stage("review")
            # one plan approval per LANDED write — the retries sealed nothing
            from mokata.govern.ledger import AuditLedger
            led = AuditLedger.from_mokata_dir(os.path.join(repo, ".mokata"))
            approved = [e for e in led.entries() if e.get("kind") == "plan_approved"]
            self.assertEqual(len(approved), 3, [e.get("payload") for e in approved])
        finally:
            out, err = writer.communicate(timeout=120)
            srv.close()
            shutil.rmtree(repo, ignore_errors=True)
        self.assertEqual(writer.returncode, 0, f"the concurrent writer failed: {err[-400:]}")
        self.assertGreater(int(out.strip() or 0), 0)
        self.assertEqual(srv.garbage, [])
        # per-call latency budgets on a tiny repo (generous: CI runners are slow and shared).
        # WINDOWS IS MEASURED SLOWER, AND SAID: msvcrt's byte-range lock is not fair, so against
        # this test's writer — which re-takes the state lock in a tight loop for 25 s — a propose
        # polls behind it for ~20 s (CI 0.0.26 cut: 20.56 s, 20.50 s). Disclosed in the 0.0.26
        # notes; a real hook does not hold the lock in a loop. The bound there is the writer's 25 s
        # window plus slack, so a WEDGE (the thing this test exists for) still reds everywhere.
        bound = 45.0 if sys.platform == "win32" else 20.0
        for key, values in lat.items():
            self.assertLess(max(values), bound, f"{key} took {values}")


@_mcp_sdk.requires_mcp_sdk
class TestR_O14_ReadLatencyBudget(unittest.TestCase):
    """Every read tool answers on a small repo inside a per-call budget, over stdio, and a burst of
    ALL of them in parallel finishes inside the same budget (the loop is not a queue)."""

    BUDGET = 15.0
    SKIP = {"baseline", "ide_open", "watch", "session_push", "session_pull", "progress_mark",
            "run_finish", "review_record", "session_save"}

    def test_R_O14_every_read_tool_answers_inside_its_budget(self):
        repo = _repo()
        srv = _Stdio(repo, "run-o14-lat")
        try:
            tools = srv.call("tools/list")[0]["result"]["tools"]
            reads = [t["name"] for t in tools if t.get("annotations", {}).get("readOnlyHint")
                     and t["name"] not in self.SKIP]
            seq = {}
            for name in reads:
                res, dt = srv.tool(name, {"path": repo})
                self.assertIsInstance(res, dict, name)
                self.assertNotEqual(res.get("status"), ST.RUNNING, name)
                seq[name] = dt
            started = time.perf_counter()
            hs = [srv.tool_start(n, {"path": repo}) for n in reads]
            got = [srv.wait(h, 120)[0] for h in hs]
            burst = time.perf_counter() - started
        finally:
            srv.close()
            shutil.rmtree(repo, ignore_errors=True)
        slow = {k: round(v, 2) for k, v in seq.items() if v > self.BUDGET}
        self.assertEqual(slow, {}, f"read tools over the {self.BUDGET}s budget")
        self.assertTrue(all(g is not None for g in got))
        self.assertLess(burst, self.BUDGET, f"a parallel burst of {len(reads)} reads took {burst}")
        self.assertLess(statistics.median(seq.values()), 2.0)


class _NoRegistry(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(MS, "_register_this_window", lambda path: None)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(MS._await_registrations, 2.0)


class TestR_O14_RunningIsJoinedNeverRepeated(_NoRegistry):
    """Past the reply budget a call answers `running`; the re-call joins the same work and gets its
    result; parallel identical calls run the body ONCE. FIRING CONTROL: make `_join_or_start`
    always start a new call and `runs` becomes 2 (and 3)."""

    def test_R_O14_running_then_the_recall_collects_the_one_result(self):
        runs = []

        def body(path=".", n=0):
            runs.append(n)
            time.sleep(0.6)
            return {"status": "ok", "n": n}

        served = MS._serve(body, name="slowbody")
        with mock.patch.object(MS, "MCP_SURFACE_TIMEOUT_SECONDS", 0.15):
            first = served(path=".", n=7)
            self.assertEqual(first["status"], ST.RUNNING)
            self.assertIs(first["isError"], False)
        second = served(path=".", n=7)
        self.assertEqual(second, {"status": "ok", "n": 7})
        self.assertEqual(runs, [7], "the re-call started the work a second time")

    def test_R_O14_parallel_identical_calls_share_one_run(self):
        runs = []

        def body(path=".", n=0):
            runs.append(n)
            time.sleep(0.3)
            return {"status": "ok", "n": n}

        served = MS._serve(body, name="shared")
        out = []
        ts = [threading.Thread(target=lambda: out.append(served(path=".", n=1))) for _ in range(3)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(10)
        self.assertEqual(len(out), 3)
        self.assertTrue(all(o == {"status": "ok", "n": 1} for o in out))
        self.assertEqual(runs, [1])
        # and a LATER identical call (nobody was told `running`) is new work, not a cached answer
        served(path=".", n=1)
        self.assertEqual(runs, [1, 1])

    def test_R_O14_writes_on_one_repo_never_interleave(self):
        active, peak = [0], [0]
        guard = threading.Lock()

        def write(path=".", n=0):
            with guard:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.1)
            with guard:
                active[0] -= 1
            return {"status": "committed", "n": n}

        served = MS._serve(write, name="w", kind="write")
        ts = [threading.Thread(target=served, kwargs={"path": ".", "n": i}) for i in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(10)
        self.assertEqual(peak[0], 1)


class TestR_O14_LockWaitsAreBoundedAndRetryable(_NoRegistry):
    """FIRING CONTROL: restore the bare `self._rlock.acquire()` in `_ReentrantFileLock.hold` and the
    second holder blocks for the whole hold instead of raising inside the bound."""

    def test_R_O14_a_busy_state_lock_is_a_retryable_error(self):
        from mokata.oslock import LockTimeout

        def busy(path="."):
            raise LockTimeout("timed out after 10s acquiring lock: /secret/path")

        out = MS._serve(busy, name="spec_amend")(path=".")
        self.assertEqual(out["status"], ST.ERROR)
        self.assertIs(out["retryable"], True)
        self.assertEqual(out["reason_code"], "state-busy")
        self.assertNotIn("/secret/path", json.dumps(out))

    def test_R_O14_the_in_process_ledger_lock_wait_is_bounded(self):
        from mokata.govern import ledger as L
        from mokata.oslock import LockTimeout
        d = tempfile.mkdtemp(prefix="o14-lock-")
        self.addCleanup(shutil.rmtree, d, True)
        lock = L._ReentrantFileLock.for_path(os.path.join(d, "x.lock"))
        held, release = threading.Event(), threading.Event()

        def holder():
            with lock.hold():
                held.set()
                release.wait(10)

        t = threading.Thread(target=holder, daemon=True)
        t.start()
        held.wait(5)
        with mock.patch.object(L, "LOCK_WAIT_SECONDS", 0.3):
            started = time.monotonic()
            with self.assertRaises(LockTimeout):
                with lock.hold():
                    pass
            self.assertLess(time.monotonic() - started, 3.0)
        release.set()
        t.join(5)


class TestR_O14_TheRedeemReusesTheEarnedLens(unittest.TestCase):
    """Propose runs gate 2 on a widening amendment; the redeem of the SAME amendment reuses that
    verdict instead of re-running the lens. FIRING CONTROL: delete the `earned is not None` branch
    in `begin_amend` and `calls` is 2."""

    def test_R_O14_one_lens_run_per_amendment(self):
        from _support import mcp_commit
        from mokata import brainstorm_impact as BI
        from mokata.mcp import tools_spec as TS
        repo = _repo()
        self.addCleanup(shutil.rmtree, repo, True)
        env = mock.patch.dict(os.environ, {"MOKATA_SESSION_ID": "run-o14-lens"})
        env.start()
        self.addCleanup(env.stop)
        from mokata import session
        session.reset_for_test()
        self.addCleanup(session.reset_for_test)
        res = mcp_commit(TS.spec_emit, path=repo, **_spec(1, ["src/m1.py"]))
        self.assertTrue(res.get("committed"), res)
        real = BI.compute_impact
        calls = []

        def counting(*a, **k):
            calls.append(a)
            return real(*a, **k)

        with mock.patch.object(BI, "compute_impact", counting):
            res = mcp_commit(TS.spec_amend, path=repo,
                             **_spec(2, ["src/m1.py", "src/m2.py"], reason="need f2"))
        self.assertTrue(res.get("committed"), res)
        self.assertEqual(len(calls), 1, "the redeem re-ran the blast-radius lens")
        self.assertIsNotNone(res.get("impact"), "the reused verdict still reaches the result")


if __name__ == "__main__":
    unittest.main()
