"""IDE e2e - the image diff (0.0.27 P9, ID-IMAGE-DIFF).

A changed PNG / GIF / SVG gets a panel above its diff: side by side (both sizes and byte counts),
swipe and onion skin (the keyboard moves the slider), and a pixel difference that runs in a Worker
with progress and Cancel - a cancelled run shows NO result. An added or deleted image shows its one
side, labelled. A 20000x20000 PNG and a 200 MB file say "too large to diff - N MB, open externally"
and nothing is decoded. An SVG holding <script>, onload=, a foreignObject and an external href is
shown as an image: nothing runs, nothing is fetched (a counting server proves it), the same file
opened DIRECTLY runs nothing either; a non-image binary keeps "binary".

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import http.server
import struct
import threading
import zlib
from urllib.parse import urlsplit

import _harness as H

GIF_1X1 = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00"
           b",\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;")
GREEN, RED, BLUE = (10, 200, 90), (230, 40, 40), (30, 60, 220)


def png(w, h, color=GREEN, block=None, header_only=False, alpha=None):
    """A real PNG: solid `color`, with `block=(x, y, bw, bh, color2)` painted over it. `header_only`
    is a valid-looking header and an end chunk - a few dozen bytes that claim w x h pixels."""
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    out = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6 if alpha is not None else 2, 0, 0, 0))
    if alpha is not None:                       # RGBA: the same colour at a given opacity
        row = b"\0" + (bytes(color) + bytes([alpha])) * w
        return out + chunk(b"IDAT", zlib.compress(row * h, 1)) + chunk(b"IEND", b"")
    if not header_only:
        plain = b"\0" + bytes(color) * w
        rows = []
        for y in range(h):
            if block and block[1] <= y < block[1] + block[3]:
                x0, bw = block[0], block[2]
                rows.append(b"\0" + bytes(color) * x0 + bytes(block[4]) * bw
                            + bytes(color) * (w - x0 - bw))
            else:
                rows.append(plain)
        out += chunk(b"IDAT", zlib.compress(b"".join(rows), 1))
    return out + chunk(b"IEND", b"")


#: what a "somewhere else" server (the SVG's external references point at it) was asked for
HITS = []


def counting_server():
    """An HTTP server on a free local port that only COUNTS what it is asked for (into HITS)."""
    class Counter(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            HITS.append(self.path)
            self.send_response(404)
            self.end_headers()

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Counter)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


EVIL_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="40" height="30" '
            'onload="window.__pwned=1"><script>window.__pwned=2</script>'
            '<rect width="40" height="30" fill="#c33"/>'
            '<image href="http://127.0.0.1:%(port)d/svg-image.png" width="5" height="5"/>'
            '<foreignObject width="40" height="30"><body xmlns="http://www.w3.org/1999/xhtml" '
            'onload="window.__pwned=3"><iframe src="http://127.0.0.1:%(port)d/svg-frame"></iframe>'
            '</body></foreignObject></svg>')
GOOD_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="30"><rect width="40" height="30" fill="#3a3"/></svg>\n'


#: a size in millimetres: the header check cannot know it, the browser works it out (here: 377,952 px)
UNITS = ('<svg xmlns="http://www.w3.org/2000/svg" width="{0}" height="{0}"><rect width="9" height="9"/></svg>\n')


#: 1100mm is 4,157 px a side: 17.3 MP - inside the view limit (64 MP), over the pixel-difference budget
#: (16 MP). The header check cannot know a size in mm, so only the page can refuse the Difference.
MID = ('<svg xmlns="http://www.w3.org/2000/svg" width="1100mm" height="1100mm"><rect width="9" height="9" fill="{0}"/></svg>\n')


def build(base):
    srv = counting_server()
    del HITS[:]
    port = srv.server_address[1]
    build.srv = srv
    repo = H.make_repo("shop", base)

    def put(rel, data):
        H._write(repo, rel, "")
        with open(repo + "/" + rel, "wb") as fh:
            fh.write(data if isinstance(data, bytes) else data.encode("utf-8"))
    put("img/logo.png", png(40, 30))
    put("img/size.png", png(20, 20))
    put("img/alpha.png", png(8, 8, alpha=255))
    put("img/cancel.png", png(3000, 3000))
    put("img/gone.gif", GIF_1X1)
    put("img/evil.svg", GOOD_SVG)
    put("img/wall.png", png(8, 8))
    put("img/units.svg", UNITS.format("10mm"))
    put("img/mid.svg", MID.format("#3a3"))
    put("data/blob.bin", b"\0\1\2\3")
    H._git(repo, "add", "-A")
    H._git(repo, "commit", "-q", "-m", "images")
    put("img/logo.png", png(40, 30, block=(5, 5, 10, 10, RED)))
    put("img/size.png", png(30, 40))
    put("img/alpha.png", png(8, 8, alpha=128))
    put("img/cancel.png", png(3000, 3000, block=(0, 0, 3000, 1500, BLUE)))
    put("img/evil.svg", EVIL_SVG % {"port": port})
    put("img/wall.png", png(20000, 20000, header_only=True))
    put("img/units.svg", UNITS.format("100000mm"))
    put("img/mid.svg", MID.format("#33a"))
    put("data/blob.bin", b"\0\1\2\3\4")
    put("img/added.png", png(12, 12))
    import os
    os.remove(repo + "/img/gone.gif")
    with open(repo + "/img/big.png", "wb") as fh:                 # 200 MB, sparse: no disk used
        fh.write(png(10, 10))
        fh.truncate(200 * 1024 * 1024)
    return repo


class TheImageDiffWorks(H.IdeFlow):
    FIXTURE = staticmethod(build)

    @classmethod
    def tearDownClass(cls):
        try:
            srv = getattr(build, "srv", None)
            if srv is not None:
                srv.shutdown()
                srv.server_close()
        finally:
            super().tearDownClass()

    def pick(self, name):
        p = self.page
        p.locator("#nav-list li", has_text=name).first.click()
        p.wait_for_selector("#diff .imd")
        self.until("!document.querySelector('#diff .imd-pending')", "the panel to be drawn")

    def mode(self, name):
        self.page.click("#diff .imd .seg[data-mode='%s']" % name)

    def test_1_a_modified_png_shows_both_sides_with_dimensions_and_byte_sizes(self):
        p = self.page
        self.go("review")
        p.wait_for_selector("#nav-list li", state="attached")
        self.pick("logo.png")
        self.until("document.querySelectorAll('#diff .imd img.imd-img').length === 2", "two images")
        self.until("[...document.querySelectorAll('#diff .imd img.imd-img')].every(i => i.complete && i.naturalWidth === 40)",
                   "both images to load at 40 px wide")
        caps = p.evaluate("[...document.querySelectorAll('#diff .imd figcaption')].map(c => c.textContent)")
        self.assertEqual(2, len(caps))
        for c in caps:
            self.assertIn("40×30", c)
        self.assertIn("Before", caps[0])
        self.assertIn("After", caps[1])
        self.assertRegex(caps[0], r"\d+ B")
        self.assertIn("→", p.text_content("#diff .imd-meta"))
        srcs = p.evaluate("[...document.querySelectorAll('#diff .imd img.imd-img')].map(i => i.getAttribute('src'))")
        self.assertIn("rev=HEAD", srcs[0])
        self.assertNotIn("rev=", srcs[1], "the after side is the working tree")
        self.assertTrue(p.evaluate("document.querySelector('#diff .file-body > .bin').hidden"),
                        "the 'binary' placeholder gives way to the panel")

    def test_2_swipe_and_onion_are_driven_from_the_keyboard(self):
        p = self.page
        self.mode("swipe")
        self.assertEqual("true", p.get_attribute("#diff .imd .seg[data-mode='swipe']", "aria-pressed"))
        self.assertTrue(p.evaluate("document.activeElement.classList.contains('seg')"),
                        "focus stays on the control that was used")
        p.focus("#diff .imd input[type=range]")
        self.press("End")
        self.until("document.querySelectorAll('#diff .imd-stack img')[1].style.clipPath.includes('100%')",
                   "the divider at the far right: all before")
        self.press("Home")
        self.until("document.querySelectorAll('#diff .imd-stack img')[1].style.clipPath.includes(' 0%')",
                   "the divider at the far left: all after")
        self.mode("onion")
        p.focus("#diff .imd input[type=range]")
        self.press("Home")
        self.until("document.querySelectorAll('#diff .imd-stack img')[1].style.opacity === '0'", "after fully faded")
        self.press("End")
        self.until("document.querySelectorAll('#diff .imd-stack img')[1].style.opacity === '1'", "after fully shown")
        self.mode("side")
        self.until("document.querySelectorAll('#diff .imd-side img').length === 2", "back to two figures")

    def test_3_the_pixel_difference_counts_the_changed_pixels(self):
        p = self.page
        self.mode("diff")
        self.text_until("#diff .imd-sum", "pixels differ", "the difference summary")
        self.assertIn("100 of 1,200 pixels differ", p.text_content("#diff .imd-sum"))
        self.assertEqual(40, p.evaluate("document.querySelector('#diff .imd canvas').width"))
        self.assertTrue(p.evaluate("document.activeElement.classList.contains('seg')"),
                        "focus survives the run")
        p.select_option("#diff .imd select", "32")
        self.text_until("#diff .imd-sum", "100 of 1,200", "the same count within 32")
        self.assertEqual("SELECT", p.evaluate("document.activeElement.tagName"),
                         "changing the tolerance keeps the focus on its select")

    def test_4_a_size_change_is_counted_and_said(self):
        p = self.page
        self.pick("size.png")
        self.mode("diff")
        self.text_until("#diff .imd-sum", "differ in size", "the size note")
        self.assertIn("800 of 1,200 pixels differ", p.text_content("#diff .imd-sum"),
                      "the overlap is identical; the 800 pixels outside the smaller image count")
        self.assertIn("20×20 → 30×40", p.text_content("#diff .imd-meta"))

    def test_4b_a_change_in_transparency_alone_is_a_difference(self):
        p = self.page
        self.pick("alpha.png")
        self.mode("diff")
        self.text_until("#diff .imd-sum", "pixels differ", "the summary")
        self.assertIn("64 of 64 pixels differ", p.text_content("#diff .imd-sum"))

    def test_5_cancel_stops_the_comparison_and_shows_no_result(self):
        p = self.page
        p.evaluate("""() => { const W = window.Worker; window.__release = false; window.__held = null;
            window.Worker = class extends W {
              set onmessage(fn) { super.onmessage = (ev) => {
                if (ev.data && ev.data.type === 'done' && !window.__release) { window.__held = () => fn(ev); return; }
                fn(ev); }; }
              get onmessage() { return super.onmessage; } }; }""")
        self.pick("cancel.png")
        self.mode("diff")
        self.until("/rows/.test((document.querySelector('#diff .imd-ptxt')||{}).textContent||'') && "
                   "/of 3,000/.test(document.querySelector('#diff .imd-ptxt').textContent)",
                   "progress in rows of 3,000")
        # the Worker has now computed EVERYTHING and its `done` is being held back: cancel while the
        # finished answer is in flight, then let it arrive late
        self.until("window.__held !== null", "the finished answer to be held")
        p.click("#diff .imd-strip button")
        self.until("/Cancelled at row/.test((document.querySelector('#diff .imd-result')||{}).textContent||'')",
                   "the cancel note")
        self.assertIn("no result is shown", p.text_content("#diff .imd-result"))
        self.assertTrue(p.evaluate("typeof window.__held === 'function'"), "a real late answer exists")
        p.evaluate("window.__held()")                       # the finished answer arrives AFTER Cancel
        p.wait_for_timeout(200)
        self.assertEqual(0, p.locator("#diff .imd canvas").count(), "a cancelled run shows nothing")
        self.assertTrue(p.evaluate("document.activeElement !== document.body"), "focus is not lost")
        p.evaluate("window.__release = true")
        self.mode("side")
        self.mode("diff")
        self.text_until("#diff .imd-sum", "pixels differ", "a fresh run completing")
        self.assertIn("4,500,000 of 9,000,000", p.text_content("#diff .imd-sum"))

    def test_6_an_added_and_a_deleted_image_show_their_one_side_labelled(self):
        p = self.page
        self.pick("added.png")
        self.assertEqual(1, p.locator("#diff .imd-side img").count())
        self.assertIn("Added", p.text_content("#diff .imd"))
        for m in ("swipe", "onion", "diff"):
            self.assertTrue(p.is_disabled("#diff .imd .seg[data-mode='%s']" % m), m)
        self.pick("gone.gif")
        self.assertEqual(1, p.locator("#diff .imd-side img").count())
        self.assertIn("Deleted", p.text_content("#diff .imd"))

    def test_7_a_huge_image_says_too_large_to_diff_and_is_not_loaded(self):
        p = self.page
        self.pick("wall.png")
        txt = p.text_content("#diff .imd")
        self.assertIn("too large to diff", txt)
        self.assertIn("20000×20000", txt)
        self.assertIn("open externally", txt)
        self.assertEqual(1, p.locator("#diff .imd-side img").count(), "the small before side still shows")
        self.assertTrue(p.is_disabled("#diff .imd .seg[data-mode='diff']"))
        self.pick("big.png")
        self.assertIn("200.0 MB", p.text_content("#diff .imd"))
        self.assertEqual(0, p.locator("#diff .imd img").count())
        self.assertFalse([r for r in self.requests if "big.png" in r[1] and "/api/image/raw" in r[1]],
                         "a too-large image is never requested")
        self.assertFalse([r for r in self.requests if "wall.png" in r[1] and "/api/image/raw" in r[1] and "rev=" not in r[1]],
                         "the 20000x20000 image is never requested")

    def test_7b_an_svg_the_header_cannot_size_is_judged_by_the_browser_after_it_loads(self):
        """`100000mm` is 377,952 px a side. The server could not know; the page does, once the image
        has loaded, and says so instead of decoding it."""
        p = self.page
        self.pick("units.svg")
        self.text_until("#diff .imd", "too large to diff", "the browser-side verdict")
        self.assertIn("open externally", p.text_content("#diff .imd"))
        self.until("document.querySelector('#diff .imd .seg[data-mode=\"diff\"]').disabled", "Difference disabled")

    def test_7c_a_pair_between_the_budget_and_the_view_limit_refuses_the_difference_in_words(self):
        """The server cannot read `1100mm`; the page learns 17.3 MP once the images load and says
        the pixel difference is over its 16 MP budget (the other modes still work)."""
        p = self.page
        self.pick("mid.svg")
        self.until("document.querySelectorAll('#diff .imd img.imd-img').length === 2 && "
                   "[...document.querySelectorAll('#diff .imd img.imd-img')].every(i => i.complete && i.naturalWidth > 0)",
                   "both images loaded")
        self.mode("diff")
        self.text_until("#diff .imd-result", "over the 16 MP budget", "the budget note")
        self.assertEqual(0, p.locator("#diff .imd canvas").count())
        self.mode("side")

    def test_8_an_svg_with_script_onload_and_external_refs_runs_and_fetches_nothing(self):
        p = self.page
        self.pick("evil.svg")
        self.until("document.querySelectorAll('#diff .imd img.imd-img').length === 2 && "
                   "[...document.querySelectorAll('#diff .imd img.imd-img')].every(i => i.complete)", "both SVGs loaded")
        self.assertEqual(40, p.evaluate("document.querySelectorAll('#diff .imd img.imd-img')[1].naturalWidth"))
        p.wait_for_timeout(500)
        self.assertIsNone(p.evaluate("window.__pwned"), "no script of the SVG ran")
        self.assertEqual([], HITS, "the SVG's external href / iframe fetched nothing")
        self.assertEqual(0, p.locator("#diff .imd svg, #diff .imd object, #diff .imd iframe, #diff .imd embed").count())
        self.assertEqual(0, p.locator("#diff script, #diff foreignObject, #diff iframe").count())
        # an SVG's pixels are the browser's rendering, never compared — in every browser (0.0.28 D:
        # this used to lean on Chromium tainting the canvas; Firefox does not taint, and compared)
        self.mode("diff")
        self.text_until("#diff .imd-result", "cannot be compared", "the SVG's pixels are refused, in words")
        self.assertIn("its source is compared in the line diff", p.text_content("#diff .imd-result"))
        self.assertEqual(0, p.locator("#diff .imd canvas").count())
        self.assertTrue(p.evaluate("document.activeElement !== document.body"))
        self.mode("side")
        self.assertIn("<script>", p.text_content("#diff"), "the SVG's source is in the line diff, as TEXT")
        # opened DIRECTLY (a new tab on the image route): still inert
        base = "{0.scheme}://{0.netloc}".format(urlsplit(self.server.url))
        other = self.browser.new_context()
        tab = other.new_page()
        try:
            tab.goto(self.server.url)                       # pair this fresh context too
            resp = tab.goto(base + "/api/image/raw?path=img/evil.svg")
            self.assertTrue(resp.headers["content-type"].startswith("image/svg+xml"))
            self.assertEqual("nosniff", resp.headers["x-content-type-options"])
            self.assertIn("sandbox", resp.headers["content-security-policy"])
            tab.wait_for_timeout(500)
            self.assertIsNone(tab.evaluate("window.__pwned"), "no script ran on direct navigation")
        finally:
            other.close()
        self.assertEqual([], HITS, "still nothing fetched")

    def test_9_a_binary_that_is_not_an_image_keeps_binary(self):
        p = self.page
        p.locator("#nav-list li", has_text="blob.bin").first.click()
        self.until("!!document.querySelector('#diff .file-body > .bin')", "the placeholder")
        self.assertIn("binary", p.text_content("#diff .file-body > .bin"))
        self.assertEqual(0, p.locator("#diff .imd, #diff .imd-pending").count())

    def test_9a_switching_images_aborts_the_info_fetch_of_the_one_left_behind(self):
        """P9-FIX: the info request of an image the user has left is ABORTED by the page (the browser
        reports it failed), not left to run to its own end. The old request is parked, never
        answered, so only an abort can end it - no clock is waited on."""
        p = self.page
        held = []
        p.route("**/api/image/info*", lambda route: held.append(route)
                if "logo.png" in route.request.url else route.continue_())
        try:
            with p.expect_request(lambda r: "/api/image/info" in r.url and "logo.png" in r.url):
                p.locator("#nav-list li", has_text="logo.png").first.click()
            with p.expect_event("requestfailed", lambda r: "logo.png" in r.url) as failed:
                self.pick("size.png")
            self.assertIn("/api/image/info", failed.value.url)
            self.assertIn("30×40", p.text_content("#diff .imd"))
        finally:
            p.unroute("**/api/image/info*")
            for route in held:
                try:
                    route.abort()
                except Exception:                                # noqa: BLE001 - already aborted
                    pass

    def test_9b_the_panel_fits_a_narrow_window(self):
        p = self.page
        self.pick("logo.png")
        p.set_viewport_size({"width": 700, "height": 900})
        try:
            p.wait_for_timeout(200)
            self.assertTrue(p.evaluate("(() => { const n = document.querySelector('#diff .imd'); return n.scrollWidth <= n.clientWidth + 1; })()"),
                            "the panel does not overflow sideways")
        finally:
            p.set_viewport_size({"width": 1400, "height": 900})
