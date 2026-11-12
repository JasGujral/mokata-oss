"""IDE e2e - the edges of a one-side copy in the Split view (0.0.28 D, DIFF-SPLIT-SELECTION-LOWS; from
the unmerged 0.0.27 "Lows A" input, re-derived: every case is read from the CURRENT selection).

* A drag that starts in a gap row (outside every code cell) takes the side of the first code cell it
  enters.
* A row a selection only TOUCHES (a triple-click's end, a drag that stops at column 0, a blank line it
  ends on) is neither copied nor cited.
* A selection that STARTS on a blank line, or ends on one it covers, counts it; a blank line alone is
  never written to the clipboard as "".
* Shift+Arrow makes the drag longer, either way: it is still a one-side copy.
* A press that turns into a native drag-and-drop does not leave the page thinking a drag is on.
* After a cite click or a copy, at 200% zoom in a narrow window, focus never falls to <body> and the
  live region says what was copied.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import unittest

import _harness as H

BLANKS = {32, 33, 45}                 # old lines 32 and 33 are two blank lines in a row
OLD = "".join(("line %d\n" % i) if i not in BLANKS else "\n" for i in range(1, 71))
NEW = (OLD.replace("line 5\n", "line FIVE\n").replace("line 9\n", "line NINE\n")
       .replace("line 30\n", "line THIRTY\n").replace("line 36\n", "line THIRTYSIX\n"))


def build(base):
    repo = H.make_repo("bare", base)
    H._write(repo, "edge.txt", OLD)
    H._git(repo, "add", "-A")
    H._git(repo, "commit", "-q", "-m", "base")
    H._write(repo, "edge.txt", NEW)
    return repo


class CopyEdges(H.IdeFlow):
    FIXTURE = staticmethod(build)

    def test_0_open_in_split(self):
        p = self.page
        p.set_viewport_size({"width": 1280, "height": 1600})
        self.go("review")
        p.wait_for_selector("#nav-list li", state="attached")
        p.locator("#nav-list li", has_text="edge.txt").first.click()
        p.wait_for_selector("#diff section.hunk table.d td.tx[data-i]")
        p.click(".dv-tools [data-mode='split']")
        p.wait_for_selector("#diff table.d.split td.tx[data-side='old']")
        self.assertEqual(2, p.evaluate("document.querySelectorAll('#diff section.hunk').length"))

    # -- helpers ---------------------------------------------------------------------------------

    def quiet(self):
        self.page.evaluate("""new Promise((done) => { const box = document.getElementById('diff'); let t;
            const quiet = () => { clearTimeout(t); t = setTimeout(() => { mo.disconnect(); done(true); }, 400); };
            const mo = new MutationObserver(quiet); mo.observe(box, { childList: true, subtree: true });
            quiet(); })""")

    def cell(self, side, n):
        """The code cell of line `n` of `side`."""
        loc = self.page.locator(f"#diff table.split td.no[data-side='{side}'][data-n='{n}']").first
        return loc.locator("xpath=../td[contains(@class,'tx')][@data-side='%s']" % side)

    def box(self, side, n):
        c = self.cell(side, n)
        c.scroll_into_view_if_needed()
        return c.bounding_box()

    def press_drag(self, start, end, release=True):
        p = self.page
        self.quiet()
        p.evaluate("window.getSelection().removeAllRanges()")
        p.mouse.move(*start)
        p.mouse.down()
        p.mouse.move(*end, steps=14)
        if release:
            p.mouse.up()

    def drag_cells(self, side, a, b, a_dx=4, b_dx=None):
        ba = self.box(side, a)
        bb = self.box(side, b)
        end_x = bb["x"] + (bb["width"] - 4 if b_dx is None else b_dx)
        self.press_drag((ba["x"] + a_dx, ba["y"] + ba["height"] / 2), (end_x, bb["y"] + bb["height"] / 2))

    # Every negative assertion below ("no side is locked") first waits on a POSITIVE state: that the
    # page has handled the event the assertion is about. These listeners are added after the page's
    # own (which it adds at boot), so for the same event and phase they run after it: a count that
    # has moved means the page's handler has already run. No sleep stands in for that (verifier M3).
    def events(self):
        """The page's event counts so far: {selectionchange, mouseup, dragend, move: [x, y, buttons]}."""
        return self.page.evaluate("""(() => { if (!window.__ev) {
            const e = window.__ev = { selectionchange: 0, mouseup: 0, dragend: 0, move: null };
            document.addEventListener('selectionchange', () => { e.selectionchange++; });
            document.addEventListener('mouseup', () => { e.mouseup++; });
            document.addEventListener('dragend', () => { e.dragend++; });
            document.addEventListener('mousemove', (m) => { e.move = [m.clientX, m.clientY, m.buttons]; });
          } return Object.assign({}, window.__ev); })()""")

    def until_handled(self, before, kind, why):
        """Wait until the page has handled one more `kind` event than it had in `before`."""
        self.until("window.__ev.%s > %d" % (kind, before[kind]), why)

    def until_hover_at(self, x, y, why):
        """Wait until the page has handled a button-less mousemove at (x, y)."""
        self.until("(() => { const m = window.__ev.move; return !!m && Math.abs(m[0] - %f) < 1.5"
                   " && Math.abs(m[1] - %f) < 1.5 && m[2] === 0; })()" % (x, y), why)

    def copied(self):
        p = self.page
        p.evaluate("window.__copied = null; window.addEventListener('copy', "
                   "(e) => { window.__copied = e.clipboardData.getData('text/plain'); })")
        self.press("ControlOrMeta+C")
        self.until("window.__copied !== null", "the copy event")
        return p.evaluate("window.__copied")

    # -- a drag that starts outside every code cell ----------------------------------------------

    def gap_start(self):
        """The middle of the gap bar between the two hunks."""
        self.until("document.querySelectorAll('#diff .dv-gap-wrap').length >= 2", "both gap bars drawn")
        self.quiet()                                  # the diff is not redrawn under the pointer
        g = self.page.locator("#diff .dv-gap-wrap").nth(1)
        g.scroll_into_view_if_needed()
        return g.bounding_box()

    def test_1_a_drag_from_a_gap_row_copies_the_side_it_enters_first(self):
        p = self.page
        gb = self.gap_start()
        target = self.box("new", 31)
        # start in the gap bar, on the right half (the new column), and go down into the new side
        self.press_drag((gb["x"] + gb["width"] * 0.8, gb["y"] + gb["height"] / 2),
                        (target["x"] + target["width"] - 4, target["y"] + target["height"] / 2))
        got = self.copied()
        lines = got.split("\n")
        self.assertEqual("line 27", lines[0])
        self.assertIn("line THIRTY", lines)
        self.assertEqual("line 31", lines[-1])
        for gone in ("removed:", "added:", "unchanged lines to", "line 29\nline 30"):
            self.assertNotIn(gone, got, "one side only, no screen-reader text")
        self.assertNotIn("line 30", lines, "the old side is not in a new-side copy")

    # -- a row a selection only touches ----------------------------------------------------------

    def test_3_a_triple_click_cites_and_copies_its_own_line_only(self):
        p = self.page
        b = self.box("old", 29)
        self.quiet()
        p.mouse.click(b["x"] + 30, b["y"] + b["height"] / 2, click_count=3)
        self.assertEqual("edge.txt:29", p.evaluate("MK.dv.selectionCite()"))
        self.assertEqual("line 29", p.evaluate("MK.dv.splitSideText()"), "no empty line from the next row")

    def test_4_a_drag_up_from_column_zero_does_not_cite_the_row_it_started_on(self):
        p = self.page
        start = self.box("old", 29)
        end = self.box("old", 27)
        self.press_drag((start["x"] + 1, start["y"] + start["height"] / 2),
                        (end["x"] + 30, end["y"] + end["height"] / 2))
        self.assertEqual("edge.txt:27-28", p.evaluate("MK.dv.selectionCite()"))
        self.assertTrue(p.evaluate("MK.dv.splitSideText()").endswith("line 28"),
                        "the copy stops at the last line it covers")

    def test_5_a_drag_that_ends_on_a_blank_line_at_column_zero_does_not_count_it(self):
        p = self.page
        self.drag_cells("old", 31, 32, b_dx=1)
        self.assertEqual("edge.txt:31", p.evaluate("MK.dv.selectionCite()"))
        self.assertEqual("line 31", p.evaluate("MK.dv.splitSideText()"))

    def test_6_blank_lines_inside_the_selection_are_copied_and_cited(self):
        p = self.page
        self.drag_cells("old", 31, 34)
        self.assertEqual("edge.txt:31-34", p.evaluate("MK.dv.selectionCite()"))
        self.assertEqual(["line 31", "", "", "line 34"], p.evaluate("MK.dv.splitSideText()").split("\n"))

    def char_at(self, side, n, i):
        """The screen point just before character `i` of line `n`'s code on `side` (mid-line)."""
        self.cell(side, n).scroll_into_view_if_needed()
        return self.page.evaluate("""([side, n, i]) => {
            const no = document.querySelector(`#diff table.split td.no[data-side="${side}"][data-n="${n}"]`);
            const td = no.parentElement.querySelector(`td.tx[data-side="${side}"]`);
            const w = document.createTreeWalker(td, NodeFilter.SHOW_TEXT);
            let t; while ((t = w.nextNode()) && t.data.trim() === "") {}
            const r = document.createRange(); r.setStart(t, i); r.setEnd(t, i + 1);
            const b = r.getBoundingClientRect(); return [b.left + 1, b.top + b.height / 2]; }""", [side, n, i])

    def test_6e_a_drag_from_mid_line_to_mid_line_copies_from_and_to_those_columns(self):
        """The edge rows are cut where the drag began and ended, not taken whole."""
        p = self.page
        self.press_drag(self.char_at("old", 31, 5), self.char_at("old", 34, 6))
        self.assertEqual("edge.txt:31-34", p.evaluate("MK.dv.selectionCite()"))
        self.assertEqual(["31", "", "", "line 3"], p.evaluate("MK.dv.splitSideText()").split("\n"))

    def test_6a_a_drag_that_starts_on_a_blank_line_counts_it(self):
        p = self.page
        self.drag_cells("old", 32, 35, a_dx=4)
        self.assertEqual("edge.txt:32-35", p.evaluate("MK.dv.selectionCite()"))
        self.assertEqual("\n\nline 34\nline 35", p.evaluate("MK.dv.splitSideText()"))
        self.assertEqual("\n\nline 34\nline 35", self.copied())

    def test_6b_a_drag_up_that_ends_on_a_blank_line_counts_it(self):
        p = self.page
        start, end = self.box("old", 36), self.box("old", 33)
        self.press_drag((start["x"] + start["width"] - 4, start["y"] + start["height"] / 2),
                        (end["x"] + 4, end["y"] + end["height"] / 2))
        self.assertEqual("edge.txt:33-36", p.evaluate("MK.dv.selectionCite()"))
        self.assertEqual("\nline 34\nline 35\nline 36", p.evaluate("MK.dv.splitSideText()"))

    def test_6f_the_release_cell_is_the_one_under_the_pointer_not_the_mouseups_target(self):
        """The release cell is the code cell under the POINTER as the button comes up, whatever element
        the mouseup is targeted at (a captured or retargeted mouseup names another element). The
        mouseup here is sent on line 36's cell at line 33's point, and the blank line released on
        still counts. (Not the Linux runner's 6b cause - that is 6h - but the same contract.)"""
        p = self.page
        start, end = self.box("old", 36), self.box("old", 33)
        at = (end["x"] + 4, end["y"] + end["height"] / 2)
        self.press_drag((start["x"] + start["width"] - 4, start["y"] + start["height"] / 2), at, release=False)
        p.evaluate("""([x, y]) => {
            const no = document.querySelector("#diff table.split td.no[data-side='old'][data-n='36']");
            const td = no.parentElement.querySelector("td.tx[data-side='old']");
            td.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, clientX: x, clientY: y, button: 0 })); }""",
                   list(at))
        p.mouse.up()
        self.assertEqual("edge.txt:33-36", p.evaluate("MK.dv.selectionCite()"))
        self.assertEqual("\nline 34\nline 35\nline 36", p.evaluate("MK.dv.splitSideText()"))

    def test_6g_linux_firefoxs_per_cell_ranges_keep_the_blank_after_the_one_put_back(self):
        """The selection Linux Firefox leaves for test 6a's drag, rebuilt range for range from the
        mirror runner's dump (probe run 42605188045): the blank line 32 the drag began on is in NO
        range, then one range per row's code cell, each preceded by a collapsed range at its row's
        start. Line 32 is put back (`lead`), and blank line 33 - the first row any range covers - is
        then INSIDE the selection, never a snapped end: it is copied, not replaced by a gap marker."""
        p = self.page
        a, z = self.box("old", 32), self.box("old", 35)
        self.press_drag((a["x"] + 4, a["y"] + a["height"] / 2), (z["x"] + z["width"] - 4, z["y"] + z["height"] / 2),
                        release=False)                        # pressed on blank 32, the button still down in 35
        p.evaluate("""() => {
            const tr = (n) => document.querySelector(`#diff table.split td.no[data-side='old'][data-n='${n}']`).parentElement;
            const td = (n) => tr(n).querySelector("td.tx[data-side='old']");
            const R = (a, ao, z, zo) => { const r = document.createRange(); r.setStart(a, ao); r.setEnd(z, zo); return r; };
            const body = tr(33).parentNode, i33 = [...body.childNodes].indexOf(tr(33));
            const w = document.createTreeWalker(td(35), NodeFilter.SHOW_TEXT);
            let t35; while ((t35 = w.nextNode()) && t35.data.trim() === "") {}
            const rs = [R(body, i33, tr(33), 0), R(td(33), 0, tr(33), 2), R(tr(34), 0, tr(34), 0),
                        R(td(34), 0, tr(34), 2), R(tr(35), 0, tr(35), 0), R(td(35), 0, t35, t35.length)];
            const fake = { rangeCount: rs.length, isCollapsed: false, getRangeAt: (i) => rs[i],
                           anchorNode: td(35), anchorOffset: 0, focusNode: t35, focusOffset: t35.length };
            window.__realSel = window.getSelection;
            window.getSelection = () => fake; }""")
        try:
            p.mouse.up()                                      # the drag ends on the selection Linux Firefox made
            self.assertEqual("edge.txt:32-35", p.evaluate("MK.dv.selectionCite()"))
            self.assertEqual("\n\nline 34\nline 35", p.evaluate("MK.dv.splitSideText()"))
        finally:
            p.evaluate("window.getSelection = window.__realSel")

    def test_6h_linux_firefoxs_drag_up_onto_a_blank_line_leaves_it_out_of_every_range(self):
        """The selection Linux Firefox leaves for test 6b's drag (up from line 36's end to x+4 of blank
        line 33), rebuilt range for range from the mirror runner's dump (probe run 42605188045): blank
        33 is in NO range - the first range is collapsed at line 34's row - exactly as the blank line a
        drag DOWN starts on is. The line the drag up was released on is put back: cited and copied."""
        p = self.page
        a, z = self.box("old", 36), self.box("old", 33)
        self.press_drag((a["x"] + a["width"] - 4, a["y"] + a["height"] / 2), (z["x"] + 4, z["y"] + z["height"] / 2),
                        release=False)
        p.evaluate("""() => {
            const tr = (n) => document.querySelector(`#diff table.split td.no[data-side='old'][data-n='${n}']`).parentElement;
            const td = (n) => tr(n).querySelector("td.tx[data-side='old']");
            const R = (a, ao, z, zo) => { const r = document.createRange(); r.setStart(a, ao); r.setEnd(z, zo); return r; };
            const body = tr(34).parentNode, i34 = [...body.childNodes].indexOf(tr(34));
            const texts = []; const w = document.createTreeWalker(td(36), NodeFilter.SHOW_TEXT);
            for (let t; (t = w.nextNode());) if (t.data.trim() !== "") texts.push(t);
            const t0 = texts[0], t1 = texts[texts.length - 1];
            const rs = [R(body, i34, tr(34), 0), R(td(34), 0, tr(34), 2), R(tr(35), 0, tr(35), 0),
                        R(td(35), 0, tr(35), 2), R(tr(36), 0, tr(36), 0), R(td(36), 0, td(36), 0),
                        R(t0, 0, t1, t1.length)];
            const fake = { rangeCount: rs.length, isCollapsed: false, getRangeAt: (i) => rs[i],
                           anchorNode: tr(36), anchorOffset: 0, focusNode: body, focusOffset: i34 };
            window.__realSel = window.getSelection;
            window.getSelection = () => fake; }""")
        try:
            p.mouse.up()                                      # released over blank line 33
            self.assertEqual("edge.txt:33-36", p.evaluate("MK.dv.selectionCite()"))
            self.assertEqual("\nline 34\nline 35\nline 36", p.evaluate("MK.dv.splitSideText()"))
        finally:
            p.evaluate("window.getSelection = window.__realSel")

    def test_6c_a_selection_of_blank_lines_only_is_cited_and_never_copied_as_nothing(self):
        p = self.page
        self.drag_cells("old", 32, 34, b_dx=1)                 # blank 32, blank 33, stopping at col 0 of 34
        self.assertEqual("edge.txt:32-33", p.evaluate("MK.dv.selectionCite()"))
        self.assertEqual("\n", p.evaluate("MK.dv.splitSideText()"))
        self.assertNotEqual("", self.copied(), "an empty string is never written to the clipboard")

    def test_6d_a_triple_click_on_a_blank_line_cites_it_and_leaves_the_copy_to_the_browser(self):
        p = self.page
        b = self.box("old", 32)
        self.quiet()
        p.evaluate("window.getSelection().removeAllRanges()")
        p.mouse.click(b["x"] + 30, b["y"] + b["height"] / 2, click_count=3)
        # (Chromium 141 and Firefox select the empty line, so it is cited; Chromium 156 selects nothing at
        # all there - a collapsed caret - and then there is nothing to cite.)
        selected = not p.evaluate("window.getSelection().isCollapsed")
        self.assertEqual("edge.txt:32" if selected else None, p.evaluate("MK.dv.selectionCite()"))
        self.assertIsNone(p.evaluate("MK.dv.splitSideText()"), "nothing to write: not an empty string")
        p.evaluate("window.__prevented = null; window.addEventListener('copy', (e) => { window.__prevented = e.defaultPrevented; })")
        self.press("ControlOrMeta+C")
        self.until("window.__prevented !== null", "the copy event")
        self.assertFalse(p.evaluate("window.__prevented"), "the page does not take the copy over to write nothing")

    # -- the keyboard makes the drag longer ------------------------------------------------------

    def test_7_shift_arrow_after_a_drag_is_still_a_one_side_copy(self):
        p = self.page
        self.drag_cells("old", 5, 7)
        before = p.evaluate("MK.dv.splitSideText()")
        self.press("Shift+ArrowDown")                       # onto line 8 (a second would enter the gap bar)
        after = p.evaluate("MK.dv.splitSideText()")
        self.assertIsNotNone(after, "still the drag's selection, one side")
        self.assertGreater(len(after), len(before), "and longer")
        self.assertEqual("line 5", after.split("\n")[0])
        self.assertIn("line 8", after.split("\n"))
        for gone in ("FIVE", "added:", "removed:"):
            self.assertNotIn(gone, after)
        got = self.copied()
        self.assertEqual(after, got)

    def test_8_a_selection_that_changes_the_anchor_is_not_the_drag_extended(self):
        p = self.page
        self.drag_cells("old", 5, 7)
        self.press("ControlOrMeta+A")
        self.assertIsNone(p.evaluate("MK.dv.splitSideText()"))


    # -- a gap-bar drag going UP, and the keyboard after a drag in either direction -----------------

    def test_8a_a_drag_from_a_gap_bar_up_into_the_old_side_copies_that_side_only(self):
        p = self.page
        self.until("document.querySelectorAll('#diff .dv-gap-wrap').length >= 2", "both gap bars drawn")
        self.quiet()
        g = p.locator("#diff .dv-gap-wrap").nth(1)
        g.scroll_into_view_if_needed()
        lb = g.locator(".dv-gap-n").bounding_box()
        end = self.box("old", 7)
        self.press_drag((lb["x"] + 3, lb["y"] + lb["height"] / 2), (end["x"] + 4, end["y"] + end["height"] / 2))
        self.until("document.querySelectorAll('#diff table.split[data-sel]').length > 0", "the side stays locked")
        text = p.evaluate("MK.dv.splitSideText()")
        self.assertIsNotNone(text, "the drag's own selection, one side")
        self.assertEqual("line 7", text.split("\n")[0], "from the line it reached")
        self.assertIn("line 8", text.split("\n"))
        for gone in ("removed:", "added:", "FIVE"):
            self.assertNotIn(gone, text)
        self.assertEqual(text, self.copied(), "and that is what Copy writes")
        self.assertTrue(p.evaluate("MK.dv.selectionCite()").startswith("edge.txt:"))

    def keyboard_after(self, a, b, keys, a_dx, b_dx):
        """Drag old line `a` -> `b`, then press each key; each must stay a one-side, locked selection."""
        p = self.page
        pa, pb = self.box("old", a), self.box("old", b)
        self.press_drag((pa["x"] + (pa["width"] - 4 if a_dx < 0 else a_dx), pa["y"] + pa["height"] / 2),
                        (pb["x"] + (pb["width"] - 4 if b_dx < 0 else b_dx), pb["y"] + pb["height"] / 2))
        for key in keys:
            before = self.events()
            self.press(key)
            self.until_handled(before, "selectionchange", "the page saw the selection " + key + " made")
            text = p.evaluate("MK.dv.splitSideText()")
            self.assertIsNotNone(text, "after %s the selection is still the drag's, one side" % key)
            self.assertGreater(p.evaluate("document.querySelectorAll('#diff table.split[data-sel]').length"), 0,
                               "the lock is still held after " + key)
            for gone in ("FIVE", "NINE", "added:", "removed:"):
                self.assertNotIn(gone, text)
            self.assertEqual(text, self.copied(), "Copy writes it after " + key)

    def test_8b_shift_arrow_after_a_downward_drag_either_way(self):
        self.keyboard_after(6, 8, ["Shift+ArrowDown", "Shift+ArrowUp", "Shift+ArrowUp", "Shift+ArrowDown"], 4, -4)

    def test_8c_shift_arrow_after_an_upward_drag_either_way(self):
        self.keyboard_after(10, 8, ["Shift+ArrowUp", "Shift+ArrowDown", "Shift+ArrowDown", "Shift+ArrowUp"], -4, 4)

    # -- a press that becomes a native drag-and-drop ----------------------------------------------

    def test_8d_a_native_drag_and_drop_does_not_leave_a_drag_running(self):
        p = self.page
        self.drag_cells("old", 6, 8)
        a = self.box("old", 7)
        self.quiet()
        before = self.events()
        p.mouse.move(a["x"] + 20, a["y"] + a["height"] / 2)      # press ON the selected text: a native drag
        p.mouse.down()
        p.mouse.move(a["x"] + 20, a["y"] + a["height"] / 2 + 160, steps=10)
        p.mouse.up()
        self.until("window.__ev.dragend > %d || window.__ev.mouseup > %d" % (before["dragend"], before["mouseup"]),
                   "the drag-and-drop (or the press) has ended in the page")
        p.evaluate("window.getSelection().removeAllRanges()")
        n = self.box("new", 28)
        hx, hy = n["x"] + 20, n["y"] + n["height"] / 2
        p.mouse.move(hx, hy, steps=5)                                     # a plain hover, no button
        self.until_hover_at(hx, hy, "the page handled the hover")
        self.assertEqual(0, p.evaluate("document.querySelectorAll('#diff table.split[data-sel]').length"),
                         "hovering with no button pressed locks no side")
        self.drag_cells("old", 5, 7)                                      # and the next drag copies one side
        self.assertEqual("line 5", p.evaluate("MK.dv.splitSideText()").split("\n")[0])

    def test_8e_a_hover_with_no_button_down_locks_no_side_even_if_a_press_never_ended(self):
        p = self.page
        self.until("document.querySelectorAll('#diff .dv-gap-wrap').length >= 2", "both gap bars drawn")
        self.quiet()
        self.events()
        p.evaluate("""document.querySelector('#diff .dv-gap-wrap').dispatchEvent(
            new MouseEvent('mousedown', { bubbles: true, button: 0, buttons: 1 }))""")   # a press whose release the page never saw
        n = self.box("new", 28)
        hx, hy = n["x"] + 20, n["y"] + n["height"] / 2
        p.mouse.move(hx, hy, steps=5)
        self.until_hover_at(hx, hy, "the page handled the hover")
        self.assertEqual(0, p.evaluate("document.querySelectorAll('#diff table.split[data-sel]').length"),
                         "no button is down: a hover is not a drag")
        p.evaluate("document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))")

    # -- where the selection starts NOW, not where the pointer has been --------------------------

    def gutter(self, side, n):
        loc = self.page.locator(f"#diff table.split td.no[data-side='{side}'][data-n='{n}']").first
        loc.scroll_into_view_if_needed()
        return loc.bounding_box()

    def test_90_an_upward_drag_that_hovers_a_blank_and_is_released_on_the_next_gutter_starts_there(self):
        p = self.page
        self.quiet()
        p.evaluate("window.getSelection().removeAllRanges()")
        s37, b33, g34 = self.box("old", 37), self.box("old", 33), self.gutter("old", 34)
        p.mouse.move(s37["x"] + s37["width"] - 4, s37["y"] + s37["height"] / 2)
        p.mouse.down()
        p.mouse.move(b33["x"] + 4, b33["y"] + b33["height"] / 2, steps=12)         # over blank 33 ...
        p.mouse.move(g34["x"] + g34["width"] / 2, g34["y"] + g34["height"] / 2, steps=4)   # ... then off it
        p.mouse.up()                                                                # onto row 34's line-number gutter
        cite = p.evaluate("MK.dv.selectionCite()")
        self.assertEqual("edge.txt:34-37", cite, "the selection starts at line 34: the blank it hovered is not in it")
        self.assertTrue(p.evaluate("MK.dv.splitSideText()").startswith("line 34"))

    def test_91_a_script_that_moves_the_start_off_the_blank_drops_the_blank(self):
        p = self.page
        self.drag_cells("old", 32, 36)
        self.assertEqual("edge.txt:32-36", p.evaluate("MK.dv.selectionCite()"), "the blank it began on counts")
        p.evaluate("""() => { const s = window.getSelection();
            const td = document.querySelector("#diff td.no[data-side='old'][data-n='34']").parentElement
                .querySelector("td.tx[data-side='old']");
            s.setBaseAndExtent(td, 0, s.focusNode, s.focusOffset); }""")
        self.assertEqual("edge.txt:34-36", p.evaluate("MK.dv.selectionCite()"))
        self.assertEqual("line 34\nline 35\nline 36", p.evaluate("MK.dv.splitSideText()"))

    # -- a selection the drag did not make, while the button is held ------------------------------

    def test_92_a_script_selection_during_a_gap_started_drag_stays_native(self):
        p = self.page
        gb = self.gap_start()
        p.evaluate("window.getSelection().removeAllRanges()")
        p.mouse.move(gb["x"] + gb["width"] * 0.8, gb["y"] + gb["height"] / 2)
        p.mouse.down()
        before = self.events()
        p.evaluate("window.getSelection().selectAllChildren(document.querySelectorAll(\"#diff td.tx[data-side='new']\")[12])")
        self.until_handled(before, "selectionchange", "the page saw the script's selection")
        p.mouse.up()
        self.until_handled(before, "mouseup", "the page saw the release")
        self.assertEqual(0, p.evaluate("document.querySelectorAll('#diff table.split[data-sel]').length"),
                         "no side was taken from a selection the drag did not make")
        self.assertIsNone(p.evaluate("MK.dv.splitSideText()"), "the browser's own copy")

    def test_92a_a_second_script_selection_with_another_anchor_is_not_the_drag_either(self):
        p = self.page
        gb = self.gap_start()
        p.evaluate("window.getSelection().removeAllRanges()")
        p.mouse.move(gb["x"] + gb["width"] * 0.8, gb["y"] + gb["height"] / 2)
        p.mouse.down()
        p.mouse.move(gb["x"] + gb["width"] * 0.8 + 3, gb["y"] + gb["height"] / 2)      # the pointer has moved
        before = self.events()
        p.evaluate("window.getSelection().selectAllChildren(document.querySelector('#diff .dv-gap-wrap'))")
        self.until_handled(before, "selectionchange", "the page saw the first script selection")
        mid = self.events()
        p.evaluate("window.getSelection().selectAllChildren(document.querySelectorAll(\"#diff td.tx[data-side='new']\")[12])")
        self.until_handled(mid, "selectionchange", "the page saw the second script selection")
        p.mouse.up()
        self.until_handled(mid, "mouseup", "the page saw the release")
        self.assertEqual(0, p.evaluate("document.querySelectorAll('#diff table.split[data-sel]').length"),
                         "the anchor moved: this is not the selection the drag began")
        self.assertIsNone(p.evaluate("MK.dv.splitSideText()"))

    # -- a right-click is not a drag ---------------------------------------------------------------

    def test_93_a_right_press_starts_no_drag_and_keeps_the_one_side_copy(self):
        p = self.page
        self.drag_cells("old", 5, 7)
        text = p.evaluate("MK.dv.splitSideText()")
        self.assertIsNotNone(text)
        # the press whose release a context menu swallows (macOS opens it on the press)
        p.evaluate("""document.querySelector("#diff td.no[data-side='old'][data-n='6']").parentElement
            .querySelector("td.tx[data-side='old']").dispatchEvent(
              new MouseEvent('mousedown', { bubbles: true, button: 2, buttons: 2 }))""")
        self.assertEqual(text, p.evaluate("MK.dv.splitSideText()"), "the selection is still the drag's, one side")
        self.assertEqual(text, self.copied(), "and the menu's Copy writes it")

    def test_94_a_context_menu_ends_a_drag_whose_release_never_comes(self):
        p = self.page
        p.evaluate("window.getSelection().removeAllRanges()")
        a, b = self.box("old", 5), self.box("old", 7)
        self.quiet()
        p.mouse.move(a["x"] + 4, a["y"] + a["height"] / 2)
        p.mouse.down()
        p.mouse.move(b["x"] + b["width"] - 4, b["y"] + b["height"] / 2, steps=8)
        p.evaluate("document.querySelector('#diff').dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, button: 2 }))")
        self.assertIsNotNone(p.evaluate("MK.dv.splitSideText()"), "no drag is running: it is the one-side copy")
        p.mouse.up()

    # -- 200% zoom, a narrow window: focus and the announcement --------------------------------------

    def test_95_no_focus_loss_at_200_percent_in_a_narrow_window(self):
        p = self.page
        self.drag_cells("old", 5, 7)               # the drag at 100% (a pointer maps 1:1), the rest at 200%
        p.set_viewport_size({"width": 640, "height": 900})
        p.evaluate("document.documentElement.style.zoom = '2'")
        try:
            got = self.copied()
            self.assertEqual("line 5", got.split("\n")[0])
            self.assertFalse(p.evaluate("document.activeElement === document.body"),
                             "focus fell to <body> after a drag and a copy")
            p.evaluate("window.__copied = []; navigator.clipboard.writeText = (t) => "
                       "{ window.__copied.push(t); return Promise.resolve(); }; 0")
            no = p.locator("#diff table.split td.no[data-side='old'][data-n='6']").first
            no.scroll_into_view_if_needed()
            no.click()
            self.until("window.__copied.includes('edge.txt:6')", "the cite copied")
            self.text_until("#dv-live", "edge.txt:6", "the live region announces the cite")
            self.assertFalse(p.evaluate("document.activeElement === document.body"),
                             "focus fell to <body> after a cite click")
        finally:
            p.evaluate("document.documentElement.style.zoom = ''")
            p.set_viewport_size({"width": 1280, "height": 1600})


if __name__ == "__main__":
    unittest.main()
