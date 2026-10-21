"""0.0.25 — the IDE's health panel: highlight what is missing / degraded / out of sync; force a
re-sync of what is derived (code graph, AST index, search index); update mokata on a confirmed
click. Never installs a dependency; never calls out unless the human presses "check".

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)


def _wait(job, secs=60):
    t0 = time.time()
    while job.snapshot().get("state") == "running" and time.time() - t0 < secs:
        time.sleep(0.05)
    return job.snapshot()


class _Repo(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mk-health-")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "pkg"))
        with open(os.path.join(self.root, "pkg", "m.py"), "w", encoding="utf-8") as fh:
            fh.write("def a():\n    return b()\n\n\ndef b():\n    return 1\n")
        from mokata.init import init_repo
        init_repo(root=self.root, profile="standard", assume_yes=True, out=lambda *a, **k: None)
        from mokata.ide import health, nav
        self.H, self.nav = health, nav
        nav.close_layers()
        self.addCleanup(nav.close_layers)
        health.JOB.state = {"action": None, "state": "idle"}


class ThePanelHighlights(_Repo):

    def test_rows_name_state_and_fix_and_never_go_online(self):
        with mock.patch("mokata.version.check_for_update",
                        side_effect=AssertionError("opening the panel went online")):
            h = self.H.health(self.root, self.nav._layer(self.root))
        keys = {r["key"]: r for r in h["rows"]}
        self.assertIn("mokata", keys)
        self.assertIn("command", keys["mokata"]["update"])
        self.assertIn("graph", keys)
        self.assertEqual(keys["ast"]["action"], "rebuild-ast")
        for r in h["rows"]:
            self.assertIn(r["state"], ("ok", "missing", "degraded", "out of sync", "broken"))
        states = [r["state"] for r in h["rows"]]
        order = ["broken", "missing", "degraded", "out of sync", "ok"]
        self.assertEqual(states, sorted(states, key=order.index), "worst first")

    def test_a_resolved_role_conflict_is_not_shown_as_a_problem(self):
        h = self.H.health(self.root, self.nav._layer(self.root))
        self.assertNotIn("doctor:role-conflict", {r["key"] for r in h["rows"]})

    def test_an_ide_older_than_the_installed_mokata_says_restart(self):
        with mock.patch.object(self.H, "_installed_version", return_value="9.9.9"):
            row = self.H._mokata_row()
        self.assertEqual(row["state"], "out of sync")
        self.assertIn("restart the IDE", row["detail"])

    def test_nothing_offers_to_install_a_dependency(self):
        h = self.H.health(self.root, self.nav._layer(self.root))
        self.assertTrue(all(r["action"] in ("", "rebuild-graph", "rebuild-ast", "rebuild-search")
                            for r in h["rows"]))


class ForceReSync(_Repo):

    def test_rebuild_ast_drops_the_cache_and_reparses(self):
        layer = self.nav._layer(self.root)
        self.nav.run(self.root, "callers", "b")
        from mokata.knowledge.freshness import _ast_backends
        cache = _ast_backends(layer)[0].cache_dir
        marker = os.path.join(cache, "stale-marker")
        os.makedirs(cache, exist_ok=True)
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write("x")
        self.H.start(self.root, layer, "rebuild-ast")
        done = _wait(self.H.JOB)
        self.assertEqual(done["state"], "done", done)
        self.assertFalse(os.path.exists(marker), "the old cache survived a forced re-parse")
        refs = self.nav.run(self.root, "callers", "b").references
        self.assertIn("a", {getattr(r, "symbol", None) or getattr(r, "name", None) for r in refs})

    def test_rebuild_graph_asks_the_adopted_graph_for_a_FULL_build(self):
        calls = []

        class Client:
            def refresh(self, root, full=False):
                calls.append(full)
                return True
        layer = mock.Mock(primary=mock.Mock(client=Client()), fallback=None)
        self.H.start(self.root, layer, "rebuild-graph")
        self.assertEqual(_wait(self.H.JOB)["state"], "done")
        self.assertEqual(calls, [True])

    def test_one_job_at_a_time(self):
        import threading
        gate = threading.Event()
        self.H.JOB.start("rebuild-graph", lambda: (gate.wait(5), {"ok": True})[1])
        second = self.H.JOB.start("rebuild-ast", lambda: {"ok": True})
        self.assertIn("still running", second.get("refused", ""))
        gate.set()
        _wait(self.H.JOB)

    def test_an_unknown_action_is_refused(self):
        from mokata.ide.server import BadRequest
        with self.assertRaises(BadRequest):
            self.H.start(self.root, None, "install-everything")


class UpdateOnAConfirmedClick(_Repo):

    def _plan(self, runnable=True):
        from mokata.install_route import InstallPlan
        return InstallPlan("uv-tool", ["mokata"], ["uv", "tool", "upgrade", "mokata"] if runnable
                           else None, "uv tool upgrade mokata", reason="" if runnable else "why")

    def test_the_confirmation_must_be_the_command_shown(self):
        with mock.patch("mokata.version.upgrade_plan", return_value=self._plan()), \
                mock.patch("mokata.version.run_upgrade",
                           side_effect=AssertionError("ran without the shown command")):
            self.H.start(self.root, None, "update", confirm="uv tool upgrade mokata --pre")
            done = _wait(self.H.JOB)
        self.assertEqual(done["state"], "failed")
        self.assertIn("changed since it was shown", done["message"])

    def test_a_confirmed_update_runs_the_plan_and_says_restart(self):
        from mokata.version import UpgradeRun
        with mock.patch("mokata.version.upgrade_plan", return_value=self._plan()), \
                mock.patch("mokata.version.run_upgrade",
                           return_value=UpgradeRun(["uv"], 0, "Updated mokata")), \
                mock.patch("mokata.version.installed_version", return_value="0.0.26"):
            self.H.start(self.root, None, "update", confirm="uv tool upgrade mokata")
            done = _wait(self.H.JOB)
        self.assertEqual(done["state"], "done", done)
        self.assertIn("0.0.26", done["message"])
        self.assertIn("Restart the IDE", done["message"])

    def test_a_plan_that_cannot_run_here_shows_the_command(self):
        with mock.patch("mokata.version.upgrade_plan", return_value=self._plan(False)):
            self.H.start(self.root, None, "update", confirm="uv tool upgrade mokata")
            done = _wait(self.H.JOB)
        self.assertEqual(done["state"], "failed")
        self.assertIn("run it yourself: uv tool upgrade mokata", done["message"])


class TheRoutes(_Repo):

    def _client(self, paired=True, demo=False):
        from starlette.testclient import TestClient
        from mokata.ide.server import AppState, build_app
        c = TestClient(build_app(AppState(self.root, "tok", 9999, None, demo)),
                       base_url="http://127.0.0.1:9999")
        if paired:
            c.get("/pair?t=tok")
        return c

    def test_every_health_route_needs_the_pairing(self):
        c = self._client(paired=False)
        self.assertIn(c.get("/api/health").status_code, (401, 403))
        self.assertIn(c.get("/api/health/job").status_code, (401, 403))
        for url in ("/api/health/action", "/api/health/check-update"):
            self.assertIn(c.post(url, json={"action": "rebuild-ast"}).status_code, (401, 403))

    def test_the_page_load_pass_runs_no_doctor_and_the_panel_pass_does(self):
        c = self._client()
        with mock.patch("mokata.govern.doctor.diagnose",
                        side_effect=AssertionError("the page-load pass ran the full doctor")):
            quick = c.get("/api/health").json()
        self.assertFalse(quick["full"])
        full = c.get("/api/health?full=1").json()
        self.assertTrue(full["full"])

    def test_an_unknown_action_is_a_400_and_the_sample_refuses_actions(self):
        h = {"Origin": "http://127.0.0.1:9999"}
        self.assertEqual(self._client().post("/api/health/action", headers=h,
                                             json={"action": "pip-install"}).status_code, 400)
        self.assertEqual(self._client(demo=True).post(
            "/api/health/action", headers=h, json={"action": "rebuild-ast"}).status_code, 400)
        self.assertEqual(self._client().post("/api/health/action", json={"action": "rebuild-ast"},
                                             headers={"Origin": "https://evil.example"}
                                             ).status_code, 403)


if __name__ == "__main__":
    unittest.main()
