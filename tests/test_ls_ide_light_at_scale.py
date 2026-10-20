"""0.0.25 lane S — the IDE stays light and answers on a 1M-line repo.

A scale run (a generated ~1M-line Python+TS repo, 7k files, one megahub function) found, in the IDE
layer: the Review page's per-file impact on a hub never finished, the abandoned calls filled the one
thread pool every route shared, and then the status bar, the tree and the state all hung — and
SIGTERM could not stop the server; the first ⌘P re-parsed every file (17–19 s) and every ⌘P after
3 s idle re-walked the repo; a ripple kept asking the graph after its picture was full; capped lists
(untested chains, impact's changed symbols) presented the cap as the answer; a common-word search
buffered rg's whole output; an unknown run's plan answered 500; and the status bar named the
configured graph while every answer came from the floor. What must be true for the user, each:

  * a heavy question that runs long answers "timed out — <what to do>" (503), not a 500 or silence;
  * the status bar, the tree and the state answer while heavy questions are stuck;
  * a request whose browser left stops its work; identical requests in flight share one answer;
  * SIGTERM stops the server promptly even with heavy work running;
  * `python -m mokata.ide.server` (how `mokata ide` starts it) answers an unknown run with 400;
  * the first ⌘P reads the graph's own index (no re-parse); ⌘P never waits on a building index —
    it says the symbols are loading; idle never re-walks; a change signal brings new files/symbols;
  * a ripple stops asking once full and says what to do (true caller count, Focus — not "lower the
    depth" when even depth 1 does not fit); the untested-chain list and impact's symbol cap are
    disclosed;
  * a common-word search stops rg at the cap, in path order;
  * an adopted graph that is failing is named degraded, with the floor that answered;
  * the IDE's per-repo caches are bounded; the page caches impact by file fingerprint.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
import asyncio
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata.ide import nav, search, visuals as V
from mokata.init import init_repo

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src")
STATIC = os.path.join(SRC, "mokata", "ide", "static")
TOK = "tok-lane-s"


def _silent(*_a, **_k):
    pass


def _write(d, rel, text):
    p = os.path.join(d, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)


def _repo(d, extra=None):
    init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
    _write(d, "src/app/core.py", "def parse(text):\n    return text.split(',')\n\n\n"
                                 "def total(text):\n    return len(parse(text))\n")
    _write(d, "tests/test_core.py",
           "from app.core import parse\n\n\ndef test_parse():\n    assert parse('a') == ['a']\n")
    for rel, text in (extra or {}).items():
        _write(d, rel, text)


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Live:
    """The real app on a real socket (uvicorn in a thread), so concurrency and disconnects are
    the real thing, not a test client's simulation."""

    def __init__(self, root):
        import uvicorn
        from mokata.ide.server import AppState, build_app
        self.port = _free_port()
        self.state = AppState(root, TOK, self.port, None, False)
        cfg = uvicorn.Config(build_app(self.state), host="127.0.0.1", port=self.port,
                             log_level="warning", lifespan="off", timeout_graceful_shutdown=1)
        self.server = uvicorn.Server(cfg)
        self.thread = threading.Thread(target=lambda: asyncio.run(self.server.serve()),
                                       daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"
        self.headers = {"x-mokata-token": TOK}
        for _ in range(100):
            try:
                if self.get("/api/ping", timeout=1).status_code == 200:
                    return
            except Exception:  # noqa: BLE001 — not up yet
                time.sleep(0.05)
        raise AssertionError("server did not start")

    def get(self, path, timeout=10.0):
        import httpx
        return httpx.get(self.base + path, headers=self.headers, timeout=timeout)

    def abandon(self, path, timeout):
        """Ask, and give up after `timeout` (a page navigating away / a refresh)."""
        import httpx
        try:
            httpx.get(self.base + path, headers=self.headers, timeout=timeout)
        except httpx.TimeoutException:
            pass

    def stop(self):
        self.state.close()
        self.server.should_exit = True
        self.thread.join(5)


class _Slow:
    """A stand-in heavy question: runs until cancelled (checking like nav.run does) or `cap` s."""

    def __init__(self, cap=8.0):
        self.cap = cap
        self.calls = 0
        self.started = threading.Event()
        self.stopped_by_cancel = threading.Event()

    def __call__(self, *_a):
        self.calls += 1
        self.started.set()
        end = time.monotonic() + self.cap
        try:
            while time.monotonic() < end:
                nav.check_abandoned()
                time.sleep(0.01)
        except nav.Abandoned:
            self.stopped_by_cancel.set()
            raise
        return {"sample": False, "path": "x", "symbols": [], "status": "covered"}


class HeavyQuestionsNeverStallThePage(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="mk-lane-s-")
        _repo(cls.root)

    @classmethod
    def tearDownClass(cls):
        nav.close_layers()
        shutil.rmtree(cls.root, True)

    def setUp(self):
        self.live = _Live(self.root)
        self.addCleanup(self.live.stop)

    def test_a_slow_impact_answers_timed_out_with_what_to_do(self):
        from mokata.ide import server
        slow = _Slow(cap=5.0)
        self.live.state.source.impact = slow
        with mock.patch.object(server, "HEAVY_DEADLINE_SECS", 0.5):
            t0 = time.monotonic()
            r = self.live.get("/api/impact?path=src/app/core.py&lines=1-3")
            took = time.monotonic() - t0
        self.assertEqual(503, r.status_code, r.text)
        body = r.json()
        self.assertTrue(body["timed_out"])
        self.assertIn("Focus", body["note"])
        self.assertIn("src/app/core.py", body["note"])
        self.assertLess(took, 3.0, "the deadline, not the work, decides when the page hears")

    def test_light_routes_answer_while_heavy_questions_are_stuck(self):
        self.live.state.source.impact = _Slow(cap=10.0)
        threads = [threading.Thread(target=self.live.abandon,
                                    args=(f"/api/impact?path=src/app/core.py&lines={i}", 0.3))
                   for i in range(1, 9)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        for ep in ("/api/caps", "/api/tree", "/api/state"):
            with self.subTest(ep=ep):
                t0 = time.monotonic()
                r = self.live.get(ep, timeout=4)
                self.assertEqual(200, r.status_code)
                self.assertLess(time.monotonic() - t0, 3.0)

    def test_a_request_whose_browser_left_stops_its_work(self):
        from mokata.ide import server
        slow = _Slow(cap=10.0)
        self.live.state.source.impact = slow
        with mock.patch.object(server, "HEAVY_DEADLINE_SECS", 30.0):
            self.live.abandon("/api/impact?path=src/app/core.py&lines=1-2", 0.4)
            self.assertTrue(slow.started.is_set())
            self.assertTrue(slow.stopped_by_cancel.wait(3.0),
                            "the work kept running for a client that had gone")

    def test_identical_questions_in_flight_share_one_answer(self):
        slow = _Slow(cap=0.6)
        self.live.state.source.impact = slow
        got = []
        ts = [threading.Thread(target=lambda: got.append(
            self.live.get("/api/impact?path=src/app/core.py&lines=1-2").status_code))
            for _ in range(3)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual([200, 200, 200], got)
        self.assertEqual(1, slow.calls, "a refresh re-asking must join the running answer")


_SIGTERM_SCRIPT = """
import sys, time
sys.path.insert(0, sys.argv[1])
from mokata.ide import server, source
source.RealSource.impact = lambda self, path, lines: time.sleep(1000)
server.serve(sys.argv[2], port=int(sys.argv[3]), idle_secs=None, token=sys.argv[4])
"""


class TheServerStopsWhenAsked(unittest.TestCase):

    def _start(self, argv, port):
        env = dict(os.environ, PYTHONPATH=os.path.abspath(SRC))
        p = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: p.poll() is None and (p.kill(), p.wait()))
        import httpx
        for _ in range(200):
            try:
                if httpx.get(f"http://127.0.0.1:{port}/api/ping", timeout=1,
                             headers={"x-mokata-token": TOK}).status_code == 200:
                    return p
            except Exception:  # noqa: BLE001 — not up yet
                time.sleep(0.05)
        self.fail("server did not start")

    def test_sigterm_and_ctrl_c_exit_promptly_with_heavy_work_in_flight(self):
        for sig in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(signal=sig.name):
                self._stops_on(sig)

    def _stops_on(self, sig):
        root = tempfile.mkdtemp(prefix="mk-lane-s-term-")
        self.addCleanup(shutil.rmtree, root, True)
        script = os.path.join(root, "run_server.py")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(_SIGTERM_SCRIPT)
        port = _free_port()
        p = self._start([sys.executable, script, os.path.abspath(SRC), root, str(port), TOK], port)
        import httpx

        def ask(i):
            try:
                httpx.get(f"http://127.0.0.1:{port}/api/impact?path=a.py&lines={i}",
                          headers={"x-mokata-token": TOK}, timeout=20)
            except httpx.HTTPError:
                pass                                            # the server went away: expected
        for i in range(3):
            threading.Thread(target=ask, args=(i,), daemon=True).start()
        time.sleep(0.8)
        t0 = time.monotonic()
        p.send_signal(sig)
        try:
            p.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.fail(f"{sig.name} did not stop the server within 15 s with heavy work in flight")
        self.assertLess(time.monotonic() - t0, 8.0)

    def test_started_as_a_module_an_unknown_run_is_a_400(self):
        root = tempfile.mkdtemp(prefix="mk-lane-s-plan-")
        self.addCleanup(shutil.rmtree, root, True)
        init_repo(root=root, profile="standard", assume_yes=True, out=_silent)
        port = _free_port()
        self._start([sys.executable, "-m", "mokata.ide.server", "--root", root, "--port", str(port),
                     "--token", TOK, "--idle", "0"], port)
        import httpx
        r = httpx.get(f"http://127.0.0.1:{port}/api/plan?run=nonexistent&source=local",
                      headers={"x-mokata-token": TOK}, timeout=10)
        self.assertEqual(400, r.status_code, r.text)
        self.assertEqual("BadRequest", r.json()["error"])


class QuickOpenAtScale(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mk-lane-s-qo-")
        self.addCleanup(shutil.rmtree, self.root, True)
        _repo(self.root)
        self.addCleanup(nav.close_layers)

    def test_the_first_quick_open_reads_the_graphs_index_not_a_reparse(self):
        from mokata.knowledge import ast_backend as AB
        nav.run(self.root, "defs", "total")                      # the graph has answered once
        real = AB.parse_source
        parses = []

        def counting(src):
            parses.append(1)
            return real(src)
        with mock.patch.object(AB, "parse_source", counting):
            r = nav.quick_open(self.root, "@tot", [])
        self.assertEqual("total", r["symbols"][0]["name"])
        self.assertEqual("ready", r["symbols_state"])
        self.assertEqual([], parses, "⌘P re-parsed files the graph had already indexed")

    def test_quick_open_never_waits_on_a_building_index(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        real = nav._Symbols._build

        def slow_build(sx, done, drop):
            gate.wait(5)
            real(sx, done, drop)
        with mock.patch.object(nav._Symbols, "_build", slow_build), \
                mock.patch.object(nav, "QUICK_WAIT_SECS", 0.2):
            t0 = time.monotonic()
            r = nav.quick_open(self.root, "core", [])
            took = time.monotonic() - t0
        self.assertLess(took, 1.5, "⌘P waited for the symbol index")
        self.assertEqual("building", r["symbols_state"])
        self.assertIn("loading", r["symbols_note"])
        self.assertIn("src/app/core.py", [f["path"] for f in r["files"]],
                      "files answer while symbols load")

    def test_idle_does_not_rewalk_the_repo(self):
        walks = []
        real = nav.walk_files

        def counting(root):
            walks.append(root)
            return real(root)
        now = [1000.0]
        with mock.patch.object(nav, "walk_files", counting), \
                mock.patch.object(nav.time, "monotonic", lambda: now[0]):
            nav.quick_open(self.root, "core", [])
            n = len(walks)
            now[0] += 5.0                                       # past the old 3 s TTL
            nav.quick_open(self.root, "core", [])
            nav.tree(self.root)                                 # the explorer reads the same list
            time.sleep(0.3)                                     # a background walk would land
        self.assertEqual(n, len(walks), "5 s of idle re-walked the repo")

    def test_a_change_signal_brings_new_files_and_their_symbols(self):
        nav.quick_open(self.root, "@tot", [])
        _write(self.root, "src/app/fresh.py", "def brand_new_helper():\n    return 1\n")
        nav.note_change(self.root)
        r = nav.quick_open(self.root, "fresh", [])
        self.assertIn("src/app/fresh.py", [f["path"] for f in r["files"]])
        nav.symbol_index(self.root)                              # wait for the rebuild
        r = nav.quick_open(self.root, "@brand_new", [])
        self.assertEqual("brand_new_helper", r["symbols"][0]["name"])

    def test_per_repo_caches_are_bounded(self):
        roots = []
        for _ in range(nav.MAX_ROOTS * 3):
            d = tempfile.mkdtemp(prefix="mk-lane-s-b-")
            self.addCleanup(shutil.rmtree, d, True)
            _write(d, "a.py", "def a():\n    pass\n")
            roots.append(d)
            nav.files_of(d)
        self.assertLessEqual(len(nav._PER_ROOT), 2 * nav.MAX_ROOTS)


def _hub_repo(d, direct, second=True, tests=False):
    """`hub` called by `direct` callers c<i>, each called by its own outer o<i>."""
    files = {"src/app/hub.py": "def hub(x):\n    return x\n"}
    for i in range(direct):
        body = f"from app.hub import hub\n\n\ndef c{i}():\n    return hub({i})\n"
        if second:
            body += f"\n\ndef o{i}():\n    return c{i}()\n"
        files[f"src/app/m{i:03d}.py"] = body
    if tests:
        files["tests/test_hub.py"] = "from app.hub import hub\n\n\ndef test_hub():\n    hub(1)\n"
    init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
    for rel, text in files.items():
        _write(d, rel, text)


class TheGraphViewsSayWhatTheyLeftOut(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mk-lane-s-v-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(nav.close_layers)

    def test_a_full_ripple_stops_asking_the_graph(self):
        _hub_repo(self.root, 30)
        asked = []
        real = nav.run

        def counting(root, kind, target, depth=2, layer=None):
            asked.append((kind, target))
            return real(root, kind, target, depth, layer)
        with mock.patch.object(V, "MAX_NODES", 36), mock.patch.object(nav, "run", counting):
            g = V.ripple(self.root, "hub", 2)
        self.assertTrue(g["truncated"])
        callers_asked = [t for k, t in asked if k == "callers"]
        self.assertLessEqual(len(callers_asked), 8,
                             f"kept asking after the picture was full: {len(callers_asked)}")
        self.assertIn("depth 1 fits", g["truncation_note"])

    def test_a_megahub_at_depth_one_says_its_caller_count_and_points_at_focus(self):
        _hub_repo(self.root, 30, second=False)
        with mock.patch.object(V, "MAX_NODES", 10):
            g = V.ripple(self.root, "hub", 1)
        self.assertTrue(g["truncated"])
        note = g["truncation_note"]
        self.assertIn("30 direct callers", note)
        self.assertIn("Focus", note)
        self.assertNotIn("lower the depth", note)

    def test_the_untested_chain_list_discloses_its_cap(self):
        _hub_repo(self.root, 60, second=False)
        g = V.ripple(self.root, "hub", 1)
        self.assertEqual(V.MAX_CHAINS, len(g["untested_chains"]))
        # 60 call chains (c<i> → hub) + 60 import chains (m<i>.py → hub): all counted, 50 listed
        self.assertEqual(120, g["untested_chains_total"])
        self.assertTrue(g["untested_chains_exact"])
        js = open(os.path.join(STATIC, "graph.js"), encoding="utf-8").read()
        self.assertIn("untested_chains_total", js)

    def test_impact_discloses_its_symbol_cap(self):
        body = "".join(f"def f{i}():\n    return {i}\n\n\n" for i in range(nav.IMPACT_MAX_SYMBOLS + 4))
        _hub_repo(self.root, 0)
        _write(self.root, "src/app/many.py", body)
        lines = body.count("\n")
        out = nav.impact(self.root, "src/app/many.py", f"1-{lines}")
        self.assertEqual(nav.IMPACT_MAX_SYMBOLS, len(out["symbols"]))
        self.assertTrue(out["symbols_capped"])
        self.assertEqual(nav.IMPACT_MAX_SYMBOLS + 4, out["symbols_changed"])
        self.assertIn(f"of {nav.IMPACT_MAX_SYMBOLS + 4}", out["cap_note"])
        self.assertNotEqual("covered", out["status"],
                            "a file is never 'covered' on the strength of some of its changes")

    def test_the_rest_is_pending_and_calculated_on_request_never_unknown(self):
        """0.0.25 (Jas): "instead of impact unknown, request the impact calculation on demand".
        A capped file is `partial` with its remaining definitions `pending` BY NAME; asking for
        them by name calculates exactly those, uncapped by the automatic limit."""
        n = nav.IMPACT_MAX_SYMBOLS + 4
        body = "".join(f"def f{i}():\n    return {i}\n\n\n" for i in range(n))
        _hub_repo(self.root, 0)
        _write(self.root, "src/app/many.py", body)
        lines = f"1-{body.count(chr(10))}"
        first = nav.impact(self.root, "src/app/many.py", lines)
        pending = [p["name"] for p in first["pending"]]
        self.assertEqual(4, len(pending))
        self.assertEqual(n, len(first["symbols"]) + len(pending))
        # what was calculated decides (`untested` outranks); the cap alone never makes it unknown
        self.assertEqual("untested" if any(x["verdict"] == "untested" for x in first["symbols"])
                         else "partial", first["status"])
        rest = nav.impact(self.root, "src/app/many.py", lines, symbols=pending)
        self.assertEqual(sorted(pending), sorted(s["name"] for s in rest["symbols"]))
        self.assertEqual([], rest["pending"])
        self.assertTrue(rest["on_demand"])
        self.assertTrue(all(s["verdict"] in ("covered", "untested", "unknown") for s in rest["symbols"]))
        ignored = nav.impact(self.root, "src/app/many.py", lines, symbols=["not_changed_here"])
        self.assertEqual([], ignored["symbols"], "only changed definitions can be asked for")

    def test_a_capped_file_whose_calculated_part_is_covered_is_partial(self):
        n = nav.IMPACT_MAX_SYMBOLS + 3
        body = "".join(f"def f{i}():\n    return {i}\n\n\n" for i in range(n))
        _hub_repo(self.root, 0)
        _write(self.root, "src/app/many.py", body)
        calls = "".join(f"    f{i}()\n" for i in range(n))
        _write(self.root, "tests/test_many.py",
               "from app.many import " + ", ".join(f"f{i}" for i in range(n)) +
               "\n\n\ndef test_all():\n" + calls)
        out = nav.impact(self.root, "src/app/many.py", f"1-{body.count(chr(10))}")
        self.assertTrue(all(s["verdict"] == "covered" for s in out["symbols"]), out["symbols"])
        self.assertEqual("partial", out["status"],
                         "covered so far, the rest not calculated: partial — not unknown, not covered")

    def test_the_route_passes_the_names_and_the_page_offers_the_button(self):
        n = nav.IMPACT_MAX_SYMBOLS + 2
        body = "".join(f"def g{i}():\n    return {i}\n\n\n" for i in range(n))
        _hub_repo(self.root, 0)
        _write(self.root, "src/app/many.py", body)
        from starlette.testclient import TestClient
        from mokata.ide.server import AppState, build_app
        c = TestClient(build_app(AppState(self.root, "tok", 9999, None, False)),
                       base_url="http://127.0.0.1:9999")
        c.get("/pair?t=tok")
        lines = f"1-{body.count(chr(10))}"
        got = c.get(f"/api/impact?path=src/app/many.py&lines={lines}&symbols=g0,g9").json()
        self.assertEqual({"g0", "g9"}, {s["name"] for s in got["symbols"]})
        js = open(os.path.join(STATIC, "app.js"), encoding="utf-8").read()
        self.assertIn("&symbols=", js)
        self.assertIn("renderPendingImpact", js)

    def test_a_cut_blast_radius_never_proves_untested_and_callers_are_capped(self):
        from mokata.knowledge.query import BASIS_STRUCTURAL, QueryResult, Reference
        _hub_repo(self.root, 0)
        real = nav.run

        def fake(root, kind, target, depth=2, layer=None):
            if kind == "callers":
                return QueryResult(kind=kind, target=target, backend="ast", basis=BASIS_STRUCTURAL,
                                   references=[Reference(path=f"src/c{i}.py", line=1, symbol=f"c{i}")
                                               for i in range(nav.IMPACT_MAX_CALLERS + 50)])
            if kind == "blast_radius":
                r = QueryResult(kind=kind, target=target, backend="ast", basis=BASIS_STRUCTURAL,
                                references=[Reference(path=f"src/c{i}.py", line=1, symbol=f"c{i}")
                                            for i in range(300)])
                r.truncated, r.total = True, 85000       # the knowledge lane's cut fields
                return r
            return real(root, kind, target, depth, layer)
        with mock.patch.object(nav, "run", fake):
            out = nav.impact(self.root, "src/app/hub.py", "1-2")
        sym = out["symbols"][0]
        self.assertEqual("unknown", sym["verdict"], "a cut blast radius proved 'untested'")
        self.assertNotEqual("untested", out["status"])
        self.assertTrue(sym["blast"]["truncated"])
        self.assertEqual(85000, sym["blast"]["total"])
        self.assertIn("not determined", sym["blast"]["not_determined"])
        self.assertIn("300 of 85000", sym["blast"]["not_determined"])
        self.assertEqual(nav.IMPACT_MAX_CALLERS, len(sym["callers"]["references"]))
        self.assertEqual(nav.IMPACT_MAX_CALLERS + 50, sym["callers"]["references_total"])
        self.assertTrue(sym["callers"]["references_capped"])


@unittest.skipUnless(shutil.which("rg"), "rg is not on PATH")
class ACommonWordSearch(unittest.TestCase):

    def test_rg_is_stopped_at_the_cap_and_the_answer_is_in_path_order(self):
        d = tempfile.mkdtemp(prefix="mk-lane-s-rg-")
        self.addCleanup(shutil.rmtree, d, True)
        for name in ("e", "b", "d", "a", "c"):
            _write(d, f"src/{name}.py", "x = 1  # needle\n" * 400)
        procs = []
        real = subprocess.Popen

        def spy(*a, **k):
            p = real(*a, **k)
            procs.append(p)
            return p
        answers = []
        with mock.patch.object(search, "MAX_MATCHES", 50), \
                mock.patch.object(search.subprocess, "Popen", spy):
            for _ in range(3):
                answers.append(search.text(d, "needle"))
        self.assertEqual("rg", answers[0]["engine"])
        self.assertTrue(answers[0]["truncated"])
        self.assertEqual(["src/a.py"], [f["path"] for f in answers[0]["files"]])
        self.assertEqual(answers[0], answers[1])
        self.assertEqual(answers[0], answers[2])
        self.assertEqual(3, len(procs), "rg is read as a stream (Popen), not buffered whole")
        for p in procs:
            self.assertNotEqual(0, p.returncode, "rg ran to the end instead of being stopped")


class _FailingGraph:
    """An adopted graph whose every answer fails (the handshake broke)."""
    name = "code-review-graph"
    is_graph = True

    def __init__(self, root):
        self.root = root

    def query(self, kind, target, depth=1):
        from mokata.knowledge.query import BackendError
        raise BackendError("handshake failed")


class TheStatusBarNamesTheGraphThatAnswers(unittest.TestCase):

    def test_a_failing_adopted_graph_is_named_degraded_with_the_floor_that_answered(self):
        from starlette.testclient import TestClient
        from mokata.ide.server import AppState, build_app
        from mokata.knowledge import KnowledgeLayer
        from mokata.knowledge.ast_backend import AstBackend
        d = tempfile.mkdtemp(prefix="mk-lane-s-deg-")
        self.addCleanup(shutil.rmtree, d, True)
        self.addCleanup(nav.close_layers)
        _repo(d)
        layer = KnowledgeLayer(_FailingGraph(d), AstBackend(d))
        nav._LAYERS[os.path.abspath(d)] = (nav._manifest_key(d), layer)
        c = TestClient(build_app(AppState(d, "tok", 9999, None, False)),
                       base_url="http://127.0.0.1:9999")
        c.get("/pair?t=tok")
        with mock.patch("sys.stderr"):
            g = c.get("/api/graph?view=focus&symbol=total").json()
        self.assertIn("degraded, answering from ast", g["graph_note"])
        caps = c.get("/api/caps").json()
        self.assertTrue(caps["graph"].startswith("code-review-graph — degraded, answering from ast"),
                        caps["graph"])
        self.assertTrue(caps["graph_degraded"])
        layer.primary = AstBackend(d)                   # it recovers: the next answer is its own
        layer.primary.is_graph = True
        layer.primary.name = "code-review-graph"
        c.get("/api/graph?view=focus&symbol=parse")
        self.assertEqual("code-review-graph", c.get("/api/caps").json()["graph"])


class ThePageAsksImpactOncePerFileVersion(unittest.TestCase):

    def test_impact_is_cached_by_file_fingerprint_and_bounded(self):
        js = open(os.path.join(STATIC, "app.js"), encoding="utf-8").read()
        load = js[js.index("function loadImpacts("):js.index("MK.loadImpacts = loadImpacts")]
        self.assertIn("c.fp === fileFp(f)", load, "a refresh reuses an unchanged file's answer")
        self.assertIn("IMPACT_AHEAD", load, "background asks are bounded")
        self.assertIn("wantImpact(f);", js[js.index("function showFile("):], "the file opened is asked")
        self.assertIn("IMPACT_CACHE_MAX", js)


if __name__ == "__main__":
    unittest.main()
