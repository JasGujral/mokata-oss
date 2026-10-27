"""0.0.26 P37 `GRAPH-AT-SCALE` — PRD §6 R4, R24, R25 (and R1/R2 on the graph views), driven
through the REAL page (Playwright) on a multi-million-line repo.

Run it (not part of the unit suite — the beds are big):

    MOKATA_P37_REPO=/path/to/kubernetes MOKATA_P37_SYMBOL=HandleError \\
    MOKATA_P37_PATH=NewController,HandleError  python tests/e2e_scale/test_p37_graph_at_scale.py

    MOKATA_P37_REPO=/path/to/django MOKATA_P37_SYMBOL=get_queryset  python tests/e2e_scale/...

The repo must have mokata set up; with code-review-graph its index must be BUILT. The interpreter
needs Playwright (+ Chromium); the IDE runs under MOKATA_P37_SERVER_PY (default: the same
interpreter), which needs mokata's `[ide]` deps. Linux only for /proc (CPU, RSS).

What must be true for the user:
  R4   ring 1 of the ripple is drawn within MOKATA_P37_RING1_S (default 2 s) and before its job
       ends;
  R24  the canvas never holds more than 150 nodes; groups carry their counts and open on click,
       still within 150; every label is drawn at >= 11 px at fit;
  R25  the status line, the legend and the summary say the same basis word;
  R1   no response >= 500 on the ripple or the path; R2 Cancel stops the server's CPU.
Measured numbers are printed as one JSON line (`P37-SCALE {...}`).

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
REPO = os.environ.get("MOKATA_P37_REPO", "")
SYMBOL = os.environ.get("MOKATA_P37_SYMBOL", "")
PATH = os.environ.get("MOKATA_P37_PATH", "")
SERVER_PY = os.environ.get("MOKATA_P37_SERVER_PY", "") or sys.executable
# MOKATA_P37_EXPECT_PARTIAL=0 for a repo whose whole ripple ends with its first ring (a small
# AST-floor answer): "drawn before the job ends" is then not observable.
RING1_S = float(os.environ.get("MOKATA_P37_RING1_S", "2.0"))
CLK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
BASIS = re.compile(r"basis:\s*(structural|approximate|floor|pending)", re.I)


def _why_skip():
    if not REPO or not os.path.isdir(REPO) or not SYMBOL:
        return "MOKATA_P37_REPO / MOKATA_P37_SYMBOL are not set (the big-repo bed is absent)"
    if not os.path.isdir("/proc"):
        return "the CPU / RSS measurements read /proc (Linux)"
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return "Playwright is not installed in this interpreter"
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


def rss_mb(pid):
    total = 0
    for p in _tree(pid):
        try:
            with open(f"/proc/{p}/status", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("VmRSS:"):
                        total += int(line.split()[1])
        except (OSError, ValueError):
            pass
    return total / 1024


@unittest.skipIf(SKIP, SKIP)
class GraphAtScale(unittest.TestCase):
    M = {}

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        env = dict(os.environ, PYTHONPATH=SRC, PYTHONUNBUFFERED="1",
                   PATH=os.path.dirname(SERVER_PY) + os.pathsep + os.environ.get("PATH", ""))
        cls.port = 25000 + os.getpid() % 2000
        cls.proc = subprocess.Popen([SERVER_PY, "-m", "mokata.cli", "ide", "--path", REPO,
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
        cls.started = url is not None       # not a SkipTest: a failure of every test, counted
        if not cls.started:
            cls.proc.kill()
            return
        log = os.environ.get("MOKATA_P37_LOG")

        def drain():
            with open(log or os.devnull, "a", encoding="utf-8") as out:
                for line in cls.proc.stdout:
                    out.write(line)
                    out.flush()
        threading.Thread(target=drain, daemon=True).start()
        cls.peak = [0.0]
        cls.stop = threading.Event()

        def sample():
            while not cls.stop.wait(0.25):
                cls.peak[0] = max(cls.peak[0], rss_mb(cls.proc.pid))
        threading.Thread(target=sample, daemon=True).start()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()
        cls.page = cls.browser.new_page(viewport={"width": 1440, "height": 900})
        cls.bad = []
        cls.page.on("response", lambda r: cls.bad.append((r.status, r.url)) if r.status >= 500
                    else None)
        cls.page.goto(url)
        cls.page.wait_for_load_state("load")
        cls.page.wait_for_function("() => window.MK && MK.jobs && MK.openGraph", timeout=30000)
        cls.page.wait_for_function("() => MK.ui.restored === true", timeout=120000)
        time.sleep(1)

    @classmethod
    def tearDownClass(cls):
        if not cls.started:
            print("P37-SCALE " + json.dumps(cls.M, sort_keys=True), flush=True)
            return
        cls.stop.set()
        cls.M["peak_rss_mb"] = round(cls.peak[0], 1)
        print("P37-SCALE " + json.dumps(cls.M, sort_keys=True), flush=True)
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

    def job(self, view, symbol):
        return self.js("""([v, s]) => MK.jobs.all().filter((j) => j.kind === 'graph.view' &&
            (j.args || {}).view === v && (j.args || {}).symbol === s)
            .sort((a, b) => b.created - a.created)[0] || null""", [view, symbol])

    def readable(self):
        """R24 / R25 facts of the picture on the canvas now."""
        return self.js("""() => { const cy = MK.graph.cy; const z = cy.zoom();
            const nodes = cy.nodes();
            const sizes = nodes.map((n) => parseFloat(n.style('font-size')) * z);
            const t = (id) => (document.getElementById(id) || {}).textContent || '';
            return { nodes: nodes.length, clusters: cy.nodes("[kind = 'cluster']").length,
                     minLabelPx: Math.min(...sizes), zoom: z,
                     status: t('g-status-text'), legend: t('g-legend-basis'),
                     summary: t('g-summary'),
                     firstCluster: (cy.nodes("[kind = 'cluster']").first().data() || {}).id || null };
        }""")

    def basis_agrees(self, r, where):
        words = [m.group(1).lower() if m else None for m in
                 (BASIS.search(r["status"]), BASIS.search(r["legend"]), BASIS.search(r["summary"]))]
        self.M[f"R25_{where}"] = words
        self.assertTrue(all(words), f"a region does not say its basis: {words} {r}")
        self.assertEqual(len(set(words)), 1, f"R25: status/legend/summary disagree: {words}")

    # ---------------------------------------------------------------- R4 R24 R25 R1 R2: ripple
    def test_1_ripple_draws_ring_one_fast_readable_and_bounded(self):
        n0 = len(self.bad)
        t0 = time.monotonic()
        # R2 is about THIS job's CPU: start from a quiet server (the page-load work — a freshness
        # update, Review's automatic impacts, the symbol index — finished first).
        quiet, _ = self.wait("() => !MK.jobs.all().some((x) => ['queued', 'running', 'partial']"
                             ".includes(x.state))", float(os.environ.get("MOKATA_P37_QUIET_S", 600)),
                             1.0)
        self.M["quiet_after_s"] = quiet
        if os.environ.get("MOKATA_P37_DEPTH"):
            self.js("(d) => { document.getElementById('g-depth').value = d; }",
                    os.environ["MOKATA_P37_DEPTH"])
        self.js("(s) => MK.openGraph('ripple', s)", SYMBOL)
        took, _ = self.wait("() => MK.graph.cy && MK.graph.cy.nodes().length > 1", 300)
        self.M["R4_ring1_drawn_s"] = round(took, 2) if took else None
        self.assertIsNotNone(took, "ring 1 never drawn")
        j = self.job("ripple", SYMBOL)
        self.M["R4_job_state_at_ring1"] = j and j["state"]
        if os.environ.get("MOKATA_P37_EXPECT_PARTIAL", "1") == "1":
            self.assertTrue(j is None or j["state"] != "done",
                            "R4: ring 1 must be drawn before the job ends")
        self.assertLessEqual(took, RING1_S, f"R4: ring 1 took {took:.2f}s (target {RING1_S}s)")
        r = self.readable()
        self.M["R24_partial"] = {k: r[k] for k in ("nodes", "clusters", "minLabelPx")}
        self.assertLessEqual(r["nodes"], 150)
        self.basis_agrees(r, "partial")
        self.assertIn("partial", r["status"])
        # to done (or a cap on the wait: the job is then cancelled and R2 measured)
        done_expr = ("() => MK.graph.data && !MK.graph.data.partial && MK.graph.data.symbol === "
                     + json.dumps(SYMBOL))
        took, _ = self.wait(done_expr, float(os.environ.get("MOKATA_P37_DONE_S", 900)), 0.5)
        self.M["ripple_done_s"] = round(time.monotonic() - t0, 1) if took else None
        if took is None:
            j = self.job("ripple", SYMBOL)
            self.M["ripple_state_at_cap"] = j and j["state"]
            if j and j["state"] in ("queued", "running", "partial"):
                self.page.click(".graph-stage .job-strip .job-cancel", timeout=5000)
                t1 = time.monotonic()
                c, _ = self.wait(f"() => MK.jobs.get('{j['id']}').state === 'cancelled'", 2.0)
                self.M["R2_cancelled_in_s"] = c
                time.sleep(max(0.0, 2.0 - (time.monotonic() - t1)))
                c0, s0 = cpu_s(self.proc.pid), time.monotonic()
                time.sleep(2.0)
                share = (cpu_s(self.proc.pid) - c0) / (time.monotonic() - s0)
                self.M["R2_cpu_share_after_cancel"] = round(share, 3)
                self.M["R2_jobs_running_after_cancel"] = self.js(
                    "() => MK.jobs.all().filter((x) => ['queued', 'running', 'partial']"
                    ".includes(x.state)).map((x) => x.kind + ':' + x.label)")
                self.assertLess(share, 0.05)
        r = self.readable()
        self.M["R24_final"] = {k: r[k] for k in ("nodes", "clusters", "minLabelPx", "zoom")}
        self.assertLessEqual(r["nodes"], 150, "R24: more than 150 nodes drawn")
        self.assertGreaterEqual(r["minLabelPx"], 11 - 1e-6, "R24: a label under 11 px at fit")
        self.basis_agrees(r, "final")
        if r["firstCluster"]:
            before = r["nodes"]
            # (a block body: `emit` returns the cytoscape collection, which must not be serialized)
            self.js("(id) => { MK.graph.cy.getElementById(id).emit('tap'); }", r["firstCluster"])
            r2 = self.readable()
            self.M["R24_after_open"] = {"before": before, "after": r2["nodes"]}
            self.assertLessEqual(r2["nodes"], 150)
            self.assertNotEqual(r2["status"], "")
        # No hard cap (U10 overruled): a full page offers Continue, which resumes the walk.
        if self.js("() => !!(MK.graph.data && MK.graph.data.more && !MK.graph.data.partial)"):
            self.assertTrue(self.js("() => !document.getElementById('g-continue').hidden"))
            self.page.click("#g-continue")
            t1 = time.monotonic()
            took, _ = self.wait("() => MK.graph.data && MK.graph.data.page === 2 && "
                                "!MK.graph.data.partial", float(os.environ.get(
                                    "MOKATA_P37_CONTINUE_S", 600)), 0.5)
            r3 = self.readable()
            self.M["continue_page2_s"] = round(time.monotonic() - t1, 1) if took else None
            self.M["continue_page2_nodes"] = self.js("() => MK.graph.data && "
                                                     "MK.graph.data.nodes.length")
            self.assertLessEqual(r3["nodes"], 150)
            if took is None:
                j = self.job("ripple", SYMBOL)
                if j and j["state"] in ("queued", "running", "partial"):
                    self.js(f"() => MK.jobs.cancel('{j['id']}')")
            else:
                self.basis_agrees(r3, "page2")
        self.assertEqual([], self.bad[n0:], "R1: a response >= 500")

    # ---------------------------------------------------------------- the call path
    def test_2_path_answers_without_a_5xx(self):
        if not PATH:
            self.skipTest("MOKATA_P37_PATH not set (src,dst)")
        src, dst = PATH.split(",", 1)
        n0 = len(self.bad)
        t0 = time.monotonic()
        self.js("([a, b]) => MK.openGraph('path', a, b)", [src, dst])
        first, _ = self.wait("() => MK.graph.cy && MK.graph.data && MK.graph.data.view === 'path'"
                             " && MK.graph.cy.nodes().length > 1", 300)
        self.M["path_first_drawn_s"] = round(first, 2) if first else None
        done, _ = self.wait("() => MK.graph.data && MK.graph.data.view === 'path' && "
                            "!MK.graph.data.partial", 900, 0.25)
        self.M["path_done_s"] = round(time.monotonic() - t0, 1) if done else None
        self.M["path_found"] = self.js("() => MK.graph.data && MK.graph.data.found")
        self.assertIsNotNone(done)
        self.basis_agrees(self.readable(), "path")
        self.assertEqual([], self.bad[n0:], "R1: a response >= 500")


if __name__ == "__main__":
    unittest.main()
