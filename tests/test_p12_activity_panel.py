"""0.0.27 P12 `ID-ACTIVITY-PANEL` — what the agent is doing right now, and what it did, for every
session, in one panel (doc 113 §5b Lane P; doc 109 §4.1 / §14 / §18.4; PRD §5.3).

Business rules — what must be true for the user, each:
  * An event the agent causes APPEARS in the panel without the page asking: a tool call the observe
    hook reports is pushed on the event stream and the session's card says what it last ran; an
    edit still drives follow mode; a gate verdict written to the typed store reaches the page too.
  * A needs-you is SURFACED: a pending approval (by id, tool and the command that resolves it —
    never its content) and an ended turn both reach the panel's queue.
  * An ENDED session's history is complete: every gate, phase, test verdict and write it produced
    is on its timeline, newest first, paged by a cursor — and the pages, walked to the end, are the
    whole record with no duplicate and no gap.
  * A RESUMED session shows its predecessor's history (the O10 chain), with the resume marked.
  * Cancel on an IDE job the panel lists STOPS it.
  * With the IDE closed nothing is lost: a history written while no server ran is reconstructed
    from the ledger, the typed store, the snapshots and the claims when one opens.
  * Scale: tens of thousands of events are PAGED, never capped — every page bounded, the cursor
    reaching every row.
  * Every item has ONE typed, extensible shape; P46's `explainer` is a registered type.
  * Content stays out: a Bash command, its output, a proposal's preview never reach the page.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import os
import queue
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import approval
from mokata import session_index as SI
from mokata.events import emit, store_for_root
from mokata.events.schema import GateDecision, PhaseTransition, TokenSpend
from mokata.ide import activity as A
from mokata.ide import bus as B
from mokata.ide import snapshots as S
from mokata.init import init_repo
from mokata.knowledge import cancel as C

TOK = "tok"
PLANTED_CMD = "export PLANTED_SECRET=hunter2 && make test"
PLANTED_OUT = "PLANTED-STDOUT-sk-live-0000"
SECRET_PREVIEW = "PLANTED-PREVIEW-sk-live-1234567890"
SECRET_SUMMARY = "PLANTED-SUMMARY-hunter2"


def _silent(*_a, **_k):
    pass


def _write(d, rel, text):
    p = os.path.join(d, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _repo(d):
    subprocess.run(["git", "-C", d, "init", "-q", "-b", "main"], check=True,
                   stdin=subprocess.DEVNULL, capture_output=True)
    init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
    _write(d, "src/a.py", "a = 1\n")


def _edit(d, rel, text, sid, run):
    """What the hooks do around one native write: a BEFORE on the session's first touch, the
    write, then an AFTER."""
    S.capture_before_first_write(d, os.path.join(d, rel), sid, run)
    _write(d, rel, text)
    time.sleep(0.002)
    return S.capture(d, os.path.join(d, rel), S.AFTER, sid, run)


def _gate(d, sid, run, gate="write_gate", decision="allowed", subject="src/a.py", ledger_seq=None):
    return emit(d, GateDecision(gate=gate, decision=decision, subject=subject), session_id=sid,
                run_id=run, ledger_seq=ledger_seq)


def _cc_post(d, tool="Bash", sid="S1", rel="src/a.py"):
    """A Claude Code PostToolUse payload (hooks reference shape)."""
    body = {"session_id": sid, "cwd": d, "hook_event_name": "PostToolUse", "tool_name": tool,
            "transcript_path": "/dev/null", "permission_mode": "default",
            "tool_input": {"command": PLANTED_CMD} if tool == "Bash"
            else {"file_path": os.path.join(d, rel), "content": "x = 2\n"},
            "tool_response": {"stdout": PLANTED_OUT, "stderr": "", "interrupted": False}}
    return body


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Live:
    """The real app on a real socket (uvicorn in a thread): a real SSE stream and real hook posts."""

    def __init__(self, root):
        import uvicorn
        from mokata.ide.server import AppState, build_app
        self.root = root
        self.bridge = B.ensure_bridge(root)
        self.port = _free_port()
        self.state = AppState(root, TOK, self.port, None, False)
        cfg = uvicorn.Config(build_app(self.state), host="127.0.0.1", port=self.port,
                             log_level="warning", lifespan="off", timeout_graceful_shutdown=1)
        self.server = uvicorn.Server(cfg)
        self.thread = threading.Thread(target=lambda: asyncio.run(self.server.serve()), daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"
        self.headers = {"x-mokata-token": TOK, "Host": f"127.0.0.1:{self.port}"}
        for _ in range(200):
            try:
                if self.get("/api/ping").status_code == 200:
                    return
            except Exception:  # noqa: BLE001 — not up yet
                time.sleep(0.05)
        raise AssertionError("server did not start")

    def get(self, path):
        import httpx
        return httpx.get(self.base + path, headers=self.headers, timeout=10)

    def post(self, path, body):
        import httpx
        return httpx.post(self.base + path, json=body,
                          headers=dict(self.headers, Origin=self.base), timeout=10)

    def hook(self, body):
        """What a Claude Code http hook sends: the hook token, no Origin."""
        import httpx
        return httpx.post(self.base + "/api/bus", content=json.dumps(body).encode("utf-8"),
                          headers={"Host": f"127.0.0.1:{self.port}",
                                   "Content-Type": "application/json",
                                   "X-Mokata-Hook": self.bridge["token"]}, timeout=10)

    def stop(self):
        self.state.close()
        self.server.should_exit = True
        self.thread.join(5)


class _SSE:
    """A live `/api/events` reader."""

    def __init__(self, port, token):
        self.q = queue.Queue()
        self.conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        self.conn.request("GET", "/api/events?client=t", headers={
            "Host": f"127.0.0.1:{port}", "X-Mokata-Token": token, "Accept": "text/event-stream"})
        self.resp = self.conn.getresponse()
        assert self.resp.status == 200, self.resp.status
        threading.Thread(target=self._read, daemon=True).start()

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
                        self.q.put((ev, "\n".join(data)))
                    ev, data = "message", []
                elif line.startswith("event:"):
                    ev = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].lstrip())
        except (OSError, ValueError, AttributeError):
            return

    def wait(self, pred, timeout):
        end = time.monotonic() + timeout
        while True:
            left = end - time.monotonic()
            if left <= 0:
                return None
            try:
                ev, data = self.q.get(timeout=left)
            except queue.Empty:
                return None
            obj = None
            if ev in ("bus", "job"):
                try:
                    obj = json.loads(data)
                except ValueError:
                    obj = None
            if pred(ev, obj):
                return ev, obj

    def close(self):
        try:
            self.conn.sock.shutdown(socket.SHUT_RDWR)
        except (OSError, AttributeError):
            pass
        self.conn.close()


class _Repo(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.d = os.path.realpath(self._t.name)
        _repo(self.d)

    def tearDown(self):
        self._t.cleanup()

    def row(self, data, sid):
        return next(r for r in data["sessions"] if r["id"] == sid or sid in r.get("chain", []))


# ===================================================================== an event appears in the panel

class AnEventAppearsInThePanel(_Repo):
    """The live rule: the page is PUSHED what the agent does; it never polls."""

    def setUp(self):
        super().setUp()
        _edit(self.d, "src/a.py", "a = 2\n", "S1", "R1")
        SI.on_start(self.d, "S1", "startup", self.d, t=time.time(), proc=None)
        self.srv = _Live(self.d)
        self.addCleanup(self.srv.stop)
        self.sse = _SSE(self.srv.port, TOK)
        self.addCleanup(self.sse.close)
        self.assertIsNotNone(self.sse.wait(lambda ev, _o: ev == "focus", 5.0), "stream not up")
        time.sleep(0.8)                      # the shared watcher primes its tails at "now"

    def test_a_tool_call_the_hook_reports_is_pushed_and_becomes_the_sessions_last_tool(self):
        """A Bash call: the stream carries a `tool` event (the tool's NAME, never the command or
        its output) and the panel's card for S1 says it is working, last tool Bash."""
        self.assertEqual(204, self.srv.hook(_cc_post(self.d, "Bash")).status_code)
        got = self.sse.wait(lambda ev, o: ev == "bus" and o and o.get("kind") == "tool", 3.0)
        self.assertIsNotNone(got, "the tool call never reached the page")
        self.assertEqual(("Bash", "S1"), (got[1]["tool"], got[1]["session"]))
        blob = json.dumps(got[1])
        self.assertNotIn("PLANTED", blob, "a command or its output leaked onto the bus")
        panel = self.srv.get("/api/activity").json()
        s1 = self.row(panel, "S1")
        self.assertEqual(("working", "Bash"), (s1["activity"]["state"], s1["activity"]["last_tool"]))
        self.assertIn("bus", s1["activity"]["basis"], "the state says where it was derived from")
        self.assertTrue(panel["bus"])
        self.assertNotIn("PLANTED", json.dumps(panel))
        # the item the page prepends for it has the history's shape
        item = A.item_from_bus(got[1])
        self.assertEqual(("tool", "Bash"), (item["type"], item["detail"]["tool"]))
        for k in ("id", "t", "type", "title", "verdict", "session", "run", "source", "ledger_seq",
                  "detail"):
            self.assertIn(k, item)

    def test_a_tool_call_is_durable_history_after_a_reload_equals_the_live_view(self):
        """Ruling: the panel promises completeness, so a hook-reported tool call is STORED (name
        only) and is on the timeline after a reload / with the IDE closed — once, with the same id
        the live push carried, and the tail does not push it a second time."""
        for tool in ("Read", "Grep", "Task", "Bash"):
            self.assertEqual(204, self.srv.hook(_cc_post(self.d, tool)).status_code)
        pushed = []
        for _ in range(4):
            got = self.sse.wait(lambda ev, o: ev == "bus" and o and o.get("kind") == "tool", 3.0)
            self.assertIsNotNone(got)
            pushed.append(got[1])
        self.assertEqual(["Read", "Grep", "Task", "Bash"], [p["tool"] for p in pushed])
        self.assertTrue(all(isinstance(p.get("seq"), int) for p in pushed), "stored, with its seq")
        # the tail must NOT re-push the rows this server stored itself
        dup = self.sse.wait(lambda ev, o: ev == "bus" and o and o.get("kind") == "event"
                            and o.get("type") == "tool_call", 1.5)
        self.assertIsNone(dup, "the stored tool row was pushed twice")
        h = self.srv.get("/api/activity/history?session=S1&types=tool").json()
        self.assertEqual([f"ev:{p['seq']}" for p in reversed(pushed)], [i["id"] for i in h["items"]])
        self.assertEqual(["Bash", "Task", "Grep", "Read"], [i["detail"]["tool"] for i in h["items"]])
        self.assertEqual([A.item_from_bus(p)["id"] for p in pushed],
                         [f"ev:{p['seq']}" for p in pushed], "the live item IS the history item")
        blob = json.dumps(h)
        self.assertNotIn("PLANTED", blob)
        # with no server at all the rows are still there
        from mokata.events import query
        self.assertEqual(4, len([e for e in query(self.d, session_id="S1", types=["tool_call"])
                                 if e.data.get("surface") == "claude-code"]))

    def test_the_panel_says_plainly_when_the_bridge_is_off(self):
        """U7/U8: the observe http hooks are opt-in; without them live tool calls cannot arrive
        and the panel says so, naming the command. With them wired, it says nothing."""
        panel = self.srv.get("/api/activity").json()
        self.assertFalse(panel["bridge"]["wired"])
        self.assertIn("--ide-bridge", panel["bridge"]["note"])
        from mokata import harness_setup as HS
        local = os.path.join(self.d, ".claude", HS.LOCAL_SETTINGS)
        os.makedirs(os.path.dirname(local), exist_ok=True)
        with open(local, "w", encoding="utf-8") as fh:
            json.dump({"hooks": HS.ide_bridge_hooks("http://127.0.0.1:1/api/bus", "t", self.d,
                                                    windows=False)}, fh)
        panel = self.srv.get("/api/activity").json()
        self.assertEqual((True, ""), (panel["bridge"]["wired"], panel["bridge"]["note"]))
        self.assertNotIn("matcher", HS.ide_bridge_hooks("u", "t", self.d, windows=False)["PostToolUse"][0],
                         "every tool reaches the bus, not only the file-mutating ones")

    def test_an_unreadable_settings_file_is_said_never_read_as_bridge_off(self):
        """A `.claude/settings.local.json` mokata cannot parse is NOT "the bridge is off — run
        setup": the bridge may well be wired in it, and setup would refuse to touch it anyway. The
        panel says the wiring is unknown and names the file, never the run-setup advice (§7g)."""
        from mokata import harness_setup as HS
        local = os.path.join(self.d, ".claude", HS.LOCAL_SETTINGS)
        os.makedirs(os.path.dirname(local), exist_ok=True)
        with open(local, "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        bridge = self.srv.get("/api/activity").json()["bridge"]
        self.assertFalse(bridge["wired"])
        self.assertNotEqual(A.BRIDGE_OFF, bridge["note"])
        self.assertIn("unknown", bridge["note"])
        self.assertIn(HS.LOCAL_SETTINGS, bridge["note"])

    def test_a_settings_file_that_is_not_utf8_is_said_never_a_500(self):
        """A `.claude/settings.local.json` saved in another encoding is the same unknown, not a
        crash: the panel still answers, says the wiring is unknown and names the file."""
        from mokata import harness_setup as HS
        local = os.path.join(self.d, ".claude", HS.LOCAL_SETTINGS)
        os.makedirs(os.path.dirname(local), exist_ok=True)
        with open(local, "wb") as fh:
            fh.write(b'{"hooks": "\xff\xfe"}')
        r = self.srv.get("/api/activity")
        self.assertEqual(200, r.status_code)
        bridge = r.json()["bridge"]
        self.assertFalse(bridge["wired"])
        self.assertIn("unknown", bridge["note"])
        self.assertIn(HS.LOCAL_SETTINGS, bridge["note"])

    def test_an_edit_still_drives_follow_mode_and_updates_the_card(self):
        """A Write is still an `edit` (path + session — follow mode opens it) and the card's last
        file follows."""
        self.assertEqual(204, self.srv.hook(_cc_post(self.d, "Write")).status_code)
        got = self.sse.wait(lambda ev, o: ev == "bus" and o and o.get("kind") == "edit", 3.0)
        self.assertIsNotNone(got)
        self.assertEqual("src/a.py", got[1]["path"])
        s1 = self.row(self.srv.get("/api/activity").json(), "S1")
        self.assertEqual(("working", "src/a.py"), (s1["activity"]["state"],
                                                   s1["activity"]["last_path"]))

    def test_a_stop_turns_the_session_idle_and_is_a_needs_you(self):
        """The agent ended its turn: the card says idle and the panel's queue says it is your
        move (turn-ended) — surfaced, not buried."""
        self.srv.hook(dict(_cc_post(self.d, "Bash"), hook_event_name="Stop"))
        self.assertIsNotNone(self.sse.wait(lambda ev, o: ev == "bus" and o
                                           and o.get("kind") == "turn_end", 3.0))
        panel = self.srv.get("/api/activity").json()
        s1 = self.row(panel, "S1")
        self.assertEqual("idle", s1["activity"]["state"])
        self.assertIn("turn-ended", [q["reason"] for q in panel["needs_you"]])
        self.assertIn("turn-ended", [q["reason"] for q in s1["needs_you"]])
        self.assertGreaterEqual(panel["waiting"], 1)

    def test_a_gate_verdict_written_to_the_store_is_pushed_without_the_page_asking(self):
        """A gate decides (the typed store grows): the shared watcher's tail pushes it as a bus
        `event`, and the panel counts it among S1's gates. The page sent no request for it."""
        _gate(self.d, "S1", "R1", "write_gate", "blocked")
        got = self.sse.wait(lambda ev, o: ev == "bus" and o and o.get("kind") == "event"
                            and o.get("type") == "gate_decision", 4.0)
        self.assertIsNotNone(got, "the store tail did not push the gate verdict")
        self.assertEqual("blocked", got[1]["data"]["decision"])
        item = A.item_from_bus(got[1])
        self.assertEqual(("gate", "blocked"), (item["type"], item["verdict"]))
        s1 = self.row(self.srv.get("/api/activity").json(), "S1")
        self.assertEqual((1, 1), (s1["gates"]["fired"], s1["gates"]["blocked"]))

    def test_the_jobs_the_panel_lists_are_the_registrys_and_their_events_reach_the_stream(self):
        """An IDE job started anywhere is in the panel's `jobs.active` and its progress arrives as
        `job` events on the same stream."""
        started = threading.Event()

        def work():
            started.set()
            end = time.monotonic() + 20
            while time.monotonic() < end:
                C.checkpoint()
                time.sleep(0.01)
            return {}
        job = self.srv.state.jobs.start("graph.view", {"symbol": "p12"}, work, label="graph p12")
        self.assertTrue(started.wait(5))
        self.assertIsNotNone(self.sse.wait(lambda ev, o: ev == "job" and o
                                           and o.get("id") == job.id, 3.0))
        panel = self.srv.get("/api/activity").json()
        self.assertIn(job.id, [j["id"] for j in panel["jobs"]["active"]])
        self.srv.state.jobs.cancel(job.id)
        self.assertTrue(job.stopped.wait(3), "Cancel did not stop the work")


# ===================================================================== a needs-you is surfaced

class ANeedsYouIsSurfaced(_Repo):

    def test_a_pending_approval_is_in_the_panel_by_id_tool_and_command_never_by_content(self):
        """mokata is waiting on a human: the session's card and the queue say so, with the exact
        command that decides it — and the proposal's summary/preview never reach the panel."""
        _edit(self.d, "src/a.py", "a = 2\n", "S1", "R1")
        p = approval.propose(self.d, tool="spec_emit", args={"x": 1}, run_id="R1", target="spec",
                             summary=SECRET_SUMMARY, preview=SECRET_PREVIEW)
        panel = A.live(self.d)
        s1 = self.row(panel, "S1")
        self.assertEqual(["awaiting-approval"], [q["reason"] for q in s1["needs_you"]])
        det = s1["needs_you"][0]["detail"]
        self.assertEqual((p.proposal_id, "spec_emit", f"mokata approve {p.proposal_id}"),
                         (det["proposal_id"], det["tool"], det["command"]))
        self.assertEqual(1, panel["waiting"])
        self.assertEqual(p.proposal_id, panel["needs_you"][0]["detail"]["proposal_id"])
        blob = json.dumps(panel)
        self.assertNotIn(SECRET_PREVIEW, blob)
        self.assertNotIn(SECRET_SUMMARY, blob)
        # a session WAITING is shown even when it has ended: the approval still needs a person
        SI.on_start(self.d, "S1", "startup", self.d, t=time.time() - 60, proc=None)
        SI.on_end(self.d, "S1", "logout")
        later = A.live(self.d)
        self.assertEqual("ended", self.row(later, "S1")["status"])
        self.assertEqual(1, later["waiting"])

    def test_an_approved_proposal_waits_on_the_agent_not_on_you(self):
        _edit(self.d, "src/a.py", "a = 2\n", "S1", "R1")
        p = approval.propose(self.d, tool="spec_emit", args={"x": 1}, run_id="R1")
        self.assertTrue(approval.approve(self.d, p.proposal_id).ok)
        self.assertEqual(0, A.live(self.d)["waiting"])


# ===================================================================== an ended session's history

class AnEndedSessionsHistoryIsComplete(_Repo):

    def _seed(self, n_gates=450):
        _edit(self.d, "src/a.py", "a = 2\n", "S1", "R1")
        emit(self.d, PhaseTransition(phase="develop"), session_id="S1", run_id="R1")
        from mokata import MOKATA_DIR
        from mokata.govern.ledger import AuditLedger
        led = AuditLedger.from_mokata_dir(os.path.join(self.d, MOKATA_DIR))
        red = led.record("tdd", event="red", test="tests/test_a.py::test_x")
        # what the ledger's own projection does for THIS session (the process's session id is not
        # S1 here, so the typed row is emitted as the hook process would): gate tdd, backed by #seq
        _gate(self.d, "S1", "R1", "tdd", "recorded", "", ledger_seq=red["seq"])
        for i in range(n_gates):
            _gate(self.d, "S1", "R1", "write_gate", "allowed" if i % 7 else "blocked", "src/a.py")
        _edit(self.d, "src/b.py", "b = 1\n", "S1", "R1")
        emit(self.d, TokenSpend(label="develop", input_tokens=10, output_tokens=2, estimated=False),
             session_id="S1", run_id="R1")
        _edit(self.d, "src/a.py", "a = 3\n", "S1", "R1")
        SI.on_start(self.d, "S1", "startup", self.d, t=time.time() - 60, proc=None)
        SI.on_end(self.d, "S1", "logout")
        return n_gates

    def _walk(self, sid, limit, **kw):
        """Every page to the end; returns (pages, items, the FIRST page — it carries `total`)."""
        pages, before, items, first = 0, None, [], None
        while True:
            page = A.history(self.d, sid, before=before, limit=limit, **kw)
            first = first or page
            pages += 1
            items += page["items"]
            if pages > 1:
                self.assertIsNone(page["total"], "a count is paid once, on the first page (F1)")
            if page["next"] is None:
                return pages, items, first
            before = page["next"]
            self.assertLess(pages, 10_000, "the cursor never ends")

    def test_every_gate_phase_test_and_write_is_on_the_timeline_once_newest_first(self):
        """Walking the pages to the end gives the WHOLE record — no duplicate, no gap — in time
        order, for a session that has ended."""
        n = self._seed()
        self.assertEqual("ended", self.row(A.live(self.d, focus="S1"), "S1")["status"])
        pages, items, last = self._walk("S1", 100)
        self.assertGreaterEqual(pages, 5, "the record is paged, not answered in one piece")
        ids = [i["id"] for i in items]
        self.assertEqual(len(ids), len(set(ids)), "an item appeared on two pages")
        want_events = n + 3                            # gates + phase + tdd + tokens
        self.assertEqual(want_events, sum(1 for i in items if i["source"] == "events"))
        # 3 writes + 2 first-touch captures (a.py once, b.py once)
        self.assertEqual(5, sum(1 for i in items if i["source"] == "snapshots"))
        self.assertEqual({"events": want_events, "writes": 5, "resumes": 0}, last["total"])
        ts = [i["t"] for i in items]
        self.assertEqual(sorted(ts, reverse=True), ts, "newest first, across pages")
        kinds = {i["type"] for i in items}
        self.assertTrue({"gate", "phase", "test", "tokens", "edit", "before"} <= kinds, kinds)
        blocked = [i for i in items if i["type"] == "gate" and i["verdict"] == "blocked"]
        self.assertEqual(sum(1 for i in range(n) if i % 7 == 0), len(blocked))
        for i in items:
            for k in ("id", "t", "type", "title", "verdict", "session", "run", "source",
                      "ledger_seq", "detail"):
                self.assertIn(k, i, "every item has the one typed shape")

    def test_the_test_record_comes_from_the_ledger_row_the_event_is_backed_by(self):
        """RED/GREEN and the test id live in the audit ledger (the typed store keeps them out on
        purpose); the timeline joins them by `ledger_seq` without reading the whole file."""
        self._seed(n_gates=5)
        _pages, items, _last = self._walk("S1", 50)
        tests = [i for i in items if i["type"] == "test"]
        self.assertEqual(1, len(tests))
        self.assertEqual(("red", "tests/test_a.py::test_x", "ledger (tdd)"),
                         (tests[0]["verdict"], tests[0]["detail"]["test"],
                          tests[0]["detail"]["basis"]))
        self.assertIsNotNone(tests[0]["ledger_seq"], "backed by the hash-chained ledger row")
        self.assertIn("RED", tests[0]["title"])

    def test_a_type_filter_narrows_without_breaking_the_walk(self):
        self._seed(n_gates=30)
        _pages, items, last = self._walk("S1", 10, types=["edit", "test"])
        self.assertEqual({"edit", "test"}, {i["type"] for i in items})
        self.assertEqual(3, sum(1 for i in items if i["type"] == "edit"))
        self.assertEqual(1, sum(1 for i in items if i["type"] == "test"))
        self.assertEqual({"edit", "test"} & set(last["types"]), {"edit", "test"})

    def test_a_bad_cursor_or_a_missing_session_is_refused_not_a_crash(self):
        for bad in ("nope", "5:nan", "5:-inf", "5:inf", "-1:3.0", "5:3.0:y", "a:b:c:d"):
            with self.assertRaises(A.ActivityRefused, msg=bad):
                A.history(self.d, "S1", before=bad)
        with self.assertRaises(A.ActivityRefused):
            A.history(self.d, "")
        empty = A.history(self.d, "never-ran")
        self.assertEqual(([], None), (empty["items"], empty["next"]))

    def test_the_snapshot_side_of_a_page_is_bounded_too_and_the_walk_stays_whole(self):
        """F7: a conversation with 900 writes and 3 events — no page carries more than `limit`
        writes (plus ties at the cut), and the pages together are still the whole record, once.
        Ties: 30 writes share one exact timestamp, so a cut lands on them."""
        import json as _json
        emit(self.d, PhaseTransition(phase="develop"), session_id="S1", run_id="R1")
        _gate(self.d, "S1", "R1")
        _gate(self.d, "S1", "R1")
        path = os.path.join(S.store_dir(self.d), "timeline.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        t0 = time.time() - 5000
        with open(path, "a", encoding="utf-8") as fh:
            for i in range(900):
                t = t0 + (i if i < 870 else 870)            # the last 30 share one timestamp
                fh.write(_json.dumps({"id": f"w{i:04d}", "t": t, "session": "S1", "run": "R1",
                                      "path": "src/a.py", "kind": "after", "sha": "", "state": "ok",
                                      "size": 1}) + "\n")
        pages, items, first = self._walk("S1", 100)
        writes = [i for i in items if i["type"] == "edit"]
        self.assertEqual(900, len(writes))
        self.assertEqual(900, len({i["id"] for i in writes}), "no write twice")
        self.assertEqual(3, sum(1 for i in items if i["source"] == "events"))
        self.assertGreaterEqual(pages, 9, "paged, not one answer")
        self.assertEqual({"events": 3, "writes": 900, "resumes": 0}, first["total"])
        # each page: at most limit writes + the ties at its cut
        before, biggest = None, 0
        while True:
            pg = A.history(self.d, "S1", before=before, limit=100)
            biggest = max(biggest, sum(1 for i in pg["items"] if i["type"] == "edit"))
            if pg["next"] is None:
                break
            before = pg["next"]
        self.assertLessEqual(biggest, 100 + 30)
        _p, only, _f = self._walk("S1", 50, types=["edit"])
        self.assertEqual(900, len(only), "a type-filtered walk is paged and complete too")

    def test_two_resumes_in_one_millisecond_are_two_items(self):
        """F5: an id collision would make the page drop one marker — data loss."""
        from mokata.run_intent import bind_registered, carry_claim, record_intent
        record_intent(self.d, "S-old", "prompt")
        bind_registered(self.d, "S-old", "R1")
        _edit(self.d, "src/a.py", "a = 2\n", "S-old", "R1")
        t = time.time()
        carry_claim(self.d, "S-new", "S-old", now=t)
        from mokata.run_intent import mark_resumed
        mark_resumed(self.d, "S-new", now=t)
        h = A.history(self.d, "S-new")
        ids = [i["id"] for i in h["items"] if i["type"] == "resume"]
        self.assertEqual(2, len(ids))
        self.assertEqual(2, len(set(ids)))

    def test_the_item_shape_is_extensible_and_p46s_explainer_is_a_registered_type(self):
        self.assertIn("explainer", A.TYPES)
        A.register_type("explainer", "x")                 # idempotent
        A.register_type("p12_test_type", "a stage's own type")
        self.assertIn("p12_test_type", A.TYPES)
        with self.assertRaises(ValueError):
            A.register_type("not an identifier", "x")
        del A.TYPES["p12_test_type"]


# ===================================================================== a resumed session

class AResumedSessionShowsItsPredecessorsHistory(_Repo):

    def test_the_new_session_carries_the_old_ones_run_and_its_timeline_joins_both(self):
        """O10: `claude --resume` gives the conversation a new id. Its history must show what the
        old id did (same run), with the resume marked where the break was."""
        from mokata.run_intent import bind_registered, carry_claim, record_intent
        record_intent(self.d, "S-old", "prompt")
        bind_registered(self.d, "S-old", "R1")
        _edit(self.d, "src/a.py", "a = 2\n", "S-old", "R1")
        _gate(self.d, "S-old", "R1", "write_gate", "allowed")
        time.sleep(0.05)
        t_resume = time.time()
        state, rid = carry_claim(self.d, "S-new", "S-old", now=t_resume)
        self.assertEqual("R1", rid)
        time.sleep(0.05)
        _edit(self.d, "src/a.py", "a = 3\n", "S-new", "R1")
        _gate(self.d, "S-new", "R1", "write_gate", "allowed")
        h = A.history(self.d, "S-new")
        self.assertEqual("R1", h["run"])
        self.assertEqual({"S-new", "S-old"}, set(h["joined"]))
        sessions = {i["session"] for i in h["items"] if i["type"] in ("edit", "gate")}
        self.assertEqual({"S-new", "S-old"}, sessions, "the predecessor's work is on the timeline")
        resumes = [i for i in h["items"] if i["type"] == "resume"]
        self.assertEqual(1, len(resumes))
        self.assertEqual(("S-new", "S-old"), (resumes[0]["session"], resumes[0]["detail"]["from"]))
        self.assertAlmostEqual(t_resume, resumes[0]["t"], places=2)
        self.assertEqual(1, h["total"]["resumes"])
        # the live panel: ONE card for the conversation, its chain named
        panel = A.live(self.d, focus="S-new")
        card = self.row(panel, "S-new")
        self.assertIn("S-old", card["chain"])
        self.assertEqual("R1", card["run"])


# ===================================================================== cancel stops a job

class CancelOnAJobStopsIt(_Repo):

    def test_cancel_through_the_jobs_door_stops_the_work_and_the_panel_says_cancelled(self):
        """The panel's Cancel is the jobs feature's `POST /api/jobs/{id}/cancel`: after it the work
        stops (its thread reaches a checkpoint and leaves) and the panel lists the job cancelled."""
        from starlette.testclient import TestClient
        from mokata.ide.server import AppState, build_app
        state = AppState(self.d, TOK, 9999, None, False)
        self.addCleanup(state.close)
        c = TestClient(build_app(state), base_url="http://127.0.0.1:9999", follow_redirects=False)
        c.get(f"/pair?t={TOK}")
        started = threading.Event()

        def work():
            started.set()
            end = time.monotonic() + 20
            while time.monotonic() < end:
                C.checkpoint()
                time.sleep(0.005)
            return {}
        job = state.jobs.start("search.text", {"q": "p12"}, work, label="search p12")
        self.assertTrue(started.wait(5))
        panel = c.get("/api/activity").json()
        self.assertIn(job.id, [j["id"] for j in panel["jobs"]["active"]])
        r = c.post(f"/api/jobs/{job.id}/cancel", json={}, headers={"Origin": "http://127.0.0.1:9999"})
        self.assertEqual(200, r.status_code)
        self.assertEqual("cancelled", r.json()["job"]["state"])
        self.assertTrue(job.stopped.wait(3), "the work kept running after Cancel")
        panel = c.get("/api/activity").json()
        self.assertNotIn(job.id, [j["id"] for j in panel["jobs"]["active"]])
        self.assertIn(("cancelled", job.id), [(j["state"], j["id"]) for j in panel["jobs"]["recent"]])


# ===================================================================== the IDE was closed

class WithTheIdeClosedNothingIsLost(_Repo):

    def test_a_history_written_with_no_server_running_is_reconstructed_when_one_opens(self):
        """Everything here happened with NO IDE process: edits, gates, a phase, a ledger row, a
        session end. The first server to open answers the complete history and an honest live
        picture (the bus saw nothing; the record did)."""
        from starlette.testclient import TestClient
        from mokata.ide.server import AppState, build_app
        SI.on_start(self.d, "S1", "startup", self.d, t=time.time() - 120, proc=None)
        _edit(self.d, "src/a.py", "a = 2\n", "S1", "R1")
        emit(self.d, PhaseTransition(phase="spec"), session_id="S1", run_id="R1")
        for i in range(12):
            _gate(self.d, "S1", "R1", "write_gate", "allowed")
        _gate(self.d, "S1", "R1", "write_gate", "blocked")
        _edit(self.d, "src/a.py", "a = 3\n", "S1", "R1")
        SI.on_end(self.d, "S1", "logout")
        state = AppState(self.d, TOK, 9999, None, False)
        self.addCleanup(state.close)
        c = TestClient(build_app(state), base_url="http://127.0.0.1:9999", follow_redirects=False)
        c.get(f"/pair?t={TOK}")
        h = c.get("/api/activity/history?session=S1&limit=500").json()
        self.assertIsNone(h["next"])
        self.assertEqual({"events": 14, "writes": 3, "resumes": 0}, h["total"])
        self.assertEqual(17, h["count"])
        self.assertEqual(1, sum(1 for i in h["items"] if i["verdict"] == "blocked"))
        panel = c.get("/api/activity?focus=S1").json()
        s1 = self.row(panel, "S1")
        self.assertEqual("ended", s1["status"])
        self.assertEqual((13, 1), (s1["gates"]["fired"], s1["gates"]["blocked"]))
        self.assertFalse(panel["bus"])
        self.assertEqual("ended", s1["activity"]["state"])
        self.assertIn("ring", s1["activity"]["basis"])
        self.assertEqual((1, 2), (s1["files"]["count"], s1["files"]["writes"]))


# ===================================================================== scale

class TensOfThousandsOfEventsArePagedNotCapped(_Repo):

    def test_twenty_thousand_events_are_reached_by_the_cursor_in_bounded_pages(self):
        """No cap: a 20,000-event session is walked to the end; every page carries at most its
        `limit` events plus the writes of its time window; the total is the truth."""
        _edit(self.d, "src/a.py", "a = 2\n", "S1", "R1")
        store = store_for_root(self.d)
        store.emit(GateDecision(gate="write_gate", decision="allowed"), session_id="S1", run_id="R1")
        n = 20_000
        conn = sqlite3.connect(store.path)
        try:
            with conn:
                conn.executemany(
                    "INSERT INTO events (event_id, ts, schema_version, type, session_id, run_id, "
                    "actor, duration_ms, ledger_seq, data) VALUES (?, ?, 1, 'gate_decision', 'S1', "
                    "'R1', '', NULL, NULL, ?)",
                    ((f"e{i:06d}", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(1_700_000_000 + i)),
                      json.dumps({"gate": "write_gate", "decision": "allowed", "subject": "src/a.py"}))
                     for i in range(n - 1)))
        finally:
            conn.close()
        self.assertEqual(n, store.count_for(session_ids=["S1"], run_id="R1"))
        t0 = time.perf_counter()
        first = A.history(self.d, "S1", limit=2000)
        first_s = time.perf_counter() - t0
        self.assertLessEqual(len(first["items"]), 2000 + 2)
        self.assertEqual(n, first["total"]["events"])
        pages, seen, before = 0, 0, None
        t1 = time.perf_counter()
        while True:
            page = A.history(self.d, "S1", before=before, limit=2000)
            pages += 1
            seen += sum(1 for i in page["items"] if i["source"] == "events")
            if page["next"] is None:
                break
            before = page["next"]
        walk_s = time.perf_counter() - t1
        self.assertEqual(n, seen, "the cursor must reach every event")
        self.assertEqual(11, pages)
        print(f"\n[P12] 20k events: first page {first_s * 1000:.0f} ms · full walk {walk_s:.2f} s "
              f"({pages} pages)")


# ===================================================================== the adapter + the contract

class TheBusAndTheContract(_Repo):

    def test_a_non_file_tool_is_a_tool_event_without_its_command_and_a_file_tool_stays_an_edit(self):
        e = B.normalise(_cc_post(self.d, "Bash"), self.d)
        self.assertEqual({"kind": "tool", "tool": "Bash", "session": "S1", "source": "claude-code"}, e)
        e = B.normalise(_cc_post(self.d, "Write"), self.d)
        self.assertEqual("edit", e["kind"])
        self.assertIsNone(B.normalise(dict(_cc_post(self.d, "Bash"), hook_event_name="PreToolUse"),
                                      self.d), "a gate event is never a bus event (U8)")
        self.assertEqual({"kind": "tool", "tool": "Grep", "session": "S9", "source": "neutral"},
                         B.normalise({"kind": "tool", "tool": "Grep", "session": "S9"}, self.d))
        self.assertIsNone(B.normalise({"kind": "tool", "session": "S9"}, self.d))

    def test_both_routes_are_in_the_contract_with_a_probe_per_status(self):
        from mokata.ide import contract as K
        idx = K.route_index(K.load())
        live = idx[("/api/activity", "GET")]
        hist = idx[("/api/activity/history", "GET")]
        self.assertEqual("activity", live["feature"])
        self.assertEqual({"200"}, set(live["responses"]))
        self.assertEqual({"200", "400"}, set(hist["responses"]))
        self.assertEqual({"200", "400"}, {str(p["status"]) for p in hist["probes"]})
        # the item shape is pinned by the contract's schema
        self.assertEqual(["id", "t", "type", "title", "verdict", "session", "run", "source",
                          "ledger_seq", "detail"], K.load()["schemas"]["ActivityItem"]["required"])

    def test_the_panel_script_registers_a_screen_and_meets_the_no_markup_bar(self):
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "..", "src", "mokata", "ide", "static", "features",
                               "activity_panel.js"), encoding="utf-8") as fh:
            js = fh.read()
        for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(",
                    "setInterval("):
            self.assertNotIn(bad, js, bad)
        self.assertIn('id: "activity", title: "Timeline", tab: "sessions"', js)   # UX-SIMPLIFY: a Sessions view
        self.assertIn("/api/activity/history", js)
        self.assertIn("addEventListener(\"bus\"", js)


if __name__ == "__main__":
    unittest.main()
