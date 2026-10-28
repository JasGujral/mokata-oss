"""ID.S3 (0.0.25 Lane ID) — the CLI↔IDE bridge: event bus, Claude Code http hooks, follow mode,
HOOK-BUDGET.

Business rules — what must be true for the user:
  * An edit a session makes appears on the open IDE page within 1 s (doc 112 §4 exit criterion 6) —
    pushed through the bus, not found by a poll. Measured here end to end on a real server.
  * The bus is harness-neutral: anything can post a neutral event; Claude Code's own hook payload is
    one ADAPTER onto the same vocabulary. The typed event store and the dirty-track timeline feed it
    too, so follow mode works with no http hook wired (the cheap fallback).
  * The hook door opens nothing else: the hook token cannot read the repo, a browser (any Origin)
    cannot use it, a wrong token is refused, a foreign Host is refused, out-of-repo paths never reach
    a page.
  * `mokata setup claude --ide-bridge` wires OBSERVE-ONLY http hooks (PostToolUse edits, Stop,
    SessionEnd) into the machine-local `.claude/settings.local.json` — merge-safe, idempotent,
    reversible; gate hooks stay command hooks; nothing on SessionStart (the docs allow only
    command/mcp_tool there); no `onFailure`.
  * HOOK-BUDGET: the http observe path meets doc 109's 50 ms p95 target, also when the IDE is closed;
    the `mokata-hook` command hooks are held to doc 109's targets by tests/test_hb_hook_budget.py.
    (Linux here; the 3-OS run is CI's.)

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import os
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import _support  # noqa: F401  (puts src/ on the path)

from mokata import harness_setup as HS
from mokata import perf
from mokata.ide import bus as B
from mokata.init import init_repo
from mokata.quiet_spawn import hidden

SRC = str(Path(__file__).resolve().parents[1] / "src")


def _silent(*_a, **_k):
    pass


def _repo(d: str) -> None:
    init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
    os.makedirs(os.path.join(d, "src"), exist_ok=True)
    with open(os.path.join(d, "src", "a.py"), "w", encoding="utf-8") as fh:
        fh.write("x = 1\n")


def _cc_post(d: str, rel: str = "src/a.py", tool: str = "Write", sid: str = "S1") -> dict:
    """A Claude Code PostToolUse payload, in the shape the hooks reference documents
    (`session_id`, `cwd`, `hook_event_name`, `tool_name`, `tool_input.file_path`)."""
    return {"session_id": sid, "cwd": d, "hook_event_name": "PostToolUse", "tool_name": tool,
            "transcript_path": "/dev/null", "permission_mode": "default",
            "tool_input": {"file_path": os.path.join(d, rel), "content": "x = 2\n" * 300},
            "tool_response": {"success": True}}


def _p95(xs):
    xs = sorted(xs)
    return xs[max(0, int(round(0.95 * len(xs))) - 1)]


def _post(port: int, body: dict, headers: dict, timeout: float = 2.0):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/bus", method="POST",
                                 data=json.dumps(body).encode("utf-8"),
                                 headers={"Host": f"127.0.0.1:{port}",
                                          "Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:   # noqa: S310 — loopback
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class _SSE:
    """A live `/api/events` reader: every event, stamped with perf_counter on arrival."""

    def __init__(self, port: int, token: str):
        self.q: "queue.Queue" = queue.Queue()
        self.conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        self.conn.request("GET", "/api/events", headers={
            "Host": f"127.0.0.1:{port}", "X-Mokata-Token": token, "Accept": "text/event-stream"})
        self.resp = self.conn.getresponse()
        assert self.resp.status == 200, self.resp.status
        self.t = threading.Thread(target=self._read, daemon=True)
        self.t.start()

    def _read(self):
        ev, data = "message", []
        try:
            while True:
                line = self.resp.fp.readline()
                if not line:
                    return
                line = line.decode("utf-8").rstrip("\r\n")
                if not line:
                    if data:
                        self.q.put((time.perf_counter(), ev, "\n".join(data)))
                    ev, data = "message", []
                elif line.startswith("event:"):
                    ev = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].lstrip())
        except (OSError, ValueError, AttributeError):
            return

    def wait(self, pred, timeout: float):
        end = time.perf_counter() + timeout
        while True:
            left = end - time.perf_counter()
            if left <= 0:
                return None
            try:
                t, ev, data = self.q.get(timeout=left)
            except queue.Empty:
                return None
            obj = None
            if ev == "bus":
                try:
                    obj = json.loads(data)
                except ValueError:
                    obj = None
            if pred(ev, obj):
                return t, obj

    def close(self):
        try:
            self.conn.sock.shutdown(socket.SHUT_RDWR)
        except (OSError, AttributeError):
            pass
        self.conn.close()


def _start(d: str):
    from mokata.ide import launch as L
    L.open_or_focus(d, None, browser=False, idle_secs=60)
    return L.running(d)


def _stop(d: str):
    from mokata.ide import launch as L
    rec = L.running(d)
    if rec:
        os.kill(int(rec["pid"]), 15)
        for _ in range(40):
            if not L.running(d):
                break
            time.sleep(0.1)


# ===================================================================== the adapter + the vocabulary

class TheAdapterMapsClaudeCodeOntoTheNeutralVocabulary(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.d = os.path.realpath(self._t.name)
        _repo(self.d)

    def tearDown(self):
        self._t.cleanup()

    def test_an_in_repo_write_is_an_edit_with_a_repo_relative_path(self):
        for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            e = B.normalise(_cc_post(self.d, tool=tool), self.d)
            self.assertEqual({"kind": "edit", "path": "src/a.py", "session": "S1", "tool": tool,
                              "source": "claude-code"}, e)

    def test_turn_end_and_session_end_map_and_gates_never_do(self):
        self.assertEqual("turn_end", B.normalise({"hook_event_name": "Stop", "session_id": "S1"},
                                                 self.d)["kind"])
        self.assertEqual("session_end", B.normalise(
            {"hook_event_name": "SessionEnd", "session_id": "S1"}, self.d)["kind"])
        pre = dict(_cc_post(self.d), hook_event_name="PreToolUse")
        self.assertIsNone(B.normalise(pre, self.d), "a gate event must never become a bus event")
        self.assertIsNone(B.normalise(dict(_cc_post(self.d), tool_name="Bash"), self.d))

    def test_nothing_outside_the_repo_or_in_mokatas_own_record_reaches_a_page(self):
        with tempfile.TemporaryDirectory() as out:
            self.assertIsNone(B.normalise(dict(_cc_post(self.d), tool_input={
                "file_path": os.path.join(out, "x.py")}), self.d))
            os.symlink(out, os.path.join(self.d, "link_out"))
            self.assertIsNone(B.normalise(_cc_post(self.d, "link_out/x.py"), self.d))
        self.assertIsNone(B.normalise(_cc_post(self.d, ".mokata/manifest.json"), self.d))
        self.assertIsNone(B.normalise(dict(_cc_post(self.d), tool_input={
            "file_path": "../../etc/passwd"}), self.d))

    def test_the_neutral_shape_is_closed(self):
        self.assertEqual("src/a.py", B.normalise({"kind": "edit", "path": "src/a.py",
                                                  "session": "X"}, self.d)["path"])
        self.assertIsNone(B.normalise({"kind": "approve", "path": "src/a.py"}, self.d))
        self.assertIsNone(B.normalise({"kind": "edit"}, self.d))
        self.assertIsNone(B.normalise(["edit"], self.d))
        self.assertEqual("turn_end", B.normalise({"kind": "turn_end"}, self.d)["kind"])


class TheBusIsBoundedAndDeduplicates(unittest.TestCase):
    def test_one_edit_seen_by_the_http_hook_and_the_timeline_is_one_event(self):
        async def go():
            b = B.EventBus()
            a = await b.publish({"kind": "edit", "path": "src/a.py", "session": "S1"})
            dup = await b.publish({"kind": "edit", "path": "src/a.py", "session": "S1",
                                   "source": "timeline"})
            other = await b.publish({"kind": "edit", "path": "src/b.py", "session": "S1"})
            return a, dup, other
        a, dup, other = asyncio.run(go())
        self.assertIsNotNone(a)
        self.assertIsNone(dup)
        self.assertIsNotNone(other)

    def test_a_subscriber_that_fell_off_the_ring_is_told_to_resync_not_queued_for(self):
        async def go():
            b = B.EventBus(cap=4)
            for i in range(10):
                await b.publish({"kind": "turn_end", "session": str(i)})
            return b.after(0), b.after(8)
        (old, missed), (new, missed2) = asyncio.run(go())
        self.assertTrue(missed)
        self.assertEqual(4, len(old))
        self.assertFalse(missed2)
        self.assertEqual([9, 10], [e["id"] for e in new])

    def test_a_wait_wakes_on_publish_not_on_a_poll_tick(self):
        async def go():
            b = B.EventBus()
            t0 = time.perf_counter()
            waiter = asyncio.ensure_future(b.wait_after(0, 5.0))
            await asyncio.sleep(0.05)
            await b.publish({"kind": "turn_end", "session": "S"})
            evts, _ = await waiter
            return evts, time.perf_counter() - t0
        evts, took = asyncio.run(go())
        self.assertEqual(1, len(evts))
        self.assertLess(took, 0.5)


# ===================================================================== the door (in-process app)

class TheHookDoorOpensNothingElse(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.d = os.path.realpath(self._t.name)
        _repo(self.d)
        self.rec = B.ensure_bridge(self.d)
        from starlette.testclient import TestClient
        from mokata.ide.server import AppState, build_app
        self.state = AppState(self.d, "pagetok", 9999, None, False)
        self.c = TestClient(build_app(self.state), base_url="http://127.0.0.1:9999")
        self.hook = {B.HOOK_HEADER: self.rec["token"]}

    def tearDown(self):
        self.c.close()
        self._t.cleanup()

    def test_a_hook_post_is_accepted_with_an_empty_2xx_and_published(self):
        r = self.c.post("/api/bus", json=_cc_post(self.d), headers=self.hook)
        self.assertEqual(204, r.status_code)
        self.assertEqual(b"", r.content, "an observe-only hook must return no decision body")
        evts, _ = self.state.bus.after(0)
        self.assertEqual(["src/a.py"], [e["path"] for e in evts])

    def test_the_hook_token_cannot_read_the_repo(self):
        for path in ("/api/state", "/api/file?path=src/a.py", "/api/tree", "/api/events"):
            self.assertEqual(401, self.c.get(path, headers=self.hook).status_code, path)

    def test_a_browser_a_wrong_token_or_a_foreign_host_is_refused(self):
        self.assertEqual(403, self.c.post("/api/bus", json=_cc_post(self.d), headers={
            **self.hook, "Origin": "http://evil.example"}).status_code)
        self.assertEqual(403, self.c.post("/api/bus", json=_cc_post(self.d), headers={
            B.HOOK_HEADER: "wrong"}).status_code)
        from starlette.testclient import TestClient
        from mokata.ide.server import build_app
        evil = TestClient(build_app(self.state), base_url="http://evil.example:9999")
        self.assertEqual(403, evil.post("/api/bus", json=_cc_post(self.d),
                                        headers=self.hook).status_code)
        self.assertEqual(0, self.state.bus.seq, "a refused post published something")

    def test_the_neutral_twin_posts_with_the_launcher_token(self):
        r = self.c.post("/api/bus", json={"kind": "edit", "path": "src/a.py", "session": "T"},
                        headers={"X-Mokata-Token": "pagetok"})
        self.assertEqual(204, r.status_code)
        self.assertEqual("neutral", self.state.bus.after(0)[0][0]["source"])

    def test_an_oversized_body_is_dropped_never_buffered_whole(self):
        old = B.MAX_BODY
        B.MAX_BODY = 1024
        try:
            r = self.c.post("/api/bus", json=_cc_post(self.d), headers=self.hook)
        finally:
            B.MAX_BODY = old
        self.assertEqual(204, r.status_code)
        self.assertEqual(0, self.state.bus.seq)


# ===================================================================== end to end, measured

class LiveFollowWithinOneSecond(unittest.TestCase):
    """A real `mokata ide` server (child process), a real SSE stream, real posts and hooks."""

    @classmethod
    def setUpClass(cls):
        cls._t = tempfile.TemporaryDirectory()
        cls.d = os.path.realpath(cls._t.name)
        _repo(cls.d)
        cls.bridge = B.ensure_bridge(cls.d)
        cls.rec = _start(cls.d)
        cls.port = int(cls.rec["port"])

    @classmethod
    def tearDownClass(cls):
        _stop(cls.d)
        cls._t.cleanup()

    def setUp(self):
        self.sse = _SSE(self.port, self.rec["token"])
        self.assertIsNotNone(self.sse.wait(lambda ev, _o: ev == "focus", 5.0), "stream not up")
        time.sleep(0.8)            # let the shared watcher prime its tails at "now"

    def tearDown(self):
        self.sse.close()

    def test_the_server_listens_on_the_stable_bridge_port(self):
        self.assertEqual(self.bridge["port"], self.port)

    def test_a_claude_code_edit_reaches_the_page_within_one_second(self):
        lat = []
        for i in range(20):
            rel = f"src/f{i}.py"
            t0 = time.perf_counter()
            status, _ = _post(self.port, _cc_post(self.d, rel), {B.HOOK_HEADER: self.bridge["token"]})
            self.assertEqual(204, status)
            got = self.sse.wait(lambda ev, o, rel=rel: ev == "bus" and o and o.get("path") == rel,
                                2.0)
            self.assertIsNotNone(got, f"edit {rel} never reached the stream")
            lat.append((got[0] - t0) * 1000)
        print(f"\n[ID.S3] http hook -> SSE latency ms: p50={sorted(lat)[len(lat) // 2]:.1f} "
              f"p95={_p95(lat):.1f} max={max(lat):.1f} (n={len(lat)})", file=sys.stderr)
        self.assertLess(max(lat), 1000.0, lat)

    def test_the_command_hook_fallback_reaches_the_page_within_one_second(self):
        lat = []
        env = dict(os.environ, PYTHONPATH=SRC)
        for i in range(5):
            rel = f"src/g{i}.py"
            with open(os.path.join(self.d, rel), "w", encoding="utf-8") as fh:
                fh.write(f"g = {i}\n")
            subprocess.run([sys.executable, "-m", "mokata.hook_cli", "dirty-track"],
                           input=json.dumps(_cc_post(self.d, rel)).encode("utf-8"), cwd=self.d,
                           env=env, capture_output=True, timeout=30, **hidden())
            t0 = time.perf_counter()        # the hook has written its timeline line
            got = self.sse.wait(lambda ev, o, rel=rel: ev == "bus" and o and o.get("path") == rel,
                                3.0)
            self.assertIsNotNone(got, f"the dirty-track timeline never surfaced {rel}")
            self.assertEqual("timeline", got[1]["source"])
            lat.append((got[0] - t0) * 1000)
        print(f"\n[ID.S3] dirty-track timeline -> SSE latency ms: max={max(lat):.1f} (n=5)",
              file=sys.stderr)
        self.assertLess(max(lat), 1000.0, lat)

    def test_an_event_written_to_the_typed_store_reaches_the_page_within_one_second(self):
        from mokata.events import ToolCall, store_for_root
        t0 = time.perf_counter()
        self.assertIsNotNone(store_for_root(self.d).emit(ToolCall(tool="probe_tool"),
                                                         session_id="S9"))
        got = self.sse.wait(lambda ev, o: ev == "bus" and o and o.get("kind") == "event"
                            and o.get("data", {}).get("tool") == "probe_tool", 2.0)
        self.assertIsNotNone(got, "a store event never reached the stream")
        self.assertEqual("S9", got[1]["session"])
        self.assertLess(got[0] - t0, 1.0)


# ===================================================================== setup wiring

class SetupWiresObserveOnlyHttpHooks(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        base = Path(self._t.name)
        self.root = base / "repo"
        self.root.mkdir()
        self.home = base / "home"
        self.home.mkdir()

    def tearDown(self):
        self._t.cleanup()

    def _setup(self, **kw):
        plan = HS.plan_setup("claude", root=str(self.root), home=str(self.home), **kw)
        HS.apply_setup(plan, assume_yes=True, out=_silent)
        return plan

    def _local(self):
        p = self.root / ".claude" / "settings.local.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    @unittest.skipIf(os.name == "nt", "Windows wires the command form (0.0.26 O4) — "
                                      "tests/test_o4_bridge_live_check.py")
    def test_the_flag_writes_documented_http_hooks_to_the_machine_local_file(self):
        self._setup(ide_bridge=True)
        rec = B.read_bridge(str(self.root.resolve()))
        hooks = self._local()["hooks"]
        self.assertEqual({"PostToolUse", "Stop", "SessionEnd"}, set(hooks))
        self.assertNotIn("SessionStart", hooks, "SessionStart supports only command/mcp_tool hooks")
        self.assertEqual(HS.HOOK_DIRTY_MATCHER, hooks["PostToolUse"][0]["matcher"])
        for event, groups in hooks.items():
            (h,) = groups[0]["hooks"]
            self.assertEqual({"type", "url", "timeout", "headers"}, set(h), event)
            self.assertEqual("http", h["type"])
            self.assertEqual(f"http://127.0.0.1:{rec['port']}/api/bus", h["url"])
            self.assertEqual(1, h["timeout"])
            self.assertEqual({"X-Mokata-Hook": rec["token"]}, h["headers"])
            self.assertNotIn("onFailure", h, "observe-only: a failure must never block (U8)")
        shared = json.loads((self.root / ".claude" / "settings.json").read_text(encoding="utf-8"))
        flat = json.dumps(shared)
        self.assertNotIn(rec["token"], flat, "the hook token leaked into the shared settings")
        for groups in shared["hooks"].values():
            for g in groups:
                for h in g["hooks"]:
                    self.assertEqual("command", h["type"], "gate/command hooks must not move")

    def test_off_by_default_idempotent_merge_safe_and_reversible(self):
        self._setup()
        self.assertIsNone(self._local(), "the bridge is opt-in")
        local = self.root / ".claude" / "settings.local.json"
        mine = {"type": "command", "command": "echo mine"}
        local.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [mine]}]},
                                     "env": {"K": "v"}}), encoding="utf-8")
        self._setup(ide_bridge=True)
        self._setup(ide_bridge=True)
        data = self._local()
        self.assertEqual(2, len(data["hooks"]["Stop"]), "re-setup duplicated the http hook")
        self.assertIn({"hooks": [mine]}, data["hooks"]["Stop"])
        self.assertEqual({"K": "v"}, data["env"])
        HS.apply_unsetup(HS.plan_unsetup("claude", root=str(self.root), home=str(self.home)))
        self.assertEqual({"hooks": {"Stop": [{"hooks": [mine]}]}, "env": {"K": "v"}}, self._local())

    def test_unsetup_removes_the_file_mokata_created(self):
        self._setup(ide_bridge=True)
        HS.apply_unsetup(HS.plan_unsetup("claude", root=str(self.root), home=str(self.home)))
        self.assertIsNone(self._local())

    def test_user_scope_says_why_it_is_not_wired(self):
        plan = HS.plan_setup("claude", root=str(self.root), home=str(self.home), scope="user",
                             ide_bridge=True)
        self.assertFalse(plan.ide_bridge)
        self.assertIn("IDE live bridge not wired", HS.render_setup_plan(plan))

    def test_the_plan_discloses_the_file_and_an_unignored_token(self):
        # The developer's own global gitignore (core.excludesFile, ~/.config/git/ignore) often ignores
        # `.claude/settings.local.json` — that made this red on a maintainer's Mac. Grade the repo alone.
        from unittest import mock
        env = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
               "HOME": str(self.home), "USERPROFILE": str(self.home),
               "XDG_CONFIG_HOME": str(self.home / ".config")}
        with mock.patch.dict(os.environ, env):
            subprocess.run(["git", "init", "-q", str(self.root)], check=True, capture_output=True,
                           stdin=subprocess.DEVNULL)
            text = HS.render_setup_plan(HS.plan_setup("claude", root=str(self.root),
                                                      home=str(self.home), ide_bridge=True))
        self.assertIn("settings.local.json", text)
        self.assertIn("NOT git-ignored", text)

    def test_the_bridge_record_is_stable_and_private(self):
        a = B.ensure_bridge(str(self.root))
        self.assertEqual(a, B.ensure_bridge(str(self.root)))
        self.assertTrue(20000 <= a["port"] < 30000)
        if os.name != "nt":
            self.assertEqual(0o600, os.stat(B.bridge_path(str(self.root))).st_mode & 0o777)

    def test_the_cli_flag_exists(self):
        from mokata.cli import build_parser
        a = build_parser().parse_args(["setup", "claude", "--ide-bridge", "--yes"])
        self.assertTrue(a.ide_bridge)


#: Windows' refused-connect retry (see HookBudget.test_with_the_ide_closed_the_http_hook_fails_open_fast).
WINDOWS_REFUSED_CONNECT_CEILING_MS = 1500.0


class ThePageFollows(unittest.TestCase):
    def test_the_page_listens_for_bus_edits_and_has_a_follow_toggle(self):
        from mokata.ide.server import STATIC_DIR
        js = Path(STATIC_DIR, "app.js").read_text(encoding="utf-8")
        html = Path(STATIC_DIR, "index.html").read_text(encoding="utf-8")
        self.assertIn('addEventListener("bus"', js)
        self.assertIn('e.kind !== "edit"', js)
        self.assertIn("reload: true", js)
        self.assertIn('id="follow"', html)


# ===================================================================== HOOK-BUDGET

class HookBudget(unittest.TestCase):
    """p95 per hook invocation, as the harness experiences it. Linux in this sandbox; macOS and
    Windows are measured by the same test on the CI matrix."""

    N_HTTP = 40

    @classmethod
    def setUpClass(cls):
        cls._t = tempfile.TemporaryDirectory()
        cls.d = os.path.realpath(cls._t.name)
        _repo(cls.d)
        cls.bridge = B.ensure_bridge(cls.d)
        cls.relax = perf.relax_factor()
        cls.results = {}

    @classmethod
    def tearDownClass(cls):
        _stop(cls.d)
        if cls.results:
            print("\n[HOOK-BUDGET] p95 ms (Linux): " + ", ".join(
                f"{k}={v:.1f}" for k, v in sorted(cls.results.items())), file=sys.stderr)
        cls._t.cleanup()

    def _budget(self, key):
        return perf.HOOK_BUDGET_P95_MS[key] * self.relax

    def test_the_http_observe_path_meets_the_50ms_target(self):
        rec = _start(self.d)
        port = int(rec["port"])
        body = _cc_post(self.d)
        for _ in range(3):
            _post(port, body, {B.HOOK_HEADER: self.bridge["token"]})
        xs = []
        for i in range(self.N_HTTP):
            t0 = time.perf_counter()
            status, _ = _post(port, _cc_post(self.d, f"src/h{i}.py"),
                              {B.HOOK_HEADER: self.bridge["token"]})
            xs.append((time.perf_counter() - t0) * 1000)
            self.assertEqual(204, status)
        self.results["observe_http"] = _p95(xs)
        self.assertLessEqual(_p95(xs), self._budget("observe_http"), xs)

    def test_with_the_ide_closed_the_http_hook_fails_open_fast(self):
        port = B._pick_port(self.d + "-closed")     # nothing listens there
        xs = []
        for _ in range(self.N_HTTP):
            t0 = time.perf_counter()
            try:
                _post(port, _cc_post(self.d), {B.HOOK_HEADER: "x"}, timeout=1.0)
            except (urllib.error.URLError, OSError):
                pass
            xs.append((time.perf_counter() - t0) * 1000)
        self.results["observe_http_closed"] = _p95(xs)
        if os.name == "nt":
            # Windows retries a refused localhost connect before giving up: ~1 s per hook, measured
            # on windows-latest (0.0.25 cut, p95 1011 ms). That is why, since 0.0.26 O4, `--ide-bridge`
            # does NOT wire the http hook on Windows: it wires `bridge_client`, which never connects
            # to a closed IDE (tests/test_o4_bridge_live_check.py). This pins the raw OS cost only.
            self.assertLessEqual(_p95(xs), WINDOWS_REFUSED_CONNECT_CEILING_MS, xs)
            return
        self.assertLessEqual(_p95(xs), self._budget("observe_http_closed"), xs)

    # The `mokata-hook` COMMAND hooks' budget moved to tests/test_hb_hook_budget.py (0.0.25 Lane HB):
    # every wired hook, three fixtures, held to doc 109's TARGETS (named gaps aside) instead of the
    # regression ceilings that lived here.

    def test_the_targets_are_doc_109s(self):
        self.assertEqual({"observe": 50.0, "gate": 150.0},
                         {k: perf.HOOK_BUDGET_TARGETS_MS[k] for k in ("observe", "gate")})
        self.assertEqual(perf.HOOK_BUDGET_TARGETS_MS["observe"],
                         perf.HOOK_BUDGET_P95_MS["observe_http"])


if __name__ == "__main__":
    unittest.main()
