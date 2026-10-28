"""0.0.26 P29 — IDE-UNINITIALIZED-REPO: `mokata ide` on a stranger's repo (never ran `mokata init`).

Business rules — what a person who just ran `pip install "mokata[ide]" && mokata ide` on their own
repo must get (doc 113 §6 #6):
  * NO 5xx anywhere: every route in the IDE's contract answers a declared status with the declared
    shape on a repo with no `.mokata/`.
  * The code tools WORK before setup: callers, blast radius, the graph views, quick-open symbols
    and the status bar answer from the AST floor (what `mokata init` would wire, held in memory).
  * Governance routes (runs, plan, lifecycle) say what the repo IS — a `not-initialized` STATE
    (200) with the one command and the list of what it lights up — not an error.
  * The health panel's top row says "not set up — run `mokata setup claude`"; the checks that
    still apply (version, code graph floor, search) are below it.
  * Opening a folder is not consent to write into it: nothing — no `.mokata/`, no cache, no
    workspace, no server record — is created in the repo. The IDE's own scratch state goes to the
    user's profile, keyed by the repo; a repo mokata governs keeps it in `.mokata/temp_local/`.
  * A failure mokata defines (a `MokataError`) is a 409 answer about the repo; anything else is a
    bug — still a 500, with its traceback LOGGED, never hidden.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
import types
import unittest

import _support  # noqa: F401  (puts src/ on the path)

import test_p0_ide_api_contract as P0

from mokata.ide import contract as C

TS = ("export function add(a: number, b: number) { return a + b }\n"
      "export const twice = (x: number) => add(x, x)\n")
SETUP = "mokata setup claude"


def stranger_repo(d: str) -> None:
    """A git repo with Python and TypeScript, committed, and NO `.mokata/`."""
    P0._git(d, "init", "-q", "-b", "main")
    P0._git(d, "config", "user.email", "t@t")
    P0._git(d, "config", "user.name", "t")
    P0._write(d, "src/app/core.py", P0.CORE)
    P0._write(d, "tests/test_core.py",
              "from app.core import parse\n\n\ndef test_parse():\n    assert parse('a') == ['a']\n")
    P0._write(d, "web/util.ts", TS)
    P0._write(d, "README.md", "# a stranger's repo\n")
    P0._git(d, "add", "-A")
    P0._git(d, "commit", "-qm", "init")


def _client(root: str, features=None):
    from starlette.testclient import TestClient
    from mokata.ide.server import AppState, build_app
    state = AppState(root, P0.TOKEN, P0.PORT, None, False)
    app = build_app(state) if features is None else build_app(state, features=features)
    c = TestClient(app, base_url=P0.BASE, follow_redirects=False, raise_server_exceptions=False)
    c.get(f"/pair?t={P0.TOKEN}")
    return c


class _Stranger(unittest.TestCase):
    """Each class: a fresh stranger repo AND a fresh user home (the user-level state lands there,
    never in the developer's real profile)."""

    @classmethod
    def setUpClass(cls):
        cls._t = tempfile.TemporaryDirectory()
        base = os.path.realpath(cls._t.name)
        cls.d, cls.home = os.path.join(base, "repo"), os.path.join(base, "home")
        os.makedirs(cls.d)
        os.makedirs(cls.home)
        cls._env = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE")}
        os.environ["HOME"] = os.environ["USERPROFILE"] = cls.home
        stranger_repo(cls.d)
        from mokata.ide import nav
        nav.close_layers()
        cls.c = _client(cls.d)

    @classmethod
    def tearDownClass(cls):
        from mokata.ide import nav
        nav.close_layers()
        for k, v in cls._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        cls._t.cleanup()

    def assertNothingWrittenInRepo(self):
        self.assertFalse(os.path.exists(os.path.join(self.d, ".mokata")),
                         "the IDE created .mokata/ in a repo nobody set up")
        status = P0.subprocess.run(["git", "-C", self.d, "status", "--porcelain", "--ignored"],
                                   capture_output=True, text=True, check=True).stdout
        self.assertEqual("", status, "the IDE left files in the user's repo")


# ===================================================================== no 5xx, contract held

class EveryRouteAnswersByContract(_Stranger):

    def test_every_contract_probe_answers_a_declared_status_and_shape_with_no_5xx(self):
        contract = C.load()
        five, wrong, n = [], [], 0
        for e in contract["routes"]:
            for p in e["probes"]:
                if p.get("demo"):
                    continue                  # would go online / start work (P0 rule)
                r = P0.send(self.c, e, p)
                n += 1
                where = f"{e['method']} {e['path']} {p.get('query') or p.get('body') or ''}"
                if r.status_code >= 500:
                    five.append(f"{where}: {r.status_code} {r.text[:200]}")
                    continue
                if str(r.status_code) not in e["responses"]:
                    wrong.append(f"{where}: undeclared status {r.status_code}: {r.text[:160]}")
                    continue
                want = dict(p, status=r.status_code)
                wrong.extend(P0.check(r, e, want, contract["schemas"]))
        self.assertEqual([], five, "5xx on a repo with no .mokata/")
        self.assertEqual([], wrong)
        self.assertGreaterEqual(n, 60)
        self.assertNothingWrittenInRepo()


# ===================================================================== the code tools work

class TheCodeToolsWorkBeforeSetup(_Stranger):

    def test_callers_answer_from_the_ast_floor(self):
        r = self.c.get("/api/query", params={"kind": "callers", "target": "parse"})
        self.assertEqual(200, r.status_code, r.text)
        body = r.json()
        self.assertEqual("ast", body["backend"])
        self.assertIn("total", [ref["symbol"] for ref in body["references"]])

    def test_blast_radius_names_the_changed_symbol_and_its_caller(self):
        r = self.c.get("/api/impact", params={"path": "src/app/core.py", "lines": "1-3"})
        self.assertEqual(200, r.status_code, r.text)
        syms = {s["name"]: s for s in r.json()["symbols"]}
        self.assertIn("parse", syms)
        self.assertIn("total", [x["symbol"] for x in syms["parse"]["callers"]["references"]])

    def test_graph_view_and_status_bar_name_the_floor(self):
        g = self.c.get("/api/graph", params={"view": "focus", "symbol": "parse"})
        self.assertEqual(200, g.status_code, g.text)
        self.assertIn("total", [n["label"] for n in g.json()["nodes"]])
        self.assertEqual("ast", self.c.get("/api/caps").json()["graph"])

    def test_health_and_its_action_build_without_a_manifest(self):
        r = self.c.post("/api/health/action", json={"action": "nope"}, headers=P0.ORIGIN)
        self.assertEqual(400, r.status_code, r.text)

    def test_an_edit_is_seen_by_the_next_question(self):
        """The floor layer has its own (write-free) freshness: an edit re-parses on the next ask."""
        from mokata.ide import nav
        self.assertNotIn("again", [x["symbol"] for x in self.c.get(
            "/api/query", params={"kind": "callers", "target": "parse"}).json()["references"]])
        P0._write(self.d, "src/app/more.py", "from app.core import parse\n\n\ndef again():\n"
                                              "    return parse('x')\n")
        try:
            nav._layer(self.d).freshness._window = 0      # skip the 2 s coalescing window
            refs = self.c.get("/api/query", params={"kind": "callers", "target": "parse"}).json()
            self.assertIn("again", [x["symbol"] for x in refs["references"]])
        finally:
            os.remove(os.path.join(self.d, "src", "app", "more.py"))


# ===================================================================== governance says its state

class GovernanceRoutesSayNotInitialized(_Stranger):

    def _assert_state(self, body):
        from mokata.ide.statedir import GOVERNANCE
        self.assertEqual("not-initialized", body["state"])
        self.assertEqual(SETUP, body["command"])
        self.assertIn(SETUP, body["note"])
        self.assertEqual([k for k, _ in GOVERNANCE], [f["key"] for f in body["lights_up"]])

    def test_runs_plan_and_lifecycle_answer_the_state_not_an_error(self):
        for path, params in (("/api/runs", {}), ("/api/runs", {"source": "team"}),
                             ("/api/plan", {"run": "R-1"}), ("/api/lifecycle", {"run": "R-1"})):
            with self.subTest(path=path, params=params):
                r = self.c.get(path, params=params)
                self.assertEqual(200, r.status_code, r.text)
                self._assert_state(r.json())

    def test_the_state_is_a_declared_shape_and_disjoint_from_the_data_shape(self):
        contract = C.load()
        idx = C.route_index(contract)
        for path in ("/api/runs", "/api/plan", "/api/lifecycle"):
            schema = idx[(path, "GET")]["responses"]["200"]["schema"]
            body = self.c.get(path, params={"run": "R-1"}).json()
            self.assertEqual([], C.validate(body, schema, contract["schemas"]), path)
            self.assertTrue(C.validate(body, schema["oneOf"][0], contract["schemas"]),
                            f"{path}: the not-initialized body also passes as real data")

    def test_health_top_row_says_not_set_up_and_the_rest_still_checks(self):
        from _health_answer import finished_health
        body = finished_health(self.c).json()
        self.assertFalse(body["initialized"])
        top = body["rows"][0]
        self.assertEqual(("setup", "not set up", SETUP), (top["key"], top["state"], top["fix"]))
        self.assertIn(f"run `{SETUP}`", top["detail"])
        self.assertTrue(top["lights_up"])
        keys = [r["key"] for r in body["rows"]]
        self.assertIn("mokata", keys)
        self.assertIn("graph", keys)
        self.assertFalse([k for k in keys if k.startswith("doctor") or k == "wiring"],
                         "doctor/wiring checks need a manifest; they must not appear as BROKEN")
        full = finished_health(self.c, {"full": "1"}).json()
        self.assertEqual("setup", full["rows"][0]["key"])
        self.assertFalse([r for r in full["rows"] if r["state"] == "broken"], full["rows"])


# ===================================================================== nothing written in the repo

class NothingIsWrittenIntoTheRepo(_Stranger):

    def test_ide_state_goes_to_the_user_profile_keyed_by_repo(self):
        from mokata.ide import server, statedir, workspace
        r = self.c.post("/api/workspace", json={"tabs": [{"kind": "file", "path": "README.md"}]},
                        headers=P0.ORIGIN)
        self.assertEqual(200, r.status_code, r.text)
        self.assertEqual(200, self.c.post("/api/context/clear", json={},
                                          headers=P0.ORIGIN).status_code)
        self.c.get("/api/query", params={"kind": "callers", "target": "total"})
        server._write_record(self.d, {"port": 1, "pid": os.getpid()})
        try:
            self.assertNothingWrittenInRepo()
            base = statedir.user_repo_dir(self.d)
            self.assertTrue(base.startswith(os.path.join(self.home, ".mokata", "ide-repos")))
            self.assertEqual(os.path.join(base, "ide", "workspace.json"),
                             workspace.workspace_path(self.d))
            # 0.0.26 P39: until the user consents (setup), the tabs and the AST floor's parse
            # cache stay in this process — only the server record (process liveness) is written
            self.assertFalse(os.path.exists(workspace.workspace_path(self.d)))
            self.assertEqual(["file:README.md"], [t["key"] for t in workspace.load(self.d)[0]["tabs"]])
            self.assertTrue(os.path.isfile(server.record_path(self.d)))
            self.assertFalse(os.path.exists(os.path.join(base, "knowledge_ast")))
            self.assertEqual("", statedir.ast_cache_dir(self.d))
            with open(os.path.join(base, "repo.txt"), encoding="utf-8") as fh:
                self.assertEqual(os.path.realpath(self.d), fh.read().strip())
            # the CLI's bridge client finds the running IDE by the same rule
            from mokata.ide import bridge_client
            self.assertEqual({"port": 1, "pid": os.getpid()}, bridge_client.live_ide(self.d))
        finally:
            os.remove(server.record_path(self.d))

    def test_a_governed_repo_keeps_ide_state_in_its_own_temp_local(self):
        from mokata.ide import statedir, workspace
        from mokata.init import init_repo
        with tempfile.TemporaryDirectory() as t:
            g = os.path.realpath(t)
            P0._git(g, "init", "-q", "-b", "main")
            self.assertEqual(statedir.user_repo_dir(g), os.path.dirname(statedir.ide_dir(g)))
            init_repo(root=g, profile="standard", assume_yes=True, out=P0._silent)
            self.assertTrue(statedir.initialized(g))
            want = os.path.join(g, ".mokata", "temp_local", "ide")
            self.assertEqual(want, statedir.ide_dir(g))
            self.assertEqual(os.path.join(want, "workspace.json"), workspace.workspace_path(g))
            from mokata.tdd_state import state_dir
            self.assertEqual(state_dir(g), statedir.state_dir(g))
            self.assertIsNone(statedir.ast_cache_dir(g))


# ===================================================================== errors: state vs bug

def _raiser(name, path, exc):
    mod = types.ModuleType(f"fake_{name}")

    def routes(reg, ctx):
        def boom(_r):
            raise exc
        reg.register(path, boom, kind="json", feature=name)
    mod.routes = routes
    return mod


class ADefinedFailureIsAStateABugIsStillA500(_Stranger):

    def test_config_error_is_409_with_the_state_and_command(self):
        from mokata.config import ConfigError
        from mokata.ide.routes import feature_modules
        c = _client(self.d, features=feature_modules() + [
            _raiser("cfg", "/api/x-cfg", ConfigError("mokata is not initialized here"))])
        r = c.get("/api/x-cfg")
        self.assertEqual(409, r.status_code, r.text)
        body = r.json()
        self.assertEqual(("ConfigError", "not-initialized", SETUP),
                         (body["error"], body["state"], body["command"]))

    def test_an_unexpected_exception_is_still_a_500_and_its_traceback_is_logged(self):
        from mokata.ide.routes import feature_modules
        c = _client(self.d, features=feature_modules() + [
            _raiser("bug", "/api/x-bug", KeyError("a typo somewhere"))])
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            r = c.get("/api/x-bug")
        self.assertEqual(500, r.status_code)
        self.assertEqual("KeyError", r.json()["error"])
        said = err.getvalue()
        self.assertIn("/api/x-bug failed", said)
        self.assertIn("Traceback (most recent call last)", said,
                      "the traceback must reach the terminal, not be swallowed")
        self.assertIn("in boom", said)
        self.assertIn("KeyError: 'a typo somewhere'", said)


if __name__ == "__main__":
    unittest.main()
