"""0.0.26 P37 — closing the IDE with work running asks first and never kills it silently (Jas,
2026-10-22: "while closing, it should confirm to run in background").

Asserted (business level):
  * the terminal prompt: [b]ackground / [c]ancel safely / [w]ait (end of input = background);
  * BACKGROUND — a running code-graph rebuild is handed to a detached worker that finishes it
    after the server is gone; the next start lists it `done` (Activity + Health) and the new index
    is in place;
  * CANCEL SAFELY — a running update stops at its checkpoint, the record says `cancelled`, and the
    index is still the last COMPLETE one (usable, R39);
  * WAIT — the stop waits for the job to finish; nothing is handed over;
  * SIGTERM — a real `serve()` stopped by SIGTERM with a build running hands it to the background
    (uvicorn re-raised the signal and killed every job on the spot before this);
  * the page: closing or reloading it with such a job running raises the browser's prompt; with
    none, it closes without one.
"""
import os
import re
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata.ide import handoff as H
from mokata.ide import jobs as J
from mokata.knowledge import crg_build as B

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, "..", "src"))
POSIX = os.name == "posix"

FAKE_CRG = r'''#!{py}
"""A stand-in `code-review-graph`: `build` / `update` write a COMPLETE graph.db after a moment."""
import os, sqlite3, sys, time
if "--version" in sys.argv:
    print("code-review-graph 2.3.9"); sys.exit(0)
cmd = sys.argv[1]
repo = sys.argv[sys.argv.index("--repo") + 1]
d = os.environ.get("CRG_DATA_DIR") or os.path.join(repo, ".code-review-graph")
os.makedirs(d, exist_ok=True)
print("Progress: 1/2 files parsed", flush=True)
time.sleep(1.0)
c = sqlite3.connect(os.path.join(d, "graph.db"))
c.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
c.executemany("INSERT OR REPLACE INTO metadata VALUES (?, ?)",
              [("build_state", "complete"), ("last_updated", "2026-10-23T10:00:00")])
c.commit(); c.close()
print("Progress: 2/2 files parsed", flush=True)
'''


def _meta(path, state, last):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
    conn.executemany("INSERT OR REPLACE INTO metadata VALUES (?, ?)",
                     [("build_state", state), ("last_updated", last)])
    conn.commit()
    conn.close()


def _repo():
    root = tempfile.mkdtemp(prefix="p37-close-")
    os.makedirs(os.path.join(root, ".mokata", "temp_local"))
    _meta(B.db_path(root), "complete", "2026-10-22T08:00:00")
    return root


def _fake_crg_env():
    """0.0.26 O15: mokata runs only its own env's code-review-graph or the one NAMED in
    `MOKATA_CRG` — a stand-in on PATH is (correctly) ignored, so the test names its fake."""
    return {"MOKATA_CRG": os.path.join(_fake_crg_bin(), "code-review-graph")}


def _fake_crg_bin():
    d = tempfile.mkdtemp(prefix="p37-crg-bin-")
    p = os.path.join(d, "code-review-graph")
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(FAKE_CRG.replace("{py}", sys.executable))
    os.chmod(p, 0o755)
    return d


def _until_cancelled():
    from mokata.knowledge.cancel import checkpoint
    while True:
        checkpoint()
        time.sleep(0.02)


def _wait_record(root, state, secs=60.0):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        recs = H.records(root)
        if recs and recs[0].get("state") == state:
            return recs[0]
        time.sleep(0.2)
    return H.records(root)[0] if H.records(root) else None


class TheTerminalAsks(unittest.TestCase):

    def test_the_prompt_offers_background_cancel_and_wait(self):
        said = []
        job = mock.Mock(label="code graph index", kind="index.crg", done=3, total=10,
                        unit="files", note="", elapsed=lambda: 4.0)
        self.assertEqual(H.CANCEL, H.ask([job], read=lambda _p: "c", say=said.append))
        self.assertIn("1 job(s) running", said[0])
        self.assertIn("3/10 files", said[0])
        self.assertEqual(H.WAIT, H.ask([job], read=lambda _p: "w", say=said.append))
        self.assertEqual(H.BACKGROUND, H.ask([job], read=lambda _p: "b", say=said.append))
        answers = iter(["?", "x", "c"])
        self.assertEqual(H.CANCEL, H.ask([job], read=lambda _p: next(answers), say=said.append))

        def eof(_p):
            raise EOFError
        self.assertEqual(H.BACKGROUND, H.ask([job], read=eof, say=said.append))

    def test_a_stop_with_no_terminal_is_background(self):
        from mokata.ide import server as S
        state = mock.Mock(root="/r")
        state.jobs.running_kept.return_value = [mock.Mock()]
        with mock.patch.object(H, "stop") as stop, mock.patch.object(H, "ask") as ask:
            self.assertEqual(H.BACKGROUND, S._on_stop(state, signal.SIGTERM, interactive=False))
        ask.assert_not_called()
        stop.assert_called_once_with(state.jobs, "/r", H.BACKGROUND)
        with mock.patch.object(H, "stop"), mock.patch.object(H, "ask", return_value=H.WAIT):
            self.assertEqual(H.WAIT, S._on_stop(state, signal.SIGINT, interactive=True))


@unittest.skipUnless(POSIX, "the stand-in code-review-graph is a POSIX script")
class StoppingWithABuildRunning(unittest.TestCase):

    def setUp(self):
        self.root = _repo()
        self.reg = J.JobRegistry()
        env = dict(_fake_crg_env(), PYTHONPATH=SRC)
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()

    def tearDown(self):
        self.reg.close()
        self.env.stop()

    def test_background_finishes_the_rebuild_after_the_server_is_gone(self):
        job = self.reg.start("index.crg", {"action": "rebuild-graph"}, _until_cancelled,
                             label="rebuild the code graph", keep=True)
        time.sleep(0.2)
        said = []
        recs = H.stop(self.reg, self.root, H.BACKGROUND, say=said.append)
        self.assertTrue(job.stopped.is_set(), "the in-process work did not stop")
        self.assertEqual([H.BACKGROUND], [r["state"] for r in recs])
        self.assertIn("continues in the background", said[-1])
        self.reg.close()                                  # the server is gone
        rec = _wait_record(self.root, "done")
        self.assertEqual("done", rec and rec["state"], rec)
        self.assertEqual(B.INDEX_BUILT, B.probe_index(self.root))
        self.assertEqual("2026-10-23T10:00:00", B._last_updated(B.db_path(self.root)))
        snap = H.snapshots(self.root)[0]                  # the next start: Activity …
        self.assertEqual(("done", True, False),
                         (snap["state"], snap["background"], snap["cancellable"]))
        from mokata.ide import health
        rows = [r for r in health._background_rows(self.root)]        # … and Health
        self.assertEqual(1, len(rows))
        self.assertIn("finished after the IDE closed", rows[0]["detail"])
        from mokata.govern.doctor import background_job_findings       # … and `mokata doctor`
        found = background_job_findings(mock.Mock(root=self.root))
        self.assertEqual(["background-job"], [f.code for f in found])
        self.assertRegex(found[0].detail, r"rebuild the code graph: finished after the IDE "
                                          r"closed at \d{4}-\d\d-\d\d \d\d:\d\d")

    def test_cancel_safely_keeps_the_last_complete_index(self):
        def slow(root, on_progress=None, incremental=False, data_dir=None):
            from mokata.knowledge.cancel import cancelled
            _meta(B.db_path(root), "in-progress", "2026-10-22T08:00:00")   # the update writes
            while not cancelled():
                time.sleep(0.02)
            return B.BuildResult(ok=False, interrupted=True, error="cancelled")
        job = self.reg.start("index.fresh", {"root": self.root},
                             lambda: B.build_now(self.root, runner=slow),
                             label="keeping the code graph current", keep=True)
        time.sleep(0.3)
        recs = H.stop(self.reg, self.root, H.CANCEL, say=lambda _s: None)
        self.assertTrue(job.stopped.is_set())
        self.assertEqual(["cancelled"], [r["state"] for r in recs])
        self.assertIn("last complete index is kept", recs[0]["note"])
        self.assertEqual(B.INDEX_BUILT, B.probe_index(self.root))     # never left INCOMPLETE
        self.assertEqual(B.UPDATE_INTERRUPTED, B.update_state(self.root))
        from mokata.govern.doctor import background_job_findings
        from mokata.ide import health
        self.assertIn("cancelled after the IDE closed",
                      background_job_findings(mock.Mock(root=self.root))[0].detail)
        self.assertIn("cancelled after the IDE closed",
                      health._background_rows(self.root)[0]["detail"])

    def test_wait_lets_the_job_finish_and_hands_nothing_over(self):
        self.reg.start("index.meaning", {"root": self.root}, lambda: time.sleep(0.6),
                       label="meaning index", keep=True)
        time.sleep(0.1)
        self.assertEqual([], H.stop(self.reg, self.root, H.WAIT, say=lambda _s: None))
        self.assertEqual([], H.records(self.root))
        self.assertEqual([], self.reg.running_kept())

    def test_mokata_doctor_lists_how_background_jobs_ended(self):
        from mokata.config import Surface
        from mokata.govern.doctor import diagnose
        from mokata.init import init_repo
        root = tempfile.mkdtemp(prefix="p37-doctor-")
        init_repo(root=root, profile="standard", assume_yes=True, out=lambda *a, **k: None)
        now = time.time()
        for i, (state, extra) in enumerate([("done", {"note": "rebuilt"}),
                                            ("failed", {"error": "boom"}),
                                            ("cancelled", {"note": "kept"})]):
            H._write(os.path.join(H.records_dir(root), f"j{i}.json"),
                     {"id": f"j{i}", "kind": "index.crg", "label": f"job {state}",
                      "state": state, "handed": now - i, "finished": now - i, **extra})
        found = {f.code: f for f in diagnose(Surface.load(root)).findings
                 if f.code.startswith("background-job")}
        details = " | ".join(f.detail for f in diagnose(Surface.load(root)).findings
                             if f.code.startswith("background-job"))
        self.assertIn("background-job-failed", found)
        self.assertEqual("warning", found["background-job-failed"].severity)
        for word in ("job done: finished after the IDE closed at", "job failed: failed after "
                     "the IDE closed at", "boom", "job cancelled: cancelled after the IDE closed"):
            self.assertIn(word, details)

    def test_a_record_names_a_kind_never_code(self):
        path = os.path.join(H.records_dir(self.root), "x.json")
        H._write(path, {"id": "x", "kind": "os.system", "root": self.root, "args": {}})
        self.assertEqual(2, H.run_worker(path))
        self.assertEqual("failed", H._read(path)["state"])


DRIVER = r'''
import sys, time
sys.path.insert(0, {src!r})
from mokata.ide import server as S
orig = S.build_app
def build_app(state, features=None):
    app = orig(state, features)
    def work():
        from mokata.knowledge.cancel import checkpoint
        while True:
            checkpoint(); time.sleep(0.02)
    state.jobs.start("index.crg", {{"action": "rebuild-graph"}}, work,
                     label="rebuild the code graph", keep=True)
    print("JOB-STARTED", flush=True)
    return app
S.build_app = build_app
sys.exit(S.serve({root!r}, port=0, idle_secs=None, token="t"))
'''


@unittest.skipUnless(POSIX, "SIGTERM and the stand-in code-review-graph are POSIX")
class SigtermContinuesInTheBackground(unittest.TestCase):

    def test_sigterm_hands_the_running_build_to_a_background_worker(self):
        try:
            import uvicorn  # noqa: F401
        except ImportError:
            self.skipTest("the [ide] extra (uvicorn) is not installed")
        root = _repo()
        env = dict(os.environ, **_fake_crg_env(), PYTHONPATH=SRC, PYTHONUNBUFFERED="1")
        p = subprocess.Popen([sys.executable, "-c", DRIVER.format(src=SRC, root=root)],
                             env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True)
        lines = []
        try:
            t0 = time.monotonic()
            while time.monotonic() - t0 < 60:
                line = p.stdout.readline()
                if not line:
                    break
                lines.append(line)
                if "JOB-STARTED" in line:
                    break
            time.sleep(2.0)                              # uvicorn is serving (the job runs)
            p.send_signal(signal.SIGTERM)
            out, _ = p.communicate(timeout=60)
        finally:
            if p.poll() is None:
                p.kill()
        lines.append(out)
        text = "".join(lines)
        self.assertIn("continue in the background", text, text[-2000:])
        rec = _wait_record(root, "done")
        self.assertEqual("done", rec and rec["state"], (rec, text[-2000:]))
        self.assertEqual(B.INDEX_BUILT, B.probe_index(root))


@unittest.skipUnless(POSIX, "starts `mokata ide` and Chromium")
class ThePageAsksBeforeClosing(unittest.TestCase):

    def test_closing_the_page_with_a_kept_job_running_prompts_and_without_one_does_not(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("Playwright is not installed in this interpreter")
        env = dict(os.environ, PYTHONPATH=SRC, PYTHONUNBUFFERED="1")
        port = 28000 + os.getpid() % 1500
        p = subprocess.Popen([sys.executable, "-m", "mokata.cli", "ide", "--demo", "--no-browser",
                              "--foreground", "--port", str(port)], env=env,
                             cwd=tempfile.mkdtemp(), stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            url = None
            t0 = time.monotonic()
            while url is None and time.monotonic() - t0 < 60:
                m = re.search(r"(http://127\.0\.0\.1:%d\S*)" % port, p.stdout.readline() or "")
                url = m and m.group(1)
            self.assertIsNotNone(url, "the IDE did not start")
            threading.Thread(target=lambda: [None for _ in p.stdout], daemon=True).start()
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                prompts = []
                for kept in (True, False):
                    page = browser.new_page()
                    page.on("dialog", lambda d: (prompts.append((kept, d.type)), d.dismiss()))
                    page.goto(url)
                    page.wait_for_function("() => window.MK && MK.jobs && MK.jobs.keptRunning")
                    page.mouse.click(5, 5)               # a user gesture: Chrome asks only then
                    if kept:
                        page.evaluate("""() => { MK.jobs.follow({id: 'index-crg-x-1',
                            job: 'index-crg-x-1', kind: 'index.crg', keep: true,
                            state: 'running', label: 'rebuild the code graph'},
                            '/api/jobs/index-crg-x-1').catch(() => null); }""")
                        self.assertEqual(1, page.evaluate("() => MK.jobs.keptRunning().length"))
                    page.close(run_before_unload=True)
                    time.sleep(0.5)
                browser.close()
            self.assertIn((True, "beforeunload"), prompts)
            self.assertNotIn((False, "beforeunload"), prompts)
        finally:
            p.terminate()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    unittest.main()
