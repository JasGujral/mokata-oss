"""0.0.28 lane D `TABLE-COUNT-JOB-WATCHER-RACE` — a job is cancelled "nobody is waiting" only when no
request of any page still wants it.

Business rules — what must be true for the user, each:
  * Two asks of one page for the same question are two WANTS: one ask being superseded (its
    request left) never cancels the answer the other ask is still waiting for.
  * A page's event stream that drops and reconnects keeps everything it wanted — also when the old
    stream's end is noticed AFTER the new stream opened (the order a server sees on a reconnect),
    and also when the stream drops twice before it comes back.
  * Past the reconnect window a query nobody wants still stops (that stays correct).
  * It holds at scale: 5k jobs x 50 asks each, every ask leaving, is fast and leaves nothing.

The registry is driven deterministically: the orphan sweep is called with an explicit clock
(`_sweep(now)`), never by sleeping for the ticker.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
import threading
import time
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.ide import jobs as J


class _Reg(unittest.TestCase):
    def setUp(self):
        self.reg = J.JobRegistry()
        self.addCleanup(self.reg.close)
        self.gate = threading.Event()
        self.addCleanup(self.gate.set)

    def body(self):
        """Work that waits (cancellably) until the test lets it go."""
        def work():
            from mokata.knowledge import cancel as C
            while not self.gate.wait(0.05):
                C.checkpoint()
            return {"ok": True}
        return work

    def past_lease(self):
        return time.monotonic() + max(J.RECONNECT_S, J.GRACE_S) + 1.0

    def ask(self, symbol, client, token):
        return self.reg.start("graph.view", {"symbol": symbol}, self.body(), client=client,
                              token=token)


class TwoAsksOneQuestion(_Reg):

    def test_one_ask_leaving_keeps_the_job_for_the_other_ask_of_the_same_page(self):
        a = self.ask("q", "page", "t1")
        b = self.ask("q", "page", "t2")
        self.assertIs(a, b, "the same question is one job")
        self.reg.leave(a, "page", "t1")
        self.reg._sweep(self.past_lease())
        self.assertNotEqual(J.CANCELLED, a.state, "the second ask still wants it")
        self.reg.leave(a, "page", "t2")
        self.assertEqual(J.CANCELLED, a.state, "nobody wants it now")
        self.assertEqual("nobody is waiting for this any more", a.error)

    def test_a_leave_names_a_request_it_never_made_and_changes_nothing(self):
        a = self.ask("q2", "page", "t1")
        self.reg.leave(a, "page", "never-asked")
        self.reg.leave(a, "other-page", "t1")
        self.reg._sweep(self.past_lease())
        self.assertNotEqual(J.CANCELLED, a.state)

    def test_another_page_keeps_it_when_the_first_page_leaves(self):
        a = self.ask("q3", "p1", "t1")
        self.ask("q3", "p2", "t9")
        self.reg.leave(a, "p1", "t1")
        self.reg._sweep(self.past_lease())
        self.assertNotEqual(J.CANCELLED, a.state)

    def test_a_leave_requires_a_token(self):
        a = self.ask("q4", "page", "t1")
        for bad in ("", None):
            with self.assertRaises(ValueError):
                self.reg.leave(a, "page", bad)
        self.assertNotEqual(J.CANCELLED, a.state)

    def test_at_scale_5k_jobs_by_50_asks_every_leave_is_fast_and_leaves_nothing(self):
        jobs = []
        for i in range(5000):
            for t in range(50):
                j = self.reg.start("graph.view", {"symbol": "s%d" % i}, self.body(),
                                   client="page", token="t%d" % t)
            jobs.append(j)
        t0 = time.perf_counter()
        for j in jobs:
            for t in range(49):
                self.reg.leave(j, "page", "t%d" % t)
        spent = time.perf_counter() - t0
        self.assertLess(spent, 1.0, "245k leaves took %.2fs" % spent)
        self.assertFalse([j for j in jobs if j.state == J.CANCELLED],
                         "every job still had one ask")
        for j in jobs:
            self.reg.leave(j, "page", "t49")
        self.assertEqual([], [j for j in jobs if j.state != J.CANCELLED])


class StaleStreamTeardown(_Reg):

    def test_the_cause_order_new_stream_opens_then_the_old_one_ends_keeps_the_count(self):
        """The cause, in the order the server sees it on a reconnect: the NEW stream opens (the
        server calls `stream_opened` + `client_back`), THEN the OLD stream's end is noticed (the
        server calls `client_gone` only when `stream_closed` says it was the last). The count the
        page still wants survives the sweep past the window. (On base, which had no stream count,
        this order cancelled it: `0028-evidence/D/cause-repro-on-base.txt`.)"""
        self.reg.stream_opened("page")                       # the old stream
        a = self.ask("count", "page", "t1")
        self.reg.stream_opened("page")                       # the NEW stream opened ...
        self.reg.client_back("page")
        if self.reg.stream_closed("page"):                   # ... then the OLD stream's end
            self.reg.client_gone("page")
        self.reg._sweep(self.past_lease())
        self.assertNotEqual(J.CANCELLED, a.state, "a stale stream's end cancelled a wanted count")

    def test_with_the_stream_count_a_stale_stream_ending_is_a_no_op(self):
        self.assertTrue(self.reg.stream_opened("page"))     # stream 1
        a = self.ask("count2", "page", "t1")
        self.assertFalse(self.reg.stream_opened("page"))    # stream 2 (reconnect) — not the first
        self.assertFalse(self.reg.stream_closed("page"), "stream 2 is still open")
        self.reg._sweep(self.past_lease())
        self.assertNotEqual(J.CANCELLED, a.state)
        self.assertTrue(self.reg.stream_closed("page"), "the last stream closed: the page is gone")

    def test_a_double_client_gone_keeps_what_the_page_wanted(self):
        a = self.ask("twice", "page", "t1")
        self.reg.client_gone("page")
        b = self.ask("twice-b", "page", "t2")  # a request that was in flight when the stream dropped
        self.reg.client_gone("page")          # a second drop before the page came back
        self.reg.client_back("page")
        self.reg._sweep(self.past_lease())
        self.assertNotEqual(J.CANCELLED, a.state, "the first drop's list was overwritten")
        self.assertNotEqual(J.CANCELLED, b.state)
        self.reg.leave(a, "page", "t1")       # and its tokens came back: a leave still works
        self.assertEqual(J.CANCELLED, a.state)

    def test_a_request_that_left_while_the_page_was_away_is_not_restored(self):
        a = self.ask("away", "page", "t1")
        self.ask("away", "page", "t2")
        self.reg.client_gone("page")
        self.reg.leave(a, "page", "t1")
        self.assertNotEqual(J.CANCELLED, a.state,
                            "t2 is away inside the reconnect window: a leave of t1 must not cancel it")
        self.reg.client_back("page")
        self.assertEqual({"page": {"t2"}}, a.watchers, "only t2 comes back; t1 left")
        self.reg.leave(a, "page", "t2")
        self.assertEqual(J.CANCELLED, a.state, "t1 left while away; t2 left after: nobody wants it")

    def test_a_leave_while_away_from_a_page_that_never_returns_stops_past_the_window(self):
        a = self.ask("never", "page", "t1")
        self.ask("never", "page", "t2")
        self.reg.client_gone("page")
        self.reg.leave(a, "page", "t1")
        self.reg._sweep(time.monotonic())                  # inside the window: t2 still wants it
        self.assertNotEqual(J.CANCELLED, a.state)
        self.reg._sweep(self.past_lease())
        self.assertEqual(J.CANCELLED, a.state, "past the window nobody wants it")

    def test_a_page_that_never_returns_leaves_nothing_behind(self):
        """Bounded growth: once the reconnect window has passed, nothing of a page that never came
        back is held — not in the registry's list, not on any job (finished or cancelled)."""
        a = self.ask("left-behind", "page", "t1")
        go_b = threading.Event()
        self.addCleanup(go_b.set)
        b = self.reg.start("graph.view", {"symbol": "finishes"}, lambda: go_b.wait(5) and {"ok": True},
                           client="page", token="t2")
        self.reg.client_gone("page")
        go_b.set()                                         # b's work ends while the page is away
        b.finished.wait(5)
        self.assertEqual(J.DONE, b.state)
        self.reg._sweep(self.past_lease())
        self.assertEqual(J.CANCELLED, a.state)
        self.assertEqual({}, a.away)
        self.assertEqual({}, b.away)
        self.assertNotIn("page", self.reg._away)

    def test_at_scale_1k_pages_that_never_return_leave_nothing_behind(self):
        jobs = [self.ask("n%d" % c, "page-%d" % c, "t") for c in range(1000)]
        for c in range(1000):
            self.reg.client_gone("page-%d" % c)
        t0 = time.perf_counter()
        self.reg._sweep(self.past_lease())
        self.assertLess(time.perf_counter() - t0, 2.0)
        self.assertEqual({}, self.reg._away)
        self.assertEqual([], [j for j in jobs if j.away])

    def test_past_the_window_a_query_nobody_wants_still_stops(self):
        a = self.ask("gone", "page", "t1")
        self.reg.client_gone("page")
        self.reg._sweep(time.monotonic())                  # inside the window: kept
        self.assertNotEqual(J.CANCELLED, a.state)
        self.reg._sweep(self.past_lease())
        self.assertEqual(J.CANCELLED, a.state)

    def test_at_scale_1k_clients_drop_twice_and_come_back_with_every_job(self):
        jobs = {}
        for c in range(1000):
            jobs[c] = self.ask("c%d" % c, "page-%d" % c, "t")
        for c in range(1000):
            self.reg.client_gone("page-%d" % c)
            self.reg.client_gone("page-%d" % c)
        for c in range(1000):
            self.reg.client_back("page-%d" % c)
        self.reg._sweep(self.past_lease())
        self.assertEqual([], [c for c, j in jobs.items() if j.state == J.CANCELLED])



class ARequestSupersededBeforeItsHandle(_Reg):
    """0.0.28 D (verifier L6): a call superseded before the page learned its job's id - the page
    aborted the fetch, so it never will - leaves by its TOKEN alone. Every job it asked for drops its
    want, and a job its request starts AFTER the leave arrived (the leave travels on its own
    connection and can win the race) never takes it."""

    def test_a_token_leave_drops_that_requests_want_and_only_it(self):
        a = self.ask("q", "page", "t1")
        self.ask("q", "page", "t2")
        self.reg.leave_request("page", "t1")
        self.assertEqual({"page": {"t2"}}, a.watchers, "only the superseded request's want goes")
        self.reg.leave_request("page", "t2")
        self.assertEqual(J.CANCELLED, a.state, "nobody wants it: a query job stops")

    def test_a_job_started_after_its_requests_leave_never_takes_the_want(self):
        self.reg.leave_request("page", "t9")                # the leave won the race
        a = self.ask("late", "page", "t9")
        self.assertEqual({}, a.watchers)
        self.reg._sweep(time.monotonic() + 0.01)
        self.assertEqual(J.CANCELLED, a.state, "the request that started it had already gone")

    def test_a_left_token_is_forgotten_past_its_window(self):
        self.reg.leave_request("page", "t9")
        self.reg._sweep(time.monotonic() + J.LEFT_S + 1.0)
        self.assertEqual({}, self.reg._left, "bounded: a tombstone does not outlive its window")

    def test_tombstones_are_capped_and_a_leave_never_rescans_them(self):
        """Verifier R2-1: 20k distinct leaves inside one window. The map stays at LEFT_MAX, the
        oldest goes first, and no leave walks the map (work counted, not timed)."""
        scans = [0]

        class Counting(J.OrderedDict):
            def __iter__(self):
                scans[0] += 1
                return super().__iter__()

            def items(self):
                scans[0] += 1
                return super().items()

        self.reg._left = Counting()
        for i in range(20000):
            self.reg.leave_request("page", "t%d" % i)
            self.assertLessEqual(len(self.reg._left), J.LEFT_MAX)
        self.assertIs(Counting, type(self.reg._left), "the map is kept, never rebuilt per leave")
        self.assertEqual(0, scans[0], "a leave evicts the oldest in O(1); it never walks the map")
        self.assertEqual(J.LEFT_MAX, len(self.reg._left))
        self.assertIn(("page", "t19999"), self.reg._left, "the newest is kept")
        self.assertNotIn(("page", "t0"), self.reg._left, "the oldest is evicted first")

    def test_an_evicted_token_is_an_unknown_one(self):
        self.reg.leave_request("page", "t0")
        for i in range(1, J.LEFT_MAX + 1):
            self.reg.leave_request("page", "x%d" % i)
        a = self.ask("late", "page", "t0")
        self.assertEqual({"page": {"t0"}}, a.watchers, "evicted: its late job takes the want")
        b = self.ask("later", "page", "x%d" % J.LEFT_MAX)
        self.assertEqual({}, b.watchers, "still remembered: no want")

    def test_the_ticker_prunes_only_the_expired_front(self):
        self.reg.leave_request("page", "old")
        now = time.monotonic()
        self.reg._left[("page", "old")] = now - 1.0          # expired
        self.reg.leave_request("page", "new")
        self.reg._sweep(now)
        self.assertEqual([("page", "new")], list(self.reg._left))

    def test_a_token_leave_needs_a_token(self):
        with self.assertRaises(ValueError):
            self.reg.leave_request("page", "")


if __name__ == "__main__":
    unittest.main()
