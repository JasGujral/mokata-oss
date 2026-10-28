"""0.0.26 P32 `IDE-JOBS` — long IDE work is a visible job: progress, a Cancel that really stops
it, partial results, no deadline (Jas 2026-10-21/22, overruling U10's 20 s cap; PRD §5.3, R1-R5,
R35, R39).

Business rules — what must be true for the user, each:
  * The same question is ONE job (dedupe): two pages, a refresh, a re-ask all join it; a finished
    answer is kept for the re-ask until the repo changes.
  * Cancel stops the WORK, not just the wait: the walks stop at their next file (the grep floor's
    walk, the meaning index), a subprocess (rg, code-review-graph) is killed — after Cancel the
    process burns no more CPU for that job.
  * While a job runs the page hears about it at least once a second (`job` events), and at idle it
    hears nothing (no ticker, no events).
  * A ripple's rings arrive as PARTIAL answers before the job ends; a word search streams its hits.
  * A query nobody wants any more (its page left, its request was superseded) stops; an index
    build keeps going with nobody watching.
  * A graph question that FAILS is seen by Health: the code-graph row says "graph views failing"
    with the reason, until one succeeds (R35).
  * Rebuilding a BUILT code graph never leaves it incomplete: it is built beside the one in use and
    swapped in whole; a cancelled or failed rebuild leaves the old graph COMPLETE (R39).
  * Job progress never pushes session events off the event bus's ring.
  * No IDE route has a deadline any more (R1: `HEAVY_DEADLINE` is gone).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
import asyncio
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata.ide import jobs as J
from mokata.knowledge import cancel as C

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")


def _write(d, rel, text):
    p = os.path.join(d, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


class _Reg(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.reg = J.JobRegistry()
        self.reg.attach(lambda e: self.events.append((time.monotonic(), e["job"])))
        self.addCleanup(self.reg.close)

    def spin(self, stop_after=30.0, started=None):
        """A job body that works until cancelled (checking like the walks do) or `stop_after`."""
        def work():
            if started is not None:
                started.set()
            end = time.monotonic() + stop_after
            while time.monotonic() < end:
                C.checkpoint()
                sum(range(2000))             # real CPU, like a parse
            return {"ok": True}
        return work


class OneQuestionIsOneJob(_Reg):

    def test_the_same_question_joins_the_running_job_and_a_different_one_does_not(self):
        calls = []
        gate = threading.Event()

        def work():
            calls.append(1)
            gate.wait(5)
            return {"n": len(calls)}
        a = self.reg.start("graph.view", {"symbol": "f", "depth": 2}, work)
        b = self.reg.start("graph.view", {"depth": 2, "symbol": "f"}, work)
        c = self.reg.start("graph.view", {"symbol": "g", "depth": 2}, work)
        self.assertIs(a, b, "same kind + args (any key order) is the same job")
        self.assertIsNot(a, c)
        gate.set()
        a.finished.wait(5)
        c.finished.wait(5)
        self.assertEqual(2, len(calls))

    def test_a_finished_answer_goes_to_who_waited_and_a_new_ask_is_computed_afresh(self):
        """The page that waited fetches the answer by the job's id; anyone asking the same
        question NOW gets a new computation (an edit since must be seen)."""
        calls = []
        a = self.reg.start("impact", {"path": "x"}, lambda: calls.append(1) or {"v": 1})
        a.finished.wait(5)
        self.assertIs(a, self.reg.answer(a.id, "impact", {"path": "x"}))
        self.assertIsNone(self.reg.answer(a.id, "impact", {"path": "y"}),
                          "a job id never answers another question")
        b = self.reg.start("impact", {"path": "x"}, lambda: calls.append(1) or {"v": 2})
        b.finished.wait(5)
        self.assertEqual({"v": 2}, b.result)
        self.assertEqual(2, len(calls))
        self.assertNotEqual(a.id, b.id, "a re-run is a new id")


class CancelStopsTheWork(_Reg):

    def _cpu(self):
        t = os.times()
        return t.user + t.system

    def test_cancel_is_said_at_once_and_the_cpu_drops_to_zero(self):
        started = threading.Event()
        job = self.reg.start("graph.view", {"symbol": "hub"}, self.spin(started=started))
        self.assertTrue(started.wait(5))
        time.sleep(0.3)
        t0 = time.monotonic()
        self.reg.cancel(job.id)
        self.assertEqual("cancelled", job.state)
        self.assertLess(time.monotonic() - t0, 1.0)
        time.sleep(0.3)                              # the walk reaches its next checkpoint
        c0, w0 = self._cpu(), time.monotonic()
        time.sleep(1.0)
        busy = (self._cpu() - c0) / (time.monotonic() - w0)
        self.assertLess(busy, 0.15, f"the cancelled job still burns {busy:.0%} of a CPU")

    def test_the_grep_floors_walk_stops_at_its_next_file(self):
        from mokata.knowledge.grep_backend import GrepBackend
        root = tempfile.mkdtemp(prefix="mk-p32-grep-")
        self.addCleanup(shutil.rmtree, root, True)
        for i in range(400):
            _write(root, f"pkg/m{i}.py", "".join(f"def f{i}_{k}():\n    return g()\n"
                                                 for k in range(50)))
        reads = []
        g = GrepBackend(root)
        real = g._read_file

        def counting(path):
            reads.append(path)
            if len(reads) == 20:
                flag.set()                           # cancel mid-walk
            return real(path)
        g._read_file = counting
        flag = threading.Event()
        with C.scope(flag), self.assertRaises(C.Cancelled):
            g.query("callers", "g")
        self.assertLessEqual(len(reads), 21, "the walk read on after it was cancelled")

    def test_a_subprocess_is_killed_on_cancel(self):
        flag = threading.Event()
        proc = subprocess.Popen([sys.executable, "-c", "while True: pass"],
                                stdin=subprocess.DEVNULL)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        with C.scope(flag), C.kill_on_cancel(proc):
            flag.set()
            for _ in range(30):
                if proc.poll() is not None:
                    break
                time.sleep(0.05)
        self.assertIsNotNone(proc.poll(), "the child kept running after Cancel")

    def test_word_search_kills_rg_on_cancel(self):
        from mokata.ide import nav, search
        root = tempfile.mkdtemp(prefix="mk-p32-rg-")
        self.addCleanup(shutil.rmtree, root, True)
        _write(root, "a.py", "x = 1\n")
        fake = os.path.join(root, "fake_rg.py")
        pidfile = os.path.join(root, "pid")
        _write(root, "fake_rg.py",
               "import os, sys, time\n"
               f"open({pidfile!r}, 'w', encoding='utf-8').write(str(os.getpid()))\n"
               "while True:\n    time.sleep(0.05)\n")
        if os.name == "nt":
            self.skipTest("a script stands in for rg here; POSIX shebang needed")
        exe = os.path.join(root, "rg")
        with open(fake, encoding="utf-8") as fh:
            _write(root, "rg", f"#!{sys.executable}\n" + fh.read())
        os.chmod(exe, 0o755)
        nav.note_change(root)
        with mock.patch.object(search, "rg_path", return_value=exe):
            job = self.reg.start("search.text", {"q": "x"},
                                 lambda: search.text(root, "x", "word"))
            for _ in range(100):
                if os.path.exists(pidfile) and open(pidfile, encoding="utf-8").read():
                    break
                time.sleep(0.05)
            pid = int(open(pidfile, encoding="utf-8").read())
            self.reg.cancel(job.id)
            gone = False
            for _ in range(40):
                try:
                    os.kill(pid, 0)
                except OSError:
                    gone = True
                    break
                time.sleep(0.05)
            # a killed child stays a zombie until reaped; the reader reaps it — give it a moment
        self.assertTrue(gone or _zombie(pid), "rg was not killed by Cancel")


def _zombie(pid):
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            return fh.read().split()[2] == "Z"
    except OSError:
        return True


class ThePageHearsWhileItRunsAndNothingAtIdle(_Reg):

    def test_a_running_job_is_announced_at_least_once_a_second_and_idle_is_silent(self):
        job = self.reg.start("graph.view", {"symbol": "s"}, self.spin(stop_after=2.2))
        job.finished.wait(10)
        mine = [t for t, e in self.events if e["id"] == job.id]
        self.assertGreaterEqual(len(mine), 4, "fewer than 1 event per second while running")
        gaps = [b - a for a, b in zip(mine, mine[1:])]
        self.assertLessEqual(max(gaps), 1.0 + 0.2, gaps)
        self.assertEqual("done", self.events[-1][1]["state"])
        n = len(self.events)
        time.sleep(1.5)
        self.assertEqual(n, len(self.events), "events at idle")
        self.assertIsNone(self.reg._ticker, "the ticker outlived the last job")

    def test_the_event_carries_the_contract_fields(self):
        job = self.reg.start("index.meaning", {"root": "/r"}, lambda: (
            C.report(done=3, total=10, unit="files", note="embedding"), time.sleep(0.6))[1],
            label="meaning index", scope="r")
        job.finished.wait(5)
        seen = [e for _t, e in self.events if e["id"] == job.id]
        mid = next(e for e in seen if e.get("done") == 3)
        for k in ("id", "kind", "state", "done", "total", "unit", "elapsed", "partial_ref",
                  "note", "error", "eta", "label", "scope"):
            self.assertIn(k, mid)
        self.assertEqual((3, 10, "files"), (mid["done"], mid["total"], mid["unit"]))
        self.assertIsNotNone(mid["eta"], "n/N known: an ETA is given")


class PartialAnswers(_Reg):

    def setUp(self):
        super().setUp()
        from mokata.init import init_repo
        from mokata.ide import nav
        self.root = tempfile.mkdtemp(prefix="mk-p32-ripple-")
        self.addCleanup(shutil.rmtree, self.root, True)
        init_repo(root=self.root, profile="standard", assume_yes=True, out=lambda *a, **k: None)
        _write(self.root, "src/app/core.py",
               "def hub():\n    return 1\n\n\ndef a():\n    return hub()\n\n\n"
               "def b():\n    return a()\n\n\ndef c():\n    return b()\n")
        nav.close_layers()
        self.addCleanup(nav.close_layers)

    def test_the_ripple_streams_its_rings_before_it_is_done(self):
        from mokata.ide import visuals as V
        partials = []
        with C.reporting(lambda **kw: kw.get("partial") is not None
                         and partials.append(kw["partial"])):
            final = V.ripple(self.root, "hub", 3)
        rings = [p["rings_done"] for p in partials]
        self.assertEqual([0, 1, 2, 3], rings[:4], rings)
        self.assertTrue(all(p["partial"] for p in partials))
        ring1 = next(p for p in partials if p["rings_done"] == 1)
        self.assertIn("sym:a", {n["id"] for n in ring1["nodes"]}, "ring 1 drawn before the end")
        self.assertNotIn("partial", final, "the final answer is not marked partial")

    def test_a_job_reports_partial_and_the_first_partial_is_announced_at_once(self):
        job = self.reg.start("search.text", {"q": "z"}, lambda: (
            C.report(partial={"files": [], "total": 120, "partial": True}, note="≥120 so far"),
            time.sleep(1.2), {"total": 300})[2])
        time.sleep(0.25)
        self.assertEqual("partial", job.state)
        self.assertTrue(any(e["state"] == "partial" for _t, e in self.events),
                        "the first partial was not announced before the tick")
        job.finished.wait(5)
        self.assertEqual("done", job.state)
        self.assertIsNone(job.partial, "the result supersedes the partial")


class WhoStillWantsIt(_Reg):

    def test_a_query_nobody_wants_stops_and_an_index_build_keeps_going(self):
        with mock.patch.object(J, "RECONNECT_S", 0.3):
            q = self.reg.start("graph.view", {"symbol": "q"}, self.spin(), client="page-1")
            b = self.reg.start("index.meaning", {"root": "/r"}, self.spin(), client="page-1")
            time.sleep(0.2)
            self.reg.client_gone("page-1")
            q.finished.wait(3)
        self.assertEqual("cancelled", q.state)
        self.assertIn(b.state, ("queued", "running"))
        self.reg.cancel(b.id)

    def test_a_page_whose_stream_reconnects_keeps_its_jobs(self):
        with mock.patch.object(J, "RECONNECT_S", 0.6):
            q = self.reg.start("graph.view", {"symbol": "rc"}, self.spin(), client="page-2")
            self.reg.client_gone("page-2")
            time.sleep(0.2)
            self.reg.client_back("page-2")
            time.sleep(1.2)
        self.assertNotEqual("cancelled", q.state, "a reconnecting page lost its job")
        self.reg.cancel(q.id)

    def test_an_anonymous_ask_holds_a_lease_then_lets_go(self):
        with mock.patch.object(J, "GRACE_S", 0.4):
            job = self.reg.start("graph.view", {"symbol": "lease"}, self.spin())
            time.sleep(0.2)
            self.assertNotEqual("cancelled", job.state)
            job.finished.wait(3)
        self.assertEqual("cancelled", job.state)

    def test_a_superseded_request_leaving_keeps_the_job_for_another_watcher(self):
        job = self.reg.start("graph.view", {"symbol": "two"}, self.spin(), client="p1")
        self.reg.watch(job, "p2")
        self.reg.leave(job, "p1")
        time.sleep(0.2)
        self.assertNotEqual("cancelled", job.state, "p2 still wants it")
        self.reg.leave(job, "p2")
        self.assertEqual("cancelled", job.state)


class FailingAtUseIsSeenByHealth(_Reg):

    def test_a_failed_graph_job_marks_the_code_graph_row_degraded_until_one_succeeds(self):
        from mokata.ide import health
        from mokata.errors import MokataError

        def explode():
            raise MokataError("backend exploded")
        bad = self.reg.start("graph.view", {"symbol": "x"}, explode, label="graph ripple · x")
        bad.finished.wait(5)
        self.assertEqual("failed", bad.state)
        rows = [{"key": "graph", "label": "code graph (ast)", "state": health.OK,
                 "detail": "fine", "fix": "", "action": "", "action_label": ""}]
        health._failing_at_use(rows, self.reg)
        self.assertEqual(health.DEGRADED, rows[0]["state"])
        self.assertIn("graph views failing", rows[0]["detail"])
        self.assertIn("backend exploded", rows[0]["detail"])
        ok = self.reg.start("graph.view", {"symbol": "y"}, lambda: {"ok": True})
        ok.finished.wait(5)
        rows[0].update(state=health.OK, detail="fine")
        health._failing_at_use(rows, self.reg)
        self.assertEqual(health.OK, rows[0]["state"], "a later success clears it")


def _built_db(path, marker):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO metadata VALUES ('last_updated', '2026-10-22')")
    con.execute("INSERT INTO metadata VALUES ('build_state', 'complete')")
    con.execute("CREATE TABLE marker (v TEXT)")
    con.execute("INSERT INTO marker VALUES (?)", (marker,))
    con.commit()
    con.close()


def _marker(path):
    con = sqlite3.connect(path)
    try:
        return con.execute("SELECT v FROM marker").fetchone()[0]
    finally:
        con.close()


class ARebuildNeverLeavesTheGraphIncomplete(unittest.TestCase):
    """R39 — rebuild beside, swap whole; a cancel or failure keeps the old graph COMPLETE."""

    def setUp(self):
        from mokata.init import init_repo
        from mokata.knowledge import crg_build as CB
        self.CB = CB
        self.root = tempfile.mkdtemp(prefix="mk-p32-swap-")
        self.addCleanup(shutil.rmtree, self.root, True)
        init_repo(root=self.root, profile="standard", assume_yes=True, out=lambda *a, **k: None)
        _built_db(CB.db_path(self.root), "old")

    def _runner(self, outcome):
        CB = self.CB

        def run(root, *, on_progress=None, incremental=False, data_dir=None):
            self.assertIsNotNone(data_dir, "a rebuild of a built index must not build in place")
            target = os.path.join(data_dir, CB.DB_FILENAME)
            if outcome == "ok":
                _built_db(target, "new")
                return CB.BuildResult(ok=True, files_done=3, files_total=3)
            _built_db(target, "half")
            con = sqlite3.connect(target)
            con.execute("UPDATE metadata SET value='in-progress' WHERE key='build_state'")
            con.commit()
            con.close()
            return CB.BuildResult(ok=False, interrupted=outcome == "cancel",
                                  error="cancelled" if outcome == "cancel" else "exited 1")
        return run

    def test_a_cancelled_rebuild_leaves_the_old_graph_complete(self):
        res = self.CB.build_now(self.root, full=True, swap=True, runner=self._runner("cancel"))
        self.assertFalse(res.ok)
        self.assertFalse(os.path.exists(self.CB.next_dir(self.root)), "a side copy was left")
        self.assertEqual(self.CB.INDEX_BUILT, self.CB.probe_index(self.root))
        self.assertEqual("old", _marker(self.CB.db_path(self.root)))

    def test_a_failed_rebuild_leaves_the_old_graph_complete(self):
        res = self.CB.build_now(self.root, full=True, swap=True, runner=self._runner("fail"))
        self.assertFalse(res.ok)
        self.assertEqual(self.CB.INDEX_BUILT, self.CB.probe_index(self.root))
        self.assertEqual("old", _marker(self.CB.db_path(self.root)))
        self.assertFalse(os.path.exists(os.path.join(self.CB.next_dir(self.root),
                                                     self.CB.DB_FILENAME)))

    def test_a_finished_rebuild_is_swapped_in_whole(self):
        closed = []
        res = self.CB.build_now(self.root, full=True, swap=True, runner=self._runner("ok"),
                                before_swap=lambda: closed.append(1))
        self.assertTrue(res.ok, res.error)
        self.assertEqual("new", _marker(self.CB.db_path(self.root)))
        self.assertEqual([1], closed, "this process's graph sessions are closed before the swap")
        self.assertFalse(os.path.isdir(self.CB.next_dir(self.root)))

    def test_the_build_is_pointed_at_the_side_directory(self):
        seen = {}

        class P:
            stdout = iter(())

            def __init__(self, cmd, **kw):
                seen.update(kw)

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 1
        self.CB.run_build(self.root, command="crg", popen=P, data_dir="/tmp/next-x")
        self.assertEqual(os.path.abspath("/tmp/next-x"), seen["env"]["CRG_DATA_DIR"])

    def test_cancel_stops_a_running_build(self):
        flag = threading.Event()
        script = ("import time, sys\n"
                  "for i in range(1000):\n"
                  "    print(f'INFO: Progress: {i}/1000 files parsed', flush=True)\n"
                  "    time.sleep(0.05)\n")
        reports = []

        def popen(cmd, *, stdin, **kw):             # run_build states stdin (DEVNULL); keep it
            kw.pop("env", None)
            return subprocess.Popen([sys.executable, "-c", script], stdin=stdin, **kw)
        threading.Timer(0.6, flag.set).start()
        t0 = time.monotonic()
        with C.scope(flag), C.reporting(lambda **kw: reports.append(kw)):
            res = self.CB.run_build(self.root, command="crg", popen=popen,
                                    data_dir=self.CB.next_dir(self.root))
        self.assertTrue(res.interrupted)
        self.assertEqual("cancelled", res.error)
        self.assertLess(time.monotonic() - t0, 3.0)
        self.assertTrue(any(r.get("total") == 1000 for r in reports), "n/N progress reported")
        self.assertEqual("old", _marker(self.CB.db_path(self.root)))


class TheBusKeepsSessionEvents(unittest.TestCase):

    def test_job_progress_never_pushes_session_events_off_the_ring(self):
        from mokata.ide.bus import RING, EventBus

        async def go():
            bus = EventBus()
            await bus.publish({"kind": "edit", "path": "a.py", "session": "S"})
            for i in range(RING * 3):
                await bus.publish({"kind": "job", "job": {"id": f"j{i % 3}", "done": i}})
            evts, missed = bus.after(0)
            return bus, evts, missed
        bus, evts, missed = asyncio.run(go())
        self.assertFalse(missed, "coalesced job events are not a loss")
        self.assertEqual("edit", evts[0]["kind"])
        jobs = [e for e in evts if e["kind"] == "job"]
        self.assertEqual(3, len(jobs), "one (the latest) event per job")
        self.assertEqual({"edit"}, {e["kind"] for e in bus.recent()})


class TheServerSpeaksJobs(unittest.TestCase):
    """End to end on a real socket: a slow graph view answers 202 with its handle (never a 503),
    its progress arrives on the page's event stream as `job` events, and POST /api/jobs/{id}/cancel
    stops it."""

    def test_a_slow_view_is_a_job_the_page_follows_and_cancels(self):
        import json
        import httpx
        from test_ls_ide_light_at_scale import _Live, _repo
        root = tempfile.mkdtemp(prefix="mk-p32-live-")
        self.addCleanup(shutil.rmtree, root, True)
        _repo(root)
        live = _Live(root)
        self.addCleanup(live.stop)
        stopped = threading.Event()

        def slow_graph(view, symbol, to, depth, page=1):
            try:
                while True:
                    C.checkpoint()
                    C.report(done=1, total=2, unit="rings", note="ring 1")
                    time.sleep(0.02)
            except C.Cancelled:
                stopped.set()
                raise
        live.state.source.graph = slow_graph
        hdr = dict(live.headers, **{"x-mokata-client": "pg"})
        with httpx.Client(headers=hdr, timeout=10) as cl:
            with cl.stream("GET", live.base + "/api/events?client=pg") as ev:
                lines = ev.iter_lines()
                r = cl.get(live.base + "/api/graph?view=ripple&symbol=hub&depth=2")
                self.assertEqual(202, r.status_code, r.text)
                jid = r.json()["job"]
                got, t0 = [], time.monotonic()
                for ln in lines:
                    if ln.startswith("data:") and jid in ln:
                        got.append(json.loads(ln[5:]))
                    if len(got) >= 3 or time.monotonic() - t0 > 5:
                        break
                self.assertGreaterEqual(len(got), 3, "no job events on the stream")
                self.assertEqual((1, 2, "rings"), (got[-1]["done"], got[-1]["total"],
                                                   got[-1]["unit"]))
                c = cl.post(live.base + f"/api/jobs/{jid}/cancel", json={},
                            headers={"Origin": live.base})
                self.assertEqual("cancelled", c.json()["job"]["state"])
        self.assertTrue(stopped.wait(3), "Cancel did not stop the work")


class NoDeadlineAnywhere(unittest.TestCase):

    def test_no_ide_route_has_a_deadline(self):
        ide = os.path.join(SRC, "mokata", "ide")
        # CORPUS: THE WORKING TREE — what the IDE SHIPS (`sync-public.sh` rsyncs the tree); an
        # untracked module still carrying a deadline would be published, so it must be seen.
        for dirpath, _d, files in os.walk(ide):
            for f in files:
                if f.endswith((".py", ".js")):
                    with open(os.path.join(dirpath, f), encoding="utf-8") as fh:
                        text = fh.read()
                    for gone in ("HEAVY_DEADLINE", "MEANING_DEADLINE", "FULL_IMPACT_DEADLINE",
                                 "run_heavy", "timed_out", "RG_TIMEOUT"):
                        self.assertNotIn(gone, text, f"{f}: {gone}")


if __name__ == "__main__":
    unittest.main()
