"""0.0.23 stage C2 — `mokata ide` search: word / regex (rg, or the labelled pure-Python floor) and
MEANING (ID.S2-SEMANTIC, ruling R2: mokata's own local code embeddings via the `[embeddings]` extra).

Asserted, each against what a user relies on:
  * word and regex search answer from the explorer's file set (a venv never appears), and NAME the
    engine — rg and the floor return the same hits;
  * a pattern can never become an rg option; a bad regex is refused with its reason;
  * meaning search without model2vec is an explicit `semantic unavailable` state with the install
    line — NEVER word results under the name "meaning", never the token-hash embedder;
  * the model is loaded OFFLINE, per call (no network unless the user presses fetch — and then the
    fetch really is online); no API key is read;
  * with an embedder, function/class chunks (cut by the graph's parser) are embedded into a local
    index under `.mokata/`, re-embedded incrementally, rebuilt when the embedder changes, and
    results link to graph symbols.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata.ide import search as SR
from mokata.ide import nav
from mokata.init import init_repo
from mokata.memory.embed import ModelUnavailable

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src", "mokata", "ide")
STATIC = os.path.join(SRC, "static")


def _silent(*_a, **_k):
    pass


def _write(d, rel, text):
    p = os.path.join(d, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as fh:
        fh.write(text.encode("utf-8"))


def _fixture(d):
    init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
    _write(d, "src/env.py",
           "import os\n\n\ndef resolve(root):\n    venv = os.environ.get('VIRTUAL_ENV')\n"
           "    return venv or root\n\n\nclass Interpreter:\n    def path(self):\n"
           "        return 'python'\n")
    _write(d, "src/net.py", "def fetch(url):\n    return download(url, retries=3)\n")
    _write(d, "docs/notes.md", "VIRTUAL_ENV is read by resolve\n-e is a flag\n")
    _write(d, "venv/pyvenv.cfg", "home = /usr\n")
    _write(d, "venv/lib/site.py", "VIRTUAL_ENV = 'from a venv'\n")
    _write(d, "node_modules/x/index.js", "const VIRTUAL_ENV = 1\n")


def _hits(res):
    return sorted((f["path"], m["line"]) for f in res["files"] for m in f["matches"])


class FakeEmbedder:
    """A deterministic concept-axis embedder — the test seam's stand-in for model2vec."""
    embedder_id = "fake-concepts-v1"
    dim = 4
    noise_floor = 0.0
    AXES = (("python", "interpreter", "venv", "virtual_env", "environ", "resolve"),
            ("download", "fetch", "url", "network", "retries"),
            ("class", "cls"), ("test", "assert"))

    def __init__(self):
        self.calls = 0

    def __call__(self, text):
        self.calls += 1
        words = re.findall(r"[a-z_]+", text.lower())
        v = [float(sum(1 for w in words if w in axis)) for axis in self.AXES]
        n = sum(x * x for x in v) ** 0.5
        return [x / n for x in v] if n else v


class WordAndRegex(unittest.TestCase):

    def test_rg_and_the_floor_give_the_same_hits_and_each_names_itself(self):
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            with mock.patch.object(SR, "rg_path", return_value=None):
                floor = SR.text(d, "VIRTUAL_ENV")
            self.assertEqual("python-floor", floor["engine"])
            self.assertEqual([("docs/notes.md", 1), ("src/env.py", 5)], _hits(floor))
            if SR.rg_path():
                rg = SR.text(d, "VIRTUAL_ENV")
                self.assertEqual("rg", rg["engine"])
                self.assertEqual(_hits(floor), _hits(rg))
                self.assertEqual(floor["files"][1]["matches"][0]["spans"],
                                 rg["files"][1]["matches"][0]["spans"])

    def test_a_venv_or_vendor_dir_is_never_searched(self):
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            for rg in (True, False):
                with self.subTest(rg=rg), mock.patch.object(
                        SR, "rg_path", return_value=SR.rg_path() if rg else None):
                    paths = {f["path"] for f in SR.text(d, "VIRTUAL_ENV")["files"]}
                    self.assertFalse({p for p in paths if p.startswith(("venv/", "node_modules/"))})

    def test_regex_mode_and_a_bad_regex_is_refused_with_its_reason(self):
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            res = SR.text(d, r"def \w+\(url\)", mode="regex")
            self.assertEqual([("src/net.py", 1)], _hits(res))
            with self.assertRaises(SR.BadPattern):
                SR.text(d, "def (", mode="regex")
            self.assertEqual([], SR.text(d, "def (", mode="word")["files"],
                             "in word mode the same text is a literal")

    def test_a_pattern_is_never_an_rg_option(self):
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            for q in ("-e is", "--files", "--pre=sh"):
                with self.subTest(q=q):
                    res = SR.text(d, q)
                    self.assertNotIn("error", res)
                    if q == "-e is":
                        self.assertEqual([("docs/notes.md", 2)], _hits(res))

    def test_case_and_changed_only_scope(self):
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            self.assertEqual([], SR.text(d, "virtual_env", case=True)["files"])
            only = SR.text(d, "VIRTUAL_ENV", only=["src/env.py", "../escape.py", "/etc/passwd"])
            self.assertEqual([("src/env.py", 5)], _hits(only))
            self.assertEqual([], SR.text(d, "VIRTUAL_ENV", only=[])["files"])

    def test_rg_refusing_a_pattern_falls_to_the_floor_and_says_so(self):
        if not SR.rg_path():
            self.skipTest("rg is not on PATH")
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            res = SR.text(d, r"(?<=def )fetch", mode="regex")   # look-behind: Python yes, rg no
            self.assertEqual("python-floor", res["engine"])
            self.assertIn("rg could not answer", res["engine_note"])
            self.assertEqual([("src/net.py", 1)], _hits(res))


class MeaningUnavailableIsSaid(unittest.TestCase):

    def test_without_model2vec_meaning_is_an_explicit_state_never_word_results(self):
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            with mock.patch.object(SR, "_extra_installed", return_value=False), \
                    mock.patch.object(SR, "EMBEDDER_FACTORY", None):
                res = SR.meaning(d, "VIRTUAL_ENV")
            self.assertIs(False, res["available"])
            self.assertEqual("not-installed", res["state"])
            self.assertIn("semantic unavailable", res["note"])
            # 0.0.26 L1: the line is THIS installer's, never a hard-coded `pip install '…'`.
            from mokata.install_route import plan_extra_install
            self.assertEqual(plan_extra_install("embeddings").display, res["install"])
            self.assertIn("model2vec", res["install"])
            self.assertEqual([], res["results"], "a word hit exists — and is NOT returned as meaning")
            self.assertFalse(os.path.exists(SR.index_dir(d)), "nothing is indexed or written")

    def _fake_extra(self, calls, have_model):
        """A stand-in for model2vec + huggingface_hub: records every fetch call's offline flag."""
        import types

        def snapshot_download(repo, local_files_only=False, etag_timeout=None):
            calls.append(local_files_only)
            if local_files_only and not have_model["on_disk"]:
                raise OSError("not in the local cache")
            have_model["on_disk"] = True
            return "/models/" + repo

        class FakeModel:
            def encode(self, texts):
                return [[1.0, 0.0, 0.0] for _ in texts]

        class StaticModel:
            @staticmethod
            def from_pretrained(path):
                assert path.startswith("/models/"), "only ever loaded from a LOCAL directory"
                return FakeModel()
        hub = types.ModuleType("huggingface_hub")
        hub.snapshot_download = snapshot_download
        m2v = types.ModuleType("model2vec")
        m2v.StaticModel = StaticModel
        return {"huggingface_hub": hub, "model2vec": m2v}

    def test_no_local_model_is_said_offline_and_the_fetch_is_really_online(self):
        """Measured against the real extra: huggingface_hub reads HF_HUB_OFFLINE once at import,
        so an env-var offline load made the later fetch offline too. Offline is a per-call flag."""
        calls, have = [], {"on_disk": False}
        before = os.environ.get("HF_HUB_OFFLINE")
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            with mock.patch.dict(sys.modules, self._fake_extra(calls, have)), \
                    mock.patch.object(SR, "EMBEDDER_FACTORY", None), \
                    mock.patch.dict(SR._EMBEDDER, {}, clear=True):
                res = SR.meaning(d, "where is the interpreter chosen")
                self.assertEqual(("model-not-local", True, []),
                                 (res["state"], res["can_fetch"], res["results"]))
                self.assertEqual([True], calls, "the status probe never touches the network")
                fetched = SR.fetch_model()
                self.assertEqual([True, False, ], calls[:2], "the fetch IS online")
                self.assertIs(True, fetched["available"])
                self.assertEqual("model2vec:minishlab/potion-base-8M", fetched["embedder"],
                                 "the same stamp memory's embedder carries")
                SR._EMBEDDER.clear()
                self.assertIs(True, SR.status()["available"], "next start: found offline")
                self.assertEqual(True, calls[-1])
        self.assertEqual(before, os.environ.get("HF_HUB_OFFLINE"), "no global env is touched")

    def test_the_hashing_embedder_is_never_substituted_for_meaning(self):
        with open(os.path.join(SRC, "search.py"), encoding="utf-8") as fh:
            src = fh.read()
        for bad in ("HashingEmbedder", "make_embedder", "detect_embedder"):
            self.assertNotIn(bad, src)

    def test_no_api_key_and_no_network_library_in_the_search_path(self):
        for name in ("search.py", "nav.py"):
            with open(os.path.join(SRC, name), encoding="utf-8") as fh:
                src = fh.read()
            for bad in ("API_KEY", "api_key", "urllib", "requests", "socket", "http.client"):
                self.assertNotIn(bad, src, f"{name}: {bad}")


class MeaningWithAnEmbedder(unittest.TestCase):

    def setUp(self):
        self.emb = FakeEmbedder()
        p = mock.patch.object(SR, "EMBEDDER_FACTORY", lambda: self.emb)
        p.start()
        self.addCleanup(p.stop)

    def test_ranked_by_meaning_and_linked_to_graph_symbols(self):
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            res = SR.meaning(d, "which python interpreter environment is used")
            self.assertIs(True, res["available"])
            top = res["results"][0]
            self.assertEqual(("src/env.py", "resolve", "function", 4, "graph-symbol"),
                             (top["path"], top["name"], top["kind"], top["line"], top["link"]))
            net = SR.meaning(d, "network download retries")["results"][0]
            self.assertEqual(("src/net.py", "fetch"), (net["path"], net["name"]))
            names = {r["name"] for r in res["results"]}
            self.assertNotIn("site.py", names, "a venv is never indexed")

    def test_the_index_is_local_incremental_and_stamped(self):
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            first = SR.meaning(d, "python")
            self.assertGreater(first["embedded_now"], 0)
            path = os.path.join(SR.index_dir(d), "index.json")
            self.assertTrue(path.startswith(os.path.join(d, ".mokata")))
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertEqual("fake-concepts-v1", data["embedder"])
            self.assertEqual(0, SR.meaning(d, "python")["embedded_now"], "nothing re-embedded")
            _write(d, "src/net.py", "def fetch(url):\n    return download(url)\n\n\n"
                                    "def ping():\n    return 1\n")
            os.utime(os.path.join(d, "src", "net.py"), (1, 1))
            self.assertEqual(2, SR.meaning(d, "python")["embedded_now"], "only the changed file")
            self.emb.embedder_id = "fake-concepts-v2"
            again = SR.meaning(d, "python")
            self.assertEqual(again["indexed_chunks"], again["embedded_now"],
                             "a different embedder's vectors never meet: full rebuild")

    def test_chunks_are_function_and_class_level_from_the_graphs_parser(self):
        src = "import os\n\n\ndef a():\n    return 1\n\n\nclass B:\n    def c(self):\n        pass\n"
        rows = [(c["name"], c["kind"], c["line"]) for c in SR.chunks_of("m.py", src)]
        self.assertEqual([("a", "function", 4), ("B", "class", 8), ("c", "method", 9)], rows)
        self.assertEqual(["module"], [c["kind"] for c in SR.chunks_of("x.js", "let a = 1\n")])

    def test_changed_only_scopes_meaning_too(self):
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            res = SR.meaning(d, "python interpreter environment", only=["src/net.py"])
            self.assertEqual([], res["results"], "the env.py match is outside the scope")
            res = SR.meaning(d, "download over the network", only=["src/net.py", "../x.py"])
            self.assertEqual({"src/net.py"}, {r["path"] for r in res["results"]})


class TheEndpoints(unittest.TestCase):

    def _client(self, root):
        from starlette.testclient import TestClient
        from mokata.ide.server import AppState, build_app
        c = TestClient(build_app(AppState(root, "tok", 9999, None, False)),
                       base_url="http://127.0.0.1:9999")
        c.get("/pair?t=tok")
        return c

    H = {"Origin": "http://127.0.0.1:9999"}

    def test_search_meaning_and_status_routes(self):
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            c = self._client(d)
            r = c.post("/api/search", json={"q": "VIRTUAL_ENV", "mode": "word"}, headers=self.H)
            self.assertEqual(200, r.status_code)
            self.assertIn(r.json()["engine"], ("rg", "python-floor"))
            bad = c.post("/api/search", json={"q": "(", "mode": "regex"}, headers=self.H)
            self.assertEqual((400, "BadPattern"), (bad.status_code, bad.json()["error"]))
            with mock.patch.object(SR, "_extra_installed", return_value=False), \
                    mock.patch.object(SR, "EMBEDDER_FACTORY", None):
                m = c.post("/api/meaning", json={"q": "VIRTUAL_ENV"}, headers=self.H).json()
                self.assertEqual((False, []), (m["available"], m["results"]))
                self.assertIs(False, c.get("/api/semantic").json()["available"])
                caps = c.get("/api/caps").json()
                self.assertTrue(caps["more"]["semantic"].startswith("unavailable"))

    def test_the_post_routes_keep_the_origin_and_pairing_guards(self):
        with tempfile.TemporaryDirectory() as d:
            _fixture(d)
            c = self._client(d)
            for path in ("/api/search", "/api/meaning", "/api/semantic/fetch"):
                with self.subTest(path=path):
                    self.assertEqual(403, c.post(path, json={"q": "x"},
                                                 headers={"Origin": "https://evil.example"}).status_code)
            from starlette.testclient import TestClient
            from mokata.ide.server import AppState, build_app
            raw = TestClient(build_app(AppState(d, "tok", 9999, None, False)),
                             base_url="http://127.0.0.1:9999")
            self.assertEqual(401, raw.post("/api/search", json={"q": "x"}, headers=self.H).status_code)

    def test_the_demo_search_is_sample(self):
        c = self._client("")
        from starlette.testclient import TestClient
        from mokata.ide.server import AppState, build_app
        c = TestClient(build_app(AppState("", "tok", 9999, None, True)),
                       base_url="http://127.0.0.1:9999")
        c.get("/pair?t=tok")
        r = c.post("/api/search", json={"q": "parse"}, headers=self.H).json()
        self.assertIs(True, r["sample"])
        self.assertTrue(r["files"])
        m = c.post("/api/meaning", json={"q": "parse"}, headers=self.H).json()
        self.assertEqual((True, False, []), (m["sample"], m["available"], m["results"]))


class ThePage(unittest.TestCase):

    def test_one_box_with_toggles_and_an_unavailable_state_that_names_the_fix(self):
        with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        # 0.0.26 P36 — one box: regex / case / meaning are toggles; the scope is a select
        for needle in ('data-stoggle="regex"', 'data-stoggle="case"', 'data-stoggle="meaning"',
                       'id="sr-meaning-state"', 'id="s-scope"', 'data-sscope="changed"',
                       'data-sscope="session"', 'src="/static/search.js"'):
            self.assertIn(needle, html)
        with open(os.path.join(STATIC, "search.js"), encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn("res.install", js)
        self.assertIn("/api/meaning", js)


if __name__ == "__main__":
    unittest.main()
