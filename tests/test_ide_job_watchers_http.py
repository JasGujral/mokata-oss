"""0.0.28 lane D `TABLE-COUNT-JOB-WATCHER-RACE`, on the real server (uvicorn on a socket).

Business rules — what must be true for the user, each:
  * A page whose event stream reconnects (two streams of one page id open for a moment, the OLD one
    ending after the new one opened) keeps every job it asked for: the stale stream ending is not
    the page leaving, so a table count it is waiting on is never cancelled "nobody is waiting".
  * When the page's LAST stream ends, its query jobs stop after the reconnect window, as before.
  * A job is wanted per request: the server records the asking request's token
    (`X-Mokata-Request`), and a leave names it — a leave with no token is refused (400).

No signal APIs: runs the same on Windows.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import _support  # noqa: F401

import shutil
import tempfile
import threading
import time
import unittest


def _wait_for(cond, what, idle_s=30.0):
    """Wait until `cond()`; fail only when NOTHING has changed for `idle_s` (no progress), never on
    a short wall clock."""
    last, since = object(), time.monotonic()
    while True:
        got = cond()
        if got:
            return got
        if got != last:
            last, since = got, time.monotonic()
        if time.monotonic() - since > idle_s:
            raise AssertionError("no progress waiting for " + what)
        time.sleep(0.02)


class _LiveCase(unittest.TestCase):
    def setUp(self):
        try:
            import httpx  # noqa: F401
        except ImportError:                      # pragma: no cover - [ide] dep
            self.skipTest("httpx is not installed")
        from test_ls_ide_light_at_scale import _Live, _repo
        root = tempfile.mkdtemp(prefix="mk-d-watch-")
        self.addCleanup(shutil.rmtree, root, True)
        _repo(root)
        self.live = _Live(root)
        self.addCleanup(self.live.stop)
        self.gate = threading.Event()
        self.addCleanup(self.gate.set)
        self.jobs = self.live.state.jobs

    def work(self):
        gate = self.gate

        def run():
            from mokata.knowledge import cancel as C
            while not gate.wait(0.05):
                C.checkpoint()
            return {"state": "ok"}
        return run

    def stream(self, client):
        """Open `/api/events?client=…` and hold it (unread) until closed; returns a closer."""
        import httpx
        cl = httpx.Client(headers=self.live.headers, timeout=None)
        r = cl.send(cl.build_request("GET", self.live.base + "/api/events?client=" + client),
                    stream=True)
        self.assertEqual(200, r.status_code)

        def close():
            r.close()
            cl.close()
        self.addCleanup(close)
        return close

    def streams_of(self, client):
        with self.jobs._lock:
            return self.jobs._streams.get(client, 0)

    def past_lease(self):
        from mokata.ide import jobs as J
        return time.monotonic() + J.RECONNECT_S + 1.0


class AReconnectingPageKeepsItsCount(_LiveCase):

    def test_the_old_stream_ending_after_the_new_one_opened_cancels_nothing(self):
        from mokata.ide import jobs as J
        close1 = self.stream("pg")
        _wait_for(lambda: self.streams_of("pg") == 1, "stream 1 to register")
        job = self.jobs.start("table.count", {"path": "data_huge.csv"}, self.work(),
                              client="pg", token="t1")
        close2 = self.stream("pg")                       # the EventSource reconnected ...
        _wait_for(lambda: self.streams_of("pg") == 2, "stream 2 to register")
        close1()                                         # ... and the old stream's end lands now
        _wait_for(lambda: self.streams_of("pg") == 1, "the server to notice stream 1 ended")
        self.jobs._sweep(self.past_lease())
        self.assertNotEqual(J.CANCELLED, job.state, "a stale stream's end cancelled the count")
        self.assertEqual({"t1"}, job.watchers.get("pg"), "the page still wants it, by request")
        close2()                                         # the page really leaves
        _wait_for(lambda: self.streams_of("pg") == 0, "the server to notice stream 2 ended")
        self.jobs._sweep(time.monotonic())
        self.assertNotEqual(J.CANCELLED, job.state, "inside the reconnect window it is kept")
        self.jobs._sweep(self.past_lease())
        self.assertEqual(J.CANCELLED, job.state, "past the window nobody wants it")


class AStreamThatFailsToOpenLeavesNoCount(_LiveCase):

    def test_a_failure_after_the_count_went_up_brings_it_down(self):
        """Whatever fails while a stream opens (here `client_back` raises), the page's stream count
        comes back down when the stream ends: a leaked count would be a page that is never "gone",
        so its query jobs would be wanted forever."""
        real = self.jobs.client_back
        calls = []

        def boom(client):
            calls.append(client)
            raise RuntimeError("client_back failed")
        self.jobs.client_back = boom
        self.addCleanup(setattr, self.jobs, "client_back", real)
        import httpx
        try:
            with httpx.Client(headers=self.live.headers, timeout=10) as cl:
                with cl.stream("GET", self.live.base + "/api/events?client=pf") as r:
                    for _ in r.iter_bytes():
                        break
        except httpx.HTTPError:
            pass                                         # the stream may end abruptly: fine
        _wait_for(lambda: bool(calls), "the stream to reach client_back")
        _wait_for(lambda: self.streams_of("pf") == 0 or None, "the stream count to come back down")


class ALeaveNamesItsRequest(_LiveCase):

    def headers(self, token=None):
        h = dict(self.live.headers, **{"x-mokata-client": "pg", "Origin": self.live.base})
        if token:
            h["x-mokata-request"] = token
        return h

    def test_the_server_records_the_asking_requests_token(self):
        import httpx
        r = httpx.post(self.live.base + "/api/jobs", json={"kind": "tree.walk", "args": {}},
                       headers=self.headers("tok-A"), timeout=30)
        self.assertEqual(200, r.status_code, r.text)
        job = self.jobs.get(r.json()["id"])
        self.assertEqual({"tok-A"}, job.watchers.get("pg"))

    def test_one_request_leaving_keeps_the_job_for_the_other(self):
        import httpx
        from mokata.ide import jobs as J
        job = self.jobs.start("table.count", {"path": "x.csv"}, self.work(), client="pg",
                              token="t1")
        self.jobs.start("table.count", {"path": "x.csv"}, self.work(), client="pg", token="t2")
        url = self.live.base + "/api/jobs/" + job.id + "/leave"
        r = httpx.post(url, json={"client": "pg"}, headers=self.headers(), timeout=30)
        self.assertEqual(400, r.status_code, "a leave with no token leaves nothing")
        self.assertNotEqual(J.CANCELLED, job.state)
        r = httpx.post(url, json={"client": "pg", "token": "t1"}, headers=self.headers(), timeout=30)
        self.assertEqual(200, r.status_code, r.text)
        self.assertNotEqual(J.CANCELLED, job.state, "t2 still wants it")
        r = httpx.post(url, json={"client": "pg", "token": "t2"}, headers=self.headers(), timeout=30)
        self.assertEqual(J.CANCELLED, job.state)
        self.assertEqual("cancelled", r.json()["job"]["state"])



class ATokenLeaveOverHttp(ALeaveNamesItsRequest):
    """`POST /api/jobs/leave {client, token}`: a request superseded before its page learned the job
    (verifier L6) leaves by its token - from a job already running, and from one it starts later."""

    def test_a_token_leave_stops_a_job_only_that_request_wanted(self):
        import httpx
        from mokata.ide import jobs as J
        job = self.jobs.start("table.count", {"path": "y.csv"}, self.work(), client="pg", token="t1")
        url = self.live.base + "/api/jobs/leave"
        r = httpx.post(url, json={"client": "pg"}, headers=self.headers(), timeout=30)
        self.assertEqual(400, r.status_code, "a token leave names its token")
        r = httpx.post(url, json={"client": "pg", "token": "t1"}, headers=self.headers(), timeout=30)
        self.assertEqual(200, r.status_code, r.text)
        self.assertEqual(J.CANCELLED, job.state)

    def test_a_leave_that_wins_the_race_keeps_the_late_request_from_wanting(self):
        import httpx
        r = httpx.post(self.live.base + "/api/jobs/leave", json={"client": "pg", "token": "late"},
                       headers=self.headers(), timeout=30)
        self.assertEqual(200, r.status_code, r.text)
        r = httpx.post(self.live.base + "/api/jobs", json={"kind": "tree.walk", "args": {}},
                       headers=self.headers("late"), timeout=30)
        self.assertEqual(200, r.status_code, r.text)
        self.assertEqual({}, self.jobs.get(r.json()["id"]).watchers, "the superseded request wants nothing")


if __name__ == "__main__":
    unittest.main()
