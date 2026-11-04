"""IDE e2e — the approval card after Cancel (0.0.27 PINS-UI, DEFLAKE L1).

While a one-time code is being asked for, or typed, the waiting bar keeps the card as it is: a live
reload must not throw away the code input under the person's fingers. So a decision made in a
terminal meanwhile is NOT drawn on that card. Cancel is where the card catches up: it shows the
proposal as it is now (approved, or gone because it was rejected), keeps its "Cancelled" note while
nothing changed, and keyboard focus stays on a control — never <body>.

The decisions are real (`mokata.approval` on the fixture repo). Only the one-time-code exchange is
stubbed, because a real one prints to a human terminal (`--foreground`). The live event stream is
held open and silent for the whole flow, so the ONLY thing that can update the card is the card's
own reload — a late push can never make a missing reload look present.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import sys

import _harness as H

if H.SRC not in sys.path:                   # the decisions below use THIS checkout's writers
    sys.path.insert(0, H.SRC)


# The routes every approval-card flow needs: the live stream held silent, the real proposal list with Approve/Reject offered here, and the one-time-code exchange stubbed (a code request can be parked; a decide answer can be set).
# Module functions, not a mixin: every class here is exactly an `H.IdeFlow` (test_o9).


def _wired_setup(t, before=None):
    """Once per class: `before` adds a flow's own routes, then the shared ones and the reload."""
    if not getattr(type(t), "_routed", False):
        type(t)._routed = True
        if before:
            before()
        _wire(t)


def _wire(t):
    cls = type(t)
    cls.held_streams, cls.held_codes = [], []
    cls.park_code = False
    cls.decide_answer = None
    p = t.page

    def approvals(route):
        url = route.request.url
        if "/api/approvals/decide" in url and cls.decide_answer is not None:
            route.fulfill(json=cls.decide_answer)
        elif "/api/approvals/code" in url:
            if cls.park_code:
                cls.held_codes.append(route)
            else:
                route.fulfill(json={"note": "a code was printed to the terminal", "tries": 3, "expires_in": 60})
        else:                               # the REAL list, with Approve/Reject offered here
            body = route.fetch().json()
            body["approve_here"] = {"available": True, "reason": ""}
            route.fulfill(json=body)

    p.route("**/api/events*", lambda route: cls.held_streams.append(route))   # never answered: no live push reaches the page
    p.route("**/api/approvals**", approvals)
    p.reload()
    t.until("!!(window.MK && MK.ui && MK.ui.state && MK.reloadApprovals)", "the page, reloaded with the stream held")


def propose(t, n):
    from mokata import approval
    pr = approval.propose(t.repo, tool="remember", args={"n": n}, run_id="R-pins-%d" % n,
                          target="memory:pins%d" % n, summary="remember pins %d" % n, preview="a line")
    p = t.page
    p.evaluate("MK.reloadApprovals()")
    p.wait_for_selector("#waiting:not([hidden])")
    if p.get_attribute("#wait-toggle", "aria-expanded") != "true":
        p.click("#wait-toggle")
    card = p.locator("#approvals-bar .ap-card[data-pid='%s']" % pr.proposal_id)
    card.wait_for()
    return pr.proposal_id, card


def ledger(t):
    from mokata.govern.ledger import AuditLedger
    import os
    return AuditLedger.from_mokata_dir(os.path.join(t.repo, ".mokata"))


def focus_in(t, pid):
    return t.page.evaluate(
        "(pid) => { const a = document.activeElement; const c = a && a.closest && a.closest('.ap-card');"
        " return a && a !== document.body && !!c && c.dataset.pid === pid; }", pid)


def release_codes(t, answer):
    """Answer every parked code request (answered, never aborted: an abort logs a console error)."""
    held, type(t).held_codes = type(t).held_codes, []
    for r in held:
        r.fulfill(json=answer)


def mark_card(t, pid):
    """Tag the card node now on screen, so a step can wait for its REPLACEMENT (a reload)."""
    t.page.evaluate("(pid) => { document.querySelector(`#approvals-bar .ap-card[data-pid='${pid}']`).__pinsOld = 1; }", pid)


def replaced(t, pid):
    return ("(() => { const c = document.querySelector(\"#approvals-bar .ap-card[data-pid='%s']\");"
            " return !!c && !c.__pinsOld; })()" % pid)


def note_colour(t, pid):
    """[the note's text colour, the theme's error colour (--del)] as the browser paints them."""
    return t.page.evaluate(
        "(pid) => { const c = document.querySelector(`#approvals-bar .ap-card[data-pid='${pid}']`);"
        " const n = c && c.querySelector('.ap-note'); const i = document.createElement('i');"
        " i.style.color = 'var(--del)'; c.append(i); const bad = getComputedStyle(i).color; i.remove();"
        " return [n ? getComputedStyle(n).color : null, bad]; }", pid)


class TheCardShowsTheCurrentStateAfterCancel(H.IdeFlow):
    FIXTURE = "shop"

    def setUp(self):
        super().setUp()
        _wired_setup(self)

    def test_1_approved_in_a_terminal_while_the_code_was_typed_then_cancel(self):
        from mokata import approval
        p = self.page
        pid, card = propose(self, 1)
        card.get_by_role("button", name="Approve", exact=True).click()
        card.locator("input.ap-code").wait_for()
        card.locator("input.ap-code").fill("ABCD")
        approval.approve(self.repo, pid, ledger=ledger(self))     # the person decides in a terminal
        p.evaluate("MK.reloadApprovals()")                           # resolves after the repaint
        self.assertEqual("ABCD", card.locator("input.ap-code").input_value(),
                         "the card being typed into is kept as it is (nothing typed is lost)")
        self.assertIn("waiting on you", card.text_content(), "…so it does not yet show the terminal's decision")
        card.get_by_role("button", name="Cancel", exact=True).click()
        self.until("(() => { const c = document.querySelector(\"#approvals-bar .ap-card[data-pid='%s']\");"
                   " return !!c && c.textContent.includes('Approved — the agent may now commit it once'); })()" % pid,
                   "the card to show the terminal's approval after Cancel")
        self.assertNotIn("waiting on you", card.text_content())
        self.assertNotIn("nothing was decided", card.text_content(),
                         "a note about this window's Cancel is not carried onto a decision made elsewhere")
        self.assertEqual(0, card.get_by_role("button", name="Approve", exact=True).count(),
                         "an approved write is not offered Approve again")
        self.assertEqual(1, card.get_by_role("button", name="Reject", exact=True).count(),
                         "it can still be rejected until it is used")
        self.assertTrue(focus_in(self, pid), "keyboard focus stays on the card, never <body>")

    def test_2_rejected_in_a_terminal_while_the_code_is_asked_for_drops_the_card_not_the_focus(self):
        """The rejected write leaves the bar on the next reload, even mid-ask (nothing is left to
        decide). Focus was on its Cancel: it moves to the bar's toggle, never <body>."""
        from mokata import approval
        p = self.page
        type(self).park_code = True
        try:
            pid, card = propose(self, 2)
            with p.expect_request("**/api/approvals/code"):
                card.get_by_role("button", name="Reject", exact=True).click()
            card.get_by_role("button", name="Cancel", exact=True).wait_for()
            self.assertTrue(focus_in(self, pid), "focus is on Cancel while the code is asked for")
            approval.reject(self.repo, pid)                            # decided in a terminal meanwhile
            p.evaluate("MK.reloadApprovals()")                         # resolves after the repaint
            self.assertEqual(0, p.locator("#approvals-bar .ap-card[data-pid='%s']" % pid).count(),
                             "a write rejected elsewhere is not offered any more")
            self.assertNotEqual("BODY", p.evaluate("document.activeElement.tagName"),
                                "its card went with focus in it: focus is not dropped to <body>")
        finally:
            type(self).park_code = False
            release_codes(self, {"error": "Gone", "note": "decided elsewhere"})

    def test_3_cancel_with_nothing_decided_keeps_its_note_through_a_reload(self):
        p = self.page
        pid, card = propose(self, 3)
        card.get_by_role("button", name="Approve", exact=True).click()
        card.locator("input.ap-code").wait_for()
        card.get_by_role("button", name="Cancel", exact=True).click()
        card.get_by_role("button", name="Approve", exact=True).wait_for()
        p.evaluate("MK.reloadApprovals()")                           # any later live reload
        self.assertIn("Cancelled - nothing was decided", card.text_content(),
                      "the note survives the repaint while the proposal is unchanged")
        self.assertTrue(focus_in(self, pid), "focus is on the card's control after the repaint")

    def test_4_a_void_code_after_a_terminal_decision_shows_the_decision(self):
        """The code typed here is refused as void/expired (the proposal was approved in a terminal
        meanwhile). The card leaves the code row AND catches up on its own: it shows the approval,
        not "waiting on you" with Approve still offered. Focus stays on the card."""
        from mokata import approval
        p = self.page
        pid, card = propose(self, 4)
        card.get_by_role("button", name="Approve", exact=True).click()
        card.locator("input.ap-code").wait_for()
        approval.approve(self.repo, pid, ledger=ledger(self))     # the person decides in a terminal
        type(self).decide_answer = {"error": "BadCode", "note": "that code has expired — ask for a new one"}
        try:
            card.locator("input.ap-code").fill("ABCD-EFGH")
            card.get_by_role("button", name="Confirm approve", exact=True).click()
            self.until("(() => { const c = document.querySelector(\"#approvals-bar .ap-card[data-pid='%s']\");"
                       " return !!c && c.textContent.includes('Approved — the agent may now commit it once'); })()" % pid,
                       "the card to show the terminal's approval after the void code")
        finally:
            type(self).decide_answer = None
        self.assertEqual(0, card.locator("input.ap-code").count(), "the void code row is gone")
        self.assertNotIn("waiting on you", card.text_content())
        self.assertEqual(0, card.get_by_role("button", name="Approve", exact=True).count(),
                         "an approved write is not offered Approve again")
        self.assertTrue(focus_in(self, pid), "keyboard focus stays on the card, never <body>")

    def test_5_a_failed_ask_after_a_terminal_decision_shows_the_decision(self):
        """Asking for a code fails (here: rate-limited) after the proposal was approved in a
        terminal. The card goes back to idle AND catches up on its own; focus stays on it."""
        from mokata import approval
        p = self.page
        type(self).park_code = True
        try:
            pid, card = propose(self, 5)
            with p.expect_request("**/api/approvals/code"):
                card.get_by_role("button", name="Approve", exact=True).click()
            card.get_by_role("button", name="Cancel", exact=True).wait_for()
            approval.approve(self.repo, pid, ledger=ledger(self))
        finally:
            type(self).park_code = False
            release_codes(self, {"error": "RateLimited", "note": "too many codes asked for — try again in 30 s"})
        self.until("(() => { const c = document.querySelector(\"#approvals-bar .ap-card[data-pid='%s']\");"
                   " return !!c && c.textContent.includes('Approved — the agent may now commit it once'); })()" % pid,
                   "the card to show the terminal's approval after the failed ask")
        self.assertEqual(0, card.get_by_role("button", name="Cancel", exact=True).count(), "no ask is left in flight")
        self.assertEqual(0, card.get_by_role("button", name="Approve", exact=True).count())
        self.assertTrue(focus_in(self, pid), "keyboard focus stays on the card, never <body>")

    def test_6_an_expired_code_with_nothing_decided_stays_an_error_through_the_reload(self):
        """Nothing was decided elsewhere: the refusal is shown as an ERROR (the theme's --del), and
        still is on the card that replaces it at the card's own reload. Approve is offered again."""
        p = self.page
        pid, card = propose(self, 6)
        card.get_by_role("button", name="Approve", exact=True).click()
        card.locator("input.ap-code").wait_for()
        mark_card(self, pid)
        type(self).decide_answer = {"error": "BadCode", "note": "that code has expired — ask for a new one"}
        try:
            card.locator("input.ap-code").fill("ABCD-EFGH")
            card.get_by_role("button", name="Confirm approve", exact=True).click()
            self.until(replaced(self, pid), "the card's own reload to replace it")
        finally:
            type(self).decide_answer = None
        self.assertIn("that code has expired", card.text_content(), "the refusal is still said")
        colour, bad = note_colour(self, pid)
        self.assertEqual(bad, colour, "…and still shown as an error, not as a plain note")
        self.assertEqual(0, card.locator("input.ap-code").count(), "the expired code row is gone")
        self.assertEqual(1, card.get_by_role("button", name="Approve", exact=True).count(), "Approve is offered again")
        self.assertTrue(focus_in(self, pid), "keyboard focus stays on the card, never <body>")

    def test_7_its_card_goes_while_another_waits_focus_goes_to_the_bar_toggle(self):
        """Two writes wait. Focus is in the first card (asking for a code) when it is rejected in a
        terminal: that card goes, the bar stays for the other, and focus lands on the bar's
        Show/Hide toggle — not <body>, and not off the bar to the screen tabs."""
        from mokata import approval
        p = self.page
        type(self).park_code = True
        try:
            pid, card = propose(self, 71)
            other, _ = propose(self, 72)
            with p.expect_request("**/api/approvals/code"):
                card.get_by_role("button", name="Reject", exact=True).click()
            card.get_by_role("button", name="Cancel", exact=True).wait_for()
            self.assertTrue(focus_in(self, pid), "focus is on Cancel while the code is asked for")
            approval.reject(self.repo, pid)
            p.evaluate("MK.reloadApprovals()")                         # resolves after the repaint
            self.assertEqual(0, p.locator("#approvals-bar .ap-card[data-pid='%s']" % pid).count())
            self.assertEqual(1, p.locator("#approvals-bar .ap-card[data-pid='%s']" % other).count(),
                             "the other write still waits in the bar")
            self.assertTrue(p.is_visible("#wait-toggle"))
            self.assertEqual("wait-toggle", p.evaluate("document.activeElement.id"),
                             "focus moves to the bar's toggle")
        finally:
            type(self).park_code = False
            release_codes(self, {"error": "Gone", "note": "decided elsewhere"})


class TheLastCardGoingHandsFocusToTheScreenTab(H.IdeFlow):
    """Its own repo: ONE write waits and nothing else, so when it is rejected in a terminal the
    whole bar goes. Focus that was in its card lands on the selected screen tab.

    The sessions' needs-you queue also lists a waiting approval, and with the live stream held it
    is never refreshed after the terminal's decision; so here it is answered empty, or its stale
    copy of THIS proposal would keep the bar up and the step would grade the wrong branch."""
    FIXTURE = "shop"

    def setUp(self):
        super().setUp()
        _wired_setup(self, self._route_sessions)   # _wire then reloads with every route in place

    def _route_sessions(self):
        from urllib.parse import urlparse

        def sessions(route):
            body = route.fetch().json()
            body["needs_you"] = []
            route.fulfill(json=body)
        self.page.route(lambda url: urlparse(url).path == "/api/sessions", sessions)

    def test_1_the_only_card_goes_focus_goes_to_the_selected_screen_tab(self):
        from mokata import approval
        p = self.page
        type(self).park_code = True
        try:
            pid, card = propose(self, 1)
            with p.expect_request("**/api/approvals/code"):
                card.get_by_role("button", name="Reject", exact=True).click()
            card.get_by_role("button", name="Cancel", exact=True).wait_for()
            self.assertTrue(focus_in(self, pid))
            approval.reject(self.repo, pid)
            p.evaluate("MK.reloadApprovals()")
            self.assertTrue(p.is_hidden("#waiting"), "nothing waits: the bar is gone")
            self.assertTrue(p.evaluate(
                "document.activeElement === document.querySelector('#screen-tabs [aria-selected=\"true\"]')"),
                "focus is on the selected screen tab (%s)" % p.evaluate(
                    "document.activeElement.id || document.activeElement.tagName"))
        finally:
            type(self).park_code = False
            release_codes(self, {"error": "Gone", "note": "decided elsewhere"})
