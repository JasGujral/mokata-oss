"""IDE e2e — Copy with Context (its "ref" shape) and Find Usages (0.0.27 P4, ID-COPY-CONTEXT), driven
the way a user reaches them: a right-click on the Code view, the palette keys, the Intelligence panel
and the Review hunk's ⋯ menu. No new pane. UX-SIMPLIFY: Copy Ref is the ref shape of Copy with
Context (#40); the three per-hunk pills are one ⋯ menu (#36).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import unittest

import _harness as H


class CopyRefContextAndUsages(H.IdeFlow):
    FIXTURE = "shop"

    @classmethod
    def setUpClass(cls):
        super(CopyRefContextAndUsages, cls).setUpClass()
        if cls.no_browser:
            return
        H.grant_clipboard(cls.page, cls.server.url.split("/?")[0].rstrip("/"))

    def clip(self):
        return self.page.evaluate("navigator.clipboard.readText()")

    def row(self, n):
        return self.page.locator("#code div.cl[data-n='%d']" % n)

    def test_1_right_click_copies_the_ref_of_the_line_from_the_context_popover(self):
        """Right-click on `title_slug`'s body → Copy with Context: its "ref" shape is offered at
        once (no job) and puts `path:line path#qual` on the clipboard, saying so."""
        p = self.page
        self.open_file("shop/text.py")
        self.row(6).click()
        self.row(6).click(button="right")
        menu = p.locator(".p4-menu")
        menu.wait_for()
        # 0.0.28 G (coordinator ruling): the call hierarchy joins the menu after Find Usages
        self.assertEqual(["Copy with Context…", "Find Usages", "Find Callers", "Find Callees"],
                         menu.locator("button").all_text_contents())
        menu.locator("button", has_text="Copy with Context").click()
        self.assertEqual(0, p.locator(".p4-menu").count(), "the menu stayed open")
        ref = p.locator(".p4-pop .p4-shape[data-shape='ref']")
        ref.wait_for()
        ref.click()
        self.text_until(".p4-pop .p4-ref-msg", "Copied", "the ref shape's copy")
        self.assertEqual("shop/text.py:6 shop/text.py#title_slug", self.clip())
        self.press("Escape")
        p.wait_for_selector(".p4-pop", state="detached")

    def test_2_find_usages_lists_paged_labelled_rows_in_the_intelligence_panel(self):
        """Shift+F12 on `slugify` lists its usages in the Intelligence panel: the test file and
        the caller, with the backend that answered and what it covers."""
        p = self.page
        self.open_file("shop/text.py")
        self.row(2).click()
        self.press("Shift+F12")
        p.locator(".p4-usages .p4-row").first.wait_for()
        rows = p.locator(".p4-usages .p4-row").all_text_contents()
        self.assertTrue(any(r.startswith("shop/text.py:6:") for r in rows), rows)
        self.assertTrue(any(r.startswith("tests/test_text.py:") for r in rows), rows)
        self.assertFalse(any(r.startswith("shop/text.py:1:") for r in rows),
                         "the definition was listed as its own usage")
        label = p.text_content(".p4-usages .p4-label")
        self.assertTrue(label.strip(), "no backend label")
        first = p.locator(".p4-usages li[role=option]").first
        self.assertGreaterEqual(int(first.get_attribute("aria-setsize")), len(rows))
        self.assertEqual("1", first.get_attribute("aria-posinset"))
        # keyboard: the list is one tab stop, arrows move inside it
        p.locator(".p4-usages .p4-row").first.focus()
        self.press("ArrowDown")
        self.until("document.activeElement === document.querySelectorAll('.p4-usages .p4-row')[1]",
                   "ArrowDown to the second usage")

    def test_3_copy_with_context_shows_sizes_then_copies_the_chosen_shape(self):
        """Copy with Context opens a popover where the menu was. It shows each shape's size before
        anything is copied, and copies only the shape picked."""
        p = self.page
        self.open_file("shop/text.py")
        self.row(5).click(button="right")
        p.locator(".p4-menu button", has_text="Copy with Context").click()
        pop = p.locator(".p4-pop")
        pop.wait_for()
        summary = pop.locator(".p4-shape[data-shape='summary']")
        summary.wait_for()
        facts = p.text_content(".p4-pop .p4-shapes")
        self.assertIn("tokens", facts)
        before = self.clip()
        self.assertEqual("shop/text.py:6 shop/text.py#title_slug", before,
                         "something was copied before a shape was picked")
        summary.click()
        self.text_until(".p4-pop", "Copied the summary shape", "the copy to finish")
        text = self.clip()
        self.assertIn("# mokata context: shop/text.py:5", text)
        self.assertIn("## Selection", text)
        self.assertIn("def title_slug(order):", text)
        self.assertIn("### title_slug", text)
        self.assertIn("## Acceptance criteria", text)
        self.press("Escape")
        p.wait_for_selector(".p4-pop", state="detached")

    def test_4_the_palette_has_the_two_commands_with_their_keys_and_no_copy_ref(self):
        p = self.page
        self.press("Control+K")
        p.wait_for_selector("#qo:not([hidden])")
        for title in ("Copy with Context", "Find Usages"):
            p.fill("#qo-input", ">" + title)
            row = p.locator("#qo-results .qo-row", has_text=title).first
            row.wait_for()
            self.assertTrue(row.locator("kbd").count() > 0, "%s shows no shortcut" % title)
        self.press("Escape")
        # UX-SIMPLIFY #40: Copy Ref is deleted as a command and a key — it is a shape, not a twin
        self.assertFalse(p.evaluate("mokataIDE.commands().some((c) => c.id === 'usages.copyRef'"
                                    " || /Copy Ref/.test(c.title))"), "Copy Ref is still a command")
        self.assertIsNone(p.evaluate("mokataIDE.keys.owner('Alt+Shift+C')"))

    def test_5_a_review_hunk_menu_is_one_keyboard_button_and_copies_its_new_side(self):
        """Each Review hunk head has ONE ⋯ button (no pills): a real menu button, at least 24×24,
        opened with Enter, moved with the arrows, closed with Esc back onto the button. Its Copy with
        Context offers the ref of the hunk's NEW side lines."""
        p = self.page
        p.evaluate("MK.setMode('review')")
        btn = p.locator("#diff section.hunk .hunk-head .hk-btn").first
        btn.wait_for()
        head = p.locator("#diff section.hunk .hunk-head").first
        self.assertEqual(0, head.locator(".pill-link").count(), "a per-hunk pill is still drawn")
        self.assertEqual(1, head.locator("button").count(), "the hunk head carries more than one button")
        self.assertEqual("menu", btn.get_attribute("aria-haspopup"))
        self.assertEqual("hunk actions", btn.get_attribute("aria-label"))
        box = btn.bounding_box()
        self.assertGreaterEqual(min(box["width"], box["height"]), 24, box)
        btn.focus()
        self.press("Enter")
        p.wait_for_selector("#hk-menu[role=menu]")
        self.assertEqual("true", btn.get_attribute("aria-expanded"))
        items = p.locator("#hk-menu [role=menuitem]").all_text_contents()
        self.assertEqual(["Blame", "Copy with Context…", "Open in Code"], items)
        self.until("document.activeElement && document.activeElement.dataset.item === 'blame'",
                   "focus on the first item")
        self.press("ArrowDown")
        self.until("document.activeElement.dataset.item === 'context'", "ArrowDown to the next item")
        self.press("Escape")
        p.wait_for_selector("#hk-menu", state="detached")
        self.until("document.activeElement && document.activeElement.classList.contains('hk-btn')",
                   "Esc hands focus back to the ⋯ button")
        self.assertEqual("false", btn.get_attribute("aria-expanded"))
        btn.click()
        p.locator("#hk-menu [data-item='context']").click()
        ref = p.locator(".p4-pop .p4-shape[data-shape='ref']")
        ref.wait_for()
        self.assertFalse(ref.is_disabled())
        ref.click()
        self.text_until(".p4-pop .p4-ref-msg", "Copied shop/orders.py:", "the hunk ref copy")
        self.assertTrue(self.clip().startswith("shop/orders.py:"), self.clip())
        self.press("Escape")
        p.wait_for_selector(".p4-pop", state="detached")

    def test_6_a_reload_releases_the_pages_spill_files(self):
        """V4: results live until the page leaves. A reload (pagehide) releases this page's
        spills; they never pile up per reload."""
        import os
        import time
        p = self.page
        spill = os.path.join(self.repo, ".mokata", "temp_local", "ide", "spill")

        def count():
            return len([n for n in os.listdir(spill) if n.endswith(".spill")]) \
                if os.path.isdir(spill) else 0
        self.go("navigate")
        self.open_file("shop/text.py")
        self.row(2).click()
        self.press("Shift+F12")
        p.locator(".p4-usages .p4-row").first.wait_for()
        self.assertGreater(count(), 0, "Find Usages left no spill to release")
        p.reload()
        self.until("!!document.getElementById('qo-open')", "the reloaded page")
        deadline = time.time() + 10
        while count() and time.time() < deadline:
            time.sleep(0.1)
        self.assertEqual(0, count(), "the reload left spill files behind")


if __name__ == "__main__":
    unittest.main()
