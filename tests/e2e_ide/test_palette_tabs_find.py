"""IDE e2e — the editor keys (0.0.26 O9 over P2 tabs, P3 palette + find), driven by KEYBOARD the
way a user drives them: every key goes through the page's own registry, so a binding that stops
firing reds here even when the command itself still works from a click.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import unittest

import _harness as H


class ThePaletteRunsCommandsAndJumps(H.IdeFlow):
    FIXTURE = "shop"

    def test_1_mod_shift_p_lists_ide_commands_with_their_shortcuts(self):
        p = self.page
        self.until("!!document.getElementById('qo-open')", "the page")
        self.press("Control+Shift+P")
        p.wait_for_selector("#qo:not([hidden])")
        self.assertEqual(">", p.input_value("#qo-input"))
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

    def test_4_mod_g_goes_to_a_line(self):
        p = self.page
        self.press("Control+G")
        p.wait_for_selector("#qo:not([hidden])")
        self.assertEqual(":", p.input_value("#qo-input"))
        p.fill("#qo-input", ":2")
        p.locator("#qo-results .qo-row").first.wait_for()
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
        self.press("Control+Shift+P")
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
        # text.py: `def slugify`, `def title_slug`, `return slugify(` -> 3; ".join(title" is not one
        self.text_until("#find-bar .fb-count", "of 3", "the count to read n of 3")
        self.assertEqual("1 of 3", p.text_content("#find-bar .fb-count").strip())
        self.assertEqual(3, self.marks())

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
