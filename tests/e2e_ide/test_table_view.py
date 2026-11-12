"""IDE e2e — the CSV table view and the table diff (0.0.27 P8, ID-CSV-VIEW).

A .csv opens in the Code view as a table (sticky header, virtual rows paged from the server); a cell
holding markup or a formula is shown as TEXT, and the real Copy and Export buttons neutralise it;
Sort and Filter are server jobs over the WHOLE file (not the loaded rows); a 300,000-row file is
counted as a visible job and its last row is reachable; a
UTF-16 file is decoded and a UTF-32 one is refused in plain words; a changed CSV gets a Table diff in
the review screen — by position and by key — with the keyed diff's memory said out loud.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import _harness as H

BIG_ROWS = 300000
HUGE_ROWS = 6000000
PEOPLE_OLD = "id,name,score\n1,Ann,10\n2,Bob,20\n3,Cy,30\n"
PEOPLE_NEW = ('id,name,score\n1,Ann,10\n2,Bob,25\n4,"<img src=x onerror=window.__pwned=1>",=1+1\n'
              '5,Eve,-3\n')


def build(base):
    repo = H.make_repo("shop", base)
    H._write(repo, "data/people.csv", PEOPLE_OLD)
    H._write(repo, "data/big.csv", "id,name,score\n" + "".join(
        "%d,name%d,%d\n" % (i, i, i % 97) for i in range(BIG_ROWS)))
    H._write(repo, "data/mac.csv", "a,b\r1,x\r2,y\r")
    with open(repo + "/data/wide.csv", "wb") as fh:
        fh.write(b"\xff\xfe" + "id,name\r\n1,Zoë\r\n2,日本\r\n".encode("utf-16-le"))
    with open(repo + "/data/utf32.csv", "wb") as fh:
        fh.write(b"\xff\xfe\x00\x00" + "a,b\n".encode("utf-32-le"))
    with open(repo + "/data/rag.csv", "wb") as fh:
        fh.write(b"a,b\n1\n2,caf\xe9\n3,4,5\n")
    H._write(repo, "data/dups.csv", "k,v\nA,1\nA,2\nB,3\n")
    # a count that takes seconds on any machine (~2.7 s locally): long enough to Cancel for real
    H._write(repo, "data/huge.csv", "a,b\n" + "1,x\n" * HUGE_ROWS)
    H._git(repo, "add", "-A")
    H._git(repo, "commit", "-q", "-m", "data")
    H._write(repo, "data/people.csv", PEOPLE_NEW)
    H._write(repo, "data/dups.csv", "k,v\nA,1\nA,9\nB,3\n")
    return repo


class TheTableViewWorks(H.IdeFlow):
    FIXTURE = staticmethod(build)

    def cells(self, row=1):
        return self.page.evaluate(
            "(i) => [...document.querySelectorAll('#tb-code .tb-body .tb-row')][i]"
            ".querySelectorAll('.tb-td') && [...document.querySelectorAll('#tb-code .tb-body .tb-row')][i]"
            ".querySelectorAll('.tb-td').length ? [...[...document.querySelectorAll('#tb-code .tb-body .tb-row')][i]"
            ".querySelectorAll('.tb-td')].map(n => n.textContent) : []", row)

    def test_1_a_csv_opens_as_a_table_and_markup_and_formulas_stay_text(self):
        p = self.page
        self.open_file("data/people.csv")
        p.wait_for_selector("#tb-code .tb-row .tb-td")
        self.assertEqual(["id", "name", "score"], p.evaluate(
            "[...document.querySelectorAll('#tb-code .tb-head .tb-sort')].map(n => n.textContent)"))
        self.text_until("#tb-code .tb-meta", "4 rows", "the row count")
        self.until("document.querySelectorAll('#tb-code .tb-body .tb-row .tb-td').length >= 12",
                   "four rows of cells")
        text = p.evaluate("[...document.querySelectorAll('#tb-code .tb-body .tb-td')].map(n => n.textContent)")
        self.assertIn("<img src=x onerror=window.__pwned=1>", text, "markup shows as text")
        self.assertIn("=1+1", text, "a formula shows literally")
        self.assertEqual(0, p.locator("#tb-code img, #tb-code script").count())
        self.assertIsNone(p.evaluate("window.__pwned"))

    def test_2_the_real_copy_and_export_buttons_neutralise_a_formula(self):
        p = self.page
        p.evaluate("window.__copied = []; navigator.clipboard.writeText = (t) => { window.__copied.push(t); return Promise.resolve(); }")
        with p.expect_response("**/api/table/export") as ri:
            p.click("#tb-code .tb-bar button:has-text('Copy')")
        self.assertEqual(200, ri.value.status)
        self.until("window.__copied.some(t => typeof t === 'string' && t.startsWith('id\\tname'))", "the copied text")
        copied = p.evaluate("window.__copied.find(t => typeof t === 'string' && t.startsWith('id\\tname'))")
        self.assertIn("'=1+1", copied, "Copy must neutralise the formula cell")
        self.assertNotIn("\t=1+1", copied)
        self.assertIn("\t-3", copied, "a plain negative number is left alone")
        with p.expect_download() as dl:
            p.click("#tb-code .tb-bar button:has-text('Export loaded')")
        with open(dl.value.path(), encoding="utf-8") as fh:
            exported = fh.read()
        self.assertIn("'=1+1", exported, "Export must neutralise the formula cell")
        self.assertNotIn(",=1+1", exported)
        out = p.evaluate("MK.table.exportText([['=1+1', '-3', 'a,b']], 'csv')")
        self.assertEqual("'=1+1,-3,\"a,b\"\n", out)

    def test_2b_sort_is_a_server_job_over_the_whole_file_both_directions(self):
        p = self.page
        p.click("#tb-code .tb-head .tb-th:nth-of-type(4) .tb-sort")     # score, ascending
        self.text_until("#tb-code .tb-note", "over the whole file", "the sort note")
        self.assertIn("Sorted by score ascending", p.text_content("#tb-code .tb-note"))
        first = p.evaluate("document.querySelector('#tb-code .tb-body .tb-row .tb-td').textContent")
        self.assertEqual("5", first, "-3 sorts first")
        # PINS-UI (P8 M21): the gutter says the row's place in the FILE, not in the sorted view
        self.assertEqual("4", p.evaluate("document.querySelector('#tb-code .tb-body .tb-row .tb-gut').textContent"),
                         "Eve is the file's 4th row; the sorted view's 1st row must not read as row 1")
        self.assertEqual("ascending", p.get_attribute("#tb-code .tb-head .tb-th:nth-of-type(4)", "aria-sort"))
        p.click("#tb-code .tb-head .tb-th:nth-of-type(4) .tb-sort")     # descending
        self.text_until("#tb-code .tb-note", "descending", "the descending note")
        self.until("document.querySelector('#tb-code .tb-body .tb-row .tb-td') && "
                   "document.querySelector('#tb-code .tb-body .tb-row .tb-td').textContent === '2'",
                   "25 sorts first when descending")
        self.assertEqual("descending", p.get_attribute("#tb-code .tb-head .tb-th:nth-of-type(4)", "aria-sort"))
        p.click("#tb-code .tb-head .tb-th:nth-of-type(4) .tb-sort")     # back to file order
        self.until("document.querySelector('#tb-code .tb-body .tb-row .tb-td') && "
                   "document.querySelector('#tb-code .tb-body .tb-row .tb-td').textContent === '1'",
                   "file order again")

    def test_2c_a_number_filter_says_how_many_cells_were_not_numbers(self):
        """PINS-UI (P8 M18): `score < 100` cannot compare the formula cell `=1+1`. The note says so,
        so "3 of 4 rows match" never reads as "the fourth row is 100 or more"."""
        p = self.page
        p.select_option("#tb-code .tb-filter select[aria-label='filter column']", "2")
        p.select_option("#tb-code .tb-filter select[aria-label='filter operator']", "lt")
        p.fill("#tb-code .tb-fval", "100")
        p.click("#tb-code .tb-filter button[type=submit]")
        self.text_until("#tb-code .tb-note", "rows match", "the filter's note")
        note = p.text_content("#tb-code .tb-note")
        self.assertIn("3 of 4 rows match", note)
        self.assertIn("1 cells were not numbers and did not match", note)
        p.click("#tb-code .tb-filter button:has-text('Clear')")
        self.until("document.querySelector('#tb-code .tb-meta').textContent.includes('4 rows')"
                   " && !document.querySelector('#tb-code .tb-meta').textContent.includes('of 4')", "file order")

    def test_2d_a_page_of_a_replaced_view_is_never_painted_into_the_new_one(self):
        """PINS-UI (P8 M19). Sort ascending, and hold that view's first page; sort descending
        before it lands. The late ascending page is dropped and the descending view's own rows are
        shown (they were never asked for while the old page was pending, so dropping alone left
        the table on "loading…" for good)."""
        p = self.page
        held, gate = [], {"on": True}

        def rows(route):
            if gate["on"]:
                held.append(route)
            else:
                route.continue_()
        p.route("**/api/table/view/rows*", rows)
        try:
            p.click("#tb-code .tb-head .tb-th:nth-of-type(4) .tb-sort")             # ascending
            self.text_until("#tb-code .tb-note", "ascending", "the ascending view")
            self.until("document.querySelector('#tb-code .tb-body .tb-td.dim') !== null", "its rows waiting")
            for _ in range(200):
                if held:
                    break
                p.wait_for_timeout(50)          # pumps the route handler; ends on state, not time
            self.assertEqual(1, len(held), "the ascending view's first page is held")
            gate["on"] = False
            p.click("#tb-code .tb-head .tb-th:nth-of-type(4) .tb-sort")             # descending
            self.text_until("#tb-code .tb-note", "descending", "the descending view")
            held[0].continue_()                                                      # the late page lands
            self.until("(() => { const r = document.querySelector('#tb-code .tb-body .tb-row');"
                       " return !!r && !r.querySelector('.tb-td.dim') && r.querySelector('.tb-td').textContent === '2'; })()",
                       "the descending view's own first row (Bob, 25)")
            col = p.evaluate("[...document.querySelectorAll('#tb-code .tb-body .tb-row')].map(r => r.querySelectorAll('.tb-td')[2].textContent)")
            self.assertEqual(["25", "10", "-3", "=1+1"], col, "descending, numbers first, never the ascending page")
        finally:
            p.unroute("**/api/table/view/rows*", rows)
        p.click("#tb-code .tb-head .tb-th:nth-of-type(4) .tb-sort")                 # back to file order
        self.until("document.querySelector('#tb-code .tb-body .tb-row .tb-td') && "
                   "document.querySelector('#tb-code .tb-body .tb-row .tb-td').textContent === '1'", "file order")

    def test_3_the_raw_text_toggle_and_back(self):
        p = self.page
        p.click("#tb-toggle")
        self.until("document.getElementById('tb-code').hidden", "the table to hide")
        self.assertIn("id,name,score", p.text_content("#code"))
        p.click("#tb-toggle")
        p.wait_for_selector("#tb-code .tb-row .tb-td")

    def test_4_a_300k_row_file_is_counted_as_a_job_and_its_end_is_reachable(self):
        p = self.page
        self.open_file("data/big.csv")
        p.wait_for_selector("#tb-code .tb-row .tb-td")
        self.text_until("#tb-code .tb-meta", "300,000 rows", "the counted total")
        self.assertNotIn("counting", p.text_content("#tb-code .tb-meta"))
        self.assertLess(p.evaluate("document.querySelectorAll('#tb-code .tb-row').length"), 80,
                        "only the viewport's rows are in the page, at the top too")
        p.evaluate("document.querySelector('#tb-code .tb-scroll').scrollTop = 1e12")
        self.until("[...document.querySelectorAll('#tb-code .tb-body .tb-td')]"
                   ".some(n => n.textContent === 'name299999')", "the last row")
        self.assertLess(p.evaluate("document.querySelectorAll('#tb-code .tb-row').length"), 80,
                        "only the viewport's rows are in the page")

    def test_4b_a_filter_and_a_sort_scan_the_whole_300k_file_and_say_so(self):
        p = self.page
        want = sum(1 for i in range(BIG_ROWS) if i % 97 == 96)
        p.select_option("#tb-code .tb-filter select[aria-label='filter column']", "2")
        p.select_option("#tb-code .tb-filter select[aria-label='filter operator']", "equals")
        p.fill("#tb-code .tb-fval", "96")
        p.click("#tb-code .tb-filter button[type=submit]")
        self.text_until("#tb-code .tb-note", "rows match", "the filter's note")
        self.assertIn("%s of %s rows match" % ("{:,}".format(want), "{:,}".format(BIG_ROWS)),
                      p.text_content("#tb-code .tb-note"))
        self.assertIn("{:,} of {:,} rows".format(want, BIG_ROWS), p.text_content("#tb-code .tb-meta"))
        first = p.evaluate("[...document.querySelectorAll('#tb-code .tb-body .tb-row')][0]"
                           ".querySelectorAll('.tb-td')[1].textContent")
        self.assertEqual("name96", first)
        self.assertEqual(want * 26, p.evaluate("parseInt(document.querySelector('#tb-code .tb-body').style.height)"),
                         "the scroll height is the MATCHES, not the file")
        p.evaluate("document.querySelector('#tb-code .tb-scroll').scrollTop = 1e12")
        self.until("[...document.querySelectorAll('#tb-code .tb-body .tb-td')].some(n => n.textContent === '96')"
                   " && document.querySelectorAll('#tb-code .tb-body .tb-row').length > 3", "the last match")
        # a filter on ONE column: id == 96 is one row (an any-column filter would find thousands)
        p.select_option("#tb-code .tb-filter select[aria-label='filter column']", "0")
        p.fill("#tb-code .tb-fval", "96")
        p.click("#tb-code .tb-filter button[type=submit]")
        self.text_until("#tb-code .tb-note", "1 of 300,000 rows match", "the one-column filter")
        p.select_option("#tb-code .tb-filter select[aria-label='filter column']", "2")
        p.fill("#tb-code .tb-fval", "96")
        p.click("#tb-code .tb-filter button[type=submit]")
        self.text_until("#tb-code .tb-note", "{:,} of".format(want), "the filter restored")
        # keyboard focus survives a rebuild (the header is redrawn): Tab/Enter keep working
        p.focus("#tb-code .tb-head .tb-th:nth-of-type(3) .tb-sort")
        p.keyboard.press("Enter")
        self.text_until("#tb-code .tb-note", "Sorted by name ascending", "a keyboard sort")
        self.until("document.activeElement && document.activeElement.classList.contains('tb-sort')", "focus kept on the header")
        # sort the filtered view too (name, descending) — still the whole file's matches
        p.click("#tb-code .tb-bar ~ .tb-filter button:has-text('Clear')")
        self.until("document.querySelector('#tb-code .tb-meta').textContent.includes('300,000 rows')", "the unfiltered file")
        # an unfiltered sort of all 300k rows by score: first row has score 0, last has 96
        p.click("#tb-code .tb-head .tb-th:nth-of-type(4) .tb-sort")
        self.text_until("#tb-code .tb-note", "Sorted by score ascending over the whole file (300,000 rows", "the whole-file sort note")
        self.assertEqual("0", p.evaluate("[...document.querySelectorAll('#tb-code .tb-body .tb-row')][0]"
                                          ".querySelectorAll('.tb-td')[2].textContent"))
        p.evaluate("document.querySelector('#tb-code .tb-scroll').scrollTop = 1e12")
        self.until("[...document.querySelectorAll('#tb-code .tb-body .tb-row')].slice(-2).some(r => "
                   "r.querySelectorAll('.tb-td')[2] && r.querySelectorAll('.tb-td')[2].textContent === '96')",
                   "the sorted file ends with the largest score")
        p.click("#tb-code .tb-filter button:has-text('Clear')")
        self.until("document.querySelector('#tb-code .tb-meta').textContent.includes('300,000 rows')", "file order")

    def test_4c_a_cancelled_count_and_a_not_yet_indexed_page_say_so_and_recover(self):
        p = self.page
        r = p.evaluate("""async () => {
            const host = document.createElement("div"); host.className = "tb-code"; host.style.height = "260px"; document.body.append(host);
            const real = MK.api; let rows = 0;
            MK.api = async (u, b, o) => {
              if (u.startsWith("/api/table/count")) return { cancelled: true };
              if (u.startsWith("/api/table/info")) { const d = await real(u, b, o); d.complete = false; d.rows = null; d.estimate = 5000; return d; }
              if (u.startsWith("/api/table/rows") && rows++ < 2) return { state: "ok", indexed: false, indexed_rows: 10, estimate: 5000, rows: [], flags: {}, row: 0 };
              if (u.startsWith("/api/table/rows")) { const d = await real(u, b, o); d.total = null; d.complete = false; return d; }
              return real(u, b, o);
            };
            const v = MK.table.mount(host, "data/people.csv");
            await new Promise((r) => setTimeout(r, 400));
            const meta = host.querySelector(".tb-meta").textContent;
            const early = host.querySelectorAll(".tb-body .tb-td:not(.dim)").length;
            await new Promise((r) => setTimeout(r, 2200));
            const late = host.querySelectorAll(".tb-body .tb-td:not(.dim)").length;
            MK.api = real; v.destroy(); host.remove();
            return { meta, early, late };
        }""")
        # the exact words: a cancelled count points at the control that recovers it (never "reload")
        self.assertIn("not counted: the count was cancelled — Retry in the Jobs drawer", r["meta"])
        self.assertNotIn("counting…", r["meta"])
        self.assertEqual(0, r["early"], "the page was not indexed yet")
        self.assertGreater(r["late"], 0, "the not-indexed page is asked for again")

    def test_4c2_a_failed_count_says_the_jobs_own_error(self):
        p = self.page
        p.evaluate("""() => {
            const host = document.createElement("div"); host.id = "tb-failed"; host.style.height = "260px"; document.body.append(host);
            window.__realApi = MK.api;
            MK.api = async (u, b, o) => {
              if (u.startsWith("/api/table/count")) return { error: "JobFailed", note: "the disk went away", failed: true };
              const d = await window.__realApi(u, b, o);
              if (u.startsWith("/api/table/info")) { d.complete = false; d.rows = null; d.estimate = 5000; }   // a count is owed
              if (u.startsWith("/api/table/rows")) { d.total = null; d.complete = false; }                  // ... and only the count pays it
              return d;
            };
            window.__tbFailed = MK.table.mount(host, "data/people.csv");
        }""")
        try:
            meta = self.text_until("#tb-failed .tb-meta", "not counted", "the count's outcome")
        finally:
            p.evaluate("() => { MK.api = window.__realApi; window.__tbFailed.destroy(); document.getElementById('tb-failed').remove(); }")
        self.assertIn("not counted: the count failed: the disk went away", meta)

    def test_4e_a_real_count_cancelled_says_so_and_the_drawers_retry_counts_it_into_this_table(self):
        """D-Q2 end to end: Cancel a real count, read its own words, press the drawer's Retry (the
        control the words name), and the OPEN table - no reopen, no new button - says the count."""
        p = self.page
        self.open_file("data/huge.csv")
        self.text_until("#tb-code .tb-meta", "counting…", "the count running")
        p.locator("#tb-code .job-strip .job-cancel").click()
        self.text_until("#tb-code .tb-meta", "not counted: the count was cancelled — Retry in the Jobs drawer",
                        "the cancelled count's own words")
        p.evaluate("window.__tb = document.querySelector('#tb-code .tb')")
        buttons = p.evaluate("document.querySelectorAll('#tb-code button').length")
        p.click("[aria-controls=activity-drawer]")
        p.locator("#activity-drawer .act-row.cancelled", has_text="huge.csv").first \
            .locator("button", has_text="Retry").click()
        p.keyboard.press("Escape")                                     # the drawer closes; the table is under it
        exact = "%s rows · " % format(HUGE_ROWS, ",")                  # the count, not "≈" the estimate
        self.until("document.querySelector('#tb-code .tb-meta').textContent.startsWith(%r)" % exact,
                   "the retried count, in this table")
        self.assertNotIn("not counted", p.text_content("#tb-code .tb-meta"))
        self.assertTrue(p.evaluate("window.__tb.isConnected"), "the same table: nothing was reopened")
        self.assertEqual(buttons, p.evaluate("document.querySelectorAll('#tb-code button').length"),
                         "no new button: the drawer's Retry is the control")

    def test_4d_ragged_and_not_utf8_rows_are_flagged_in_the_gutter(self):
        p = self.page
        self.open_file("data/rag.csv")
        self.text_until("#tb-code .tb-meta", "3 rows", "the ragged file's table")
        self.until("document.querySelectorAll('#tb-code .tb-body .tb-gut').length >= 3", "the three rows")
        gut = p.evaluate("[...document.querySelectorAll('#tb-code .tb-body .tb-gut')].map(n => n.textContent)")
        self.assertIn("⚠", gut[0], gut)
        self.assertIn("�", gut[1], gut)
        self.assertIn("⚠", gut[2], gut)
        self.assertTrue(p.evaluate("!!document.querySelector('#tb-code .tb-ragged')"))

    def test_5_bare_cr_and_utf16_decode_and_utf32_is_refused_plainly(self):
        p = self.page
        self.open_file("data/mac.csv")
        self.text_until("#tb-code .tb-meta", "2 rows", "a bare-CR file is two rows")
        self.open_file("data/wide.csv")
        self.text_until("#tb-code .tb-meta", "2 rows", "the UTF-16 rows")
        self.until("[...document.querySelectorAll('#tb-code .tb-body .tb-td')]"
                   ".some(n => n.textContent === '日本')", "decoded UTF-16 text")
        self.assertIn("UTF-16", p.text_content("#tb-code .tb-chips"))
        self.open_file("data/utf32.csv")
        self.text_until("#tb-code", "UTF-32", "the plain refusal")
        self.assertEqual(0, p.locator("#tb-code .tb-row").count())

    def test_6_a_changed_csv_gets_a_table_diff_by_position_and_by_key(self):
        p = self.page
        self.go("review")
        p.wait_for_selector("#nav-list li", state="attached")
        p.locator("#nav-list li", has_text="people.csv").first.click()
        p.wait_for_selector("#diff .tb-dbtn:not([disabled])")
        p.click("#diff .tb-dbtn")
        p.wait_for_selector("#diff .tb-dsum")
        # by position: row 2 changed, row 3 changed (Cy -> markup), row 4 added
        self.assertIn("added", p.text_content("#diff .tb-dsum"))
        p.select_option("#diff .tb-key", "id")
        self.until("document.querySelector('#diff .tb-dsum') && "
                   "document.querySelector('#diff .tb-dsum').textContent.includes('+2 added')",
                   "the keyed summary")
        s = p.text_content("#diff .tb-dsum")
        self.assertIn("+2 added", s)
        self.assertIn("1 removed", s.replace("−", ""))
        self.assertIn("~1 changed", s)
        self.assertIn("1 unchanged", s)
        body = p.text_content("#diff .tb-dwrap")
        self.assertIn("<img src=x onerror=window.__pwned=1>", body, "diff cells are text")
        self.assertIn("25", body)
        self.assertEqual(0, p.locator("#diff .tb-dwrap img").count())
        self.assertIsNone(p.evaluate("window.__pwned"))
        self.assertIn("holds the old side's 3 keys", p.text_content("#diff .tb-dmem"),
                      "the keyed diff says what memory it holds")
        self.assertEqual(1, p.locator("#diff .tb-cellchg").count(), "the changed cell is highlighted")
        self.assertIn("20", p.text_content("#diff .tb-cellchg .tb-old"))
        self.assertIn("25", p.text_content("#diff .tb-cellchg .tb-new"))

    def test_7_the_line_diff_is_one_click_back(self):
        p = self.page
        p.click("#diff .tb-dbtn")
        self.until("!document.querySelector('#diff section.hunk').hidden", "the hunks to return")

    def test_8_a_repeated_key_is_said_and_paired_in_order(self):
        p = self.page
        p.locator("#nav-list li", has_text="dups.csv").first.click()
        p.wait_for_selector("#diff .tb-dbtn:not([disabled])")
        p.click("#diff .tb-dbtn")
        p.wait_for_selector("#diff .tb-dsum")
        p.select_option("#diff .tb-key", "k")
        self.text_until("#diff .tb-dsum", "repeated keys", "the repeated-keys chip")
        self.assertIn("~1 changed", p.text_content("#diff .tb-dsum"))
        self.assertIn("1 repeated keys", p.text_content("#diff .tb-dsum"))


def build_uncountable(base):
    """A file whose count cannot finish: 300 short rows, then one record over the 64 MiB bound
    (a quoted cell). The first page reads; the whole-file count is refused in plain words."""
    import sys
    if H.SRC not in sys.path:               # THIS checkout's bound, not an installed copy's
        sys.path.insert(0, H.SRC)
    from mokata.ide.tableview import RECORD_MAX
    repo = H.make_repo("shop", base)
    with open(repo + "/data_huge.csv", "wb") as fh:
        fh.write(b"id,name\n" + b"".join(b"%d,n%d\n" % (i, i) for i in range(300)))
        fh.write(b'300,"' + b"x" * (RECORD_MAX + 16) + b'"\n')
    return repo


class ACountThatCannotFinishSaysWhy(H.IdeFlow):
    FIXTURE = staticmethod(build_uncountable)

    def test_1_the_meta_line_gives_the_reason_not_reload(self):
        """PINS-UI (P8 L1): the count job answers `unsupported` with its reason. The meta line says
        that reason; it never advises "reload to count again", which would fail the same way."""
        p = self.page
        self.open_file("data_huge.csv")
        p.wait_for_selector("#tb-code .tb-row .tb-td")
        self.text_until("#tb-code .tb-meta", "not counted", "the count's outcome")
        meta = p.text_content("#tb-code .tb-meta")
        self.assertIn("over 64 MiB", meta, "the server's reason is said")
        self.assertNotIn("reload", meta)
        self.assertNotIn("counting", meta)
        self.assertEqual("n0", p.evaluate("[...document.querySelectorAll('#tb-code .tb-body .tb-row')][0]"
                                          ".querySelectorAll('.tb-td')[1].textContent"), "the rows still read")


class ACountInFlightSaysCounting(H.IdeFlow):
    """0.0.28 D: from the table's FIRST paint until the count answers, the meta line says
    "counting" — never an outcome ("not counted: " with no reason) the count has not had.

    The race this pins (the Linux runner's red on `ACountThatCannotFinishSaysWhy`): the mount
    painted the meta line BEFORE it set `st.counting`, so its first paint read "not counted: "
    with an empty reason; page 0's arrival repainted it as "counting…". A test reading the meta
    between those two paints saw an outcome the count never had. Here every text the meta line
    ever holds is recorded (a MutationObserver installed before the mount), and the count request
    is HELD (Playwright `route`), so the window is made, not hoped for."""
    FIXTURE = staticmethod(build_uncountable)

    def test_1_held_count_reads_counting_from_the_first_paint(self):
        p = self.page
        held, state = [], {"on": True}

        def hold(route):
            if state["on"]:
                held.append(route)
            else:
                route.continue_()
        p.evaluate("""() => { window.__metas = [];
            new MutationObserver(() => { const m = document.querySelector('#tb-code .tb-meta');
                if (m && m.textContent && window.__metas[window.__metas.length - 1] !== m.textContent)
                    window.__metas.push(m.textContent); })
              .observe(document.body, { childList: true, subtree: true, characterData: true }); }""")
        p.route("**/api/table/count*", hold)
        try:
            self.open_file("data_huge.csv")
            p.wait_for_selector("#tb-code .tb-row .tb-td")
            for _ in range(400):
                if held:
                    break
                p.wait_for_timeout(25)               # pumps the route handler
            self.assertTrue(held, "the count was never asked")
            metas = p.evaluate("window.__metas")
            self.assertTrue(metas, "the meta line was painted")
            for m in metas:
                self.assertIn("counting", m, "every paint before the count answers says counting")
                self.assertNotIn("not counted", m)
        finally:
            state["on"] = False
            for r in held:
                r.continue_()
            p.unroute("**/api/table/count*", hold)
        self.text_until("#tb-code .tb-meta", "over 64 MiB", "the server's reason once the count answers")


MILLION_ROWS = 4000000


def build_twice(base):
    """The uncountable file again, plus a big COUNTABLE one (4M rows, a count of a second or more):
    the page mounts each table more than once while its count is in flight."""
    repo = build_uncountable(base)
    with open(repo + "/million.csv", "wb") as fh:
        fh.write(b"id,name\n")
        step = 200000
        for lo in range(0, MILLION_ROWS, step):
            fh.write(b"".join(b"%d,n%d\n" % (i, i) for i in range(lo, lo + step)))
    import shutil
    for twin in ("stale.csv", "abort.csv", "both.csv"):        # a count is remembered per file: each flow its own
        shutil.copyfile(repo + "/million.csv", repo + "/" + twin)
    return repo


class ACountMountedTwiceKeepsItsReason(H.IdeFlow):
    """0.0.28 D (TABLE-COUNT-JOB-WATCHER-RACE): a count is cancelled "nobody is waiting" only when no
    request of the page still wants it. Mounting the table again (Raw/Table, another file and back),
    a stream that reconnects, or one of two asks being aborted never turns a count into "the count
    stopped; reload" — each outcome says its own word."""
    FIXTURE = staticmethod(build_twice)

    def meta(self):
        return self.page.text_content("#tb-code .tb-meta") or ""

    def toggle(self):
        p = self.page
        p.click("#tb-toggle")

    def test_1_an_uncountable_file_mounted_twice_says_the_servers_reason(self):
        p = self.page
        self.open_file("data_huge.csv")
        p.wait_for_selector("#tb-code .tb-row .tb-td")
        self.toggle()                                       # Raw text
        self.until("document.getElementById('tb-code').hidden", "the raw text")
        self.toggle()                                       # the table again: a second mount
        p.wait_for_selector("#tb-code .tb-row .tb-td")
        self.open_file("README.md")
        self.open_file("data_huge.csv")                     # and a third, from another file
        p.wait_for_selector("#tb-code .tb-row .tb-td")
        self.until("(() => { const m = document.querySelector('#tb-code .tb-meta'); "
                   "return !!m && m.textContent.includes('not counted') && !m.textContent.includes('counting'); })()",
                   "the count's outcome on the mounted table")
        meta = self.meta()
        self.assertIn("over 64 MiB", meta, "the server's reason is said")
        for gone in ("reload", "stopped", "counting"):
            self.assertNotIn(gone, meta)

    def test_2_a_countable_file_mounted_twice_is_counted(self):
        p = self.page
        self.open_file("million.csv")
        p.wait_for_selector("#tb-code .tb-row .tb-td")
        self.open_file("README.md")                         # away while it counts ...
        self.open_file("million.csv")                       # ... and back: a second mount
        p.wait_for_selector("#tb-code .tb-row .tb-td")
        self.text_until("#tb-code .tb-meta", "4,000,000 rows", "the full count")
        self.assertNotIn("not counted", self.meta())

    def test_3_a_stale_stream_ending_mid_count_keeps_the_count(self):
        """A second stream of the same page id opens (as an EventSource reconnect opens its new stream
        before the server has noticed the old one end), the count starts, then that stream ENDS. The
        page's stream is still open: its end is a stale stream's, not the page leaving. The count the
        page waits on finishes `done`, never `cancelled`."""
        p = self.page
        p.evaluate("""() => new Promise((ok) => {
            window.__es2 = new EventSource('/api/events?client=' + encodeURIComponent(MK.clientId));
            window.__es2.addEventListener('changed', () => ok(true), { once: true }); })""")
        p.evaluate("""() => { const t = window.__s = { seen: [], got: null };
            MK.api('/api/table/count?' + new URLSearchParams({ path: 'stale.csv', sync_ms: '0' }),
                   undefined, { onJob: (j) => t.seen.push(j.state) }).then((r) => { t.got = r; });
            return 0; }""")
        self.until("__s.seen.length > 0", "the count to start")
        p.evaluate("window.__es2.close()")
        self.until("__s.got !== null", "the count's answer")
        got = p.evaluate("__s.got")
        self.assertEqual("ok", got.get("state"), "the count was cancelled: %r" % (got,))
        self.assertEqual(MILLION_ROWS, got.get("rows"))

    def test_4_aborting_one_of_two_same_asks_keeps_the_other(self):
        p = self.page
        p.evaluate("""() => {
            const t = window.__t = { s1: [], s2: [], leaves: [], first: null, second: null };
            const real = window.__realFetch = window.fetch;
            window.fetch = async (...a) => { const r = await real(...a);
                if (String(a[0]).includes('/leave')) t.leaves.push(await r.clone().json()); return r; };
            const u = '/api/table/count?' + new URLSearchParams({ path: 'abort.csv', sync_ms: '0' });
            t.ac = new AbortController();
            MK.api(u, undefined, { signal: t.ac.signal, onJob: (j) => t.s1.push(j.state) })
              .then((r) => { t.first = { resolved: r }; }, (e) => { t.first = { rejected: e.name }; });
            MK.api(u, undefined, { onJob: (j) => t.s2.push(j.state) }).then((r) => { t.second = r; });
            return 0; }""")
        self.until("__t.s1.length > 0 && __t.s2.length > 0", "both asks following the one count job")
        at_abort = p.evaluate("() => { const s = __t.s2[__t.s2.length - 1]; __t.ac.abort(); return s; }")
        self.until("__t.first !== null && __t.second !== null && __t.leaves.length > 0",
                   "both asks answered and the leave sent")
        got = p.evaluate("() => { window.fetch = window.__realFetch; return { first: __t.first, second: __t.second, leave: __t.leaves[0] }; }")
        self.assertEqual({"rejected": "AbortError"}, got["first"], "the aborted ask is superseded")
        self.assertNotEqual("done", at_abort, "the count was still running when one ask left")
        self.assertNotEqual("cancelled", got["leave"]["job"]["state"], "one ask leaving cancelled the other's count")
        self.assertEqual("ok", got["second"].get("state"), "the other ask got its answer: %r" % (got["second"],))
        self.assertEqual(MILLION_ROWS, got["second"].get("rows"))

    def test_5_when_both_asks_leave_the_count_stops(self):
        """The other half of the rule: each ask is named by its own request token, so when the LAST
        ask of the page leaves, nobody wants the count and it stops at once ("nobody is waiting")."""
        p = self.page
        p.evaluate("""() => {
            const t = window.__b = { s: [[], []], leaves: [], done: [null, null] };
            const real = window.__realFetch = window.fetch;
            window.fetch = async (...a) => { const r = await real(...a);
                if (String(a[0]).includes('/leave')) t.leaves.push(await r.clone().json()); return r; };
            const u = '/api/table/count?' + new URLSearchParams({ path: 'both.csv', sync_ms: '0' });
            t.ac = [new AbortController(), new AbortController()];
            for (const i of [0, 1]) MK.api(u, undefined, { signal: t.ac[i].signal, onJob: (j) => t.s[i].push(j.state) })
              .then((r) => { t.done[i] = { resolved: r }; }, (e) => { t.done[i] = { rejected: e.name }; });
            return 0; }""")
        self.until("__b.s[0].length > 0 && __b.s[1].length > 0", "both asks following the one count job")
        p.evaluate("() => { __b.ac[0].abort(); __b.ac[1].abort(); }")
        self.until("__b.done[0] !== null && __b.done[1] !== null && __b.leaves.length === 2",
                   "both asks rejected and both leaves sent")
        got = p.evaluate("() => { window.fetch = window.__realFetch; return __b.leaves.map((l) => l.job.state); }")
        # the two answers may arrive in either order; the server handled one leave first (the count
        # still wanted by the other ask) and the other last (nobody left: cancelled)
        self.assertEqual(1, got.count("cancelled"),
                         "exactly the LAST leave cancels the count (one alone must not): %r" % (got,))
