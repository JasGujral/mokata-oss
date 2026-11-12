"""IDE e2e - leaving the page closes the live stream first (0.0.28 D, FIREFOX-E2E-REDS).

The race: a navigation (`goto`, a link, a reload) while `/api/events` is still connecting makes
Firefox log its own abort of the stream as a console error ("can't establish a connection to the
server at .../api/events") - the Linux runner's `test_explorer_locator` test_99 red, 2 of 4 Firefox
runs. `beforeunload` runs before the browser stops the page's loads, so the page closes the stream
there - unless something on the page asked "leave?" (it prevented the event): then the person may
stay, and the stream stays open.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import unittest

import _harness as H


class LeavingClosesTheLiveStream(H.IdeFlow):
    FIXTURE = staticmethod(lambda base: H.make_repo("bare", base))

    def leave(self):
        return self.page.evaluate("""() => { const ev = new Event('beforeunload', { cancelable: true });
            window.dispatchEvent(ev); return [ev.defaultPrevented, MK.liveState()]; }""")

    def test_1_a_page_asking_leave_keeps_its_stream(self):
        """The asking listener is registered BEFORE the page's scripts (an init script), as
        activity.js's running-jobs confirm is registered before boot opens the stream."""
        p = self.page
        p.add_init_script("window.addEventListener('beforeunload', (e) => { if (window.__ask) e.preventDefault(); })")
        p.reload()
        self.until("!!window.MK && !!MK.liveState && MK.liveState() === 1", "the live stream open")
        p.evaluate("window.__ask = true")
        self.assertEqual([True, 1], self.leave(), "asked to stay: the stream stays open")
        p.evaluate("window.__ask = false")

    def test_2_leaving_closes_the_stream_before_the_browser_stops_it(self):
        self.until("!!MK.liveState && MK.liveState() === 1", "the live stream open")
        self.assertEqual([False, 2], self.leave())


if __name__ == "__main__":
    unittest.main()
