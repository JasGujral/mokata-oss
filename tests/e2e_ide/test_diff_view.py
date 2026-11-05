"""IDE e2e — how the review screen shows a diff (0.0.27 P5, ID-DIFF-SPLIT + ID-DIFF-INTRALINE).

A changed line shows WHICH words changed (and a `<script>` in it stays text); Split puts the old
and the new side by side, each with its own line numbers, and a click on a number copies
`path:line` for that side (the OLD path on the left of a rename); a drag selects one side only;
"Ignore whitespace" hides a re-indent and SAYS how many lines it hid, while a Python indentation
change is kept; the unchanged lines between hunks expand in pages; a rewritten 60,000-token line is
diffed as a visible job; keyboard focus never falls to the page body.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import random
import unittest

import _harness as H

APP_OLD = ("function total(items) {\n"
           "  const count = items.length;\n"
           "  const s = 0;\n"
           "  return count;\n"
           "}\n")
APP_NEW = ("function total(items) {\n"
           "  const counter = items.length;\n"
           "  const s = \"<img src=x onerror=window.__pwned=1><script>window.__pwned=2</script>\";\n"
           "      return count;\n"
           "}\n")
# lines 127-146 and 160 are longer than a context line may be (LINE_BYTES): each is shown CUT, and
# twenty of them are more than one page's byte budget, so "above" takes two pages to fill
_LONGS = set(range(127, 147)) | {160}
LONG_OLD = "".join(("line %d " % i + "q" * 100000 if i in _LONGS else "line %d" % i) + "\n"
                   for i in range(1, 301))
LONG_NEW = LONG_OLD.replace("line 150\n", "line one-fifty\n")
PY_OLD = "def f(a):\n    if a:\n        b()\n    c()\n"
PY_NEW = "def f(a):\n    if a:\n        b()\n        c()\n"
REN_OLD = "".join("v%d = %d\n" % (i, i) for i in range(1, 21))
REN_NEW = REN_OLD.replace("v10 = 10\n", "v10 = 100\n")


def _minified(seed, n):
    rnd = random.Random(seed)
    return " ".join("%s%d" % (rnd.choice("abcdefgh"), rnd.randrange(10 ** 6)) for _ in range(n))


def build(base):
    repo = H.make_repo("bare", base)
    H._write(repo, "web/app.js", APP_OLD)
    H._write(repo, "src/long.txt", LONG_OLD)
    H._write(repo, "src/m.py", PY_OLD)
    H._write(repo, "src/old_name.py", REN_OLD)
    H._write(repo, "data/min.js", _minified(1, 30000) + "\n")
    H._git(repo, "add", "-A")
    H._git(repo, "commit", "-q", "-m", "p5 base")
    H._write(repo, "web/app.js", APP_NEW)
    H._write(repo, "src/long.txt", LONG_NEW)
    H._write(repo, "src/m.py", PY_NEW)
    H._git(repo, "mv", "src/old_name.py", "src/new_name.py")
    H._write(repo, "src/new_name.py", REN_NEW)
    H._write(repo, "data/min.js", _minified(2, 30000) + "\n")
    return repo


class TheDiffViewWorks(H.IdeFlow):
    FIXTURE = staticmethod(build)

    def open_change(self, path):
        p = self.page
        p.locator("#nav-list li", has_text=path.rsplit("/", 1)[-1]).first.click()
        self.until("(() => { const f = MK.ui.files[MK.ui.sel]; return !!(f && f.path === %r); })()"
                   % path, "%s to be the file in view" % path)
        p.wait_for_selector("#diff section.hunk table.d td.tx[data-i]")

    def active(self):
        return self.page.evaluate("document.activeElement ? (document.activeElement.id || "
                                  "document.activeElement.className || document.activeElement.tagName) : ''")

    def test_1_unified_marks_the_changed_words_and_markup_stays_text(self):
        p = self.page
        p.evaluate("try { localStorage.removeItem('mokata-ide:diff-mode'); "
                   "localStorage.removeItem('mokata-ide:diff-ws'); } catch (e) {}")
        self.go("review")
        p.wait_for_selector("#nav-list li", state="attached")
        self.open_change("web/app.js")
        self.until("[...document.querySelectorAll('#diff .ix')].some(n => n.textContent === 'counter')",
                   "the renamed word to be marked")
        marks = p.evaluate("[...document.querySelectorAll('#diff tr.del .ix')].map(n => n.textContent)")
        self.assertIn("count", marks)
        self.assertNotIn("items", "".join(marks), "an unchanged word was marked")
        text = p.evaluate("document.querySelector('#diff').textContent")
        # the whole string is ONE changed run, so it is painted as one marked span: that span is
        # where markup would turn into elements if a cell were ever written as HTML
        self.assertIn("<img src=x onerror=window.__pwned=1><script>window.__pwned=2</script>", text,
                      "markup must show as text")
        self.assertTrue(p.evaluate("[...document.querySelectorAll('#diff tr.add .ix')]"
                                   ".some(n => n.textContent.includes('<script>'))"),
                        "the markup sits inside a marked span")
        self.assertIsNone(p.evaluate("window.__pwned"))
        self.assertEqual(0, p.locator("#diff script, #diff b, #diff [onerror]").count())

    def test_2_split_puts_the_sides_together_and_keeps_focus(self):
        p = self.page
        p.click(".dv-tools [data-mode='split']")
        p.wait_for_selector("#diff table.d.split td.tx[data-side='old']")
        self.assertEqual("true", p.get_attribute(".dv-tools [data-mode='split']", "aria-pressed"))
        self.assertEqual("seg", self.active(), "focus stays on the toggle")
        row = p.evaluate("""(() => { const tr = [...document.querySelectorAll('#diff table.split tr.chg')]
              .find(r => r.textContent.includes('counter = items'));
              return tr ? [...tr.children].map(td => [td.className, td.textContent]) : null; })()""")
        self.assertIsNotNone(row, "the changed pair sits on one row")
        self.assertEqual("2", row[0][1])
        self.assertIn("count = items", row[1][1])
        self.assertEqual("2", row[2][1])
        self.assertIn("counter = items", row[3][1])
        self.until("[...document.querySelectorAll('#diff table.split td.tx.add .ix')].some(n => n.textContent === 'counter')",
                   "intra-line marks in split")
        # the toggle is remembered
        self.assertEqual("split", p.evaluate("localStorage.getItem('mokata-ide:diff-mode')"))

    def test_3_a_line_number_cites_its_own_side_and_the_old_path_of_a_rename(self):
        p = self.page
        p.evaluate("window.__copied = []; navigator.clipboard.writeText = (t) => "
                   "{ window.__copied.push(t); return Promise.resolve(); }; 0")
        self.open_change("src/new_name.py")
        p.click("#diff table.split td.no[data-side='old'][data-n='10']")
        p.click("#diff table.split td.no[data-side='new'][data-n='10']")
        self.until("window.__copied.length >= 2", "two cites copied")
        self.assertEqual(["src/old_name.py:10", "src/new_name.py:10"], p.evaluate("window.__copied"))
        self.assertIn("src/new_name.py:10", p.text_content("#dv-live"))

    def test_4_a_drag_selects_one_side_only_and_cites_that_side(self):
        p = self.page
        self.open_change("web/app.js")
        a = p.locator("#diff table.split td.tx[data-side='old']", has_text="function total").first
        b = p.locator("#diff table.split td.tx[data-side='old']", has_text="return count").first
        ba, bb = a.bounding_box(), b.bounding_box()
        p.mouse.move(ba["x"] + 4, ba["y"] + ba["height"] / 2)
        p.mouse.down()
        p.mouse.move(bb["x"] + bb["width"] - 4, bb["y"] + bb["height"] / 2, steps=8)
        p.mouse.up()
        # What a person COPIES is the business rule. `getSelection().toString()` is not that: it is
        # browser-dependent (Chromium 141 includes the other side's cells and the screen-reader
        # spans there), so the test reads the text the copy event actually puts on the clipboard.
        p.evaluate("window.__copied = null; window.addEventListener('copy', "
                   "(e) => { window.__copied = e.clipboardData.getData('text/plain'); })")
        self.press("ControlOrMeta+C")
        self.until("window.__copied !== null", "the copy event")
        sel = p.evaluate("window.__copied")
        self.assertIn("const count", sel)
        self.assertNotIn("counter", sel, "the new side leaked into an old-side copy")
        self.assertNotIn("<img", sel, "the new side leaked into an old-side copy")
        self.assertNotIn("removed:", sel, "screen-reader text leaked into the copy")
        self.assertNotIn("added:", sel, "screen-reader text leaked into the copy")
        self.assertEqual("function total(items) {", sel.split("\n")[0])
        self.assertEqual(4, len(sel.split("\n")), "one line per row of the old side, no other side's")
        self.assertEqual("web/app.js:1-4", p.evaluate("MK.dv.selectionCite()"))
        p.evaluate("window.getSelection().removeAllRanges()")

    def test_5_ignore_whitespace_says_what_it_hid_and_keeps_python_indentation(self):
        p = self.page
        p.focus(".dv-tools .dv-ws")
        self.press("Enter")
        self.text_until("#diff-ws", "hidden", "the whitespace note")
        note = p.text_content("#diff-ws")
        self.assertIn("whitespace-only line change", note)
        self.assertIn("indentation change", note, "the kept Python indentation is said")
        self.assertEqual("true", p.get_attribute(".dv-tools .dv-ws", "aria-pressed"))
        self.until("document.activeElement && document.activeElement.classList.contains('dv-ws')",
                   "focus back on the toggle after the refresh")
        self.open_change("web/app.js")
        self.assertIn("hidden", p.text_content("#diff .dv-note"))
        self.open_change("src/m.py")
        self.assertIn("kept", p.text_content("#diff .dv-note"))
        self.assertEqual(1, p.locator("#diff table.split td.tx.add").count())
        p.click("#diff-ws button")                  # show whitespace again
        self.until("document.getElementById('diff-ws').hidden", "the note to go")
        self.assertEqual("false", p.get_attribute(".dv-tools .dv-ws", "aria-pressed"))

    def test_6_context_expands_in_pages_with_the_right_numbers(self):
        p = self.page
        p.click(".dv-tools [data-mode='unified']")
        self.open_change("src/long.txt")
        gap = "#diff section.hunk .dv-gap"
        p.wait_for_selector(gap)
        first = p.text_content("#diff .dv-gap-n")
        self.assertIn("unchanged lines", first)
        btn = p.locator("#diff .dv-gap button", has_text="above").first
        btn.focus()
        self.press("Enter")
        self.until("document.querySelectorAll('#diff tr.dv-ctx').length >= 20", "20 context rows")
        nums = p.evaluate("[...document.querySelectorAll('#diff tr.dv-ctx')].map(r => "
                          "[r.children[0].textContent, r.children[1].textContent, r.children[2].textContent.slice(0, 10)])")
        # every line 127..146, none skipped although the server answered in two (byte-bounded) pages
        self.assertEqual([[str(n), str(n), " line %d " % n] for n in range(127, 147)], nums)
        # each is longer than LINE_BYTES: shown CUT, and every row says so (never silently)
        cut = p.evaluate("[...document.querySelectorAll('#diff tr.dv-ctx')].filter(r => r.querySelector('.dv-cut'))"
                         ".map(r => [r.children[2].textContent.length, r.querySelector('.dv-cut').textContent])")
        self.assertEqual(20, len(cut))
        self.assertIn("100,009 bytes in all", cut[0][1])
        self.assertLess(cut[0][0], 70000)
        self.assertNotEqual("BODY", p.evaluate("document.activeElement.tagName"), "focus fell to <body>")
        # an impact / outline answer re-draws the file under the reader: focus comes back to the
        # same control in the new drawing, not to <body>
        self.assertEqual("up", p.evaluate("document.activeElement.dataset.act"))
        p.evaluate("MK.showFile(MK.ui.sel, { keepScroll: true }); 0")
        self.until("document.activeElement && document.activeElement.dataset.act === 'up'"
                   " && document.activeElement.isConnected", "focus back on the gap's button")
        self.assertGreaterEqual(p.locator("#diff tr.dv-ctx").count(), 20, "the expansion survives")
        before = p.locator("#diff tr.dv-ctx").count()
        p.locator("#diff section.hunk").last.locator(".dv-gap button", has_text="more").first.click()
        self.until("document.querySelectorAll('#diff tr.dv-ctx').length > %d" % before,
                   "the trailing context page")
        self.assertEqual(21, p.locator("#diff tr.dv-ctx .dv-cut").count(), "line 160 is shown cut too")

    def test_7_a_rewritten_minified_line_is_a_job_and_then_marked(self):
        p = self.page
        statuses = []
        p.on("response", lambda r: statuses.append(r.status) if "/api/diff/intraline" in r.url else None)
        # a fresh page: data/min.js is the first file, so the page drew (and asked for) it at load
        p.reload()
        self.go("review")
        p.wait_for_selector("#nav-list li", state="attached")
        self.open_change("data/min.js")
        self.until("!!document.querySelector('#diff tr.del .ix, #diff tr.add .ix')",
                   "the token marks of the 60,000-token pair", timeout_ms=120000)
        self.assertIn(202, statuses, "a pair over the sync bound must be a visible job")
        self.assertIn(200, statuses, "the finished answer is fetched by naming the job")
        seen = p.evaluate("[...document.querySelectorAll('#diff .job-strip')].map(n => n.textContent).join('|')")
        self.assertNotIn("failed", seen)

    def test_8_the_keyboard_reaches_the_toggles_and_focus_survives(self):
        p = self.page
        p.focus(".dv-tools [data-mode='unified']")
        self.press("Tab")
        self.assertEqual("split", p.evaluate("document.activeElement.dataset.mode"))
        self.press("Space")
        p.wait_for_selector("#diff table.d.split")
        self.assertEqual("split", p.evaluate("document.activeElement.dataset.mode"))
        self.press("Shift+Tab")
        self.press("Enter")
        p.wait_for_selector("#diff table.d:not(.split)")
        self.assertEqual("unified", p.evaluate("document.activeElement.dataset.mode"))


if __name__ == "__main__":
    unittest.main()
