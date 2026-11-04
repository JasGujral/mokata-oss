"""0.0.27 P9-FIX - a git that goes quiet must not hang an image request.

Business rules:
  * a `git cat-file` that says NOTHING (a partial clone fetching the blob over a dead network) or
    writes part of the blob and then stalls ends the request with an honest "git did not answer"
    state - in the API (`state: unresponsive`, 504) and in the pair the page draws - never a hang;
  * the clock bounds SILENCE, not size: a slow read that keeps making progress is NOT cut;
  * the git process is killed when the read gives up;
  * every error answer of the image route carries `nosniff`;
  * guards the P9 verifier found unpinned: a newline in a path, `.GIT` in any case.
"""
from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from unittest import mock

import _support  # noqa: F401

from mokata.ide import imagediff as IM
from test_p9_ide_image_diff import Repo, TOKEN, PORT, BASE, png, write


class FakeGit:
    """A `git cat-file blob` whose stdout is a pipe the test controls: it writes `script` (a list
    of (delay_seconds, bytes)) and then either closes it (`end=True`) or leaves it open and silent."""

    def __init__(self, script, end):
        r, self._w = os.pipe()
        self.stdout = os.fdopen(r, "rb")
        self.killed = False
        self.ended = False
        self._t = threading.Thread(target=self._run, args=(script, end), daemon=True)
        self._t.start()

    def _run(self, script, end):
        try:
            for delay, data in script:
                time.sleep(delay)
                if self._w is None:            # killed meanwhile
                    return
                os.write(self._w, data)
            if end:
                # `ended` BEFORE the close: the reader sees EOF the instant the pipe closes and asks
                # `poll()`; set after, a loaded runner let it ask first and "kill" a git that had
                # finished (the flake this ordering fixes).
                self.ended = True
                w, self._w = self._w, None
                os.close(w)
        except OSError:
            pass

    def poll(self):
        return 0 if (self.killed or self.ended) else None

    def kill(self):
        self.killed = True
        if self._w is not None:           # a killed git closes its end: the parked reader unwinds
            try:
                os.close(self._w)
            except OSError:
                pass
            self._w = None

    def wait(self, timeout=None):
        return 0


class Quiet(Repo):
    def setUp(self):
        super().setUp()
        self.data = png(60, 40)
        write(self.d, "a.png", self.data)
        self.commit()
        for name, val in (("STALL_SECS", 0.4), ("GRACE_SECS", 0.5), ("MIN_BPS", 50)):
            p = mock.patch.object(IM, name, val)
            p.start()
            self.addCleanup(p.stop)

    def stub(self, script, end=False):
        self.fake = FakeGit(script, end)
        p = mock.patch.object(IM, "_popen", lambda *a, **k: self.fake)
        p.start()
        self.addCleanup(p.stop)


class GitGoesSilent(Quiet):
    def test_git_that_says_nothing_ends_the_read_and_is_killed(self):
        self.stub([])
        t0 = time.time()
        with self.assertRaises(IM.ImageUnavailable) as cm:
            IM.read_image(self.d, "a.png", "HEAD")
        self.assertLess(time.time() - t0, 5)
        self.assertEqual("unresponsive", cm.exception.state)
        self.assertIn("git did not answer", cm.exception.note)
        self.assertTrue(self.fake.killed)

    def test_git_that_writes_part_of_the_blob_and_stalls_is_not_a_short_image(self):
        self.stub([(0, self.data[:20])])
        with self.assertRaises(IM.ImageUnavailable) as cm:
            IM.read_image(self.d, "a.png", "HEAD")
        self.assertEqual("unresponsive", cm.exception.state)
        self.assertTrue(self.fake.killed)

    def test_git_that_ends_early_is_an_unreadable_image_not_a_short_one(self):
        self.stub([(0, self.data[:20])], end=True)
        with self.assertRaises(IM.ImageUnavailable) as cm:
            IM.read_image(self.d, "a.png", "HEAD")
        self.assertEqual("unreadable", cm.exception.state)
        self.assertIn("ended after 20", cm.exception.note)

    def test_a_cancelled_request_stops_waiting_on_git_long_before_the_stall_bound(self):
        self.stub([])
        with mock.patch.object(IM, "STALL_SECS", 30.0):
            t0 = time.time()
            with self.assertRaises(IM.ImageUnavailable):
                IM.read_image(self.d, "a.png", "HEAD", cancelled=lambda: True)
        self.assertLess(time.time() - t0, 5)
        self.assertTrue(self.fake.killed)

    def test_git_that_trickles_a_byte_at_a_time_is_ended_by_the_minimum_speed(self):
        """Never silent for the stall bound, so only the speed floor can end it: 10 bytes a second
        against a 50 B/s floor, from a script that would trickle for 10 s."""
        self.stub([(0.1, b"x")] * 100)
        t0 = time.time()
        with self.assertRaises(IM.ImageUnavailable) as cm:
            IM.read_image(self.d, "a.png", "HEAD")
        self.assertLess(time.time() - t0, 4)
        self.assertEqual("unresponsive", cm.exception.state)
        self.assertIn("git did not answer", cm.exception.note)
        self.assertIn("too slowly", cm.exception.note)
        self.assertTrue(self.fake.killed)

    def test_a_slow_start_then_a_fast_blob_is_not_called_too_slow(self):
        """A lazy fetch is silent for longer than the speed floor's grace (1.2 s against 1.0 s here;
        40 s of the 60 s silence budget in production), then the blob arrives in a burst: it must be
        shown. The floor counts from the FIRST byte, never from the start of git - counted from the
        start, 1.2 s and a ~1 KB blob is far under a 1 MB/s floor. Slack on a loaded runner: the
        burst only has to land inside the 1.0 s grace (it takes milliseconds), and the silence
        bound is 5x the 1.2 s start."""
        chunks = [self.data[i:i + 100] for i in range(0, len(self.data), 100)]
        self.assertGreater(len(chunks), 1)
        with mock.patch.object(IM, "STALL_SECS", 6.0), mock.patch.object(IM, "GRACE_SECS", 1.0), \
                mock.patch.object(IM, "MIN_BPS", 1_000_000):
            self.assertGreater(1.2, IM.GRACE_SECS, "the start must outlast the grace")
            self.stub([(1.2, chunks[0])] + [(0, c) for c in chunks[1:]], end=True)
            mime, got = IM.read_image(self.d, "a.png", "HEAD")
        self.assertEqual(self.data, got)
        self.assertFalse(self.fake.killed)

    def test_the_pair_the_page_draws_says_git_did_not_answer(self):
        self.stub([(0, self.data[:20])])
        got = IM.pair(self.d, "a.png", "a.png", "HEAD", "")
        self.assertEqual("unresponsive", got["old"]["state"])
        self.assertIn("git did not answer", got["old"]["note"])
        self.assertFalse(got["diff"]["ok"])
        self.assertEqual("ok", got["new"]["state"], "the working-tree side is unaffected")

    def test_a_slow_read_that_keeps_progressing_is_not_cut(self):
        """Each chunk arrives inside the stall bound; the whole takes longer than it. Size and
        total time are not what the clock bounds. Slack: the stall bound is 10x the gap between
        chunks, so a runner that delays a chunk by several gaps still does not look silent; the
        speed floor is dropped to 1 B/s because it is not the subject here."""
        gap = 0.1
        chunks = [self.data[i:i + 10] for i in range(0, len(self.data), 10)]
        script = [(gap, c) for c in chunks]
        with mock.patch.object(IM, "STALL_SECS", gap * 10), mock.patch.object(IM, "MIN_BPS", 1):
            self.assertGreater(gap * len(chunks), IM.STALL_SECS, "the read must outlast one stall")
            self.stub(script, end=True)
            mime, got = IM.read_image(self.d, "a.png", "HEAD")
        self.assertEqual(self.data, got)
        self.assertFalse(self.fake.killed)


class TheRouteAnswers(Quiet):
    def setUp(self):
        super().setUp()
        from starlette.testclient import TestClient
        from mokata.ide.server import AppState, build_app
        self.c = TestClient(build_app(AppState(self.d, TOKEN, PORT, None, False)), base_url=BASE,
                            follow_redirects=False)
        self.c.get(f"/pair?t={TOKEN}")

    def test_raw_answers_504_json_with_the_words_not_a_hang(self):
        self.stub([(0, self.data[:5])])
        t0 = time.time()
        r = self.c.get("/api/image/raw", params={"path": "a.png", "rev": "HEAD"})
        self.assertLess(time.time() - t0, 5)
        self.assertEqual(504, r.status_code)
        self.assertEqual("unresponsive", r.json()["state"])
        self.assertIn("git did not answer", r.json()["note"])

    def test_info_says_unresponsive_in_the_api(self):
        self.stub([])
        j = self.c.get("/api/image/info", params={"path": "a.png", "base": "HEAD"}).json()
        self.assertEqual("unresponsive", j["old"]["state"])

    def test_every_error_answer_is_nosniff(self):
        g = lambda **p: self.c.get("/api/image/raw", params=p)  # noqa: E731
        write(self.d, "fake.png", b"<html>x</html>")
        for r in (g(path="../x.png"), g(), g(path="none.png"), g(path="fake.png"),
                  g(path="a.png", rev="--output=x")):
            self.assertGreaterEqual(r.status_code, 400)
            self.assertEqual("nosniff", r.headers.get("x-content-type-options"), r.status_code)
        self.stub([])
        r = g(path="a.png", rev="HEAD")
        self.assertEqual(504, r.status_code)
        self.assertEqual("nosniff", r.headers.get("x-content-type-options"))


class TheClientLeaves(Quiet):
    """Starlette does not cancel an endpoint when the browser goes away: the route has to notice,
    or a git still being read lives on in a worker thread. The ASGI app is driven directly with a
    client that disconnects 0.5 s in."""

    STALL_BOUND_SECS = 120.0

    def drive(self, path, query):
        import anyio
        from starlette.testclient import TestClient
        from mokata.ide.server import AppState, build_app
        app = build_app(AppState(self.d, TOKEN, PORT, None, False))
        c = TestClient(app, base_url=BASE, follow_redirects=False)
        c.get(f"/pair?t={TOKEN}")
        cookie = "; ".join(f"{k}={v}" for k, v in c.cookies.items())
        t0 = time.time()

        async def receive():
            if time.time() - t0 > 0.5:
                return {"type": "http.disconnect"}
            await anyio.sleep(3600)                 # cancelled by is_disconnected's own scope

        async def send(_m):
            pass
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
                 "scheme": "http", "path": path, "raw_path": path.encode(), "root_path": "",
                 "query_string": query.encode(), "client": ("127.0.0.1", 1),
                 "server": ("127.0.0.1", PORT),
                 "headers": [(b"host", f"127.0.0.1:{PORT}".encode()), (b"cookie", cookie.encode())]}
        import asyncio
        asyncio.run(asyncio.wait_for(app(scope, receive, send), 150))
        return time.time() - t0

    def setUp(self):
        super().setUp()
        # Only a disconnect can end it inside STALL_BOUND_SECS: git is never silent for the stall
        # bound, never too slow, and its script outlasts it. How soon the departure is noticed is
        # 0.25 s of polling + 0.1 s of waiting in the product (~0.6 s measured, also under a dozen
        # CPU hogs); a loaded CI runner took 12.3 s, so the bound below leaves it ~5x that and
        # still sits at half the stall bound - the one thing that would end a read nobody cancelled.
        for name, val in (("STALL_SECS", self.STALL_BOUND_SECS), ("GRACE_SECS", 600.0)):
            p = mock.patch.object(IM, name, val)
            p.start()
            self.addCleanup(p.stop)
        self.stub([(0.1, b"x")] * int(self.STALL_BOUND_SECS * 12))

    def test_raw_stops_reading_git_when_the_client_goes(self):
        took = self.drive("/api/image/raw", "path=a.png&rev=HEAD")
        self.assertLess(took, self.STALL_BOUND_SECS / 2)
        self.assertTrue(self.fake.killed, "the git the request started is gone")

    def test_info_stops_reading_git_when_the_client_goes(self):
        took = self.drive("/api/image/info", "path=a.png&base=HEAD")
        self.assertLess(took, self.STALL_BOUND_SECS / 2)
        self.assertTrue(self.fake.killed, "the git the request started is gone")


class GuardsTheVerifierFoundUnpinned(Repo):
    def test_a_newline_in_a_path_is_refused(self):
        name = "a\nb.png"
        try:
            write(self.d, name, png(2, 2))
        except (OSError, ValueError):
            self.skipTest("this file system cannot hold a newline in a name")
        self.assertRaises(Exception, IM.pair, self.d, name, name, "HEAD", "")
        with self.assertRaises(Exception) as cm:
            IM.pair(self.d, name, name, "HEAD", "")
        self.assertEqual("PathRefused", type(cm.exception).__name__)

    def test_dot_git_is_refused_in_any_case(self):
        for p in (".GIT/x.png", ".Git/x.png", "sub/.GIT/x.png"):
            with self.subTest(p=p):
                with self.assertRaises(Exception) as cm:
                    IM.pair(self.d, p, p, "HEAD", "")
                self.assertEqual("PathRefused", type(cm.exception).__name__)


if __name__ == "__main__":
    unittest.main()
