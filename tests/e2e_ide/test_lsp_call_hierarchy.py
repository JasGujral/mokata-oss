"""IDE e2e — Callers and Callees in the Find Usages box (0.0.28 G, ID-LSP-CALL-HIERARCHY), driven the way a
person reaches them: the palette ("Find Callers" / "Find Callees") and the box's own tabs. The project's
language server is a stand-in (tests/_fake_lsp_server.py, found as the repo's `.venv` copy of
`pyright-langserver`), the workspace trusted in a private profile — the person's real `~/.mokata` is
never touched.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import stat
import sys
import tempfile
import unittest

import _harness as H

FAKE = os.path.join(H.ROOT, "tests", "_fake_lsp_server.py")
HUB_LINES = 500


def _lsp_fixture(knobs, home_box):
    """The shop fixture + a `.venv/bin/pyright-langserver` that runs the fake server with `knobs`,
    the workspace trusted in a private HOME (`home_box[0]`), which the IDE server is launched with."""
    def build(base):
        repo = H.make_repo("shop", base)
        bindir = os.path.join(repo, ".venv", "bin")
        os.makedirs(bindir)
        exe = os.path.join(bindir, "pyright-langserver")
        with open(exe, "w", encoding="utf-8") as fh:
            fh.write("#!%s -I\nimport runpy, sys\nsys.argv = [%r] + %r\nrunpy.run_path(%r, run_name='__main__')\n"
                     % (sys.executable, FAKE, list(knobs), FAKE))
        os.chmod(exe, os.stat(exe).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        if "--hub" in knobs:
            H._write(repo, "shop/hub.py", "".join("def caller_%d(): return slugify('x')\n" % i
                                                   for i in range(HUB_LINES)))
        home = tempfile.mkdtemp(prefix="mokata-e2e-home-")
        home_box.append(home)
        if H.SRC not in sys.path:
            sys.path.insert(0, H.SRC)
        from unittest import mock
        from mokata.ide import trust as T
        with mock.patch.dict(os.environ, {"HOME": home, "USERPROFILE": home}):
            T.decide(repo, True, "e2e")
        return repo
    return build


def _helpers():
    # not a top-level class: test_o9 grades every module-level class as a flow
    class _LspHelpers:
        """Mixed into each flow by `_lsp_flow` (every flow's base is exactly `H.IdeFlow`, the
        structural rule test_o9 grades; a decorator adds the language-server setup without a base)."""

        @classmethod
        def setUpClass(cls):
            box = []
            cls.FIXTURE = staticmethod(_lsp_fixture(cls.KNOBS, box))
            real_env = H._env

            def env():
                e = real_env()
                if box:
                    e["HOME"] = e["USERPROFILE"] = box[0]
                e.pop("VIRTUAL_ENV", None)
                return e
            H._env = env                      # only the IDE server's child sees the private HOME
            try:
                H.IdeFlow.setUpClass.__func__(cls)
            finally:
                H._env = real_env
            cls.home_box = box
            if cls.no_browser:
                return
            res = cls.page.evaluate("MK.api('/api/lsp/start', {language: 'python'})")
            assert res.get("started"), res
            cls.wait_ready()

        @classmethod
        def wait_ready(cls):
            import time
            end = time.time() + 30
            while time.time() < end:
                st = cls.page.evaluate("MK.api('/api/lsp')")
                if any(s.get("state") == "ready" for s in st.get("servers", [])):
                    return
                time.sleep(0.2)
            raise AssertionError("the fake language server never became ready")

        # -- helpers ------------------------------------------------------------------------------
        def row(self, n):
            return self.page.locator("#code div.cl[data-n='%d']" % n)

        def palette(self, title):
            p = self.page
            self.press("Control+K")
            p.wait_for_selector("#qo:not([hidden])")
            p.fill("#qo-input", ">" + title)
            p.locator("#qo-results .qo-row", has_text=title).first.click()

        def not_body(self, what):
            self.assertFalse(self.page.evaluate("document.activeElement === document.body || !document.activeElement"),
                             "focus fell to <body> after " + what)

        def focused(self):
            return self.page.evaluate("(() => { const a = document.activeElement; return a ? (a.getAttribute('role') || a.tagName) + '|' + (a.dataset.name || a.textContent.slice(0, 40)) : ''; })()")
    return _LspHelpers


def _lsp_flow(cls):
    for name, val in vars(_helpers()).items():
        if not name.startswith("__") and name not in vars(cls):   # a flow's own override wins
            setattr(cls, name, val)
    return cls


@_lsp_flow
class CallersAndCallees(H.IdeFlow):
    KNOBS = ("--index-seconds", "0.1", "--delay", "slugify:4")

    def test_1_find_callers_from_the_palette_shows_a_tree_under_the_function(self):
        """On `slugify`, palette → Find Callers: the box's Callers tab is selected; the tree's root
        is `slugify` with its callers one level below (title_slug and the test), from the language
        server, labelled; each row names path:line and a call count."""
        p = self.page
        self.open_file("shop/text.py")
        self.row(1).click()
        self.palette("Find Callers")
        p.locator(".p4-usages [role=treeitem][aria-level='2']").first.wait_for()
        tab = p.locator(".p4-usages [role=tab][aria-selected=true]")
        self.assertEqual("Callers", tab.text_content().strip())
        root = p.locator(".p4-usages [role=treeitem][aria-level='1']")
        self.assertEqual(1, root.count())
        self.assertIn("slugify", root.text_content())
        kids = p.locator(".p4-usages [role=treeitem][aria-level='2']")
        names = sorted(kids.evaluate_all("(xs) => xs.map((x) => x.dataset.name)"))
        self.assertEqual(["test_slugify", "title_slug"], names)
        self.assertIn("language server", p.text_content(".p4-usages .p4-calls-head"))
        first = kids.first
        self.assertEqual(str(kids.count()), first.get_attribute("aria-setsize"))
        self.assertEqual("1", first.get_attribute("aria-posinset"))
        self.assertEqual("false", first.get_attribute("aria-expanded"))
        self.not_body("the answer arrived")

    def test_2_expand_a_caller_one_more_level_and_focus_stays_on_it(self):
        """→ on title_slug expands it: its caller order_ref appears at level 3; focus stays on
        title_slug; ← collapses it and focus is still there."""
        p = self.page
        row = p.locator(".p4-usages [role=treeitem][data-name='title_slug']")
        row.focus()
        self.press("ArrowRight")
        p.locator(".p4-usages [role=treeitem][aria-level='3'][data-name='order_ref']").wait_for()
        self.assertEqual("true", row.get_attribute("aria-expanded"))
        self.until("document.activeElement && document.activeElement.dataset.name === 'title_slug'",
                   "focus to stay on the expanded row")
        self.press("ArrowLeft")
        self.until("document.querySelectorAll(\".p4-usages [role=treeitem][aria-level='3']\").length === 0",
                   "the collapse")
        self.assertEqual("false", row.get_attribute("aria-expanded"))
        self.until("document.activeElement && document.activeElement.dataset.name === 'title_slug'",
                   "focus to stay on the collapsed row")

    def test_3_arrows_move_and_enter_opens_the_file_at_the_callers_line(self):
        p = self.page
        p.locator(".p4-usages [role=treeitem][data-name='title_slug']").focus()
        self.press("ArrowDown")
        self.press("ArrowUp")
        self.until("document.activeElement && document.activeElement.dataset.name === 'title_slug'", "ArrowUp back")
        self.press("Enter")
        self.until("MK.nav.path === 'shop/text.py' && MK.nav.line === 5", "the file to open at title_slug's line")

    def test_4_the_callees_tab_answers_what_the_function_calls(self):
        """The tablist: ← / → move between Usages · Callers · Callees (roving tabindex); selecting
        Callees asks about the same function: title_slug calls slugify."""
        p = self.page
        self.open_file("shop/text.py")
        self.row(5).click()
        self.palette("Find Callers")
        p.locator(".p4-usages [role=treeitem][aria-level='1'][data-name='title_slug']").wait_for()
        p.locator(".p4-usages [role=tab][aria-selected=true]").focus()
        self.press("ArrowRight")
        self.until("document.activeElement && document.activeElement.textContent.trim() === 'Callees'", "→ to Callees")
        self.press("Enter")
        p.locator(".p4-usages [role=treeitem][aria-level='2'][data-name='slugify']").wait_for()
        tabs = p.locator(".p4-usages [role=tab]")
        self.assertEqual(["Usages", "Callers", "Callees"], [t.strip() for t in tabs.all_text_contents()])
        self.assertEqual(["-1", "-1", "0"], tabs.evaluate_all("(xs) => xs.map((x) => String(x.tabIndex))"))
        self.not_body("the tab switch")
        self.press("Home")
        self.until("document.activeElement && document.activeElement.textContent.trim() === 'Usages'", "Home")
        self.press("End")
        self.until("document.activeElement && document.activeElement.textContent.trim() === 'Callees'", "End")

    def test_5_a_newer_question_supersedes_an_older_slower_one(self):
        """Find Callers on slugify (the server answers it late) then at once on order_ref: the
        tree shows order_ref's callers, and still does after slugify's late answer lands."""
        p = self.page
        self.open_file("shop/text.py")
        self.row(1).click()
        self.palette("Find Callers")
        self.open_file("shop/orders.py")
        self.row(9).click()
        self.palette("Find Callers")
        p.locator(".p4-usages [role=treeitem][aria-level='1'][data-name='order_ref']").wait_for()
        self.until("MK.api('/api/jobs').then((j) => !(j.active || []).some((x) => x.kind === 'lsp.query'))"
                   , "every call question to end", 60000)
        # rule F11: the superseded question was CANCELLED by the page, not left to finish
        self.assertTrue(p.evaluate("MK.api('/api/jobs').then((j) => (j.recent || []).some((x) => x.kind === 'lsp.query'"
                                   " && (x.label || '').includes('shop/text.py') && x.state === 'cancelled'))"),
                        "the superseded slugify question was not cancelled")
        self.assertEqual(1, p.locator(".p4-usages [role=treeitem][aria-level='1'][data-name='order_ref']").count())
        self.assertEqual(0, p.locator(".p4-usages [role=treeitem][data-name='slugify']").count())

    def test_5b_a_question_superseded_before_its_job_id_arrived_is_still_cancelled(self):
        """The race the CI runner hit (probe/matrix/G run 42595158407): the older question's 202
        reaches the page only AFTER the newer question was answered, so its job id was not known
        when the newer one superseded it. It must be cancelled the moment the id arrives. The page
        holds that 202 until the newer answer is rendered (verify-G L-5: gated on state)."""
        p = self.page
        cancelled = ("MK.api('/api/jobs').then((j) => (j.recent || []).filter((x) => x.kind === 'lsp.query'"
                     " && (x.label || '').includes('shop/text.py') && x.state === 'cancelled').length)")
        before = p.evaluate(cancelled)
        p.evaluate("""(() => {
            const f = window.fetch; window.__realFetch = f; let once = true;
            window.fetch = function (u) {
                const r = f.apply(this, arguments);
                if (once && String(u).includes('/api/lsp/calls') && String(u).includes('text.py')) {
                    once = false;
                    // held until the NEWER question's answer is on screen (a state, not a
                    // clock): the older job's id then reaches the page strictly after it was
                    // superseded, so only the late-id path can cancel it
                    const newer = () => document.querySelector(
                        ".p4-usages [role=treeitem][aria-level='1'][data-name='order_ref']");
                    return r.then((x) => new Promise((ok) => {
                        const poll = () => (newer() ? ok(x) : setTimeout(poll, 20));
                        poll();
                    }));
                }
                return r;
            };
        })()""")
        try:
            self.open_file("shop/text.py")
            self.row(1).click()
            self.palette("Find Callers")
            self.open_file("shop/orders.py")
            self.row(9).click()
            self.palette("Find Callers")
            p.locator(".p4-usages [role=treeitem][aria-level='1'][data-name='order_ref']").wait_for()
            self.until("MK.api('/api/jobs').then((j) => !(j.active || []).some((x) => x.kind === 'lsp.query'))",
                       "every call question to end", 60000)
            self.assertEqual(before + 1, p.evaluate(cancelled),
                             "the question superseded before its id arrived ran to the end")
            self.assertEqual(0, p.locator(".p4-usages [role=treeitem][data-name='slugify']").count())
        finally:
            # an arrow, not an assignment expression: `evaluate` CALLS an expression's function value
            p.evaluate("() => { window.fetch = window.__realFetch || window.fetch; }")

    def test_6_tree_a11y_names_and_two_hundred_percent_zoom(self):
        """Every row has a screen-reader name (path:line and the name), the counts are right, and at
        200% zoom in a narrow panel rows wrap (no clipping) and the tablist scrolls, not clips."""
        p = self.page
        self.open_file("shop/text.py")
        self.row(1).click()
        self.palette("Find Callers")
        p.locator(".p4-usages [role=treeitem][aria-level='2']").first.wait_for()
        labels = p.locator(".p4-usages [role=treeitem]").evaluate_all(
            "(xs) => xs.map((x) => x.getAttribute('aria-label') || '')")
        self.assertTrue(all(":" in x and len(x) > 4 for x in labels), labels)
        self.assertEqual(1, p.locator(".p4-usages [role=tree]").count())
        self.assertEqual(1, p.locator(".p4-usages [role=tabpanel]").count())
        p.evaluate("document.documentElement.style.zoom = '2'; document.querySelector('.p4-usages').style.maxWidth = '260px'")
        try:
            over = p.evaluate("[...document.querySelectorAll('.p4-usages [role=treeitem] .p4-call-row')]"
                              ".some((x) => x.scrollWidth > x.clientWidth + 1)")
            self.assertFalse(over, "a row clips at 200%")
            ox = p.evaluate("getComputedStyle(document.querySelector('.p4-usages [role=tablist]')).overflowX")
            self.assertIn(ox, ("auto", "scroll"))
        finally:
            p.evaluate("document.documentElement.style.zoom = ''; document.querySelector('.p4-usages').style.maxWidth = ''")
        p.emulate_media(reduced_motion="reduce")
        try:
            dur = p.evaluate("getComputedStyle(document.querySelector('.p4-usages [role=treeitem]')).transitionDuration")
            self.assertIn(dur, ("0s", ""))
        finally:
            p.emulate_media(reduced_motion="no-preference")

    def test_7_palette_has_the_commands_without_keys_and_the_language_panel_lost_references(self):
        """G-Q4: Find Callers / Find Callees in the palette, no keybinding. G-Q3: the Language
        panel's references command is gone (references: Find Usages only); hover/definition stay."""
        p = self.page
        cmds = p.evaluate("mokataIDE.commands().map((c) => [c.id, c.title, [].concat(c.keys || []).length])")
        by = {c[0]: c for c in cmds}
        self.assertIn("usages.callers", by)
        self.assertIn("usages.callees", by)
        self.assertEqual(0, by["usages.callers"][2])
        self.assertEqual(0, by["usages.callees"][2])
        self.assertNotIn("lsp.references", by)
        self.assertIn("lsp.definition", by)
        self.assertIn("lsp.hover", by)

    def test_8_the_code_menu_offers_find_callers_and_find_callees(self):
        """Right-click in `title_slug`'s body: the menu offers Find Callers and Find Callees after
        Find Usages (reachable by ↓ alone); each opens its tab in the Find Usages box, asked about
        the function enclosing that line — callers `order_ref`, callees `slugify`."""
        p = self.page
        self.open_file("shop/text.py")
        self.row(6).click()
        self.row(6).click(button="right")
        menu = p.locator(".p4-menu")
        menu.wait_for()
        self.assertEqual(["Copy with Context…", "Find Usages", "Find Callers", "Find Callees"],
                         menu.locator("[role=menuitem]").all_text_contents())
        self.press("ArrowDown")
        self.press("ArrowDown")
        self.until("document.activeElement && document.activeElement.textContent === 'Find Callers'",
                   "↓ to reach Find Callers")
        self.press("Enter")
        self.assertEqual(0, p.locator(".p4-menu").count(), "the menu stayed open")
        p.locator(".p4-usages [role=treeitem][aria-level='2'][data-name='order_ref']").wait_for()
        tab = p.locator(".p4-usages [role=tab][aria-selected=true]")
        self.assertEqual("Callers", tab.text_content().strip())
        self.assertEqual("title_slug", p.locator(".p4-usages [role=treeitem][aria-level='1']").first.get_attribute("data-name"))
        self.not_body("Find Callers from the menu")
        self.row(6).click()
        self.row(6).click(button="right")
        p.locator(".p4-menu").wait_for()
        p.locator(".p4-menu [role=menuitem]", has_text="Find Callees").click()
        p.locator(".p4-usages [role=treeitem][aria-level='2'][data-name='slugify']").wait_for()
        self.assertEqual("Callees", p.locator(".p4-usages [role=tab][aria-selected=true]").text_content().strip())


@_lsp_flow
class OutsideRowsAreInert(H.IdeFlow):
    KNOBS = ("--index-seconds", "0.1", "--malformed", "entries", "--outside", "/nonexistent/outside_secret.py")

    def test_1_outside_rows_are_disabled_and_names_render_as_text(self):
        """A hostile server: the skipped count is said; outside rows are aria-disabled, not
        expandable, and Enter on one opens nothing; a `<script>` name stays text."""
        p = self.page
        self.open_file("shop/text.py")
        self.row(1).click()
        self.palette("Find Callers")
        p.locator(".p4-usages [role=treeitem][aria-level='2']").first.wait_for()
        self.text_until(".p4-usages .p4-calls-head", "malformed were skipped", "the skipped count")
        out = p.locator(".p4-usages [role=treeitem][aria-disabled=true]")
        self.assertGreaterEqual(out.count(), 4)
        self.assertIsNone(out.first.get_attribute("aria-expanded"))
        path = p.evaluate("MK.nav.path")
        out.first.focus()
        self.press("Enter")
        self.press("ArrowRight")
        self.assertEqual(path, p.evaluate("MK.nav.path"))
        self.assertEqual(0, p.locator(".p4-usages script").count())
        self.assertTrue(p.evaluate("[...document.querySelectorAll('.p4-usages [role=treeitem]')].some((x) => x.textContent.includes('<script>'))"))


@_lsp_flow
class IndexingShowsTheGraph(H.IdeFlow):
    KNOBS = ("--index-seconds", "600")

    @classmethod
    def wait_ready(cls):
        import time
        end = time.time() + 30
        while time.time() < end:
            st = cls.page.evaluate("MK.api('/api/lsp')")
            if any(s.get("state") == "indexing" for s in st.get("servers", [])):
                return
            time.sleep(0.2)
        raise AssertionError("the fake language server never reported indexing")

    def test_1_while_indexing_the_call_graph_answers_callers_and_says_so(self):
        p = self.page
        self.open_file("shop/text.py")
        self.row(1).click()
        self.palette("Find Callers")
        p.locator(".p4-usages [role=treeitem][aria-level='2']").first.wait_for()
        head = self.text_until(".p4-usages .p4-calls-head", "indexing", "the indexing banner")
        self.assertIn("the graph shows callers meanwhile", head)
        self.assertIn("call graph", head)
        self.palette("Find Callees")
        self.text_until(".p4-usages .p4-calls-head", "need the language server", "callees' reason")
        self.assertEqual(0, p.locator(".p4-usages [role=treeitem][aria-level='2']").count())


@_lsp_flow
class HubRendersBounded(H.IdeFlow):
    KNOBS = ("--index-seconds", "0.1", "--hub", "50000", "--hub-file", "shop/hub.py")

    def test_1_fifty_thousand_callers_render_two_thousand_and_say_so(self):
        p = self.page
        self.open_file("shop/text.py")
        self.row(1).click()
        self.palette("Find Callers")
        self.text_until(".p4-usages .p4-calls-head", "showing 2,000 of 50,000", "the cut sentence")
        self.assertEqual(2000, p.locator(".p4-usages [role=treeitem][aria-level='2']").count())
        self.assertEqual("50000", p.locator(".p4-usages [role=treeitem][aria-level='2']").first.get_attribute("aria-setsize"))


if __name__ == "__main__":
    unittest.main()
