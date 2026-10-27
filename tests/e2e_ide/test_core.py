"""IDE e2e — the first minute (0.0.26 O9): the page renders, ⌘P finds and opens a file, the graph
draws a symbol's callers, the health panel loads. Until 0.0.26 this was run by hand, so a page that
rendered blank or threw on load could ship behind a green unit suite (no unit test loads the page).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import unittest

import _harness as H


class TheFirstMinuteWorks(H.IdeFlow):
    FIXTURE = "shop"

    def test_1_the_page_renders_with_the_uncommitted_change(self):
        p = self.page
        self.until("document.getElementById('repo').textContent.trim().length > 0", "the repo name")
        self.assertIn("fixture-shop", p.text_content("#repo"))
        self.until("document.getElementById('diff-title').textContent !== 'Loading…'",
                   "the review view to load")
        p.wait_for_selector("#nav-list li", state="attached")
        self.assertIn("orders.py", p.text_content("#nav-list"),
                      "the uncommitted change is not listed in the Changes panel")

    def test_2_quickopen_finds_a_file_and_picking_it_opens_it(self):
        p = self.page
        p.click("#qo-open")
        p.wait_for_selector("#qo:not([hidden])")
        p.fill("#qo-input", "text.py")
        row = p.locator("#qo-results .qo-row", has_text="text.py").first
        row.wait_for()
        row.click()
        p.wait_for_selector("#view-navigate:not([hidden])")
        self.text_until("#code", "def slugify", "text.py's source in the code view")
        self.assertIn("def title_slug", p.text_content("#code"))

    def test_3_the_graph_draws_a_symbols_caller(self):
        p = self.page
        self.rail("graph")
        p.wait_for_selector("#view-graph:not([hidden])")
        p.fill("#g-symbol", "slugify")
        p.click("#g-go")
        # cytoscape registers its instance on the container; ask IT what it drew.
        self.until("(() => { const c = document.getElementById('cy');"
                   " return !!(c && c._cyreg && c._cyreg.cy && c._cyreg.cy.nodes().length > 1); })()",
                   "the graph to draw nodes")
        labels = p.evaluate(
            "document.getElementById('cy')._cyreg.cy.nodes().map(n => JSON.stringify(n.data()))")
        self.assertTrue(any("title_slug" in s for s in labels),
                        "slugify's caller title_slug is not a node: %s" % labels)

    def test_4_the_health_panel_loads(self):
        p = self.page
        p.click("#health-chip")
        p.wait_for_selector("#hp:not([hidden])")
        p.wait_for_selector("#hp-rows li")
        self.assertGreater(p.locator("#hp-rows li").count(), 0)
        self.assertNotIn("checks failed", p.text_content("#hp-sub"))
        self.press("Escape")


if __name__ == "__main__":
    unittest.main()
