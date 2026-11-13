"""IDE e2e - a call superseded before its 202 arrives still leaves its job (0.0.28 D, verifier L6).

A superseded call (`opts.signal` aborted) leaves its job so the server can stop work nobody wants.
That worked only when the abort came AFTER the page had the job's id (`MK.jobs.follow` leaves it). An
abort that landed while the 202 was on its way aborted the fetch: the server had started the job and
counted the request's want, the page never learned the id, and the job stayed wanted until the page's
stream closed.

The contract: the fetch is still aborted (P9-FIX, `test_image_diff` 9a: a request the person left is
ended), the caller is answered at once (AbortError), and the page leaves by the request's own token
(`POST /api/jobs/leave`) - the server drops that want from every job it is in and refuses it to a job
the request starts later (`test_jobs_watchers.ARequestSupersededBeforeItsHandle`,
`test_ide_job_watchers_http.ATokenLeaveOverHttp`). The test holds the server's real 202 between the
server and the page (a route that has already fetched it) and aborts.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import time
import unittest

import _harness as H


class AnAbortBeforeTheHandleStillLeaves(H.IdeFlow):
    FIXTURE = "shop"

    def pump_until(self, cond, what):
        """Let Playwright deliver its events (route, request) until `cond()` holds."""
        end = time.monotonic() + H.STEP_MS / 1000
        while time.monotonic() < end:
            if cond():
                return
            self.page.evaluate("1")
        self.fail("timed out waiting for " + what)

    def test_1_a_call_aborted_before_its_202_leaves_by_its_own_token(self):
        p = self.page
        self.until("!!(window.MK && MK.ui && MK.ui.restored)", "the page")
        held, leaves = [], []

        def hold(route):
            held.append((route, route.fetch()))      # the server has it - and answered 202 - now
        p.route("**/api/search", hold)
        p.on("request", lambda r: leaves.append((r.url, r.post_data)) if r.url.endswith("/api/jobs/leave") else None)
        p.evaluate("""() => { window.__ab = new AbortController();
            window.__abP = MK.api('/api/search', { q: 'def', sync_ms: 0 }, { signal: window.__ab.signal })
              .then(() => 'answered', (e) => e.name); }""")
        self.pump_until(lambda: bool(held), "the server's 202, held before the page")
        route, resp = held[0]
        self.assertEqual(202, resp.status, "a job handle (sync_ms 0)")
        job = json.loads(resp.body())
        token = route.request.headers.get("x-mokata-request")
        self.assertTrue((job.get("id") or job.get("job")) and token, (job, route.request.headers))
        p.evaluate("window.__ab.abort()")
        self.assertEqual("AbortError", p.evaluate("window.__abP"), "the superseded caller is answered at once")
        self.pump_until(lambda: bool(leaves), "the page to leave by the request's token")
        _url, body = leaves[0]
        self.assertEqual(token, json.loads(body).get("token"), "the leave names the aborted request's own token")
        try:
            route.fulfill(response=resp)             # the fetch was aborted: nothing reaches the page
        except Exception:                            # noqa: BLE001 - the request is already gone
            pass
        p.unroute("**/api/search")


if __name__ == "__main__":
    unittest.main()
