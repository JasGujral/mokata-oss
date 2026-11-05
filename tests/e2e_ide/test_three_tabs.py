"""0.0.27 UX-SIMPLIFY — the three-screen IDE in a real browser, the way a person walks it.

  * The top bar is ONE tablist: Review · Sessions · Code. The selected tab, its sub-tab and the
    visible view always agree; arrow keys / Home / End move AND select (WAI-ARIA tabs).
  * review → prove → land: a diff's symbol expands to the graph in one click and Back returns to
    the diff; marking the last file seen ENDS the review with the next steps (Proof, then the
    worktree's gated merge), and both are one click from there.
  * ONE place says what waits on you: a pending approval shows in the waiting bar under the top
    bar, and nowhere else in the shell.
  * Keyboard only: every primary action is in the Tab order and works with Enter; the single-key
    shortcuts can be turned off (WCAG 2.1.4) and then do nothing.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import sys

import _harness as H

SRC = H.SRC
if SRC not in sys.path:                     # the seeding below uses THIS checkout's writers
    sys.path.insert(0, SRC)

STATE = """() => ({
  tab: [...document.querySelectorAll('#screen-tabs [role=tab]')].filter((b) => b.getAttribute('aria-selected') === 'true').map((b) => b.dataset.tab),
  sub: [...document.querySelectorAll('#subtabs [role=tab]')].filter((b) => b.getAttribute('aria-selected') === 'true').map((b) => b.dataset.view),
  views: [...document.querySelectorAll('main.center > .view-pane')].filter((v) => !v.hidden).map((v) => v.id),
  bodyTab: document.body.dataset.tab, labelled: document.getElementById('shell').getAttribute('aria-labelledby') })"""


class TheThreeScreensAgreeAndReviewEnds(H.IdeFlow):
    FIXTURE = "shop"

    def state(self):
        return self.page.evaluate(STATE)

    def focused(self):
        return self.page.evaluate("document.activeElement === document.body ? 'BODY'"
                                  " : (document.activeElement && document.activeElement.id) || 'NONE'")

    def tab_to(self, selector, what, limit=300):
        """Press Tab until `selector` has focus — proof it is in the keyboard order."""
        p = self.page
        for _ in range(limit):
            if p.evaluate("(s) => !!document.activeElement && document.activeElement.matches(s)", selector):
                return
            self.press("Tab")
        self.fail("Tab never reached %s (%s)" % (selector, what))

    def test_1_one_tablist_three_screens_and_what_is_selected_is_what_is_shown(self):
        p = self.page
        self.until("MK.ui.restored === true", "the first view")
        tabs = p.evaluate("[...document.querySelectorAll('#screen-tabs [role=tab]')].map((b) => [b.id, b.textContent.trim().split(/\\s+/)[0]])")
        self.assertEqual([["tab-review", "Review"], ["tab-sessions", "Sessions"], ["tab-navigate", "Code"]], tabs)
        self.assertEqual("tablist", p.get_attribute("#screen-tabs", "role"))
        self.assertEqual(0, p.locator("nav.rail, footer, .rail-btn").count(), "no rail, no footer")
        for tab, first in (("review", "review"), ("sessions", "activity"), ("navigate", "navigate")):
            p.click("#tab-" + tab)
            st = self.state()
            self.assertEqual([tab], st["tab"], tab)
            self.assertEqual(tab, st["bodyTab"])
            self.assertEqual("tab-" + tab, st["labelled"], "the shell is labelled by its tab")
            self.assertEqual(["view-" + first], st["views"], tab)
        # sub-tabs: Review › Diff · Proof · Explain
        p.click("#tab-review")
        self.assertEqual(["review", "proof", "explain"],
                         p.evaluate("[...document.querySelectorAll('#subtabs [role=tab]')].map((b) => b.dataset.view)"))
        p.click("#subtab-proof")
        self.assertEqual((["review"], ["proof"], ["view-proof"]),
                         tuple(self.state()[k] for k in ("tab", "sub", "views")))
        # arrows / Home / End over the top tabs: automatic activation, focus follows
        p.focus("#tab-review")
        self.press("ArrowRight")
        self.assertEqual(["sessions"], self.state()["tab"])
        self.assertEqual("tab-sessions", p.evaluate("document.activeElement.id"))
        self.press("End")
        self.assertEqual(["navigate"], self.state()["tab"])
        self.press("Home")
        self.assertEqual(["review"], self.state()["tab"])
        self.assertEqual(["view-proof"], self.state()["views"], "a screen comes back on its last view")
        self.assertEqual(["0", "-1", "-1"], p.evaluate(
            "[...document.querySelectorAll('#screen-tabs [role=tab]')].map((b) => b.getAttribute('tabindex'))"),
            "roving tabindex: only the selected tab is in the Tab order")
        p.click("#subtab-review")

    def test_2_expand_graph_is_one_click_from_the_diff_and_back_returns_to_it(self):
        p = self.page
        p.click("#tab-review")
        p.wait_for_selector("#nav-list li", state="attached")
        p.locator("#nav-list li", has_text="orders.py").first.click()
        self.until("!document.getElementById('insp-ripple').disabled", "the diff's symbol in the inspector")
        self.assertEqual("Expand graph", p.text_content("#insp-ripple").strip())
        p.click("#insp-ripple")
        p.wait_for_selector("#view-graph:not([hidden])")
        st = self.state()
        self.assertEqual((["review"], ["view-graph"]), (st["tab"], st["views"]),
                         "the graph opens OVER the screen it was asked from")
        self.assertEqual("region", p.get_attribute("#view-graph", "role"))
        p.click("#g-back")
        self.assertEqual(["view-review"], self.state()["views"])
        self.assertEqual("insp-ripple", self.focused(),
                         "Back hands focus to Expand graph, the control that opened it")
        p.click("#insp-ripple")
        p.wait_for_selector("#view-graph:not([hidden])")
        p.evaluate("document.activeElement && document.activeElement.blur()")
        self.press("Escape")
        self.assertEqual(["view-review"], self.state()["views"], "Esc is Back from the graph")
        self.assertEqual("insp-ripple", self.focused(), "Esc and Back agree on where focus goes")
        # opened from somewhere that is gone when it closes (the jobs drawer, on Sessions): Back
        # hands focus to the screen's selected tab, never to <body>
        p.click("#tab-sessions")
        p.evaluate("document.activeElement && document.activeElement.blur()")
        p.evaluate("MK.openGraph('ripple', '')")
        p.wait_for_selector("#view-graph:not([hidden])")
        p.click("#g-back")
        self.assertEqual(["sessions"], self.state()["tab"])
        self.assertEqual("tab-sessions", self.focused(), "no opener left: the selected tab takes focus")
        p.click("#tab-review")

    def test_3_the_last_file_seen_ends_the_review_with_prove_and_land(self):
        p = self.page
        p.click("#tab-review")
        p.wait_for_selector("#nav-list li", state="attached")
        n = p.evaluate("MK.ui.files.length")
        self.assertGreater(n, 0)
        self.assertTrue(p.is_hidden("#review-done"), "the review has not ended yet")
        for _ in range(n + 2):
            if p.is_visible("#review-done"):
                break
            p.click("#mark-seen")
        p.wait_for_selector("#review-done:not([hidden])")
        done = p.text_content("#review-done")
        self.assertIn("All %d file" % n, done)
        self.assertIn("reviewed", done)
        self.assertEqual("status", p.get_attribute("#review-done", "role"))
        self.assertEqual("review-done", p.evaluate("document.activeElement.id"),
                         "focus moves to the end-of-review state (it is announced)")
        p.locator("#review-done button", has_text="Open Proof").click()
        self.assertEqual(["view-proof"], self.state()["views"])
        p.click("#subtab-review")
        p.wait_for_selector("#review-done:not([hidden])")
        p.locator("#review-done button", has_text="Worktree and merge").click()
        self.assertEqual((["sessions"], ["view-worktrees"]),
                         tuple(self.state()[k] for k in ("tab", "views")))
        row = p.locator(".wt-table .wt-row", has_text=H.WORKTREE).first
        row.wait_for()
        self.assertEqual(1, row.locator("button.wt-btn", has_text="Merge").count(),
                         "land: the worktree's gated merge is right there")
        p.click("#tab-review")
        p.locator("#review-done button", has_text="Review again").click()
        self.assertTrue(p.is_hidden("#review-done"))
        self.assertEqual("mark-seen", self.focused(),
                         "Review again hides its own button: focus goes to Mark seen · next")

    def test_4_one_waiting_bar_holds_what_waits_on_you(self):
        from mokata import approval
        p = self.page
        self.assertTrue(p.is_hidden("#waiting"), "nothing waits yet: no bar")
        pr = approval.propose(self.repo, tool="remember", args={"n": 1}, run_id="R-ux",
                              target="memory:ux", summary="remember ux", preview="a line")
        p.evaluate("MK.reloadApprovals()")
        p.wait_for_selector("#waiting:not([hidden])")
        self.assertEqual("1", p.text_content("#wait-n").strip())
        self.assertIn("mokata approve %s" % pr.proposal_id, p.text_content("#wait-cmd"))
        self.assertEqual("false", p.get_attribute("#wait-toggle", "aria-expanded"))
        p.click("#wait-toggle")
        self.assertEqual("true", p.get_attribute("#wait-toggle", "aria-expanded"))
        p.wait_for_selector("#approvals-bar .ap-card[data-pid='%s']" % pr.proposal_id)
        self.assertEqual(0, p.locator(".ap-card:not(#waiting .ap-card)").count(),
                         "an approval is shown in ONE place")
        for tab in ("sessions", "navigate", "review"):
            p.click("#tab-" + tab)
            self.assertTrue(p.is_visible("#waiting"), "the bar stays on every screen: %s" % tab)
        p.click("#wait-toggle")
        self.assertTrue(p.is_hidden("#wait-list"))
        approval.reject(self.repo, pr.proposal_id)
        p.evaluate("MK.reloadApprovals()")
        self.until("document.getElementById('waiting').hidden", "the bar to go once nothing waits")

    def test_5_every_primary_action_is_reachable_by_keyboard_alone(self):
        p = self.page
        p.click("#tab-review")
        p.focus("#tab-review")
        p.evaluate("document.activeElement.blur()")
        p.focus("body")
        for sel, what in (("#tab-review", "the screen tabs"), ("#session-chip", "Viewing"),
                          ("#qo-open", "go to / search / commands"), ("#activity-chip", "jobs"),
                          ("#health-chip", "health"), ("#subtab-review", "the sub-tabs"),
                          ("#mark-seen", "mark seen · next"), ("#insp-ripple", "Expand graph")):
            p.evaluate("document.activeElement && document.activeElement.blur()")
            self.tab_to(sel, what)
        # Enter on Expand graph opens it; the graph's Back is next in the order and returns
        self.until("!document.getElementById('insp-ripple').disabled", "a symbol to expand")
        p.focus("#insp-ripple")
        self.press("Enter")
        p.wait_for_selector("#view-graph:not([hidden])")
        p.evaluate("document.activeElement && document.activeElement.blur()")
        self.tab_to("#g-back", "Back from the graph")
        self.press("Enter")
        self.assertEqual(["view-review"], self.state()["views"])
        self.assertEqual("insp-ripple", self.focused(), "Enter on Back: focus returns to Expand graph")
        # Viewing ▾ opens from the keyboard, says it is expanded, and Esc hands focus back
        p.focus("#session-chip")
        self.press("Enter")
        self.assertEqual("true", p.get_attribute("#session-chip", "aria-expanded"))
        self.press("Escape")
        self.assertEqual("false", p.get_attribute("#session-chip", "aria-expanded"))
        self.assertEqual("session-chip", p.evaluate("document.activeElement.id"))

    def test_6_single_key_shortcuts_can_be_turned_off(self):
        """WCAG 2.1.4: a person on speech input (or prone to stray keys) turns the one-character
        shortcuts off from the `?` list; they then do nothing, and the choice is saved."""
        p = self.page
        p.click("#tab-review")
        p.evaluate("document.activeElement && document.activeElement.blur()")
        self.press("?")
        p.wait_for_selector("#keys-help:not([hidden])")
        self.assertTrue(p.is_checked("#single-keys"))
        p.uncheck("#single-keys")
        self.press("Escape")
        p.evaluate("document.activeElement && document.activeElement.blur()")
        self.press("g")
        self.press("s")
        self.assertEqual(["review"], self.state()["tab"], "g s still switched with single keys off")
        self.press("?")
        self.assertTrue(p.is_hidden("#keys-help"), "? still opened the list with single keys off")
        self.until("fetch('/api/workspace').then((r) => r.json()).then((w) => w.single_keys === false)",
                   "the choice to be saved")
        p.evaluate("MK.setSingleKeys(true)")
        self.press("g")
        self.press("s")
        self.assertEqual(["sessions"], self.state()["tab"])
        p.click("#tab-review")

    def test_7_deciding_in_the_bar_then_hiding_it_leaves_no_empty_bar(self):
        """Round 2, M1. A person opens the waiting bar, rejects the one waiting write in its card
        (the card stays to show the outcome), then clicks Hide: with nothing left waiting the bar
        goes, on every screen, and focus is not dropped to <body>.

        The decision itself is real (`approval.reject` on the fixture repo); only the one-time-code
        exchange is stubbed, because a real one prints to a human terminal (`--foreground`)."""
        from mokata import approval
        p = self.page
        pr = approval.propose(self.repo, tool="remember", args={"n": 7}, run_id="R-ux7",
                              target="memory:ux7", summary="remember ux7", preview="a line")
        pid = pr.proposal_id
        repo = self.repo

        def approvals(route):
            url = route.request.url
            if "/api/approvals/code" in url:
                route.fulfill(json={"note": "a code was printed to the terminal", "tries": 3, "expires_in": 60})
            elif "/api/approvals/decide" in url:
                approval.reject(repo, pid)
                route.fulfill(json={"message": "rejected", "ledger": [], "proposal": {
                    "proposal_id": pid, "tool": "remember", "summary": "remember ux7",
                    "status": "rejected", "decidable": False}})
            else:                                   # the real list, with Approve/Reject offered here
                body = route.fetch().json()
                body["approve_here"] = {"available": True, "reason": ""}
                route.fulfill(json=body)

        p.route("**/api/approvals**", approvals)
        try:
            p.click("#tab-review")
            p.evaluate("MK.reloadApprovals()")
            p.wait_for_selector("#waiting:not([hidden])")
            if p.get_attribute("#wait-toggle", "aria-expanded") != "true":
                p.click("#wait-toggle")
            card = "#approvals-bar .ap-card[data-pid='%s']" % pid
            p.locator(card).get_by_role("button", name="Reject", exact=True).click()
            p.fill(card + " input.ap-code", "ABCD-EFGH")
            p.press(card + " input.ap-code", "Enter")
            p.wait_for_selector("#approvals-bar .ap-card.st-rejected[data-pid='%s']" % pid)
            self.until("document.getElementById('wait-n').textContent === ''", "nothing to be counted")
            self.assertTrue(p.is_visible("#waiting"), "the open bar keeps the decided card on screen")
            p.click("#wait-toggle")
            self.assertTrue(p.is_hidden("#waiting"), "Hide with nothing waiting: no empty bar is left")
            self.assertEqual("tab-review", self.focused(), "the bar went with focus in it: the tab takes it")
            for tab in ("sessions", "navigate", "review"):
                p.click("#tab-" + tab)
                self.assertTrue(p.is_hidden("#waiting"), "no bar on %s" % tab)
        finally:
            p.unroute("**/api/approvals**", approvals)

    def test_8_health_never_hides_a_failure_behind_a_get_started_hint(self):
        """Round 2, M2. The code graph is not built (Get started prints `mokata graph build`) and a
        graph view just FAILED (the Health row carries the error, the same fix and Retry). Health
        shows the failure and Retry. A row that is ONLY the same hint as Get started (search by
        meaning's install line) is printed once, in Get started."""
        p = self.page
        wel = p.evaluate("fetch('/api/welcome').then((r) => r.json())")
        caps = {c["key"]: c for c in wel["capabilities"]}
        caps["graph"].update(state="not-ready", fix="mokata graph build", ready=False)
        health = p.evaluate("fetch('/api/health').then((r) => r.json())")
        rows = {r["key"]: r for r in health["rows"]}
        self.assertEqual(caps["meaning"]["fix"], rows["search"]["fix"],
                         "the fixture's own duplicate hint: search by meaning is not installed")
        rows["graph"].update(state="degraded", fix="mokata graph build",
                             detail="graph views failing: ripple failed - the graph backend exploded",
                             failing=["graph.view"], retry={"kind": "graph.view", "args": {}})
        health["problems"] = max(1, health.get("problems") or 0)

        def welcome(route):
            if route.request.method == "GET":
                route.fulfill(json=wel)
            else:
                route.continue_()

        def is_health(url):                     # /api/health and /api/health?full=1, nothing under it
            return url.split("?", 1)[0].endswith("/api/health")

        def checks(route):
            route.fulfill(json=health)

        p.route("**/api/welcome", welcome)
        p.route(is_health, checks)
        try:
            p.click("#health-chip")
            p.wait_for_selector("#hp-welcome li.wl-row[data-key=graph][data-state=not-ready]")
            p.wait_for_selector("#hp-rows li.hp-row button.hp-retry")
            hp = p.text_content("#hp-rows")
            self.assertIn("the graph backend exploded", hp, "the failure is shown")
            self.assertEqual(1, p.locator("#hp-rows li.hp-row", has_text="exploded")
                             .locator("code.hp-fix", has_text="mokata graph build").count(),
                             "the failure keeps its fix line")
            self.assertEqual(0, p.locator("#hp-rows li.hp-row", has_text=rows["search"]["label"]).count(),
                             "a row that is only Get started's hint is not printed twice")
            p.click("#hp-close")
            self.assertTrue(p.is_hidden("#hp"))
        finally:
            p.unroute("**/api/welcome", welcome)
            p.unroute(is_health, checks)
