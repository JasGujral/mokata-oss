"""IDE e2e — search, long work and session worktrees (0.0.26 O9 over P36 search-at-scale, P32 jobs,
P31 session worktrees), and the window on a repo mokata was never set up in.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import re
import unittest

import _harness as H


class SearchPagesPastTheFirstThousandHits(H.IdeFlow):
    FIXTURE = "shop"

    def header(self):
        return self.page.text_content("#sr-word-h")

    def test_1_a_word_search_finds_the_symbol(self):
        p = self.page
        self.until("!!document.getElementById('qo-open')", "the page")
        self.rail("search")
        p.wait_for_selector("#sq")
        p.fill("#sq", "slugify")
        self.text_until("#sr-word-h", "hit(s) in", "the word results header")
        self.assertIn("slugify", self.header(), "the header names another query")
        self.assertIn("shop/text.py", p.text_content("#sr-word-list"))

    def test_2_a_common_word_stops_at_a_page_and_offers_load_more(self):
        p = self.page
        p.fill("#sq", "needle")
        self.text_until("#sr-word-h", "needle · ≥", "a first page of `needle`")
        m = re.search(r"≥(\d+) hit", self.header())
        self.assertIsNotNone(m, self.header())
        self.assertLess(int(m.group(1)), H.NEEDLE_LINES, "the first page was not a page")
        more = p.locator("#sr-word-list .sr-more")
        more.wait_for()
        self.assertIn("Load more", more.text_content())

    def test_3_load_more_reaches_every_hit(self):
        p = self.page
        p.click("#sr-word-list .sr-more")
        self.text_until("#sr-word-h", "%d hit(s)" % H.NEEDLE_LINES,
                        "all %d hits after Load more" % H.NEEDLE_LINES)
        self.assertNotIn("≥", self.header(), "still says there is more")
        self.assertEqual(0, p.locator("#sr-word-list .sr-more").count(),
                         "a Load more button with nothing left to load")


class TheActivityDrawerShowsJobs(H.IdeFlow):
    FIXTURE = "shop"

    def test_1_the_chip_says_idle_and_dot_opens_the_drawer(self):
        p = self.page
        self.until("!!document.getElementById('activity-chip')", "the Activity chip")
        self.text_until("#activity-chip", "idle", "the chip to say idle")
        # `.` is a bare key: it fires only outside a text field.
        self.page.evaluate("document.activeElement && document.activeElement.blur()")
        self.press(".")
        p.wait_for_selector("#activity-drawer:not([hidden])")

    def test_2_a_started_index_build_is_listed_and_finishes(self):
        # The same door the drawer's Retry and the health panel use (POST /api/jobs).
        p = self.page
        started = p.evaluate("MK.jobs.start('index.ast', {}).then(() => true, () => false)")
        self.assertTrue(started, "POST /api/jobs refused the AST index build")
        self.text_until("#activity-drawer .act-list", "AST index", "the build listed")
        try:
            self.until("[...document.querySelectorAll('#activity-drawer .act-row.done')]"
                       ".some(r => r.textContent.includes('AST index'))",
                       "the build to finish (state done)", timeout_ms=60000)
        except AssertionError as exc:
            raise AssertionError("%s — the drawer: %s · /api/jobs: %s" % (
                exc, p.text_content("#activity-drawer .act-list"),
                p.evaluate("MK.api('/api/jobs').then(r => JSON.stringify(r))")[:1500]))
        row = p.locator("#activity-drawer .act-row.done", has_text="AST index").first
        self.assertIn("Open result", row.text_content())

    def test_3_escape_closes_it(self):
        self.press("Escape")
        self.page.wait_for_selector("#activity-drawer", state="hidden")


class WorktreesListAndMergeIsGated(H.IdeFlow):
    FIXTURE = "shop"

    def test_1_the_panel_lists_the_session_worktree_one_ahead(self):
        p = self.page
        self.until("!!document.getElementById('qo-open')", "the page")
        self.press("Alt+Shift+W")
        row = p.locator(".wt-table .wt-row", has_text=H.WORKTREE).first
        row.wait_for()
        cells = row.locator("td").all_text_contents()
        self.assertIn(H.WORKTREE, cells)           # the branch
        self.assertEqual("1", cells[5], "ahead: %s" % cells)

    def test_2_merge_proposes_and_waits_for_the_human(self):
        # P2: the page PROPOSES; the merge happens only after the human approves it.
        p = self.page
        row = p.locator(".wt-table .wt-row", has_text=H.WORKTREE).first
        row.locator("button.wt-btn", has_text="Merge").click()
        box = p.locator(".wt-pending").first
        box.wait_for()
        # P38: the shared approval card. This server has no terminal (stdin is not a TTY, as in
        # CI), so the card names the command and offers NO Approve button: the page cannot grant.
        card = box.locator(".ap-card").first
        card.wait_for()
        self.text_until(".wt-pending .ap-cmd", "mokata approve", "the approve command on the card")
        self.assertEqual(0, card.get_by_role("button", name="Approve", exact=True).count(),
                         "an Approve button without a terminal to read the code from")
        self.assertEqual(1, p.locator(".wt-table .wt-row", has_text=H.WORKTREE).count(),
                         "the worktree went away before anyone approved the merge")


class AStrangersRepoOpens(H.IdeFlow):
    """`mokata ide` in a git repo mokata was never set up in (P39 first run): the window opens on
    Welcome, still browses and searches, says what is missing, and sets mokata up only after the
    human confirmed the plan it was SHOWN (the consent token round-trip)."""

    FIXTURE = "bare"

    def test_1_it_opens_on_welcome_saying_set_up_is_needed(self):
        p = self.page
        self.until("document.getElementById('repo').textContent.trim().length > 0", "the repo name")
        self.assertIn("bare-shop", p.text_content("#repo"))
        self.until("document.body.dataset.mode === 'welcome'", "the Welcome screen first")
        row = p.locator(".wl-row", has_text="Sessions").first
        row.wait_for()
        self.assertIn("wl-needs-setup", row.get_attribute("class"), row.text_content())
        self.assertFalse(os.path.isdir(os.path.join(self.repo, ".mokata")),
                         "opening the window set mokata up by itself")

    def test_2_a_file_opens(self):
        self.open_file("shop/text.py")
        self.text_until("#code", "def slugify", "text.py's source")

    def test_3_search_answers(self):
        p = self.page
        self.rail("search")
        p.fill("#sq", "title_slug")
        self.text_until("#sr-word-h", "hit(s) in", "the word results header")
        self.assertIn("shop/orders.py", p.text_content("#sr-word-list"))

    def test_4_the_health_panel_says_what_is_missing(self):
        p = self.page
        p.click("#health-chip")
        p.wait_for_selector("#hp:not([hidden])")
        p.wait_for_selector("#hp-rows li")
        self.assertGreater(p.locator("#hp-rows li").count(), 0)
        self.press("Escape")

    def test_5_set_up_shows_the_plan_before_anything_runs(self):
        p = self.page
        self.rail("welcome")
        p.locator(".wl-row", has_text="Sessions").locator(".wl-act").first.click()
        p.wait_for_selector("#wl-consent .wl-confirm")
        self.assertTrue(p.text_content("#wl-consent .wl-plan").strip(), "no plan shown")
        self.assertGreater(p.locator("#wl-consent .wl-writes li").count(), 0,
                           "the consent does not say what it will write")
        self.assertFalse(os.path.isdir(os.path.join(self.repo, ".mokata")),
                         "showing the plan wrote to the repo")

    def test_6_confirming_runs_init_and_the_row_turns_ready(self):
        p = self.page
        p.click("#wl-consent .wl-confirm")
        self.until("[...document.querySelectorAll('.wl-row')].some(r =>"
                   " r.textContent.includes('Sessions') && r.classList.contains('wl-ready'))",
                   "the Sessions row ready after init", timeout_ms=90000)
        self.assertTrue(os.path.isdir(os.path.join(self.repo, ".mokata")), "init wrote nothing")


if __name__ == "__main__":
    unittest.main()
