"""0.0.27 P46 CHANGE-EXPLAINER — business tests. Each test's docstring is the user-visible rule.

mokata never calls a model: Claude Code (on the user's own login) asks `explain_bundle` for the
FACTS of one change set, writes the narrative, and submits it through `explain_save`, which keeps
only what is cited, renders a page with zero scripts, and stores it under gitignored
`.mokata/temp_local/reviews/`. The IDE lists, shows (sandboxed) and hands requests off; it never
calls a model either.

The fixture (`tests/_explain_fixture.py`) is the §2a done-when: a call-path change, a branchy
function, a module edge, an injected error-handling pattern deviation, an injected AC
contradiction (BIG subtracts 50: AC-1 says never negative), and an untested edge case.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import ast
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SRC = os.path.join(os.path.dirname(HERE), "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import _support  # noqa: F401,E402

import _explain_fixture as F  # noqa: E402

from mokata.explain import bundle as B  # noqa: E402
from mokata.explain import ground as G  # noqa: E402
from mokata.explain import render as R  # noqa: E402
from mokata.explain import store as ST  # noqa: E402
from mokata.explain.safedir import UnsafeStore, open_chain  # noqa: E402
from mokata.repo_paths import name_of  # noqa: E402

PKG = os.path.join(SRC, "mokata")
# A secret built by concatenation so no literal key shape sits in this file.
FAKE_KEY = "AK" + "IA" + "IOSFODNN7" + "EXAMPLE"


def _serve_all(root: str, built: dict) -> int:
    """Cache a built bundle and mark every page served (what explain_bundle does as Claude reads)."""
    ST.cache_bundle(root, built)
    h = built["bundle"]["content_hash"]
    rec = ST.load_bundle(root, h)
    pages = ST.page_view(rec, 1)["pages"]
    for p in range(1, pages + 1):
        ST.mark_served(root, h, p)
    return pages


def _fixture_repo() -> str:
    d = tempfile.mkdtemp(prefix="p46-")
    F.build_repo(d)
    return d


def _plain_repo(files: dict) -> str:
    """A mokata repo with one base commit of `files` (no run, no session)."""
    from mokata.init import init_repo
    d = tempfile.mkdtemp(prefix="p46p-")
    F.git(d, "init", "-q", "-b", "main")
    F.git(d, "config", "user.email", "t@t")
    F.git(d, "config", "user.name", "t")
    F.git(d, "config", "commit.gpgsign", "false")
    init_repo(root=d, profile="minimal", assume_yes=True, out=F._silent)
    for rel, text in files.items():
        F.write(d, rel, text)
    F.git(d, "add", "-A")
    F.git(d, "commit", "-qm", "base")
    return d


def _head(d: str) -> str:
    return B.rev(d, "HEAD")


def _rm(d: str) -> None:
    shutil.rmtree(d, ignore_errors=True)


# ===================================================================================== fixture

class TheFixtureDoneWhen(unittest.TestCase):
    """§2a: the page explains the fixture change with every diagram, both injected discrepancies
    (cited) and the untested case flagged — and drops every claim it cannot ground."""

    @classmethod
    def setUpClass(cls):
        cls.d = _fixture_repo()
        cls.built = B.build(cls.d, B.parse_target("session S1"))
        cls.bundle = cls.built["bundle"]
        cls.pages = _serve_all(cls.d, cls.built)
        cls.out = ST.save(cls.d, F.claude_doc(cls.bundle, cls.pages), claude_version="2.1.0")
        cls.html = ST.read_page(cls.d, cls.out["review_id"]).decode("utf-8")
        cls.sc = ST.read_sidecar(cls.d, cls.out["review_id"])
        cls.facts = {f["id"]: f for f in cls.bundle["facts"]}

    @classmethod
    def tearDownClass(cls):
        _rm(cls.d)

    def test_page_has_a_flow_diagram_a_flowchart_and_an_architecture_diff(self):
        """The page carries the call path before → after, a decision flowchart for the function
        whose complexity rose, and the module edges added/removed — each drawn exactly once."""
        caps = [d["caption"] for d in self.sc["diagrams"]]
        self.assertEqual(1, sum("call path" in c for c in caps), caps)
        self.assertEqual(1, sum("module edges" in c for c in caps), caps)
        self.assertEqual(1, sum(c == "decision flow" for c in caps), caps)
        self.assertEqual(3, len(caps), caps)
        self.assertEqual(len(caps), self.html.count('class="diagram" data-diagram='))
        # A fact diagram Claude refers to in an EARLIER section is drawn there, and the section
        # that owns it does not draw it again.
        _html, _c, early = R.render(self.bundle, [{"id": "summary", "blocks": [
            {"kind": "use_fact_diagram", "diagram": "flow", "text": "", "cites": []}]}],
            {"generated_at": "t", "dropped": 0})
        self.assertEqual(2, len(early), [d["caption"] for d in early])
        arch = self.html.split('id="architecture"')[1].split("</section>")[0]
        self.assertIn("src/shop/cart.py] --&gt;|added| n1[shop.pricing]", arch)
        flow = self.html.split('id="flow"')[1].split("</section>")[0]
        self.assertIn("legacy_total] -.-&gt;|removed|", flow)
        self.assertEqual(9, self.facts["c:src/shop/pricing.py::apply_discount"]["after"])

    def test_the_pattern_deviation_and_the_AC_contradiction_are_reported_with_cites(self):
        """A discrepancy cites BOTH sides: the changed line and the pattern / AC outside it."""
        self.assertIn("p:1", self.facts)
        self.assertIn("error handling differs", self.facts["p:1"]["what"])
        cited = {c.get("fact") for c in self.sc["cites"]}
        self.assertIn("p:1", cited)
        self.assertIn("ac:AC-1", cited)
        self.assertIn("contradicting AC-1", self.html)
        self.assertIn("validate raises ValueError", self.html)
        self.assertEqual(2, self.html.count("discrepancy · "))

    def test_the_untested_edge_case_is_flagged_UNTESTED(self):
        """An edge case with no test fact covering it is shown as UNTESTED, never as covered."""
        self.assertIn("PCT150", self.html)
        self.assertIn("UNTESTED — no test fact covers this case", self.html)
        self.assertEqual("untested", self.facts["ac:AC-1"]["status"])

    def test_a_removed_function_lists_the_callers_it_leaves_behind(self):
        """Removing `legacy_total` names every remaining reference to it (report.py), counted."""
        r = self.facts["r:head:legacy_total"]
        self.assertTrue(r["removed"])
        self.assertEqual(2, r["count"])
        self.assertEqual({"src/shop/report.py"}, {x[0] for x in r["refs"]})
        self.assertIn("lexical", r["basis"])

    def test_every_claim_without_a_resolvable_cite_is_dropped_and_recorded(self):
        """Uncited, one-sided, a traversal path and a too-wide span are DROPPED, each recorded in
        the sidecar with its reason — the page says how many were dropped."""
        self.assertEqual((10, 4), (self.out["kept"], self.out["dropped"]))
        reasons = " | ".join(x["reason"] for x in self.sc["dropped"])
        for want in ("uncited", "other side", "unsafe or refused path", "wider than 120"):
            self.assertIn(want, reasons)
        self.assertIn("4 claim(s) were dropped", self.html)
        self.assertNotIn("cites nothing and must be dropped", self.html)

    def test_the_page_has_zero_scripts_and_passes_the_positive_allowlist(self):
        """The stored page runs nothing: no <script>, no handler, no link — and re-validates clean."""
        self.assertNotIn("<script", self.html.lower())
        self.assertEqual([], R.validate(self.html))
        self.assertNotIn(" href=", self.html)
        self.assertNotIn(" on", self.html.split("<body>")[1].split(">")[0])

    def test_the_page_is_in_session_history_with_its_sidecar_and_ledger_row(self):
        """The page appears in the session's history (fresh), has its sidecar, and one
        `change_explained` ledger row records it."""
        lr = ST.list_reviews(self.d, "S1")
        self.assertEqual([self.out["review_id"]], [r["id"] for r in lr["reviews"]])
        self.assertEqual("fresh", lr["reviews"][0]["state"])
        from mokata.ide import activity as A
        page = A.history(self.d, "S1")
        self.assertIn(f"expl:{self.out['review_id']}", [i.get("id") for i in page["items"]])
        ledger = os.path.join(self.d, ".mokata", "temp_local", "audit", "ledger.jsonl")
        with open(ledger, encoding="utf-8") as fh:
            rows = [json.loads(x) for x in fh if "change_explained" in x]
        self.assertEqual(1, len(rows))
        self.assertIn(self.out["review_id"], json.dumps(rows[0]))

    def test_cites_resolve_only_through_the_closed_kept_list(self):
        """Every cite button on the page is an index into the sidecar's kept-cite list."""
        import re
        ks = {int(k) for k in re.findall(r'data-cite="(\d+)"', self.html)}
        self.assertEqual(set(range(len(self.sc["cites"]))), ks)


# ================================================================================ determinism

class TheBundleIsDeterministic(unittest.TestCase):

    def test_the_content_hash_ignores_test_runs_and_gate_rows(self):
        """Two builds of an unchanged change set hash the same, even after a new test run — so a
        page is not called stale because a test re-ran."""
        d = _fixture_repo()
        try:
            h1 = B.build(d, B.parse_target("session S1"))["bundle"]["content_hash"]
            from mokata import test_runs as TR
            TR.record_test_run(d, "r1", passed=False, command="x",
                               tests=["tests/test_cart.py::test_no_code"],
                               ac_map={"AC-2": ["tests/test_cart.py::test_no_code"]}, at=3000.0)
            h2 = B.build(d, B.parse_target("session S1"))["bundle"]["content_hash"]
            self.assertEqual(h1, h2)
            F.write(d, "src/shop/pricing.py", F.PRICING_NEW + "\n# changed\n")
            h3 = B.build(d, B.parse_target("live"))["bundle"]["content_hash"]
            self.assertNotEqual(h1, h3)
        finally:
            _rm(d)


# ================================================================================== grounding

class GroundingDropsWhatItCannotResolve(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.d = _fixture_repo()
        cls.built = B.build(cls.d, B.parse_target("session S1"))
        cls.bundle = cls.built["bundle"]
        ST.cache_bundle(cls.d, cls.built)
        cls.rec = ST.load_bundle(cls.d, cls.bundle["content_hash"])

    @classmethod
    def tearDownClass(cls):
        _rm(cls.d)

    def res(self, pages=(1,)):
        return G.Resolver(self.bundle, self.rec["blobs"], set(pages), self.rec["page_of"],
                          lambda sha, rel: None)

    def drop(self, block, pages=(1,)):
        kept, why = G.check_block(block, self.res(pages), R.mermaid_problem)
        self.assertIsNone(kept, f"expected a drop: {block}")
        return why

    def test_a_fact_on_a_page_never_read_is_dropped(self):
        """A cite into a bundle page Claude never fetched is dropped."""
        self.assertIn("not read", self.drop(
            {"kind": "prose", "text": "x", "cites": ["fact:p:1"]}, pages=()))

    def test_a_base_cite_without_a_base_commit_is_dropped(self):
        """A session with no base commit cannot be cited at base."""
        self.assertIn("no base commit", self.drop(
            {"kind": "prose", "text": "x", "cites": ["base:src/shop/cart.py:1-2"]}))

    def test_a_flowchart_must_cite_the_complexity_fact(self):
        """A flowchart cites the c: fact of the function it draws, or it is dropped."""
        self.assertIn("complexity fact", self.drop(
            {"kind": "flowchart", "text": "x", "mermaid": "flowchart TD\n a --> b\n",
             "cites": ["fact:p:1"]}))

    def test_a_flowchart_with_mermaid_directives_is_dropped(self):
        """Only plain nodes and edges: `click`, `style`, directives and HTML are refused."""
        for bad in ("flowchart TD\n a --> b\n click a call x()\n", "%%{init: {}}%%\nflowchart TD\n",
                    "flowchart TD\n a[<img src=x>] --> b\n", "flowchart TD\n style a fill:#f00\n"):
            self.assertIn("mermaid refused", self.drop(
                {"kind": "flowchart", "text": "x", "mermaid": bad,
                 "cites": ["fact:c:src/shop/pricing.py::apply_discount"]}), bad)

    def test_covered_by_must_name_a_test_fact(self):
        """An edge case claims coverage only by naming a t: test fact — anything else is dropped."""
        self.assertIn("covered_by", self.drop(
            {"kind": "edge_case", "text": "x", "cites": ["src/shop/pricing.py:4"],
             "covered_by": "ac:AC-2"}))
        kept, _ = G.check_block({"kind": "edge_case", "text": "x", "cites": [
            "src/shop/pricing.py:4"], "covered_by": "t:tests/test_cart.py::test_no_code"},
            self.res(), R.mermaid_problem)
        self.assertEqual("t:tests/test_cart.py::test_no_code", kept["covered_by"])

    def test_a_wide_span_must_sit_inside_one_symbol_even_in_a_new_file(self):
        """More than 8 lines must lie inside ONE symbol; a whole new file is never one cite."""
        self.assertIn("not inside one symbol", self.drop(
            {"kind": "prose", "text": "x", "cites": ["src/shop/cart.py:1-12"]}))
        kept, _ = G.check_block({"kind": "prose", "text": "x", "cites": [
            "src/shop/pricing.py:1-13"]}, self.res(), R.mermaid_problem)
        self.assertIsNotNone(kept, "apply_discount spans 1-13: inside one symbol")
        self.assertIn("wider than 120", self.drop(
            {"kind": "prose", "text": "x", "cites": ["src/shop/pricing.py:1-121"]}))

    def test_paths_that_escape_or_hide_are_refused(self):
        """Traversal, absolute, backslash, .git, .mokata, drive and reserved names are refused."""
        for p in ("../x.py", "/etc/passwd", "a\\b.py", ".git/config", ".mokata/manifest.json",
                  "C:/x.py", "src/CON.py", "src/./x.py"):
            self.assertIsNone(G.safe_rel(p), p)
        self.assertEqual("src/shop/cart.py", G.safe_rel("src/shop/cart.py"))

    def test_an_unknown_bundle_refuses_the_whole_save(self):
        """A document for a bundle mokata never served is refused, not partially saved."""
        with self.assertRaises(ST.SaveRefused):
            ST.save(self.d, {"content_hash": "0" * 64, "sections": []})


# ================================================================================= the page

class ThePageRunsNothing(unittest.TestCase):

    def test_the_validator_rejects_every_way_a_page_could_act(self):
        """A page with a script, a handler, a link, a meta refresh, a base, a form, an iframe, a
        comment or an @import is refused — the allowlist names what may appear, not what may not."""
        ok = ('<!doctype html><html lang="en"><head><title>t</title><style>p{}</style></head>'
              "<body><main><p>x</p></main></body></html>")
        self.assertEqual([], R.validate(ok))
        bad = ['<script>1</script>', '<p onclick="x">a</p>', '<a href="https://x">a</a>',
               '<meta http-equiv="refresh" content="0;url=//x">', '<base href="//x">',
               '<form action="//x"><button>a</button></form>', '<iframe src="//x"></iframe>',
               "<!-- x -->", "<style>@import url(//x);</style>", '<img src="//x">',
               '<p style="background:url(//x)">a</p>', '<svg><use href="#x"/></svg>']
        for b in bad:
            page = ok.replace("<p>x</p>", b)
            self.assertNotEqual([], R.validate(page), b)
        for css in ("p{background:url(//x)}", "@import '//x';", "p{behavior:url(x.htc)}"):
            self.assertNotEqual([], R.validate(ok.replace("p{}", css)), css)

    def test_a_script_in_the_diff_is_shown_escaped_and_never_runs(self):
        """Code in the change that looks like HTML is text on the page: `<script>` stays inert."""
        d = _plain_repo({"web/page.py": "X = 1\n"})
        try:
            base = _head(d)
            F.write(d, "web/page.py", 'X = 1\nHTML = "<script>alert(1)</script><img src=x '
                                      'onerror=alert(2)>"\n')
            F.git(d, "commit", "-qam", "change")
            spec = B.parse_target(f"range {base} {_head(d)}")
            built = B.build(d, spec)
            pages = _serve_all(d, built)
            out = ST.save(d, {"content_hash": built["bundle"]["content_hash"], "model": "m",
                              "pages_read": list(range(1, pages + 1)),
                              "sections": [{"id": "summary", "blocks": [
                                  {"kind": "code", "cites": ["web/page.py:2"]}]}]})
            html = ST.read_page(d, out["review_id"]).decode("utf-8")
            self.assertNotIn("<script", html.lower())
            self.assertNotIn("<img", html.lower())
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
            self.assertEqual([], R.validate(html))
        finally:
            _rm(d)

    def test_the_page_names_itself_inferred_and_self_reported(self):
        """The page says which parts are inferred by Claude and that the model name is
        self-reported — facts drawn by mokata are labelled as such."""
        d = _fixture_repo()
        try:
            built = B.build(d, B.parse_target("session S1"))
            pages = _serve_all(d, built)
            out = ST.save(d, F.claude_doc(built["bundle"], pages))
            html = ST.read_page(d, out["review_id"]).decode("utf-8")
            self.assertIn(R.BANNER.split("—")[0].strip()[:30], html)
            self.assertIn(R.SELF_REPORTED, html)
            self.assertIn("drawn by mokata from the facts", html)
        finally:
            _rm(d)


# ================================================================================== secrets

class SecretsNeverReachThePage(unittest.TestCase):

    def test_a_secret_in_the_change_is_redacted_everywhere(self):
        """A key in a changed line never reaches the bundle, the page or the sidecar; a sensitive
        file (.env) is listed but never shown."""
        d = _plain_repo({"app/conf.py": "A = 1\n"})
        try:
            base = _head(d)
            F.write(d, "app/conf.py", f'A = 1\nKEY = "{FAKE_KEY}"\n')
            F.write(d, ".env", f"AWS_ACCESS_KEY_ID={FAKE_KEY}\n")
            F.git(d, "add", "-A", "-f")
            F.git(d, "commit", "-qm", "secret")
            spec = B.parse_target(f"range {base} {_head(d)}")
            built = B.build(d, spec)
            dump = json.dumps(built)
            self.assertNotIn(FAKE_KEY, dump)
            env = [f for f in built["bundle"]["files"] if f["path"] == ".env"]
            self.assertEqual(1, len(env))
            self.assertIn("never shown", env[0]["shown"])
            pages = _serve_all(d, built)
            out = ST.save(d, {"content_hash": built["bundle"]["content_hash"], "model": "m",
                              "pages_read": list(range(1, pages + 1)),
                              "sections": [{"id": "summary", "blocks": [
                                  {"kind": "code", "cites": ["app/conf.py:2"]},
                                  {"kind": "prose", "text": f"the key is {FAKE_KEY}",
                                   "cites": ["app/conf.py:2"]},
                                  {"kind": "prose", "text": f"uncited {FAKE_KEY}"},
                                  {"kind": "prose", "text": "env", "cites": [".env:1"]}]}]})
            html = ST.read_page(d, out["review_id"]).decode("utf-8")
            sc = json.dumps(ST.read_sidecar(d, out["review_id"]))
            self.assertNotIn(FAKE_KEY, html)
            self.assertNotIn(FAKE_KEY, sc)
            self.assertIn("refused", " ".join(x["reason"] for x in json.loads(sc)["dropped"]))
        finally:
            _rm(d)


# ================================================================================= staleness

class PagesGoStaleWhenTheChangeMoves(unittest.TestCase):

    def _saved(self, d, target):
        built = B.build(d, B.parse_target(target))
        pages = _serve_all(d, built)
        out = ST.save(d, {"content_hash": built["bundle"]["content_hash"], "model": "m",
                          "pages_read": list(range(1, pages + 1)), "sections": [
                              {"id": "summary", "blocks": [{"kind": "prose", "text": "x",
                               "cites": ["fact:s:head:src/shop/cart.py::checkout"]}]}]})
        return out["review_id"]

    def test_a_new_commit_makes_the_page_stale(self):
        """After a new commit the session's page is listed as stale, with why."""
        d = _fixture_repo()
        try:
            rid = self._saved(d, "session S1")
            self.assertEqual("fresh", ST.list_reviews(d, "S1")["reviews"][0]["state"])
            F.git(d, "add", "-A")
            F.git(d, "commit", "-qm", "land it")
            row = ST.list_reviews(d, "S1")["reviews"][0]
            self.assertEqual((rid, "stale"), (row["id"], row["state"]))
            self.assertIn("head moved", row["why"])
        finally:
            _rm(d)

    def test_a_further_uncommitted_edit_makes_the_page_stale(self):
        """A page of the working tree (`live`) goes stale when the files are edited again,
        uncommitted."""
        d = _fixture_repo()
        try:
            self._saved(d, "live")
            sid = B.sid_of(B.parse_target("live"))
            self.assertEqual("fresh", ST.list_reviews(d, sid)["reviews"][0]["state"])
            F.write(d, "src/shop/cart.py", F.CART_NEW + "\n# more\n")
            row = ST.list_reviews(d, sid)["reviews"][0]
            self.assertEqual("stale", row["state"])
            self.assertIn("uncommitted", row["why"])
        finally:
            _rm(d)

    def test_an_unreadable_sidecar_is_listed_never_deleted(self):
        """A sidecar that cannot be read is listed as `unreadable — regenerate` and left on disk."""
        d = _fixture_repo()
        try:
            rid = self._saved(d, "session S1")
            p = os.path.join(d, ".mokata", "temp_local", "reviews", *rid.split("/")) + ".json"
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("{broken")
            row = ST.list_reviews(d, "S1")["reviews"][0]
            self.assertEqual("unreadable", row["state"])
            self.assertTrue(os.path.exists(p))
        finally:
            _rm(d)

    def test_a_range_page_goes_stale_when_its_ref_moves(self):
        """A page for `range base head` is stale once `head` points somewhere else."""
        d = _plain_repo({"a.py": "def f():\n    return 1\n"})
        try:
            F.git(d, "checkout", "-qb", "feat")
            F.write(d, "a.py", "def f():\n    return 2\n")
            F.git(d, "commit", "-qam", "c1")
            built = B.build(d, B.parse_target("range main feat"))
            pages = _serve_all(d, built)
            ST.save(d, {"content_hash": built["bundle"]["content_hash"], "model": "m",
                        "pages_read": list(range(1, pages + 1)), "sections": [
                            {"id": "summary", "blocks": [{"kind": "prose", "text": "x",
                                                          "cites": ["a.py:2"]}]}]})
            sid = B.sid_of(B.parse_target("range main feat"))
            self.assertEqual("fresh", ST.list_reviews(d, sid)["reviews"][0]["state"])
            F.write(d, "a.py", "def f():\n    return 3\n")
            F.git(d, "commit", "-qam", "c2")
            self.assertEqual("stale", ST.list_reviews(d, sid)["reviews"][0]["state"])
        finally:
            _rm(d)


# ==================================================================================== store

class TheStoreNeverFollowsALink(unittest.TestCase):

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="p46s-")
        self.out = tempfile.mkdtemp(prefix="p46o-")
        os.makedirs(os.path.join(self.d, ".mokata", "temp_local", "reviews"))

    def tearDown(self):
        _rm(self.d)
        _rm(self.out)

    def _link(self, *parts):
        p = os.path.join(self.d, *parts)
        shutil.rmtree(p, ignore_errors=True)
        os.symlink(self.out, p)

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_a_linked_store_component_is_refused_and_nothing_is_written_outside(self):
        """If `.mokata`, `temp_local`, `reviews` or the session folder is a symlink, the store
        refuses — and writes NOTHING where the link points."""
        for parts in ((".mokata",), (".mokata", "temp_local"),
                      (".mokata", "temp_local", "reviews"),
                      (".mokata", "temp_local", "reviews", "S1")):
            shutil.rmtree(os.path.join(self.d, ".mokata"), ignore_errors=True)
            if os.path.islink(os.path.join(self.d, ".mokata")):
                os.unlink(os.path.join(self.d, ".mokata"))
            os.makedirs(os.path.join(self.d, ".mokata", "temp_local", "reviews"), exist_ok=True)
            self._link(*parts)
            with self.assertRaises(UnsafeStore, msg=str(parts)):
                with open_chain(self.d, [".mokata", "temp_local", "reviews", "S1"]) as dd:
                    dd.write("x.json", b"{}")
            if len(parts) < 4:               # `_requests` sits beside `<sid>`, not under it
                with self.assertRaises(UnsafeStore):
                    ST.new_request(self.d, B.parse_target("session S1"), "terminal")
            self.assertEqual([], os.listdir(self.out), parts)
            os.unlink(os.path.join(self.d, *parts))

    def test_a_reparse_point_directory_is_refused(self):
        """A Windows junction (a directory that is a reparse point) is refused like a symlink."""
        import stat as _stat
        from types import SimpleNamespace
        from mokata.explain import safedir as SD
        fake = SimpleNamespace(st_mode=_stat.S_IFDIR | 0o755, st_file_attributes=0x400,
                               st_dev=1, st_ino=1, st_size=0, st_mtime=0)
        with open_chain(self.d, [".mokata"]) as dd:
            with mock.patch.object(SD.SafeDir, "_lstat", return_value=fake):
                with self.assertRaisesRegex(UnsafeStore, "is a link or not a directory"):
                    dd.sub("temp_local", create=False)

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_a_directory_swapped_for_a_link_after_opening_writes_nothing_outside(self):
        """The store holds the verified directory open: swapping it for a link afterwards cannot
        redirect a write outside the repository."""
        from mokata.explain import safedir as SD
        if not SD._DIR_FD:
            self.skipTest("no dir_fd on this platform (the path fallback is disclosed)")
        with open_chain(self.d, [".mokata", "temp_local", "reviews", "S1"]) as dd:
            self.assertIsNotNone(dd.fd)
            real = os.path.join(self.d, ".mokata", "temp_local", "reviews")
            os.rename(os.path.join(real, "S1"), os.path.join(real, "S1x"))
            os.symlink(self.out, os.path.join(real, "S1"))
            dd.write("x.json", b"{}")
        self.assertEqual([], os.listdir(self.out))
        self.assertTrue(os.path.exists(os.path.join(real, "S1x", "x.json")))

    def test_unsafe_store_names_are_refused_before_anything_is_opened(self):
        """A store path component or file name is a plain name: never `..`, a separator or empty."""
        for bad in ("..", ".", "", "a/b", "a\\b", ".hidden"):
            with self.assertRaises(UnsafeStore, msg=bad):
                open_chain(self.d, [".mokata", "temp_local", "reviews", bad])
        with open_chain(self.d, [".mokata", "temp_local", "reviews"]) as dd:
            for bad in ("..", "../x.json", "a/b"):
                with self.assertRaises(UnsafeStore, msg=bad):
                    dd.write(bad, b"x")

    def test_bad_session_ids_are_refused(self):
        """A session id is a plain name: no traversal, no reserved device name, bounded length."""
        for bad in ("..", "../x", "a/b", "CON", "nul", "x" * 200, "", ".hidden", "a b"):
            with self.assertRaises((UnsafeStore, ValueError), msg=bad):
                ST.check_sid(bad)
        self.assertEqual("S1", ST.check_sid("S1"))

    def test_bad_review_ids_are_refused(self):
        """A review id is `<sid>/<timestamp>` and nothing else."""
        for bad in ("S1", "S1/x", "../S1/20260101T000000.000000Z", "S1/../x", "S1/a/b"):
            with self.assertRaises(UnsafeStore, msg=bad):
                ST.check_review_id(bad)


class RetentionIsByCountAndVisible(unittest.TestCase):

    def test_old_pages_are_pruned_by_count_and_the_answer_says_so(self):
        """Only the newest KEEP_PAGES pages per session are kept; each save says how many it
        pruned, and the newest is never the one removed."""
        d = _fixture_repo()
        try:
            built = B.build(d, B.parse_target("session S1"))
            pages = _serve_all(d, built)
            doc = {"content_hash": built["bundle"]["content_hash"], "model": "m",
                   "pages_read": list(range(1, pages + 1)), "sections": [
                       {"id": "summary", "blocks": [{"kind": "prose", "text": "x",
                        "cites": ["fact:s:head:src/shop/cart.py::checkout"]}]}]}
            with mock.patch.object(ST, "KEEP_PAGES", 2):
                outs = [ST.save(d, doc, now=1_800_000_000.0 + i) for i in range(4)]
            self.assertEqual([0, 0, 1, 1], [o["pruned"] for o in outs])
            ids = [r["id"] for r in ST.list_reviews(d, "S1")["reviews"]]
            self.assertEqual([outs[3]["review_id"], outs[2]["review_id"]], ids)
        finally:
            _rm(d)

    def test_the_bundle_cache_is_bounded_per_session(self):
        """At most KEEP_BUNDLES cached bundles per session; caching says what it pruned."""
        d = _fixture_repo()
        try:
            pruned = 0
            with mock.patch.object(ST, "KEEP_BUNDLES", 1):
                for i in range(3):
                    F.write(d, "src/shop/pricing.py", F.PRICING_NEW + f"\n# v{i}\n")
                    rec = ST.cache_bundle(d, B.build(d, B.parse_target("live")))
                    pruned += rec["pruned"]
            self.assertEqual(2, pruned)
            names = os.listdir(os.path.join(d, ".mokata", "temp_local", "reviews", "_bundles"))
            self.assertEqual(1, len([n for n in names if not n.endswith(".read.json")]))
        finally:
            _rm(d)


# =================================================================================== runner

class TheBundleJobLivesOnlyWhilePolled(unittest.TestCase):

    def setUp(self):
        from mokata.explain import runner as RN
        self.RN = RN
        self.d = _fixture_repo()

    def tearDown(self):
        self.RN.shutdown()
        _rm(self.d)

    def _slow_build(self):
        from mokata.knowledge import cancel as C

        def slow(root, spec):
            for _ in range(400):
                C.checkpoint()
                time.sleep(0.01)
            raise AssertionError("the job was never cancelled")
        return slow

    def test_a_job_nobody_polls_cancels_itself(self):
        """Esc in Claude Code abandons the tool call; with no poll the job stops — no orphan."""
        with mock.patch.object(self.RN, "LEASE_S", 0.2), mock.patch.object(self.RN, "WATCH_S", 0.02), \
                mock.patch.object(B, "build", self._slow_build()):
            j = self.RN.start(self.d, B.parse_target("live"))
            j.wait(3.0)
            self.assertEqual("cancelled", j.state)
            self.assertIn("nobody polled", j.note)
            status = [s for s in ST.list_jobs(self.d) if s.get("id") == j.id]
            self.assertEqual("cancelled", status[0]["state"])

    def test_the_IDE_cancels_an_MCP_job_through_its_cancel_file(self):
        """Cancel in the IDE writes the job's `.cancel` flag; the MCP-side job stops."""
        with mock.patch.object(self.RN, "LEASE_S", 30), mock.patch.object(self.RN, "WATCH_S", 0.02), \
                mock.patch.object(B, "build", self._slow_build()):
            j = self.RN.start(self.d, B.parse_target("live"))
            ST.request_cancel(self.d, j.id)
            j.wait(3.0)
            self.assertEqual("cancelled", j.state)
            self.assertIn("IDE", j.note)

    def test_a_second_start_of_the_same_target_joins_the_running_job(self):
        """Asking twice for the same change set runs ONE job."""
        with mock.patch.object(self.RN, "WATCH_S", 0.02), \
                mock.patch.object(B, "build", self._slow_build()):
            a = self.RN.start(self.d, B.parse_target("live"))
            b = self.RN.start(self.d, B.parse_target("live"))
            self.assertIs(a, b)
            a.cancel("test over")
            a.wait(3.0)

    def test_an_unchanged_target_reuses_its_cached_bundle(self):
        """A second explain of an unchanged change set does not rebuild it."""
        j = self.RN.start(self.d, B.parse_target("session S1"))
        j.wait(30)
        self.assertEqual(("done", False), (j.state, j.result["cached"]))
        with mock.patch.object(B, "build", side_effect=AssertionError("rebuilt")):
            k = self.RN.start(self.d, B.parse_target("session S1"))
            k.wait(30)
        self.assertEqual(("done", True), (k.state, k.result["cached"]))


# ====================================================================================== MCP

class TheMcpToolsCarryTheWholeFlow(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.d = _fixture_repo()

    @classmethod
    def tearDownClass(cls):
        from mokata.explain import runner as RN
        RN.shutdown()
        _rm(cls.d)

    def test_bundle_then_save_then_a_gated_commit(self):
        """Claude reads the bundle page by page, saves a cited document, and committing the page
        into the repo is only PROPOSED until a human approves — nothing is written before."""
        from mokata.mcp.tools_explain import explain_bundle, explain_commit, explain_save
        a = explain_bundle(path=self.d, target="session S1")
        self.assertEqual("ready", a["status"], a)
        n = a["bundle"]["pages"]
        for p in range(2, n + 1):
            self.assertEqual("ready", explain_bundle(path=self.d, job=a["job"], page=p)["status"])
        doc = F.claude_doc(a["bundle"]["bundle"] if "bundle" in a["bundle"] else
                           {"content_hash": a["bundle"]["content_hash"]}, n)
        s = explain_save(path=self.d, document=json.dumps(doc))
        self.assertEqual("saved", s["status"], s)
        self.assertEqual(4, s["dropped"])
        c = explain_commit(path=self.d, review=s["review_id"], dest="docs/review.html")
        self.assertNotEqual("committed", c.get("status"))
        self.assertFalse(c.get("committed"))
        self.assertTrue(c.get("proposal_id"))
        self.assertFalse(os.path.exists(os.path.join(self.d, "docs", "review.html")))

    def test_the_tools_refuse_bad_input_with_a_message(self):
        """A PR target, an unknown job, a non-JSON document and a bad destination are refused."""
        from mokata.mcp.tools_explain import explain_bundle, explain_commit, explain_save
        self.assertEqual("refused", explain_bundle(path=self.d, target="pr 12")["status"])
        self.assertEqual("unknown_job", explain_bundle(path=self.d, job="nope")["status"])
        self.assertEqual("refused", explain_save(path=self.d, document="{x")["status"])
        self.assertEqual("refused", explain_save(path=self.d, document={"content_hash": "0"})
                         ["status"])
        for dest in ("../x.html", ".git/x.html", "docs/x.js", ".mokata/x.html"):
            self.assertEqual("refused", explain_commit(path=self.d, review="S1/x", dest=dest)
                             ["status"], dest)

    def test_a_repo_nobody_set_up_is_left_untouched(self):
        """In a repo without `.mokata/`, explaining refuses and says to run `mokata init` — it
        never creates the directory itself."""
        from mokata.mcp.tools_explain import explain_bundle
        d = tempfile.mkdtemp(prefix="p46u-")
        try:
            F.git(d, "init", "-q", "-b", "main")
            F.write(d, "a.py", "x = 1\n")
            a = explain_bundle(path=d, target="live")
            self.assertEqual("refused", a["status"], a)
            self.assertIn("mokata init", a["message"])
            self.assertFalse(os.path.exists(os.path.join(d, ".mokata")))
        finally:
            _rm(d)

    def test_the_tools_are_registered_read_read_write(self):
        """explain_bundle and explain_save are read-kind; explain_commit is the gated write."""
        from mokata.mcp.registry import TOOLS
        kinds = {t.name: t.kind for t in TOOLS} if not isinstance(TOOLS, dict) else \
            {k: v.kind for k, v in TOOLS.items()}
        self.assertEqual(("read", "read", "write"), (kinds["explain_bundle"],
                                                     kinds["explain_save"],
                                                     kinds["explain_commit"]))


# ====================================================================================== CLI

class TheCliOnlyPrintsTheCommand(unittest.TestCase):

    @mock.patch.dict(os.environ, {"MOKATA_VIA_PLUGIN": "1"})   # the namespaced plugin route
    def test_mokata_explain_prints_the_claude_command_and_spawns_nothing(self):
        """`mokata explain <target>` prints the `claude` command to run; it starts nothing and
        resolves a range to commit SHAs."""
        from mokata.cli_commands import explain as CE
        d = _plain_repo({"a.py": "x = 1\n"})
        try:
            F.git(d, "checkout", "-qb", "feat")
            F.write(d, "a.py", "x = 2\n")
            F.git(d, "commit", "-qam", "c")
            out, err = io.StringIO(), io.StringIO()
            import subprocess
            real = subprocess.Popen
            spawned = []

            def watch(argv, *a, **k):
                if "claude" in os.path.basename(str(argv[0] if isinstance(argv, list) else argv)):
                    spawned.append(argv)
                return real(argv, *a, **k)
            with mock.patch("subprocess.Popen", watch), \
                    redirect_stdout(out), redirect_stderr(err), \
                    mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("ANTHROPIC_API_KEY", None)
                rc = CE.cmd_explain(mock.Mock(path=d, target=["range", "main", "feat"]))
            self.assertEqual(0, rc)
            self.assertEqual([], spawned)
            line = out.getvalue().strip().splitlines()[-1]
            self.assertTrue(line.startswith('claude "/mokata:explain range '), line)
            self.assertRegex(line, r"range [0-9a-f]{40} [0-9a-f]{40}\"$")
            self.assertEqual("", err.getvalue())
        finally:
            _rm(d)

    def test_a_set_api_key_gets_the_billing_notice(self):
        """With ANTHROPIC_API_KEY set, the printed command comes with the billing notice."""
        from mokata.cli_commands import explain as CE
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), \
                mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "x"}):
            rc = CE.cmd_explain(mock.Mock(path=HERE, target=["live"]))
        self.assertEqual(0, rc)
        self.assertIn("ANTHROPIC_API_KEY", err.getvalue())

    def test_a_bad_target_is_refused(self):
        """A target that is not session/run/range/live exits 2 with a message."""
        from mokata.cli_commands import explain as CE
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            rc = CE.cmd_explain(mock.Mock(path=HERE, target=["pr", "1"]))
        self.assertEqual(2, rc)
        self.assertTrue(err.getvalue().strip())


# ================================================================================ terminal

class TheTerminalHandOffIsOneSafeArgument(unittest.TestCase):

    @mock.patch.dict(os.environ, {"MOKATA_VIA_PLUGIN": "1"})   # the namespaced plugin route
    def test_the_prompt_is_built_by_the_server_from_a_validated_target(self):
        """The embedded `claude` gets ONE argument, `/mokata:explain <target>`, from a parsed
        target — a range only as two SHAs, and no shell metacharacter can ride along."""
        from mokata.ide.terminal import TerminalRefused, explain_prompt
        sha = "a" * 40
        self.assertEqual(f"/mokata:explain range {sha} {'b' * 40}",
                         explain_prompt(f"range {sha} {'b' * 40}"))
        self.assertEqual("/mokata:explain session S1", explain_prompt("session S1"))
        for bad in ("range main HEAD", "session a&calc", "session a|b", "session $(x)",
                    "session a;b", "session a^b", "session %PATH%", "live\n/x", "pr 1",
                    'session "x"'):
            with self.assertRaises(TerminalRefused, msg=bad):
                explain_prompt(bad)

    @mock.patch.dict(os.environ, {"MOKATA_VIA_PLUGIN": "1"})   # the namespaced plugin route
    def test_the_explain_argument_is_appended_to_the_same_claude_program(self):
        """The hand-off reuses the terminal's `claude` program (its billing notice included) and
        appends one argument; it never resumes and never uses another program."""
        from mokata.ide import terminal as T
        # A REAL host: `__new__` skipped `__init__`, and the reaper thread `open` starts then died on
        # a missing `idle_secs` (an AttributeError on a background thread, seen only in the Windows
        # CI log). No thread of the host may die, and the host is closed so its reaper ends.
        died = []
        hook = threading.excepthook
        threading.excepthook = lambda args: died.append(repr(args.exc_value))
        self.addCleanup(setattr, threading, "excepthook", hook)
        host = T.TerminalHost(HERE, programs={"claude": lambda resume: ["/bin/claude"],
                                              "shell": lambda r: ["/bin/sh"]},
                              idle_secs=0.4, enabled=(True, ""))
        made = {}
        swept = threading.Event()
        real_reap = host.reap

        def counted_reap(*a, **k):
            try:
                return real_reap(*a, **k)
            finally:
                swept.set()
        host.reap = counted_reap             # the reaper loop calls self.reap: see its sweeps

        def fake(tid, program, argv, root, cols, rows):
            made["argv"], made["program"] = argv, program
            return mock.Mock(alive=True, id=tid, last_poll=time.time() + 3600.0)
        with mock.patch.object(T, "_make_terminal", fake):
            self.addCleanup(host.close_all)
            host.open("claude", explain="session S1")
            # WAIT FOR the observed sweep (it comes every ~0.1 s; 30 s is >100x slack) - a deadline
            # that is hit is RED, never a pass that tested nothing.
            self.assertTrue(swept.wait(30.0), "the reaper never swept: nothing was proven")
            self.assertEqual([], died, "a terminal-host thread died")
            self.assertEqual(["/bin/claude", "/mokata:explain session S1"], made["argv"])
            with self.assertRaises(T.TerminalRefused):
                host.open("shell", explain="session S1")
            with self.assertRaises(T.TerminalRefused):
                host.open("claude", resume="abc", explain="session S1")
        self.assertIn("claude", T.PROGRAMS)
        from mokata import billing_env
        self.assertTrue(hasattr(billing_env, "terminal_preface"))

    def test_windows_cmd_quoting_keeps_the_argument_whole(self):
        """On Windows `claude.cmd` runs under cmd.exe; the explain argument has no character
        cmd.exe reads as syntax, so list2cmdline quotes it as one argument."""
        import subprocess
        from mokata.ide.terminal import explain_prompt
        p = explain_prompt("range " + "a" * 40 + " " + "b" * 40)
        line = subprocess.list2cmdline([os.environ.get("COMSPEC", "cmd.exe"), "/c", "claude.cmd", p])
        self.assertTrue(line.endswith(f'"{p}"'))
        for ch in "&|<>^%!\"":
            self.assertNotIn(ch, p)


# ========================================================================== live-session path

class TheLiveSessionHandOff(unittest.TestCase):

    def setUp(self):
        self.d = _fixture_repo()

    def tearDown(self):
        _rm(self.d)

    def test_the_next_turn_carries_the_request_once_with_its_exact_header(self):
        """A queued request rides the session's next prompt as an honestly-labelled instruction,
        is drained after delivery, and marks the request delivered."""
        from mokata import hook_cli as H
        from mokata.config import Surface
        from mokata.ide import context as X
        from mokata.plugin_runtime import slash_command
        rec = ST.new_request(self.d, B.parse_target("session S1"), "session", session="S1")
        X.queue_explain(self.d, "S1", "session S1", rec["request_id"])
        block, ids = H._ide_context_for(Surface.load(self.d), self.d, "S1", "")
        want = X.EXPLAIN_HEADER.format(command=slash_command("explain"), target="session S1", request_id=rec["request_id"])
        self.assertIn(want, block)
        self.assertIn(f"explain:{rec['request_id']}", ids)
        H._ide_context_delivered(self.d, ids, session_id="S1")
        self.assertEqual(("", None), X.explain_block(self.d, "S1"))
        got = [r for r in ST.requests(self.d) if r["request_id"] == rec["request_id"]][0]
        self.assertTrue(got["delivered_at"])
        other, _ = H._ide_context_for(Surface.load(self.d), self.d, "S2", "")
        self.assertNotIn("Explain changes", other)

    def test_a_newer_request_replaces_the_older_and_says_so(self):
        """One slot per session: a second request supersedes the first, visibly."""
        from mokata.ide import context as X
        X.queue_explain(self.d, "S1", "session S1", "a" * 16)
        out = X.queue_explain(self.d, "S1", "live", "b" * 16)
        self.assertEqual("a" * 16, out["superseded"])
        self.assertIn("b" * 16, X.explain_block(self.d, "S1")[0])

    def test_the_explain_slot_never_touches_the_selection_queue(self):
        """The explain request has its own slot: it neither evicts nor is evicted by the
        8-item IDE-context queue."""
        from mokata.ide import context as X
        X.queue_explain(self.d, "S1", "session S1", "c" * 16)
        before = X.read(self.d) if hasattr(X, "read") else None
        X.clear(self.d) if hasattr(X, "clear") else None
        self.assertIn("c" * 16, X.explain_block(self.d, "S1")[0])
        self.assertIsNotNone(before)

    def test_the_needs_you_list_shows_the_queued_request_outside_the_risk_ranking(self):
        """A queued request shows on the session row as `explain-queued` — never as a risk."""
        from mokata import session_index as SI
        from mokata.ide import context as X
        from mokata.ide import session_views as V
        SI.on_start(self.d, "S1", "startup", self.d, t=time.time(), proc=None)
        X.queue_explain(self.d, "S1", "session S1", "d" * 16)
        rows = V.browser(self.d)
        row = [r for r in rows["sessions"] if r["id"] == "S1"][0]
        items = [i for i in row["needs_you"] if i["reason"] == V.EXPLAIN_QUEUED]
        self.assertEqual(1, len(items))
        self.assertFalse(items[0]["ranked"])
        self.assertEqual(0, items[0]["risk"])

    def test_the_waiting_state_is_derived_and_ends(self):
        """A request is queued, waiting, arrived, cancelled or `no page arrived` — never left
        spinning forever."""
        rec = {"spec": "session S1", "via": "terminal", "at": 1000.0, "wait_s": 60}
        self.assertEqual("waiting", ST.request_state(rec, [], alive=True, now=1010)["state"])
        self.assertEqual("no page arrived", ST.request_state(rec, [], alive=True,
                                                             now=1100)["state"])
        self.assertEqual("no page arrived", ST.request_state(rec, [], alive=False,
                                                             now=1010)["state"])
        page = {"id": "S1/x", "target": {"spec": "session S1"}, "generated_ts": 1005}
        self.assertEqual("arrived", ST.request_state(rec, [page], alive=False, now=9999)["state"])
        self.assertEqual("cancelled", ST.request_state({**rec, "cancelled": True}, [],
                                                       alive=True, now=1010)["state"])
        q = {**rec, "via": "session"}
        self.assertEqual("queued", ST.request_state(q, [], alive=True, now=99999)["state"])


# =============================================================================== boundaries

def _imports(path):
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            out.add(("." * n.level) + (n.module or ""))
        elif isinstance(n, ast.Import):
            out.update(a.name for a in n.names)
    return out


class TheExplainerNeverGatesAndNeverCallsAModel(unittest.TestCase):

    def test_ship_merge_and_gate_code_never_imports_the_explainer(self):
        """An explainer never blocks a merge or a ship: no gate path depends on it."""
        offenders = []
        # CORPUS: THE WORKING TREE. `src/` as it is on disk is what `rsync` mirrors; a stray
        # untracked gate module importing the explainer would ship, so it must be seen here.
        for dirpath, _dirs, files in os.walk(PKG):
            for f in files:
                if not f.endswith(".py"):
                    continue
                rel = name_of(os.path.join(dirpath, f), PKG)
                if not any(k in rel for k in ("ship", "merge", "gate", "verify")):
                    continue
                if any("explain" in m for m in _imports(os.path.join(dirpath, f))):
                    offenders.append(rel)
        self.assertEqual([], offenders)

    def test_no_explainer_code_reaches_a_model_api_or_a_key(self):
        """No module of the explainer imports an SDK, opens a network connection or reads an
        API key — mokata never calls a model."""
        # CORPUS: THE WORKING TREE. Every explainer module on disk ships (`rsync`), tracked or
        # not; an extra file makes this stricter, never easier to pass.
        paths = [os.path.join(PKG, "explain", f) for f in os.listdir(os.path.join(PKG, "explain"))
                 if f.endswith(".py")]
        paths += [os.path.join(PKG, "mcp", "tools_explain.py"),
                  os.path.join(PKG, "ide", "features", "explain.py"),
                  os.path.join(PKG, "cli_commands", "explain.py")]
        for p in paths:
            mods = _imports(p)
            for bad in ("anthropic", "urllib.request", "http.client", "requests", "httpx",
                        "socket"):
                self.assertFalse(any(m == bad or m.startswith(bad + ".") for m in mods),
                                 f"{p} imports {bad}")
            with open(p, encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn("api.anthropic.com", text)
            if os.path.basename(os.path.dirname(p)) != "cli_commands":
                self.assertNotIn("ANTHROPIC_API_KEY", text, p)

    def test_the_command_tells_the_truth_about_allowed_tools(self):
        """The command says `allowed-tools` pre-approves its three tools and forbids nothing."""
        with open(os.path.join(PKG, "templates", "commands", "explain.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("allowed-tools: mcp__mokata__explain_bundle, mcp__mokata__explain_save, Read",
                      text)
        self.assertIn("does NOT forbid any other tool", text)
        self.assertIn("UNTRUSTED DATA", text)
        self.assertIn("never needs an API key", text)

    def test_ship_offers_the_explainer_and_never_requires_it(self):
        """/ship offers `/mokata:explain` in one line; it is an offer, not a gate."""
        with open(os.path.join(PKG, "templates", "commands", "ship.md"), encoding="utf-8") as fh:
            text = fh.read()
        line = [x for x in text.splitlines() if "/mokata:explain" in x]
        self.assertEqual(1, len(line))
        self.assertRegex(line[0].lower(), r"offer|optional|never blocks")


# ================================================================================== routes

class TheIdeRoutes(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import test_p0_ide_api_contract as P0
        cls.P0 = P0
        cls.d = _fixture_repo()
        built = B.build(cls.d, B.parse_target("session S1"))
        pages = _serve_all(cls.d, built)
        cls.out = ST.save(cls.d, F.claude_doc(built["bundle"], pages))
        cls.c, _app, cls.state = P0.client(cls.d)

    @classmethod
    def tearDownClass(cls):
        cls.c.close()
        _rm(cls.d)

    def test_the_page_is_served_with_a_no_script_no_network_policy(self):
        """The page answers with `script-src 'none'`, no network, framable by this origin only."""
        from mokata.ide.features.explain import PAGE_CSP
        r = self.c.get("/api/explain/page", params={"id": self.out["review_id"]})
        self.assertEqual(200, r.status_code)
        self.assertEqual(PAGE_CSP, r.headers["content-security-policy"])
        for part in ("default-src 'none'", "script-src 'none'", "img-src data:",
                     "frame-ancestors 'self'", "base-uri 'none'", "form-action 'none'"):
            self.assertIn(part, PAGE_CSP)
        self.assertEqual("SAMEORIGIN", r.headers["x-frame-options"])
        self.assertEqual("nosniff", r.headers["x-content-type-options"])
        self.assertNotIn("<script", r.text.lower())

    def test_a_page_changed_on_disk_is_never_served(self):
        """A stored page edited after mokata validated it (a meta refresh, a script) is replaced
        by a notice — the allowlist is re-checked on every serve, not only at save."""
        from mokata.ide.features.explain import TAMPERED_PAGE
        d = _fixture_repo()
        try:
            built = B.build(d, B.parse_target("session S1"))
            pages = _serve_all(d, built)
            out = ST.save(d, F.claude_doc(built["bundle"], pages))
            p = os.path.join(d, ".mokata", "temp_local", "reviews",
                             *out["review_id"].split("/")) + ".html"
            with open(p, encoding="utf-8") as fh:
                html = fh.read()
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(html.replace("<head>", '<head><meta http-equiv="refresh" '
                                                 'content="0;url=https://evil.example/">'))
            c, _app, _st = self.P0.client(d)
            r = c.get("/api/explain/page", params={"id": out["review_id"]})
            c.close()
            self.assertEqual(200, r.status_code)
            self.assertEqual(TAMPERED_PAGE, r.text)
            self.assertNotIn("evil.example", r.text)
        finally:
            _rm(d)

    def test_the_list_carries_pages_staleness_requests_and_sizes(self):
        """The Review tab's list has every page with its state, the requests, and the store size."""
        r = self.c.get("/api/explain/list", params={"session": "S1"}).json()
        self.assertEqual([self.out["review_id"]], [x["id"] for x in r["reviews"]])
        self.assertEqual("fresh", r["reviews"][0]["state"])
        self.assertGreater(r["size"]["pages"], 0)
        self.assertEqual(ST.KEEP_PAGES, r["keep"])

    def test_the_sidecar_is_the_closed_cite_list(self):
        """The sidecar route returns the kept cites the frame's buttons resolve through."""
        r = self.c.get("/api/explain/sidecar", params={"id": self.out["review_id"]}).json()
        self.assertEqual(10, r["kept"])
        self.assertTrue(all("k" in c for c in r["cites"]))
        self.assertEqual("fresh", r["state"])

    def test_no_route_returns_or_needs_an_api_key(self):
        """Nothing the Review tab calls answers with, or asks for, an API key."""
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-" + "test-" + "zzz"}):
            for path, params in (("/api/explain/list", {}),
                                 ("/api/explain/sidecar", {"id": self.out["review_id"]}),
                                 ("/api/explain/page", {"id": self.out["review_id"]})):
                r = self.c.get(path, params=params)
                self.assertEqual(200, r.status_code, path)
                self.assertNotIn("sk-test-zzz", r.text)
                self.assertNotIn("api_key", r.text.lower())

    def test_a_request_on_a_non_live_session_opens_claude_in_the_terminal(self):
        """With no live session, "Explain changes" builds the bundle as a job, then starts the
        embedded `claude` with `/mokata:explain <target>` and records a waiting request."""
        opened = {}

        def fake_open(program, cols=100, rows=30, resume=None, explain=None):
            opened.update(program=program, explain=explain, resume=resume)
            return mock.Mock(id="T1", alive=True)
        with mock.patch.object(self.state.terminals, "open", fake_open), \
                mock.patch("mokata.ide.sessions.get", return_value=mock.Mock(status="ended")):
            r = self.c.post("/api/explain/request", json={"target": "session S1"},
                            headers=self.P0.ORIGIN)
            self.assertEqual(200, r.status_code, r.text)
            jid = r.json()["id"]
            for _ in range(300):
                job = self.state.jobs.get(jid)
                if job is not None and job.snapshot()["state"] not in ("running", "queued"):
                    break
                time.sleep(0.05)
        self.assertEqual({"program": "claude", "explain": "session S1", "resume": None}, opened)
        reqs = self.c.get("/api/explain/list", params={"session": "S1"}).json()["requests"]
        self.assertEqual("terminal", reqs[0]["via"])

    @mock.patch.dict(os.environ, {"MOKATA_VIA_PLUGIN": "1"})   # the namespaced plugin route
    def test_a_request_on_a_live_session_queues_for_its_next_turn(self):
        """With the session live, the request is queued for its next turn — no terminal opens."""
        from mokata import session_index as SI
        from mokata.ide import context as X
        SI.on_start(self.d, "S1", "startup", self.d, t=time.time(), proc=None)
        with mock.patch("mokata.ide.sessions.get") as get, \
                mock.patch.object(self.state.terminals, "open",
                                  side_effect=AssertionError("opened a terminal")):
            get.return_value = mock.Mock(status="live")
            r = self.c.post("/api/explain/request", json={"target": "session S1"},
                            headers=self.P0.ORIGIN)
            jid = r.json()["id"]
            for _ in range(300):
                job = self.state.jobs.get(jid)
                if job is not None and job.snapshot()["state"] not in ("running", "queued"):
                    break
                time.sleep(0.05)
        self.assertEqual("done", self.state.jobs.get(jid).snapshot()["state"],
                         self.state.jobs.get(jid).snapshot())
        line, rid = X.explain_block(self.d, "S1")
        self.assertIn("/mokata:explain session S1", line)
        X.explain_clear(self.d, "S1")


if __name__ == "__main__":
    unittest.main()


# ============================================================== rulings after review (P46 r2)

class APrTargetWaitsForP20(unittest.TestCase):
    """Ruling 1: a PR target stays refused until P20 (Wave B) — said plainly, on every surface,
    in the same words, and never quietly read as some other target."""

    def test_every_spelling_of_a_pr_target_gets_the_p20_sentence(self):
        """`pr 12`, `PR 12`, a bare `pr` and `{kind: pr}` are each refused with the P20 sentence."""
        self.assertIn("P20", B.PR_REFUSAL)
        self.assertIn("Wave B", B.PR_REFUSAL)
        for spec in ("pr 12", "PR 12", "pr", "pull 12", {"kind": "pr", "pr": "12"},
                     {"kind": "PR"}):
            with self.assertRaises(B.BundleRefused) as cm:
                B.parse_target(spec)
            self.assertEqual(B.PR_REFUSAL, str(cm.exception), spec)

    def test_the_mcp_tool_and_the_cli_say_the_same_sentence(self):
        """`explain_bundle(target="pr 12")` answers refused with it; `mokata explain pr 12` exits 2
        and prints it — no fallback to `live` or to a range."""
        from mokata.cli_commands import explain as CE
        from mokata.mcp import tools_explain as TX
        d = _plain_repo({"a.py": "x = 1\n"})
        try:
            got = TX.explain_bundle(path=d, target="pr 12")
            self.assertEqual({"status": "refused", "message": B.PR_REFUSAL}, got)
            err, out = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = CE.cmd_explain(mock.Mock(path=d, target=["pr", "12"]))
            self.assertEqual(2, rc)
            self.assertIn(B.PR_REFUSAL, err.getvalue())
            self.assertEqual("", out.getvalue())
        finally:
            _rm(d)

    def test_the_ide_request_route_refuses_it_and_starts_nothing(self):
        """POST /api/explain/request with a PR target is a 400 carrying the P20 sentence."""
        import test_p0_ide_api_contract as P0
        d = _plain_repo({"a.py": "x = 1\n"})
        c, _app, _st = P0.client(d)
        try:
            r = c.post("/api/explain/request", json={"target": "pr 12"}, headers=P0.ORIGIN)
            self.assertEqual(400, r.status_code)
            self.assertIn(B.PR_REFUSAL, json.dumps(r.json(), ensure_ascii=False))
        finally:
            c.close()
            _rm(d)


def _landed_range():
    """The fixture with the session's change committed: `range <base> <head>` where cart.py's
    `legacy_total` exists only at base and pricing.py exists only at head."""
    d = _fixture_repo()
    base = _head(d)
    F.git(d, "add", "-A")
    F.git(d, "commit", "-qm", "land the session")
    return d, base, _head(d)


class BaseCitesOpenTheBaseSide(unittest.TestCase):
    """Ruling 2: a base cite opens the BASE revision, read-only — never the file at head (the
    reader would see the wrong side of the change, unlabelled)."""

    @classmethod
    def setUpClass(cls):
        import test_p0_ide_api_contract as P0
        cls.d, cls.base, cls.headsha = _landed_range()
        built = B.build(cls.d, B.parse_target(f"range {cls.base} {cls.headsha}"))
        pages = _serve_all(cls.d, built)
        h = built["bundle"]["content_hash"]
        cls.out = ST.save(cls.d, {"content_hash": h, "model": "m",
                                  "pages_read": list(range(1, pages + 1)), "sections": [
            {"id": "summary", "blocks": [
                {"kind": "prose", "text": "legacy_total was removed",
                 "cites": ["base:src/shop/cart.py:8-9", "fact:s:base:src/shop/cart.py::legacy_total"]},
                {"kind": "prose", "text": "validate is new", "cites": ["src/shop/cart.py:8-11"]}]}]})
        cls.c, _app, _st = P0.client(cls.d)
        cls.sc = cls.c.get("/api/explain/sidecar", params={"id": cls.out["review_id"]}).json()

    @classmethod
    def tearDownClass(cls):
        cls.c.close()
        _rm(cls.d)

    def by_cite(self, cite):
        got = [c for c in self.sc["cites"] if c["cite"] == cite]
        self.assertTrue(got, (cite, self.sc["cites"]))
        return got[0]

    def test_a_base_line_cite_carries_the_base_commit(self):
        """A `base:` cite in the sidecar names the base commit it must be read at; a head cite
        names none (it is read where head lives)."""
        self.assertEqual(self.base, self.by_cite("base:src/shop/cart.py:8-9").get("rev"))
        self.assertEqual("base", self.by_cite("base:src/shop/cart.py:8-9")["tree"])
        self.assertFalse(self.by_cite("src/shop/cart.py:8-11").get("rev"))

    def test_a_base_symbol_fact_cite_is_a_base_cite_too(self):
        """A fact that lives only at base (a removed function) opens at base, not at head."""
        c = self.by_cite("fact:s:base:src/shop/cart.py::legacy_total")
        self.assertEqual("base", c.get("tree"))
        self.assertEqual(self.base, c.get("rev"))
        self.assertEqual(8, c["line"])

    def test_the_base_revision_read_shows_the_base_lines(self):
        """Read through the existing `rev=` path, the cited lines are base's `legacy_total`, not
        what head holds at those line numbers (`validate`)."""
        c = self.by_cite("base:src/shop/cart.py:8-9")
        r = self.c.get("/api/diff/context", params={"path": c["path"], "rev": c["rev"],
                                                     "start": c["line"], "count": 2}).json()
        self.assertEqual("ok", r["state"])
        self.assertEqual(F.CART_OLD.splitlines()[7:9], r["lines"])
        self.assertNotIn("validate", " ".join(r["lines"]))

    def test_a_file_the_change_added_has_no_base_and_says_so(self):
        """At base, a file the change added does not exist: the read is `unavailable` with a
        reason — the viewer says so and never falls back to head."""
        r = self.c.get("/api/diff/context", params={"path": "src/shop/pricing.py",
                                                     "rev": self.base, "start": 1,
                                                     "count": 5}).json()
        self.assertEqual("unavailable", r["state"])
        self.assertEqual([], r["lines"])
        self.assertTrue(r["note"])

    def test_the_viewer_never_opens_a_base_cite_at_head(self):
        """The Explain screen's cite follower routes a base cite to the base viewer (labelled
        `base @ <sha>`) and has no head fallback for it."""
        with open(os.path.join(PKG, "ide", "static", "features", "explain.js"),
                  encoding="utf-8") as fh:
            js = fh.read()
        self.assertFalse("opened at head" in js, "a base cite still falls back to head")
        self.assertTrue("base @ " in js, "the base view is not labelled `base @ <sha>`")
        self.assertTrue("/api/diff/context" in js, "the base view does not read through rev=")


# ============================================================== round 3 (P46.verify.md M1-M5)

PR_SPELLINGS = [
    "pr 12", "PR 12", "pr", "pull 12", "PR#12", "pr/12", "pr12", "pr-12", "#12", "!12",
    "pull_request 12", "pull-request 12", "pulls/12", "gh pr 12", "glab mr 7", "mr 3", "MR!3",
    "merge_request 4", "https://github.com/o/r/pull/12", "https://github.com/o/r/pull/12/files",
    "https://gitlab.com/g/p/-/merge_requests/9", "refs/pull/12/head", "refs/pull/12/merge",
    "range main refs/pull/12/head", "range refs/merge-requests/3/head main",
    {"kind": "pull_request"}, {"kind": "PR"}, {"kind": "merge-request", "id": "4"},
    {"kind": "range", "base": "main", "head": "refs/pull/3/head"},
    # r4: the verifier's residuals, through the same one detector
    "o/r#12", "o/r!12", "git@github.com:o/r.git#12", "pr_12", "pull_request_12", "pr=12",
    "pr.12", "pr12/head", "explain pr 12", "/mokata:explain pr 12", "/explain pr 12", "gitlab mr 12",
    "hub pull-request", "\uff50\uff52 12", "gh pr view 12",
    {"kind": "live", "pr": 12}, {"kind": "session", "session": "S1", "pull_request": "3"},
    {"kind": "range", "base": "main", "head": "feat", "mr": 7},
]
NOT_PR = ["live", "session S1", "session pr-thing", "run r1", "range main feat",
          "session prod12", "range main print", "session pr", "session pull", "session prompt",
          "range pull-fix hotfix", "range main pulley/12x", "explain live",
          {"kind": "session", "session": "pr12"}]


class EveryPrSpellingGetsTheP20Sentence(unittest.TestCase):
    """M3: ONE detector (`bundle.is_pr_target`, reached through `parse_target` by the MCP tool,
    the CLI and the IDE route) — every spelling of a PR / MR gets the P20 sentence; nothing is
    read in its place, and an ordinary target is never mistaken for one."""

    def test_the_detector_table(self):
        """Each spelling is a PR target; each ordinary target is not."""
        for spec in PR_SPELLINGS:
            with self.subTest(spec=spec):
                self.assertTrue(B.is_pr_target(spec))
                with self.assertRaises(B.BundleRefused) as cm:
                    B.parse_target(spec)
                self.assertEqual(B.PR_REFUSAL, str(cm.exception))
        for spec in NOT_PR:
            with self.subTest(spec=spec):
                self.assertFalse(B.is_pr_target(spec))

    def test_an_unknown_field_is_refused_never_ignored(self):
        """`{kind: live, foo: 1}` is refused (it might name what to explain); nothing is read."""
        for spec in ({"kind": "live", "foo": 1}, {"kind": "session", "session": "S1", "x": "y"},
                     {"kind": "range", "base": "a", "head": "b", "c": "d"}):
            with self.subTest(spec=spec), self.assertRaises(B.BundleRefused) as cm:
                B.parse_target(spec)
            self.assertIn("has no field", str(cm.exception))

    def test_every_surface_says_the_same_sentence_for_every_spelling(self):
        """MCP answers refused with it, the CLI exits 2 with it, the IDE route answers 400 with
        it — for every spelling, and no bundle job starts."""
        import test_p0_ide_api_contract as P0
        from mokata.cli_commands import explain as CE
        from mokata.explain import runner as RN
        from mokata.mcp import tools_explain as TX
        d = _plain_repo({"a.py": "x = 1\n"})
        c, _app, _st = P0.client(d)
        try:
            with mock.patch.object(RN, "start", side_effect=AssertionError("a job started")):
                for spec in PR_SPELLINGS:
                    with self.subTest(spec=spec):
                        self.assertEqual({"status": "refused", "message": B.PR_REFUSAL},
                                         TX.explain_bundle(path=d, target=spec))
                        r = c.post("/api/explain/request", json={"target": spec},
                                   headers=P0.ORIGIN)
                        self.assertEqual(400, r.status_code)
                        self.assertIn(B.PR_REFUSAL, json.dumps(r.json(), ensure_ascii=False))
                        if isinstance(spec, str):
                            err = io.StringIO()
                            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                                rc = CE.cmd_explain(mock.Mock(path=d, target=spec.split() or [""]))
                            self.assertEqual(2, rc)
                            self.assertIn(B.PR_REFUSAL, err.getvalue())
        finally:
            c.close()
            _rm(d)


class ABaseIsPresentAddedOrUnavailable(unittest.TestCase):
    """M2: whether the base side exists is an explicit OBJECT check of the commit and the blob —
    `present`, `added` (the commit is here, the path is not in it) or `unavailable` (the commit
    is not here: pruned, or past a shallow clone's boundary). Never read off an error's wording,
    and never a fall-back to head."""

    def test_the_three_states(self):
        d, base, _head_sha = _landed_range()
        try:
            got = B.base_objects(d, base, ["src/shop/cart.py", "src/shop/pricing.py"])
            self.assertEqual({"src/shop/cart.py": "present", "src/shop/pricing.py": "added"}, got)
            self.assertEqual({"src/shop/cart.py": "unavailable"},
                             B.base_objects(d, "de" * 20, ["src/shop/cart.py"]))
        finally:
            _rm(d)

    def test_a_shallow_clone_says_the_base_commit_is_unavailable_not_added(self):
        """In a depth-1 clone the base commit is past the boundary: the sidecar route says
        `unavailable` for a file that existed — where P5's read says only "exists on disk, but
        not in <sha>", which a wording match took for "the change added it"."""
        import subprocess
        import test_p0_ide_api_contract as P0
        d, base, head_sha = _landed_range()
        sh = tempfile.mkdtemp(prefix="p46s-")
        try:
            built = B.build(d, B.parse_target(f"range {base} {head_sha}"))
            pages = _serve_all(d, built)
            out = ST.save(d, {"content_hash": built["bundle"]["content_hash"], "model": "m",
                              "pages_read": list(range(1, pages + 1)), "sections": [
                {"id": "summary", "blocks": [{"kind": "prose", "text": "legacy_total was removed",
                                              "cites": ["base:src/shop/cart.py:8-9"]}]}]})
            subprocess.run(["git", "clone", "-q", "--depth", "1", "--no-local", d,
                            os.path.join(sh, "c")], check=True, capture_output=True)
            clone = os.path.join(sh, "c")
            shutil.copytree(os.path.join(d, ".mokata"), os.path.join(clone, ".mokata"),
                            dirs_exist_ok=True)
            self.assertEqual("", B.rev(clone, base))
            c, _app, _st = P0.client(clone)
            try:
                sc = c.get("/api/explain/sidecar", params={"id": out["review_id"]}).json()
                cite = [x for x in sc["cites"] if x["cite"] == "base:src/shop/cart.py:8-9"][0]
                self.assertEqual("unavailable", cite["base"])
                self.assertEqual(base, cite["rev"])
                c2, _a, _s = P0.client(d)
                try:
                    sc2 = c2.get("/api/explain/sidecar", params={"id": out["review_id"]}).json()
                finally:
                    c2.close()
                self.assertEqual("present", [x for x in sc2["cites"]
                                             if x["cite"] == cite["cite"]][0]["base"])
            finally:
                c.close()
        finally:
            _rm(sh)
            _rm(d)

    def test_the_viewer_reads_the_state_not_an_error_message(self):
        """The Explain screen branches on `base` (present / added / unavailable / none) and has
        no wording match on P5's note."""
        with open(os.path.join(PKG, "ide", "static", "features", "explain.js"),
                  encoding="utf-8") as fh:
            js = fh.read()
        for state in ('c.base === "unavailable"', 'c.base === "added"', 'c.base === "none"'):
            self.assertTrue(state in js, state)
        self.assertFalse("does not exist|" in js, "the viewer still matches P5's wording")


def _blob_mb(built, mb):
    """`mb` MiB of extra head blobs, each under the 1 MiB per-file read bound (as a build
    makes them)."""
    built["blobs"] = dict(built["blobs"])
    for i in range(mb):
        built["blobs"][f"head:src/big/f{i:03d}.txt"] = ("line %d of a big file\n" % i) * 46000
    return built


class AnOversizeBundleIsSpilledNotHeldNorRebuiltInline(unittest.TestCase):
    """M1 + r4 N1 + r5 N3: the cache write and read share ONE bound. A record over it is not one
    cache file and not held in memory: its size is measured without a second serialisation, a
    stub names its target, and the record is SPILLED row by row through P4's `SpillWriter`,
    keyed by its hash — every later page and the save stream from the spill, and nothing is
    rebuilt while it lives. A released spill is rebuilt AS A JOB, never inline in a tool call."""

    def setUp(self):
        from mokata.explain import spill as SP
        self.SP = SP
        self.addCleanup(SP.release)

    def _big(self, d, mb=46):
        return _blob_mb(B.build(d, B.parse_target("session S1")), mb)

    def test_a_46_mb_record_is_spilled_and_says_so(self):
        d = _fixture_repo()
        try:
            built = self._big(d)
            h = built["bundle"]["content_hash"]
            rec = ST.cache_bundle(d, built)
            self.assertIn("not cached", rec["cache"])
            self.assertIn("spilled", rec["cache"])
            bdir = os.path.join(d, ".mokata", "temp_local", "reviews", "_bundles")
            for n in os.listdir(bdir):
                self.assertLess(os.path.getsize(os.path.join(bdir, n)), 64 * 1024, n)
            sp = self.SP.get(d, h)
            self.assertTrue(os.path.isfile(sp.path))
            if os.name != "nt":
                self.assertEqual(0o600, os.stat(sp.path).st_mode & 0o777)
            else:                    # no mode bits there: the ACL is the promise (owner only)
                from _winacl_assert import assert_owner_only
                assert_owner_only(self, sp.path)
            self.assertTrue(os.path.basename(sp.path).startswith("p4-"))
            got = ST.load_bundle(d, h)
            self.assertEqual(built["blobs"]["head:src/big/f007.txt"],
                             got["blobs"].get("head:src/big/f007.txt"))
            self.assertEqual(ST.page_view({"bundle": built["bundle"],
                                           "page_of": ST.paginate(built["bundle"])}, 1)["facts"],
                             ST.view(d, h, 1)["facts"])
            ST.mark_served(d, h, 1)
            self.assertEqual("saved", ST.save(d, F.claude_doc(built["bundle"], 1))["status"])
        finally:
            _rm(d)

    def test_pages_and_the_save_never_build_again(self):
        """Page 1, page 2 and the save of an oversize bundle: ONE build in all."""
        from mokata.mcp import tools_explain as TX
        d = _fixture_repo()
        try:
            with mock.patch.object(ST, "PAGE_BYTES", 1500), \
                    mock.patch.object(ST, "CACHE_MAX_BYTES", 4096):
                real = B.build
                calls = []

                def counted(*a, **kw):
                    calls.append(a)
                    return real(*a, **kw)
                with mock.patch.object(B, "build", side_effect=counted):
                    one = TX.explain_bundle(path=d, target="session S1")
                    self.assertEqual("ready", one["status"], one)
                    self.assertIn("not cached", one["bundle"]["cache"])
                    pages = one["bundle"]["pages"]
                    self.assertGreater(pages, 1)
                    for p in range(2, pages + 1):
                        got = TX.explain_bundle(path=d, job=one["job"], page=p)
                        self.assertEqual("ready", got["status"])
                        self.assertEqual(p, got["bundle"]["page"])
                n_before_save = len(calls)
                with mock.patch.object(B, "build", side_effect=counted):
                    saved = TX.explain_save(path=d, document={
                        "content_hash": one["bundle"]["content_hash"], "model": "m",
                        "pages_read": list(range(1, pages + 1)), "sections": [
                            {"id": "summary", "blocks": [
                                {"kind": "prose", "text": "checkout changed",
                                 "cites": ["fact:s:head:src/shop/cart.py::checkout"]}]}]})
                self.assertEqual("saved", saved["status"], saved)
                self.assertEqual(1, len(calls), "an oversize bundle was built more than once")
                self.assertEqual(1, n_before_save)
        finally:
            _rm(d)

    def test_a_released_spill_is_rebuilt_as_a_job_never_inline(self):
        """With the spill released, page 2 and the save each answer `building` with a job at
        once — the build runs in that job (here held on a gate), not inside the call."""
        from mokata.explain import runner as RN
        from mokata.mcp import tools_explain as TX
        d = _fixture_repo()
        gate = threading.Event()
        try:
            with mock.patch.object(ST, "PAGE_BYTES", 1500), \
                    mock.patch.object(ST, "CACHE_MAX_BYTES", 4096):
                one = TX.explain_bundle(path=d, target="session S1")
                h = one["bundle"]["content_hash"]
                self.assertEqual(1, self.SP.release(d))
                real = B.build

                def held(*a, **kw):
                    gate.wait(20)
                    return real(*a, **kw)
                with mock.patch.object(B, "build", side_effect=held), \
                        mock.patch.object(TX, "WAIT_S", 0.2):
                    t0 = time.monotonic()
                    got = TX.explain_bundle(path=d, job=one["job"], page=2)
                    self.assertLess(time.monotonic() - t0, 5.0)
                    self.assertEqual("building", got["status"], got)
                    job = RN.get(got["job"])
                    self.assertEqual("running", job.state)
                    saved = TX.explain_save(path=d, document={"content_hash": h, "model": "m",
                                                              "pages_read": [1], "sections": []})
                    self.assertEqual("building", saved["status"])
                    self.assertEqual(got["job"], saved["job"])          # joins the same job
                    gate.set()
                    job.wait(30)
                self.assertEqual("done", job.state, job.error)
                again = TX.explain_bundle(path=d, job=got["job"], page=2)
                self.assertEqual("ready", again["status"])
                self.assertIsNotNone(self.SP.get(d, h))
        finally:
            gate.set()
            _rm(d)

    def test_a_process_that_spilled_and_exits_normally_leaves_no_spill_file_behind(self):
        """A spill is a ~50 MB file in the repo's private spill dir. When the IDE (or any process
        that explained a change) exits, its spills go with it - at once, by the process itself,
        not later by some other process's sweep of dead pids."""
        import subprocess
        d = _plain_repo({"a.py": "x = 1\n"})
        src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
        code = ("import sys\nsys.path.insert(0, sys.argv[1])\n"
                "from mokata.explain import spill as SP\n"
                "rec = {'bundle': {'content_hash': 'h' * 16, 'facts': [{'id': 'f1'}]},\n"
                "       'page_of': {'f1': 1}, 'blobs': {'k': 'v' * 1000}}\n"
                "sp = SP.write(sys.argv[2], 'sid', rec, 1000)\n"
                "assert sp.path and __import__('os').path.isfile(sp.path)\n"
                "print(sp.path)\n")
        try:
            done = subprocess.run([sys.executable, "-c", code, src, d], capture_output=True,
                                  text=True, stdin=subprocess.DEVNULL)
            self.assertEqual(0, done.returncode, done.stderr)
            path = done.stdout.strip()
            self.assertTrue(path)
            self.assertFalse(os.path.exists(path), "the exiting process left its spill behind")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_the_spill_is_released_on_a_hash_change_when_idle_and_at_release(self):
        d = _plain_repo({"a.py": "def f():\n    return 1\n"})
        try:
            with mock.patch.object(ST, "CACHE_MAX_BYTES", 10):
                F.write(d, "a.py", "def f():\n    return 2\n")
                one = ST.cache_bundle(d, B.build(d, B.parse_target("live")))
                h1 = one["bundle"]["content_hash"]
                p1 = self.SP.get(d, h1).path
                F.write(d, "a.py", "def f():\n    return 3\n")
                two = ST.cache_bundle(d, B.build(d, B.parse_target("live")))
                h2 = two["bundle"]["content_hash"]
                self.assertNotEqual(h1, h2)
                self.assertFalse(os.path.exists(p1), "the old hash's spill was kept")
                self.assertIsNone(self.SP.get(d, h1))
                p2 = self.SP.get(d, h2).path
                with mock.patch.object(self.SP, "IDLE_S", 0.0):
                    # idle means "the clock has moved past its last use": Windows' monotonic clock
                    # ticks every ~16 ms, so wait on the clock itself, not on a guessed sleep
                    last = self.SP.live(d)[0].used
                    deadline = time.monotonic() + 60
                    while time.monotonic() <= last and time.monotonic() < deadline:
                        time.sleep(0.005)
                    self.assertIsNone(self.SP.get(d, h2))
                self.assertFalse(os.path.exists(p2), "an idle spill was kept")
                three = ST.cache_bundle(d, B.build(d, B.parse_target("live")))
                p3 = self.SP.get(d, three["bundle"]["content_hash"]).path
                self.SP.release()
                self.assertFalse(os.path.exists(p3))
                with self.assertRaises(ST.NeedsRebuild):
                    ST.load_bundle(d, h2)
        finally:
            _rm(d)

    def test_asking_again_for_the_same_target_reuses_the_spill(self):
        """A NEW explain_bundle call for the same target (not a page of the old job) finds the
        live spill: no second build."""
        from mokata.mcp import tools_explain as TX
        d = _fixture_repo()
        try:
            with mock.patch.object(ST, "CACHE_MAX_BYTES", 4096):
                real = B.build
                calls = []

                def counted(*a, **kw):
                    calls.append(a)
                    return real(*a, **kw)
                with mock.patch.object(B, "build", side_effect=counted):
                    one = TX.explain_bundle(path=d, target="session S1")
                    two = TX.explain_bundle(path=d, target="session S1")
                self.assertEqual("ready", two["status"], two)
                self.assertEqual(one["bundle"]["content_hash"], two["bundle"]["content_hash"])
                self.assertEqual(1, len(calls))
        finally:
            _rm(d)

    def test_a_spill_is_never_served_after_head_moves(self):
        """The reuse is keyed by the RESOLVED target (spec, SHAs, dirty hash): a new commit
        moves the head, so the old spill is not served — the bundle is built again."""
        from mokata.mcp import tools_explain as TX
        import subprocess as sp_
        d = _fixture_repo()
        try:
            with mock.patch.object(ST, "CACHE_MAX_BYTES", 4096):
                real = B.build
                calls = []

                def counted(*a, **kw):
                    calls.append(a)
                    return real(*a, **kw)
                with mock.patch.object(B, "build", side_effect=counted):
                    one = TX.explain_bundle(path=d, target="session S1")
                    self.assertEqual("ready", one["status"], one)
                    with open(os.path.join(d, "moved.txt"), "w", encoding="utf-8") as fh:
                        fh.write("head moves\n")
                    sp_.run(["git", "-C", d, "add", "moved.txt"], check=True,
                            capture_output=True)
                    sp_.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@t",
                             "commit", "-qm", "move head"], check=True, capture_output=True)
                    two = TX.explain_bundle(path=d, target="session S1")
                self.assertEqual("ready", two["status"], two)
                self.assertEqual(2, len(calls), "a spill was served after HEAD moved")
                t1, t2 = one["bundle"]["target"], two["bundle"]["target"]
                self.assertNotEqual(t1.get("head_sha"), t2.get("head_sha"))
        finally:
            _rm(d)

    def test_a_page_reads_only_its_own_rows(self):
        d = _fixture_repo()
        try:
            with mock.patch.object(ST, "PAGE_BYTES", 1500), \
                    mock.patch.object(ST, "CACHE_MAX_BYTES", 4096):
                built = B.build(d, B.parse_target("session S1"))
                rec = ST.cache_bundle(d, built)
                sp = self.SP.get(d, rec["bundle"]["content_hash"])
                for p in sorted(set(sp.page_of.values())):
                    want = sorted(f for f, q in sp.page_of.items() if q == p)
                    self.assertEqual(want, sorted(f["id"] for f in sp.page_facts(p)), p)
        finally:
            _rm(d)

    def test_a_spill_file_removed_by_hand_is_released_and_rebuilt(self):
        d = _fixture_repo()
        try:
            with mock.patch.object(ST, "CACHE_MAX_BYTES", 4096):
                rec = ST.cache_bundle(d, B.build(d, B.parse_target("session S1")))
                h = rec["bundle"]["content_hash"]
                os.unlink(self.SP.get(d, h).path)
                self.assertIsNone(self.SP.get(d, h))
                self.assertEqual([], self.SP.live(d))
                with self.assertRaises(ST.NeedsRebuild):
                    ST.view(d, h, 1)
        finally:
            _rm(d)

    def test_the_size_is_measured_and_spilled_without_a_second_copy(self):
        """`record_size` equals the serialised length exactly; measuring and spilling a 40 MB
        record allocates a few MB at most — never a second whole copy."""
        import tracemalloc
        d = _fixture_repo()
        try:
            built = B.build(d, B.parse_target("session S1"))
            rec = {"bundle": built["bundle"], "blobs": dict(built["blobs"]),
                   "page_of": ST.paginate(built["bundle"]), "at": 1.0}
            rec["blobs"]["head:q.txt"] = 'a"\\\n\t\x01\u00e9\u2028' * 300000
            self.assertEqual(len(ST._dump(rec)), ST.record_size(rec))
            big = self._big(d, 40)
            with mock.patch.object(ST, "_dump", wraps=ST._dump) as dump:
                tracemalloc.start()
                base = tracemalloc.get_traced_memory()[0]
                ST.cache_bundle(d, big)
                peak = tracemalloc.get_traced_memory()[1] - base
                tracemalloc.stop()
            self.assertLess(peak, 8 * 1024 * 1024, f"measuring + spilling allocated {peak} bytes")
            for call in dump.call_args_list:
                blobs = call.args[0].get("blobs") or {}
                self.assertLess(sum(len(v) for v in blobs.values()), 1024 * 1024)
        finally:
            _rm(d)

    def test_nothing_keeps_the_record_after_it_is_served(self):
        """The record lives only in its spill: after the first answer no dict holding its blobs is
        reachable in the process."""
        import gc
        from mokata.mcp import tools_explain as TX
        d = _fixture_repo()
        try:
            built = self._big(d)
            with mock.patch.object(B, "build", return_value=built):
                got = TX.explain_bundle(path=d, target="session S1")
            self.assertEqual("ready", got["status"])
            del built
            gc.collect()
            held = [o for o in gc.get_objects() if isinstance(o, dict)
                    and "head:src/big/f000.txt" in o]
            self.assertEqual([], held, "an oversize record is still referenced")
        finally:
            _rm(d)

    def test_the_ide_neither_holds_nor_spills(self):
        d = _fixture_repo()
        try:
            rec = ST.cache_bundle(d, self._big(d, 33), spill=False)
            self.assertIn("builds and spills its own", rec["cache"])
            self.assertIsNone(self.SP.get(d, rec["bundle"]["content_hash"]))
        finally:
            _rm(d)

    def test_the_write_bound_is_the_read_bound(self):
        """A record just under the bound is written AND loads back from disk."""
        d = _fixture_repo()
        try:
            built = B.build(d, B.parse_target("session S1"))
            small = len(json.dumps({"bundle": built["bundle"], "blobs": built["blobs"]}))
            with mock.patch.object(ST, "CACHE_MAX_BYTES", small * 3):
                rec = ST.cache_bundle(d, built)
                self.assertNotIn("cache", rec)
                self.assertIsNotNone(ST.load_bundle(d, built["bundle"]["content_hash"]))
        finally:
            _rm(d)


_REAL_POPEN = B.subprocess.Popen
#: a "git" that takes its input and then says NOTHING, never exiting (a partial clone stuck on a
#: lazy object fetch)
_SILENT = [sys.executable, "-c", "import sys, time; sys.stdin.read(); time.sleep(60)"]


def _silent_git(*verbs):
    """Popen where any git argv naming one of `verbs` runs the silent stub instead."""
    def popen(argv, *a, **kw):
        if argv[:1] == ["git"] and any(v in argv for v in verbs):
            argv = list(_SILENT)
        return _REAL_POPEN(argv, *a, **kw)
    return mock.patch.object(B.subprocess, "Popen", side_effect=popen)


class AStalledGitFailsTheJobNeverHangs(unittest.TestCase):
    """r5 N4 (A1): every read of git's stdout goes through `bounded_io`, bounded by SILENCE
    (`GIT_STALL_S`) and by the job's Cancel. A git that stops answering ends the job `failed`
    with "git did not answer" and its process killed — never a hang, never a short bundle."""

    def test_a_silent_cat_file_raises_git_did_not_answer(self):
        d = _fixture_repo()
        try:
            head = B._git(d, "rev-parse", "HEAD")[1].strip()
            with _silent_git("cat-file"), mock.patch.object(B, "GIT_STALL_S", 0.5):
                t0 = time.monotonic()
                with self.assertRaises(B.GitSilent) as got:
                    B.cat_blobs(d, [(head, "src/shop/cart.py")], 1 << 20)
            self.assertLess(time.monotonic() - t0, 10.0)
            self.assertIn("git did not answer", str(got.exception))
            self.assertIsInstance(got.exception, B.BundleRefused)
        finally:
            _rm(d)

    def test_a_silent_grep_raises_too(self):
        d = _fixture_repo()
        try:
            with _silent_git("grep"), mock.patch.object(B, "GIT_STALL_S", 0.5):
                with self.assertRaises(B.GitSilent):
                    B.build(d, B.parse_target("session S1"))
        finally:
            _rm(d)

    def test_the_job_fails_honestly(self):
        from mokata.explain import runner as RN
        d = _fixture_repo()
        try:
            with _silent_git("grep"), mock.patch.object(B, "GIT_STALL_S", 0.5):
                job = RN.start(d, B.parse_target("session S1"))
                job.wait(30)
            self.assertEqual("failed", job.state)
            self.assertIn("git did not answer", job.error)
        finally:
            _rm(d)

    def test_cancel_ends_a_silent_read_at_once(self):
        """The stall bound is long (sized for a lazy fetch), so Cancel — not the clock — is what
        a person uses; it must cut the silent read short."""
        from mokata.explain import runner as RN
        d = _fixture_repo()
        try:
            with _silent_git("grep"), mock.patch.object(B, "GIT_STALL_S", 120.0):
                job = RN.start(d, B.parse_target("session S1"))
                time.sleep(1.0)
                self.assertEqual("running", job.state)
                t0 = time.monotonic()
                job.cancel("test")
                job.wait(20)
            self.assertEqual("cancelled", job.state)
            self.assertLess(time.monotonic() - t0, 10.0)
        finally:
            _rm(d)

    def test_the_read_itself_honours_the_jobs_cancel(self):
        """`_Pipe` passes the job's flag to `read_bounded`: a cancelled read ends at once with
        Cancelled and the silent git killed — not left to the stall clock."""
        from mokata.knowledge import cancel as C
        proc = _REAL_POPEN(_SILENT, stdin=B.subprocess.PIPE, stdout=B.subprocess.PIPE)
        proc.stdin.close()
        flag = threading.Event()
        try:
            with C.scope(flag), mock.patch.object(B, "GIT_STALL_S", 120.0):
                pipe = B._Pipe(proc, "test")
                threading.Timer(0.3, flag.set).start()
                t0 = time.monotonic()
                with self.assertRaises(C.Cancelled):
                    pipe.readline()
            self.assertLess(time.monotonic() - t0, 10.0)
            self.assertIsNotNone(proc.wait(10))
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.stdout.close()
            proc.wait()

    def test_a_git_that_errors_mid_stream_is_still_reaped(self):
        """Any failure mid-read (here a garbled header) kills the git it was reading — the reap
        never waits on a git that is still stuck."""
        d = _fixture_repo()
        bad = [sys.executable, "-c", "import sys, time; sys.stdin.read(); "
               "sys.stdout.write('abc blob zz\\n'); sys.stdout.flush(); time.sleep(60)"]

        def popen(argv, *a, **kw):
            return _REAL_POPEN(bad if "cat-file" in argv else argv, *a, **kw)
        try:
            with mock.patch.object(B.subprocess, "Popen", side_effect=popen):
                t0 = time.monotonic()
                with self.assertRaises(ValueError):
                    B.cat_blobs(d, [("HEAD", "a.txt")], 1 << 20)
            self.assertLess(time.monotonic() - t0, 10.0)
        finally:
            _rm(d)

    def test_a_blob_over_the_limit_is_skipped_in_chunks_never_held(self):
        import tracemalloc
        d = _plain_repo({"huge.txt": "z" * (24 << 20)})
        try:
            head = B._git(d, "rev-parse", "HEAD")[1].strip()
            tracemalloc.start()
            base = tracemalloc.get_traced_memory()[0]
            got = B.cat_blobs(d, [(head, "huge.txt")], 1 << 20)
            peak = tracemalloc.get_traced_memory()[1] - base
            tracemalloc.stop()
            self.assertEqual(("big", 24 << 20), got[(head, "huge.txt")])
            self.assertLess(peak, 4 << 20, f"skipping a 24 MB blob allocated {peak} bytes")
        finally:
            _rm(d)

    def test_the_production_stall_bound_is_finite_and_is_the_one_used(self):
        """The silent-git tests set their own limit; this pins the DEFAULT: finite, sized for a
        stall (minutes, not seconds), and the value every pipe read actually passes."""
        self.assertGreaterEqual(B.GIT_STALL_S, 60.0)
        self.assertLessEqual(B.GIT_STALL_S, 600.0)
        from mokata import bounded_io as BIO
        seen = []
        real = BIO.read_bounded

        def spy(reader, timeout, cancelled=None):
            seen.append(timeout)
            return real(reader, timeout, cancelled)
        d = _plain_repo({"a.txt": "a\n"})
        try:
            head = B._git(d, "rev-parse", "HEAD")[1].strip()
            with mock.patch.object(BIO, "read_bounded", side_effect=spy):
                B.cat_blobs(d, [(head, "a.txt")], 1 << 20)
            self.assertTrue(seen)
            self.assertEqual({B.GIT_STALL_S}, set(seen))
        finally:
            _rm(d)

    def test_the_bound_is_silence_not_size(self):
        """A blob far bigger than one bounded read arrives whole: the clock bounds silence."""
        d = _plain_repo({"big.txt": "x" * (3 * (1 << 16) + 17) + "\n", "small.txt": "hi\n",
                         "huge.txt": "y" * (2 << 20)})
        try:
            head = B._git(d, "rev-parse", "HEAD")[1].strip()
            with mock.patch.object(B, "GIT_STALL_S", 5.0):
                got = B.cat_blobs(d, [(head, "big.txt"), (head, "small.txt"), (head, "huge.txt"),
                                      (head, "nope.txt"), (head, "small.txt")], 1 << 20)
            self.assertEqual(3 * (1 << 16) + 18, len(got[(head, "big.txt")]))
            self.assertEqual("hi\n", got[(head, "small.txt")])   # framing kept after each blob
            self.assertEqual(("big", 2 << 20), got[(head, "huge.txt")])    # skipped, not held
            self.assertEqual("", got[(head, "nope.txt")])
        finally:
            _rm(d)


class TheReferenceScanIsOnePass(unittest.TestCase):
    """M4: references are found in ONE pass over each tree — git matches a few names itself and,
    past `GREP_DIRECT_NAMES`, streams the lines once while each line's words are looked up — so
    the work grows with lines + matches, not lines x names; nothing is cut; the argv never grows
    with the change; progress reports the real phase with its own denominator."""

    def test_both_modes_find_exactly_the_same_references(self):
        d = _fixture_repo()
        try:
            side = B.Side(d, B.rev(d, "HEAD"))
            names = ["total", "checkout", "ShopError", "legacy_total", "nothing_here", "items"]
            direct = B.git_grep(side, names, "base")
            with mock.patch.object(B, "GREP_DIRECT_NAMES", 0):
                stream = B.git_grep(side, names, "base")
            self.assertEqual(direct, stream)
            self.assertTrue(direct["total"] and direct["ShopError"])
            live = B.Side(d, tree=d)
            a = B.git_grep(live, names, "head")
            with mock.patch.object(B, "GREP_DIRECT_NAMES", 0):
                self.assertEqual(a, B.git_grep(live, names, "head"))
        finally:
            _rm(d)

    def test_the_argv_never_grows_with_the_names(self):
        """10,000 names: the git command line stays the same size (Windows caps it at 32K)."""
        d = _plain_repo({"a.py": "f_1 = f_2\n"})
        seen = []
        real = B.subprocess.Popen

        def spy(argv, *a, **kw):
            seen.append(argv)
            return real(argv, *a, **kw)
        try:
            names = [f"f_{i}" for i in range(10_000)]
            with mock.patch.object(B.subprocess, "Popen", side_effect=spy):
                got = B.git_grep(B.Side(d, B.rev(d, "HEAD")), names, "head")
            self.assertEqual([("a.py", 1, "f_1 = f_2")], got["f_1"])
            self.assertEqual(got["f_1"], got["f_2"])
            grep = [a for a in seen if "grep" in a]
            self.assertTrue(grep)
            self.assertLess(max(len(" ".join(a)) for a in grep), 2000)
        finally:
            _rm(d)

    def test_progress_has_a_real_denominator_in_every_phase(self):
        """The reference phase counts files of the tree it scans (never a frozen `n/n+2` bar),
        then references, then the last steps."""
        from mokata.knowledge import cancel as C
        d = _fixture_repo()
        reps = []
        try:
            with C.scope(threading.Event()), C.reporting(
                    lambda **kw: reps.append((kw.get("done"), kw.get("total"), kw.get("unit")))):
                B.build(d, B.parse_target("live"))
            units = [u for _d, _t, u in reps if u]
            self.assertIn("files of the tree", units)
            self.assertIn("steps", units)
            tree = [(dn, t) for dn, t, u in reps if u == "files of the tree"]
            sizes = {len(B._tree_files(B.Side(d, tree=d))),
                     len(B._tree_files(B.Side(d, B.rev(d, "HEAD"))))}
            self.assertTrue(all(t in sizes for _dn, t in tree), (tree, sizes))
            # each tree's scan (head, then base) climbs to ITS end — never parked short of it
            self.assertEqual({t for _dn, t in tree}, {dn for dn, t in tree if dn == t}, tree)
            # …through real intermediate positions (the files it is in), not 0 then a jump
            for total in {t for _dn, t in tree}:
                mid = {dn for dn, t in tree if t == total and 0 < dn < total}
                self.assertGreaterEqual(len(mid), 2, (total, tree))
            for dn, t, _u in reps:
                if dn is not None and t:
                    self.assertLessEqual(dn, t)
        finally:
            _rm(d)


class AClaimedReadIsNotARead(unittest.TestCase):
    """M5: a fact cite counts only on a page the server actually SERVED — a document that merely
    claims `pages_read` it never fetched gets those cites dropped as unread."""

    def test_claimed_but_unserved_pages_drop_their_cites(self):
        d = _fixture_repo()
        try:
            with mock.patch.object(ST, "PAGE_BYTES", 1500):
                built = B.build(d, B.parse_target("session S1"))
                ST.cache_bundle(d, built)
                h = built["bundle"]["content_hash"]
                rec = ST.load_bundle(d, h)
                pages = ST.page_view(rec, 1)["pages"]
                self.assertGreater(pages, 2)
                citable = [f for f in built["bundle"]["facts"] if f["kind"] != "file"]
                last = [f for f in citable if rec["page_of"][f["id"]] == pages][0]
                first = [f for f in citable if rec["page_of"][f["id"]] == 1][0]
                ST.mark_served(d, h, 1)
                out = ST.save(d, {"content_hash": h, "model": "m",
                                  "pages_read": list(range(1, pages + 1)), "sections": [
                    {"id": "summary", "blocks": [
                        {"kind": "prose", "text": "on a served page",
                         "cites": [f"fact:{first['id']}"]},
                        {"kind": "prose", "text": "on a page never fetched",
                         "cites": [f"fact:{last['id']}"]}]}]})
            self.assertEqual(1, out["kept"])
            self.assertEqual(1, out["dropped"])
            self.assertIn("was not read", " ".join(out["dropped_reasons"]))
        finally:
            _rm(d)

    def test_nothing_served_means_no_fact_cite_counts(self):
        d = _fixture_repo()
        try:
            built = B.build(d, B.parse_target("session S1"))
            ST.cache_bundle(d, built)
            h = built["bundle"]["content_hash"]
            f = [x for x in built["bundle"]["facts"] if x["kind"] != "file"][0]
            out = ST.save(d, {"content_hash": h, "model": "m", "pages_read": [1], "sections": [
                {"id": "summary", "blocks": [{"kind": "prose", "text": "claimed",
                                              "cites": [f"fact:{f['id']}"]}]}]})
            self.assertEqual(0, out["kept"])
            self.assertIn("was not read", " ".join(out["dropped_reasons"]))
        finally:
            _rm(d)


class AWrappedKeyNeverReachesTheBundle(unittest.TestCase):
    """Merged with REDACT-FIX: the explainer's redaction is the same whole-key rule — a private
    key wrapped narrow (each body line too short for the entropy layer) is replaced BEGIN through
    END in the cached blobs, the facts and the page."""

    def test_a_16_column_key_in_a_changed_file(self):
        import base64
        import random
        rnd = random.Random(46)
        body = base64.b64encode(bytes(rnd.randrange(256) for _ in range(600))).decode()
        kind = "PRIVATE" + " KEY"
        key = (["-----BEGIN RSA " + kind + "-----"]
               + [body[i:i + 16] for i in range(0, len(body), 16)]
               + ["-----END RSA " + kind + "-----"])
        d = _plain_repo({"conf.py": "A = 1\n"})
        try:
            F.write(d, "conf.py", "A = 1\nKEY = '''\n" + "\n".join(key) + "\n'''\nB = 2\n")
            built = B.build(d, B.parse_target("live"))
            blob = built["blobs"]["head:conf.py"]
            text = json.dumps(built, ensure_ascii=False)
            for ln in key[1:-1]:
                self.assertNotIn(ln, text)
            self.assertNotIn("BEGIN RSA", blob)
            self.assertEqual(len(key) + 5, len(blob.split("\n")))   # line count kept: cites hold
            self.assertIn("B = 2", blob)
        finally:
            _rm(d)


class TheRedactionFailsClosed(unittest.TestCase):
    """r4 N2: a key the whole-key pass cannot hold back (an END after a base64 run longer than it
    buffers) replaces EVERY line — the redaction fails closed, never open."""

    def test_an_orphan_end_after_a_long_run_replaces_every_line(self):
        import base64
        from mokata.ide import usages as U
        run = [base64.b64encode(bytes((i + k) % 251 for k in range(36))).decode()
               for i in range(U.PEM_HOLD_LINES + 50)]
        text = "\n".join(["x = 1"] + run + ["-----END RSA " + "PRIVATE" + " KEY-----", "y = 2"])
        out, n = G.redact(text)
        lines = out.split("\n")
        self.assertEqual(len(text.split("\n")), len(lines))
        self.assertEqual(len(lines), n)
        self.assertTrue(all("redacted by mokata" in ln for ln in lines))
        for ln in run[:5] + run[-5:]:
            self.assertNotIn(ln, out)
