"""IDE e2e - 0.0.29 lane E `IDE-UI-PIN-GAPS`: behaviours of the IDE that had no direct test.

Each class here pins ONE named gap and says which. Waits are on page state or on an event the test
controls (a held Worker message, a counter) - never on a sleep or a short wall clock.

  ThePageBudgetsTheDifferenceByTheCommonBox   P9: the page-side Difference size check
  ALateWorkerAnswerAfterCancelIsIgnored       P9: the "late Worker result" step, made non-vacuous
  ACancelledIntraLineBatchIsAskedAgain        IDE-DIFF-CONTEXT-LOWS: a cancelled intra-line ask
  AGroupHoldingAPendingCallIsPending          ruling D6 (verifier F1): a cluster edge is never resolved
                                              while any call it stands for is unconfirmed

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import struct
import unittest
import zlib

import _harness as H


def png(w, h, color=(10, 200, 90), block=None):
    """A real PNG: solid `color`, with `block=(x, y, bw, bh, color2)` painted over it."""
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    rows = []
    for y in range(h):
        if block and block[1] <= y < block[1] + block[3]:
            x0, bw = block[0], block[2]
            rows.append(b"\0" + bytes(color) * x0 + bytes(block[4]) * bw + bytes(color) * (w - x0 - bw))
        else:
            rows.append(b"\0" + bytes(color) * w)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 1)) + chunk(b"IEND", b""))


def put(repo, rel, data):
    H._write(repo, rel, "")
    with open(repo + "/" + rel, "wb") as fh:
        fh.write(data if isinstance(data, bytes) else data.encode("utf-8"))


def _svg(w_mm, h_mm, fill):
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="%dmm" height="%dmm">'
            '<rect width="9" height="9" fill="%s"/></svg>\n' % (w_mm, h_mm, fill))


def _pick(flow, name):
    """A step the image flows share. Module functions, not a mixin class: test_o9 requires every
    class in an e2e module to be exactly `H.IdeFlow` (the base that carries the console-error
    step)."""
    p = flow.page
    p.locator("#nav-list li", has_text=name).first.click()
    p.wait_for_selector("#diff .imd")
    flow.until("!document.querySelector('#diff .imd-pending')", "the panel to be drawn")


def _mode(flow, name):
    flow.page.click("#diff .imd .seg[data-mode='%s']" % name)


# ---- P9: the page-side Difference size check ---------------------------------------------------

def build_budget(base):
    repo = H.make_repo("shop", base)
    # `mm` sizes are unknown to the server's header read, so ONLY the page can judge them. Before: a
    # wide, short image (4,199 x 94 px, 0.4 MP). After: its transpose (94 x 4,199 px, 0.4 MP). Each
    # side is tiny; the box that holds BOTH is 4,199 x 4,199 = 17.6 MP, over the 16 MP budget.
    put(repo, "img/turn.svg", _svg(1111, 25, "#3a3"))
    H._git(repo, "add", "-A")
    H._git(repo, "commit", "-q", "-m", "img")
    put(repo, "img/turn.svg", _svg(25, 1111, "#33a"))
    return repo


class ThePageBudgetsTheDifferenceByTheCommonBox(H.IdeFlow):
    """The pixel difference decodes BOTH images into one canvas as large as the widest by the tallest
    (`max(w) x max(h)`), so that box - not either image alone - is what must fit the budget. The
    server cannot read a size in `mm`, so the page learns it once the images load and refuses in
    words, before it starts a Worker or decodes anything."""

    FIXTURE = staticmethod(build_budget)

    def test_1_two_small_images_whose_common_box_is_over_budget_refuse_the_difference_in_words(self):
        p = self.page
        self.go("review")
        p.wait_for_selector("#nav-list li", state="attached")
        _pick(self, "turn.svg")
        self.until("document.querySelectorAll('#diff .imd img.imd-img').length === 2 && "
                   "[...document.querySelectorAll('#diff .imd img.imd-img')].every(i => i.complete && i.naturalWidth > 0)",
                   "both images loaded")
        sides = p.evaluate("[...document.querySelectorAll('#diff .imd img.imd-img')]"
                           ".map(i => i.naturalWidth * i.naturalHeight)")
        self.assertEqual(2, len(sides))
        for px in sides:
            self.assertLess(px, 1_000_000, "each image alone is far inside the budget (that is the point)")
        p.evaluate("(() => { const W = window.Worker; window.__workers = 0;"
                   " window.Worker = class extends W { constructor(...a) { window.__workers++; super(...a); } }; })()")
        _mode(self, "diff")
        text = self.text_until("#diff .imd-result", "over the 16 MP budget", "the budget note")
        self.assertRegex(text, r"\d+\.\d MP is over the 16 MP budget")
        self.assertEqual(0, p.evaluate("window.__workers"), "a Worker was started for a refused comparison")
        self.assertEqual(0, p.locator("#diff .imd canvas").count())
        self.assertTrue(p.evaluate("document.querySelector('#diff .imd-strip').hidden"),
                        "no progress strip is left for a comparison that never began")


# ---- P9: a late Worker answer after Cancel -----------------------------------------------------

def build_cancel(base):
    repo = H.make_repo("shop", base)
    put(repo, "img/big.png", png(1500, 1500))
    H._git(repo, "add", "-A")
    H._git(repo, "commit", "-q", "-m", "img")
    put(repo, "img/big.png", png(1500, 1500, block=(0, 0, 1500, 750, (30, 60, 220))))
    return repo


class ALateWorkerAnswerAfterCancelIsIgnored(H.IdeFlow):
    """Cancel says "no result is shown", and that must stay true when the Worker's finished answer
    was already in flight. The test HOLDS the Worker's `done` message in the page the moment the
    Worker has computed everything, proves it holds one (`__held` is a function), cancels, and only
    then delivers it. A page that paints a message from a job it cancelled shows a canvas and a
    count here."""

    FIXTURE = staticmethod(build_cancel)

    def test_1_the_finished_answer_arrives_after_cancel_and_paints_nothing(self):
        p = self.page
        self.go("review")
        p.wait_for_selector("#nav-list li", state="attached")
        p.evaluate("""() => { const W = window.Worker; window.__held = null;
            window.Worker = class extends W {
              set onmessage(fn) { super.onmessage = (ev) => {
                if (ev.data && ev.data.type === 'done') { window.__held = () => fn(ev); return; }
                fn(ev); }; }
              get onmessage() { return super.onmessage; } }; }""")
        _pick(self, "big.png")
        _mode(self, "diff")
        # the Worker has computed EVERYTHING; its `done` is parked in the page and `job` is still live
        self.until("typeof window.__held === 'function'", "the finished answer to be held")
        self.assertEqual(0, self.page.locator("#diff .imd canvas").count(), "nothing is drawn while it is held")
        p.click("#diff .imd-strip button")
        self.until("/Cancelled at row/.test((document.querySelector('#diff .imd-result')||{}).textContent||'')",
                   "the cancel note")
        self.assertIn("no result is shown", p.text_content("#diff .imd-result"))
        before = p.text_content("#diff .imd-result")
        p.evaluate("window.__held()")                       # the finished answer arrives AFTER Cancel
        self.assertEqual(0, p.locator("#diff .imd canvas").count(), "a cancelled run drew its late answer")
        self.assertEqual(0, p.locator("#diff .imd-sum").count(), "a cancelled run counted its late answer")
        self.assertEqual(before, p.text_content("#diff .imd-result"), "the cancel note was replaced")
        self.assertNotIn("pixels differ", p.text_content("#diff .imd"))


# ---- IDE-DIFF-CONTEXT-LOWS: a cancelled intra-line batch is asked again ----------------------------

N_LINES = 900


def build_intra(base):
    repo = H.make_repo("bare", base)
    H._write(repo, "long.txt", "".join("line %d value %d\n" % (i, i) for i in range(1, N_LINES + 1)))
    H._git(repo, "add", "-A")
    H._git(repo, "commit", "-q", "-m", "base")
    lines = ["line %d value %d\n" % (i, i) for i in range(1, N_LINES + 1)]
    lines[1] = "line 2 VALUE 2x\n"                      # the first hunk: one changed pair
    for i in range(N_LINES - 400, N_LINES):            # a far hunk: 400 changed rows to scroll to
        lines[i] = "line %d other %d\n" % (i + 1, i + 1)
    H._write(repo, "long.txt", "".join(lines))
    return repo


class ACancelledIntraLineBatchIsAskedAgain(H.IdeFlow):
    """The first ask for the first hunk's changed words comes back cancelled (the person pressed
    Cancel on its job). Before 0.0.29 the block stayed "asked" for good and its marks never came.
    Now it is asked again when it next comes into view — and only then: not by the observer's own
    first report while it is still on screen."""

    FIXTURE = staticmethod(build_intra)

    def test_1_cancel_then_scroll_away_and_back_asks_again_and_marks_the_words(self):
        p = self.page
        p.set_viewport_size({"width": 1280, "height": 800})
        held = []                       # no live push: only the page's own asks redraw the diff
        p.route("**/api/events*", lambda route: held.append(route))
        # Every ask for the first hunk's words is answered "cancelled" until the view has settled;
        # asks are counted PER BLOCK NODE, so a redraw of the file (a new node, asked afresh) is
        # never mistaken for the re-arm.
        # 0.0.29 wave 2 (W2-final-2): the wrapper goes in as an INIT script, before app.js assigns
        # MK.api. The reloaded page restores the review view on long.txt and asks for its words on
        # its own; a wrapper installed by `evaluate` after the reload lost that race (the real
        # answer was cached, nothing was asked again -> the flow timed out). And the restore, the
        # view switch and the click each redraw or hide the diff (a hidden block has LEFT the view,
        # so its re-ask is correct), so "the first ask was the only ask" was a snapshot of a race:
        # 1 run in 5 locally saw three nodes cancelled and the third re-asked before it looked.
        p.add_init_script("""(() => { const MK = (window.MK = window.MK || {}); let real = null;
            window.__per = new WeakMap(); window.__tb = null; window.__cancelAll = true;
            window.__n = () => (window.__tb && window.__per.get(window.__tb)) || 0;
            const wrap = async (u, b, o) => {
              if (u.startsWith('/api/diff/intraline') && JSON.stringify(b).includes('VALUE 2x')) {
                const tb = document.querySelector('#diff section.hunk tbody');
                const n = (window.__per.get(tb) || 0) + 1;
                window.__per.set(tb, n); window.__tb = tb;
                if (window.__cancelAll) return { error: 'Cancelled', cancelled: true };
              }
              return real(u, b, o); };
            Object.defineProperty(MK, 'api', { configurable: true, enumerable: true,
              get: () => real && wrap, set: (fn) => { real = fn; } }); })()""")
        p.reload()
        self.until("!!(window.MK && MK.ui && MK.dv)", "the page, reloaded with the stream held")
        try:
            self.go("review")
            p.wait_for_selector("#nav-list li", state="attached")
            p.locator("#nav-list li", has_text="long.txt").first.click()
            # the block now on screen was asked, every ask was cancelled, and it is re-armed; and it
            # stays so for two frames (the observer has had its chance to report the block still on
            # screen). A page that re-asked a shown block in a loop never gets here and reds.
            settled = "!!window.__tb && window.__tb.isConnected && window.__n() >= 1 && " \
                      "window.__tb === document.querySelector('#diff section.hunk tbody') && " \
                      "!window.__tb._asked && window.__tb._rearm"
            while True:
                self.until(settled, "the first hunk's words to be asked and cancelled")
                n0 = p.evaluate("(window.__tb0 = window.__tb, window.__n())")
                p.evaluate("new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
                if p.evaluate("window.__tb === window.__tb0 && (%s)" % settled):
                    break                  # a redraw or a hide in those frames: settle again
            self.assertEqual(n0, p.evaluate("window.__n()"), "re-asked while still on screen")
            self.assertEqual(0, p.evaluate("window.__tb.querySelectorAll('span.ix').length"))
            p.evaluate("() => { window.__cancelAll = false; }")     # the next ask is answered
            p.evaluate("[...document.querySelectorAll('#diff section.hunk')].pop().scrollIntoView({block: 'end'})")
            self.until("!window.__tb._rearm", "the block to have left the view")
            p.evaluate("window.__tb.scrollIntoView({block: 'start'})")
            self.until("window.__n() > %d" % n0, "the cancelled block to be asked again")
            self.assertTrue(p.evaluate("window.__tb.isConnected"), "the same block, not a redraw")
            self.until("window.__tb.querySelectorAll('span.ix').length > 0",
                       "the changed words of the first hunk to be marked")
        finally:
            p.evaluate("() => { window.__cancelAll = false; }")


# ---- ruling D6: a cluster edge that stands for an unconfirmed call is not drawn resolved -------------

def _mixed_ripple():
    """A ripple answer as the server sends it after Path B's page 1: Hub <- A (ring 1); three
    ring-2 callers of A sit in ONE cluster, so their three edges are drawn as one GROUP edge. The
    FIRST of them is proved (structural); the other two were matched by name only and are pending."""
    node = lambda i, ring, **k: dict({"id": i, "label": i, "kind": "function", "ring": ring,  # noqa: E731
                                      "symbol": i, "path": "src/%s.ts" % i.lower(), "line": 1}, **k)
    nodes = [node("Hub", 0, kind="center"), node("A", 1), node("C1", 2), node("N1", 2, pending=True),
             node("N2", 2, pending=True)]
    edges = [{"id": "e0", "source": "A", "target": "Hub", "kind": "CALLS", "basis": "structural",
              "site": "src/a.ts:4"},
             {"id": "e1", "source": "C1", "target": "A", "kind": "CALLS", "basis": "structural",
              "site": "src/c1.ts:6"},
             {"id": "e2", "source": "N1", "target": "A", "kind": "CALLS", "basis": "name-matched",
              "site": "src/n1.ts:3", "pending": True},
             {"id": "e3", "source": "N2", "target": "A", "kind": "CALLS", "basis": "name-matched",
              "site": "src/n2.ts:8", "pending": True}]
    return {"view": "ripple", "symbol": "Hub", "depth": 2, "nodes": nodes, "edges": edges,
            "basis": "approximate", "answer_bases": ["structural"], "bases": {"structural": 2},
            "more": True, "more_note": "2 call(s) matched by name only — not confirmed; Continue "
                                       "asks code-review-graph about 1 name(s)",
            "direct_callers": 1, "untested_chains": [], "untested_chains_total": 0,
            "untested_chains_exact": True,
            "draw": {"shown": ["Hub", "A"], "limit": 150, "total": 5,
                     "clusters": [{"id": "cluster:src", "label": "src · 3", "members": ["C1", "N1", "N2"],
                                   "ring": 2, "untested": 0}]}}


class AGroupHoldingAPendingCallIsPending(H.IdeFlow):
    """The verifier's F1: `visible()` built a cluster edge from its FIRST member, so a group whose
    first call is proved drew solid and resolved while it also held calls matched by name only. A
    group is pending, and not structural, when ANY member is; it says how many of its calls are."""

    def test_1_a_mixed_group_is_pending_and_counts_its_unconfirmed_calls(self):
        import json
        p = self.page
        body = json.dumps(_mixed_ripple())
        p.route("**/api/graph?*", lambda route: route.fulfill(status=200, body=body,
                                                               content_type="application/json"))
        try:
            self.until("!!(window.MK && MK.openGraph)", "the page")
            p.evaluate("MK.openGraph('ripple', 'Hub')")          # the door Review's "Expand graph" uses
            p.wait_for_selector("#view-graph:not([hidden])")
            self.until("(() => { const c = document.getElementById('cy');"
                       " return !!(c && c._cyreg && c._cyreg.cy && c._cyreg.cy.edges(\"[kind = 'GROUP']\").length); })()",
                       "the cluster edge to be drawn")
            got = p.evaluate("""() => { const cy = document.getElementById('cy')._cyreg.cy;
                const g = cy.edges("[kind = 'GROUP']");
                return { n: g.length, data: g[0].data(), opacity: +g[0].style('opacity') }; }""")
            self.assertEqual(1, got["n"])
            g = got["data"]
            self.assertEqual(3, g["count"])
            self.assertTrue(g.get("pending"), "a group holding unconfirmed calls is drawn resolved: %r" % g)
            self.assertEqual(2, g.get("pend"), g)
            self.assertNotEqual("structural", g["basis"], g)
            self.assertLess(got["opacity"], 0.55, "the group is not faded like a pending edge")
            # a person clicks the group edge: at its midpoint on the canvas
            mid = p.evaluate("""() => { const c = document.getElementById('cy'), cy = c._cyreg.cy;
                const m = cy.edges("[kind = 'GROUP']")[0].renderedMidpoint(), r = c.getBoundingClientRect();
                return [r.left + m.x, r.top + m.y]; }""")
            p.mouse.click(mid[0], mid[1])
            self.text_until("#g-sel", "2 of 3", "the group's details to say how many calls are unconfirmed")
            self.assertIn("matched by name only", p.text_content("#g-sel"))
        finally:
            p.unroute("**/api/graph?*")


if __name__ == "__main__":
    unittest.main()
