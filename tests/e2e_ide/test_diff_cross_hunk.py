"""IDE e2e - a one-side copy in the Split view across HUNKS (0.0.27 CI-FIX round 2, A2).

A Split view draws one table per hunk with a gap row between. A drag that starts on one side of
hunk 1 and ends in hunk 2 crosses that gap, and what is COPIED is still that side's code only - the
lines of every hunk the drag reaches, none of the other side's, none of the screen-reader
"removed:/added:" text. The unchanged lines elided between two hunks are not in the copy (they were
never selected: nothing in the gap row is code). A browser that splits a selection around
`user-select:none` into several ranges (Firefox) copies the same lines.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import unittest

import _harness as H

OLD = "".join("line %d\n" % i for i in range(1, 41))
NEW = OLD.replace("line 5\n", "line FIVE\n").replace("line 30\n", "line THIRTY\n")


def build(base):
    repo = H.make_repo("bare", base)
    H._write(repo, "two.txt", OLD)
    H._git(repo, "add", "-A")
    H._git(repo, "commit", "-q", "-m", "base")
    H._write(repo, "two.txt", NEW)
    return repo


class ACopyAcrossHunks(H.IdeFlow):
    FIXTURE = staticmethod(build)

    def test_0_the_split_view_has_one_table_per_hunk(self):
        p = self.page
        p.set_viewport_size({"width": 1280, "height": 1400})   # both ends of a drag on screen at once
        self.go("review")
        p.wait_for_selector("#nav-list li", state="attached")
        p.locator("#nav-list li", has_text="two.txt").first.click()
        p.wait_for_selector("#diff section.hunk table.d td.tx[data-i]")
        p.click(".dv-tools [data-mode='split']")
        p.wait_for_selector("#diff table.d.split td.tx[data-side='old']")
        self.assertEqual(2, p.evaluate("document.querySelectorAll('#diff section.hunk').length"),
                         "two hunks")


    def drag(self, side, first, last):
        p = self.page
        # the diff is not redrawn under the pointer: wait until #diff has stopped changing
        p.evaluate("""new Promise((done) => { const box = document.getElementById('diff'); let t;
            const quiet = () => { clearTimeout(t); t = setTimeout(() => { mo.disconnect(); done(true); }, 400); };
            const mo = new MutationObserver(quiet); mo.observe(box, { childList: true, subtree: true });
            quiet(); })""")
        a = p.locator(f"#diff table.split td.tx[data-side='{side}']", has_text=first).first
        b = p.locator(f"#diff table.split td.tx[data-side='{side}']", has_text=last).first
        p.evaluate("window.getSelection().removeAllRanges()")   # a press inside a selection drags it
        a.scroll_into_view_if_needed()
        ba, bb = a.bounding_box(), b.bounding_box()
        self.assertLess(bb["y"] + bb["height"], p.viewport_size["height"], "both ends are on screen")
        self.assertGreater(ba["y"], 0, "both ends are on screen")
        p.mouse.move(ba["x"] + 4, ba["y"] + ba["height"] / 2)
        p.mouse.down()
        p.mouse.move(bb["x"] + bb["width"] - 4, bb["y"] + bb["height"] / 2, steps=12)
        p.mouse.up()

    def copied(self):
        p = self.page
        p.evaluate("window.__copied = null; window.addEventListener('copy', "
                   "(e) => { window.__copied = e.clipboardData.getData('text/plain'); })")
        self.press("ControlOrMeta+C")
        self.until("window.__copied !== null", "the copy event")
        return p.evaluate("window.__copied")

    def test_1_an_old_side_drag_from_hunk_one_into_hunk_two_copies_old_lines_of_both(self):
        self.drag("old", "line 5", "line 30")
        got = self.copied()
        lines = got.split("\n")
        self.assertEqual("line 5", lines[0])
        self.assertEqual("line 30", lines[-1])
        for gone in ("FIVE", "THIRTY", "removed:", "added:", "unchanged"):
            self.assertNotIn(gone, got, "the other side or screen-reader text leaked into the copy")
        self.assertIn("line 6", lines, "the context of hunk one is in")
        self.assertIn("line 28", lines, "the context of hunk two is in")
        self.assertNotIn("line 20", lines, "the lines elided between the hunks were never selected")
        self.assertEqual(len(lines), len(set(lines)), "no line twice")
        self.assertEqual("two.txt:5-30", self.page.evaluate("MK.dv.selectionCite()"))

    def test_2_a_new_side_drag_copies_new_lines_of_both(self):
        self.drag("new", "line FIVE", "line THIRTY")
        got = self.copied()
        lines = got.split("\n")
        self.assertEqual("line FIVE", lines[0])
        self.assertEqual("line THIRTY", lines[-1])
        for gone in ("line 5\n", "removed:", "added:"):
            self.assertNotIn(gone, got)
        self.assertNotIn("line 5", lines)
        self.assertNotIn("line 30", lines)

    def test_3_a_selection_in_several_ranges_copies_the_same_lines(self):
        """Firefox gives one range per run between `user-select:none` cells. Chromium keeps one, so
        the selection is stood in for by an object that answers with two ranges."""
        p = self.page
        self.drag("old", "line 5", "line 30")
        got = p.evaluate("""(() => {
          const text = (td) => { const c = td.cloneNode(true);
              c.querySelectorAll('.dv-sr').forEach(x => x.remove()); return c.textContent.trim(); };
          const cell = (t) => [...document.querySelectorAll("#diff td.tx[data-side='old']")]
              .find(td => text(td) === t);
          const r1 = document.createRange(); r1.selectNodeContents(cell("line 5"));
          const r2 = document.createRange(); r2.selectNodeContents(cell("line 30"));
          const fake = { rangeCount: 2, isCollapsed: false, getRangeAt: (i) => [r1, r2][i],
                         anchorNode: r1.startContainer };
          const saved = window.getSelection;
          window.getSelection = () => fake;
          try { return MK.dv.splitSideText(); } finally { window.getSelection = saved; }
        })()""")
        self.assertEqual("line 5\nline 30", got)

    def test_4_a_plain_mousedown_elsewhere_ends_the_one_side_rule(self):
        p = self.page
        self.drag("old", "line 5", "line 30")
        p.mouse.click(5, 5)
        p.evaluate("window.getSelection().selectAllChildren(document.getElementById('diff'))")
        self.assertIsNone(p.evaluate("MK.dv.splitSideText()"),
                          "a selection that did not start in a Split cell is the browser's own")


if __name__ == "__main__":
    unittest.main()
