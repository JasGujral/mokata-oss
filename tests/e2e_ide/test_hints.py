"""0.0.27 P14 — the next step on the session card (UX-SIMPLIFY: the Sessions header card of the
picked session), in a real browser: derived from what mokata
recorded for the session's run, naming the fact it came from; re-asked ONLY for the session a pushed
change names; dismissable from the keyboard (and remembered); and a store that cannot be read is
said as UNKNOWN on the card — never "nothing to do"."""

import json
import os
import sys
import time

import _harness as H

SRC = H.SRC
if SRC not in sys.path:                     # the seeding below uses THIS checkout's writers
    sys.path.insert(0, SRC)

SID, RUN = "e2e-hints-0001", "R-e2e-hints"
TEST = "tests/test_text.py::test_shout"


def _seed(repo):
    """One live session bound to its run: an approved approach, a spec, one RED test run."""
    from mokata import run_intent
    from mokata import session_index as SI
    from mokata.engine.spec import AcceptanceCriterion, Spec
    from mokata.govern.resume import PipelineCheckpoint
    from mokata.state import StateStore
    from mokata.tdd_state import state_dir
    from mokata.test_runs import record_test_run
    store = StateStore(state_dir(repo))
    PipelineCheckpoint(store, RUN).ensure_registered()
    store.update(f"pipeline_run__{RUN}", lambda c: dict(c, created_at=time.time() - 3600),
                 default={})
    store.write(f"approved_approach__{RUN}", {"topic": "shout", "approach": {"name": "A"},
                                              "approver": "e2e", "approved_at": "2026-10-31"})
    store.write(f"emitted_spec__{RUN}", Spec(title="shout", criteria=[
        AcceptanceCriterion("AC-1", "shout upper-cases")]).to_dict())
    record_test_run(repo, RUN, passed=False, command="pytest", tests=[TEST],
                    ac_map={"AC-1": [TEST]}, at=time.time() - 600)
    SI.on_start(repo, SID, "startup", repo, t=time.time() - 30, proc=None)
    run_intent.bind_registered(repo, SID, RUN)
    # the project's test command, for the IDE re-run job (step 2c): a runner that passes
    with open(os.path.join(repo, "runner.py"), "w", encoding="utf-8") as fh:
        fh.write("import sys\nprint('running', sys.argv[1], flush=True)\n")
    man = os.path.join(repo, ".mokata", "manifest.json")
    with open(man, encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("settings", {}).setdefault("baseline", {})["test_command"] = \
        '"%s" runner.py' % sys.executable
    with open(man, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def _write_and_push(repo, body):
    """A write in the session (a timeline capture) and the gate decision the write gate records
    beside it — the pushed change that names the session."""
    from mokata.events import emit
    from mokata.events.schema import GateDecision
    from mokata.ide import snapshots as S
    path = os.path.join(repo, "shop", "text.py")
    S.capture_before_first_write(repo, path, SID, RUN)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(body)
    S.capture(repo, path, S.AFTER, SID, RUN)
    emit(repo, GateDecision(gate="write_gate", decision="allowed", subject="shop/text.py"),
         session_id=SID, run_id=RUN)


def _hint_reqs(requests):
    return [u for _m, u in requests if "/api/hints" in u]


class EachCardSaysItsDerivedNextStep(H.IdeFlow):
    FIXTURE = "shop"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if cls.no_browser:
            return
        _seed(cls.repo)

    def card(self):
        return self.page.locator("#s1-card .ap-card", has_text=SID[:8]).first

    def test_1_the_card_names_the_next_step_and_the_fact_it_came_from(self):
        self.page.evaluate("document.activeElement && document.activeElement.blur()")
        self.press("g")
        self.press("s")
        self.page.wait_for_selector("#view-activity:not([hidden])")
        # hints are asked for the PICKED session only (its header card)
        self.page.locator("#s1-rows button.s1-row", has_text=SID[:8]).first.click()
        self.text_until("#s1-card", "Next:", "the card's next step")
        self.until("MK.activityPanel.hintSettled()", "the hints to answer")
        nxt = self.card().locator(".ap-next").text_content()
        self.assertIn("failing", nxt)
        self.assertIn("test_shout", nxt)
        self.assertIn("test_record", nxt, "the hint names the recorded fact it came from")
        # a push that landed after the first answer (the seeding's own run-state writes reach the
        # watcher a tick later) re-asks once: wait on the STATE, not a time
        self.until("MK.activityPanel.hintSettled() && document.querySelector("
                   "'#s1-card .ap-next[aria-busy=\"false\"]') !== null",
                   "the card's next step to settle")

    def test_2_a_pushed_write_re_asks_only_that_session_and_says_re_run(self):
        self.until("MK.activityPanel.settled() && MK.activityPanel.hintSettled()", "settle")
        before = len(_hint_reqs(self.requests))
        from mokata.events import emit
        from mokata.events.schema import GateDecision
        from mokata.ide import snapshots as S
        path = os.path.join(self.repo, "shop", "text.py")
        S.capture_before_first_write(self.repo, path, SID, RUN)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n\ndef whisper(s):\n    return s.lower()\n")
        S.capture(self.repo, path, S.AFTER, SID, RUN)
        # what the write gate records beside the write — the pushed change that names the session
        emit(self.repo, GateDecision(gate="write_gate", decision="allowed", subject="shop/text.py"),
             session_id=SID, run_id=RUN)
        self.until("(MK.activityPanel.hints()[%r] || {hints: []}).hints.some(h => h.rule === 'rerun')"
                   % SID, "the card's hint to become re-run")
        self.until("MK.activityPanel.hintSettled()", "the re-ask to settle")
        nxt = self.card().locator(".ap-next").text_content()
        self.assertIn("since — re-run", nxt)
        self.assertIn("timeline", nxt, "the write it came from is named")
        asked = _hint_reqs(self.requests)[before:]
        self.assertTrue(asked, "the pushed change re-asked")
        self.assertLessEqual(len(asked), 2, "one request in flight plus at most one trailing")
        for u in asked:
            self.assertIn(SID, u)

    def whys(self, since):
        return self.page.evaluate("MK.activityPanel.hintWhy()")[since:]

    def rule(self):
        return self.page.evaluate("((MK.activityPanel.hints()[%r] || {}).hints || [])"
                                  ".map(h => h.rule).join(',')" % SID)

    def test_2b_a_test_run_recorded_in_a_terminal_updates_the_card_with_no_reload(self):
        """HIGH-1 — the offered command, run in a terminal, records a run and emits NO event: the
        watcher's `runstate` push re-asks the card within one bus round-trip; the page is not
        reloaded and the person does not have to focus anything."""
        self.until("MK.activityPanel.hintSettled()", "settle")
        self.assertIn("rerun", self.rule())
        self.page.evaluate("window.__p14_no_reload = 1")
        from mokata.test_runs import record_test_run
        n0 = len(self.requests)
        w0 = len(self.page.evaluate("MK.activityPanel.hintWhy()"))
        record_test_run(self.repo, RUN, passed=True, command="pytest", tests=[TEST])
        self.until("!(MK.activityPanel.hints()[%r] || {hints: []}).hints.some(h => h.rule === "
                   "'rerun') && MK.activityPanel.hintSettled()" % SID,
                   "the card to leave re-run after the terminal's test run", timeout_ms=8000)
        self.until("MK.activityPanel.hintWhy().slice(%d).includes('runstate')" % w0,
                   "re-asked BY the watcher's runstate push")
        self.assertTrue(_hint_reqs(self.requests[n0:]))
        self.assertEqual(1, self.page.evaluate("window.__p14_no_reload"), "no reload")
        self.assertIn("review", self.rule())
        self.assertIn("as of", self.card().locator(".ap-next").text_content())

    def test_2c_an_ide_re_run_job_updates_the_card_when_it_finishes(self):
        """HIGH-1 — the IDE's own re-run (a `tests.run` job) records the run; when the job ends the
        card is re-asked."""
        _write_and_push(self.repo, "\n\ndef murmur(s):\n    return s\n")
        self.until("(MK.activityPanel.hints()[%r] || {hints: []}).hints.some(h => h.rule === "
                   "'rerun')" % SID, "the card to say re-run after the write")
        w0 = len(self.page.evaluate("MK.activityPanel.hintWhy()"))
        res = self.page.evaluate("MK.api('/api/review/tests/run', {run: %r, all: true})" % RUN)
        self.assertNotIn("error", res or {}, res)
        self.until("!(MK.activityPanel.hints()[%r] || {hints: []}).hints.some(h => h.rule === "
                   "'rerun') && MK.activityPanel.hintSettled()" % SID,
                   "the card to leave re-run when the IDE re-run job ends", timeout_ms=15000)
        self.until("MK.activityPanel.hintWhy().slice(%d).includes('job')" % w0,
                   "the job's end to re-ask the cards")

    def test_2d_a_stale_answer_says_its_age_and_may_be_out_of_date_with_the_stream_down(self):
        self.page.evaluate("MK.activityPanel.setBusDown(true)")
        nxt = self.card().locator(".ap-next").text_content()
        self.assertIn("as of", nxt)
        self.assertIn("may be out of date", nxt)
        self.page.evaluate("MK.activityPanel.setBusDown(false)")
        self.assertNotIn("may be out of date", self.card().locator(".ap-next").text_content())

    def test_2e_coming_back_to_the_window_re_asks_once(self):
        self.until("MK.activityPanel.hintSettled()", "settle")
        self.page.wait_for_timeout(2500)      # pushes from the earlier steps' writes have landed
        self.until("MK.activityPanel.hintSettled()", "settle")
        before = len(_hint_reqs(self.requests))
        self.page.evaluate("window.dispatchEvent(new Event('focus'))")
        self.until("MK.activityPanel.hintSettled() && MK.activityPanel.hintRequests() > 0", "re-ask")
        self.page.wait_for_timeout(300)
        self.assertEqual(1, len(_hint_reqs(self.requests)) - before, "one re-ask, no timer")
        self.assertEqual("focus", self.page.evaluate("MK.activityPanel.hintWhy()").pop())

    def test_3_escape_dismisses_the_hint_returns_focus_and_it_stays_dismissed(self):
        x = self.card().locator(".ap-next-x")
        x.focus()
        self.press("Escape")
        self.text_until("#s1-card", "dismissed", "the hint to be dismissed")
        self.assertEqual(SID, self.page.evaluate(
            "document.activeElement && document.activeElement.dataset.sid"),
            "focus returns to the card")
        self.page.reload()
        self.page.wait_for_selector("#view-activity:not([hidden])")
        self.until("MK.activityPanel && MK.activityPanel.settled() && "
                   "MK.activityPanel.hintSettled() && !!MK.activityPanel.hints()[%r]" % SID,
                   "the reloaded panel's hints")
        self.assertIn("dismissed", self.card().locator(".ap-next").text_content())

    def test_4_an_unreadable_store_says_unknown_on_the_card(self):
        from mokata.events import emit
        from mokata.events.schema import GateDecision
        from mokata.tdd_state import state_dir
        with open(os.path.join(state_dir(self.repo), f"emitted_spec__{RUN}.json"), "w",
                  encoding="utf-8") as fh:
            fh.write("{torn")
        emit(self.repo, GateDecision(gate="write_gate", decision="allowed", subject="shop/text.py"),
             session_id=SID, run_id=RUN)
        self.text_until("#s1-card", "unknown —", "the card to say unknown")
        nxt = self.card().locator(".ap-next").text_content()
        self.assertIn("emitted spec", nxt)
        self.assertNotIn("nothing owed", nxt)

    def test_5_with_the_live_stream_down_the_panel_says_so_and_offers_refresh(self):
        self.page.evaluate("MK.activityPanel.setBusDown(true)")
        note = self.page.locator("#view-activity .ap-busdown")
        self.assertTrue(note.is_visible())
        self.assertIn("live updates off", note.text_content())
        self.assertEqual(1, note.locator("button", has_text="Refresh").count())
        self.page.evaluate("MK.activityPanel.setBusDown(false)")
        self.assertFalse(note.is_visible())


# ===================================================================== PINS-UI (P14 r2 Lows)

PRED, HEAD, CHAIN_RUN = "e2e-chain-pred-0001", "e2e-chain-head-0002", "R-e2e-chain"


def _seed_chain(repo):
    """A resumed conversation (O10): PRED ran and ended; HEAD is a NEW id resumed from it (its
    transcript holds PRED's lines). One run, two session records, ONE chain."""
    from mokata import run_intent
    from mokata import session_index as SI
    from mokata.govern.resume import PipelineCheckpoint
    from mokata.state import StateStore
    from mokata.tdd_state import state_dir
    from mokata.test_runs import record_test_run
    store = StateStore(state_dir(repo))
    PipelineCheckpoint(store, CHAIN_RUN).ensure_registered()
    record_test_run(repo, CHAIN_RUN, passed=False, command="pytest", tests=[TEST], at=time.time() - 900)
    now = time.time()
    SI.on_start(repo, PRED, "startup", repo, t=now - 600, proc=None)
    run_intent.bind_registered(repo, PRED, CHAIN_RUN)
    SI.on_end(repo, PRED, "logout", t=now - 500)
    tr = os.path.join(repo, ".mokata", "temp_local", "e2e-transcript.jsonl")
    os.makedirs(os.path.dirname(tr), exist_ok=True)
    with open(tr, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"sessionId": PRED, "type": "user"}) + "\n")
        fh.write(json.dumps({"sessionId": HEAD, "type": "user"}) + "\n")
    SI.on_start(repo, HEAD, "resume", repo, transcript_path=tr, t=now - 60, proc=None)
    run_intent.bind_registered(repo, HEAD, CHAIN_RUN)


class AResumedChainIsShownOnce(H.IdeFlow):
    """AC16 — a resumed chain is ONE run: one row in the Sessions list, one card, one "Next:"
    line, whichever of its sessions is picked."""
    FIXTURE = "shop"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if cls.no_browser:
            return
        _seed_chain(cls.repo)

    def shown_once(self, why):
        p = self.page
        self.until("MK.activityPanel.hintSettled() && document.querySelectorAll('#s1-card .ap-next').length > 0"
                   " && !document.querySelector('#s1-card .ap-next[aria-busy=\"true\"]')", "the card's next step: " + why)
        self.assertEqual(1, p.locator("#s1-card .ap-card").count(), "one card for the chain: " + why)
        self.assertEqual(1, p.locator("#s1-card .ap-next").count(), "its next step once: " + why)
        card = p.locator("#s1-card .ap-card").first
        self.assertEqual(HEAD, card.get_attribute("data-sid"), "the card is the chain's newest session: " + why)
        self.assertIn("resumed ×1", card.text_content())
        self.assertIn(CHAIN_RUN, card.locator(".ap-next").text_content(), "derived from the chain's ONE run: " + why)

    def test_1_the_list_holds_the_chain_as_one_row(self):
        p = self.page
        self.go("activity")
        self.until("document.querySelectorAll('#s1-rows button.s1-row').length > 0", "the sessions list")
        rows = p.evaluate("[...document.querySelectorAll('#s1-rows button.s1-row')].map(b => b.textContent)")
        self.assertEqual(1, sum(HEAD[:8] in r for r in rows), rows)
        self.assertEqual(0, sum(PRED[:8] in r and HEAD[:8] not in r for r in rows),
                         "the predecessor is folded into the chain's row, not listed again: %r" % rows)

    def test_2_picking_it_shows_one_card_and_one_next_step(self):
        p = self.page
        before = len(_hint_reqs(self.requests))
        p.locator("#s1-rows button.s1-row", has_text=HEAD[:8]).first.click()
        self.shown_once("picked from the list")
        asked = _hint_reqs(self.requests)[before:]
        self.assertTrue(asked)
        for u in asked:
            self.assertNotIn(PRED, u, "hints are asked for the chain's head, never per predecessor")

    def test_3_focusing_the_predecessor_still_shows_the_chain_once(self):
        self.page.evaluate("MK.switchSession(%r)" % PRED)
        self.until("MK.ui.focus === %r" % PRED, "the predecessor in focus")
        self.shown_once("the predecessor focused")


class HintsFitAt200PercentAndInANarrowPanel(H.IdeFlow):
    """AC18 — at 200% zoom (the CSS viewport halves) and in a narrow panel the next step is all on
    screen: nothing clipped, no horizontal scroll, and its keyboard path still works with focus
    never dropped to <body>."""
    FIXTURE = "shop"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if cls.no_browser:
            return
        _seed(cls.repo)

    FITS = r"""(() => {
      const out = [];
      const de = document.documentElement;
      if (de.scrollWidth > de.clientWidth + 1) out.push('the page scrolls sideways: ' + de.scrollWidth + ' > ' + de.clientWidth);
      for (const box of document.querySelectorAll('#s1-card .ap-next, #rv-next:not([hidden])')) {
        const b = box.getBoundingClientRect();
        if (!b.width) continue;
        if (b.left < -0.5 || b.right > window.innerWidth + 0.5) out.push('the line leaves the window: ' + b.left + '..' + b.right + ' of ' + window.innerWidth);
        if (box.scrollWidth > box.clientWidth + 1) out.push('the line overflows its box: ' + box.scrollWidth + ' > ' + box.clientWidth);
        for (let n = box.parentElement; n && n !== document.body; n = n.parentElement) {
          if (n.scrollWidth > n.clientWidth + 1 && getComputedStyle(n).overflowX !== 'visible') { out.push('a container clips it sideways: ' + (n.id || n.className)); break; }
        }
        for (const c of box.children) {
          const r = c.getBoundingClientRect();
          if (!r.width) continue;
          if (r.left < b.left - 0.5 || r.right > b.right + 0.5) out.push('cut off: ' + c.className + ' ' + JSON.stringify(c.textContent.slice(0, 40)));
          if (c.classList.contains('ap-next-why')) continue;   // its tail is an ellipsis by design; the full text is in its title-free hover
          if (c.scrollWidth > c.clientWidth + 1 && getComputedStyle(c).overflowX !== 'visible') out.push('clipped: ' + c.className);
        }
      }
      return out;
    })()"""

    def test_1_the_card_fits_at_200_percent_and_in_a_narrow_panel(self):
        p = self.page
        self.go("activity")
        p.locator("#s1-rows button.s1-row", has_text=SID[:8]).first.click()
        # settled AND painted (a push from the seeding's own writes re-asks once): wait on state
        self.until("MK.activityPanel.hintSettled() && !!document.querySelector("
                   "'#s1-card .ap-next[aria-busy=\"false\"] .ap-cmd')", "the card's next step")
        try:
            for (w, h), what in (((700, 450), "200% zoom of a 1400x900 window"), ((420, 800), "a narrow panel")):
                p.set_viewport_size({"width": w, "height": h})
                self.until("window.innerWidth === %d" % w, what)
                p.evaluate("document.querySelector('#s1-card .ap-next').scrollIntoView({block: 'nearest'})")
                self.assertEqual([], p.evaluate(self.FITS), what)
                self.assertTrue(p.locator("#s1-card .ap-next .ap-cmd").is_visible(), "the command is on screen: " + what)
                self.assertTrue(p.locator("#s1-card .ap-next-x").is_visible(), "dismiss is on screen: " + what)
        finally:
            p.set_viewport_size({"width": 1400, "height": 900})

    def test_2_the_keyboard_path_holds_in_a_narrow_panel(self):
        p = self.page
        p.set_viewport_size({"width": 420, "height": 800})
        try:
            self.until("window.innerWidth === 420", "a narrow panel")
            p.focus("#s1-card .ap-next-x")
            self.press("Escape")
            self.text_until("#s1-card", "dismissed", "the hint to be dismissed")
            self.assertEqual(SID, p.evaluate("document.activeElement && document.activeElement.dataset.sid"),
                             "focus returns to the card, never <body>")
            self.assertEqual([], p.evaluate(self.FITS), "the dismissed line fits too")
        finally:
            p.set_viewport_size({"width": 1400, "height": 900})


class AnOlderAnswerIsNeverPaintedOverANewer(H.IdeFlow):
    """AC19 — three facts in a row: a RED run (on screen), a GREEN run (re-ask 1 is sent and its
    answer, "review", is derived), then a write (re-ask 2 is owed while 1 is in flight). Answer 1
    lands late: it is older than the write, so it is DROPPED — the card stays "re-checking" on
    what it showed and "review" is never painted. Only answer 2 ("re-run") is shown as current."""
    FIXTURE = "shop"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if cls.no_browser:
            return
        _seed(cls.repo)

    def rules(self):
        return self.page.evaluate("((MK.activityPanel.hints()[%r] || {}).hints || []).map(h => h.rule)" % SID)

    def test_1_the_late_old_answer_is_dropped(self):
        p = self.page
        self.go("activity")
        p.locator("#s1-rows button.s1-row", has_text=SID[:8]).first.click()
        self.until("MK.activityPanel.hintSettled() && document.querySelector("
                   "'#s1-card .ap-next[aria-busy=\"false\"]') !== null", "the card's first answer")
        self.assertIn("failing", self.card_text())
        old, later, gate = [], [], {"on": True}

        def hold(route):
            if not gate["on"]:
                route.continue_()
            elif not old:
                old.append((route, route.fetch()))    # answer 1, derived NOW (after the green run)
            else:
                later.append(route)
        p.route("**/api/hints*", hold)
        try:
            from mokata.test_runs import record_test_run
            record_test_run(self.repo, RUN, passed=True, command="pytest", tests=[TEST])   # fact 2
            n0 = p.evaluate("MK.activityPanel.hintRequests()")
            p.evaluate("window.dispatchEvent(new Event('focus'))")         # re-ask 1 (or the push's)
            self.until_py(lambda: old, "re-ask 1 held with its answer")
            n1 = p.evaluate("MK.activityPanel.hintRequests()")
            self.assertGreaterEqual(n1, n0 + 1)
            w0 = len(p.evaluate("MK.activityPanel.hintWhy()"))
            _write_and_push(self.repo, "\n\ndef hum(s):\n    return s\n")      # fact 3: a write
            p.evaluate("window.dispatchEvent(new Event('focus'))")
            self.until("MK.activityPanel.hintWhy().length > %d" % w0, "re-ask 2, owed while 1 flies")
            self.assertEqual(n1, p.evaluate("MK.activityPanel.hintRequests()"), "one request in flight, no more")
            route, resp = old[0]
            self.assertIn('"review"', resp.text(), "answer 1 says review (it was derived after the green run)")
            route.fulfill(response=resp)                                  # answer 1 lands late
            self.until("MK.activityPanel.hintRequests() > %d" % n1, "the trailing re-ask sent")
            self.assertNotIn("review", self.rules(), "the older answer is never applied")
            box = p.locator("#s1-card .ap-next")
            self.assertEqual("true", box.get_attribute("aria-busy"), "the card is still re-checking")
            self.assertIn("failing", self.card_text(), "it still shows what it showed, dimmed — never answer 1")
            self.until_py(lambda: later, "the trailing re-ask held")
            gate["on"] = False
            for r in later:
                r.continue_()
            self.until("MK.activityPanel.hintSettled() && document.querySelector("
                       "'#s1-card .ap-next[aria-busy=\"false\"]') !== null", "the newer answer")
            self.assertIn("rerun", self.rules(), "the answer asked after the write is the one shown")
            self.assertIn("re-run", self.card_text())
        finally:
            p.unroute("**/api/hints*", hold)

    def card_text(self):
        return self.page.locator("#s1-card .ap-next").text_content()

    def until_py(self, fn, what):
        """Wait on a test-side state the page's events fill (route handlers run while it polls)."""
        deadline = time.time() + H.STEP_MS / 1000.0
        while time.time() < deadline:
            if fn():
                return
            self.page.evaluate("0")
            time.sleep(0.05)
        self.fail("timed out waiting for %s" % what)
