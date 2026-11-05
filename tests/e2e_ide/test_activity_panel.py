"""0.0.27 P12 — the session Timeline in a real browser (UX-SIMPLIFY: Sessions › Timeline): the
picked session's header card, its virtualised timeline, and a gate verdict that arrives PUSHED (no
request from the page) while the view is open."""

import json
import os
import sys
import time

import _harness as H

SRC = H.SRC
if SRC not in sys.path:                     # the seeding below uses THIS checkout's writers
    sys.path.insert(0, SRC)


def _seed(repo):
    """What the hooks would have recorded for one session: a start, two writes, a phase and some
    gate verdicts — written straight to the stores, as if no IDE had been open (it was not)."""
    from mokata import session_index as SI
    from mokata.events import emit
    from mokata.events.schema import GateDecision, PhaseTransition
    from mokata.ide import snapshots as S
    sid, run = "e2e-session-0001", "R-e2e"
    SI.on_start(repo, sid, "startup", repo, t=time.time() - 30, proc=None)
    path = os.path.join(repo, "shop", "text.py")
    S.capture_before_first_write(repo, path, sid, run)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n\ndef shout(s):\n    return s.upper()\n")
    S.capture(repo, path, S.AFTER, sid, run)
    emit(repo, PhaseTransition(phase="develop"), session_id=sid, run_id=run)
    for i in range(60):
        emit(repo, GateDecision(gate="write_gate", decision="allowed", subject="shop/text.py"),
             session_id=sid, run_id=run)
    return sid, run


class TheActivityPanelShowsASessionLiveAndItsHistory(H.IdeFlow):
    FIXTURE = "shop"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if cls.no_browser:
            return
        cls.sid, cls.run_id = _seed(cls.repo)

    def test_1_g_s_then_a_pick_shows_the_sessions_card_over_its_timeline(self):
        self.page.evaluate("document.activeElement && document.activeElement.blur()")
        self.press("g")
        self.press("s")
        self.page.wait_for_selector("#view-activity:not([hidden])")
        self.page.locator("#s1-rows button.s1-row", has_text=self.sid[:8]).first.click()
        self.text_until("#s1-card", self.sid[:8], "the session's card in the Sessions header")
        card = self.page.text_content("#s1-card")
        self.assertIn("gates 60 fired", card)
        self.assertIn("1 file(s)", card)

    def test_1b_a_first_run_never_takes_the_screen_from_a_keyboard_only_person(self):
        """UX-SIMPLIFY §6 P1: Get started is a section of Health; nothing pulls a person who
        moved with the `g s` chord (no pointer) off the screen they picked."""
        self.until("MK.ui.restored === true", "the saved view to be restored")
        time.sleep(1.5)                       # /api/welcome has answered by now
        self.assertEqual("activity", self.page.evaluate("document.body.dataset.mode"))
        self.assertTrue(self.page.evaluate("document.getElementById('hp').hidden"))

    def test_2_the_timeline_is_paged_from_the_server_and_virtualised(self):
        self.text_until("#view-activity .ap-tlhead", "event(s)", "the timeline note")
        note = self.page.text_content("#view-activity .ap-tlhead")
        self.assertIn("61 event(s)", note)
        self.assertIn("2 write(s)", note)
        n_items = self.page.evaluate("MK.activityPanel.items().length")
        self.assertEqual(63, n_items, "every item of the session is loaded (61 events + 2 writes)")
        rows = self.page.evaluate("document.querySelectorAll('#view-activity .ap-row').length")
        self.assertLess(rows, n_items, "only the rows in view are in the DOM")
        self.assertGreater(rows, 0)
        kinds = self.page.evaluate("MK.activityPanel.items().map(i => i.type)")
        self.assertEqual({"gate", "phase", "edit", "before"}, set(kinds))
        ts = self.page.evaluate("MK.activityPanel.items().map(i => i.t)")
        self.assertEqual(sorted(ts, reverse=True), ts, "newest first")

    def test_3_a_gate_verdict_arrives_pushed_while_the_panel_is_open(self):
        # nothing in flight (the first read answered, no coalesced re-read pending) before counting
        self.until("MK.activityPanel.settled()", "the panel to settle")
        before = len([r for r in self.requests if "/api/activity" in r[1]])
        from mokata.events import emit
        from mokata.events.schema import GateDecision
        emit(self.repo, GateDecision(gate="write_gate", decision="blocked", subject="shop/text.py"),
             session_id=self.sid, run_id=self.run_id)
        self.until("MK.activityPanel.items().length >= 64", "the pushed gate verdict on the timeline")
        first = self.page.locator("#view-activity .ap-row").first.text_content()
        self.assertIn("blocked", first)
        self.text_until("#s1-card", "61 fired", "the card's gate count to follow")
        self.assertIn("1 blocked", self.page.text_content("#s1-card"))
        after = len([r for r in self.requests if "/api/activity" in r[1]])
        self.assertEqual(before, after, "the page asked the server for nothing — it was pushed")

    def test_4_a_row_opens_its_detail_and_the_write_offers_its_diff(self):
        # the write is the oldest item: scroll the virtualised list to it first
        self.page.evaluate("MK.activityPanel.reveal(MK.activityPanel.items().findIndex(i => i.type === 'edit'))")
        self.page.locator("#view-activity .ap-row", has_text="wrote shop/text.py").first.click()
        self.text_until("#view-activity .ap-detail", "shop/text.py", "the item's detail")
        self.assertIn("Diff this write", self.page.text_content("#view-activity .ap-detail"))

    def test_5_viewing_lists_the_session_under_live_sessions(self):
        """UX-SIMPLIFY §5: the jobs drawer's live half folded into Viewing ▾ → Live sessions."""
        self.page.click("#session-chip")
        self.page.wait_for_selector("#viewing-pop:not([hidden])")
        self.text_until("#join-list", self.sid[:8], "the session in Live sessions")
        self.press("Escape")
        self.assertTrue(self.page.evaluate("document.getElementById('viewing-pop').hidden"))
        self.assertEqual("session-chip", self.page.evaluate("document.activeElement.id"),
                         "Esc returns focus to the chip that opened it")
