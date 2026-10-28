"""0.0.26 P36 `SEARCH-AT-SCALE` — PRD §6 R16, R17, R18, R40 and G33 driven through the REAL page
(Playwright) on a multi-million-line repo.

Run it (not part of the unit suite — the bed is big):

    MOKATA_SCALE_K8S=/path/to/kubernetes  python tests/e2e_scale/test_p36_search_at_scale.py

The repo must be a kubernetes checkout. Meaning (R18) also needs the `[embeddings]` extra with its
model on disk; that test is skipped, saying why, when it is not. No code-review-graph index is
needed: search does not ask the graph. Measured numbers print as one line (`P36-SCALE {...}`).

What must be true for the user:
  R16  typing `NewController` paints its first hits <= 1 s after the query settles; a common word
       pages past the old 2,000 stop ("Load more"), every page appended;
  R17  typing `NewController` key by key sends <= 2 `/api/search` requests;
  R40  a second query within 300 ms aborts the first fetch; the header never shows the old query;
  R18  10 meaning queries: no response >= 500, warm ones <= 2 s;
  G33  a hit inside a >1 MB generated file is found, and the file says its size.

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


def _why_skip():
    if not REPO or not os.path.isdir(REPO):
        return "MOKATA_SCALE_K8S is not set to a kubernetes checkout (the big-repo bed is absent)"
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return "Playwright is not installed in this interpreter"
    return ""


SKIP = _why_skip()


@unittest.skipIf(SKIP, SKIP)
class SearchAtScale(unittest.TestCase):
    M = {}

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
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
        cls.bad, cls.requests, cls.aborted = [], [], []
        cls.page.on("response", lambda r: cls.bad.append((r.status, r.url)) if r.status >= 500
                    else None)
        cls.page.on("request", lambda r: cls.requests.append((time.monotonic(), r.url,
                                                              r.post_data or "")))
        cls.page.on("requestfailed", lambda r: cls.aborted.append((r.url, r.failure)))
        cls.page.goto(url)
        cls.page.wait_for_load_state("load")
        cls.page.wait_for_function("() => window.MK && MK.wireSearch && MK.jobs", timeout=30000)
        cls.page.wait_for_function("() => MK.ui.restored === true", timeout=120000)
        time.sleep(1)

    @classmethod
    def tearDownClass(cls):
        print("P36-SCALE " + json.dumps(cls.M, sort_keys=True), flush=True)
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

    def header(self):
        return self.js("() => document.getElementById('sr-word-h').textContent")

    def wait(self, expr, secs, poll=0.02):
        t0 = time.monotonic()
        while time.monotonic() - t0 < secs:
            v = self.js(expr)
            if v:
                return time.monotonic() - t0, v
            time.sleep(poll)
        return None, None

    def fresh(self):
        self.js("() => MK.openSearch('')")
        self.page.fill("#sq", "")
        self.js("() => document.getElementById('sq').oninput()")
        time.sleep(0.5)

    def searches(self, since):
        """Questions ASKED — a P32 job's re-ask for its finished answer (`"job":`) is the same
        question's delivery, not a new request."""
        return [r for r in self.requests if r[0] >= since and "/api/search" in r[1]
                and '"job":' not in r[2]]

    def _type_and_paint(self):
        self.fresh()
        t0 = time.monotonic()
        self.page.click("#sq")
        self.page.keyboard.type("NewController", delay=70)
        took, _ = self.wait("() => /NewController · ≥?\\d+ hit/.test(document.getElementById("
                            "'sr-word-h').textContent)", 60)
        self.assertIsNotNone(took, "no hits painted")
        time.sleep(1.0)
        return t0, took

    def test_1_R16_R17_typing_sends_one_request_and_paints_within_a_second(self):
        # the first search of a fresh server walks the repo's file list once (cold): said apart
        _t, cold = self._type_and_paint()
        self.M["R16_cold_first_paint_after_last_key_s"] = round(cold, 3)
        runs, counts = [], []
        for _ in range(5):
            t0, took = self._type_and_paint()
            runs.append(took)
            counts.append(len(self.searches(t0)))
        med = sorted(runs)[2]
        # first paint measured from the moment the query settled (the 250 ms pause)
        self.M["R16_first_paint_after_settle_s_median5"] = round(med - 0.25, 3)
        self.M["R17_search_requests_while_typing"] = counts
        self.assertLessEqual(med - 0.25, 1.0)
        self.assertLessEqual(max(counts), 2, counts)
        n = len(self.searches(0))
        self.page.keyboard.press("Enter")
        time.sleep(0.5)
        self.assertEqual(n, len(self.searches(0)), "Enter re-asked a settled query")

    def test_2_R16_a_common_word_pages_past_2000(self):
        self.fresh()
        self.page.fill("#sq", "err")
        self.js("() => document.getElementById('sq').onkeydown({key: 'Enter', preventDefault() {}})")
        took, _ = self.wait("() => !!document.querySelector('#sr-word-list .sr-more')", 30)
        self.assertIsNotNone(took, "no Load more on a page with more")
        self.M["R16_common_word_page1_s"] = round(took, 3)
        clicks = []
        for _ in range(2):
            t1 = time.monotonic()
            self.page.click("#sr-word-list .sr-more")
            took, _ = self.wait("() => !!document.querySelector('#sr-word-list .sr-more:not([disabled])')",
                                60)
            self.assertIsNotNone(took)
            clicks.append(round(time.monotonic() - t1, 3))
        self.M["R16_load_more_s"] = clicks
        h = self.header()
        n = int(re.search(r"≥?(\d+) hit", h).group(1))
        self.M["R16_hits_reachable_after_2_more"] = n
        self.assertGreater(n, 2000, h)

    def test_3_R40_a_second_query_aborts_the_first_and_never_shows_it(self):
        self.fresh()
        a0 = len(self.aborted)
        self.page.click("#sq")
        self.page.keyboard.type("WaitForCacheSync")
        self.page.keyboard.press("Enter")
        time.sleep(0.05)
        self.page.fill("#sq", "NewSharedInformerFactory")
        self.page.keyboard.press("Enter")
        wrong = []
        t0 = time.monotonic()
        while time.monotonic() - t0 < 30:
            h = self.header()
            if "WaitForCacheSync" in h:
                wrong.append(h)
            if "NewSharedInformerFactory" in h and "hit(" in h and "so far" not in h:
                break
            time.sleep(0.03)
        aborted = [u for u, _f in self.aborted[a0:] if "/api/search" in u]
        self.M["R40_aborted_search_fetches"] = len(aborted)
        self.assertGreaterEqual(len(aborted), 1)
        self.assertEqual([], wrong)

    def test_4_G33_a_hit_in_a_large_generated_file_says_its_size(self):
        self.fresh()
        self.page.fill("#sq", "func schema_k8sio_api_core_v1_PodSpec(")
        self.js("() => document.getElementById('sq').onkeydown({key: 'Enter', preventDefault() {}})")
        took, note = self.wait("() => { const b = document.querySelector('#sr-word-list .sr-big');"
                               " return b && b.textContent; }", 30)
        self.M["G33_note"] = note
        self.assertIsNotNone(took, "the >1 MB file was not searched, or not labelled")
        self.assertIn("MB", note)

    def test_5_R18_ten_meaning_queries_never_a_500(self):
        sys.path.insert(0, SRC)
        from mokata.ide import search
        st = search.status()
        if not st.get("available"):
            self.skipTest(f"meaning search unavailable here: {st.get('state')}")
        self.fresh()
        n0 = len(self.bad)
        self.js("() => { if (!MK.search.meaning) document.querySelector('[data-stoggle=meaning]').click(); }")
        qs = ["start the informer and wait for cache sync", "retry with exponential backoff",
              "validate the pod spec", "admission webhook", "garbage collect orphaned objects",
              "parse a resource quantity", "leader election lease", "rate limited work queue",
              "serialize to protobuf", "mount a volume into a container"]
        times = []
        for q in qs:
            t1 = time.monotonic()
            self.page.fill("#sq", q)
            self.js("() => document.getElementById('sq').onkeydown({key: 'Enter', preventDefault() {}})")
            took, state = self.wait("""() => { const l = document.getElementById('sr-meaning-list');
                const s = document.getElementById('sr-meaning-state');
                return !l.classList.contains('stale') && (l.children.length || !s.hidden) ?
                  (s.hidden ? 'ranked' : s.textContent.slice(0, 60)) : null; }""", 600, 0.05)
            times.append(round(time.monotonic() - t1, 3))
            self.assertIsNotNone(took, q)
        self.M["R18_meaning_query_s"] = times
        self.M["R18_responses_ge_500"] = len(self.bad) - n0
        self.assertEqual([], self.bad[n0:])
        warm = sorted(times[1:])[len(times[1:]) // 2]
        self.M["R18_warm_median_s"] = warm
        self.assertLessEqual(warm, 2.0)


if __name__ == "__main__":
    unittest.main()
