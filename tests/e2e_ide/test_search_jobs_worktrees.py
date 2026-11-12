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
        self.go("search")
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
        """P21 (gated merge): Merge… opens the merge card BELOW the table. With no mokata run bound
        it is BLOCKED by name (`no-run`) and proposes nothing. Once a human files an override for
        that head (out-of-band, as `mokata worktree merge-override` does at a TTY), the page
        PROPOSES and waits: the approval card names `mokata approve <id>`, offers no Approve button
        (no terminal here), and nothing has merged — the page never merges in-browser."""
        from mokata import merge_gate as MG
        p = self.page
        import subprocess

        def head():
            return subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, check=True,
                                  capture_output=True, text=True,
                                  stdin=subprocess.DEVNULL).stdout.strip()
        head_before = head()
        row = p.locator(".wt-table .wt-row", has_text=H.WORKTREE).first
        row.locator("button.wt-btn", has_text="Merge").click()
        card = p.locator(".wt-merge-block .wt-merge-card").first
        card.wait_for()
        self.assertEqual(0, p.locator(".wt-table .wt-merge-card").count(),
                         "the merge card is inside the table (it overflows a nowrap cell)")
        self.assertTrue(p.evaluate("(() => { const b = document.querySelector('.wt-merge-block');"
                                   " return b.scrollWidth <= b.clientWidth + 1; })()"),
                        "the merge card overflows its block")
        self.text_until(".wt-merge-card .wt-evidence", "no-run", "the named no-run blocker")
        self.assertEqual(0, card.locator(".ap-card").count(), "a blocked merge was proposed")
        self.assertEqual(0, card.get_by_role("button", name="Merge now").count())
        # the fixture's main checkout carries an uncommitted edit: also blocked, by name
        self.text_until(".wt-merge-card .wt-evidence", "dirty-main", "the named dirty-main blocker")
        wt = os.path.join(self.repo, ".claude", "worktrees", H.WORKTREE)
        MG.file_override(self.repo, wt, "the e2e's human: land it without run evidence",
                         actor="e2e-human")
        subprocess.run(["git", "stash", "-q"], cwd=self.repo,          # the human sets it aside
                       check=True, capture_output=True, stdin=subprocess.DEVNULL)
        head_before = head()
        row.locator("button.wt-btn", has_text="Merge").click()
        ap = p.locator(".wt-merge-card .ap-card").first
        ap.wait_for()
        self.text_until(".wt-merge-card .ap-cmd", "mokata approve", "the approve command on the card")
        # 0.0.28 F: the override waives "no run bound" only — the spec legs are said NOT CHECKED,
        # in their own warning line, and no line with a check mark names a spec leg
        self.text_until(".wt-merge-card .wt-evidence", "NOT CHECKED: spec-awareness, spec-drift",
                        "the spec legs said as NOT CHECKED")
        self.assertEqual([], p.evaluate(
            "[...document.querySelectorAll('.wt-merge-card .wt-evidence li.wt-ok')]"
            ".map(li => li.textContent).filter(t => t.includes('spec'))"),
            "a spec leg was shown with a check mark")
        self.until("document.querySelector('.wt-merge-card .wt-message').value"
                   ".includes('merged under override')",
                   "a message that says it is an override, not 'tests green'")
        self.assertEqual(0, ap.get_by_role("button", name="Approve", exact=True).count(),
                         "an Approve button without a terminal to read the code from")
        self.assertEqual(head_before, head(),
                         "proposing merged")
        self.assertEqual(1, p.locator(".wt-table .wt-row", has_text=H.WORKTREE).count(),
                         "the worktree went away before anyone approved the merge")


class AStrangersRepoOpens(H.IdeFlow):
    """`mokata ide` in a git repo mokata was never set up in (P39 first run): the window opens on
    Review (UX-SIMPLIFY §6 P1: never a screen that takes the window), whose empty state and the
    top bar lead to Get started; it still browses and searches, says what is missing, and sets
    mokata up only after the human confirmed the plan it was SHOWN (the consent token round-trip)."""

    FIXTURE = "bare"

    def test_1_it_opens_on_review_and_get_started_says_set_up_is_needed(self):
        p = self.page
        self.until("document.getElementById('repo').textContent.trim().length > 0", "the repo name")
        self.assertIn("bare-shop", p.text_content("#repo"))
        self.until("MK.ui.restored === true", "the first view")
        self.assertEqual("review", p.evaluate("document.body.dataset.mode"))
        # §6 P2: a first run's top bar reads as a start — no "done" flash, no "health N" fault count
        self.until("document.getElementById('health-label').textContent === 'Get started'",
                   "the health chip to say Get started")
        top = p.text_content("header.top")
        self.assertNotIn("done", top)
        self.assertNotRegex(top, r"health\s*\d")
        p.wait_for_selector("#get-started-btn")
        p.click("#get-started-btn")
        row = p.locator("#hp-welcome .wl-row", has_text="Sessions").first
        row.wait_for()
        self.assertIn("wl-needs-setup", row.get_attribute("class"), row.text_content())
        self.press("Escape")
        self.assertTrue(p.evaluate("document.getElementById('hp').hidden"))
        self.assertFalse(os.path.isdir(os.path.join(self.repo, ".mokata")),
                         "opening the window set mokata up by itself")

    def test_2_a_file_opens(self):
        self.open_file("shop/text.py")
        self.text_until("#code", "def slugify", "text.py's source")

    def test_3_search_answers(self):
        p = self.page
        self.go("search")
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
        self.go("welcome")
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
