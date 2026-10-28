"""0.0.26 P35 `NAV-AT-SCALE` — PRD §6 R19, R21, R22 and G16 driven through the REAL page
(Playwright) on a big repo whose code-review-graph index is BUILT.

Run it (not part of the unit suite — the bed is big):

    MOKATA_P35_REPO=/path/to/kubernetes MOKATA_P35_SYMBOL=NewMainKubelet \\
        python tests/e2e_scale/test_p35_nav_at_scale.py

The interpreter needs mokata's `[ide]` deps and Playwright (+ a Chromium). Skipped, with the reason,
when any of that is missing or the repo's index is not built. What must be true for the user:

  R21 F12 / ⌘-click on MOKATA_P35_SYMBOL opens the viewer at the path:line code-review-graph
      records for its definition;
  R22 selecting a symbol asks the graph exactly ONE question (the visible tab), not six;
  G16 one live `edit` event for the open file costs at most 2 requests;
  R19 the explorer's first listing is ≤ 300 KB, and typing a file name in its filter finds it
      server-side (MOKATA_P35_FIND, default the definition's basename).

Measured numbers are printed as one JSON line (`P35-SCALE {...}`) for the stage report.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, "..", "..", "src"))
REPO = os.environ.get("MOKATA_P35_REPO", "")
SYMBOL = os.environ.get("MOKATA_P35_SYMBOL", "")


def _skip_reason() -> str:
    if not REPO or not os.path.isdir(REPO) or not SYMBOL:
        return "set MOKATA_P35_REPO (a repo with a BUILT code-review-graph index) and MOKATA_P35_SYMBOL"
    try:
        import playwright  # noqa: F401
    except ImportError:
        return "Playwright is not installed in this interpreter"
    sys.path.insert(0, SRC)
    from mokata.knowledge import crg_nodes
    if crg_nodes.reader(REPO) is None:
        return f"{REPO} has no BUILT code-review-graph index (`mokata graph build`)"
    return ""


SKIP = _skip_reason()


@unittest.skipIf(SKIP, SKIP)
class NavAtScale(unittest.TestCase):
    M = {}

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        from mokata.knowledge import crg_nodes
        defs = crg_nodes.reader(REPO).defs(SYMBOL)
        cls.defs = defs
        env = dict(os.environ, PYTHONPATH=SRC,
                   PATH=os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", ""))
        cls.port = 25000 + os.getpid() % 2000
        cls.proc = subprocess.Popen([sys.executable, "-m", "mokata.cli", "ide", "--path", REPO,
                                     "--no-browser", "--foreground", "--port", str(cls.port)],
                                    cwd=REPO, env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        url, t0 = None, time.time()
        while time.time() - t0 < 120 and url is None:
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
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()
        cls.page = cls.browser.new_page(viewport={"width": 1440, "height": 900})
        cls.requests, cls.errors = [], []
        cls.page.on("request", lambda r: cls.requests.append(r.url))
        cls.page.on("pageerror", lambda e: cls.errors.append(str(e)))
        cls.page.on("console", lambda m: cls.errors.append(m.text) if m.type == "error" else None)
        cls.page.goto(url)
        cls.page.wait_for_load_state("load")
        cls.page.wait_for_function("() => window.MK && MK.ui && MK.ui.restored === true",
                                   timeout=180000)
        time.sleep(1)

    @classmethod
    def tearDownClass(cls):
        print("P35-SCALE " + json.dumps(cls.M, sort_keys=True), flush=True)
        if not cls.started:
            return
        try:
            cls.browser.close()
            cls.pw.stop()
        finally:
            cls.proc.terminate()
            try:
                cls.proc.wait(10)
            except subprocess.TimeoutExpired:
                cls.proc.kill()

    def setUp(self):
        if not self.started:
            self.fail("`mokata ide` did not print its URL within 120s")

    def js(self, expr, arg=None):
        return self.page.evaluate(expr, arg)

    def wait(self, expr, secs, arg=None):
        t0 = time.monotonic()
        while time.monotonic() - t0 < secs:
            v = self.js(expr, arg)
            if v:
                return time.monotonic() - t0, v
            self.page.wait_for_timeout(50)
        return None, None

    def since(self, n, part):
        return [u for u in self.requests[n:] if part in u]

    def test_1_R19_the_explorer_is_lazy_and_finds_any_file(self):
        self.js("() => MK.setMode('navigate')")
        took, _ = self.wait("() => MK.nav.dirs && MK.nav.dirs[''] && MK.nav.dirs[''].entries.length", 60)
        self.assertIsNotNone(took, "the explorer's root never listed")
        biggest = self.js("() => fetch('/api/tree?dir=&sync_ms=2000').then((r) => r.text())"
                          ".then((t) => t.length)")
        self.M["R19_tree_root_bytes"] = biggest
        self.assertLessEqual(biggest, 300 * 1024)
        target = self.defs[0]["path"] if self.defs else ""
        find = os.environ.get("MOKATA_P35_FIND") or target.rsplit("/", 1)[-1]
        self.page.fill("#tree-filter", find)
        took, _ = self.wait("(p) => [...document.querySelectorAll('#tree .tree-row.file')]"
                            ".some((b) => b.textContent.includes(p))", 30, target or find)
        self.M["R19_filter_found_s"] = round(took, 2) if took else None
        self.assertIsNotNone(took, f"the filter {find!r} never listed {target}")
        self.page.fill("#tree-filter", "")

    def test_2_R21_go_to_definition_opens_the_graphs_definition(self):
        self.assertTrue(self.defs, f"the graph holds no definition of {SYMBOL}")
        want = sorted((d["path"], d["line"]) for d in self.defs)
        t0 = time.monotonic()
        self.js("(s) => MK.goToDefinition(s)", SYMBOL)
        if len(want) == 1:
            took, _ = self.wait("([p, l]) => MK.nav.path === p && MK.nav.line === l", 60, list(want[0]))
            self.M["R21_jump_s"] = round(time.monotonic() - t0, 2)
            self.assertIsNotNone(took, f"the viewer did not open {want[0]} (at {self.js('() => [MK.nav.path, MK.nav.line]')})")
        else:
            took, _ = self.wait("() => MK.nav.tab === 'defs' && MK.nav.answers.defs && MK.nav.answers.defs.references", 60)
            got = sorted((r["path"], r["line"]) for r in self.js("() => MK.nav.answers.defs.references"))
            self.M["R21_defs_listed"] = len(got)
            self.assertEqual(want, got)
        ans = self.js("() => MK.nav.answers.defs")
        self.M["R21_defs_backend"] = ans and ans.get("backend")
        self.assertEqual("structural", ans["basis"])

    def test_3_R22_selecting_a_symbol_asks_one_question(self):
        self.js("() => { MK.nav.tab = 'callers'; }")
        n = len(self.requests)
        self.js("() => { MK.nav.sym = null; }")
        self.js("(s) => MK.selectSymbol(s)", SYMBOL)
        self.page.wait_for_timeout(3000)
        asked = self.since(n, "/api/query")
        self.M["R22_queries_per_select"] = len(asked)
        self.assertEqual(1, len([u for u in asked if "job=" not in u]), asked)

    def test_4_G16_one_edit_event_costs_at_most_two_requests(self):
        path = self.js("() => MK.nav.path")
        self.assertTrue(path, "no file open")
        self.page.wait_for_timeout(4000)
        self.js("() => { window.__p35 = []; const o = MK.onDeltaNav; MK.onDeltaNav = (x) => "
                "{ window.__p35.push([x, MK.ui.mode, MK.nav.path]); return o(x); }; }")
        n = len(self.requests)
        self.js("(p) => fetch('/api/bus', {method: 'POST', headers: {'content-type': "
                "'application/json'}, body: JSON.stringify({kind: 'edit', path: p})})", path)
        self.page.wait_for_timeout(4000)
        # `job=` re-asks and /api/jobs reads follow questions asked BEFORE the event (P32)
        after = [u for u in self.requests[n:] if "/api/bus" not in u and "/api/events" not in u
                 and "job=" not in u and "/api/jobs" not in u]
        self.M["G16_requests_per_edit_event"] = len(after)
        self.M["G16_delta_calls"] = self.js("() => window.__p35")
        self.M["page_errors"] = self.errors[-5:]
        self.M["G16_requests"] = [re.sub(r"^https?://[^/]+", "", u).split("?")[0] for u in after]
        self.assertLessEqual(len(after), 2, after)
        self.assertTrue([u for u in after if "/api/file" in u], "the edited open file was not re-read")


if __name__ == "__main__":
    unittest.main()
