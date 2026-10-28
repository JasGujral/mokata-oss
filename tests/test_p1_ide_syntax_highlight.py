"""0.0.26 P1 — ID-SYNTAX-HIGHLIGHT: the IDE colours code on the SERVER (Pygments) and sends token
spans, for the file viewer (windowed, any size), every diff, and spec / AC code blocks.

Business rules — what a person reading code in `mokata ide` relies on:
  * A Python file comes back with its keywords and strings marked ON THE RIGHT LINES — the row
    for line N is line N's text, split into classed spans that join back to exactly that text.
  * A huge file is viewable: a window of a 200,000-line file answers quickly and without reading
    the file whole (time and memory bounded), and a window deep in the file is the right lines.
  * A string that opened above a chunk boundary still colours the lines below it (lead-in).
  * The cache is bounded (items and bytes), serves a repeat from memory, and never serves a
    stale chunk after the file changes.
  * In a diff, each line is coloured as it is in ITS OWN version of the file: a removed line by
    the old text around it, an added line by the new — and every row maps to its diff line.
  * Language is found by file name, else by content (a shebang); unknown is plain and says so.
  * Plain text is never silent: past the size cap, on a minified line, or with Pygments absent,
    the answer says why. Binary is not served.
  * Spec / AC fenced code is coloured by its fence language.
  * The answer is token CLASSES and text, never markup: `<script>` in the code stays text.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import shutil
import statistics
import subprocess
import tempfile
import time
import tracemalloc
import unittest
from pathlib import Path
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata.ide import highlight as H

HAVE_PYGMENTS = H.available()
NEED = unittest.skipUnless(HAVE_PYGMENTS, "pygments is not installed — the [ide] extra is incomplete")
STATIC = Path(__file__).resolve().parents[1] / "src" / "mokata" / "ide" / "static"
TOKEN, PORT = "tok", 9999
BASE = f"http://127.0.0.1:{PORT}"

PY = ('import os\n'
      '\n'
      'def greet(name):\n'
      '    """Say hello."""\n'
      '    msg = "hello " + name  # a comment\n'
      '    return msg.upper() if name else "<script>alert(1)</script>"\n')


def _write(d, rel, text, mode="w"):
    p = os.path.join(d, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if mode == "wb":
        with open(p, "wb") as fh:
            fh.write(text)
    else:
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    return p


def _client(root, demo=False):
    from starlette.testclient import TestClient
    from mokata.ide.server import AppState, build_app
    c = TestClient(build_app(AppState(root, TOKEN, PORT, None, demo)), base_url=BASE,
                   follow_redirects=False)
    c.get(f"/pair?t={TOKEN}")
    return c


def _classes(row, text):
    """The classes of the spans covering `text` in one row."""
    return {c for c, t in row if text in t}


def _joined(row):
    return "".join(t for _c, t in row)


def _big_python(lines):
    """A generated Python file: every line names its own line number."""
    out = []
    for n in range(1, lines + 1):
        k = n % 4
        out.append(f"def f_{n}(x):" if k == 1 else
                   f"    s = 'line {n}'" if k == 2 else
                   f"    return x + {n}  # c{n} -- a comment long enough to make the file big"
                   if k == 3 else "")
    return "\n".join(out) + "\n"


class Fresh(unittest.TestCase):
    def setUp(self):
        for c in (H.CHUNKS, H.INDEXES, H.SNIPPETS):
            c.clear()
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)


# ============================================================================ the file viewer

@NEED
class APythonFileIsColouredOnTheRightLines(Fresh):
    def test_keywords_and_strings_land_on_their_lines_over_http(self):
        _write(self.d, "src/greet.py", PY)
        r = _client(self.d).get("/api/highlight", params={"path": "src/greet.py", "start": 1,
                                                          "end": 50})
        self.assertEqual(200, r.status_code, r.text)
        a = r.json()
        self.assertEqual(("Python", "filename", 6, 1, 6), (a["lang"], a["detected_by"],
                                                           a["lines"], a["start"], a["end"]))
        rows = a["rows"]
        self.assertEqual(PY.splitlines(), [_joined(r) for r in rows], "rows must be the lines")
        self.assertEqual({"kw"}, _classes(rows[0], "import"))
        self.assertEqual({"kw"}, _classes(rows[2], "def"))
        self.assertEqual({"fn"}, _classes(rows[2], "greet"))
        self.assertEqual({"doc"}, _classes(rows[3], '"""Say hello."""'))
        self.assertEqual({"str"}, _classes(rows[4], '"hello "'))
        self.assertEqual({"com"}, _classes(rows[4], "# a comment"))
        self.assertFalse(any(c == "kw" for c, _t in rows[1]), "line 2 is blank")
        # never markup: the angle brackets come back as text inside a string token
        self.assertIn("str", _classes(rows[5], "<script>"))
        self.assertFalse(a["plain"])

    def test_a_window_is_just_those_lines(self):
        _write(self.d, "a.py", _big_python(5000))
        a = H.window(self.d, "a.py", 2501, 2510)
        self.assertEqual((2501, 2510, 10), (a["start"], a["end"], len(a["rows"])))
        self.assertIn("def f_2501", _joined(a["rows"][0]))
        self.assertIn("'line 2502'", _joined(a["rows"][1]))
        self.assertEqual({"str"}, _classes(a["rows"][1], "'line 2502'"))

    def test_a_string_opened_above_a_chunk_boundary_still_colours_below_it(self):
        text = "x = 1\n" * (H.CHUNK - 20) + 'S = """\n' + "def not_code():\n" * 40 + '"""\n'
        _write(self.d, "b.py", text)
        a = H.window(self.d, "b.py", H.CHUNK + 1, H.CHUNK + 5)
        self.assertEqual("def not_code():", _joined(a["rows"][0]))
        self.assertNotIn("kw", {c for c, _t in a["rows"][0]},
                         "inside a string opened 20 lines above the chunk — no keyword")
        self.assertIn("lead-in", a["basis"])

    def test_the_sample_is_served_from_memory_and_labelled(self):
        c = _client(self.d, demo=True)
        a = c.get("/api/highlight", params={"path": "src/parser.py"}).json()
        self.assertTrue(a["sample"])
        self.assertEqual({"kw"}, _classes(a["rows"][0], "def"))


@NEED
class AHugeFileIsViewableInBoundedTimeAndMemory(Fresh):
    LINES = 200_000

    def setUp(self):
        super().setUp()
        self.text = _big_python(self.LINES)
        _write(self.d, "huge.py", self.text)
        self.size = os.path.getsize(os.path.join(self.d, "huge.py"))

    def test_a_deep_window_is_fast_bounded_in_memory_and_the_right_lines(self):
        _write(self.d, "warm.py", PY)
        H.window(self.d, "warm.py", 1, 5)        # Pygments' own imports are not the window's cost
        t0 = time.perf_counter()
        a = H.window(self.d, "huge.py", 150_001, 150_100)
        cold = time.perf_counter() - t0
        self.assertEqual(self.LINES, a["lines"])
        self.assertEqual(100, len(a["rows"]))
        self.assertIn("def f_150001", _joined(a["rows"][0]))
        self.assertIn("return x + 150003", _joined(a["rows"][2]))
        self.assertLess(cold, 3.0, f"cold window (index pass + one chunk) took {cold:.2f}s")
        for c in (H.CHUNKS, H.INDEXES):
            c.clear()
        tracemalloc.start()                     # memory measured apart (it slows the clock)
        H.window(self.d, "huge.py", 150_001, 150_100)
        _cur, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.assertLess(peak, self.size // 2,
                        f"peak {peak:,} B for a {self.size:,} B file — the file was read whole")
        # warm windows (index built, chunk cached or one new chunk lexed): p95 stays small
        times = []
        for i in range(40):
            s = 1 + (i * 4999) % (self.LINES - 200)
            t0 = time.perf_counter()
            H.window(self.d, "huge.py", s, s + 99)
            times.append(time.perf_counter() - t0)
        p95 = sorted(times)[int(len(times) * 0.95) - 1]
        self.assertLess(p95, 1.5, f"p95 {p95:.3f}s, p50 {statistics.median(times):.3f}s")

    def test_the_file_view_offers_a_huge_file_in_windows(self):
        from mokata.ide import nav
        f = nav.read_file(self.d, "huge.py")
        self.assertGreater(self.size, 1024 * 1024, "the fixture must pass the whole-file cap")
        self.assertIsNone(f["text"])
        self.assertTrue(f.get("windowed"), f)
        self.assertEqual(self.LINES, f["lines"])

    def test_one_request_is_capped_and_says_so(self):
        a = H.window(self.d, "huge.py", 1, 100_000)
        self.assertTrue(a["truncated"])
        self.assertEqual(H.MAX_WINDOW, len(a["rows"]))


@NEED
class TheCacheIsBoundedAndNeverStale(Fresh):
    def test_items_and_bytes_are_bounded_and_repeats_are_cached(self):
        _write(self.d, "c.py", _big_python(H.CHUNK * (H.CACHE_CHUNKS + 10)))
        for k in range(H.CACHE_CHUNKS + 10):
            H.window(self.d, "c.py", k * H.CHUNK + 1, k * H.CHUNK + 10)
        info = H.CHUNKS.info()
        self.assertLessEqual(info["items"], H.CACHE_CHUNKS)
        self.assertLessEqual(info["bytes"], H.CACHE_BYTES)
        self.assertGreaterEqual(info["evictions"], 10)
        last = H.CACHE_CHUNKS + 9
        again = H.window(self.d, "c.py", last * H.CHUNK + 1, last * H.CHUNK + 10)
        self.assertTrue(again["cached"], "the most recent chunk must come from the cache")
        first = H.window(self.d, "c.py", 1, 10)
        self.assertFalse(first["cached"], "the oldest chunk was evicted (LRU)")

    def test_a_changed_file_is_never_served_from_the_old_chunk(self):
        p = _write(self.d, "d.py", "x = 1\n")
        self.assertEqual("x = 1", _joined(H.window(self.d, "d.py", 1, 1)["rows"][0]))
        _write(self.d, "d.py", "y = 'changed'\n")
        st = os.stat(p)
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        self.assertEqual("y = 'changed'", _joined(H.window(self.d, "d.py", 1, 1)["rows"][0]))


# ============================================================================ diffs

@NEED
class EachDiffLineIsColouredInItsOwnSide(Fresh):
    # Old file:  s = """a / """ / def f(): pass        (the string closes on line 2)
    # New file:  s = """a / more text / def f(): pass  (line 2 replaced: the string never closes)
    HUNK = {"lines": [[" ", 1, 1, 's = """a'],
                      ["-", 2, None, '"""'],
                      ["+", None, 2, "more text"],
                      [" ", 3, 3, "def f(): pass"]]}

    def test_rows_map_to_lines_and_follow_their_side(self):
        a = H.diff("m.py", [self.HUNK])
        self.assertEqual("Python", a["lang"])
        rows = a["hunks"][0]
        self.assertEqual([ln[3] for ln in self.HUNK["lines"]], [_joined(r) for r in rows])
        self.assertIn("str", _classes(rows[1], '"""'), "the removed line closes the OLD string")
        self.assertEqual({"str"}, _classes(rows[2], "more text"),
                         "the added line sits inside the NEW side's open string — read top to "
                         "bottom as one text it would be plain names")
        self.assertEqual({"str"}, _classes(rows[3], "def"),
                         "context is shown as the new file has it: still inside the string")

    def test_over_http_and_with_bad_input_refused(self):
        c = _client(self.d)
        r = c.post("/api/highlight/diff", json={"path": "m.py", "hunks": [self.HUNK]},
                   headers={"Origin": BASE})
        self.assertEqual(200, r.status_code, r.text)
        self.assertEqual(4, len(r.json()["hunks"][0]))
        self.assertEqual(400, c.post("/api/highlight/diff", json={"path": "m.py"},
                                     headers={"Origin": BASE}).status_code)

    def test_a_diff_from_the_live_route_round_trips(self):
        from mokata.ide import diffs as D
        fd = D.text_diff("w.py", "def a():\n    return 1\n", "def a():\n    return 'two'\n")
        hunks = fd and D.DiffSet("t", [fd]).facts()["files"][0]["hunks"]
        a = H.diff("w.py", hunks)
        flat = [(ln, row) for h, rows in zip(hunks, a["hunks"]) for ln, row in zip(h["lines"], rows)]
        added = [row for ln, row in flat if ln[0] == "+"]
        self.assertEqual({"str"}, _classes(added[0], "'two'"))


# ============================================================================ language + plain

@NEED
class LanguageIsFoundAndPlainIsNeverSilent(Fresh):
    def test_by_name_then_by_content_then_none(self):
        _write(self.d, "lib.rs", "fn main() { let x = 1; }\n")
        _write(self.d, "tool", "#!/usr/bin/env bash\necho hi\n")
        _write(self.d, "NOTES", "just some words here\n")
        self.assertEqual(("Rust", "filename"), self._lang("lib.rs"))
        self.assertEqual(("Bash", "content"), self._lang("tool"))
        self.assertEqual("none", self._lang("NOTES")[1])

    def _lang(self, rel):
        a = H.window(self.d, rel, 1, 10)
        return a["lang"], a["detected_by"]

    def test_past_the_size_cap_it_is_plain_and_says_why(self):
        _write(self.d, "e.py", PY * 50)
        with mock.patch.object(H, "HL_MAX_BYTES", 100):
            a = H.window(self.d, "e.py", 1, 10)
        self.assertTrue(a["plain"])
        self.assertIn("highlighting stops at", a["plain_reason"])
        self.assertEqual(PY.splitlines()[0], _joined(a["rows"][0]))
        self.assertEqual({""}, {c for r in a["rows"] for c, _t in r})

    def test_a_minified_line_makes_its_chunk_plain_and_says_why(self):
        _write(self.d, "m.js", "var a = 1;\n" + "x=1;" * 2000 + "\n")
        a = H.window(self.d, "m.js", 1, 2)
        self.assertTrue(a["plain"])
        self.assertIn("minified", a["plain_reason"])

    def test_without_pygments_it_is_plain_with_the_install_line(self):
        _write(self.d, "g.py", PY)
        with mock.patch.object(H, "available", lambda: False):
            a = H.window(self.d, "g.py", 1, 3)
            s = H.snippet("x = 1", "python")
        self.assertTrue(a["plain"] and s["plain"])
        self.assertIn("mokata[ide]", a["plain_reason"])
        self.assertEqual("import os", _joined(a["rows"][0]))

    def test_binary_and_outside_paths_are_not_served(self):
        _write(self.d, "bin.dat", b"\x00\x01\x02abc", mode="wb")
        a = H.window(self.d, "bin.dat", 1, 10)
        self.assertTrue(a.get("not_served"))
        self.assertIn("binary", a["plain_reason"])
        self.assertEqual(400, _client(self.d).get("/api/highlight",
                                                  params={"path": "../etc/passwd"}).status_code)


@NEED
class SpecSnippetsAreColouredByTheirFence(Fresh):
    def test_a_python_fence(self):
        a = H.snippet("def f():\n    return 'x'\n", "python")
        self.assertEqual(("Python", "fence"), (a["lang"], a["detected_by"]))
        self.assertEqual({"kw"}, _classes(a["rows"][0], "def"))
        self.assertEqual({"str"}, _classes(a["rows"][1], "'x'"))

    def test_over_http(self):
        r = _client(self.d).post("/api/highlight/snippet",
                                 json={"text": "SELECT 1 FROM t;", "lang": "sql"},
                                 headers={"Origin": BASE})
        self.assertEqual(200, r.status_code)
        self.assertIn("kw", _classes(r.json()["rows"][0], "SELECT"))


# ============================================================================ the page (node)

@unittest.skipUnless(shutil.which("node"), "node is not on PATH — the page logic runs in a browser")
class ThePagePaintsSpansAsText(unittest.TestCase):
    DOM = r"""
const made = [];
const mk = (tag) => ({ tag, kids: [], className: "", dataset: {}, text: "",
  append(...k) { this.kids.push(...k); }, replaceChildren(...k) { this.kids = k; } });
globalThis.document = { readyState: "complete",
  createElement: (t) => { const n = mk(t); made.push(t); return n; },
  createTextNode: (t) => ({ tag: "#text", text: t }),
  createDocumentFragment: () => mk("#frag") };
Object.defineProperty(Object.prototype, "textContent", {
  set(v) { this.text = v; }, get() { return this.text; }, configurable: true });
globalThis.window = { MK: { $: () => null, el: (t, c, x) => { const n = document.createElement(t);
  if (x !== undefined) n.textContent = String(x); return n; }, api: async () => null } };
"""

    def _node(self, js):
        p = subprocess.run(_support.node_argv(js), capture_output=True, text=True,
                           timeout=30)
        self.assertEqual(0, p.returncode, p.stderr)
        return json.loads(p.stdout.strip().splitlines()[-1])

    def test_paint_makes_classed_spans_with_text_only(self):
        src = (STATIC / "features" / "highlight.js").read_text(encoding="utf-8")
        out = self._node(self.DOM + src + r"""
const t = document.createElement("span");
window.MK.hl.paint(t, [["kw", "def"], ["", " "], ["str", "'<b>x</b>'"]]);
console.log(JSON.stringify(t.kids.map((k) => [k.tag, k.className || "", k.text])));
""")
        self.assertEqual([["span", "tk-kw", "def"], ["#text", "", " "],
                          ["span", "tk-str", "'<b>x</b>'"]], out)

    def test_a_spec_fence_becomes_a_code_block_handed_to_the_highlighter(self):
        import re
        js = (STATIC / "plan.js").read_text(encoding="utf-8")
        body = re.search(r"// --- mdLite ---(.*?)// --- end mdLite ---", js, re.S).group(1)
        out = self._node(self.DOM + r"""
const asked = [];
const MK = { hl: { snippet: (pre, lang) => asked.push([pre.tag, lang, pre.text]) } };
const el = window.MK.el;
""" + body + r"""
const f = mdLite("Given:\n\n```python\ndef f():\n    return 1\n```\n\nthen it holds");
console.log(JSON.stringify({ kids: f.kids.map((k) => k.tag), asked }));
""")
        self.assertEqual(["#frag", "pre", "#frag"], out["kids"])
        self.assertEqual([["pre", "python", "def f():\n    return 1"]], out["asked"])

    def test_every_token_class_has_a_theme_colour_in_each_theme(self):
        css = (STATIC / "features" / "highlight.css").read_text(encoding="utf-8")
        for cls in H.TOKEN_CLASSES:
            self.assertIn(f".tk-{cls} ", css, cls)
        for var in ("--tk-kw", "--tk-str", "--tk-com"):
            self.assertEqual(4, css.count(var + ":"), f"{var}: dark, light, contrast x2")


if __name__ == "__main__":
    unittest.main()
