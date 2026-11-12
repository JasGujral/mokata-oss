"""IDE e2e - a one-side copy in the Split view across HUNKS (0.0.27 CI-FIX round 2, A2).

A Split view draws one table per hunk with a gap row between. A drag that starts on one side of
hunk 1 and ends in hunk 2 crosses that gap, and what is COPIED is still that side's code only - the
lines of every hunk the drag reaches, none of the other side's, none of the screen-reader
"removed:/added:" text. The unchanged lines elided between two hunks are not in the copy (nothing in
the gap row is code), and the copy SAYS so with a marker line, so a paste never reads as contiguous
code. The one-side rule belongs to the selection the drag made: a later selection (select-all, a
find-in-page match, a script) is the browser's own. A browser that splits a selection around
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
        for gone in ("FIVE", "THIRTY", "removed:", "added:"):
            self.assertNotIn(gone, got, "the other side or screen-reader text leaked into the copy")
        self.assertIn("line 6", lines, "the context of hunk one is in")
        self.assertIn("line 28", lines, "the context of hunk two is in")
        self.assertNotIn("line 20", lines, "the lines elided between the hunks were never selected")
        self.assertIn("\u22ef 18 unchanged lines \u22ef", lines, "the elision is said, not silent")
        self.assertEqual(lines.index("line 8") + 1, lines.index("\u22ef 18 unchanged lines \u22ef"))
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
        """Firefox splits ONE drag into a range per run of selectable cells (around the other side's
        `user-select:none` cells), and its ranges can stop short inside a cell. What the person dragged
        over is the union of them: the same drag told as two ranges with a hole in the middle copies
        the same lines. (Chromium keeps one range, so the split is stood in for here; the real Firefox
        drag is test_1/test_2 run in Firefox.)"""
        p = self.page
        self.drag("old", "line 5", "line 30")
        one = p.evaluate("MK.dv.splitSideText()")
        got = p.evaluate("""(() => {
          const text = (td) => { const c = td.cloneNode(true);
              c.querySelectorAll('.dv-sr').forEach(x => x.remove()); return c.textContent.trim(); };
          const cell = (t) => [...document.querySelectorAll("#diff td.tx[data-side='old']")]
              .find(td => text(td) === t);
          // the drag's whole extent: the FIRST range's start to the LAST range's end (Firefox may
          // hold a drag as several ranges; range 0 alone is only its first piece)
          const real = window.getSelection(), first = real.getRangeAt(0),
                whole = { startContainer: first.startContainer, startOffset: first.startOffset,
                          endContainer: real.getRangeAt(real.rangeCount - 1).endContainer,
                          endOffset: real.getRangeAt(real.rangeCount - 1).endOffset };
          const r1 = document.createRange(); r1.setStart(whole.startContainer, whole.startOffset);
          r1.setEnd(cell("line 7"), 0);                       // stops at the START of a cell
          const r2 = document.createRange(); r2.setStart(cell("line 28"), 0);
          r2.setEnd(whole.endContainer, whole.endOffset);
          const fake = { rangeCount: 2, isCollapsed: false, getRangeAt: (i) => [r1, r2][i],
                         anchorNode: real.anchorNode, anchorOffset: real.anchorOffset,
                         focusNode: real.focusNode, focusOffset: real.focusOffset };
          const saved = window.getSelection;
          window.getSelection = () => fake;
          try { return [MK.dv.splitSideText(), MK.dv.selectionCite()]; } finally { window.getSelection = saved; }
        })()""")
        self.assertEqual(one, got[0])
        self.assertEqual("two.txt:5-30", got[1])

    def test_4_a_plain_mousedown_elsewhere_ends_the_one_side_rule(self):
        p = self.page
        self.drag("old", "line 5", "line 30")
        p.mouse.click(5, 5)
        p.evaluate("window.getSelection().selectAllChildren(document.getElementById('diff'))")
        self.assertIsNone(p.evaluate("MK.dv.splitSideText()"),
                          "a selection that did not start in a Split cell is the browser's own")

    def copy_prevented(self):
        """True when the page took the copy over (preventDefault); False when the browser's own copy ran."""
        p = self.page
        p.evaluate("window.__prevented = null; window.addEventListener('copy', "
                   "(e) => { window.__prevented = e.defaultPrevented; })")
        self.press("ControlOrMeta+C")
        self.until("window.__prevented !== null", "the copy event")
        return p.evaluate("window.__prevented")

    def test_5_select_all_after_a_drag_is_the_browsers_own_copy(self):
        p = self.page
        self.drag("old", "line 5", "line 30")
        p.evaluate("document.activeElement && document.activeElement.blur()")
        self.press("ControlOrMeta+A")
        self.assertIsNone(p.evaluate("MK.dv.splitSideText()"), "select-all is not the drag's selection")
        self.assertFalse(self.copy_prevented(), "the page leaves a select-all copy to the browser")

    def select_cell(self, side, text):
        self.page.evaluate("""([side, text]) => {
          const t = (td) => { const c = td.cloneNode(true);
              c.querySelectorAll('.dv-sr').forEach(x => x.remove()); return c.textContent.trim(); };
          const td = [...document.querySelectorAll("#diff td.tx[data-side='" + side + "']")]
              .find(x => t(x) === text);
          window.getSelection().selectAllChildren(td);
        }""", [side, text])
        # the page lifts the one-side lock once it sees a selection that is not the drag's
        self.until("!document.querySelector('#diff table.split[data-sel]')", "the one-side lock lifted")

    def test_6_a_script_selection_on_the_other_side_after_a_drag_is_not_emptied(self):
        p = self.page
        self.drag("old", "line 5", "line 8")
        self.select_cell("new", "line THIRTY")                    # no mousedown: not the drag's selection
        self.assertIsNone(p.evaluate("MK.dv.splitSideText()"))
        self.assertFalse(self.copy_prevented(), "an empty clipboard would be a silent loss")

    def test_7_the_cite_of_that_selection_is_its_own_line_and_side(self):
        p = self.page
        self.drag("old", "line 5", "line 8")
        self.select_cell("new", "line THIRTY")
        self.assertEqual("two.txt:30", p.evaluate("MK.dv.selectionCite()"),
                         "line 30 of the new file, not the dragged side's number for it")

    def test_8_a_drag_selection_still_copies_one_side_after_the_listeners_are_in_play(self):
        p = self.page
        self.drag("new", "line FIVE", "line 8")
        self.assertTrue(self.copy_prevented())
        self.assertEqual("two.txt:5-8", p.evaluate("MK.dv.selectionCite()"))


if __name__ == "__main__":
    unittest.main()
