"""IDE e2e — the Proof screen (0.0.27 P13, ID-VIS-PROOF; ruling U15: the launch demo).

On the "shop" fixture (mokata initialised, no run yet, one uncommitted change to `order_ref`): the
Proof screen opens on the working tree, draws the AC → test → code flow in SVG, flags the changed
definition no test reaches, lets the risk overlay be toggled, and a click on a code node opens it
in the Code view. A stranger's repo, no API key.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import unittest

import _harness as H


class TheProofScreenShowsTheTrace(H.IdeFlow):
    FIXTURE = "shop"

    def test_1_it_opens_on_the_working_tree_and_draws_the_flow(self):
        p = self.page
        self.go("proof")
        p.wait_for_selector("#view-proof:not([hidden])")
        p.wait_for_selector("#view-proof .pf-svg")
        self.text_until("#view-proof .pf-summary", "working tree", "the trace of the working tree")
        self.assertGreater(p.locator("#view-proof .pf-node").count(), 0, "no node was drawn")
        labels = p.evaluate("[...document.querySelectorAll('#view-proof .pf-node title')].map(t => t.textContent)")
        self.assertTrue(any("order_label" in s or "order_ref" in s for s in labels),
                        "the changed definition is not a node: %s" % labels)

    def test_2_the_risk_overlay_flags_code_no_test_reaches_and_toggles_off(self):
        p = self.page
        self.assertGreater(p.locator("#view-proof .pf-node.pf-risk-untested").count(), 0,
                           "the untested changed definition is not flagged")
        self.assertIn("changed without a test", p.text_content("#view-proof .pf-chips"))
        p.locator("#view-proof .pf-toggle", has_text="untested").locator("input").click()
        self.until("document.querySelectorAll('#view-proof .pf-node.pf-risk-untested').length === 0",
                   "the overlay to switch off")
        p.locator("#view-proof .pf-toggle", has_text="untested").locator("input").click()
        self.until("document.querySelectorAll('#view-proof .pf-node.pf-risk-untested').length > 0",
                   "the overlay to switch back on")

    def test_3_clicking_a_code_node_opens_it_in_the_code_view(self):
        p = self.page
        p.locator("#view-proof .pf-node.pf-code").first.click()
        p.wait_for_selector("#view-navigate:not([hidden])")
        self.text_until("#code", "def order_ref", "orders.py in the code view")

    def test_4_the_table_lists_gate_rows_or_says_there_are_none(self):
        p = self.page
        self.go("proof")
        p.wait_for_selector("#view-proof:not([hidden])")
        self.assertIn("every node on this page", p.text_content("#view-proof .pf-tables"))


if __name__ == "__main__":
    unittest.main()
