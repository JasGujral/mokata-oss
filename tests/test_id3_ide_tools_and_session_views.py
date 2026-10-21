"""ID3 (0.0.25 Lane ID3) — the `ide_*` MCP tools, the session visuals (ID-VIS-SESSION), the session
browser (ID-SESSION-BROWSER), the switcher (ID-SESSION-SWITCHER) and needs-you (ID-NEEDS-YOU).

Business rules — what must be true for the user:
  * The agent can find the human's IDE (`ide_status`) and point them at code (`ide_open`: a file
    and line, and/or a session) — a UI round trip that writes nothing, starts nothing, contains the
    path on BOTH sides, and says honestly when nobody has the window open. The pairing token is
    never handed to the agent.
  * Every session that wrote in the repo is listed — live and past — with what it changed, the gates
    that fired for its run, its token spend (measured and estimated never blended), its ledger
    links, and a status that names the signal it was derived from.
  * A session waiting on a human is impossible to miss: a pending approval, a gate block nothing
    has moved past, a secret block — each is a needs-you reason, the queue is sorted by RISK (the
    formula is shown), and a proposal's content is never shown (only its id and the command).
  * A session can be replayed (time-lapse: every capture and gate decision, in order, with the
    lines each write changed — an unmeasurable write says so, it is never a zero) and its changes
    seen as a treemap of the repo sized by LOC, coloured by change volume.
  * The page's server routes are paired, Host-checked and contained like every other route; the
    SAMPLE server labels every answer as SAMPLE.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import http.client
import json
import os
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import approval
from mokata.events import emit
from mokata.events.schema import GateDecision, SecretScanHit, TokenSpend
from mokata.ide import session_views as SV
from mokata.ide import snapshots as S
from mokata.init import init_repo

STATIC = Path(__file__).resolve().parents[1] / "src" / "mokata" / "ide" / "static"
SECRET_PREVIEW = "PLANTED-PREVIEW-sk-live-1234567890"
SECRET_SUMMARY = "PLANTED-SUMMARY-hunter2"


def _silent(*_a, **_k):
    pass


def _repo(d: str) -> None:
    init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
    os.makedirs(os.path.join(d, "src", "pkg"), exist_ok=True)
    _write(d, "src/a.py", "a = 1\n")
    _write(d, "src/pkg/b.py", "".join(f"b{i} = {i}\n" for i in range(40)))
    _write(d, "README.md", "# r\n\ntext\n")


def _write(d: str, rel: str, text: str) -> None:
    with open(os.path.join(d, *rel.split("/")), "w", encoding="utf-8") as fh:
        fh.write(text)


def _edit(d: str, rel: str, text: str, sid: str, run: str):
    """What the hooks do around one native write: a BEFORE on the session's first touch, then the
    write, then an AFTER."""
    S.capture_before_first_write(d, os.path.join(d, rel), sid, run)
    _write(d, rel, text)
    time.sleep(0.002)
    return S.capture(d, os.path.join(d, rel), S.AFTER, sid, run)


def _gate(d: str, run: str, gate: str, decision: str, subject: str = "src/a.py"):
    return emit(d, GateDecision(gate=gate, decision=decision, subject=subject),
                session_id="mk-" + run, run_id=run)


class _Repo(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.d = os.path.realpath(self._t.name)
        _repo(self.d)

    def tearDown(self):
        self._t.cleanup()

    def row(self, data, sid):
        return next(r for r in data["sessions"] if r["id"] == sid)


# ===================================================================== ID-SESSION-BROWSER

class EverySessionIsListedWithWhatItDid(_Repo):

    def test_live_and_past_sessions_each_carry_changes_status_and_its_basis(self):
        _edit(self.d, "src/a.py", "a = 2\n", "S-old", "R-old")
        _edit(self.d, "src/a.py", "a = 3\nz = 1\n", "S-new", "R-new")
        now = time.time()
        live = SV.browser(self.d, now=now)
        self.assertEqual({"S-old", "S-new"}, {r["id"] for r in live["sessions"]})
        new = self.row(live, "S-new")
        self.assertEqual(["src/a.py"], new["files"])
        self.assertEqual(1, new["writes"])
        self.assertEqual("live", new["status"])
        self.assertIn("write within", new["status_basis"])
        later = SV.browser(self.d, now=now + SV.LIVE_SECS + 60)
        self.assertEqual("past", self.row(later, "S-new")["status"])
        self.assertEqual("no-signal", self.row(later, "S-new")["status_basis"],
                         "a status with no live signal must say so, not guess")

    def test_gates_spend_and_ledger_links_are_per_run_and_spend_is_never_blended(self):
        _edit(self.d, "src/a.py", "a = 2\n", "S1", "R1")
        _gate(self.d, "R1", "tdd", "pass")
        _gate(self.d, "R1", "tdd", "pass")
        _gate(self.d, "R2", "tdd", "block")                      # another run — not S1's
        emit(self.d, TokenSpend(label="develop", input_tokens=100, output_tokens=20,
                                estimated=False), session_id="mk", run_id="R1")
        emit(self.d, TokenSpend(label="estimate", input_tokens=7, output_tokens=3),
             session_id="mk", run_id="R1")
        r = self.row(SV.browser(self.d), "S1")
        self.assertEqual({"fired": 2, "blocked": 0, "by_gate": {"tdd": 2}}, r["gates"])
        self.assertEqual({"input": 100, "output": 20, "events": 1}, r["tokens"]["measured"])
        self.assertEqual({"input": 7, "output": 3, "events": 1}, r["tokens"]["estimated"])
        self.assertIsInstance(r["ledger"], list)

    def test_a_session_the_bus_saw_before_its_first_write_is_listed_and_an_ended_one_says_so(self):
        _edit(self.d, "src/a.py", "a = 2\n", "S1", "R1")
        now = time.time()
        bus = [{"kind": "session_start", "session": "S-fresh", "ts": now},
               {"kind": "session_end", "session": "S1", "ts": now}]
        data = SV.browser(self.d, now=now, bus_events=bus)
        self.assertEqual("live", self.row(data, "S-fresh")["status"])
        self.assertEqual(("ended", "session_end hook"),
                         (self.row(data, "S1")["status"], self.row(data, "S1")["status_basis"]))
        self.assertTrue(data["bus"])


# ===================================================================== ID-NEEDS-YOU

class WaitingOnAHumanIsImpossibleToMiss(_Repo):

    def test_a_pending_approval_is_a_needs_you_with_its_id_and_command_and_never_its_content(self):
        _edit(self.d, "src/a.py", "a = 2\n", "S1", "R1")
        p = approval.propose(self.d, tool="spec_emit", args={"x": 1}, run_id="R1",
                             target="spec", summary=SECRET_SUMMARY, preview=SECRET_PREVIEW)
        data = SV.browser(self.d)
        r = self.row(data, "S1")
        self.assertEqual(["awaiting-approval"], [x["reason"] for x in r["needs_you"]])
        det = r["needs_you"][0]["detail"]
        self.assertEqual((p.proposal_id, "spec_emit", f"mokata approve {p.proposal_id}"),
                         (det["proposal_id"], det["tool"], det["command"]))
        self.assertIn("AWAITING APPROVAL", r["needs_you"][0]["text"])
        blob = json.dumps(data)
        self.assertNotIn(SECRET_PREVIEW, blob, "a proposal's preview leaked into the browser")
        self.assertNotIn(SECRET_SUMMARY, blob, "a proposal's summary leaked into the browser")
        self.assertEqual(1, data["waiting"])

    def test_an_approved_proposal_waits_on_the_agent_not_on_a_person(self):
        _edit(self.d, "src/a.py", "a = 2\n", "S1", "R1")
        p = approval.propose(self.d, tool="spec_emit", args={"x": 1}, run_id="R1")
        self.assertTrue(approval.approve(self.d, p.proposal_id).ok)
        self.assertEqual([], SV.browser(self.d)["needs_you"])

    def test_a_gate_block_nothing_moved_past_is_a_needs_you_and_a_later_write_or_pass_clears_it(self):
        _edit(self.d, "src/a.py", "a = 2\n", "S1", "R1")
        time.sleep(1.1)                                         # the store's ts is per-second
        _gate(self.d, "R1", "tdd", "block")
        r = self.row(SV.browser(self.d), "S1")
        self.assertEqual(["blocked-at-gate"], [x["reason"] for x in r["needs_you"]])
        self.assertEqual(("tdd", "block"), (r["needs_you"][0]["detail"]["gate"],
                                            r["needs_you"][0]["detail"]["decision"]))
        _gate(self.d, "R1", "tdd", "pass")
        self.assertEqual([], self.row(SV.browser(self.d), "S1")["needs_you"],
                         "a gate that has since passed is not waiting on anyone")
        _gate(self.d, "R1", "tdd", "block")
        time.sleep(1.1)
        _edit(self.d, "src/a.py", "a = 9\n", "S1", "R1")
        self.assertEqual([], self.row(SV.browser(self.d), "S1")["needs_you"],
                         "a session that wrote after the block has moved past it")

    def test_the_queue_is_sorted_by_risk_not_recency_and_shows_its_formula(self):
        now = time.time()
        _edit(self.d, "src/a.py", "a = 2\n", "S-secret", "R-secret")
        _edit(self.d, "README.md", "# x\n", "S-wait", "R-wait")
        time.sleep(1.1)
        emit(self.d, SecretScanHit(kind="aws-key", where="src/a.py"), session_id="mk",
             run_id="R-secret")
        _gate(self.d, "R-secret", "write_gate", "blocked")
        # the approval is NEWER than the secret block, but a secret block is the riskier wait
        approval.propose(self.d, tool="remember", args={"y": 2}, run_id="R-wait", now=now + 5)
        bus = [{"kind": "turn_end", "session": "S-idle", "ts": now - 3600}]
        data = SV.browser(self.d, now=now + 10, bus_events=bus)
        self.assertEqual(["secret-blocked", "awaiting-approval", "turn-ended"],
                         [q["reason"] for q in data["needs_you"]])
        self.assertEqual("S-secret", data["sessions"][0]["id"], "the riskiest session leads")
        risks = [q["risk"] for q in data["needs_you"]]
        self.assertEqual(sorted(risks, reverse=True), risks)
        self.assertIn("risk =", data["risk_formula"])

    def test_a_proposal_no_session_owns_still_reaches_the_queue(self):
        p = approval.propose(self.d, tool="config_set", args={"k": "v"}, run_id="R-orphan")
        q = SV.browser(self.d)["needs_you"]
        self.assertEqual([("", p.proposal_id)], [(x["session"], x["detail"]["proposal_id"])
                                                 for x in q])

    def test_an_ended_session_is_not_asked_for_its_turn(self):
        _edit(self.d, "src/a.py", "a = 2\n", "S1", "R1")
        now = time.time()
        data = SV.browser(self.d, now=now, bus_events=[
            {"kind": "turn_end", "session": "S1", "ts": now - 5},
            {"kind": "session_end", "session": "S1", "ts": now}])
        self.assertEqual([], self.row(data, "S1")["needs_you"])


# ===================================================================== ID-VIS-SESSION time-lapse

class ASessionCanBeReplayedStepByStep(_Repo):

    def test_steps_are_ordered_with_before_write_and_gate_and_each_write_names_its_lines(self):
        _edit(self.d, "src/a.py", "a = 1\nb = 2\nc = 3\n", "S1", "R1")
        _edit(self.d, "src/pkg/b.py", "only\n", "S1", "R1")
        time.sleep(1.1)
        _gate(self.d, "R1", "tdd", "block", "src/a.py")
        _edit(self.d, "src/b2.py", "new\n", "S-other", "R-other")
        tl = SV.timelapse(self.d, "S1")
        kinds = [(s["kind"], s.get("path") or s.get("gate")) for s in tl["steps"]]
        self.assertEqual([("before", "src/a.py"), ("write", "src/a.py"),
                          ("before", "src/pkg/b.py"), ("write", "src/pkg/b.py"),
                          ("gate", "tdd")], kinds)
        w = tl["steps"][1]
        self.assertEqual((2, 0, True), (w["added"], w["removed"], w["measured"]))
        b = tl["steps"][3]
        self.assertEqual((1, 40), (b["added"], b["removed"]))
        self.assertTrue(tl["steps"][4]["blocked"])
        self.assertEqual(["src/a.py", "src/pkg/b.py"], tl["files"])
        self.assertEqual(list(range(5)), [s["i"] for s in tl["steps"]])
        self.assertNotIn("src/b2.py", json.dumps(tl), "another session's write leaked in")
        self.assertIn("reads are not recorded", tl["reads"])

    def test_a_write_whose_content_was_not_kept_is_unmeasured_never_zero(self):
        with mock.patch.object(S, "MAX_FILE_BYTES", 10):
            _edit(self.d, "src/a.py", "x" * 50 + "\n", "S1", "R1")
        w = [s for s in SV.timelapse(self.d, "S1")["steps"] if s["kind"] == "write"][0]
        self.assertEqual((None, None, False), (w["added"], w["removed"], w["measured"]))
        self.assertEqual(1, SV.timelapse(self.d, "S1")["unmeasured"])

    def test_a_missing_session_id_is_refused(self):
        from mokata.ide.nav import PathRefused
        with self.assertRaises(PathRefused):
            SV.timelapse(self.d, "")


# ===================================================================== ID-VIS-SESSION treemap

class TheChangeTreemap(_Repo):

    def _check_tiling(self, rects, w, h, eps=1e-6):
        """Tiles exactly (to `eps` per coordinate: the server rounds to 0.01 px)."""
        area = sum(r[2] * r[3] for r in rects)
        self.assertAlmostEqual(w * h, area, delta=w * h * 1e-6 + len(rects) * eps * (w + h))
        for x, y, rw, rh in rects:
            self.assertGreaterEqual(x, -eps)
            self.assertGreaterEqual(y, -eps)
            self.assertLessEqual(x + rw, w + eps)
            self.assertLessEqual(y + rh, h + eps)
        for i, a in enumerate(rects):
            for b in rects[i + 1:]:
                ox = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
                oy = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
                self.assertFalse(ox > 2 * eps and oy > 2 * eps, f"{a} overlaps {b}")

    def test_squarify_tiles_the_canvas_with_areas_proportional_to_weight(self):
        weights = [60, 30, 20, 10, 6, 3, 1]
        rects = SV.squarify(weights, 0, 0, 1000, 600)
        self._check_tiling(rects, 1000, 600)
        total = sum(weights)
        for wt, r in zip(weights, rects):
            self.assertAlmostEqual(wt / total, r[2] * r[3] / 600000, places=6)
        worst = max(max(r[2] / r[3], r[3] / r[2]) for r in rects)
        self.assertLess(worst, 4.0, "squarified rectangles should stay near-square")

    def test_the_repo_is_sized_by_loc_and_coloured_by_what_the_session_changed(self):
        _edit(self.d, "src/a.py", "a = 1\n" * 30, "S1", "R1")
        tm = SV.treemap(self.d, "S1")
        by = {f["path"]: f for f in tm["files"]}
        self.assertIn("src/pkg/b.py", by)
        self.assertEqual(40, by["src/pkg/b.py"]["loc"])
        self.assertEqual(0, by["src/pkg/b.py"]["heat"])
        self.assertGreater(by["src/a.py"]["heat"], 0)
        self.assertEqual(1, by["src/a.py"]["writes"])
        self._check_tiling([(f["x"], f["y"], f["w"], f["h"]) for f in tm["files"]], 1000, 600,
                           eps=0.02)
        pkg = next(dr for dr in tm["dirs"] if dr["path"] == "src/pkg")
        f = by["src/pkg/b.py"]
        self.assertTrue(pkg["x"] - 0.01 <= f["x"] and f["x"] + f["w"] <= pkg["x"] + pkg["w"] + 0.01
                        and pkg["y"] - 0.01 <= f["y"] and f["y"] + f["h"] <= pkg["y"] + pkg["h"] + 0.01,
                        "a file is drawn inside its directory's frame")
        self.assertEqual("lines of code", tm["sized_by"])
        self.assertFalse(any(p.startswith(".mokata") for p in by), "mokata's own record is not code")

    def test_sized_by_change_shows_only_changed_files_weighted_by_lines_changed(self):
        _edit(self.d, "src/a.py", "a = 1\n" * 30, "S1", "R1")
        _edit(self.d, "README.md", "# changed\n", "S2", "R2")
        one = SV.treemap(self.d, "S1", size="change")
        self.assertEqual(["src/a.py"], [f["path"] for f in one["files"]])
        both = SV.treemap(self.d, None, size="change")
        self.assertEqual({"src/a.py", "README.md"}, {f["path"] for f in both["files"]})
        a = next(f for f in both["files"] if f["path"] == "src/a.py")
        self.assertEqual(a["added"] + a["removed"], a["weight"])

    def test_a_huge_repo_keeps_every_changed_file_and_says_how_many_were_omitted(self):
        for i in range(12):
            _write(self.d, f"src/f{i}.py", "x\n" * (i + 1))
        _edit(self.d, "src/f0.py", "y\n", "S1", "R1")             # the SMALLEST file changes
        with mock.patch.object(SV, "MAX_TREEMAP_FILES", 4):
            tm = SV.treemap(self.d, "S1")
        paths = [f["path"] for f in tm["files"]]
        self.assertEqual(4, len(paths))
        self.assertIn("src/f0.py", paths)
        self.assertEqual(tm["total_files"] - 4, tm["omitted"])

    def test_a_binary_file_has_no_loc_and_is_left_out(self):
        with open(os.path.join(self.d, "src", "blob.bin"), "wb") as fh:
            fh.write(b"\0\1\2" * 100)
        self.assertNotIn("src/blob.bin", [f["path"] for f in SV.treemap(self.d)["files"]])


# ===================================================================== the server routes

def _client(root, demo=False, token="tok", port=9999):
    from starlette.testclient import TestClient
    from mokata.ide.server import AppState, build_app
    state = AppState(root, token, port, None, demo)
    return TestClient(build_app(state), base_url=f"http://127.0.0.1:{port}"), state


class TheServerRoutesAreGuardedAndContained(_Repo):
    def setUp(self):
        super().setUp()
        _edit(self.d, "src/a.py", "a = 2\n", "S1", "R1")
        self.c, self.state = _client(self.d)
        self.tok = {"X-Mokata-Token": "tok"}

    def tearDown(self):
        self.c.close()
        super().tearDown()

    def test_every_new_read_route_needs_pairing_and_answers_once_paired(self):
        for path in ("/api/sessions", "/api/timelapse?session=S1", "/api/treemap?session=S1"):
            self.assertEqual(401, self.c.get(path).status_code, path)
            r = self.c.get(path, headers=self.tok)
            self.assertEqual(200, r.status_code, path)
            self.assertFalse(r.json()["sample"], path)
        self.assertEqual(["S1"], [s["id"] for s in
                                  self.c.get("/api/sessions", headers=self.tok).json()["sessions"]])

    def test_bad_parameters_are_a_400(self):
        self.assertEqual(400, self.c.get("/api/timelapse", headers=self.tok).status_code)
        self.assertEqual(400, self.c.get("/api/treemap?size=bogus", headers=self.tok).status_code)

    def test_the_browser_reads_the_bus_ring(self):
        r = self.c.post("/api/bus", json={"kind": "session_end", "session": "S1"}, headers=self.tok)
        self.assertEqual(204, r.status_code)
        rows = self.c.get("/api/sessions", headers=self.tok).json()["sessions"]
        self.assertEqual("ended", rows[0]["status"])

    def test_open_publishes_a_contained_path_and_line_and_refuses_anything_outside(self):
        r = self.c.post("/api/open", json={"path": "src/a.py", "line": 7}, headers=self.tok)
        self.assertEqual(200, r.status_code)
        self.assertEqual({"pages": 0, "demo": False, "path": "src/a.py", "line": 7}, r.json())
        evts, _ = self.state.bus.after(0)
        self.assertEqual([("open", "src/a.py", 7)], [(e["kind"], e["path"], e["line"])
                                                     for e in evts])
        for bad in ("../etc/passwd", "/etc/passwd", ".mokata/manifest.json"):
            r = self.c.post("/api/open", json={"path": bad}, headers=self.tok)
            self.assertEqual(400, r.status_code, bad)
        self.assertEqual(1, self.state.bus.seq, "a refused open published something")
        self.assertEqual(400, self.c.post("/api/open", json={}, headers=self.tok).status_code)

    def test_open_with_a_session_focuses_it(self):
        r = self.c.post("/api/open", json={"session": "S1"}, headers=self.tok)
        self.assertEqual("S1", r.json()["focus"])
        self.assertEqual("S1", self.state.focus)
        self.assertEqual("focus", self.state.bus.after(0)[0][0]["kind"])

    def test_open_refuses_another_origin_and_the_hook_token(self):
        from mokata.ide import bus as B
        rec = B.ensure_bridge(self.d)
        c, state = _client(self.d)
        try:
            self.assertEqual(403, c.post("/api/open", json={"path": "src/a.py"}, headers={
                **self.tok, "Origin": "http://evil.example"}).status_code)
            self.assertIn(c.post("/api/open", json={"path": "src/a.py"},
                                 headers={B.HOOK_HEADER: rec["token"]}).status_code, (401, 403))
            self.assertEqual(0, state.bus.seq)
        finally:
            c.close()

    def test_the_sample_server_labels_every_answer(self):
        c, _ = _client(self.d, demo=True)
        try:
            for path in ("/api/sessions", "/api/timelapse?session=sample-session-a",
                         "/api/treemap"):
                body = c.get(path, headers=self.tok).json()
                self.assertTrue(body["sample"], path)
            sb = c.get("/api/sessions", headers=self.tok).json()
            self.assertEqual(1, sb["waiting"], "the SAMPLE shows what a waiting session looks like")
        finally:
            c.close()


# ===================================================================== the ide_* MCP tools

class TheIdeToolsAreReadOnlyAndRegisteredAtTheReadTail(unittest.TestCase):
    NAMES = ("ide_status", "ide_open", "ide_sessions")

    def test_registered_as_read_tools_with_read_only_closed_world_annotations(self):
        from mokata.mcp import registry as REG
        from mokata.mcp import server  # noqa: F401  (registers every tool)
        from mokata.mcp.tool_annotations import annotations_for
        for n in self.NAMES:
            self.assertIn(n, REG.read_tool_names())
            self.assertEqual({"readOnlyHint": True, "openWorldHint": False},
                             annotations_for("read", n), n)
        names = REG.tool_names()
        reads = REG.read_tool_names()
        self.assertEqual(list(self.NAMES), reads[-3:], "appended at the READ tail")
        self.assertLess(names.index("ide_sessions"), names.index("remember"))


class TheIdeToolsWithNoWindow(_Repo):

    def test_status_says_not_running_and_how_to_open_it(self):
        from mokata.mcp.tools_ide import ide_status
        out = ide_status(self.d)
        self.assertEqual(("not_running", False), (out["status"], out["running"]))
        self.assertIn("mokata ide", out["hint"])
        self.assertEqual({"wired": False, "port": None}, out["bridge"])

    def test_open_contains_the_path_before_contacting_anything(self):
        from mokata.mcp.tools_ide import ide_open
        with mock.patch("mokata.ide.launch.running") as running:
            for bad in ("../x.py", "/etc/passwd"):
                self.assertEqual("refused", ide_open(self.d, file=bad)["status"], bad)
            self.assertEqual("error", ide_open(self.d)["status"])
            running.assert_not_called()
        self.assertEqual("not_running", ide_open(self.d, file="src/a.py")["status"])

    def test_sessions_lists_the_browser_and_the_queue_and_can_filter_to_waiting(self):
        from mokata.mcp.tools_ide import ide_sessions
        self.assertIn("no session", ide_sessions(self.d)["message"])
        _edit(self.d, "src/a.py", "a = 2\n", "S1", "R1")
        _edit(self.d, "README.md", "# x\n", "S2", "R2")
        out = ide_sessions(self.d)
        self.assertEqual((2, 2), (out["count"], out["total"]))
        self.assertIn("nothing is waiting", out["message"])
        approval.propose(self.d, tool="remember", args={"z": 1}, run_id="R2",
                         preview=SECRET_PREVIEW)
        waiting = ide_sessions(self.d, needs_you_only=True)
        self.assertEqual(["S2"], [s["id"] for s in waiting["sessions"]])
        self.assertEqual(1, waiting["waiting"])
        self.assertNotIn(SECRET_PREVIEW, json.dumps(waiting))
        page = ide_sessions(self.d, limit=1)
        self.assertEqual((1, True, 1), (page["count"], page["has_more"], page["next_offset"]))


class _SSE:
    def __init__(self, port: int, token: str):
        self.q: "queue.Queue" = queue.Queue()
        self.conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        self.conn.request("GET", "/api/events", headers={
            "Host": f"127.0.0.1:{port}", "X-Mokata-Token": token, "Accept": "text/event-stream"})
        self.resp = self.conn.getresponse()
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
        end = time.time() + timeout
        while time.time() < end:
            try:
                ev, data = self.q.get(timeout=max(0.01, end - time.time()))
            except queue.Empty:
                return None
            try:
                obj = json.loads(data)
            except ValueError:
                obj = None
            if pred(ev, obj):
                return ev, obj
        return None

    def close(self):
        try:
            self.conn.close()
        except OSError:
            pass


class TheIdeToolsAgainstARealWindow(unittest.TestCase):
    """A real `mokata ide` server child; the tools talk to it the way the MCP process would."""

    @classmethod
    def setUpClass(cls):
        from mokata.ide import launch as L
        cls._t = tempfile.TemporaryDirectory()
        cls.d = os.path.realpath(cls._t.name)
        _repo(cls.d)
        L.open_or_focus(cls.d, None, browser=False, idle_secs=60)
        cls.rec = L.running(cls.d)

    @classmethod
    def tearDownClass(cls):
        from mokata.ide import launch as L
        rec = L.running(cls.d)
        if rec:
            os.kill(int(rec["pid"]), 15)
            for _ in range(40):
                if not L.running(cls.d):
                    break
                time.sleep(0.1)
        cls._t.cleanup()

    def test_status_finds_the_window_and_never_returns_its_token(self):
        from mokata.mcp.tools_ide import ide_status
        out = ide_status(self.d)
        self.assertEqual(("ok", True, False), (out["status"], out["running"], out["demo"]))
        self.assertEqual(f"http://127.0.0.1:{self.rec['port']}/", out["url"])
        self.assertNotIn(self.rec["token"], json.dumps(out), "the pairing token leaked to the agent")

    def test_open_with_no_page_attached_says_nobody_saw_it(self):
        from mokata.mcp.tools_ide import ide_open
        out = ide_open(self.d, file="src/a.py", line=3)
        self.assertEqual(("no_page", 0), (out["status"], out["pages"]))
        self.assertIn("mokata ide", out["message"])

    def test_open_reaches_an_attached_page_within_one_second(self):
        from mokata.mcp.tools_ide import ide_open
        sse = _SSE(int(self.rec["port"]), self.rec["token"])
        try:
            self.assertIsNotNone(sse.wait(lambda ev, _o: ev == "focus", 5.0), "stream not up")
            t0 = time.perf_counter()
            out = ide_open(self.d, file="src/pkg/b.py", line=12)
            self.assertEqual(("ok", 1), (out["status"], out["pages"]))
            got = sse.wait(lambda ev, o: ev == "bus" and o and o.get("kind") == "open", 2.0)
            self.assertLess(time.perf_counter() - t0, 1.0)
            self.assertEqual(("src/pkg/b.py", 12), (got[1]["path"], got[1]["line"]))
            out = ide_open(self.d, session="S-elsewhere")
            self.assertEqual("S-elsewhere", out["session"])
            got = sse.wait(lambda ev, o: ev == "focus" and o and o.get("focus") == "S-elsewhere",
                           2.0)
            self.assertIsNotNone(got, "the session switch never reached the page")
        finally:
            sse.close()


# ===================================================================== the page (not browser-run)

class ThePageHasTheSessionsMode(unittest.TestCase):

    def test_the_sessions_view_needs_you_marker_and_script_are_served(self):
        from mokata.ide.server import STATIC_FILES
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        for needle in ('data-mode="sessions"', 'id="view-sessions"', 'id="needs-you"',
                       'id="tl-range"', 'id="tm"', 'id="sb-list"', 'id="ny-list"',
                       '/static/sessions.js'):
            self.assertIn(needle, html)
        self.assertIn("sessions.js", STATIC_FILES)
        self.assertLess(html.index("/static/sessions.js"), html.index("/static/boot.js"),
                        "a view registers its hooks before boot runs them")

    def test_the_page_follows_open_events_and_draws_without_markup(self):
        app = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn('e.kind === "open"', app)
        self.assertIn('"sessions"]', app)
        js = (STATIC / "sessions.js").read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", js)
        self.assertNotIn("insertAdjacentHTML", js)
        for hook in ("MK.wireSessions", "MK.onModeSessions", "MK.onRefreshSessions",
                     "/api/sessions", "/api/timelapse", "/api/treemap"):
            self.assertIn(hook, js)


if __name__ == "__main__":
    unittest.main()
