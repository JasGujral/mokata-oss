"""0.0.26 P0 — ID-API-CONTRACT: the IDE's route registry, its versioned HTTP contract, and the
frontend feature registry (commands, panels, keybindings).

Business rules — what must be true for the people building on the IDE (and so for its users):
  * The contract IS the app: every route the server answers is in the contract and every contract
    entry is answered — a route added without its contract entry (or the reverse) fails here.
  * Every answer matches its declared shape: each entry's probes are sent to the app booted on a
    real fixture repo (or the SAMPLE, where a real call would go online or start work), and the
    status, the media type and the JSON body are held to the entry.
  * A feature adds routes with ONE new module — and those routes sit behind the very same doors
    (Host check, pairing, Origin check on a POST) as the core's. Two owners of one path+method are
    refused when the app is built, naming both.
  * A feature adds frontend with ONE new file under static/features/: the page loads it (after the
    core views, before boot), only whitelisted names are served, and it meets the same no-markup
    bar as every other script.
  * Commands and keys are a registry: a key two owners want — or one the core already handles —
    is refused at registration, naming both, and nothing is half-registered. ⌘P and Ctrl+P are
    one key.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

import _support  # noqa: F401  (puts src/ on the path)

from mokata.ide import contract as C
from mokata.ide import snapshots as S
from mokata.init import init_repo

STATIC = Path(__file__).resolve().parents[1] / "src" / "mokata" / "ide" / "static"
TOKEN, PORT = "tok", 9999
BASE = f"http://127.0.0.1:{PORT}"
ORIGIN = {"Origin": BASE}
_MEDIA = {"json": "application/json", "html": "text/html", "text": "text/plain",
          "js": "text/javascript", "css": "text/css", "svg": "image/svg+xml",
          "sse": "text/event-stream"}

CORE = ("def parse(text):\n    if not text:\n        return []\n"
        "    return [p.strip() for p in text.split(',')]\n\n\ndef total(text):\n"
        "    return len(parse(text))\n")


def _silent(*_a, **_k):
    pass


def _git(d, *a):
    subprocess.run(["git", "-C", d, *a], check=True, capture_output=True, stdin=subprocess.DEVNULL)


def _write(d, rel, text):
    p = os.path.join(d, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def fixture(d: str) -> None:
    """A small git repo with mokata initialised, one test, and one session (S-1) that edited a
    file — enough for every route to answer with data rather than an empty shape."""
    _git(d, "init", "-q", "-b", "main")
    _git(d, "config", "user.email", "t@t")
    _git(d, "config", "user.name", "t")
    init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
    _write(d, "src/app/core.py", CORE)
    _write(d, "tests/test_core.py",
           "from app.core import parse\n\n\ndef test_parse():\n    assert parse('a') == ['a']\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "init")
    path = os.path.join(d, "src", "app", "core.py")
    S.capture_before_first_write(d, path, "S-1", "R-1")
    _write(d, "src/app/core.py", CORE + "\n\ndef extra():\n    return total('a')\n")
    S.capture(d, path, S.AFTER, "S-1", "R-1")


def client(root: str, demo: bool = False, features=None):
    from starlette.testclient import TestClient
    from mokata.ide.server import AppState, build_app
    state = AppState(root, TOKEN, PORT, None, demo)
    app = build_app(state) if features is None else build_app(state, features=features)
    c = TestClient(app, base_url=BASE, follow_redirects=False)
    c.get(f"/pair?t={TOKEN}")
    return c, app, state


def send(c, entry, probe):
    """Send one probe of a contract entry; returns the response (the SSE stream: its head)."""
    path = entry["path"]
    for k, v in probe.get("path", {}).items():
        path = path.replace("{" + k + "}", v)
    if entry["path"] == "/pair":
        c.cookies.clear()
        try:
            return c.get(path, params=probe.get("query"))
        finally:
            c.get(f"/pair?t={TOKEN}")           # leave the client paired for the next probe
    if entry.get("kind") == "stream" and entry["path"] == "/api/events":
        with c.stream("GET", path, params=probe.get("query")) as r:
            first = next(r.iter_lines())
            r.status_line, r.first_line = r.status_code, first
            return r
    if entry["method"] == "GET":
        return c.get(path, params=probe.get("query"))
    if "raw" in probe:
        return c.post(path, content=probe["raw"].encode(), headers=ORIGIN)
    return c.post(path, json=probe.get("body", {}), headers=ORIGIN)


def check(resp, entry, probe, schemas):
    """Every way an answer breaks its contract entry ([] = it holds)."""
    want = probe["status"]
    where = f"{entry['method']} {entry['path']} {probe}"
    if resp.status_code != want:
        return [f"{where}: status {resp.status_code} (contract says {want}): "
                f"{getattr(resp, 'text', '')[:200] if entry['kind'] != 'stream' else ''}"]
    spec = entry["responses"].get(str(want))
    if spec is None:
        return [f"{where}: status {want} is not declared in `responses`"]
    media = spec["media"]
    if media == "redirect":
        return [] if resp.headers.get("location") else [f"{where}: a redirect with no Location"]
    if media == "empty":
        return [] if not resp.content else [f"{where}: expected an empty body"]
    ctype = resp.headers.get("content-type", "")
    if not ctype.startswith(_MEDIA[media]):
        return [f"{where}: content-type {ctype!r}, contract says {media}"]
    if media == "sse":
        return [] if resp.first_line.startswith(("event:", "id:", "data:", ":")) else \
            [f"{where}: not an event stream: {resp.first_line!r}"]
    if media != "json":
        return []
    return [f"{where}: {e}" for e in C.validate(resp.json(), spec.get("schema", {}), schemas)]


def app_routes(app):
    """{(path, method)} the starlette app actually answers (HEAD is starlette's GET twin)."""
    out = set()
    for r in app.routes:
        for m in r.methods or ():
            if m != "HEAD":
                out.add((r.path, m))
    return out


class _Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._t = tempfile.TemporaryDirectory()
        cls.d = os.path.realpath(cls._t.name)
        fixture(cls.d)
        cls.contract = C.load()

    @classmethod
    def tearDownClass(cls):
        cls._t.cleanup()


# ===================================================================== the contract is the app

class TheContractIsTheApp(_Fixture):

    def test_every_served_route_is_in_the_contract_and_every_entry_is_served(self):
        _c, app, _s = client(self.d)
        served = app_routes(app)
        listed = set(C.route_index(self.contract))
        self.assertEqual(set(), served - listed, "routes served with no contract entry")
        self.assertEqual(set(), listed - served, "contract entries no route answers")
        self.assertGreaterEqual(len(listed), 55)

    def test_the_registry_table_is_what_starlette_mounted(self):
        _c, app, _s = client(self.d)
        table = {(r["path"], r["method"]) for r in app.state.registry.table()}
        self.assertEqual(app_routes(app), table)

    def test_every_entry_declares_kind_params_responses_and_a_probe_per_status(self):
        idx = C.route_index(self.contract)
        for (path, method), e in idx.items():
            with self.subTest(route=f"{method} {path}"):
                self.assertIn(e["kind"], ("page", "static", "json", "read", "action", "stream",
                                          "hook"))
                self.assertIsInstance(e.get("params"), dict)
                self.assertTrue(e.get("probes"), "a route nobody probes is a route nobody checks")
                probed = {str(p["status"]) for p in e["probes"]}
                self.assertEqual(set(e["responses"]), probed,
                                 "every declared status is probed, every probed status declared")

    def test_the_kinds_in_the_contract_are_the_kinds_registered(self):
        _c, app, _s = client(self.d)
        reg = {(r["path"], r["method"]): r["kind"] for r in app.state.registry.table()}
        for key, e in C.route_index(self.contract).items():
            self.assertEqual(reg[key], e["kind"], key)

    def test_the_contract_is_versioned_and_says_how_to_add_a_route(self):
        with open(C.CORE_FILE, encoding="utf-8") as fh:
            core = json.load(fh)
        self.assertIsInstance(core["version"], int)
        how = " ".join(core["how_to_add_a_route"])
        for needle in ("features/", "routes(reg, ctx)", ".contract.json", "probe"):
            self.assertIn(needle, how)


class EveryAnswerMatchesItsShape(_Fixture):

    def test_every_probe_answers_as_the_contract_says(self):
        real, _a, _s = client(self.d)
        demo, _a2, _s2 = client(tempfile.gettempdir(), demo=True)
        errs = []
        n = 0
        for e in self.contract["routes"]:
            for p in e["probes"]:
                resp = send(demo if p.get("demo") else real, e, p)
                errs.extend(check(resp, e, p, self.contract["schemas"]))
                n += 1
        self.assertEqual([], errs)
        self.assertGreaterEqual(n, 70)

    def test_a_changed_answer_shape_is_caught(self):
        """The validator is not a rubber stamp: a dropped key and a wrong type are named."""
        s = {"type": "object", "required": ["files"], "properties": {
            "files": {"type": "array", "items": {"type": "object", "required": ["path"],
                                                 "properties": {"path": {"type": "string"}}}}}}
        self.assertEqual([], C.validate({"files": [{"path": "a"}]}, s, {}))
        self.assertTrue(C.validate({}, s, {}))
        self.assertTrue(C.validate({"files": [{"path": 3}]}, s, {}))
        self.assertTrue(C.validate({"files": [{}]}, s, {}))
        self.assertTrue(C.validate({"x": 1}, {"$ref": "#/schemas/Error"}, self.contract["schemas"]))


# ===================================================================== a feature is one module

def _feature(name, path, kind="json", methods=None, handler=None):
    mod = types.ModuleType(f"fake_{name}")

    def routes(reg, ctx):
        h = handler or (lambda r: {"feature": name, "q": ctx.q(r, "x")})
        reg.register(path, h, kind=kind, feature=name, **({"methods": methods} if methods else {}))
    mod.routes = routes
    return mod


class AFeatureIsOneModuleBehindTheSameDoors(_Fixture):

    def test_a_feature_route_is_served_with_no_edit_to_the_server(self):
        from mokata.ide.routes import feature_modules
        c, app, _s = client(self.d, features=feature_modules() + [_feature("demo-x", "/api/x")])
        self.assertEqual({"feature": "demo-x", "q": "1"}, c.get("/api/x?x=1").json())
        self.assertIn(("/api/x", "GET"), app_routes(app))

    def test_a_feature_route_is_paired_host_checked_and_origin_checked(self):
        from starlette.responses import JSONResponse

        async def act(_r):
            return JSONResponse({"did": True})
        feats = [_feature("r", "/api/x"), _feature("w", "/api/y", kind="action", handler=act)]
        c, _app, _s = client(self.d, features=feats)
        self.assertEqual(200, c.get("/api/x").status_code)
        self.assertEqual(403, c.get("/api/x", headers={"Host": "evil.example:9999"}).status_code)
        self.assertEqual(403, c.post("/api/y", json={},
                                     headers={"Origin": "https://evil.example"}).status_code)
        self.assertEqual(403, c.post("/api/y", json={}).status_code, "no Origin, no token")
        self.assertEqual(200, c.post("/api/y", json={}, headers=ORIGIN).status_code)
        c.cookies.clear()
        self.assertEqual(401, c.get("/api/x").status_code, "a feature route skipped pairing")

    def test_a_json_feature_read_gets_the_core_error_discipline(self):
        def bad(r):
            from mokata.ide.server import BadRequest
            raise BadRequest("give me an x")
        c, _a, _s = client(self.d, features=[_feature("b", "/api/bad", handler=bad)])
        r = c.get("/api/bad")
        self.assertEqual((400, "BadRequest"), (r.status_code, r.json()["error"]))

    def test_two_owners_of_one_route_are_refused_naming_both(self):
        from mokata.ide.routes import RouteConflict, feature_modules
        cases = (([_feature("thief", "/api/state")], "'core'"),
                 (feature_modules() + [_feature("thief", "/api/manifest")], "'manifest'"),
                 ([_feature("a", "/api/z"), _feature("thief", "/api/z")], "'a'"))
        for feats, first in cases:
            with self.subTest(first=first), self.assertRaises(RouteConflict) as cm:
                client(self.d, features=feats)
            self.assertIn(first, str(cm.exception))
            self.assertIn("'thief'", str(cm.exception))

    def test_a_route_registered_off_the_kind_rules_is_refused(self):
        from mokata.ide.routes import Registry, RouteConflict
        reg = Registry()
        for kw in ({"kind": "json", "methods": ("POST",)}, {"kind": "nope"},
                   {"kind": "action", "hook": True}, {"kind": "action", "job": "graph.view"},
                   {"kind": "json", "job": "no.such.kind"}, {"kind": "json", "job_label": str}):
            with self.subTest(kw=kw), self.assertRaises(RouteConflict):
                reg.register("/api/q", lambda r: {}, feature="f", **kw)
        with self.assertRaises(RouteConflict):
            reg.register("api/q", lambda r: {}, kind="json", feature="f")

    def test_discovery_finds_the_shipped_features_and_refuses_one_without_routes(self):
        from mokata.ide import routes as R
        names = [m.__name__.rsplit(".", 1)[1] for m in R.feature_modules()]
        self.assertIn("manifest", names)
        self.assertEqual(sorted(names), names, "load order is name order")
        with tempfile.TemporaryDirectory() as d:
            pkg = os.path.join(d, "p0fakefeat")
            os.makedirs(pkg)
            Path(pkg, "__init__.py").write_text("", encoding="utf-8")
            Path(pkg, "nothing.py").write_text("X = 1\n", encoding="utf-8")
            sys.path.insert(0, d)
            try:
                with self.assertRaises(R.RouteConflict):
                    R.feature_modules("p0fakefeat")
            finally:
                sys.path.remove(d)
                sys.modules.pop("p0fakefeat", None)
                sys.modules.pop("p0fakefeat.nothing", None)

    def test_the_manifest_lists_every_route_and_the_feature_assets(self):
        c, app, _s = client(self.d)
        m = c.get("/api/manifest").json()
        self.assertEqual(self.contract["version"], m["contract"])
        self.assertEqual(app_routes(app), {(r["path"], r["method"]) for r in m["routes"]})
        self.assertIn("core-commands.js", m["scripts"])


class TheContractFilesAreOnePerFeature(unittest.TestCase):

    def test_a_route_listed_twice_across_files_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            shutil.copy(C.CORE_FILE, os.path.join(d, "api_contract.json"))
            feats = os.path.join(d, "features")
            os.makedirs(feats)
            dup = {"routes": [{"path": "/api/state", "method": "GET", "kind": "json",
                               "params": {}, "responses": {}, "probes": []}]}
            Path(feats, "dup.contract.json").write_text(json.dumps(dup), encoding="utf-8")
            old = (C.CORE_FILE, C.FEATURES_DIR)
            C.CORE_FILE, C.FEATURES_DIR = os.path.join(d, "api_contract.json"), feats
            try:
                with self.assertRaises(C.ContractError):
                    C.load()
            finally:
                C.CORE_FILE, C.FEATURES_DIR = old

    def test_every_feature_module_ships_its_contract_file(self):
        here = os.path.dirname(C.__file__)
        # CORPUS: THE WORKING TREE — the feature modules that SHIP (the package directory as
        # rsync'd), each of which must ship its contract beside it; an untracked one counts.
        mods = {n[:-3] for n in os.listdir(os.path.join(here, "features"))
                if n.endswith(".py") and not n.startswith("_")}
        files = {n[: -len(".contract.json")] for n in os.listdir(os.path.join(here, "features"))
                 if n.endswith(".contract.json")}
        self.assertEqual(mods, files)


# ===================================================================== the page loads features

class ThePageLoadsFeatureFiles(_Fixture):

    def test_index_carries_each_feature_script_after_the_views_and_before_boot(self):
        c, _a, _s = client(self.d)
        html = c.get("/").text
        tag = '<script src="/static/features/core-commands.js"></script>'
        self.assertIn(tag, html)
        self.assertLess(html.index("/static/health.js"), html.index(tag))
        self.assertLess(html.index(tag), html.index("/static/boot.js"))
        self.assertLess(html.index("/static/registry.js"), html.index("/static/app.js"),
                        "the registry exists before any view registers")
        self.assertNotIn("mokata:feature-", html, "a marker was left in the served page")

    def test_a_new_feature_file_is_loaded_and_served_and_nothing_else_is(self):
        from mokata.ide import server
        with tempfile.TemporaryDirectory() as d:
            for n in ("zz-new.js", "zz-new.css", "Bad Name.js", "notes.txt"):
                Path(d, n).write_text("/* x */\n", encoding="utf-8")
            old = server.FEATURES_DIR
            server.FEATURES_DIR = d
            try:
                self.assertEqual({"scripts": ["zz-new.js"], "styles": ["zz-new.css"]},
                                 server.feature_assets())
                c, _a, _s = client(self.d)
                html = c.get("/").text
                self.assertIn('<script src="/static/features/zz-new.js"></script>', html)
                self.assertIn('<link rel="stylesheet" href="/static/features/zz-new.css">', html)
                self.assertEqual(200, c.get("/static/features/zz-new.js").status_code)
                for bad in ("notes.txt", "Bad%20Name.js", "..%2Fapp.js", "..%2F..%2Fserver.py"):
                    self.assertEqual(404, c.get("/static/features/" + bad).status_code, bad)
            finally:
                server.FEATURES_DIR = old

    def test_a_lost_marker_is_loud(self):
        from mokata.errors import MokataError
        from mokata.ide.server import render_index
        with self.assertRaises(MokataError):
            render_index("<html></html>", {"scripts": ["a.js"], "styles": []})

    def test_feature_scripts_meet_the_no_markup_no_network_bar(self):
        feats = STATIC / "features"
        names = sorted(n for n in os.listdir(feats) if n.endswith(".js"))
        self.assertTrue(names)
        for name in names + ["../registry.js"]:
            js = (feats / name).read_text(encoding="utf-8")
            for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(",
                        "new Function", "http://", "https://"):
                self.assertNotIn(bad, js, f"{name}: {bad}")


# ===================================================================== commands + keys (node)

_HARNESS = r"""
const ev = (o) => Object.assign({ ctrlKey: false, metaKey: false, altKey: false, shiftKey: false,
  code: "", key: "", target: null, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } }, o);
const out = {};
const R = globalThis.mokataIDE;
const N = R.keys.normalize;
out.same = [N("Ctrl+P"), N("Cmd+p"), N("mod+P"), N("Meta+P")];
out.order = N("shift+alt+ctrl+k");
out.sym = [N("Shift+>"), N(">")];
out.alt1 = R.keys.fromEvent(ev({ altKey: true, code: "Digit1", key: "¡" }));
const tryReg = (spec) => { try { R.register(spec); return "ok"; } catch (e) { return e.message; } };
let ran = 0;
out.ok = tryReg({ id: "a", commands: [{ id: "a.go", title: "A go", run: () => { ran++; }, keys: "Ctrl+K" }],
                  keybindings: [{ keys: "Alt+1", command: "a.go" }] });
out.dupKey = tryReg({ id: "b", commands: [{ id: "b.go", title: "B go", run() {}, keys: "Cmd+K" }] });
out.bAtomic = R.commands().some((c) => c.id === "b.go");
out.reserved = tryReg({ id: "c", commands: [{ id: "c.go", title: "C", run() {}, keys: "j" }] });
out.dupCmd = tryReg({ id: "d", commands: [{ id: "a.go", title: "again", run() {} }] });
out.dupFeature = tryReg({ id: "a" });
out.unknownCmd = tryReg({ id: "e", keybindings: [{ keys: "Alt+2", command: "nope" }] });
out.badKey = tryReg({ id: "f", commands: [{ id: "f.go", title: "F", run() {}, keys: "Hyper+Q" }] });
out.list = R.commands().map((c) => [c.id, c.keys.sort().join(",")]);
out.conflicts = R.conflicts().length;
out.owner = R.keys.owner("Mod+K");
out.run = R.run("a.go"); out.ran = ran;
"""


@unittest.skipUnless(shutil.which("node"), "node is not on PATH — the page logic runs in a browser")
class CommandsAndKeysAreARegistry(unittest.TestCase):

    def _node(self, js):
        src = (STATIC / "registry.js").read_text(encoding="utf-8")
        p = subprocess.run(_support.node_argv(src + "\n" + js +
                            "\nprocess.stdout.write(JSON.stringify(out));"),
                           capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
                           encoding="utf-8")
        self.assertEqual(0, p.returncode, p.stderr)
        return json.loads(p.stdout)

    def test_conflicts_are_refused_naming_both_owners_and_nothing_is_half_added(self):
        o = self._node(_HARNESS)
        self.assertEqual(["Mod+P"] * 4, o["same"], "Ctrl and Cmd are one modifier")
        self.assertEqual("Mod+Alt+Shift+K", o["order"])
        self.assertEqual([">", ">"], o["sym"])
        self.assertIn("Alt+1", o["alt1"], "macOS Alt+1 (¡) still reads as Alt+1")
        self.assertEqual("ok", o["ok"])
        self.assertIn("Mod+K", o["dupKey"])
        self.assertIn("a (a.go)", o["dupKey"])
        self.assertIn("b (b.go)", o["dupKey"])
        self.assertFalse(o["bAtomic"], "a refused registration left its command behind")
        self.assertIn("core app.js", o["reserved"])
        self.assertIn("registered twice", o["dupCmd"])
        self.assertIn("registered twice", o["dupFeature"])
        self.assertIn("unknown command", o["unknownCmd"])
        self.assertIn("unknown modifier", o["badKey"])
        self.assertEqual([["a.go", "Alt+1,Mod+K"]], o["list"])
        self.assertEqual(6, o["conflicts"])
        self.assertEqual("a (a.go)", o["owner"])
        self.assertEqual((True, 1), (o["run"], o["ran"]))

    def test_a_bound_key_runs_its_command_and_respects_fields(self):
        js = r"""
const listeners = [];
globalThis.document = { body: {}, addEventListener: (t, f) => listeners.push(f),
  querySelector: () => null, getElementById: () => null };
""" + (STATIC / "registry.js").read_text(encoding="utf-8") + r"""
const out = {};
let n = 0, m = 0;
mokataIDE.register({ id: "x", commands: [
  { id: "x.k", title: "K", run: () => { n++; }, keys: "Ctrl+K" },
  { id: "x.p", title: "P", run: () => { m++; }, keys: "Ctrl+Shift+P", inFields: true }] });
const ev = (o) => Object.assign({ ctrlKey: false, metaKey: false, altKey: false, shiftKey: false,
  code: "", key: "", target: document.body, defaultPrevented: false,
  preventDefault() { this.defaultPrevented = true; } }, o);
const fire = (o) => { const e = ev(o); listeners.forEach((f) => f(e)); return e.defaultPrevented; };
out.k = fire({ metaKey: true, code: "KeyK", key: "k" });
out.kField = fire({ ctrlKey: true, code: "KeyK", key: "k", target: { tagName: "INPUT" } });
out.pField = fire({ ctrlKey: true, shiftKey: true, code: "KeyP", key: "P", target: { tagName: "TEXTAREA" } });
out.taken = (() => { const e = ev({ ctrlKey: true, code: "KeyK", key: "k" }); e.defaultPrevented = true;
  listeners.forEach((f) => f(e)); return n; })();
out.n = n; out.m = m;
process.stdout.write(JSON.stringify(out));
"""
        p = subprocess.run(_support.node_argv(js), capture_output=True, text=True,
                           timeout=60, stdin=subprocess.DEVNULL, encoding="utf-8")
        self.assertEqual(0, p.returncode, p.stderr)
        o = json.loads(p.stdout)
        self.assertTrue(o["k"], "⌘K did not reach the Ctrl+K command")
        self.assertFalse(o["kField"], "a key typed into a field ran a command")
        self.assertTrue(o["pField"], "an inFields command did not run from a field")
        self.assertEqual((1, 1), (o["n"], o["m"]))
        self.assertEqual(1, o["taken"], "a key a focused widget took was run again")

    def test_the_core_commands_feature_owns_quick_open_and_the_modes(self):
        js = (STATIC / "features" / "core-commands.js").read_text(encoding="utf-8")
        o = self._node(r"""
globalThis.window = globalThis;
MK.SCREENS = ["sessions", "review", "navigate", "search", "graph", "plan"].map((id) => ({ id, title: id, chord: id[0] }));
let opened = 0; MK.openQuick = () => { opened++; };
""" + js + r"""
const out = { list: mokataIDE.commands().map((c) => [c.id, c.keys.join(",")]) };
mokataIDE.run("quickopen.open"); out.opened = opened;
""")
        got = dict(map(tuple, o["list"]))
        self.assertEqual("Mod+P", got["quickopen.open"])
        # P34 (IA-ONE-NAV): one command per SCREEN ("Go to: …"); the search screen owns ⌘⇧F.
        for m in ("review", "navigate", "graph", "plan", "sessions", "search"):
            self.assertIn("screen." + m, got)
        self.assertEqual("Mod+Shift+F", got["screen.search"])
        self.assertEqual(1, o["opened"])
        app = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertNotIn('ev.key === "p"', app, "⌘P is handled twice (app.js and the registry)")

    def test_every_key_the_core_handles_itself_is_reserved(self):
        """A drift guard: a single-key case in app.js's onKey that the registry does not reserve
        would let a feature bind a key the core already acts on."""
        app = (STATIC / "app.js").read_text(encoding="utf-8")
        handler = re.search(r"function onKey\(ev\) \{(.*?)\n\}", app, re.S).group(1)
        o = self._node("const out = mokataIDE.reserved();")
        for key in re.findall(r'case "(.)":', handler):
            self.assertIn(key.upper(), o, key)
        sessions = (STATIC / "sessions.js").read_text(encoding="utf-8")
        for key in re.findall(r'ev\.key === "(.)"', sessions):
            self.assertIn(key, o, key)


if __name__ == "__main__":
    unittest.main()
