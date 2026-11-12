"""IDE e2e — explorer polish and `path:line:col` launch (0.0.27 P11), driven the way a person does:
the Files panel's buttons and right-click menu, a pasted location in the palette, and the pairing
link `mokata ide path:line:col` opens.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os

import _harness as H


def build(base):
    repo = H._git_repo(base, "explorer-repo", {
        "pkg/core/engine/run.py": "def run():\n    return 1\n",
        "pkg/core/engine/util.py": "UTIL = 1\n",
        "lib/top.py": "a = 1\nb = 2\nc = 3\n",
        "lib/a/w.py": "w = 1\n",
        "lib/a/x/one.py": "one = 1\n",
        "lib/b/v.py": "v = 1\n",
        "lib/b/y/two.py": "two = 1\n",
        "docs/readme.md": "# hi\n",
    })
    H._git(repo, "add", "-A")
    H._git(repo, "commit", "-q", "-m", "fixture")
    return repo


def rows(page):
    return page.eval_on_selector_all("#tree .tree-row", "els => els.map(e => e.textContent)")


class TheExplorerIsPolished(H.IdeFlow):
    FIXTURE = staticmethod(build)

    def _text(self):
        return " | ".join(rows(self.page))

    def test_1_a_chain_of_single_child_folders_is_one_row(self):
        p = self.page
        self.until("!!document.getElementById('qo-open')", "the page")
        self.go("navigate")
        p.wait_for_selector("#tree .tree-row.dir")
        text = self._text()
        self.assertIn("pkg/core/engine", text, "the single-child chain is not one row: " + text)
        self.assertNotIn("| core", text)

    def test_2_expand_all_opens_what_is_listed_and_says_it_goes_one_level_at_a_time(self):
        p = self.page
        p.click("#tree-expand")
        self.text_until("#tree-job", "Opened", "the expand job to finish")
        self.until("document.querySelectorAll('#tree .tree-row.file').length >= 4", "files of the opened folders")
        text = self._text()
        self.assertIn("run.py", text)
        self.assertIn("top.py", text)
        self.assertNotIn("one.py", text, "Expand all walked past the loaded folders")
        self.assertIn("never walks the whole", p.text_content("#tree-job"))

    def test_3_pressing_it_again_goes_one_level_deeper(self):
        self.page.click("#tree-expand")
        self.until("document.querySelector('#tree').textContent.includes('w.py')", "the next level")
        self.assertNotIn("one.py", self._text(), "one press went two levels")
        self.page.click("#tree-expand")
        self.until("document.querySelector('#tree').textContent.includes('one.py')", "the level after")

    def test_4_collapse_all_closes_every_folder(self):
        p = self.page
        p.click("#tree-collapse")
        self.until("document.querySelectorAll('#tree .tree-row.file').length === 0", "every folder closed")
        self.assertGreaterEqual(p.locator("#tree .tree-row.dir").count(), 3)

    def test_5_compact_off_shows_each_folder_of_the_chain(self):
        p = self.page
        p.uncheck("#tree-compact")
        p.locator("#tree .tree-row.dir", has_text="pkg").first.click()
        self.until("document.querySelector('#tree').textContent.includes('core')", "pkg's child folder")
        self.assertNotIn("pkg/core/engine", self._text())
        p.check("#tree-compact")
        self.until("document.querySelector('#tree').textContent.includes('pkg/core/engine')", "the chain again")

    def test_6_right_click_copies_the_relative_and_the_absolute_path(self):
        p = self.page
        p.click("#tree-collapse")
        p.locator("#tree .tree-row.dir", has_text="lib").first.click()
        row = p.locator("#tree .tree-row.file", has_text="top.py").first
        row.wait_for()
        row.click(button="right")
        p.locator(".tab-menu button", has_text="Copy relative path").click()
        self.assertIn("lib/top.py", self.text_until("#tree-job", "lib/top.py", "the relative path"))
        row.click(button="right")
        p.locator(".tab-menu button", has_text="Copy absolute path").click()
        got = self.text_until("#tree-job", "explorer-repo", "the absolute path")
        self.assertIn("lib/top.py", got.replace("\\", "/"))


class AnEditorOpensAtPathLineCol(H.IdeFlow):
    FIXTURE = staticmethod(build)
    EXPECTED_HTTP = (400,)         # a refused location answers 400 on purpose

    def test_1_the_launch_link_opens_the_file_at_the_line_and_column(self):
        p = self.page
        p.goto(self.server.url + "&open=pkg/core/engine/run.py&line=2&col=4")
        self.until("!!(window.MK && MK.nav && MK.nav.path === 'pkg/core/engine/run.py'"
                   " && MK.nav.line === 2 && MK.nav.col === 4)", "run.py at 2:4")
        p.wait_for_selector("#code .cl.here .lt.at-col")
        self.assertEqual("3", p.eval_on_selector("#code .cl.here .lt.at-col",
                                                 "e => e.style.getPropertyValue('--col')"))

    def test_2_a_pasted_location_in_the_palette_opens_there(self):
        p = self.page
        self.press("Control+K")
        p.wait_for_selector("#qo:not([hidden])")
        p.fill("#qo-input", "lib/top.py:3:2")
        row = p.locator("#qo-results .qo-row", has_text="lib/top.py:3:2").first
        row.wait_for()
        self.press("Enter")
        self.until("MK.nav.path === 'lib/top.py' && MK.nav.line === 3 && MK.nav.col === 2", "top.py at 3:2")

    def test_3_an_absolute_path_inside_the_workspace_is_accepted(self):
        p = self.page
        self.press("Control+K")
        p.wait_for_selector("#qo:not([hidden])")
        p.fill("#qo-input", self.repo + "/docs/readme.md:1")
        p.locator("#qo-results .qo-row", has_text="docs/readme.md:1").first.wait_for()
        self.press("Escape")

    def test_4_a_path_outside_the_workspace_is_refused_not_opened(self):
        p = self.page
        self.until("MK.nav.path === 'lib/top.py'", "the previous file")
        for bad in ("/etc/passwd:1", "C:\\x\\y.py:3:1", "../../outside.py:2"):
            self.press("Control+K")
            p.wait_for_selector("#qo:not([hidden])")
            p.fill("#qo-input", bad)
            p.locator("#qo-results .qo-row", has_text="Not opened").first.wait_for()
            self.press("Escape")
        self.assertEqual("lib/top.py", p.evaluate("MK.nav.path"), "a refused path changed the open file")

    def test_5_a_launch_link_that_escapes_opens_nothing(self):
        p = self.page
        p.goto(self.server.url + "&open=../../etc/passwd&line=1")
        self.until("!!(window.MK && MK.nav && MK.ui && MK.ui.restored)", "the page to settle on its saved view")
        self.assertFalse([u for _m, u in self.requests if "passwd" in u and "/api/file" in u],
                         "the escaping target was read")
        self.assertNotIn("passwd", p.evaluate("String(MK.nav.path)"))


def build_wide(base):
    """6 x 6 x 6 folders with 5 files each: 258 folders / 1,290 files, every level wide enough that
    Expand all has a real frontier, small enough for CI."""
    files = {}
    for t in range(6):
        for m in range(6):
            for lf in range(6):
                for f in range(5):
                    files["t%d/m%d/l%d/f%d.py" % (t, m, lf, f)] = "x = %d\n" % f
            for f in range(5):
                files["t%d/m%d/g%d.py" % (t, m, f)] = "x = 1\n"
        for f in range(5):
            files["t%d/h%d.py" % (t, f)] = "x = 1\n"
    files["t0/m0/l0/a_rather_long_directory_name/another_long_directory_name/the_filename_that_must_stay_visible.py"] = "x = 1\n"
    for f in range(100):                    # one folder longer than the painted window: its own row can
        files["t5/m5/l5/n%03d.py" % f] = "x = 1\n"     # be above the window while its files are on screen
    repo = H._git_repo(base, "wide-repo", files)
    H._git(repo, "add", "-A")
    H._git(repo, "commit", "-q", "-m", "fixture")
    return repo


#: every /api/tree answer waits this long: a slow disk, so a job is long enough to act inside.
SLOW_TREE = """() => { const of = window.fetch;
  window.fetch = (u, o) => String(u).includes('/api/tree') ? new Promise((r) => setTimeout(r, %d)).then(() => of(u, o)) : of(u, o); }"""

#: A GATE on the folder listings, so a flow decides when each answer lands instead of racing a
#: timer: every `/api/tree?dir=` request is recorded in `__gate.asked` and HELD until the flow
#: releases it (`__gate.release(dir)`, or `__gate.openAll()`), unless its dir is in `__gate.pass`.
#: A dir in `__gate.fail` fails at once, as a dropped connection does; a path in `__gate.drop` is
#: left out of every answer, as if it had just been deleted on disk.
GATE = """() => { if (window.__gate) return; const of = window.fetch;
  const g = window.__gate = { held: [], asked: [], pass: new Set(), fail: new Set(), drop: new Set(), open: false, inflight: 0 };
  g.release = (d) => { const keep = []; for (const h of g.held) { if (d === undefined || h.dir === d) h.go(); else keep.push(h); } g.held = keep; };
  g.openAll = () => { g.open = true; g.release(); };
  window.fetch = (u, o) => { const s = String(u); if (!s.includes('/api/tree')) return of(u, o);
    const sp = new URL(s, location.href).searchParams; if (sp.has('filter')) return of(u, o);
    const dir = sp.get('dir') || ''; g.asked.push(dir);
    if (g.fail.has(dir)) return Promise.reject(new TypeError('Failed to fetch (gated)'));
    g.inflight++;
    const go = (g.open || g.pass.has(dir)) ? Promise.resolve() : new Promise((r) => g.held.push({ dir, go: r }));
    const answer = () => of(u, o).then((r) => !g.drop.size ? r : r.json().then((j) => {
      j.entries = (j.entries || []).filter((e) => !g.drop.has(e.path));
      return new Response(JSON.stringify(j), { status: r.status, headers: { 'Content-Type': 'application/json' } }); }));
    return go.then(answer).finally(() => { g.inflight--; }); }; }"""


#: Nothing listing: the gate has nothing in flight and the listing pool is empty.
IDLE = ("window.__gate.inflight === 0 && !!MK.nav.pool && MK.nav.pool.running === 0 && "
        "MK.nav.pool.queue.length === 0")


class TheExplorerHoldsAtScale(H.IdeFlow):
    """Expand all on a wide tree: one level per press, the DOM follows the viewport, Cancel stops
    the run at once, and a folder the user opens by hand is served while the run is on."""
    FIXTURE = staticmethod(build_wide)

    def fresh(self):
        """A new page, its saved view restored (that restore reopens folders, so it must have
        landed BEFORE Collapse all), then every folder closed. Waits on state, never on a clock."""
        p = self.page
        p.goto(self.server.url)
        self.until("!!(window.MK && MK.ui && MK.ui.restored)", "the page to restore its saved view")
        self.go("navigate")
        p.wait_for_selector("#tree .tree-row.dir")
        p.click("#tree-collapse")
        self.until("Object.values(MK.nav.collapsed).every((v) => v !== false) && "
                   "document.querySelectorAll('#tree .tree-row.file').length === 0", "every folder closed")

    def scroll_row_to_middle(self, rid):
        """Scroll the panel so the row `rid` sits mid-screen, by its position in the open tree (it
        need not be painted yet), and wait for it to be painted."""
        self.page.evaluate("""(rid) => { const box = document.getElementById('lt-files'), ul = document.getElementById('tree');
            const i = MK.nav.flat.findIndex((r) => r.id === rid);
            const top = ul.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop;
            box.scrollTop = top + i * 27 - box.clientHeight / 2; }""", rid)
        self.until("!!document.querySelector('#tree [data-rid=\"%s\"]')" % rid, "%s painted mid-screen" % rid)

    def row_top(self, rid):
        """The painted row's top on screen, or None when it is not painted."""
        return self.page.evaluate("""(rid) => { const b = [...document.querySelectorAll('#tree [data-rid]')]
            .find((x) => x.dataset.rid === rid); return b && b.isConnected ? b.getBoundingClientRect().top : null; }""", rid)

    def press_and_wait(self):
        runs = self.page.evaluate("MK.nav.expandRuns")
        self.page.click("#tree-expand")
        self.until("MK.nav.expandRuns > %d" % runs, "this press's run to finish")

    def test_1_after_collapse_all_expand_all_opens_one_level_not_every_loaded_one(self):
        p = self.page
        self.fresh()
        for _ in range(3):
            self.press_and_wait()                                   # t*, t*/m*, t*/m*/l* all loaded and open
        self.assertIn("f0.py", p.text_content("#tree"))
        p.click("#tree-collapse")
        self.until("document.querySelectorAll('#tree .tree-row.file').length === 0", "everything closed")
        self.press_and_wait()
        open_keys = sorted(k for k, v in p.evaluate("MK.nav.collapsed").items() if v is False)
        self.assertEqual(["t0", "t1", "t2", "t3", "t4", "t5"], open_keys,
                         "one press after Collapse all opened more than the visible level")
        self.assertEqual(1, p.locator("#tree .tree-row.dir[title='t0/m0']").count(), "the next level is not shown")
        self.assertEqual(0, p.locator("#tree .tree-row.dir[title='t0/m0/l0']").count(), "a second level was opened")

    def test_2_the_dom_follows_the_viewport_not_the_open_tree(self):
        p = self.page
        self.fresh()
        for _ in range(3):
            self.press_and_wait()
        total = p.evaluate("MK.nav.flat.length")
        painted = p.locator("#tree .tree-row").count()
        self.assertGreater(total, 1500, "the fixture did not open into a long tree")
        self.assertLess(painted, 150, "%d rows painted for a %d-row tree" % (painted, total))
        p.evaluate("document.getElementById('lt-files').scrollTop = 20000")
        self.until("document.querySelectorAll('#tree .tree-row').length > 0 && "
                   "document.querySelector('#tree li:first-child').getAttribute('aria-hidden') === 'true'",
                   "the window to follow the scroll")
        self.assertLess(p.locator("#tree .tree-row").count(), 150)

    def test_3_cancel_stops_the_run_and_nothing_is_requested_afterwards(self):
        p = self.page
        self.fresh()
        self.press_and_wait()
        p.evaluate(GATE)
        p.click("#tree-expand")                                      # 36 folders; four are asked, and held
        self.until("window.__gate.held.length === 4", "the run's first four listings")
        p.click("#tree-job button")
        p.evaluate("window.__gate.openAll()")                        # the four already asked may answer now
        self.text_until("#tree-job", "Cancelled after", "the run to say it was cancelled")
        self.until("MK.nav.expandJob === null && window.__gate.inflight === 0", "the run to stop")
        self.assertEqual(4, p.evaluate("window.__gate.asked.length"),
                         "folders were listed after Cancel: %s" % p.evaluate("window.__gate.asked"))
        self.assertIn("the rest stay closed", p.text_content("#tree-job"))

    def test_4_a_folder_opened_by_hand_is_listed_at_once_even_off_the_painted_window(self):
        """M3: a folder the user opens is asked for BY THE CLICK, while an Expand-all run is on,
        whether or not its row is still painted when the tree repaints. (Round 2 asked only for
        painted "loading" rows; the run opens rows above the click, so the folder slid out of the
        window and waited for the whole run: 18.5 s on 68k files.)"""
        p = self.page
        self.fresh()
        self.press_and_wait()
        p.evaluate(GATE)
        p.evaluate("window.__gate.pass.add('t5/m5')")                # only the run's listings are held
        p.click("#tree-expand")
        self.until("window.__gate.held.length === 4", "the run to start (its listings held)")
        p.evaluate("document.getElementById('lt-files').scrollTop = 1e9")
        self.until("!!document.querySelector('#tree [data-rid=\"d|t5/m5\"]')", "t5/m5 painted")
        self.assertTrue(p.evaluate("!MK.nav.dirs['t5/m5'] && !window.__gate.asked.includes('t5/m5')"),
                        "t5/m5 was listed before the click: the click cannot be what serves it")
        # The click lands on t5/m5; by the time the tree repaints, the window is elsewhere (what a
        # run opening folders above it does), so neither its row nor its "loading" row is painted.
        p.evaluate("""() => { const b = document.querySelector('#tree [data-rid="d|t5/m5"]');
            document.getElementById('lt-files').scrollTop = 0; window.__t0 = performance.now(); b.click(); }""")
        # 0.0.28 D — the race this used to lose on Linux Firefox: fresh()'s restore re-lists the
        # folders the PREVIOUS test left open, and the run counts a folder whose listing is already
        # here as done without asking, so `expandJob.done` at this point was however many restored
        # listings had landed (18 of 36 in one run), not 0. What decides "served by the click" is
        # that every one of the run's four workers is still blocked on its own held listing when
        # t5/m5's listing lands (no worker was free to reach it), and that it was asked once.
        self.until("!!MK.nav.dirs['t5/m5']", "the hand-opened folder's listing")
        ms = p.evaluate("performance.now() - window.__t0")
        self.assertEqual([True, 4], p.evaluate("[!!MK.nav.expandJob, window.__gate.held.length]"),
                         "the hand-opened folder was served by the run, not by the click")
        self.assertEqual(1, p.evaluate("window.__gate.asked.filter((d) => d === 't5/m5').length"),
                         "the folder was asked for more than once (%.0f ms)" % ms)
        p.evaluate("window.__gate.openAll()")
        self.text_until("#tree-job", "Opened 36", "the run to finish")

    def test_4b_the_run_never_pushes_the_folder_the_user_opened_out_of_view(self):
        """M3: while Expand all opens folders ABOVE the one the user just opened, that folder stays
        where it is on screen; the rows the run adds go above the viewport, not under the pointer."""
        p = self.page
        self.fresh()
        self.press_and_wait()
        p.evaluate(GATE)
        p.evaluate("window.__gate.pass.add('t4/m2')")
        p.click("#tree-expand")
        self.until("window.__gate.held.length === 4", "the run to start (its listings held)")
        self.scroll_row_to_middle("d|t4/m2")
        self.until("!!document.querySelector('#tree [data-rid=\"d|t4/m2\"]')", "t4/m2 painted")
        p.locator("#tree [data-rid='d|t4/m2']").click()
        self.until("!!MK.nav.dirs['t4/m2'] && !!document.querySelector('#tree [data-rid=\"f|t4/m2/g0.py\"]')",
                   "the hand-opened folder's files")
        before = self.row_top("d|t4/m2")
        p.evaluate("window.__gate.openAll()")                        # 28 folders above it open now
        self.text_until("#tree-job", "Opened 36", "the run to finish")
        after = self.row_top("d|t4/m2")
        self.assertIsNotNone(after, "the folder the user opened was pushed out of the painted window")
        self.assertLess(abs(after - before), 2, "the folder moved %.0f px on screen while the run opened folders above it" % (after - before))


    # ---- verify2 round: focus, filter rows, refresh burst, a11y, the Cancel button --------------

    def open_levels(self, n=3):
        self.fresh()
        for _ in range(n):
            self.press_and_wait()

    def test_5_keyboard_focus_stays_on_its_row_while_the_window_shifts(self):
        p = self.page
        self.open_levels()
        self.scroll_row_to_middle("d|t0/m2")
        p.locator("#tree .tree-row.dir[title='t0/m2']").focus()
        rid = p.evaluate("document.activeElement.dataset.rid")
        p.evaluate("document.getElementById('lt-files').scrollTop += 54")     # two rows, the row stays on screen
        self.until("document.activeElement && document.activeElement.dataset.rid === %r && document.contains(document.activeElement)" % rid,
                   "focus to stay on the same row while the window shifts")

    def test_6_tabbing_and_arrows_never_fall_out_of_the_tree(self):
        p = self.page
        self.open_levels()
        self.assertEqual(1, p.evaluate("document.querySelectorAll('#tree [tabindex=\"0\"]').length"),
                         "the tree must be ONE tab stop (roving tabindex), or Tab walks every painted row")
        p.locator("#tree .tree-row.dir").first.focus()
        for i in range(90):                                          # more rows than one window holds
            self.press("ArrowDown")
            inside = p.evaluate("!!document.activeElement && !!document.activeElement.closest('#tree')")
            self.assertTrue(inside, "focus fell out of the tree at ArrowDown #%d" % (i + 1))
        self.assertGreater(p.evaluate("document.getElementById('lt-files').scrollTop"), 0, "the window did not follow the focus")
        p.evaluate("document.getElementById('lt-files').scrollTop = 0")     # the focused row scrolls out
        self.until("!!document.activeElement && !!document.activeElement.closest('#tree')", "focus to stay in the tree")
        self.press("ArrowDown")
        self.assertTrue(p.evaluate("!!document.activeElement.closest('#tree')"))

    def test_7_the_rows_say_how_many_there_really_are(self):
        p = self.page
        self.open_levels()
        total = p.evaluate("MK.nav.flat.length")
        got = p.evaluate("[...document.querySelectorAll('#tree > li:not([aria-hidden])')].map(li => [li.getAttribute('aria-setsize'), li.getAttribute('aria-posinset')])")
        self.assertTrue(got)
        self.assertTrue(all(int(a) == total for a, _b in got), "aria-setsize is not the real row count")
        pos = [int(b) for _a, b in got]
        self.assertEqual(pos, list(range(pos[0], pos[0] + len(pos))))

    def test_8_a_filter_result_shows_the_whole_path_not_a_clipped_one(self):
        p = self.page
        self.fresh()
        p.fill("#tree-filter", "must_stay_visible")
        self.until("[...document.querySelectorAll('#tree .tree-row.file')].some(e => e.textContent.includes('must_stay_visible'))", "the filter result")
        clipped = p.evaluate("[...document.querySelectorAll('#tree .tree-row.file')].filter(e => e.scrollWidth > e.clientWidth + 1).length")
        self.assertEqual(0, clipped, "filter rows are clipped: the filename at the end of the path is hidden")
        p.fill("#tree-filter", "")

    def test_9_a_refresh_relists_only_what_is_painted_through_a_bounded_visible_job(self):
        """F2: a refresh with the tree wide open re-lists what is on screen, at most four at a
        time, as a visible job with Cancel — never every open folder at once."""
        p = self.page
        self.open_levels()
        want = p.evaluate("document.querySelectorAll('#tree .tree-row.dir[aria-expanded=true]').length")
        p.evaluate(GATE)
        p.evaluate("window.__gate.pass.add('')")
        p.evaluate("() => { window.__r = MK.loadTree(true); }")
        p.wait_for_selector("#tree-job button")                      # a visible job, with Cancel
        self.assertIn("folders", p.text_content("#tree-job"))
        self.until("window.__gate.held.length === 4", "the pool to fill")
        self.assertEqual(4, p.evaluate("window.__gate.inflight"), "the refresh is not bounded: more than four listings in flight")
        p.evaluate("window.__gate.openAll()")
        self.until(IDLE, "the refresh to finish")
        asked = p.evaluate("window.__gate.asked.filter((d) => d !== '').length")
        self.assertLessEqual(asked, want, "the refresh listed folders that are not on screen (%d asked, %d open on screen)" % (asked, want))
        self.assertEqual(0, p.evaluate("Object.keys(MK.nav.dirErr).length"), "a refresh left a failed listing")
        self.assertEqual(0, p.locator("#tree .tree-row.more", has_text="retry").count())

    def test_10_cancelling_a_refresh_leaves_visible_retry_rows_not_silent_stale_ones(self):
        """A cancelled refresh leaves a retry row for every folder it did not re-list, and a retry
        re-lists THAT folder (and only it). Deterministic: the listings are gated, so how many are
        cancelled is decided by the flow, not by a race with a timer (R2-4)."""
        p = self.page
        self.open_levels()
        cancelled = "Object.values(MK.nav.dirErr).filter((e) => e && e.error === 'cancelled').length"
        p.evaluate(GATE)
        p.evaluate("window.__gate.pass.add('')")                     # the root answers; every folder is held
        p.evaluate("() => { window.__r = MK.loadTree(true); }")
        p.wait_for_selector("#tree-job button")                      # more waiting than the pool runs at once
        self.until("window.__gate.held.length === 4", "the pool's four listings")
        p.click("#tree-job button")
        self.text_until("#tree-job", "Cancelled", "the refresh to say it was cancelled")
        n = p.evaluate(cancelled)
        self.assertGreater(n, 1, "the construction must cancel more than one listing")
        retry = p.locator("#tree .tree-row.more", has_text="not refreshed (cancelled)")
        self.assertGreater(retry.count(), 0, "a cancelled listing shows no retry row")
        rid = retry.first.get_attribute("data-rid")
        d = rid[2:]
        # the pool is still busy with the four listings asked before Cancel (held): a retry is the
        # user's own click, so it is served at once, not queued behind them
        p.evaluate("(d) => window.__gate.pass.add(d)", d)
        retry.first.click()
        self.until("!!MK.nav.dirs[%r] && !MK.nav.dirErr[%r]" % (d, d), "the retried folder to be listed")
        self.assertEqual(4, p.evaluate("window.__gate.held.length"), "the retry waited for the pool")
        p.evaluate("window.__gate.openAll()")                        # the four already asked land
        self.until(IDLE, "the four in flight to land")
        self.assertEqual(n - 1, p.evaluate(cancelled), "a retry re-listed more (or less) than its own folder")

    def test_11_the_cancel_button_is_the_same_element_while_the_count_ticks(self):
        p = self.page
        self.fresh()
        self.press_and_wait()
        p.evaluate(GATE)
        p.click("#tree-expand")
        self.until("window.__gate.held.length === 4", "the run's first four listings")
        p.evaluate("() => { window.__btn = document.querySelector('#tree-job button'); window.__t0 = document.getElementById('tree-job').textContent; }")
        p.evaluate("window.__gate.release(window.__gate.held[0].dir)")      # one folder lands: the count ticks
        self.until("document.getElementById('tree-job').textContent !== window.__t0", "the count to tick")
        self.assertTrue(p.evaluate("document.querySelector('#tree-job button') === window.__btn"),
                        "the Cancel button was rebuilt while the count ticked (a click that starts on it is lost)")
        self.assertTrue(p.evaluate("document.querySelector('#tree-job').firstElementChild === window.__btn"), "the button moved")
        p.click("#tree-job button")
        p.evaluate("window.__gate.openAll()")
        self.text_until("#tree-job", "Cancelled", "cancel")
        self.until("MK.nav.expandJob === null && window.__gate.inflight === 0", "the run to stop")

    # ---- round 3: a refresh reaches what is on screen, stale is never shown as fresh, Enter keeps focus

    def scroll_to_the_end(self):
        p = self.page
        p.evaluate("document.getElementById('lt-files').scrollTop = 1e9")
        self.until("!!document.querySelector('#tree [data-rid=\"f|t5/m5/l5/n098.py\"]') && "
                   "!document.querySelector('#tree [data-rid=\"d|t5/m5/l5\"]')",
                   "t5/m5/l5's files on screen with its own row above the painted window")

    def test_12_a_refresh_relists_a_folder_whose_files_are_on_screen_though_its_row_is_above(self):
        """R2-2: the user scrolled into the middle of a long folder (its row is far above), a file
        in it was deleted and one was added: the refresh re-lists that folder, because its FILES are
        on screen. Round 2 re-asked a folder only when its own row was painted, so the deleted file
        stayed listed and the new one never appeared, with nothing saying so."""
        p = self.page
        self.open_levels()
        self.scroll_to_the_end()
        d = os.path.join(self.repo, "t5", "m5", "l5")
        os.remove(os.path.join(d, "n099.py"))
        with open(os.path.join(d, "n100_new.py"), "w", encoding="utf-8") as fh:
            fh.write("x = 1\n")
        H._git(self.repo, "add", "-A")
        p.evaluate("() => { window.__r = MK.loadTree(true); }")
        self.until("!!document.querySelector('#tree [data-rid=\"f|t5/m5/l5/n100_new.py\"]')",
                   "the new file, in a folder whose files are on screen, to appear")
        self.assertEqual(0, p.locator("#tree [data-rid='f|t5/m5/l5/n099.py']").count(), "the deleted file is still listed")
        self.assertFalse(p.evaluate("MK.nav.stale.has('t5/m5/l5')"), "the folder on screen is still stale")

    def test_13_rows_waiting_for_their_refresh_are_marked_stale_never_shown_as_fresh(self):
        """F2: while a refresh has not re-listed a folder, its rows on screen say so (`.stale`),
        and the mark goes when the fresh listing lands."""
        p = self.page
        self.open_levels()
        self.scroll_to_the_end()
        p.evaluate(GATE)
        p.evaluate("window.__gate.pass.add('')")
        p.evaluate("() => { window.__r = MK.loadTree(true); }")
        self.until("window.__gate.asked.includes('t5/m5/l5')", "the refresh to ask for the folder on screen")
        unmarked = p.evaluate("[...document.querySelectorAll('#tree .tree-row[title^=\"t5/m5/l5/\"]')].filter((b) => !b.classList.contains('stale')).length")
        marked = p.locator("#tree .tree-row.stale[title^='t5/m5/l5/']").count()
        self.assertGreater(marked, 0, "rows waiting for their refresh carry no stale mark")
        self.assertEqual(0, unmarked, "rows waiting for their refresh are shown as fresh")
        p.evaluate("window.__gate.openAll()")
        self.until(IDLE + " && !MK.nav.stale.has('t5/m5/l5') && "
                   "document.querySelectorAll('#tree .tree-row.stale').length === 0", "the stale mark to go once re-listed")

    def focused(self):
        return self.page.evaluate("""() => { const a = document.activeElement;
            return { rid: a && a.dataset ? a.dataset.rid || null : null, inTree: !!(a && a.closest && a.closest('#tree')),
                     visible: !!(a && a.matches(':focus-visible') && getComputedStyle(a).outlineStyle !== 'none'
                                 && parseFloat(getComputedStyle(a).outlineWidth) > 0) }; }""")

    def test_14_enter_on_a_folder_or_a_file_keeps_focus_on_its_row_and_a_second_enter_works(self):
        """R2-3 / F1: Enter on a folder toggles it, Enter on a file opens it, and either way the
        keyboard focus stays on that row with a visible outline, so the next Enter acts again."""
        p = self.page
        self.open_levels(1)
        p.locator("#tree [tabindex='0']").focus()
        self.press("ArrowDown")
        self.press("ArrowUp")                                        # a keyboard user, on t0
        self.assertEqual("d|t0", self.focused()["rid"])
        self.press("Enter")
        self.until("MK.nav.collapsed['t0'] === true", "Enter to close t0")
        f = self.focused()
        self.assertEqual("d|t0", f["rid"], "focus left the folder row after Enter: %s" % f)
        self.assertTrue(f["visible"], "the focused row shows no focus outline")
        self.press("Enter")
        self.until("MK.nav.collapsed['t0'] === false", "a second Enter to open t0 again")
        self.assertEqual("d|t0", self.focused()["rid"])
        while self.focused()["rid"] != "f|t0/h0.py":
            self.press("ArrowDown")
        self.press("Enter")
        self.until("MK.nav.path === 't0/h0.py'", "Enter to open the file")
        f = self.focused()
        self.assertEqual("f|t0/h0.py", f["rid"], "focus left the file row after Enter: %s" % f)
        self.assertTrue(f["visible"], "the focused file row shows no focus outline")
        self.press("ArrowDown")
        self.press("Enter")
        self.until("MK.nav.path === 't0/h1.py'", "the next Enter to open the next file")
        self.assertEqual("f|t0/h1.py", self.focused()["rid"])

    def test_15_a_taller_window_paints_the_rows_it_now_shows_without_a_scroll(self):
        """The painted window follows the viewport's SIZE too: after the window grows (or the page
        is zoomed out), every row now on screen is painted, with no scroll needed to fill holes."""
        p = self.page
        self.open_levels(2)
        p.set_viewport_size({"width": 1400, "height": 400})
        self.until("document.querySelectorAll('#tree .tree-row').length < 70", "a short window")
        try:
            p.set_viewport_size({"width": 1400, "height": 2400})
            self.until("""(() => { const box = document.getElementById('lt-files').getBoundingClientRect();
                const rows = [...document.querySelectorAll('#tree .tree-row')].map((b) => b.getBoundingClientRect());
                const low = Math.max(...rows.map((r) => r.bottom));
                return low >= Math.min(box.bottom, box.top + MK.nav.flat.length * 27) - 27; })()""",
                       "the rows the taller window shows to be painted")
        finally:
            p.set_viewport_size({"width": 1400, "height": 900})

    # ---- round 4: a refresh that fails, honest job counts, a tree that shrinks under the scroll

    def test_16_a_refresh_that_fails_shows_a_retry_row_never_the_old_rows_as_fresh(self):
        """F2 / R3-2: when a folder's refresh fails, its old listing goes and a retry row says so.
        Keeping the old rows would show them as current (they lose their stale mark) and no retry
        row would ever appear."""
        p = self.page
        self.open_levels(2)
        p.evaluate(GATE)
        p.evaluate("() => { window.__gate.open = true; window.__gate.fail.add('t0/m0'); }")
        p.evaluate("async () => { await MK.loadTree(true); }")
        self.until(IDLE + " && !!MK.nav.dirErr['t0/m0']", "t0/m0's refresh to fail")
        self.assertIsNotNone(self.row_top("x|t0/m0"), "a failed refresh shows no retry row")
        self.assertIn("could not list", p.locator("#tree [data-rid='x|t0/m0']").text_content())
        self.assertEqual(0, p.locator("#tree [data-rid='d|t0/m0/l0']").count(), "the failed folder's old rows are still shown")
        self.assertFalse(p.evaluate("'t0/m0' in MK.nav.dirs"), "the failed folder's old listing is kept")
        self.assertFalse(p.evaluate("MK.nav.stale.has('t0/m0')"), "the failed folder is still counted as merely stale")
        self.until(IDLE + " && document.querySelectorAll('#tree .tree-row.stale').length === 0", "the rest of the refresh to land")

    def test_17_a_refresh_over_a_refresh_counts_only_its_own_folders(self):
        """Visible long work: "Listing folders… n of m" is true. A second refresh drops what the first
        had queued, so m counts the folders still to list, never ones nobody will list."""
        p = self.page
        self.open_levels()
        p.evaluate(GATE)
        p.evaluate("window.__gate.pass.add('')")
        p.evaluate("async () => { await MK.loadTree(true); }")
        self.until("window.__gate.held.length === 4 && MK.nav.pool.queue.length > 0", "the first refresh's queue")
        p.evaluate("async () => { await MK.loadTree(true); }")
        self.until("window.__gate.held.length === 4 && MK.nav.pool.queue.length > 0", "the second refresh's queue")
        pool = p.evaluate("({ total: MK.nav.pool.total, done: MK.nav.pool.done, running: MK.nav.pool.running, queued: MK.nav.pool.queue.length })")
        self.assertEqual(pool["done"] + pool["running"] + pool["queued"], pool["total"], "the job counts folders nobody will list: %s" % pool)
        self.assertIn("of %d" % pool["total"], p.text_content("#tree-job"))
        p.evaluate("window.__gate.openAll()")
        self.until(IDLE, "the refresh to finish")

    def test_18_a_tree_that_shrinks_under_the_scroll_still_shows_rows(self):
        """R3-1: the user is at the end of a long open tree and a refresh drops a big subtree (a
        checkout, a delete). The panel shows the end of the shorter tree, never a blank panel of
        empty padding. (The gate answers the refresh as if t4 and t5 were deleted on disk, so the
        flow does not race the file watcher.)"""
        p = self.page
        self.open_levels()
        p.evaluate("document.getElementById('lt-files').scrollTop = 1e9")
        self.until("!!document.querySelector('#tree [data-rid=\"f|t5/h4.py\"]')", "the end of the tree")
        p.evaluate(GATE)
        p.evaluate("() => { window.__gate.open = true; window.__gate.drop.add('t4'); window.__gate.drop.add('t5'); }")
        # the render that drops them must paint the end of the shorter tree itself: blank padding
        # that the browser's scroll clamping eats away frame by frame is the bug (68k: it stays blank)
        seen = p.evaluate("""async () => { await MK.loadTree(true);
            const box = document.getElementById('lt-files').getBoundingClientRect();
            return { gone: !MK.nav.flat.some((r) => r.id.startsWith('d|t5')),
                     visible: [...document.querySelectorAll('#tree .tree-row')].filter((b) => { const r = b.getBoundingClientRect();
                       return r.bottom > box.top && r.top < box.bottom; }).length }; }""")
        self.assertTrue(seen["gone"], "the construction must drop t4 and t5")
        self.assertGreater(seen["visible"], 0, "the panel went blank when the tree shrank under the scroll")
        last = p.evaluate("MK.nav.flat[MK.nav.flat.length - 1].id")
        self.assertIsNotNone(self.row_top(last), "the end of the shorter tree is not shown")

