"""IDE e2e - a one-side copy costs what the SELECTION costs, not what the hunk costs (0.0.28 D,
DIFF-SPLIT-SELECTION-LOWS; the 0.0.27 H1 regression: copy 275 ms -> 7.7 s, cite 0.9 -> 9.4 s and
growing per call on a 3,136-row hunk).

The rules, in a 3,136-row hunk and in a 30,000-line single hunk (every one of its 30,000 rows BUILT:
the diff builds 3,000 rows eagerly and the rest as they scroll near, so the large case fills them all
first - without that it was a ~3.1k-row table, no larger than the small one; verifier M4):
  * the page visits the rows between the selection's ends, and no others (`MK.dv.visits`) - THE bound:
    a count, exact on every machine;
  * the SAME copy / cite costs the same after 2,400 calls as after the first few: nothing accumulates
    (0.0.28 D found each call costing ~0.4 us more than the last - a clone + node removal per copy while
    every Range made so far was still attached to the document; 6 calls under a 1 ms floor hid it);
  * a 3,000-row selection costs about 250x a 12-row one, not more (linear in the selection).
The two timing rules are a coarse BACKSTOP behind the visit counts (an accumulation the counts cannot
see; a per-row cost that went quadratic, ~60,000x). Times are RELATIVE to earlier calls on the same
page - never an absolute ms cap - and each "round" is 1,000 calls, so a browser's 1 ms clock
(Firefox) is a few percent of what it measures. The runner has shown 114-318x for the ratio; the
bound is 1,000x (verifier M4: 400x left 1.26x headroom on a blocking check).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import unittest

import _harness as H

FILES = {"h3k.txt": 3136, "h30k.txt": 30000}
SELECTED = 12
BATCH = 1000       # calls per round: ~15-30 ms a round, against Firefox's 1 ms clock
ROUNDS = 8
LINEAR = 1000      # a 3,000-row copy costs under 1,000x a 12-row one (ideal 250x; quadratic ~60,000x)


def build(base):
    repo = H.make_repo("bare", base)
    for name, n in FILES.items():
        H._write(repo, name, "".join("old %d\n" % i for i in range(1, n + 1)))
    H._git(repo, "add", "-A")
    H._git(repo, "commit", "-q", "-m", "base")
    for name, n in FILES.items():
        H._write(repo, name, "".join("new %d\n" % i for i in range(1, n + 1)))
    return repo


class CopyAndCiteAreSelectionBound(H.IdeFlow):
    FIXTURE = staticmethod(build)

    def open_split(self, name):
        p = self.page
        p.set_viewport_size({"width": 1280, "height": 1400})
        self.go("review")
        p.wait_for_selector("#nav-list li", state="attached")
        p.locator("#nav-list li", has_text=name).first.click()
        self.until("!!document.querySelector('#diff section.hunk table.d td.tx[data-i]') && "
                   "MK.ui.files[MK.ui.sel] && MK.ui.files[MK.ui.sel].path === %r" % name,
                   "%s to draw" % name)
        if p.evaluate("MK.dv.mode") != "split":
            p.click(".dv-tools [data-mode='split']")
        self.until("document.querySelectorAll(\"#diff table.d.split td.tx[data-side='old']\").length >= 3000",
                   "the split rows of %s" % name)
        # every row BUILT, through the diff's own "built as you scroll" fill: the large case must be a
        # larger table than the small one, not its 3,000 eager rows and placeholders
        p.evaluate("document.querySelectorAll('#diff tbody').forEach((tb) => { if (tb._fill && !tb._done) tb._fill(); })")
        built = p.evaluate("document.querySelectorAll(\"#diff table.d.split td.tx[data-side='old']\").length")
        self.assertGreaterEqual(built, FILES[name], "%s: every row of the hunk is in the table" % name)

    def drag(self):
        p = self.page
        cells = p.locator("#diff table.split td.tx[data-side='old']")
        a, b = cells.nth(1), cells.nth(SELECTED)
        a.scroll_into_view_if_needed()
        ba, bb = a.bounding_box(), b.bounding_box()
        p.evaluate("window.getSelection().removeAllRanges()")
        p.mouse.move(ba["x"] + 4, ba["y"] + ba["height"] / 2)
        p.mouse.down()
        p.mouse.move(bb["x"] + bb["width"] - 4, bb["y"] + bb["height"] / 2, steps=10)
        p.mouse.up()
        self.assertEqual("old 2", p.evaluate("MK.dv.splitSideText()").split("\n")[0])

    def measure(self, rounds=ROUNDS, batch=BATCH):
        """[(copy ms, cite ms, rows visited by one copy, rows visited by one cite)] per round."""
        return self.page.evaluate("""(arg) => {
            const out = [];
            const [rounds, batch] = arg;
            for (let i = 0; i < rounds; i++) {
              MK.dv.visits = 0; MK.dv.splitSideText(); const v = MK.dv.visits;
              MK.dv.visits = 0; MK.dv.selectionCite(); const vc = MK.dv.visits;
              let t0 = performance.now();
              for (let k = 0; k < batch; k++) MK.dv.splitSideText();
              const copy = performance.now() - t0;
              t0 = performance.now();
              for (let k = 0; k < batch; k++) MK.dv.selectionCite();
              out.push([copy, performance.now() - t0, v, vc]);
            }
            return out; }""", [rounds, batch])

    def extend_to(self, rows):
        """Keep the drag's start and move its end `rows` cells down (the drag made longer).

        The start is the FIRST range's, not `anchorNode`: Firefox may hold a drag as several ranges
        and reports the anchor of the LAST one (the Linux runner's 13-3000 red), so `anchorNode`
        is not where the drag began in every browser."""
        self.page.evaluate("""(n) => { const s = window.getSelection(), r0 = s.getRangeAt(0);
            const td = document.querySelectorAll("#diff table.split td.tx[data-side='old']")[n];
            s.setBaseAndExtent(r0.startContainer, r0.startOffset, td, td.childNodes.length); }""", rows)

    def check(self, name):
        self.open_split(name)
        self.drag()
        got = self.measure()
        for _copy, _cite, visits, cite_visits in got:          # the bound: rows walked, counted
            self.assertLessEqual(visits, SELECTED + 2, "%s: the copy visited %d rows for a %d-row selection"
                                 % (name, visits, SELECTED))
            self.assertLessEqual(cite_visits, SELECTED + 2, "%s: the cite visited %d rows for a %d-row selection"
                                 % (name, cite_visits, SELECTED))
        mean = lambda xs: sum(xs) / len(xs)  # noqa: E731
        for k, what in ((0, "copy"), (1, "cite")):              # the backstop: nothing accumulates
            early = mean([r[k] for r in got[1:4]])          # round 0 is warm-up
            late = mean([r[k] for r in got[-3:]])
            self.assertLessEqual(late, 3 * max(early, 3.0),
                                 "%s: the %s grew with use: %r" % (name, what, [round(r[k], 1) for r in got]))
        small = mean([r[0] for r in got[1:]]) / BATCH          # ms per 12-row copy
        self.extend_to(2999)
        self.assertEqual("%s:2-3000" % name, self.page.evaluate("MK.dv.selectionCite()"),
                         "the drag made longer is still the drag's")
        big_run = self.measure(rounds=3, batch=20)
        big = mean([r[0] for r in big_run]) / 20              # ms per 3,000-row copy
        import sys
        print("\n[timing] %s: %d rounds x %d calls, 12-row copy ms/round %s, cite ms/round %s; per call "
              "12 rows %.4f ms, 3000 rows %.2f ms (ratio %.0fx, linear bound %dx); visits %d"
              % (name, ROUNDS, BATCH, [round(r[0], 1) for r in got], [round(r[1], 1) for r in got],
                 small, big, big / max(small, 1e-4), LINEAR, big_run[0][2]), file=sys.stderr)
        for r in big_run:                                       # the bound, for the long selection too
            self.assertGreaterEqual(r[2], 2999 - 1, "%s: the copy walks the whole selection" % name)
            self.assertLessEqual(r[2], 2999 + 2, "%s: visits follow the selection, not the table" % name)
            self.assertLessEqual(r[3], 2999 + 2, "%s: the cite's visits follow the selection" % name)
        self.assertLess(big, LINEAR * max(small, 0.001), "%s: 3,000 rows cost %.2f ms, 12 rows %.4f ms"
                        % (name, big, small))

    def test_1_a_3136_row_hunk(self):
        self.check("h3k.txt")

    def test_2_a_30000_line_single_hunk(self):
        self.check("h30k.txt")


if __name__ == "__main__":
    unittest.main()
