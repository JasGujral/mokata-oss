"""0.0.26 P2 — `ID-TABS` + session restore (doc 109 §13.4; doc 84 ID-TABS).

Business behaviour, each falsifiable:
  * Open three tabs, restart the server app: the same tabs (order, kinds, scroll, line), the same
    active tab and the same expanded explorer dirs come back — from the SERVER's per-repo file
    under .mokata/temp_local/ide/, not the browser.
  * Shared CLI <-> IDE: a server started with no session to focus re-focuses the saved one; one
    started for a session (/mokata:review-window) focuses that session and still restores the
    tabs; `mokata ide` re-focusing a running window no longer clears its focus.
  * The edge over px0: restore also says what happened while the window was away — the focused
    session's run and its pipeline position, and the approvals waiting on a person (by id, tool
    and command; never the proposal's content).
  * Bounded: tabs, closed tabs and dirs are capped (newest kept), the file stays under its byte
    cap, a bad path never enters it, an oversized body is a 400.
  * User-owned data: a corrupt / foreign-schema file is REFUSED LOUDLY — moved aside byte for
    byte (never deleted, never overwritten), named in the answer and on stderr — and the IDE
    starts clean without crashing.
  * The tab-list rules (close / others / right / left / all with pinned tabs, reopen, Ctrl+Tab,
    Alt+1..9, preview, cap) are the page's pure model, run under node (skipped without node);
    every key goes through the P0 registry with Ctrl and Cmd as one modifier.
"""

from __future__ import annotations

import contextlib
import glob
import io
import json
import re
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from test_p0_ide_api_contract import BASE, ORIGIN, TOKEN, PORT, fixture

from mokata.ide import workspace as W

STATIC = Path(__file__).resolve().parents[1] / "src" / "mokata" / "ide" / "static"

THREE = {
    "tabs": [
        {"kind": "file", "path": "src/app/core.py", "line": 7, "scroll": 140},
        # P34 (G20): one tab per file; a tab showing the CHANGE is `view: "diff"` (the Δ toggle).
        {"kind": "file", "view": "diff", "path": "src/app/views.py", "line": 1, "scroll": 30},
        {"kind": "file", "path": "tests/test_core.py", "line": 3, "scroll": 0, "pinned": True},
    ],
    "active": "file:src/app/views.py",
    "closed": [{"kind": "file", "path": "README.md", "line": 1, "scroll": 0}],
    "expanded": ["src", "src/app"], "collapsed": ["tests"],
    "mode": "review", "panel": "changes",
}


def boot(root, demo=False, focus=None):
    """A fresh server app — what a restart (or a relaunch) builds."""
    from starlette.testclient import TestClient
    from mokata.ide.server import AppState, build_app
    state = AppState(root, TOKEN, PORT, focus, demo)
    c = TestClient(build_app(state), base_url=BASE, follow_redirects=False)
    c.get(f"/pair?t={TOKEN}")
    return c, state


class _Repo(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.d = os.path.realpath(self._t.name)
        fixture(self.d)

    def tearDown(self):
        self._t.cleanup()

    def save(self, c, doc):
        r = c.post("/api/workspace", json=doc, headers=ORIGIN)
        self.assertEqual(200, r.status_code, r.text)
        return r.json()


# ===================================================================== the round trip

class TabsSurviveARestart(_Repo):

    def test_three_tabs_their_scroll_the_active_tab_and_the_dirs_come_back_after_a_restart(self):
        c, _ = boot(self.d)
        self.assertEqual([], c.get("/api/workspace").json()["tabs"], "a new repo has no tabs")
        self.assertEqual(3, self.save(c, THREE)["tabs"])
        del c                                                       # the server goes away

        c2, _ = boot(self.d)                                        # ... and a new one starts
        w = c2.get("/api/workspace").json()
        self.assertEqual(["file:src/app/core.py", "file:src/app/views.py",
                          "file:tests/test_core.py"], [t["key"] for t in w["tabs"]])
        self.assertEqual(["code", "diff", "code"], [t["view"] for t in w["tabs"]])
        self.assertEqual("file:src/app/views.py", w["active"])
        self.assertEqual([140, 30, 0], [t["scroll"] for t in w["tabs"]])
        self.assertEqual([7, 1, 3], [t["line"] for t in w["tabs"]])
        self.assertEqual([False, False, True], [t["pinned"] for t in w["tabs"]])
        self.assertEqual(["file:README.md"], [t["key"] for t in w["closed"]], "reopen survives too")
        self.assertEqual(["src", "src/app"], w["expanded"])
        self.assertEqual(["tests"], w["collapsed"])
        self.assertEqual(("review", "changes"), (w["mode"], w["panel"]))
        self.assertIsNone(w["refused"])
        # It is the server's per-repo file — where the server's own record lives.
        path = W.workspace_path(self.d)
        self.assertEqual(os.path.join(self.d, ".mokata", "temp_local", "ide", "workspace.json"), path)
        with open(path, "rb") as fh:
            raw = fh.read()
        self.assertNotIn(b"\r\n", raw, "one byte form on every OS")
        self.assertEqual(3, len(json.loads(raw)["tabs"]))

    def test_a_relaunch_with_no_session_refocuses_the_saved_one_and_a_session_that_asks_wins(self):
        c, state = boot(self.d)
        c.post("/api/focus", json={"session": "S-1"}, headers=ORIGIN)
        self.save(c, THREE)                                         # focus recorded server-side
        self.assertEqual("S-1", json.loads(Path(W.workspace_path(self.d)).read_text(encoding="utf-8"))["focus"])

        c2, s2 = boot(self.d)                                       # `mokata ide` relaunch
        self.assertEqual("S-1", s2.focus)
        self.assertEqual("S-1", c2.get("/api/state").json()["focus"])
        self.assertEqual("S-1", c2.get("/api/workspace").json()["live"]["focus"])

        c3, s3 = boot(self.d, focus="S-2")                          # /mokata:review-window from S-2
        self.assertEqual("S-2", s3.focus)
        w = c3.get("/api/workspace").json()
        self.assertEqual("S-2", w["live"]["focus"])
        self.assertEqual(3, len(w["tabs"]), "the same tabs re-open for the other entry point")

    def test_the_page_cannot_set_the_recorded_focus(self):
        c, _ = boot(self.d)
        self.save(c, dict(THREE, focus="FORGED"))
        self.assertIsNone(json.loads(Path(W.workspace_path(self.d)).read_text(encoding="utf-8"))["focus"])

    def test_refocusing_a_running_window_without_a_session_keeps_its_focus(self):
        from mokata.ide import launch
        rec = {"port": 1, "token": "t", "demo": False}
        for session, want in ((None, []), ("S1", ["/api/focus"])):
            calls = []
            with mock.patch.object(launch, "running", return_value=rec), \
                    mock.patch.object(launch, "_call", lambda *a, **k: calls.append(a[2]) or {}), \
                    mock.patch.object(launch, "_open", lambda *a: None):
                got = launch._open_or_focus("/r", session, False, False, None, False)
            self.assertFalse(got.started)
            self.assertEqual(want, calls, f"session={session!r}")

    def test_sample_data_keeps_the_workspace_in_memory_and_writes_nothing(self):
        c, _ = boot(self.d, demo=True)
        out = self.save(c, THREE)
        self.assertTrue(out["sample"])
        self.assertEqual(3, len(c.get("/api/workspace").json()["tabs"]))
        self.assertFalse(os.path.exists(W.workspace_path(self.d)))


# ===================================================================== the edge: since you left

class RestoreSaysWhatHappenedWhileAway(_Repo):

    def test_it_names_the_focused_runs_position_and_the_approvals_waiting_on_a_person(self):
        from mokata import approval
        mine = approval.propose(self.d, tool="spec_emit", args={"secret": "do-not-show"},
                                run_id="R-1", target="spec.md")
        approval.propose(self.d, tool="remember", args={"y": 2}, run_id="R-other")
        c, _ = boot(self.d, focus="S-1")
        live = c.get("/api/workspace").json()["live"]
        self.assertEqual("S-1", live["focus"])
        self.assertIsNotNone(live["run"], "the focused session's run is found")
        self.assertEqual("R-1", live["run"]["run"])
        self.assertIn("current", live["run"])
        self.assertEqual([mine.proposal_id], [p["proposal_id"] for p in live["pending"]],
                         "the focused run's approvals, not another run's")
        p = live["pending"][0]
        self.assertEqual("spec_emit", p["tool"])
        self.assertIn(mine.proposal_id, p["command"])
        self.assertNotIn("do-not-show", json.dumps(live), "never the proposal's content")

    def test_an_approved_proposal_is_not_waiting_on_a_person(self):
        from mokata import approval
        p = approval.propose(self.d, tool="spec_emit", args={"x": 1}, run_id="R-1")
        approval.approve(self.d, p.proposal_id)
        c, _ = boot(self.d, focus="S-1")
        self.assertEqual([], c.get("/api/workspace").json()["live"]["pending"])

    def test_with_no_focus_there_is_no_run_and_nothing_is_invented(self):
        c, _ = boot(self.d)
        live = c.get("/api/workspace").json()["live"]
        self.assertIsNone(live["focus"])
        self.assertIsNone(live["run"])


# ===================================================================== bounded

class TheStateIsBounded(_Repo):

    def test_lists_are_capped_newest_kept_and_the_file_stays_small(self):
        c, _ = boot(self.d)
        many = {"tabs": [{"kind": "file", "path": f"src/f{i:04d}.py", "line": i, "scroll": i}
                         for i in range(300)],
                "closed": [{"kind": "file", "path": f"old/{i}.py"} for i in range(300)],
                "expanded": [f"d{i}" for i in range(3000)], "collapsed": []}
        out = self.save(c, many)
        self.assertEqual(W.MAX_TABS, out["tabs"])
        self.assertGreater(out["dropped"], 0)
        w = c.get("/api/workspace").json()
        self.assertEqual(f"src/f{299:04d}.py", w["tabs"][-1]["path"], "the newest tabs are kept")
        self.assertEqual(W.MAX_CLOSED, len(w["closed"]))
        self.assertLessEqual(len(w["expanded"]), W.MAX_DIRS)
        self.assertLessEqual(os.path.getsize(W.workspace_path(self.d)), W.MAX_BYTES)

    def test_long_paths_still_fit_the_byte_cap(self):
        c, _ = boot(self.d)
        long = "a/" * 500 + "x.py"                                  # ~1000 chars, allowed
        self.save(c, {"tabs": [{"kind": "file", "path": f"{i}/{long}"} for i in range(40)],
                      "expanded": [f"{i}/{long}" for i in range(180)]})
        self.assertLessEqual(os.path.getsize(W.workspace_path(self.d)), W.MAX_BYTES)
        w = c.get("/api/workspace").json()
        self.assertTrue(w["tabs"], "it sheds the oldest, not everything")
        self.assertIn(w["active"], [t["key"] for t in w["tabs"]])

    def test_a_path_out_of_the_repo_never_enters_the_file(self):
        c, _ = boot(self.d)
        bad = ["../etc/passwd", "/etc/passwd", "C:/Windows/x", "a/../../b", "x\0y", "", "a" * 2000]
        out = self.save(c, {"tabs": [{"kind": "file", "path": p} for p in bad]
                            + [{"kind": "file", "path": "./src//ok.py"}, {"kind": "nope", "path": "z"},
                               {"kind": "commit", "sha": "not-a-sha"}],
                            "expanded": bad})
        w = c.get("/api/workspace").json()
        self.assertEqual(["src/ok.py"], [t["path"] for t in w["tabs"]])
        self.assertEqual([], w["expanded"])
        self.assertEqual(len(bad) + 2, out["dropped"])

    def test_the_reserved_commit_tab_format_is_kept(self):
        c, _ = boot(self.d)
        self.save(c, {"tabs": [{"kind": "commit", "sha": "abc1234", "path": "src/app/core.py"}]})
        t = c.get("/api/workspace").json()["tabs"][0]
        self.assertEqual(("commit", "abc1234", "commit:abc1234:src/app/core.py"),
                         (t["kind"], t["sha"], t["key"]))

    def test_an_oversized_or_malformed_body_is_a_400_and_writes_nothing(self):
        c, _ = boot(self.d)
        huge = json.dumps({"tabs": [], "pad": "x" * (300 * 1024)})
        r = c.post("/api/workspace", content=huge.encode(), headers=dict(ORIGIN, **{
            "Content-Type": "application/json"}))
        self.assertEqual(400, r.status_code)
        for body in (b"not json", b"[1,2]", b'{"tabs": 3}'):
            self.assertEqual(400, c.post("/api/workspace", content=body, headers=ORIGIN).status_code)
        self.assertFalse(os.path.exists(W.workspace_path(self.d)))

    def test_a_cross_origin_page_cannot_write_it(self):
        c, _ = boot(self.d)
        r = c.post("/api/workspace", json=THREE, headers={"Origin": "https://evil.example"})
        self.assertEqual(403, r.status_code)
        self.assertFalse(os.path.exists(W.workspace_path(self.d)))


# ===================================================================== user-owned data

class ACorruptWorkspaceIsRefusedLoudly(_Repo):

    def _plant(self, raw: bytes):
        path = W.workspace_path(self.d)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(raw)
        return path

    def _check(self, raw: bytes, why: str):
        path = self._plant(raw)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            c, state = boot(self.d)                                 # does not crash
        w = c.get("/api/workspace").json()
        self.assertEqual([], w["tabs"], "starts clean")
        self.assertIn("refused", w["refused"])
        self.assertIn(why, w["refused"])
        self.assertIn("refused the saved workspace", err.getvalue(), "said on stderr")
        self.assertFalse(os.path.exists(path))
        aside = glob.glob(os.path.join(os.path.dirname(path), "workspace.corrupt-*.json"))
        self.assertEqual(1, len(aside), "kept, not deleted")
        with open(aside[0], "rb") as fh:
            self.assertEqual(raw, fh.read(), "moved aside byte for byte")
        self.assertIn(aside[0], w["refused"], "the answer names where it went")
        # ... and a fresh save works afterwards, without touching the kept copy.
        self.save(c, THREE)
        self.assertEqual(3, len(c.get("/api/workspace").json()["tabs"]))
        with open(aside[0], "rb") as fh:
            self.assertEqual(raw, fh.read())
        self.assertIsNone(state.focus)

    def test_garbage_bytes(self):
        self._check(b"\x00\xffnot json at all{", "not JSON")

    def test_json_that_is_not_an_object(self):
        self._check(b"[1, 2, 3]\n", "not a JSON object")

    def test_a_foreign_schema(self):
        self._check(json.dumps({"schema": 99, "tabs": []}).encode(), "schema 99")

    def test_a_file_that_cannot_be_moved_aside_is_never_overwritten(self):
        path = self._plant(b"{broken")
        with mock.patch("mokata.ide.workspace.os.replace", side_effect=OSError("locked")), \
                contextlib.redirect_stderr(io.StringIO()):
            c, _ = boot(self.d)
        r = c.post("/api/workspace", json=THREE, headers=ORIGIN)
        self.assertEqual(409, r.status_code)
        with open(path, "rb") as fh:
            self.assertEqual(b"{broken", fh.read())


# ===================================================================== the page's model (node)

def _node(js: str):
    src = (STATIC / "features" / "tabs.js").read_text(encoding="utf-8")
    p = subprocess.run(_support.node_argv(src + "\nconst M = globalThis.MK.tabsModel;\n"
                        + js + "\nprocess.stdout.write(JSON.stringify(out));"),
                       capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
                       encoding="utf-8")
    if p.returncode:
        raise AssertionError(p.stderr)
    return json.loads(p.stdout)


_SETUP = r"""
const keys = (m) => m.tabs.map((t) => t.path);
let m = M.empty();
for (const p of ["a", "b", "c", "d", "e"]) m = M.open(m, { kind: "file", path: p });
const pinA = { ...m, tabs: m.tabs.map((t) => t.path === "a" ? { ...t, pinned: true } : t) };
const out = {};
"""


@unittest.skipUnless(shutil.which("node"), "node is not on PATH — the page logic runs in a browser")
class TheTabListRules(unittest.TestCase):

    def test_close_others_right_left_all_keep_pinned_tabs_and_move_the_active_tab(self):
        o = _node(_SETUP + r"""
const at = (mm, how, key) => { const r = M.close(mm, "file:" + key, how); return [keys(r), r.active, r.closed.map((t) => t.path)]; };
out.one = at(m, "one", "e");           // closing the active, last tab -> its left neighbour
const mc = { ...m, active: "file:c" };
out.oneMid = at(mc, "one", "c");       // closing the active middle tab -> its right neighbour
out.others = at(pinA, "others", "c");
out.right = at({ ...pinA, active: "file:e" }, "right", "c");
out.left = at(pinA, "left", "c");
out.all = at(pinA, "all", "c");
out.pinnedOne = at(pinA, "one", "a");  // asking for ONE pinned tab closes it
out.noop = M.close(m, "file:e", "right") === m;
out.missing = M.close(m, "file:zz", "one") === m;
""")
        self.assertEqual([["a", "b", "c", "d"], "file:d", ["e"]], o["one"])
        self.assertEqual([["a", "b", "d", "e"], "file:d", ["c"]], o["oneMid"])
        self.assertEqual([["a", "c"], "file:c", ["b", "d", "e"]], o["others"])
        self.assertEqual([["a", "b", "c"], "file:c", ["d", "e"]], o["right"])
        self.assertEqual([["a", "c", "d", "e"], "file:e", ["b"]], o["left"])
        self.assertEqual([["a"], "file:a", ["b", "c", "d", "e"]], o["all"])
        self.assertEqual([["b", "c", "d", "e"], "file:e", ["a"]], o["pinnedOne"])
        self.assertTrue(o["noop"], "nothing to the right of the last tab: the same model")
        self.assertTrue(o["missing"])

    def test_reopen_brings_back_the_last_closed_first_and_ctrl_tab_alt_n_move(self):
        o = _node(_SETUP + r"""
let r = M.close(m, "file:b", "one"); r = M.close(r, "file:d", "one");
r = M.reopen(r); out.re1 = [keys(r), r.active];
r = M.reopen(r); out.re2 = [keys(r), r.active];
out.reEmpty = M.reopen(r) === r;
out.next = M.cycle(m, 1).active;                     // active is e (last) -> wraps to a
out.prev = M.cycle(m, -1).active;
out.n1 = M.nth(m, 1).active; out.n3 = M.nth(m, 3).active;
out.n9 = M.nth(m, 9).active; out.n8 = M.nth(m, 8) === m;
""")
        self.assertEqual([["a", "c", "e", "d"], "file:d"], o["re1"])
        self.assertEqual([["a", "c", "e", "d", "b"], "file:b"], o["re2"])
        self.assertTrue(o["reEmpty"])
        self.assertEqual("file:a", o["next"])
        self.assertEqual("file:d", o["prev"])
        self.assertEqual(("file:a", "file:c"), (o["n1"], o["n3"]))
        self.assertEqual("file:e", o["n9"], "Alt+9 is the LAST tab")
        self.assertTrue(o["n8"], "no 8th tab: nothing moves")

    def test_walking_fills_one_preview_tab_and_the_cap_evicts_into_reopen(self):
        o = _node(r"""
const out = {};
let m = M.empty();
m = M.open(m, { kind: "file", path: "keep.py" });
for (const p of ["x", "y", "z"]) m = M.open(m, { kind: "file", view: "diff", path: p }, { preview: true });
out.walk = m.tabs.map((t) => [t.key, t.preview]);
m = M.keep(m, m.active);
m = M.open(m, { kind: "file", view: "diff", path: "w" }, { preview: true });
out.kept = m.tabs.map((t) => t.key);
out.again = M.open(m, { kind: "file", path: "keep.py", line: 9 }).tabs.length;
let c = M.empty();
for (let i = 0; i < M.MAX_TABS + 5; i++) c = M.open(c, { kind: "file", path: "f" + i });
out.cap = [c.tabs.length, c.tabs[0].path, c.closed.length, c.closed[0].path, c.active];
out.merge = (() => { const live = M.open(M.empty(), { kind: "file", view: "diff", path: "p" }, { preview: true });
  const r = M.merge(live, { tabs: [{ kind: "file", path: "s" }], active: "file:s", closed: [] });
  return [r.tabs.map((t) => t.key), r.active]; })();
out.lang = ["a/b.py", "x.ts", "Dockerfile", "noext", "s.SH"].map((p) => M.langOf(p)[0]);
""")
        self.assertEqual([["file:keep.py", False], ["file:z", True]], o["walk"],
                         "] / [ through three files leaves ONE preview tab")
        self.assertEqual(["file:keep.py", "file:z", "file:w"], o["kept"])
        self.assertEqual(3, o["again"], "re-opening an open file activates it, no duplicate")
        self.assertEqual([40, "f5", 5, "f0", "file:f44"], o["cap"])
        self.assertEqual([["file:s", "file:p"], "file:s"], o["merge"],
                         "the saved active tab wins over a tab the page opened while loading")
        self.assertEqual(["py", "ts", "dkr", "·", "$"], o["lang"])

    def test_the_page_and_the_server_agree_on_tab_identity_and_caps(self):
        tabs = [{"kind": "file", "path": "a/b.py"}, {"kind": "file", "view": "diff", "path": "c"},
                {"kind": "file", "view": "bogus", "path": "c"},
                {"kind": "commit", "sha": "abcdef1", "path": "x"}]
        o = _node("const out = { keys: %s.map((t) => M.keyOf(t)), max: [M.MAX_TABS, M.MAX_CLOSED] };"
                  % json.dumps(tabs))
        self.assertEqual([W.clean_tab(t)["key"] for t in tabs], o["keys"])
        self.assertEqual([W.MAX_TABS, W.MAX_CLOSED], o["max"])


@unittest.skipUnless(shutil.which("node"), "node is not on PATH — the page logic runs in a browser")
class EveryTabKeyIsARegisteredBinding(unittest.TestCase):
    """tabs.js under node with the real registry and a stub DOM: its keys are registered (Ctrl and
    Cmd as one modifier), owned by the tabs feature, and collide with no core key."""

    STUB = r"""
const handlers = [];
const fake = () => { const n = { style: {}, dataset: {}, children: [], classList: { add() {}, toggle() {}, remove() {} },
  append(...k) { n.children.push(...k); }, prepend() {}, replaceChildren() { n.children = []; }, setAttribute() {},
  addEventListener() {}, querySelector() { return null; }, contains() { return false; }, focus() {} }; return n; };
globalThis.window = globalThis;
globalThis.document = { addEventListener(t, f) { handlers.push([t, f]); }, querySelector() { return null; },
  createElement: fake, body: fake(), getElementById() { return null; } };
"""

    def test_alt_chords_belong_to_tabs_and_the_browser_reserved_ones_to_nobody(self):
        # P34 (R30, B-F16): Ctrl/⌘+W closed the IDE's window; Ctrl/⌘+Tab and Ctrl/⌘+Shift+T are the
        # browser's too. Tabs use Alt chords only, and the registry refuses the browser's.
        reg = (STATIC / "registry.js").read_text(encoding="utf-8")
        tabs = (STATIC / "features" / "tabs.js").read_text(encoding="utf-8")
        core = (STATIC / "features" / "core-commands.js").read_text(encoding="utf-8")
        screens = re.search(r"const SCREENS = \[.*?\];", (STATIC / "app.js").read_text(encoding="utf-8"),
                            re.S).group(0)
        js = (self.STUB + reg + "\nwindow.MK.ui = {}; window.MK.$ = () => null; window.MK.el = fake;\n"
              + screens + "\nMK.SCREENS = SCREENS;\n" + core + "\n" + tabs + r"""
const R = window.mokataIDE, N = R.keys.normalize;
const out = {};
for (const k of ["Ctrl+W", "Cmd+W", "Ctrl+Tab", "Cmd+Shift+Tab", "Ctrl+Shift+T", "Alt+1", "Alt+9",
                 "Alt+W", "Alt+PageDown", "Alt+PageUp", "Alt+Shift+T"]) out[k] = R.keys.owner(N(k));
out.palette = R.commands().filter((c) => c.feature === "tabs").map((c) => c.title);
out.conflicts = R.conflicts();
process.stdout.write(JSON.stringify(out));
""")
        p = subprocess.run(_support.node_argv(js), capture_output=True, text=True,
                           timeout=60, stdin=subprocess.DEVNULL, encoding="utf-8")
        self.assertEqual(0, p.returncode, p.stderr)
        o = json.loads(p.stdout)
        for k in ("Ctrl+W", "Cmd+W", "Ctrl+Tab", "Cmd+Shift+Tab", "Ctrl+Shift+T"):
            self.assertIsNone(o[k], f"{k} is the browser's — no tab command may own it")
        self.assertEqual("tabs (tabs.go1)", o["Alt+1"])
        self.assertEqual("tabs (tabs.go9)", o["Alt+9"])
        self.assertEqual("tabs (tabs.close)", o["Alt+W"])
        self.assertEqual("tabs (tabs.next)", o["Alt+PageDown"])
        self.assertEqual("tabs (tabs.prev)", o["Alt+PageUp"])
        self.assertEqual("tabs (tabs.reopen)", o["Alt+Shift+T"])
        for t in ("Tabs: close others", "Tabs: close to the right", "Tabs: close to the left",
                  "Tabs: close all", "Tabs: reopen closed tab"):
            self.assertIn(t, o["palette"])
        self.assertEqual([], o["conflicts"])

    def test_the_views_hand_every_shown_file_to_the_tabs(self):
        app = (STATIC / "app.js").read_text(encoding="utf-8")
        nav = (STATIC / "nav.js").read_text(encoding="utf-8")
        self.assertIn("MK.onDiffShown(f, o)", app)
        self.assertIn("MK.onFileOpened(path, nav.line, o)", nav)
        self.assertNotIn("localStorage.setItem(viewKey()", app, "one restore, the server's")


if __name__ == "__main__":
    unittest.main()
