"""0.0.23 stage C1 — `mokata ide` navigation (ID.S2): the changed-files navigator, ⌘P, the tree,
a read-only file with its changes, the outline, and the structural questions.

What is asserted, each against the behaviour a user relies on:
  * every structural answer the IDE shows is `mokata query --json`'s, BYTE FOR BYTE, for every kind;
  * each answer names its `basis` (structural / verified_empty / lexical);
  * ⌘P ranks CHANGED files first, then files, then symbols, and NAMES the matcher that answered
    (rapidfuzz, or the labelled pure-Python floor when rapidfuzz is not importable);
  * the tree is the graph's own walk — a venv of any name, vendor dirs and hidden dirs never appear;
  * a file's change markers are its regions against HEAD; the outline comes from the graph's parser;
  * impact: a changed symbol no test reaches is `untested` ONLY on a structural answer;
  * every path-taking endpoint refuses traversal, absolute paths and a symlink out of the tree;
  * the page: keyboard handlers ignore keystrokes typed into fields; repo text never becomes markup;
    the unseen marks survive a storage that throws.
"""
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata.ide import nav
from mokata.init import init_repo
from mokata.knowledge.query import QUERY_KINDS

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "..", "src", "mokata", "ide", "static")

CORE_V1 = ("def parse(text):\n    return text.split(',')\n\n\ndef total(text):\n"
           "    return len(parse(text))\n\n\nclass Base:\n    pass\n\n\nclass Child(Base):\n"
           "    def run(self):\n        return total('a,b')\n")
CORE_V2 = ("def parse(text):\n    if not text:\n        return []\n"
           "    return [p.strip() for p in text.split(',')]\n\n\ndef total(text):\n"
           "    return len(parse(text)) + 0\n\n\nclass Base:\n    pass\n\n\nclass Child(Base):\n"
           "    def run(self):\n        return total('a,b')\n")


def _silent(*_a, **_k):
    pass


def _git(d, *a):
    subprocess.run(["git", "-C", d, *a], check=True, capture_output=True, stdin=subprocess.DEVNULL)


def _write(d, rel, text):
    p = os.path.join(d, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as fh:
        fh.write(text.encode("utf-8"))


def _fixture(d, git=True):
    """A small Python repo: `parse` is tested, `total` is not; a venv and node_modules beside it."""
    if git:
        _git(d, "init", "-q", "-b", "main")
        _git(d, "config", "user.email", "t@t")
        _git(d, "config", "user.name", "t")
    init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
    _write(d, "src/app/core.py", CORE_V1)
    _write(d, "src/app/cli.py", "from app.core import total\n\n\ndef main():\n    print(total('x'))\n")
    _write(d, "tests/test_core.py",
           "from app.core import parse\n\n\ndef test_parse():\n    assert parse('a') == ['a']\n")
    _write(d, "venv/pyvenv.cfg", "home = /usr\n")
    _write(d, "venv/lib/site.py", "def vendored():\n    pass\n")
    _write(d, "node_modules/left/index.py", "def left():\n    pass\n")
    if git:
        _git(d, "add", "-A")
        _git(d, "commit", "-qm", "init")
    _write(d, "src/app/core.py", CORE_V2)


def _client(root, demo=False):
    from starlette.testclient import TestClient
    from mokata.ide.server import AppState, build_app
    state = AppState(root, "tok", 9999, None, demo)
    c = TestClient(build_app(state), base_url="http://127.0.0.1:9999")
    c.get("/pair?t=tok")
    return c


def _cli_json(d, *argv):
    from mokata import cli
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.main(["query", *argv, "--json", "--path", d])
    return rc, buf.getvalue()


class SameAnswerAsMokataQuery(unittest.TestCase):
    """The C1 exit criterion: the IDE's answer IS `mokata query`'s, byte for byte."""

    def test_every_kind_is_byte_for_byte_the_cli_json(self):
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            c = _client(d)
            for kind in QUERY_KINDS:
                for target in ("parse", "total", "Base", "app.core", "nothing_here_xyz"):
                    with self.subTest(kind=kind, target=target):
                        rc, out = _cli_json(d, kind, target)
                        self.assertEqual(0, rc)
                        r = c.get("/api/query", params={"kind": kind, "target": target})
                        self.assertEqual(200, r.status_code)
                        self.assertEqual(out.encode("utf-8"), r.content)

    def test_blast_radius_depth_is_passed_through_identically(self):
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            c = _client(d)
            for depth in (1, 3):
                _, out = _cli_json(d, "blast_radius", "parse", "--depth", str(depth))
                r = c.get("/api/query", params={"kind": "blast_radius", "target": "parse",
                                                "depth": depth})
                self.assertEqual(out.encode("utf-8"), r.content)

    def test_each_answer_names_its_basis(self):
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            c = _client(d)
            callers = c.get("/api/query", params={"kind": "callers", "target": "parse"}).json()
            self.assertEqual("structural", callers["basis"])
            self.assertIn("tests/test_core.py", {r["path"] for r in callers["references"]})
            leaf = c.get("/api/query", params={"kind": "callers", "target": "run"}).json()
            self.assertEqual("verified_empty", leaf["basis"], "a leaf is an answer, not an absence")
            refs = c.get("/api/query", params={"kind": "refs", "target": "parse"}).json()
            self.assertEqual("lexical", refs["basis"], "refs is the lexical superset, said so")

    def test_the_cli_json_is_one_parseable_line_and_the_text_mode_is_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            _, out = _cli_json(d, "callers", "parse")
            self.assertEqual(1, out.count("\n"))
            self.assertEqual("callers", json.loads(out)["kind"])
            from mokata import cli
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli.main(["query", "callers", "parse", "--path", d])
            self.assertTrue(buf.getvalue().startswith("callers(parse) via ast [graph]"))

    def test_a_bad_kind_or_empty_target_is_refused_not_guessed(self):
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            c = _client(d)
            self.assertEqual(400, c.get("/api/query?kind=rm&target=x").status_code)
            self.assertEqual(400, c.get("/api/query?kind=callers&target=").status_code)

    def test_a_repo_mokata_cannot_load_is_said_not_a_500_page(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "a.py", "def f():\n    pass\n")
            r = _client(d).get("/api/query?kind=defs&target=f")
            self.assertEqual(409, r.status_code)
            self.assertIn("error", r.json())


class QuickOpen(unittest.TestCase):

    def test_changed_files_rank_first_then_files_then_symbols(self):
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            c = _client(d)
            r = c.post("/api/quickopen", json={"q": "core", "changed": ["src/app/core.py"]},
                       headers={"Origin": "http://127.0.0.1:9999"}).json()
            self.assertEqual(["src/app/core.py"], [x["path"] for x in r["changed"]])
            self.assertNotIn("src/app/core.py", [x["path"] for x in r["files"]],
                             "a changed file is listed once, in the changed section")
            self.assertIn("tests/test_core.py", [x["path"] for x in r["files"]])
            self.assertIn(r["matcher"], ("rapidfuzz", "python-floor"))
            keys = list(r)
            self.assertLess(keys.index("changed"), keys.index("files"))
            self.assertLess(keys.index("files"), keys.index("symbols"))

    def test_at_asks_symbols_only_from_the_graph_with_a_basis(self):
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            r = nav.quick_open(d, "@tot", ["src/app/core.py"])
            self.assertEqual([], r["changed"])
            self.assertEqual([], r["files"])
            self.assertEqual(("total", "src/app/core.py", 7),
                             (r["symbols"][0]["name"], r["symbols"][0]["path"],
                              r["symbols"][0]["line"]))
            self.assertEqual("structural", r["symbols"][0]["basis"])

    def test_without_rapidfuzz_the_floor_answers_and_says_so(self):
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            with mock.patch.dict(sys.modules, {"rapidfuzz": None}):
                r = nav.quick_open(d, "core", [])
            self.assertEqual("python-floor", r["matcher"])
            self.assertIn("rapidfuzz", r["matcher_note"])

    def test_vendored_and_venv_symbols_never_rank(self):
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            names = {row[0] for row in nav.symbol_index(d)}
            self.assertIn("parse", names)
            self.assertNotIn("vendored", names)
            self.assertNotIn("left", names)


class TheFloorMatcher(unittest.TestCase):

    def test_subsequence_only_and_a_basename_hit_beats_a_directory_hit(self):
        with mock.patch.dict(sys.modules, {"rapidfuzz": None}):
            rows, m = nav.fuzzy("base", ["src/base/zzz.py", "src/engine/baseline.py", "x/y.py"])
        self.assertEqual("python-floor", m)
        self.assertEqual("src/engine/baseline.py", rows[0][0])
        self.assertNotIn("x/y.py", [r[0] for r in rows])

    def test_rapidfuzz_ranks_the_same_matches_the_floor_finds_and_says_so(self):
        try:
            import rapidfuzz  # noqa: F401
        except ImportError:
            self.skipTest("rapidfuzz is not installed here — the floor answers, labelled")
        cands = ["src/base/zzz.py", "src/engine/baseline.py", "x/y.py", "docs/b_a_s_e.md"]
        rows, m = nav.fuzzy("base", cands)
        self.assertEqual("rapidfuzz", m)
        with mock.patch.dict(sys.modules, {"rapidfuzz": None}):
            floor, _ = nav.fuzzy("base", cands)
        self.assertEqual({r[0] for r in floor}, {r[0] for r in rows},
                         "the accelerator changes the ranking, never which files match")

    def test_no_match_is_none_and_order_is_case_insensitive(self):
        self.assertIsNone(nav._floor_score("zq", "abc.py"))
        self.assertIsNotNone(nav._floor_score("rtc", "src/RunTestCommand.py"))

    def test_regex_metacharacters_in_the_query_are_literal(self):
        with mock.patch.dict(sys.modules, {"rapidfuzz": None}):
            rows, _ = nav.fuzzy("a.(b", ["a.(b.py", "axxb.py"])
        self.assertEqual(["a.(b.py"], [r[0] for r in rows])


class TreeFileOutline(unittest.TestCase):

    def test_the_tree_is_the_graphs_own_walk(self):
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            files = nav.tree(d)["files"]
            self.assertIn("src/app/core.py", files)
            for bad in ("venv/", "node_modules/", ".mokata/", ".git/"):
                self.assertFalse([f for f in files if f.startswith(bad)], bad)

    def test_a_file_carries_its_change_regions_against_head(self):
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            f = nav.read_file(d, "src/app/core.py")
            self.assertEqual(CORE_V2, f["text"])
            self.assertEqual([(2, 4, "mod"), (8, 8, "mod")],
                             [(c["start"], c["end"], c["kind"]) for c in f["changes"]])
            _write(d, "src/app/new.py", "def fresh():\n    return 2\n")
            new = nav.read_file(d, "src/app/new.py")
            self.assertEqual([(1, 2, "add")], [(c["start"], c["end"], c["kind"])
                                               for c in new["changes"]])

    def test_a_removed_block_is_marked_where_it_was(self):
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            _write(d, "src/app/cli.py", "from app.core import total\n")
            f = nav.read_file(d, "src/app/cli.py")
            self.assertEqual(["del"], [c["kind"] for c in f["changes"]])

    def test_outside_git_a_file_opens_with_no_markers(self):
        with tempfile.TemporaryDirectory() as d:
            _fixture(d, git=False)
            self.assertEqual([], nav.read_file(d, "src/app/core.py")["changes"])

    def test_the_outline_comes_from_the_graphs_parser_with_spans_and_nesting(self):
        ol = nav.outline("src/app/core.py", CORE_V2)
        self.assertEqual("structural", ol["basis"])
        rows = [(s["name"], s["kind"], s["line"], s["end"], s["depth"]) for s in ol["symbols"]]
        self.assertEqual([("parse", "function", 1, 4, 0), ("total", "function", 7, 8, 0),
                          ("Base", "class", 11, 12, 0), ("Child", "class", 15, 17, 0),
                          ("run", "method", 16, 17, 1)], rows)
        self.assertEqual("run", nav.symbol_at(ol["symbols"], 17)["name"])

    def test_an_unparseable_or_foreign_file_says_why_it_has_no_outline(self):
        self.assertIn("does not parse", nav.outline("a.py", "def (:\n")["note"])
        self.assertIn("no outline", nav.outline("README.md", "# hi\n")["note"])
        self.assertEqual([], nav.outline("a.py", None)["symbols"])


class Impact(unittest.TestCase):

    def test_a_tested_symbol_is_covered_and_an_untested_one_is_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            im = nav.impact(d, "src/app/core.py", "2-4,8-8")
            by = {s["name"]: s for s in im["symbols"]}
            self.assertEqual("covered", by["parse"]["verdict"])
            self.assertTrue(any(t.startswith("tests/test_core.py:") for t in by["parse"]["tests"]))
            self.assertEqual("untested", by["total"]["verdict"])
            self.assertEqual("untested", im["status"])
            self.assertEqual("structural", by["parse"]["callers"]["basis"])

    def test_only_a_structural_answer_may_claim_untested(self):
        from mokata.knowledge.query import QueryResult
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            lexical = QueryResult(kind="blast_radius", target="total", references=[],
                                  backend="grep", basis="lexical")
            real = nav.run

            def fake(root, kind, target, depth=2, layer=None):
                if kind == "blast_radius":
                    return lexical
                return real(root, kind, target, depth, layer)
            with mock.patch.object(nav, "run", side_effect=fake):
                im = nav.impact(d, "src/app/core.py", "8-8")
            self.assertEqual("unknown", im["symbols"][0]["verdict"])
            self.assertEqual("unknown", im["status"])

    def test_a_test_file_is_a_test_and_lines_outside_any_def_name_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            self.assertEqual("test", nav.impact(d, "tests/test_core.py", "1-5")["status"])
            self.assertEqual("no-symbols", nav.impact(d, "src/app/core.py", "")["status"])

    def test_junk_line_specs_are_ignored_not_raised(self):
        self.assertEqual([(3, 9), (14, 14)], nav.parse_lines("3-9,x,14,-2,9-3,"))


class PathEndpointsAreContained(unittest.TestCase):
    """Every path-taking endpoint: traversal, absolute paths, a symlink out — refused, unread."""

    ENDPOINTS = ("/api/file", "/api/outline", "/api/impact")

    def test_traversal_absolute_drive_and_nul_are_refused(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as other:
            _fixture(d)
            secret = os.path.join(other, "secret.py")
            _write(other, "secret.py", "TOKEN = 'do-not-leak'\n")
            bad = ["../" + os.path.basename(other) + "/secret.py", "src/../../x.py", secret,
                   os.path.join(d, "src", "app", "core.py"), "C:/Windows/win.ini",
                   "C:\\Windows\\win.ini", "\\\\server\\share\\x.py", "src/app/core.py\0.txt",
                   ".mokata/manifest.json", ""]
            c = _client(d)
            for ep in self.ENDPOINTS:
                for p in bad:
                    with self.subTest(ep=ep, p=p):
                        r = c.get(ep, params={"path": p})
                        self.assertEqual(400, r.status_code)
                        self.assertNotIn("do-not-leak", r.text)

    def test_a_symlink_out_of_the_tree_is_refused(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as other:
            _fixture(d)
            _write(other, "secret.py", "TOKEN = 'do-not-leak'\n")
            try:
                os.symlink(os.path.join(other, "secret.py"), os.path.join(d, "src", "link.py"))
                os.symlink(other, os.path.join(d, "src", "linkdir"))
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are not available here")
            c = _client(d)
            for ep in self.ENDPOINTS:
                for p in ("src/link.py", "src/linkdir/secret.py"):
                    with self.subTest(ep=ep, p=p):
                        r = c.get(ep, params={"path": p})
                        self.assertEqual(400, r.status_code)
                        self.assertNotIn("do-not-leak", r.text)

    def test_the_new_routes_keep_the_pairing_and_host_guards(self):
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            from starlette.testclient import TestClient
            from mokata.ide.server import AppState, build_app
            raw = TestClient(build_app(AppState(d, "tok", 9999, None, False)),
                             base_url="http://127.0.0.1:9999")
            for path in ("/api/tree", "/api/file?path=src/app/core.py", "/api/query?kind=defs&target=x",
                         "/api/impact?path=src/app/core.py", "/api/caps"):
                self.assertEqual(401, raw.get(path).status_code, path)
            c = _client(d)
            self.assertEqual(403, c.get("/api/tree", headers={"Host": "evil:9999"}).status_code)
            self.assertEqual(403, c.post("/api/quickopen", json={"q": "x"},
                                         headers={"Origin": "https://evil.example"}).status_code)


class DemoStaysSample(unittest.TestCase):

    def test_every_navigation_answer_in_the_demo_says_sample(self):
        c = _client("", demo=True)
        for path in ("/api/tree", "/api/file?path=src/parser.py", "/api/outline?path=src/parser.py",
                     "/api/query?kind=callers&target=parse", "/api/impact?path=src/parser.py"):
            with self.subTest(path=path):
                self.assertIs(True, c.get(path).json()["sample"])
        r = c.post("/api/quickopen", json={"q": "par", "changed": ["src/parser.py"]},
                   headers={"Origin": "http://127.0.0.1:9999"}).json()
        self.assertIs(True, r["sample"])
        self.assertEqual("src/parser.py", r["changed"][0]["path"])


class TheChangedLinesAreChangesNotContext(unittest.TestCase):
    """A hunk's context lines are not changes: the symbol a hunk is "in", and the lines sent to
    /api/impact (which decide `untested`), come from its +/- lines only. Found in the browser: a
    one-hunk diff over lines 1-11 was labelled "in Base" — a class whose only lines were context."""

    def test_only_added_and_removed_lines_count(self):
        import shutil
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not on PATH — the page logic is exercised in a browser instead")
        with open(os.path.join(STATIC, "app.js"), encoding="utf-8") as fh:
            src = fh.read()
        fns = "".join(re.search(rf"function {n}\([\s\S]*?\n\}}\n", src).group(0)
                      for n in ("hunkRange", "hunkChanged", "changedLines"))
        probe = fns + """
const h = {header: '@@ -1,9 +1,11 @@', lines: [[' ',1,1,'a'],['-',2,null,'x'],['+',null,2,'b'],
  ['+',null,3,'c'],['+',null,4,'d'],[' ',3,5,''],[' ',4,6,''],[' ',5,7,''],['-',6,null,'y'],
  ['+',null,8,'z'],[' ',7,9,''],[' ',8,10,''],[' ',9,11,'class Base:']]};
const del = {header: '@@ -5,3 +5,2 @@', lines: [[' ',5,5,'a'],['-',6,null,'x'],[' ',7,6,'b']]};
console.log(JSON.stringify([changedLines({hunks: [h]}), changedLines({hunks: [del]})]));
"""
        p = subprocess.run([node, "-e", "const MK = {};\n" + probe], capture_output=True, text=True,
                           timeout=60, stdin=subprocess.DEVNULL)
        self.assertEqual(0, p.returncode, p.stderr)
        self.assertEqual(["2-4,8-8", "6-6"], json.loads(p.stdout))


class ThePage(unittest.TestCase):

    def _js(self):
        out = {}
        # CORPUS: THE WORKING TREE — the static files the page is served (they ship as package data).
        for name in sorted(os.listdir(STATIC)):
            if name.endswith(".js"):
                with open(os.path.join(STATIC, name), encoding="utf-8") as fh:
                    out[name] = fh.read()
        return out

    def test_no_script_puts_repo_text_into_the_dom_as_html(self):
        for name, js in self._js().items():
            for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write",
                        "eval(", "new Function"):
                self.assertNotIn(bad, js, f"{name}: {bad}")

    def test_every_served_script_is_whitelisted_and_loaded(self):
        from mokata.ide.server import STATIC_FILES
        with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        for name in self._js():
            self.assertIn(name, STATIC_FILES)
            self.assertIn(f'src="/static/{name}"', html)
        self.assertLess(html.index("app.js"), html.index("boot.js"), "boot runs last")

    def test_keyboard_commands_ignore_typing_in_fields(self):
        js = self._js()["app.js"]
        typing = re.search(r"function typing\(ev\) \{(.*?)\n\}", js, re.S).group(1)
        for tag in ('"input"', '"textarea"', '"select"', "isContentEditable"):
            self.assertIn(tag, typing)
        handler = re.search(r"function onKey\(ev\) \{(.*?)\n\}", js, re.S).group(1)
        guard = handler.index("typing(ev)")
        for key in ('case "]"', 'case "["', 'case "j"', 'case "k"', 'case "u"', 'case "e"'):
            self.assertGreater(handler.index(key), guard, f"{key} is reachable while typing")

    def test_the_design_shell_is_there(self):
        with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        for needle in ('data-mode="review"', 'data-mode="navigate"', 'data-mode="graph"',
                       'id="qo-open"', 'class="rail"', 'id="nav-list"', 'data-filter="unseen"',
                       'data-filter="untested"', 'data-filter="tests"', 'id="mark-seen"',
                       'id="pos-label"', 'id="only-changed"', 'id="outline"', 'id="ref-tabs"',
                       'id="nav-back"', 'id="nav-fwd"', 'id="change-float"', 'id="insp-blast"'):
            self.assertIn(needle, html)

    def test_unseen_marks_survive_a_storage_that_throws(self):
        js = self._js()["app.js"]
        for fn in ("loadSeen", "saveSeen"):
            body = re.search(rf"function {fn}\([^)]*\) \{{(.*?)\n\}}", js, re.S).group(1)
            self.assertIn("try", body)
            self.assertIn("catch", body)
        self.assertIn("localStorage", js)


if __name__ == "__main__":
    unittest.main()
