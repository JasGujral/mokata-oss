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
        self.assertIn("not counted", r["meta"], "a stopped count must not read as counting")
        self.assertNotIn("counting…", r["meta"])
        self.assertEqual(0, r["early"], "the page was not indexed yet")
        self.assertGreater(r["late"], 0, "the not-indexed page is asked for again")

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
