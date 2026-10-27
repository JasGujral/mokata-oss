"""0.0.26 P39 `FIRST-RUN` — a stranger runs `mokata ide` on their repo and is GUIDED (PRD F-1,
§5 S0 + §5.4, G27/G24/G18; rows R34, R35, R36, plus P34's leftover R33 and its write finding).

What must be true for the user:
  R34  an uninitialised repo opens on Welcome: what works now (word search, the floor's Impact)
       and what does not (meaning, the full graph, sessions, gates), each with its cost; Build /
       Set up opens a CONSENT naming what it writes, and only the consent's token starts the job;
       when the job is done the row flips and what it unlocks (Impact, Search by meaning,
       Sessions) is enabled.
  R35  Health is fed by what jobs DID: a failed graph view marks the code graph
       `degraded — graph views failing` with the error and a Retry that re-runs that job; a failed
       index build does the same on its own row.
  R36  one fix line per capability, from doctor: the search card, the Health row and the Welcome
       row print the same string for the same state.
  R33  Plan, Worktrees and the tree/outline errors say what failed, with Retry and the CLI twin.
  P29+ the IDE writes NOTHING for a repo that has not run setup — not in the repo, not in the
       user's profile (tabs, context, parse cache, meaning index) — until the user consents.

The browser rows run the real page in Chromium (Playwright) on a fresh small fixture repo and are
skipped, saying why, where Playwright/Chromium (or code-review-graph, for the graph build) is
absent. Everything else runs under unittest with the IDE app in-process.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

import test_p0_ide_api_contract as P0
import test_p29_ide_uninitialized_repo as P29
from test_c2_ide_search import FakeEmbedder

from mokata.govern import doctor
from mokata.ide import firstrun as F
from mokata.ide import health as H
from mokata.ide import search as SR

SRC = str(Path(__file__).resolve().parents[1] / "src")


def _home_files(home):
    out = []
    for dp, _dn, fns in os.walk(home):
        out += [os.path.relpath(os.path.join(dp, f), home) for f in fns]
    return sorted(out)


def _wait(c, jid, secs=120):
    t0 = time.time()
    while time.time() - t0 < secs:
        s = c.get(f"/api/jobs/{jid}").json()["job"]
        if s["state"] in ("done", "failed", "cancelled"):
            return s
        time.sleep(0.2)
    raise AssertionError(f"job {jid} did not finish in {secs}s")


def _numpy():
    try:
        import numpy  # noqa: F401
        return True
    except ImportError:
        return False


class _Stranger(P29._Stranger):
    """A fresh stranger repo + a fresh user home per class (P29's fixture)."""

    def welcome(self):
        r = self.c.get("/api/welcome", params={"sync_ms": "2000"})
        self.assertEqual(200, r.status_code, r.text)
        return {row["key"]: row for row in r.json()["capabilities"]}, r.json()


# ===================================================================== R36 — one fix line

class R36_OneFixLinePerCapability(_Stranger):

    def _three(self, state):
        """(search card, Health row, Welcome row) fix strings for meaning search."""
        card = SR.status()["install"]
        row = H._search_row(self.d)["fix"]
        rows, _ = self.welcome()
        return card, row, rows["meaning"]["fix"]

    def test_R36_extra_missing_the_card_the_health_row_and_welcome_print_one_string(self):
        with mock.patch.object(SR, "_extra_installed", return_value=False), \
                mock.patch.object(SR, "EMBEDDER_FACTORY", None):
            card, row, wl = self._three("not-installed")
        want = doctor.fix_line("meaning", "not-installed")
        self.assertIn("model2vec", want)
        self.assertEqual((want, want, want), (card, row, wl))

    def test_R36_model_not_local_one_string_and_it_is_not_the_pip_line(self):
        from mokata.memory.embed import ModelUnavailable
        with mock.patch.object(SR, "_extra_installed", return_value=True), \
                mock.patch.object(SR, "EMBEDDER_FACTORY", None), \
                mock.patch.object(SR, "_load_embedder", side_effect=ModelUnavailable("no model")):
            card, row, wl = self._three("model-not-local")
        want = doctor.fix_line("meaning", "model-not-local")
        self.assertEqual((want, want, want), (card, row, wl))
        self.assertNotIn("pip", want, "the extra IS installed: the fix is the model, not pip")

    def test_R36_setup_rows_share_doctors_line_and_it_is_p29s_command(self):
        from mokata.ide.statedir import SETUP_COMMAND
        from _health_answer import finished_health
        body = finished_health(self.c).json()
        top = body["rows"][0]
        rows, _ = self.welcome()
        self.assertEqual(SETUP_COMMAND, doctor.fix_line("setup", "not-set-up"))
        self.assertEqual({SETUP_COMMAND}, {top["fix"], rows["sessions"]["fix"], rows["gates"]["fix"]})

    def test_R36_doctors_own_ts_finding_uses_the_same_line(self):
        self.assertEqual(doctor.fix_line("ts", "not-installed"), doctor._graph_ts_install_line())


# ===================================================================== R34 — welcome + gated jobs

class R34_WelcomeOnAStrangersRepo(_Stranger):

    def test_R34_an_uninitialised_repo_lists_ready_and_not_ready_with_costs_and_shows_first(self):
        rows, body = self.welcome()
        self.assertTrue(body["first_run"])
        self.assertFalse(body["initialized"])
        self.assertGreaterEqual(body["repo"]["files"], 4)
        self.assertEqual(["lexical", "graph", "meaning", "sessions", "gates"], list(rows))
        self.assertTrue(rows["lexical"]["ready"])
        impact = rows["graph"]["enables"][0]
        self.assertEqual(("graph", True), (impact["screen"], impact["now"]),
                         "Impact answers from the floor right now")
        self.assertIn(rows["graph"]["state"], ("limited", "needs-setup"))
        self.assertEqual([False], [e["now"] for e in rows["meaning"]["enables"]],
                         "search by meaning is not offered before its index exists")
        for k in ("sessions", "gates"):
            self.assertEqual(("needs-setup", "init"), (rows[k]["state"], rows[k]["action"]))
        for r in rows.values():         # never blank: every not-ready row has a fix or an action
            if not r["ready"]:
                self.assertTrue(r["fix"] or r["action"], r)
        self.assertNothingWrittenInRepo()

    def test_R34_cost_is_an_estimate_scaled_by_file_count(self):
        small = F.cost("graph", {"code_files": 3000, "ts_files": 0, "meaning_files": 3000})
        big = F.cost("graph", {"code_files": 20000, "ts_files": 0, "meaning_files": 20000})
        self.assertIn("estimate", small["text"])
        self.assertLess(small["seconds"], big["seconds"])
        self.assertLess(small["bytes"], big["bytes"])
        # within 2x of the measured django build (3,010 files: 74 s, 542 MB)
        self.assertTrue(37 <= small["seconds"] <= 148 and 271e6 <= small["bytes"] <= 1084e6, small)

    def test_R34_a_forged_or_stale_click_runs_nothing(self):
        c = self.c.get("/api/welcome/consent", params={"action": "init"}).json()
        self.assertTrue(c["available"])
        self.assertIn(".mokata/", c["text"])
        for bad in ("", "forged", c["token"][:-1] + "x"):
            r = self.c.post("/api/welcome/run", json={"action": "init", "confirm": bad},
                            headers=P0.ORIGIN)
            self.assertEqual((409, "ConsentStale"), (r.status_code, r.json()["error"]))
        self.assertEqual([], self.c.get("/api/jobs").json()["active"])
        self.assertNothingWrittenInRepo()


class R34_ConsentThenJobThenEnabled(_Stranger):

    def test_R34_set_up_through_the_gate_then_sessions_and_gates_are_ready(self):
        env = {"PYTHONPATH": SRC + os.pathsep + os.environ.get("PYTHONPATH", "")}
        with mock.patch.dict(os.environ, env):
            c = self.c.get("/api/welcome/consent", params={"action": "init"}).json()
            r = self.c.post("/api/welcome/run", json={"action": "init", "confirm": c["token"]},
                            headers=P0.ORIGIN)
            self.assertEqual(200, r.status_code, r.text)
            self.assertEqual("mokata.init", r.json()["job"]["kind"])
            done = _wait(self.c, r.json()["id"])
        self.assertEqual("done", done["state"], done)
        self.assertTrue(os.path.isfile(os.path.join(self.d, ".mokata", "manifest.json")))
        rows, body = self.welcome()
        self.assertTrue(body["initialized"])
        self.assertEqual(("ready", "ready"), (rows["sessions"]["state"], rows["gates"]["state"]))
        self.assertIn({"screen": "sessions", "label": "Sessions", "now": True},
                      rows["sessions"]["enables"])
        # and the consent is spent: a second init is not on offer
        again = self.c.get("/api/welcome/consent", params={"action": "init"}).json()
        self.assertFalse(again["available"])

    @unittest.skipUnless(_numpy(), "the meaning index needs numpy (the [embeddings] extra)")
    def test_R34_build_meaning_is_a_job_and_search_by_meaning_is_enabled_at_done(self):
        emb = FakeEmbedder()
        with mock.patch.object(SR, "EMBEDDER_FACTORY", lambda: emb):
            rows, _ = self.welcome()
            m = rows["meaning"]
            self.assertEqual(("not-ready", "build-meaning", False, [False]),
                             (m["state"], m["action"], m["ready"], [e["now"] for e in m["enables"]]))
            self.assertIn("estimate", m["cost"]["text"])
            c = self.c.get("/api/welcome/consent", params={"action": "build-meaning"}).json()
            self.assertIn(SR.index_dir(self.d), c["writes"])
            r = self.c.post("/api/welcome/run", json={"action": "build-meaning",
                                                      "confirm": c["token"]}, headers=P0.ORIGIN)
            self.assertEqual(200, r.status_code, r.text)
            self.assertEqual("index.meaning", r.json()["job"]["kind"])
            self.assertEqual("done", _wait(self.c, r.json()["id"])["state"])
            rows, _ = self.welcome()
        self.assertEqual("ready", rows["meaning"]["state"])
        self.assertEqual([{"screen": "search", "label": "Search by meaning", "now": True}],
                         rows["meaning"]["enables"])


# ===================================================================== R35 — failing at use

class R35_HealthIsFedByJobs(_Stranger):

    def _row(self, key):
        from _health_answer import finished_health
        body = finished_health(self.c).json()
        return next(r for r in body["rows"] if r["key"] == key)

    def test_R35_a_failed_graph_view_degrades_the_code_graph_with_the_error_and_a_retry(self):
        from mokata.errors import MokataError
        from mokata.ide import source
        boom = MokataError("the graph backend exploded")
        with mock.patch.object(source.RealSource, "graph", side_effect=boom):
            r = self.c.post("/api/jobs", json={"kind": "graph.view",
                                               "args": {"view": "ripple", "symbol": "total"}},
                            headers=P0.ORIGIN)
            self.assertEqual("failed", _wait(self.c, r.json()["id"])["state"])
        row = self._row("graph")
        self.assertEqual("degraded", row["state"])
        self.assertIn(H.FAILING_AT_USE, row["detail"])
        self.assertIn("the graph backend exploded", row["detail"])
        self.assertEqual({"kind": "graph.view", "args": {"view": "ripple", "symbol": "total"}},
                         row["retry"])
        # Retry re-runs the same question; its success clears the row
        again = self.c.post("/api/jobs", json=row["retry"], headers=P0.ORIGIN)
        self.assertEqual(200, again.status_code, again.text)
        self.assertEqual("done", _wait(self.c, again.json()["id"])["state"])
        self.assertNotIn("retry", self._row("graph"))

    def test_R35_a_failed_meaning_build_degrades_its_own_row(self):
        from mokata.errors import MokataError
        from mokata.ide import jobs as J
        jobs = J.installed()              # this class's app installed its registry

        def fail():
            raise MokataError("disk full")
        j = jobs.start("index.meaning", {"root": self.d, "t": 1}, fail, label="meaning index")
        j.finished.wait(10)
        row = self._row("search")
        self.assertIn("meaning index build failing", row["detail"])
        self.assertIn("disk full", row["detail"])
        self.assertEqual("index.meaning", row["retry"]["kind"])
        self.assertNotEqual("ok", row["state"])


# ===================================================================== nothing written (P34 finding)

class NothingIsWrittenUntilConsent(_Stranger):

    def test_tabs_context_parse_cache_and_meaning_write_nothing_anywhere(self):
        from mokata.ide import workspace as W
        tabs = {"tabs": [{"kind": "file", "path": "README.md"}]}
        self.assertEqual(200, self.c.post("/api/workspace", json=tabs, headers=P0.ORIGIN).status_code)
        self.assertEqual(["file:README.md"], [t["key"] for t in W.load(self.d)[0]["tabs"]],
                         "the tabs are kept (in this server's memory)")
        self.assertEqual(200, self.c.post("/api/context", json={"kind": "file", "path": "README.md"},
                                          headers=P0.ORIGIN).status_code)
        self.c.get("/api/query", params={"kind": "callers", "target": "total"})
        with mock.patch.object(SR, "EMBEDDER_FACTORY", lambda: FakeEmbedder()):
            m = SR.meaning(self.d, "parse")
        self.assertEqual("unbuilt", m["state"], "a search does not build an index unasked")
        self.assertTrue(m["install"])
        W.load(self.d)
        self.assertEqual([], _home_files(self.home), "the IDE wrote into the user's profile")
        self.assertNothingWrittenInRepo()

    def test_after_consent_the_tabs_land_in_the_repo(self):
        from mokata.ide import workspace as W
        from mokata.init import init_repo
        with tempfile.TemporaryDirectory() as t:
            g = os.path.realpath(t)
            P0._git(g, "init", "-q", "-b", "main")
            W.save(g, {"tabs": [{"kind": "file", "path": "a.py"}]})
            self.assertFalse(os.path.exists(W.workspace_path(g)))
            self.assertEqual(["file:a.py"], [x["key"] for x in W.load(g)[0]["tabs"]])
            init_repo(root=g, profile="standard", assume_yes=True, out=P0._silent)
            W.save(g, {"tabs": [{"kind": "file", "path": "b.py"}]})
            self.assertTrue(W.workspace_path(g).startswith(os.path.join(g, ".mokata")))
            self.assertTrue(os.path.isfile(W.workspace_path(g)))


# ===================================================================== the browser rows

def _why_no_browser():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "Playwright is not installed in this interpreter"
    if not shutil.which("git"):
        return "git is not on PATH"
    try:
        with sync_playwright() as pw:
            pw.chromium.launch().close()
    except Exception as exc:                          # noqa: BLE001 — any launch failure skips
        return f"Chromium for Playwright cannot start here: {str(exc).splitlines()[0][:120]}"
    return ""


SKIP = _why_no_browser()


def _crg():
    from mokata.detect import find_command
    return bool(find_command("code-review-graph"))


class _Browser(unittest.TestCase):

    def page(self, width=1440, height=900, routes=None):
        ctx = self.browser.new_context(viewport={"width": width, "height": height})
        self.addCleanup(ctx.close)
        for pattern, handler in (routes or {}).items():
            ctx.route(pattern, handler)
        pg = ctx.new_page()
        self.errors = []
        pg.on("pageerror", lambda e: self.errors.append(str(e)))
        pg.goto(self.url)
        pg.wait_for_function("() => window.MK && MK.ui && MK.ui.state && document.body.dataset.mode")
        # the saved view is restored after the first refresh (P32 finding 1): act after it
        pg.wait_for_function("() => MK.ui.restored === true", timeout=20000)
        return pg

    def wl_state(self, pg, key):
        return pg.evaluate("(k) => { const r = document.querySelector(`#view-welcome li.wl-row[data-key=${k}]`); return r ? r.dataset.state : null; }", key)


@unittest.skipIf(SKIP, SKIP)
class InTheBrowser_RealIde(_Browser):
    """`mokata ide` itself (a subprocess, as a user runs it) on a fresh small repo + home."""

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls._t = tempfile.TemporaryDirectory()
        base = os.path.realpath(cls._t.name)
        cls.repo, cls.home = os.path.join(base, "repo"), os.path.join(base, "home")
        os.makedirs(cls.repo)
        os.makedirs(cls.home)
        P29.stranger_repo(cls.repo)
        env = dict(os.environ, PYTHONPATH=SRC, HOME=cls.home, USERPROFILE=cls.home)
        cls.port = 28000 + os.getpid() % 1500
        cls.proc = subprocess.Popen([sys.executable, "-m", "mokata.cli", "ide", "--path", cls.repo,
                                     "--no-browser", "--foreground", "--port", str(cls.port)],
                                    cwd=cls.repo, env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        url, t0 = None, time.time()
        while time.time() - t0 < 90 and url is None:
            line = cls.proc.stdout.readline()
            if not line and cls.proc.poll() is not None:
                break
            m = re.search(r"(http://127\.0\.0\.1:%d\S*)" % cls.port, line or "")
            url = m.group(1) if m else None
        cls.started = url is not None
        if not cls.started:
            cls.proc.kill()
            return
        threading.Thread(target=lambda: [None for _ in cls.proc.stdout], daemon=True).start()
        cls.url = url
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    def setUp(self):
        if not self.started:
            self.skipTest("the IDE server did not start")

    @classmethod
    def tearDownClass(cls):
        if cls.started:
            cls.browser.close()
            cls.pw.stop()
            cls.proc.terminate()
            try:
                cls.proc.wait(10)
            except subprocess.TimeoutExpired:
                cls.proc.kill()
        cls._t.cleanup()

    def _git_clean(self):
        return subprocess.run(["git", "-C", self.repo, "status", "--porcelain", "--ignored"],
                              capture_output=True, text=True, check=True).stdout

    @unittest.skipUnless(_crg(), "code-review-graph is not installed in this interpreter")
    def test_1_R34_welcome_first_then_set_up_then_build_the_graph_then_impact_turns_structural(self):
        pg = self.page()
        pg.wait_for_function("() => document.body.dataset.mode === 'welcome'", timeout=20000)
        g = "#view-welcome li.wl-row[data-key=graph]"
        pg.wait_for_selector(g)
        self.assertEqual("needs-setup", self.wl_state(pg, "sessions"))
        self.assertTrue(pg.is_disabled("#view-welcome li.wl-row[data-key=sessions] button.wl-open"))
        self.assertIn("(floor)", pg.inner_text(g + " button.wl-open"))
        self.assertTrue(pg.locator(g + " .wl-cost").inner_text())
        # P34 finding: browsing writes nothing for this repo — not in it, not in the profile
        pg.click("nav.rail .rail-btn[data-screen=navigate]")
        pg.evaluate("() => MK.openFile('src/app/core.py', 1, null)")
        pg.wait_for_timeout(1500)                           # the tabs' debounced save
        self.assertEqual("", self._git_clean())
        files = _home_files(self.home)
        self.assertFalse([f for f in files if not f.endswith(("server.json", "repo.txt"))], files)
        pg.click("nav.rail .rail-btn[data-screen=welcome]")
        # Set up -> consent (what it writes) -> confirm -> job -> ready
        pg.click("#view-welcome li.wl-row[data-key=sessions] button.wl-act")
        pg.wait_for_selector("#wl-consent h3")
        self.assertIn(".mokata/", pg.inner_text("#wl-consent"))
        self.assertEqual("", self._git_clean(), "nothing is written before Confirm")
        pg.click("#wl-consent button.wl-confirm")
        pg.wait_for_function("() => { const r = document.querySelector('#view-welcome li.wl-row[data-key=sessions]'); return r && r.dataset.state === 'ready'; }", timeout=90000)
        self.assertTrue(os.path.isfile(os.path.join(self.repo, ".mokata", "manifest.json")))
        self.assertFalse(pg.is_disabled("#view-welcome li.wl-row[data-key=sessions] button.wl-open"))
        # Build graph -> consent names .code-review-graph + its cost -> job -> structural Impact
        pg.wait_for_selector(g + " button.wl-act[data-action=build-graph]", timeout=20000)
        pg.click(g + " button.wl-act")
        pg.wait_for_selector("#wl-consent h3")
        txt = pg.inner_text("#wl-consent")
        self.assertIn(".code-review-graph", txt)
        self.assertIn("estimate", txt)
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".code-review-graph", "graph.db")))
        pg.click("#wl-consent button.wl-confirm")
        pg.wait_for_function("(s) => { const r = document.querySelector(s); return r && r.dataset.state === 'ready'; }", arg=g, timeout=180000)
        self.assertIn("(structural)", pg.inner_text(g + " button.wl-open"))
        pg.click(g + " button.wl-open")
        self.assertEqual("graph", pg.evaluate("document.body.dataset.mode"))
        self.assertEqual([], self.errors)

    def test_2_R33_plan_worktrees_tree_and_outline_errors_have_retry_and_the_cli_twin(self):
        fail = {"on": True}

        def maybe_503(route):
            if fail["on"]:
                route.fulfill(status=503, body="Service Unavailable", content_type="text/plain")
            else:
                route.continue_()
        pg = self.page(routes={"**/api/runs*": maybe_503, "**/api/worktrees*": maybe_503,
                               "**/api/tree*": maybe_503, "**/api/file?*": maybe_503})
        probe = "(s) => { const b = document.querySelector(s); return [!!b.querySelector('button'), (b.querySelector('code') || {}).textContent || '', b.textContent]; }"
        got = {}
        pg.click("nav.rail .rail-btn[data-screen=plan]")
        pg.wait_for_selector("#plan-state .region-msg.error", timeout=10000)
        got["plan"] = pg.evaluate(probe, "#plan-state .region-msg.error")
        pg.click("nav.rail .rail-btn[data-screen=navigate]")
        pg.evaluate("() => MK.loadTree(true)")
        pg.wait_for_selector("#tree .region-msg.error", timeout=10000)
        got["tree"] = pg.evaluate(probe, "#tree .region-msg.error")
        pg.evaluate("() => MK.openFile('src/app/core.py', 1, null)")
        pg.wait_for_selector("#outline .region-msg.error", timeout=10000)
        got["outline"] = pg.evaluate(probe, "#outline .region-msg.error")
        fail["on"] = False                     # Retry, once the server answers, shows content
        pg.click("#outline .region-msg.error button")
        pg.wait_for_function("() => !document.querySelector('#outline .region-msg.error')", timeout=10000)
        fail["on"] = True
        pg.click("nav.rail .rail-btn[data-screen=worktrees]")
        pg.wait_for_selector("#view-worktrees .region-msg.error", timeout=10000)
        got["worktrees"] = pg.evaluate(probe, "#view-worktrees .region-msg.error")
        fail["on"] = False
        pg.click("#view-worktrees .region-msg.error button")
        pg.wait_for_function("() => !document.querySelector('#view-worktrees .region-msg.error')", timeout=10000)
        for k, (retry, cli, text) in got.items():
            self.assertTrue(retry, f"{k}: no Retry")
            self.assertTrue(cli, f"{k}: no CLI twin")
            self.assertNotIn("Service Unavailable", text, f"{k}: the raw server text is shown")
        self.assertEqual({"plan", "tree", "outline", "worktrees"}, set(got))
        self.assertEqual([], self.errors)


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@unittest.skipIf(SKIP, SKIP)
class InTheBrowser_InProcess(_Browser):
    """The IDE app served in THIS process (uvicorn on a thread), so a test can make a job fail."""

    @classmethod
    def setUpClass(cls):
        import uvicorn
        from playwright.sync_api import sync_playwright
        from mokata.ide import nav
        from mokata.ide.server import AppState, build_app
        cls._t = tempfile.TemporaryDirectory()
        base = os.path.realpath(cls._t.name)
        cls.repo, cls.home = os.path.join(base, "repo"), os.path.join(base, "home")
        os.makedirs(cls.repo)
        os.makedirs(cls.home)
        cls._env = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE")}
        os.environ["HOME"] = os.environ["USERPROFILE"] = cls.home
        P29.stranger_repo(cls.repo)
        nav.close_layers()
        port = _free_port()
        cls.state = AppState(cls.repo, "tok39", port, None, False)
        cls.server = uvicorn.Server(uvicorn.Config(build_app(cls.state), host="127.0.0.1",
                                                   port=port, log_level="warning",
                                                   lifespan="off"))
        threading.Thread(target=cls.server.run, daemon=True).start()
        t0 = time.time()
        while not cls.server.started and time.time() - t0 < 20:
            time.sleep(0.05)
        cls.url = f"http://127.0.0.1:{port}/pair?t=tok39"
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        from mokata.ide import nav
        cls.browser.close()
        cls.pw.stop()
        cls.server.should_exit = True
        cls.state.close(2)
        nav.close_layers()
        for k, v in cls._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        cls._t.cleanup()

    def test_R35_a_failing_graph_view_shows_degraded_graph_views_failing_in_health_with_retry(self):
        from mokata.errors import MokataError
        from mokata.ide import source
        pg = self.page()
        with mock.patch.object(source.RealSource, "graph",
                               side_effect=MokataError("graph backend exploded")):
            res = pg.evaluate("() => MK.api('/api/jobs', {kind: 'graph.view', args: {view: 'ripple', symbol: 'total'}})")
            jid = res["id"]
            for _ in range(100):
                st = pg.evaluate("(id) => MK.api('/api/jobs/' + id)", jid)["job"]["state"]
                if st == "failed":
                    break
                time.sleep(0.1)
        pg.click("#health-chip")
        pg.wait_for_selector("#hp-rows li.hp-row button.hp-retry", timeout=15000)
        text = pg.evaluate("() => [...document.querySelectorAll('#hp-rows li.hp-row')].map((r) => r.textContent).join('\\n')")
        self.assertRegex(text, r"degraded\s*code graph")
        self.assertIn("graph views failing", text)
        self.assertIn("graph backend exploded", text)
        pg.click("#hp-rows li.hp-row button.hp-retry")       # the backend answers again
        pg.wait_for_function("() => !document.querySelector('#hp-rows li.hp-row button.hp-retry')", timeout=20000)
        self.assertEqual([], self.errors)

    def test_R34_meaning_build_from_welcome_enables_search_by_meaning_at_done(self):
        if not _numpy():
            self.skipTest("the meaning index needs numpy (the [embeddings] extra)")
        with mock.patch.object(SR, "EMBEDDER_FACTORY", lambda: FakeEmbedder()):
            pg = self.page()
            pg.wait_for_function("() => document.body.dataset.mode === 'welcome'", timeout=20000)
            sel = "#view-welcome li.wl-row[data-key=meaning]"
            pg.wait_for_selector(sel + " button.wl-act")
            self.assertTrue(pg.is_disabled(sel + " button.wl-open"), "nothing to open yet")
            pg.click(sel + " button.wl-act")
            pg.wait_for_selector("#wl-consent button.wl-confirm")
            self.assertIn("estimate", pg.inner_text("#wl-consent"))
            pg.click("#wl-consent button.wl-confirm")
            pg.wait_for_function("(s) => { const r = document.querySelector(s); return r && r.dataset.state === 'ready'; }", arg=sel, timeout=60000)
            self.assertFalse(pg.is_disabled(sel + " button.wl-open"))
            pg.click(sel + " button.wl-open")
            self.assertEqual("search", pg.evaluate("document.body.dataset.mode"))
        self.assertEqual([], self.errors)


if __name__ == "__main__":
    unittest.main()
