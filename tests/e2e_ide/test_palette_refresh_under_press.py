"""IDE e2e - the ⌘P list is never replaced under the person's hand (0.0.28 D).

The cause of the Linux Firefox full-suite red "`open_file` waited 20 s for Navigate; the page stayed
in Review" (probe run 42623534645: TheTableViewWorks, FindInTheCurrentFile - each the FIRST test of
its class, on a fresh server whose symbol index is still building). The palette asks again when the
index job finishes (P32 `whenSymbols`), and every answer rebuilt the list: new row elements, the
selection back to row 0. A press that went down on the old row came up on a new one - two different
elements, so the browser fires no click at all - and an arrowed-to row was silently swapped for the
first. One file alone never sees it: only a fresh server under full-suite load puts the index's
"done" inside the 100 ms of a click.

The contract:
* a press on a row that is released on it opens that row, whatever the list was asked in between;
* a row picked with the arrows is still the picked row after the list is asked again;
* a refresh held back by a press is not lost: it lands once the button comes up.

"Asked again" is the page's own refresh path driven from outside (an `input` event on the field, the
same `run()` the index's "done" calls); "answered" is a positive state - the page has handled the
/api/quickopen reply (`settled`), never a sleep.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import unittest

import _harness as H


class AResultIsNotReplacedUnderThePress(H.IdeFlow):
    FIXTURE = "shop"

    # -- helpers ---------------------------------------------------------------------------------

    def open_palette(self, needle, row_text):
        p = self.page
        self.until("!!(window.MK && MK.ui && MK.ui.restored)", "the page's own restore done")
        p.evaluate("""(() => { if (window.__qo) return; window.__qo = { settled: 0, asked: [] };
            const api = MK.api;
            MK.api = (path, ...rest) => {
              const r = api(path, ...rest);
              // after the palette's own continuation (it awaits this same reply): a task later
              if (String(path).startsWith('/api/quickopen')) {
                const q = rest[0] && rest[0].q;
                r.finally(() => setTimeout(() => { window.__qo.settled++; window.__qo.asked.push(q); }, 0));
              }
              return r;
            };
          })()""")
        p.click("#qo-open")
        p.wait_for_selector("#qo:not([hidden])")
        p.fill("#qo-input", needle)
        # the answer to THIS query, handled: a row from the empty query's answer is not it (and an
        # arrow pressed on that list would be pressed on rows the answer is about to replace)
        self.until("window.__qo.asked.includes(%r) && [...document.querySelectorAll('#qo-results .qo-row')]"
                   ".some((x) => x.textContent.includes(%r))" % (needle, row_text), "the answer for " + needle)

    def ask_again_and_wait(self):
        """The palette's own refresh (the index-done path), and wait until the page has handled it."""
        p = self.page
        before = p.evaluate("window.__qo.settled")
        p.evaluate("document.getElementById('qo-input').dispatchEvent(new Event('input'))")
        self.until("window.__qo.settled > %d" % before, "the page handled the second answer")

    def tearDown(self):
        if getattr(self, "page", None) is not None and not self.no_browser:
            self.page.mouse.up()
            self.page.evaluate("(() => { const q = document.getElementById('qo');"
                               " if (q && !q.hidden) document.getElementById('qo-input')"
                               ".dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); })()")
        super().tearDown()

    # -- the contract ----------------------------------------------------------------------------

    def test_1_a_press_released_on_its_row_opens_it_though_the_list_was_asked_again(self):
        p = self.page
        self.open_palette("text.py", "text.py")
        row = p.locator("#qo-results .qo-row", has_text="text.py").first
        row.scroll_into_view_if_needed()
        b = row.bounding_box()
        p.mouse.move(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)
        p.mouse.down()
        self.ask_again_and_wait()                      # the index finished while the button was down
        p.mouse.up()
        p.wait_for_selector("#view-navigate:not([hidden])")
        self.until("MK.nav.path === 'shop/text.py'", "the row pressed is the file opened")

    def test_2_a_row_picked_with_the_arrows_is_still_picked_after_the_list_is_asked_again(self):
        p = self.page
        self.go("review")
        self.open_palette("py", "orders.py")
        picked = None
        for _ in range(12):                            # off row 0, onto a .py file row
            self.press("ArrowDown")
            # a "Files" row (a changed file's row opens its diff): no status letter
            picked = p.evaluate("(() => { const r = document.querySelector('#qo-results .qo-row.active');"
                                " const a = r && !r.querySelector('.st') && r.querySelector('.nm');"
                                " return a ? a.textContent : null; })()")
            if picked and picked.partition("  ")[0].endswith(".py") and not self.is_open(picked):
                break
        self.assertIsNotNone(picked, "a file row is picked")
        name, _, folder = picked.partition("  ")
        self.assertTrue(name.endswith(".py"), "the picked row is a .py file: %r" % picked)
        folder = folder.strip().rstrip("/")
        self.ask_again_and_wait()
        self.press("Enter")
        want = (folder + "/" + name) if folder else name
        # Navigate AND that file: the first row is a changed file (Enter opens its diff, in Review), and
        # the file test 1 left open is never the picked one - so neither stands in for the pick
        self.until("MK.ui.mode === 'navigate' && MK.nav.path === %r" % want,
                   "Enter opens the row that was picked (%s)" % want)

    def is_open(self, nm_text):
        name, _, folder = nm_text.partition("  ")
        folder = folder.strip().rstrip("/")
        return self.page.evaluate("MK.nav.path") == ((folder + "/" + name) if folder else name)

    def test_3_a_refresh_held_back_by_a_press_lands_when_the_button_comes_up(self):
        p = self.page
        self.go("review")
        # the symbol index built: no "done" re-ask of its own can redraw the list, so a redraw after
        # the release is the held-back answer and nothing else
        self.index_built()
        self.open_palette("py", "orders.py")
        p.evaluate("window.__old = document.querySelector('#qo-results .qo-row')")
        head = p.locator("#qo-results .qo-sec").first      # a press on a heading opens nothing
        head.wait_for()
        b = head.bounding_box()
        p.mouse.move(b["x"] + 4, b["y"] + b["height"] / 2)
        p.mouse.down()
        self.ask_again_and_wait()
        p.mouse.up()
        self.until("!window.__old.isConnected && document.querySelectorAll('#qo-results .qo-row').length > 0",
                   "the held-back answer is drawn once the press is over")
        self.assertFalse(p.is_hidden("#qo"), "a press on a heading leaves the palette open")

    # -- a press whose release is lost never freezes the list (verifier round-2 Low) ----------------

    def index_built(self):
        """The symbol index built: no "done" re-ask of its own can redraw the list after this."""
        self.until("(() => { if (!window.__idx) { window.__idx = 'asking'; MK.api('/api/quickopen', { q: 'py', changed: [] })"
                   ".then((r) => { window.__idx = r && r.symbols_state; }); } const v = window.__idx;"
                   " if (v !== 'asking' && v === 'building') window.__idx = null; return v !== 'asking' && v !== null"
                   " && v !== 'building'; })()", "the symbol index to be built")

    def press_and_hold(self):
        """A press in the list that this page will never see come up (the mouse stays down, still)."""
        p = self.page
        self.index_built()
        # close() keeps the last list's data-q (an earlier test's 'py'): mark it stale, so the wait
        # below is for THIS opening's answer, not a list about to be replaced under the pointer
        p.evaluate("(() => { const r = document.getElementById('qo-results'); if (r) r.dataset.q = '__stale__'; })()")
        self.open_palette("py", "orders.py")
        # the LIST is the answer to 'py' before the press (an earlier test's 'py' is in `asked` too)
        self.until("document.getElementById('qo-results').dataset.q === 'py'", "the list answers 'py'")
        head = p.locator("#qo-results .qo-sec").first
        head.wait_for()
        b = head.bounding_box()
        p.mouse.move(b["x"] + 4, b["y"] + b["height"] / 2)
        p.mouse.down()

    def answers(self, needle):
        """Ask by typing (no mouse event at all), and wait for the LIST to be the answer to it."""
        self.page.fill("#qo-input", needle)
        self.until("window.__qo.asked.includes(%r) && document.getElementById('qo-results').dataset.q === %r"
                   % (needle, needle), "the list is redrawn for %r" % needle)

    def test_4_a_palette_closed_under_a_held_press_redraws_when_reopened(self):
        p = self.page
        self.go("review")
        self.press_and_hold()
        # the press took focus off the field: Escape is delivered where the palette handles it
        p.evaluate("document.getElementById('qo-input')"
                   ".dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))")
        p.wait_for_selector("#qo", state="hidden")
        p.evaluate("MK.openQuick()")                   # reopened from the keyboard path: no mouse event
        p.wait_for_selector("#qo:not([hidden])")
        self.answers("text.py")

    def test_5_a_window_blur_ends_a_held_press(self):
        self.go("review")
        self.press_and_hold()
        self.page.evaluate("window.dispatchEvent(new Event('blur'))")
        self.answers("text.py")

    def test_6_a_hidden_page_ends_a_held_press(self):
        self.go("review")
        self.press_and_hold()
        self.page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        self.answers("text.py")


if __name__ == "__main__":
    unittest.main()
