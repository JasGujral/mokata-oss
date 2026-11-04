"""IDE e2e — the change explainer (0.0.27 P46, CHANGE-EXPLAINER).

On its own fixture (the P46 shop: a session S1 whose change adds a branchy discount function, a
module edge, a pattern deviation and an AC contradiction; plus a committed range whose diff holds
a `<script>`): the Explain screen lists the stored pages, shows one in a frame that can run
nothing, draws its diagrams as images, follows a cite into the Code view, refuses a page changed
on disk, exports a page with zero scripts, and "Explain changes" on the live session queues the
request for its next turn. mokata calls no model; no API key anywhere.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import sys
import time
import unittest

import _harness as H

TESTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build(base):
    """The P46 fixture, two stored pages (session S1, and a range whose diff holds a script) and a
    third page rewritten on disk after it was validated (a meta refresh, a script, a form)."""
    if TESTS not in sys.path:
        sys.path.insert(0, TESTS)
    if H.SRC not in sys.path:
        sys.path.insert(0, H.SRC)
    import _explain_fixture as F
    from mokata import session_index as SI
    from mokata.explain import bundle as B
    from mokata.explain import store as ST
    repo = os.path.join(base, "p46-shop")
    os.makedirs(repo)
    F.build_repo(repo)

    def save(target, sections):
        built = B.build(repo, B.parse_target(target))
        ST.cache_bundle(repo, built)
        h = built["bundle"]["content_hash"]
        pages = ST.page_view(ST.load_bundle(repo, h), 1)["pages"]
        for p in range(1, pages + 1):
            ST.mark_served(repo, h, p)
        doc = sections(built["bundle"], pages) if callable(sections) else {
            "content_hash": h, "model": "m", "pages_read": list(range(1, pages + 1)),
            "sections": sections}
        return ST.save(repo, doc, claude_version="2.1.0")

    s1 = save("session S1", F.claude_doc)
    before_land = B.rev(repo, "HEAD")
    F.git(repo, "add", "-A")
    F.git(repo, "commit", "-qm", "land the session")
    base_sha = B.rev(repo, "HEAD")
    # The landed range: `legacy_total` lives only at base, pricing.py only at head. Its base cites
    # must open the BASE commit (ruling 2). The second base cite's sidecar entry is then pointed at
    # pricing.py — a file the change ADDED — to drive the "no base side" branch.
    landed = save(f"range {before_land} {base_sha}", [{"id": "summary", "blocks": [
        {"kind": "prose", "text": "legacy_total was removed",
         "cites": ["base:src/shop/cart.py:8-9", "fact:s:base:src/shop/cart.py::legacy_total"]},
        {"kind": "prose", "text": "the import line before", "cites": ["base:src/shop/cart.py:1-1"]}]}])
    sc_path = os.path.join(repo, ".mokata", "temp_local", "reviews",
                           *landed["review_id"].split("/")) + ".json"
    with open(sc_path, encoding="utf-8") as fh:
        sc = json.load(fh)
    for c in sc["cites"]:
        if c.get("cite") == "base:src/shop/cart.py:1-1":
            c["path"] = "src/shop/pricing.py"
    with open(sc_path, "w", encoding="utf-8") as fh:
        json.dump(sc, fh)
    # The same page with its base commit GONE (pruned / past a shallow boundary): the base side
    # is "unavailable" — not "the change added it", and never head.
    gone = save(f"range {before_land} {base_sha}", [{"id": "summary", "blocks": [
        {"kind": "prose", "text": "legacy_total, base commit gone",
         "cites": ["base:src/shop/cart.py:8-9"]}]}])
    g_path = os.path.join(repo, ".mokata", "temp_local", "reviews",
                          *gone["review_id"].split("/")) + ".json"
    with open(g_path, encoding="utf-8") as fh:
        sc = json.load(fh)
    sc["target"]["base_sha"] = "0123456789abcdef" * 2 + "01234567"
    with open(g_path, "w", encoding="utf-8") as fh:
        json.dump(sc, fh)
    # A committed range whose change holds markup: shown as text, never run.
    F.write(repo, "src/shop/banner.py",
            "HTML = \"<script>document.title='PWNED'</script><img src=x onerror=alert(1)>\"\n")
    F.git(repo, "add", "-A")
    F.git(repo, "commit", "-qm", "banner")
    head_sha = B.rev(repo, "HEAD")
    banner = save(f"range {base_sha} {head_sha}", [{"id": "summary", "blocks": [
        {"kind": "code", "cites": ["src/shop/banner.py:1"]},
        {"kind": "prose", "text": "adds a banner constant", "cites": ["src/shop/banner.py:1"]}]}])
    # A page rewritten on disk after it was checked: never served.
    bad = save(f"range {base_sha} {head_sha}", [{"id": "summary", "blocks": [
        {"kind": "prose", "text": "to be tampered with", "cites": ["src/shop/banner.py:1"]}]}])
    p = os.path.join(repo, ".mokata", "temp_local", "reviews", *bad["review_id"].split("/")) + ".html"
    with open(p, encoding="utf-8") as fh:
        html = fh.read()
    html = html.replace("<head>", '<head><meta http-equiv="refresh" content="0;url=https://evil.example/r">'
                                  '<base href="https://evil.example/">')
    html = html.replace("</main>", "<script>document.title='PWNED'</script>"
                                   '<a id="js" href="javascript:document.title=\'PWNED\'">x</a>'
                                   '<form action="https://evil.example/f"><button id="fb">go</button></form>'
                                   '<img src="https://evil.example/i"></main>')
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(html)
    with open(os.path.join(base, "p46-ids.json"), "w", encoding="utf-8") as fh:
        json.dump({"s1": s1["review_id"], "bad": bad["review_id"],
                   "landed": landed["review_id"], "base": before_land,
                   "banner": banner["review_id"], "gone": gone["review_id"]}, fh)
    # S1 is live: recent activity (its process cannot be checked here).
    SI.on_start(repo, "S1", "startup", repo, t=time.time(), proc=None)
    return repo


class TheExplainerShowsCitedPagesSafely(H.IdeFlow):
    FIXTURE = staticmethod(build)

    @classmethod
    def _expected_console(cls, text):
        # The frame's sandbox refusing a script IS the property under test; Chromium reports it
        # as a console error. Only those exact refusals are expected; anything else still reds.
        return ("Blocked script execution" in text and "sandboxed" in text) or \
            super()._expected_console(text)

    def ids(self):
        with open(os.path.join(self.tmp, "p46-ids.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def frame(self):
        return self.page.frame_locator("#view-explain iframe.xp-frame")

    def open_review(self, rid):
        p = self.page
        p.evaluate("(id) => MK.explain.open(id)", rid)
        p.wait_for_selector("#view-explain iframe.xp-frame")
        self.until("(() => { const f = document.querySelector('#view-explain iframe.xp-frame');"
                   " return !!(f && f.contentDocument && f.contentDocument.querySelector('main')); })()",
                   "the page to load in the frame")

    def test_1_the_screen_lists_the_pages(self):
        p = self.page
        self.go("explain")
        p.wait_for_selector("#view-explain:not([hidden])")
        p.wait_for_selector("#panel-explain .xp-row")
        self.assertGreaterEqual(p.locator("#panel-explain .xp-row").count(), 1)
        self.assertIn("store:", p.text_content("#panel-explain"))

    def test_2_the_frame_can_run_nothing_and_draws_diagrams_as_images(self):
        p = self.page
        self.open_review(self.ids()["s1"])
        sandbox = p.get_attribute("#view-explain iframe.xp-frame", "sandbox")
        self.assertEqual("allow-same-origin", sandbox)
        self.assertNotIn("allow-scripts", sandbox)
        self.until("document.querySelector('#view-explain iframe.xp-frame').contentDocument"
                   ".querySelectorAll('img.diagram-img').length >= 3", "three diagrams drawn")
        srcs = p.evaluate("[...document.querySelector('#view-explain iframe.xp-frame').contentDocument"
                          ".querySelectorAll('img.diagram-img')].map(i => i.src.slice(0, 26))")
        self.assertTrue(all(s.startswith("data:image/svg+xml") for s in srcs), srcs)
        self.assertEqual(0, p.evaluate("document.querySelector('#view-explain iframe.xp-frame')"
                                       ".contentDocument.querySelectorAll('script').length"))
        self.assertIn("UNTESTED", self.frame().locator("body").text_content())
        self.assertIn("claim(s) dropped", p.text_content("#view-explain .xp-dropped summary"))

    def test_3_a_cite_opens_the_code_view_and_the_frame_never_navigates(self):
        p = self.page
        self.frame().locator("button.cite", has_text="src/shop/pricing.py").first.click()
        p.wait_for_selector("#view-navigate:not([hidden])")
        self.until("!!(window.MK && MK.nav && MK.nav.path === 'src/shop/pricing.py')",
                   "pricing.py to open from its cite")

    def test_4_a_script_in_the_diff_is_text(self):
        p = self.page
        self.go("explain")
        p.wait_for_selector("#panel-explain .xp-row")
        self.open_review(self.ids()["banner"])
        self.until("(() => { const f = document.querySelector('#view-explain iframe.xp-frame');"
                   " const d = f && f.contentDocument; return !!(d && d.body &&"
                   " d.body.textContent.includes(\"<script>document.title='PWNED'\")); })()",
                   "the diff's script shown as text")
        doc_title = p.evaluate("document.querySelector('#view-explain iframe.xp-frame').contentDocument.title")
        self.assertNotIn("PWNED", doc_title)
        self.assertNotIn("PWNED", p.title())
        self.assertEqual(0, p.evaluate("document.querySelector('#view-explain iframe.xp-frame')"
                                       ".contentDocument.querySelectorAll('script,img:not(.diagram-img)').length"))

    def test_5_a_page_changed_on_disk_is_refused_and_reaches_nothing(self):
        p = self.page
        before = len(self.requests)
        self.open_review(self.ids()["bad"])
        self.until("document.querySelector('#view-explain iframe.xp-frame').contentDocument"
                   ".body.textContent.includes('This page is not shown')", "the refusal notice")
        p.wait_for_timeout(1500)            # a meta refresh would have fired by now
        url = p.evaluate("document.querySelector('#view-explain iframe.xp-frame').contentWindow.location.pathname")
        self.assertEqual("/api/explain/page", url)
        self.assertEqual([], [u for _m, u in self.requests[before:] if "evil.example" in u])
        self.assertNotIn("PWNED", p.title())

    def test_6_export_has_zero_scripts_and_a_no_network_policy(self):
        p = self.page
        self.open_review(self.ids()["s1"])
        self.until("document.querySelector('#view-explain iframe.xp-frame').contentDocument"
                   ".querySelectorAll('img.diagram-img').length >= 3", "diagrams before export")
        with p.expect_download() as dl:
            p.locator("#view-explain .xp-btn", has_text="Export").click()
        path = dl.value.path()
        with open(path, encoding="utf-8") as fh:
            html = fh.read()
        self.assertNotIn("<script", html.lower())
        self.assertIn("default-src 'none'", html)
        self.assertIn("data:image/svg+xml", html)

    def test_8_a_base_cite_opens_the_base_commit_read_only_never_head(self):
        p = self.page
        ids = self.ids()
        sh = ids["base"][:12]
        nav_before = p.evaluate("(window.MK && MK.nav && MK.nav.path) || ''")
        self.open_review(ids["landed"])
        self.frame().locator("button.cite", has_text="base:src/shop/cart.py:8-9").first.click()
        self.text_until("#view-explain .xp-base", "def legacy_total(items):", "the base lines")
        box = p.text_content("#view-explain .xp-base")
        self.assertIn("base @ " + sh, box)
        self.assertIn("read-only", box)
        self.assertNotIn("def validate", box)          # what head holds at those line numbers
        self.assertEqual(2, p.locator("#view-explain .xp-base tr.hit").count())
        self.assertTrue(p.is_visible("#view-explain"))
        self.assertEqual(nav_before, p.evaluate("(window.MK && MK.nav && MK.nav.path) || ''"))
        # a symbol that lives only at base is a base cite too
        self.frame().locator("button.cite", has_text="s:base:src/shop/cart.py::legacy_total").first.click()
        box = self.text_until("#view-explain .xp-base", "def legacy_total(items):", "the base symbol")
        self.assertIn("src/shop/cart.py:8 · read-only", box)
        # a file the change added has no base side: said, and head is NOT shown instead
        self.frame().locator("button.cite", has_text="base:src/shop/cart.py:1-1").first.click()
        self.text_until("#view-explain .xp-base", "did not exist at base @ " + sh, "the no-base notice")
        box = p.text_content("#view-explain .xp-base")
        self.assertNotIn("def apply_discount", box)
        self.assertEqual(0, p.locator("#view-explain .xp-base table").count())
        self.assertEqual(nav_before, p.evaluate("(window.MK && MK.nav && MK.nav.path) || ''"))
        # a base commit that is not in the repository: said as unavailable, never "added", never head
        self.open_review(ids["gone"])
        self.frame().locator("button.cite", has_text="base:src/shop/cart.py:8-9").first.click()
        box = self.text_until("#view-explain .xp-base", "is not in this repository",
                              "the base-unavailable notice")
        self.assertIn("pruned, or past a shallow clone", box)
        self.assertNotIn("the change added it", box)
        self.assertNotIn("def ", box)
        self.assertEqual(0, p.locator("#view-explain .xp-base table").count())
        self.assertEqual(nav_before, p.evaluate("(window.MK && MK.nav && MK.nav.path) || ''"))

    def test_7_explain_changes_on_a_live_session_queues_it_for_the_next_turn(self):
        p = self.page
        self.go("explain")
        p.evaluate("MK.ui.focus = 'S1'")
        p.select_option("#panel-explain select.xp-input", "session")
        p.locator("#panel-explain button.xp-go").click()
        self.text_until("#panel-explain .xp-reqs", "queued", "the request queued for S1's next turn")
        self.assertIn("next turn", p.text_content("#panel-explain .xp-reqs"))
        self.assertEqual([], [u for _m, u in self.requests if "anthropic" in u])


if __name__ == "__main__":
    unittest.main()
