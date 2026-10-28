"""0.0.23 stage C3 — `mokata ide` graph views (ID-VIS-CORE, ruling R3): the blast-radius ripple,
symbol focus, the call-path tracer and the untested-path finder, drawn with VENDORED Cytoscape.js
(+ fcose, + dagre — MIT, no CDN).

Asserted:
  * every edge is justified by a `mokata query` answer — its kind, its `basis`, its site;
  * the ripple: the symbol at the centre, callers by hop, importers on ring 1, tests on the outer
    ring; a chain no test runs is `untested`, but ONLY when it is structural — lexical evidence
    is `unknown`, and a symbol the graph has no definition for claims nothing;
  * focus = callers left / callees right; the path tracer finds the shortest call path or says none;
  * past a page (PAGE_NODES) the answer says `more`, and the next page resumes it (P37);
  * the bundle is reproducible: pinned versions, sha256s that match, LICENSE files, served from
    /static/vendor/ under the strict CSP, packaged into the wheel, and no CDN URL anywhere.
"""
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata.ide import nav
from mokata.ide import visuals as V
from mokata.init import init_repo
from mokata.knowledge.query import QueryResult, Reference

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
STATIC = os.path.join(ROOT, "src", "mokata", "ide", "static")
VENDOR = os.path.join(STATIC, "vendor")


def _silent(*_a, **_k):
    pass


def _write(d, rel, text):
    p = os.path.join(d, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as fh:
        fh.write(text.encode("utf-8"))


def _fixture(d):
    init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
    _write(d, "src/app/core.py",
           "def parse(text):\n    return text.split(',')\n\n\ndef total(text):\n"
           "    return len(parse(text))\n\n\nclass Child:\n    def run(self):\n"
           "        return total('a,b')\n")
    _write(d, "src/app/cli.py", "from app.core import total\n\n\ndef main():\n    print(total('x'))\n")
    _write(d, "tests/test_core.py",
           "from app.core import parse\n\n\ndef test_parse():\n    assert parse('a') == ['a']\n")


def _by_label(payload):
    return {n["label"]: n for n in payload["nodes"]}


class EveryEdgeIsJustified(unittest.TestCase):

    def test_each_calls_and_imports_edge_is_in_the_query_answer_it_names(self):
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            g = V.ripple(d, "parse", 3)
            nodes = {n["id"]: n for n in g["nodes"]}
            self.assertTrue(g["edges"])
            for e in g["edges"]:
                with self.subTest(edge=e):
                    self.assertIn(e["basis"], ("structural", "lexical", "verified_empty"))
                    dst = nodes[e["target"]]["label"]
                    if e["kind"] == "CALLS":
                        ans = nav.run(d, "callers", dst)
                        sites = {(f"{r.path}:{r.line}", r.symbol) for r in ans.references}
                        self.assertIn((e["site"], nodes[e["source"]].get("symbol")), sites)
                    else:
                        self.assertEqual("IMPORTS", e["kind"])
                        ans = nav.run(d, "imports", dst)
                        self.assertIn(e["site"], {f"{r.path}:{r.line}" for r in ans.references})
                    self.assertEqual(ans.basis, e["basis"])

    def test_focus_and_path_edges_come_from_callers_and_callees(self):
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            f = V.focus(d, "total")
            by = _by_label(f)
            self.assertEqual({"main", "run"}, {k for k, n in by.items() if n.get("side") == "left"})
            self.assertEqual({"parse", "len"}, {k for k, n in by.items() if n.get("side") == "right"})
            callees = {f"{r.path}:{r.line}" for r in nav.run(d, "callees", "total").references}
            for e in f["edges"]:
                if e["source"] == "sym:total":
                    self.assertIn(e["site"], callees)
            p = V.path(d, "run", "parse", 4)
            self.assertTrue(p["found"])
            self.assertEqual(["run", "total", "parse"], [n["label"] for n in p["nodes"]])
            self.assertEqual(2, len(p["edges"]))
            none = V.path(d, "parse", "run", 4)
            self.assertFalse(none["found"])
            self.assertEqual([], none["edges"])
            self.assertIn("no call path", none["note"])


class TheRipple(unittest.TestCase):

    def test_rings_tests_outside_and_the_chains_no_test_runs(self):
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            g = V.ripple(d, "parse", 3)
            by = _by_label(g)
            self.assertEqual(("center", 0, "covered"),
                             (by["parse"]["kind"], by["parse"]["ring"], by["parse"]["coverage"]))
            self.assertEqual(1, by["total"]["ring"])
            self.assertEqual(2, by["main"]["ring"])
            self.assertEqual(4, by["test_parse"]["ring"], "tests sit on the outer ring")
            self.assertEqual("test", by["test_parse"]["kind"])
            self.assertEqual("untested", by["total"]["coverage"])
            self.assertEqual(sorted([["main", "total", "parse"], ["run", "total", "parse"]]),
                             sorted(g["untested_chains"]))

    def test_importers_are_ring_one_with_an_imports_edge(self):
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            g = V.ripple(d, "total", 2)
            imp = [e for e in g["edges"] if e["kind"] == "IMPORTS"]
            self.assertEqual(["src/app/cli.py:1"], [e["site"] for e in imp])
            self.assertEqual(1, _by_label(g)["src/app/cli.py"]["ring"])

    def test_lexical_evidence_never_marks_anything_untested(self):
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            real = nav.run

            def lexical(root, kind, target, depth=2, layer=None):
                if kind == "callers" and target == "parse":
                    return QueryResult(kind=kind, target=target, backend="grep", basis="lexical",
                                       references=[Reference("src/app/core.py", 6, "", None)])
                return real(root, kind, target, depth, layer)
            with mock.patch.object(nav, "run", side_effect=lexical):
                g = V.ripple(d, "parse", 2)
            site = _by_label(g)["src/app/core.py:6"]
            self.assertEqual(("site", "unknown"), (site["kind"], site["coverage"]))
            self.assertEqual([], g["untested_chains"])
            self.assertEqual({"lexical"}, {e["basis"] for e in g["edges"] if e["kind"] == "CALLS"})

    def test_a_symbol_the_graph_does_not_hold_claims_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            g = V.ripple(d, "no_such_symbol_xyz", 2)
            self.assertEqual(0, g["defs"])
            self.assertEqual("unknown", g["nodes"][0]["coverage"])
            self.assertEqual([], g["untested_chains"])

    def test_past_a_page_the_answer_says_more_and_the_next_page_resumes(self):
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            with mock.patch.object(V, "PAGE_NODES", 2):
                g = V.ripple(d, "parse", 3)
                self.assertTrue(g["more"])
                self.assertLessEqual(len(g["nodes"]), 2)
                g2 = V.ripple(d, "parse", 3, page=2)
            self.assertGreater(len(g2["nodes"]), len(g["nodes"]))
            self.assertEqual(2, g2["page"])


class TheEndpoint(unittest.TestCase):

    def _client(self, root, demo=False):
        from starlette.testclient import TestClient
        from mokata.ide.server import AppState, build_app
        c = TestClient(build_app(AppState(root, "tok", 9999, None, demo)),
                       base_url="http://127.0.0.1:9999")
        c.get("/pair?t=tok")
        return c

    def test_views_bad_requests_and_the_demo(self):
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            c = self._client(d)
            for view in ("ripple", "focus", "untested"):
                r = c.get("/api/graph", params={"view": view, "symbol": "total"})
                self.assertEqual(200, r.status_code, view)
                self.assertTrue(r.json()["nodes"])
            p = c.get("/api/graph", params={"view": "path", "symbol": "run", "to": "parse",
                                            "depth": 4}).json()
            self.assertTrue(p["found"])
            for bad in ({"view": "rm"}, {"view": "ripple"}, {"view": "path", "symbol": "x"}):
                self.assertEqual(400, c.get("/api/graph", params=bad).status_code, bad)
        demo = self._client("", demo=True).get("/api/graph?view=ripple&symbol=parse").json()
        self.assertEqual((True, []), (demo["sample"], demo["edges"]))

    def test_vendor_files_are_whitelisted_and_served_under_the_strict_csp(self):
        from mokata.ide.server import VENDOR_FILES
        c = self._client("", demo=True)
        for name in VENDOR_FILES:
            r = c.get("/static/vendor/" + name)
            self.assertEqual(200, r.status_code, name)
            self.assertIn("javascript", r.headers["content-type"])
            self.assertIn("script-src 'self'", r.headers["content-security-policy"])
        for bad in ("VERSIONS.json", "LICENSE.cytoscape.txt", "..%2F..%2Fserver.py", "nope.js"):
            self.assertEqual(404, c.get("/static/vendor/" + bad).status_code, bad)
        from starlette.testclient import TestClient
        from mokata.ide.server import AppState, build_app
        raw = TestClient(build_app(AppState("", "tok", 9999, None, True)),
                         base_url="http://127.0.0.1:9999")
        self.assertEqual(401, raw.get("/static/vendor/cytoscape.min.js").status_code)


class TheBundleIsReproducible(unittest.TestCase):

    def setUp(self):
        with open(os.path.join(VENDOR, "VERSIONS.json"), encoding="utf-8") as fh:
            self.pins = json.load(fh)

    def test_every_vendored_file_matches_its_pinned_sha256_and_ships_its_mit_license(self):
        from mokata.ide.server import VENDOR_FILES
        self.assertEqual(set(VENDOR_FILES), {r["file"] for r in self.pins["files"]})
        self.assertEqual(list(VENDOR_FILES), self.pins["load_order"])
        for row in self.pins["files"]:
            with self.subTest(file=row["file"]):
                with open(os.path.join(VENDOR, row["file"]), "rb") as fh:
                    self.assertEqual(row["sha256"], hashlib.sha256(fh.read()).hexdigest())
                self.assertEqual("MIT", row["license"])
                self.assertTrue(row["tarball"].startswith("https://registry.npmjs.org/"))
                self.assertTrue(row["integrity"].startswith("sha512-"))
                with open(os.path.join(VENDOR, row["license_file"]), encoding="utf-8") as fh:
                    self.assertIn("Permission is hereby granted, free of charge", fh.read())
        with open(os.path.join(VENDOR, "LICENSE.dagre.txt"), encoding="utf-8") as fh:
            self.assertIn("Chris Pettitt", fh.read())

    def test_the_page_loads_them_from_static_vendor_and_no_cdn_anywhere(self):
        ours = ["index.html", "app.js", "nav.js", "search.js", "graph.js", "boot.js", "app.css"]
        for name in ours:
            with open(os.path.join(STATIC, name), encoding="utf-8") as fh:
                text = fh.read()
            with self.subTest(name=name):
                self.assertIsNone(re.search(r"(src|href)\s*=\s*[\"']https?://", text))
                for cdn in ("cdnjs", "jsdelivr", "unpkg", "googleapis", "cdn."):
                    self.assertNotIn(cdn, text)
        with open(os.path.join(STATIC, "graph.js"), encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn('"/static/vendor/"', js)
        for f in self.pins["load_order"]:
            self.assertIn(f'"{f}"', js)

    def test_cytoscape_cannot_inject_an_inline_style_under_our_csp(self):
        with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn('id="__________cytoscape_stylesheet"', html)
        with open(os.path.join(STATIC, "app.css"), encoding="utf-8") as fh:
            self.assertIn(".__________cytoscape_container { position: relative; }", fh.read())

    def test_the_wheel_packages_the_vendor_directory(self):
        with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as fh:
            toml = fh.read()
        self.assertIn('"ide/static/vendor/*"', toml)
        # CORPUS: THE WORKING TREE — what the wheel's package-data glob would pick up.
        for name in os.listdir(VENDOR):
            self.assertTrue(fnmatch.fnmatch(f"ide/static/vendor/{name}", "ide/static/vendor/*"))

    def test_the_offline_check_of_the_vendor_script_passes(self):
        import sys
        p = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "vendor_ide_graph_libs.py"),
                            "--check"], capture_output=True, text=True, timeout=60,
                           stdin=subprocess.DEVNULL)
        self.assertEqual(0, p.returncode, p.stdout + p.stderr)


class ThePage(unittest.TestCase):

    def test_four_views_and_the_legend(self):
        with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        for v in ("ripple", "focus", "path"):            # P37 §5.1: three tabs + a toggle
            self.assertIn(f'data-gview="{v}"', html)
        self.assertIn('data-gview-toggle="untested"', html)
        for needle in ("structural edge", "lexical edge", "reached by no test", 'id="cy"'):
            self.assertIn(needle, html)
        with open(os.path.join(STATIC, "graph.js"), encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn("edge[basis = 'lexical']", js)
        self.assertIn('"line-style": "dashed"', js)
        self.assertIn("fcose", js)
        self.assertIn("dagre", js)


def setUpModule():
    # 0.0.26 O11 — this module's expectations are the CORE install's: no external code graph on
    # the machine. Pinned, so a venv with code-review-graph (`mokata[full]`) means the same thing;
    # what is true WITH it present is graded in test_o11_crg_present (and the sibling below, if any).
    from _support import hide_external_graphs
    hide_external_graphs()


if __name__ == "__main__":
    unittest.main()
