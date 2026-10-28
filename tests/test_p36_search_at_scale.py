"""0.0.26 P36 `SEARCH-AT-SCALE` — search that holds on a 7M-line repo.

  * R16 / G13 — word and regex search have NO hard stop: an answer is a page, `next_offset` asks for
    the next one, and paging reaches every hit exactly once (rg and the pure-Python floor alike).
  * G33 — a file over 1 MB is searched in full, and the answer says how big it is.
  * R17 / R40 / G26 / G31 — the page asks ONE request per settled query and source; Enter or
    turning Meaning on re-asks nothing already asked; a new query aborts the superseded fetch, and
    a late answer for the old query never paints under the new input.
  * R18 — `/api/meaning` never answers a 500: a failure is a `failed` state naming the error.
  * G32 — the meaning index's vectors are a memory map, ranked by one vectorised product whose
    top-k equals a brute-force ranking; reused work is copied file-to-file, not gathered in memory.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata.ide import search as SR

HERE = Path(__file__).resolve().parent
STATIC = HERE.parent / "src" / "mokata" / "ide" / "static"

try:
    import numpy  # noqa: F401
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False


def _write(d, rel, text):
    p = os.path.join(d, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _many_hits(d, files=6, per=420):
    for i in range(files):
        _write(d, f"src/m{i}.py", "".join(f"needle_{i}_{n} = 1\n" for n in range(per)))
    return files * per


def _all_pages(root, q, engine, page):
    """Every page of `q`, following next_offset, with rg present or forced absent."""
    seen, offset, pages = [], 0, 0
    with mock.patch.object(SR, "rg_path", return_value=None) if engine == "floor" \
            else mock.patch.object(SR, "PARTIAL_EVERY_S", 99):
        while True:
            res = SR.text(root, q, offset=offset, page=page)
            pages += 1
            seen += [(f["path"], m["line"]) for f in res["files"] for m in f["matches"]]
            if not res["more"]:
                return seen, pages, res
            offset = res["next_offset"]


class R16_NoHardStop(unittest.TestCase):

    def test_R16_every_hit_is_reachable_by_pages_rg_and_floor(self):
        with tempfile.TemporaryDirectory() as d:
            n = _many_hits(d)                                     # 2,520 hits > the old 2,000 stop
            engines = ["floor"] + (["rg"] if SR.rg_path() else [])
            for engine in engines:
                seen, pages, last = _all_pages(d, "needle", engine, page=1000)
                self.assertEqual(n, len(seen), f"{engine}: every hit reachable")
                self.assertEqual(n, len(set(seen)), f"{engine}: no hit twice across pages")
                self.assertEqual(3, pages, engine)
                self.assertEqual(n, last["total"])
                self.assertIsNone(last["next_offset"])
            if len(engines) == 2:
                a, _, _ = _all_pages(d, "needle", "rg", page=700)
                b, _, _ = _all_pages(d, "needle", "floor", page=700)
                self.assertEqual(sorted(a), sorted(b))

    def test_R16_a_full_page_says_more_and_where_the_next_starts(self):
        with tempfile.TemporaryDirectory() as d:
            _many_hits(d)
            res = SR.text(d, "needle", page=1000)
            self.assertEqual((True, 1000, 1000, 0), (res["more"], res["next_offset"], res["count"],
                                                     res["offset"]))
            self.assertNotIn("truncated", res, "the hard stop is gone, not renamed")
            self.assertEqual(SR.PAGE_MATCHES, SR.text(d, "needle")["count"])

    def test_R16_the_route_pages_and_refuses_a_bad_offset(self):
        from starlette.testclient import TestClient
        from mokata.ide.server import AppState, build_app
        with tempfile.TemporaryDirectory() as d:
            _many_hits(d)
            c = TestClient(build_app(AppState(d, "tok", 9999, None, False)),
                           base_url="http://127.0.0.1:9999")
            c.get("/pair?t=tok")
            h = {"Origin": "http://127.0.0.1:9999"}
            first = c.post("/api/search", json={"q": "needle", "sync_ms": 2000}, headers=h).json()
            nxt = c.post("/api/search", json={"q": "needle", "offset": first["next_offset"],
                                              "sync_ms": 2000}, headers=h).json()
            self.assertEqual(SR.PAGE_MATCHES, nxt["offset"])
            a = {(f["path"], m["line"]) for f in first["files"] for m in f["matches"]}
            b = {(f["path"], m["line"]) for f in nxt["files"] for m in f["matches"]}
            self.assertFalse(a & b, "page 2 repeats page 1")
            for bad in (-1, "3", True):
                r = c.post("/api/search", json={"q": "needle", "offset": bad}, headers=h)
                self.assertEqual(400, r.status_code, bad)


class G33_LargeFiles(unittest.TestCase):

    def test_G33_a_file_over_1MB_is_searched_and_says_its_size(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "src/big.go", "// filler line of a generated file\n" * 40000 +
                   "func NeedleAtTheEnd() {}\n")                   # ~1.3 MB, the hit at the end
            _write(d, "src/small.go", "func NeedleAtTheEnd2() {}\n")
            engines = ["floor"] + (["rg"] if SR.rg_path() else [])
            for engine in engines:
                with mock.patch.object(SR, "rg_path", return_value=None) if engine == "floor" \
                        else mock.patch.object(SR, "PARTIAL_EVERY_S", 99):
                    res = SR.text(d, "NeedleAtTheEnd")
                by = {f["path"]: f for f in res["files"]}
                self.assertIn("src/big.go", by, f"{engine}: the >1 MB file was not searched")
                self.assertGreater(by["src/big.go"]["size"], 1024 * 1024)
                self.assertIn("1.3 MB", by["src/big.go"]["size_note"])
                self.assertNotIn("size", by["src/small.go"])
                self.assertEqual(["src/big.go"], res["large_files"])


class R18_MeaningNeverA500(unittest.TestCase):

    def test_R18_a_failing_meaning_search_is_a_stated_failure_not_a_500(self):
        from starlette.testclient import TestClient
        from mokata.ide.server import AppState, build_app
        with tempfile.TemporaryDirectory() as d:
            _write(d, "src/a.py", "def a():\n    return 1\n")
            st = AppState(d, "tok", 9999, None, False)
            c = TestClient(build_app(st), base_url="http://127.0.0.1:9999")
            c.get("/pair?t=tok")
            h = {"Origin": "http://127.0.0.1:9999"}
            with mock.patch.object(type(st.source), "meaning",
                                   side_effect=RecursionError("maximum recursion depth exceeded")), \
                    mock.patch("sys.stderr"):
                codes, bodies = [], []
                for i in range(10):
                    r = c.post("/api/meaning", json={"q": f"query {i}", "sync_ms": 2000}, headers=h)
                    codes.append(r.status_code)
                    bodies.append(r.json())
            self.assertEqual([], [x for x in codes if x >= 500], codes)
            self.assertEqual({"failed"}, {b["state"] for b in bodies})
            self.assertIn("RecursionError", bodies[0]["note"])
            self.assertIs(False, bodies[0]["available"])

    def test_R18_a_file_that_breaks_the_parser_never_breaks_the_build(self):
        emb = _Emb()
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(SR, "EMBEDDER_FACTORY", lambda: emb):
            _write(d, "src/deep.ts", "export const x = 1\n")
            _write(d, "src/ok.py", "def fine():\n    return 1\n")
            # 0.0.26 P39: a search builds the index only in a repo mokata governs (elsewhere the
            # first build is the user's consented Build) — make this one governed
            os.makedirs(os.path.join(d, ".mokata"))
            real = SR.chunks_of

            def chunks(rel, text):
                if rel.endswith(".ts"):
                    raise RecursionError("maximum recursion depth exceeded")
                return real(rel, text)
            with mock.patch.object(SR, "chunks_of", chunks):
                res = SR.meaning(d, "fine")
            self.assertTrue(res["available"])
            self.assertEqual({"src/deep.ts", "src/ok.py"},
                             set(SR._read_meta(d)["files"]), "the bad file is indexed by its head")
            self.assertEqual("RecursionError", SR._read_meta(d)["files"]["src/deep.ts"]["unparsed"])
            self.assertEqual(1, res["unparsed_files"])


class _Emb:
    embedder_id = "p36-fake"
    dim = 8
    noise_floor = -2.0

    def __call__(self, text):
        h = [0.0] * 8
        for i, ch in enumerate(text.encode("utf-8")[:400]):
            h[(ch + i) % 8] += 1.0
        return h


@unittest.skipUnless(HAVE_NUMPY, "numpy is the embeddings extra's; the plain path is in test_c2")
class G32_MeaningAtScale(unittest.TestCase):

    def _repo(self, d, n=60):
        for i in range(n):
            _write(d, f"pkg/f{i:03d}.go", "".join(
                f"func F{i}_{k}(x int) int {{\n\treturn x * {k} + {i}\n}}\n\n" for k in range(5)))

    def test_G32_vectors_are_a_memory_map_and_top_k_equals_brute_force(self):
        import numpy as np
        emb = _Emb()
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(SR, "EMBEDDER_FACTORY", lambda: emb):
            self._repo(d)
            SR.build_index(d, emb)
            meta = SR._read_meta(d)
            mat = SR._matrix(d, meta)
            self.assertIsInstance(mat[1], np.memmap, "the matrix is mapped, not read whole")
            self.assertEqual(300, len(mat[0]))
            q = emb("func F7_3 return x")
            got = SR._rank(q, mat, None, -2.0, 10)
            rows = mat[0]
            brute = []
            for (rel, c), v in zip(rows, np.asarray(mat[1])):
                s = float(v @ np.asarray(q, np.float32) /
                          ((np.linalg.norm(v) or 1) * np.linalg.norm(q)))
                brute.append((s, rel, c))
            brute.sort(key=lambda r: (-r[0], r[1], r[2]["line"]))
            self.assertEqual([(r[1], r[2]["line"]) for r in brute[:10]],
                             [(r[1], r[2]["line"]) for r in got])
            scoped = SR._rank(q, mat, {"pkg/f001.go"}, -2.0, 10)
            self.assertEqual({"pkg/f001.go"}, {r[1] for r in scoped})

    def test_G32_a_resumed_build_copies_reused_rows_without_reading_the_matrix_whole(self):
        emb = _Emb()
        with tempfile.TemporaryDirectory() as d:
            self._repo(d)
            SR.build_index(d, emb)
            before = SR._read_meta(d)
            _write(d, "pkg/f000.go", "func Changed() {}\n")
            os.utime(os.path.join(d, "pkg", "f000.go"), (1, 1))
            seen = []
            real_write = SR._write_index

            def spy(root, meta, blobs, dim, old_src=None):
                seen.append({rel: type(b).__name__ for rel, b in blobs.items()})
                return real_write(root, meta, blobs, dim, old_src)
            with mock.patch.object(SR, "_write_index", spy):
                out = SR.build_index(d, emb)
            kinds = seen[-1]
            self.assertEqual("bytes", kinds.pop("pkg/f000.go"))
            self.assertEqual({"list"}, set(kinds.values()),
                             "reused work was gathered into memory instead of copied by row")
            self.assertEqual(1, out["embedded"])
            after = SR._read_meta(d)
            self.assertEqual(before["rows"] - 4, after["rows"])
            # the reused rows are byte-identical to the earlier build's
            m1 = SR._matrix(d, after)
            r = {(rel, c["line"]): i for i, (rel, c) in enumerate(m1[0])}
            i = r[("pkg/f059.go", 1)]
            self.assertEqual(list(emb(SR.chunks_of("pkg/f059.go", open(
                os.path.join(d, "pkg", "f059.go"), encoding="utf-8").read())[0]["text"])),
                [float(x) for x in m1[1][i]])


# ------------------------------------------------------------------ the page (search.js in node)

_DOM = r"""
globalThis.window = globalThis;
const timers = []; let now = 0;
globalThis.setTimeout = (f, ms) => { timers.push({ f, at: now + ms, id: timers.length + 1 }); return timers.length; };
globalThis.clearTimeout = (id) => { const t = timers.find((x) => x.id === id); if (t) t.f = null; };
function tick(ms) { now += ms; for (const t of timers.slice()) if (t.f && t.at <= now) { const f = t.f; t.f = null; f(); } }
class Node {
  constructor(tag, cls, text) { this.tag = tag; this.className = cls || ""; this.textContent = text == null ? "" : String(text);
    this.children = []; this.hidden = false; this.value = ""; this.checked = false; this.dataset = {}; this.attrs = {};
    const c = new Set(); this.classList = { add: (x) => c.add(x), remove: (x) => c.delete(x), toggle: (x, on) => (on ? c.add(x) : c.delete(x)), contains: (x) => c.has(x) }; }
  append(...k) { this.children.push(...k); } replaceChildren(...k) { this.children = k; }
  insertBefore(a) { this.children.unshift(a); } remove() {} focus() {}
  setAttribute(k, v) { this.attrs[k] = v; } querySelector() { return null; }
}
const nodes = {};
globalThis.document = { createDocumentFragment: () => new Node("frag"), createTextNode: (t) => new Node("#text", "", t),
  querySelectorAll: (sel) => sel.includes("stoggle") ? toggles : [] };
const toggles = ["regex", "case", "meaning"].map((k) => { const n = new Node("button"); n.dataset.stoggle = k; return n; });
const calls = [];
globalThis.MK = { $: (id) => nodes[id] || (nodes[id] = new Node("div")), el: (t, c, x) => new Node(t, c, x), ui: { files: [] },
  markSample() {}, setMode() {}, openFile() {}, sessionFiles: () => null, viewing: () => ({ kind: "live", label: "uncommitted changes" }), onViewing: [],
  api: (path, body, opts) => new Promise((resolve, reject) => {
    const c = { path, body, opts, aborted: false, resolve, reject };
    if (opts && opts.signal) opts.signal.addEventListener("abort", () => { c.aborted = true; const e = new Error("aborted"); e.name = "AbortError"; reject(e); });
    calls.push(c); }) };
"""


def _node(js: str):
    p = subprocess.run(_support.node_argv(js), capture_output=True, text=True,
                       timeout=60, stdin=subprocess.DEVNULL, encoding="utf-8")
    if p.returncode:
        raise AssertionError(p.stderr)
    return json.loads(p.stdout)


@unittest.skipUnless(shutil.which("node"), "node is not on PATH — the page logic runs in a browser")
class R17_R40_ThePage(unittest.TestCase):

    def _run(self, script):
        js = _DOM + (STATIC / "search.js").read_text(encoding="utf-8") + r"""
MK.wireSearch();
const sq = MK.$("sq");
function type(text, gap) { for (let i = 1; i <= text.length; i++) { sq.value = text.slice(0, i); sq.oninput(); tick(gap); } }
const searches = () => calls.filter((c) => c.path === "/api/search");
const meanings = () => calls.filter((c) => c.path === "/api/meaning");
const out = {};
(async () => {
""" + script + r"""
process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e.stack); process.exit(1); });
"""
        return _node(js)

    def test_R17_one_request_per_settled_query(self):
        o = self._run(r"""
type("NewController", 80);                 // typing at 80 ms a key: no request until it settles
out.typing = searches().length;
tick(300);
out.settled = searches().length;
sq.onkeydown({ key: "Enter", preventDefault() {} });      // Enter on the same question
out.afterEnter = searches().length;
toggles[2].onclick();                                      // Meaning on: only meaning is asked
out.afterMeaning = [searches().length, meanings().length];
sq.onkeydown({ key: "Enter", preventDefault() {} });
out.again = [searches().length, meanings().length];
out.q = searches()[0].body.q;
""")
        self.assertEqual(0, o["typing"])
        self.assertEqual(1, o["settled"])
        self.assertEqual(1, o["afterEnter"], "Enter re-asked a question already on its way (G26)")
        self.assertEqual([1, 1], o["afterMeaning"], "turning Meaning on re-asked word search")
        self.assertEqual([1, 1], o["again"])
        self.assertEqual("NewController", o["q"])

    def test_R40_a_new_query_aborts_the_old_and_its_late_answer_never_paints(self):
        o = self._run(r"""
toggles[2].onclick();                                      // Meaning on
sq.value = "alpha"; sq.oninput(); tick(300);
const [w1] = searches(); const [m1] = meanings();
sq.value = "beta"; sq.oninput(); tick(300);                // a second query within the window
out.aborted = [w1.aborted, m1.aborted];
out.header = MK.$("sr-word-h").textContent;
// the old answers arrive late anyway — they must not paint
w1.resolve({ q: "alpha", files: [{ path: "a.go", matches: [{ line: 1, text: "alpha", spans: [] }] }], total: 1, more: false, engine: "rg" });
await new Promise((r) => setImmediate(r)); await new Promise((r) => setImmediate(r));
out.headerAfterLate = MK.$("sr-word-h").textContent;
out.stale = MK.$("sr-word-list").classList.contains("stale");
const w2 = searches()[1];
w2.resolve({ q: "beta", files: [{ path: "b.go", matches: [{ line: 2, text: "beta", spans: [] }] }], total: 1, count: 1, offset: 0, more: true, next_offset: 1000, large_files: [], engine: "rg" });
await new Promise((r) => setImmediate(r)); await new Promise((r) => setImmediate(r));
out.final = MK.$("sr-word-h").textContent;
out.loadMore = MK.$("sr-word-list").children.filter((c) => (c.className || "").includes("sr-more")).length;
out.n = [searches().length, meanings().length];
""")
        self.assertEqual([True, True], o["aborted"])
        self.assertIn("beta", o["header"])
        self.assertNotIn("alpha", o["headerAfterLate"], "the old query painted under the new input")
        self.assertTrue(o["stale"], "results on screen for the old query are not marked updating")
        self.assertIn("beta", o["final"])
        self.assertIn("≥1", o["final"], "a page with more says ≥")
        self.assertEqual(1, o["loadMore"], "no Load more for the next page (R16)")
        self.assertEqual([2, 2], o["n"])

    def test_R16_load_more_asks_the_next_offset_and_appends(self):
        o = self._run(r"""
sq.value = "x"; sq.oninput(); tick(300);
searches()[0].resolve({ q: "x", files: [{ path: "a.go", matches: [{ line: 1, text: "x", spans: [] }] }], total: 1000, count: 1000, offset: 0, more: true, next_offset: 1000, large_files: [], engine: "rg" });
await new Promise((r) => setImmediate(r)); await new Promise((r) => setImmediate(r));
const btn = MK.$("sr-word-list").children.find((c) => (c.className || "").includes("sr-more"));
btn.onclick();
const p2 = searches()[1];
out.offset = p2.body.offset;
p2.resolve({ q: "x", files: [{ path: "z.go", matches: [{ line: 9, text: "x", spans: [] }] }], total: 1001, count: 1, offset: 1000, more: false, next_offset: null, large_files: ["z.go"], engine: "rg" });
await new Promise((r) => setImmediate(r)); await new Promise((r) => setImmediate(r));
out.cards = MK.$("sr-word-list").children.filter((c) => c.className === "sr-file").length;
out.header = MK.$("sr-word-h").textContent;
out.note = MK.$("sr-word-note").textContent;
""")
        self.assertEqual(1000, o["offset"])
        self.assertEqual(2, o["cards"], "page 2 replaced page 1 instead of appending")
        self.assertIn("1001 hit(s) in 2 file(s)", o["header"])
        self.assertIn("over 1 MB", o["note"])


if __name__ == "__main__":
    unittest.main()
