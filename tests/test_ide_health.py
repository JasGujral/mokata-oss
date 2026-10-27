"""0.0.25 — the IDE's health panel: highlight what is missing / degraded / out of sync; force a
re-sync of what is derived (code graph, AST index, search index); SHOW the line that updates
mokata (`mokata update`'s plan; print-only since 0.0.26 L2). Never installs anything; never calls
out unless the human presses "check".

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
    """0.0.26 P32: a health action is a job of the IDE's registry — wait for it to finish."""
    job.finished.wait(secs)
    return job.snapshot()


def _answer(c, url, secs=300):
    """INT-2a — the FINISHED health rows: `/api/health` answers 200 always (the rows, or the
    CHECKING shape with the job in `pending`); follow the job, then ask for its kept answer."""
    from urllib.parse import parse_qsl, urlsplit
    from _health_answer import finished_health
    parts = urlsplit(url)
    return finished_health(c, dict(parse_qsl(parts.query)), secs=secs)


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
        from mokata.ide.jobs import JobRegistry
        self.jobs = JobRegistry()
        self.addCleanup(self.jobs.close)

    def start(self, layer, action, confirm=""):
        snap = self.H.start(self.root, layer, action, confirm, self.jobs)["job"]
        return self.jobs.get(snap["id"])


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
        done = _wait(self.start(layer, "rebuild-ast"))
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
        self.assertEqual(_wait(self.start(layer, "rebuild-graph"))["state"], "done")
        self.assertEqual(calls, [True])

    def test_one_job_at_a_time(self):
        """P32: the actions share ONE maintenance lane — the second waits, visibly QUEUED (it is
        no longer refused), and runs when the first ends."""
        import threading
        gate = threading.Event()
        with mock.patch.object(self.H, "_rebuild_graph",
                               lambda root, layer: (gate.wait(10), {"ok": True})[1]), \
                mock.patch.object(self.H, "_rebuild_ast", lambda root, layer: {"ok": True}):
            first = self.start(None, "rebuild-graph")
            second = self.start(None, "rebuild-ast")
            time.sleep(0.3)
            self.assertEqual("running", first.state)
            self.assertEqual("queued", second.state)
            gate.set()
            self.assertEqual("done", _wait(first)["state"])
            self.assertEqual("done", _wait(second)["state"])

    def test_an_unknown_action_is_refused(self):
        from mokata.ide.server import BadRequest
        with self.assertRaises(BadRequest):
            self.H.start(self.root, None, "install-everything", "", self.jobs)


class TheUpdateRowIsMokataUpdate(_Repo):
    """0.0.26 L2: one planner + executor (`update.py`) for the CLI and the page."""

    def test_the_row_shows_the_same_command_mokata_update_runs(self):
        from mokata.update import plan_update
        row = self.H._mokata_row(self.root)
        self.assertEqual(row["update"]["command"], plan_update(self.root, check=False).command)
        self.assertIn("keeps", row["update"])
        self.assertIn("runnable", row["update"])

    def test_a_click_with_a_command_that_was_not_shown_runs_nothing(self):
        with mock.patch.object(self.H, "_stream", side_effect=AssertionError("ran unshown")):
            done = _wait(self.start(None, "update", confirm="pip install evil"))
        self.assertEqual(done["state"], "failed")

    def test_check_returns_the_plan_with_the_release_notes(self):
        feed = '[{"tag_name": "v99.0.0", "body": "- the big change"}]'
        with mock.patch("mokata.update._default_fetch", return_value=feed):
            res = self.H.check_update(self.root)
        self.assertTrue(res["ok"])
        self.assertFalse(res["up_to_date"])
        self.assertEqual(res["update"]["latest"], "99.0.0")
        self.assertIn("- the big change", res["update"]["delta"][-1]["notes"])


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
        self.assertIn(c.get("/api/jobs").status_code, (401, 403))
        for url in ("/api/health/action", "/api/health/check-update"):
            self.assertIn(c.post(url, json={"action": "rebuild-ast"}).status_code, (401, 403))

    def test_the_page_load_pass_runs_no_doctor_and_the_panel_pass_does(self):
        c = self._client()
        with mock.patch("mokata.govern.doctor.diagnose",
                        side_effect=AssertionError("the page-load pass ran the full doctor")):
            quick = _answer(c, "/api/health").json()
        self.assertFalse(quick["full"])
        full = _answer(c, "/api/health?full=1").json()
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


def setUpModule():
    # 0.0.26 O11 — this module's expectations are the CORE install's: no external code graph on
    # the machine. Pinned, so a venv with code-review-graph (`mokata[full]`) means the same thing;
    # what is true WITH it present is graded in test_o11_crg_present (and the sibling below, if any).
    from _support import hide_external_graphs
    hide_external_graphs()


if __name__ == "__main__":
    unittest.main()
