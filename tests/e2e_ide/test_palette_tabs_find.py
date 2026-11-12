"""IDE e2e — the editor keys (0.0.26 O9 over P2 tabs, P3 palette + find), driven by KEYBOARD the
way a user drives them: every key goes through the page's own registry, so a binding that stops
firing reds here even when the command itself still works from a click.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import unittest

import _harness as H


class ThePaletteRunsCommandsAndJumps(H.IdeFlow):
    FIXTURE = "shop"

    def test_1_gt_in_mod_k_lists_ide_commands_with_their_shortcuts(self):
        p = self.page
        self.until("!!document.getElementById('qo-open')", "the page")
        self.press("Control+K")
        p.wait_for_selector("#qo:not([hidden])")
        p.fill("#qo-input", ">close tab")
        row = p.locator("#qo-results .qo-row", has_text="Tabs: close tab").first
        row.wait_for()
        self.assertTrue(row.locator("kbd").count() > 0, "the command row shows no shortcut")
        self.press("Escape")
        p.wait_for_selector("#qo", state="hidden")

    def test_2_slash_lists_the_mokata_commands_to_paste_into_claude_code(self):
        p = self.page
        self.press("Control+K")
        p.wait_for_selector("#qo:not([hidden])")
        p.fill("#qo-input", "/spe")
        row = p.locator("#qo-results .qo-row", has_text="/mokata:").first
        row.wait_for()
        self.assertIn("/mokata:", row.text_content())
        self.press("Escape")

    def test_3_at_jumps_to_a_symbol_in_the_current_file(self):
        p = self.page
        self.open_file("shop/text.py")
        self.press("Control+K")
        p.wait_for_selector("#qo:not([hidden])")
        p.fill("#qo-input", "@title_slug")
        p.locator("#qo-results .qo-row", has_text="title_slug").first.wait_for()
        self.press("Enter")
        self.until("MK.nav.path === 'shop/text.py' && MK.nav.line === 5",
                   "the cursor on title_slug's line (5)")

    def test_4_colon_in_mod_k_goes_to_a_line(self):
        p = self.page
        self.press("Control+K")
        p.wait_for_selector("#qo:not([hidden])")
        p.fill("#qo-input", ":2")
        # wait for THE line row (a row from the previous query may still be first for a moment)
        self.until("(() => { const r = document.querySelector('#qo-results .qo-row');"
                   " return !!(r && r.textContent.includes('Go to line 2')); })()", "the line row")
        self.press("Enter")
        self.until("MK.nav.line === 2", "line 2")


class TabsOpenCloseAndReopen(H.IdeFlow):
    FIXTURE = "shop"

    def tabs(self):
        return self.page.evaluate(
            "[...document.querySelectorAll('#tabstrip .tab .tab-name')].map(n => n.textContent)")

    def active(self):
        return self.page.evaluate(
            "(() => { const t = document.querySelector('#tabstrip .tab.active .tab-name');"
            " return t ? t.textContent : null; })()")

    def count_is(self, n, what):
        self.until("document.querySelectorAll('#tabstrip .tab').length === %d" % n, what)

    def test_0_close_all_from_the_palette_empties_the_strip(self):
        # The review view opens the uncommitted change as a tab once the first load lands; wait
        # for THAT (a close-all sent before it closes nothing that is there yet), then start
        # from none.
        p = self.page
        self.until("document.getElementById('diff-title').textContent !== 'Loading…'"
                   " && document.querySelectorAll('#tabstrip .tab').length > 0", "the review's tab")
        self.press("Control+K")
        p.fill("#qo-input", ">tabs: close all")
        p.locator("#qo-results .qo-row", has_text="Tabs: close all").first.click()
        try:
            self.count_is(0, "no tab left")
        except AssertionError as exc:
            raise AssertionError("%s — still open: %s" % (exc, self.tabs()))

    def test_1_two_files_kept_open_are_two_tabs(self):
        self.open_file("shop/text.py")
        self.page.locator("#tabstrip .tab", has_text="text.py").first.dblclick()   # keep (not preview)
        # (an UNCHANGED file: a changed one opens as a diff tab in Review)
        self.open_file("tests/test_text.py")
        self.count_is(2, "two tabs")
        self.assertEqual(["text.py", "test_text.py"], self.tabs())
        self.assertEqual("test_text.py", self.active())

    def test_1b_the_tab_controls_are_24_px_targets(self):
        """§6 WCAG 2.5.8 (UX-SIMPLIFY): the active tab's × is a 24×24 hit area at least, not an
        18 px glyph; Δ (on a tab that has a change) is held to the same bar by its CSS."""
        box = self.page.locator("#tabstrip .tab.active .tab-x").bounding_box()
        self.assertGreaterEqual(min(box["width"], box["height"]), 24, box)
        dl = self.page.evaluate(
            "(() => { const b = document.createElement('button'); b.className = 'tab-delta';"
            " document.getElementById('tabstrip').append(b); const r = b.getBoundingClientRect();"
            " b.remove(); return [r.width, r.height]; })()")
        self.assertGreaterEqual(min(dl), 24, dl)

    def test_2_alt_w_closes_the_active_tab(self):
        self.press("Alt+W")
        self.count_is(1, "one tab left")
        self.assertEqual(["text.py"], self.tabs())
        self.until("MK.nav.path === 'shop/text.py'", "the remaining tab's file shown")

    def test_3_alt_shift_t_reopens_it(self):
        self.press("Alt+Shift+T")
        self.count_is(2, "the tab reopened")
        self.assertIn("test_text.py", self.tabs())
        self.until("MK.nav.path === 'tests/test_text.py'", "the reopened file shown")

    def test_4_alt_1_switches_to_the_first_tab(self):
        self.press("Alt+1")
        self.until("MK.nav.path === 'shop/text.py'", "tab 1's file")
        self.assertEqual("text.py", self.active())


class FindInTheCurrentFile(H.IdeFlow):
    FIXTURE = "shop"

    def marks(self):
        return self.page.evaluate("document.querySelectorAll('#code mark').length")

    def test_1_mod_f_counts_and_highlights_every_match(self):
        p = self.page
        self.until("!!document.getElementById('qo-open')", "the page")
        self.open_file("shop/text.py")
        self.before = p.text_content("#code")
        self.press("Control+F")
        p.wait_for_selector("#find-bar:not([hidden])")
        p.fill("#find-input", "slug")
        # text.py: `def slugify`, `def title_slug`, `return slugify(` -> 3; ".join(title" is not one.
        # ONE atomic read of the state: the count AND the marks it counts. (Highlight tokens can
        # arrive after the search and repaint the rows; the bar re-searches, so the state settles
        # to this - waiting for the count alone and reading the marks later is the race.)
        self.until("(() => { const c = document.querySelector('#find-bar .fb-count');"
                   " return !!c && c.textContent.trim() === '1 of 3'"
                   " && document.querySelectorAll('#code mark').length === 3; })()",
                   "the count to read 1 of 3 with 3 highlights in the text")

    def test_1b_a_repaint_never_leaves_a_stale_count(self):
        """A re-render that detaches the marks must not leave the old `n of m` standing: right
        after the repaint (one microtask, before the debounced re-search) the count is not a
        number; once settled it is 1 of 3 again with 3 highlights."""
        stale = self.page.evaluate(
            "(async () => { MK.paintCodeRows(true); await Promise.resolve();"
            " return [document.querySelector('#find-bar .fb-count').textContent.trim(),"
            " document.querySelectorAll('#code mark').length]; })()")
        self.assertFalse(stale[0] == "1 of 3" and stale[1] == 0, "stale count over zero marks: %r" % (stale,))
        self.until("(() => document.querySelector('#find-bar .fb-count').textContent.trim() === '1 of 3'"
                   " && document.querySelectorAll('#code mark').length === 3)()",
                   "the bar to settle again after the repaint")

    def test_1c_closing_cancels_a_pending_re_search(self):
        """Esc inside the 60 ms re-search window must not leave highlights behind. Deterministic:
        the page's timers are captured, a repaint arms the re-search, Esc closes the bar, and only
        THEN are the captured timers fired by hand (as if the 60 ms had elapsed)."""
        r = self.page.evaluate(
            "(async () => { const t0 = window.setTimeout, c0 = window.clearTimeout, q = new Map(); let n = 0;"
            " window.setTimeout = (fn) => { const id = ++n; q.set(id, fn); return id; };"
            " window.clearTimeout = (id) => { q.delete(id); };"
            " try { MK.paintCodeRows(true); await Promise.resolve();"
            "  document.getElementById('find-input').dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));"
            "  for (const fn of [...q.values()]) fn(); }"
            " finally { window.setTimeout = t0; window.clearTimeout = c0; }"
            " return document.querySelectorAll('#code mark').length; })()")
        self.assertEqual(0, r, "highlights left behind by a re-search that outlived the bar")
        self.press("Control+F")                        # back to the open-bar state test_2 steps in
        self.until("(() => document.querySelector('#find-bar .fb-count').textContent.trim() === '1 of 3'"
                   " && document.querySelectorAll('#code mark').length === 3)()",
                   "the bar to reopen on 1 of 3")

    def test_2_enter_steps_to_the_next_match(self):
        self.press("Enter")
        self.text_until("#find-bar .fb-count", "2 of 3", "the count to step to 2 of 3")

    def test_3_escape_closes_and_leaves_the_text_as_it_was(self):
        p = self.page
        self.press("Escape")
        p.wait_for_selector("#find-bar", state="hidden")
        self.assertEqual(0, self.marks(), "highlights left behind after the bar closed")
        self.assertIn("def slugify(title):", p.text_content("#code"))
        self.assertIn("return slugify(order.title)", p.text_content("#code"))


if __name__ == "__main__":
    unittest.main()
