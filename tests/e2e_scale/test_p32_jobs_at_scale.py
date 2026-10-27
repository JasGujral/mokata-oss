"""0.0.26 P32 `IDE-JOBS` — the scale acceptance of PRD §6 R1-R5, R39, R40, driven through the REAL
page (Playwright) on a multi-million-line repo.

Run it (it is not part of the unit suite — the bed is big and the run takes minutes):

    MOKATA_SCALE_K8S=/path/to/kubernetes  python tests/e2e_scale/test_p32_jobs_at_scale.py

The repo must be a kubernetes checkout with mokata set up (`mokata setup claude --mode full`) and
code-review-graph's index BUILT (`mokata graph build`, ~10 min). The interpreter must have mokata's
`[ide]` deps, code-review-graph and Playwright (+ a Chromium). Skipped, with the reason, when any of
that is missing. Linux only for the CPU measurements (/proc).

What must be true for the user (each assertion names its R):
  R1  a ripple on `HandleError` (depth 2) and a path `NewController -> HandleError` never answer
      >= 500 — their jobs reach `partial` or `done`;
  R2  the page hears the ripple's job at least once a second; Cancel makes it `cancelled` within
      1 s and the server's CPU drops to ~0 within 2 s;
  R3  starting a code-graph rebuild shows `1 job` on the Activity chip within 500 ms, and the
      drawer row has %, elapsed and Cancel;
  R4  ring 1 of the ripple is drawn before its job ends (target <= 2 s, measured and printed);
  R5  20 s idle: zero HTTP requests, server CPU < 1 %;
  R39 a cancelled rebuild leaves code-review-graph's index COMPLETE;
  R40 a second query typed within 300 ms aborts the first fetch, and the results header never
      shows the old query under the new input.
Measured numbers are printed as one JSON line (`P32-SCALE {...}`) for the stage report.

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
REPO = os.environ.get("MOKATA_SCALE_K8S", "")
CLK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100


def _why_skip():
    if not REPO or not os.path.isdir(REPO):
        return "MOKATA_SCALE_K8S is not set to a kubernetes checkout (the big-repo bed is absent)"
    if not os.path.isdir("/proc"):
        return "the CPU measurements read /proc (Linux)"
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return "Playwright is not installed in this interpreter"
    sys.path.insert(0, SRC)
    from mokata.knowledge import crg_build
    if crg_build.probe_index(REPO) != crg_build.INDEX_BUILT:
        return (f"code-review-graph's index is not BUILT in {REPO} — run `mokata graph build` there "
                "first (~10 min on kubernetes)")
    return ""


SKIP = _why_skip()


def _tree(pid):
    out, kids = [pid], {}
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                with open(f"/proc/{d}/stat", encoding="utf-8") as fh:
                    pp = int(fh.read().rsplit(")", 1)[1].split()[1])
                kids.setdefault(pp, []).append(int(d))
            except (OSError, ValueError, IndexError):
                pass
    i = 0
    while i < len(out):
        out += kids.get(out[i], [])
        i += 1
    return out


def cpu_s(pid):
    total = 0.0
    for p in _tree(pid):
        try:
            with open(f"/proc/{p}/stat", encoding="utf-8") as fh:
                f = fh.read().rsplit(")", 1)[1].split()
            total += (int(f[11]) + int(f[12])) / CLK
        except (OSError, ValueError, IndexError):
            pass
    return total


def cpu_share(pid, secs):
    c0, t0 = cpu_s(pid), time.monotonic()
    time.sleep(secs)
    return (cpu_s(pid) - c0) / (time.monotonic() - t0)


@unittest.skipIf(SKIP, SKIP)
class JobsAtScale(unittest.TestCase):
    M = {}

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        env = dict(os.environ, PYTHONPATH=SRC,
                   PATH=os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", ""))
        cls.port = 23000 + os.getpid() % 2000
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
        # NOT a SkipTest (`test_suite_count_integrity`): the environment condition is the class's
        # `skipIf`; with the bed present, an IDE that does not start is a FAILURE of every test
        # here, counted, not a class that silently vanishes from `Ran N`.
        cls.started = url is not None
        if not cls.started:
            cls.proc.kill()
            return
        threading.Thread(target=lambda: [None for _ in cls.proc.stdout], daemon=True).start()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()
        cls.page = cls.browser.new_page(viewport={"width": 1440, "height": 900})
        cls.bad, cls.requests, cls.aborted = [], [], []
        cls.page.on("response", lambda r: cls.bad.append((r.status, r.url)) if r.status >= 500
                    else None)
        cls.page.on("request", lambda r: cls.requests.append((time.monotonic(), r.url)))
        cls.page.on("requestfailed", lambda r: cls.aborted.append((r.url, r.failure)))
        cls.page.goto(url)
        cls.page.wait_for_load_state("load")
        cls.page.wait_for_function("() => window.MK && MK.jobs && document.getElementById("
                                   "'activity-chip')", timeout=30000)
        # The saved workspace is put back after the first refresh (P2); on a big repo that lands
        # late and would switch the mode under the test — wait for it first.
        cls.page.wait_for_function("() => MK.ui.restored === true", timeout=120000)
        time.sleep(1)

    @classmethod
    def tearDownClass(cls):
        print("P32-SCALE " + json.dumps(cls.M, sort_keys=True), flush=True)
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
            self.fail("`mokata ide` did not print its URL within 120s on the scale bed")

    def js(self, expr, arg=None):
        return self.page.evaluate(expr, arg)

    def wait(self, expr, secs, poll=0.05):
        t0 = time.monotonic()
        while time.monotonic() - t0 < secs:
            v = self.js(expr)
            if v:
                return time.monotonic() - t0, v
            time.sleep(poll)
        return None, None

    def graph_job(self, kind="graph.view", symbol="HandleError"):
        return self.js("""([k, s]) => MK.jobs.all().filter((j) => j.kind === k &&
            (j.args || {}).symbol === s).sort((a, b) => b.created - a.created)[0] || null""",
                       [kind, symbol])

    # ---------------------------------------------------------------- R1 R2 R4: the ripple
    def test_1_ripple_streams_rings_reports_each_second_and_cancel_stops_it(self):
        n0 = len(self.bad)
        t0 = time.monotonic()
        self.js("() => MK.openGraph('ripple', 'HandleError')")
        took, _ = self.wait("() => !!document.querySelector('.graph-stage .job-strip:not([hidden])')",
                            2.0)
        self.M["R5.4_strip_in_canvas_s"] = took
        self.assertIsNotNone(took, "no progress strip on the canvas (§5.4)")
        took, _ = self.wait("() => MK.graph.cy && MK.graph.cy.nodes().length > 1", 120)
        self.M["R4_ring1_drawn_s"] = round(took, 2) if took else None
        self.assertIsNotNone(took, "ring 1 never drawn")
        job = self.graph_job()
        self.assertIsNotNone(job)
        jid = job["id"]
        self.M["R1_ripple_state_at_ring1"] = job["state"]
        self.assertIn(job["state"], ("partial", "done"), "R4: drawn before the job ended")
        # R2: events at least once a second, counted from the page's own job map updates
        stamps = []
        for _ in range(12):
            j = self.js("(id) => MK.jobs.get(id)", jid)
            stamps.append((time.monotonic(), j["elapsed"], j["state"]))
            if j["state"] not in ("queued", "running", "partial"):
                break
            time.sleep(0.25)
        live = [s for s in stamps if s[2] in ("running", "partial")]
        if len(live) >= 8:
            span = live[-1][1] - live[0][1]
            self.M["R2_elapsed_advanced_over_3s"] = round(span, 2)
            self.assertGreater(span, 1.5, "job events stalled (< 1 per second)")
        if self.js("(id) => MK.jobs.get(id).state", jid) in ("queued", "running", "partial"):
            t1 = time.monotonic()
            self.M["R2_strip_before_cancel"] = self.js("""() => { const s = document.querySelector(
                '.graph-stage .job-strip'); const b = s && s.querySelector('.job-cancel');
                return s ? {strip: s.hidden, btn: b.hidden, text: s.textContent,
                mode: MK.ui.mode, rect: JSON.stringify(b.getBoundingClientRect())} : null; }""")
            self.page.click(".graph-stage .job-strip .job-cancel", timeout=5000)
            took, _ = self.wait(f"() => MK.jobs.get('{jid}').state === 'cancelled'", 2.0, 0.02)
            self.M["R2_cancelled_in_s"] = round(took, 3) if took is not None else None
            self.assertIsNotNone(took)
            self.assertLess(took, 1.0)
            time.sleep(max(0.0, 2.0 - (time.monotonic() - t1)))
            after = cpu_share(self.proc.pid, 2.0)
            self.M["R2_cpu_share_after_cancel"] = round(after, 3)
            self.assertLess(after, 0.05, f"server still busy after Cancel: {after:.0%}")
        else:
            self.M["R2_note"] = "the ripple finished before it could be cancelled"
        self.M["R1_ripple_total_s"] = round(time.monotonic() - t0, 1)
        self.assertEqual([], self.bad[n0:], "R1: a response >= 500")

    def test_2_path_answers_without_a_5xx(self):
        n0 = len(self.bad)
        t0 = time.monotonic()
        self.js("() => MK.openGraph('path', 'NewController', 'HandleError')")
        took, state = self.wait("""() => { const j = MK.jobs.all().filter((x) => x.kind ===
            'graph.view' && (x.args || {}).view === 'path').sort((a, b) => b.created -
            a.created)[0]; return j && ['partial', 'done'].includes(j.state) ? j.state :
            (MK.graph.data && MK.graph.data.view === 'path' ? 'done' : null); }""", 600, 0.5)
        self.M["R1_path_state"] = state
        self.M["R1_path_s"] = round(time.monotonic() - t0, 1)
        self.assertIn(state, ("partial", "done"))
        j = self.js("""() => MK.jobs.all().filter((x) => x.kind === 'graph.view' &&
            (x.args || {}).view === 'path').sort((a, b) => b.created - a.created)[0] || null""")
        if j and j["state"] in ("queued", "running", "partial"):
            self.js(f"() => MK.jobs.cancel('{j['id']}')")
        self.assertEqual([], self.bad[n0:], "R1: a response >= 500")

    # ---------------------------------------------------------------- R40: superseded queries
    def test_3_a_second_query_aborts_the_first_and_never_shows_it(self):
        self.js("() => MK.openSearch('')")
        a0 = len(self.aborted)
        self.page.fill("#sq", "WaitForCacheSync")
        self.js("() => MK.search && document.getElementById('sq').onkeydown({key: 'Enter', "
                "preventDefault() {}})")
        time.sleep(0.1)
        self.page.fill("#sq", "NewSharedInformerFactory")
        self.js("() => document.getElementById('sq').onkeydown({key: 'Enter', "
                "preventDefault() {}})")
        wrong = []
        t0 = time.monotonic()
        while time.monotonic() - t0 < 30:
            h = self.js("() => document.getElementById('sr-word-h').textContent")
            if "WaitForCacheSync" in h:
                wrong.append(h)
            if "NewSharedInformerFactory" in h and "hit(" in h and "so far" not in h:
                break
            time.sleep(0.05)
        aborted = [u for u, _f in self.aborted[a0:] if "/api/search" in u]
        self.M["R40_aborted_search_fetches"] = len(aborted)
        self.assertGreaterEqual(len(aborted), 1, "the first search was not aborted")
        self.assertEqual([], wrong, "the old query's header showed under the new input")

    # ---------------------------------------------------------------- R3 + R39: a rebuild
    def test_4_a_rebuild_shows_on_the_chip_and_a_cancel_keeps_the_graph_complete(self):
        sys.path.insert(0, SRC)
        from mokata.knowledge import crg_build
        t0 = time.monotonic()
        self.js("() => MK.jobs.start('index.crg', {})")
        took, text = self.wait("() => { const t = document.getElementById('activity-chip')"
                               ".textContent; return /job/.test(t) ? t : null; }", 5.0, 0.02)
        self.M["R3_chip_s"] = round(took, 3) if took is not None else None
        self.assertIsNotNone(took, "the chip never showed the job")
        self.assertLess(took, 0.5, f"R3: chip after {took:.2f}s")
        self.page.keyboard.press("Escape")
        self.js("() => document.activeElement && document.activeElement.blur()")
        self.page.keyboard.press(".")
        self.page.wait_for_selector("#activity-drawer:not([hidden])", timeout=3000)
        # wait for n/N progress so the row has a % (CRG prints it every 50 files)
        took, row = self.wait("""() => { const r = [...document.querySelectorAll(
            '#activity-drawer .act-row.running')].find((x) => /code graph/.test(x.textContent));
            return r && /%/.test(r.textContent) && /elapsed/.test(r.textContent) &&
            r.querySelector('button') ? r.textContent : null; }""", 300, 0.5)
        self.M["R3_drawer_row_with_percent_s"] = round(took, 1) if took else None
        self.assertIsNotNone(row, "the drawer row lacks %, elapsed or Cancel")
        self.assertIn("Cancel", row)
        # MOKATA_SCALE_CANCEL_PCT=50 cancels half-way (the R39 acceptance; ~5 min on kubernetes)
        want = int(os.environ.get("MOKATA_SCALE_CANCEL_PCT", "0") or 0)
        while want:
            row = self.js("""() => { const r = [...document.querySelectorAll(
                '#activity-drawer .act-row.running')].find((x) => /code graph/.test(x.textContent));
                return r ? r.textContent : ''; }""")
            m = re.search(r"\((\d+)%\)", row or "")
            if not m or int(m.group(1)) >= want:
                break
            time.sleep(2)
        pct = int(re.search(r"\((\d+)%\)", row).group(1))
        self.M["R39_cancelled_at_pct"] = pct
        self.page.click("#activity-drawer .act-row.running button:has-text('Cancel')")
        took, _ = self.wait("""() => !MK.jobs.all().some((j) => j.kind === 'index.crg' &&
            ['queued', 'running', 'partial'].includes(j.state))""", 10.0)
        self.assertIsNotNone(took)
        self.M["R39_cancel_to_state_s"] = round(took, 3)
        time.sleep(1)
        self.M["R39_cpu_after_cancel"] = round(cpu_share(self.proc.pid, 2.0), 3)
        self.M["R39_index_after_cancel"] = crg_build.probe_index(REPO)
        self.assertEqual(crg_build.INDEX_BUILT, crg_build.probe_index(REPO),
                         "R39: the cancelled rebuild left the graph incomplete")
        self.M["R39_total_s"] = round(time.monotonic() - t0, 1)

    # ---------------------------------------------------------------- R5: idle is idle
    def test_5_idle_twenty_seconds_is_zero_requests_and_no_cpu(self):
        self.page.keyboard.press("Escape")
        self.wait("() => !MK.jobs.all().some((j) => ['queued', 'running', 'partial'].includes("
                  "j.state))", 30)
        time.sleep(2)
        n0 = len(self.requests)
        share = cpu_share(self.proc.pid, 20.0)
        reqs = [u for _t, u in self.requests[n0:]]
        self.M["R5_requests_in_20s"] = len(reqs)
        self.M["R5_server_cpu_share"] = round(share, 4)
        self.assertEqual([], reqs)
        self.assertLess(share, 0.01)


if __name__ == "__main__":
    unittest.main(verbosity=2)
