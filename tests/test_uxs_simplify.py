"""0.0.27 UX-SIMPLIFY — the server-side halves of the simplified IDE, as business rules.

  * A first run is a START, not a fault: the health chip counts what is broken, missing or degraded,
    and an index "out of sync" only once mokata is set up here (§6 P2: the top bar of a stranger's
    first run never says "health 2").
  * Welcome's / Health's fix line for a missing code graph installs ONE package (code-review-graph),
    not the whole `[full]` extra (§6: "Welcome's fix line prints one package").
  * The single-key shortcuts can be turned off (WCAG 2.1.4) and the choice is the person's, saved
    per repo: only an explicit `false` turns them off; anything else is on.
  * Deleted, not hidden: the Replay scrubber's route, the workspace's unused `panel` field.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.ide import health as Hh
from mokata.ide import workspace as W
from mokata import install_route as R

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FULL = ["code-review-graph>=2.3", "model2vec>=0.3", "numpy>=1.24", "pywinpty>=2; sys_platform == 'win32'"]


def _rows(*states):
    return [{"key": "k%d" % i, "state": s} for i, s in enumerate(states)]


class AFirstRunIsAStartNotAFault(unittest.TestCase):

    def test_out_of_sync_is_not_counted_until_mokata_is_set_up(self):
        rows = _rows(Hh.STALE, Hh.STALE, Hh.OK, Hh.NOT_SET_UP)
        self.assertEqual(0, Hh.problems(rows, setup=False), "a stranger's unbuilt index is not a fault")
        self.assertEqual(2, Hh.problems(rows, setup=True), "once set up, out of sync is owed work")

    def test_broken_missing_and_degraded_always_count(self):
        rows = _rows(Hh.BROKEN, Hh.MISSING, Hh.DEGRADED, Hh.OK, Hh.NOT_SET_UP)
        self.assertEqual(3, Hh.problems(rows, setup=False))
        self.assertEqual(3, Hh.problems(rows, setup=True))

    def test_not_set_up_itself_is_never_a_problem(self):
        self.assertEqual(0, Hh.problems(_rows(Hh.NOT_SET_UP), setup=False))


class TheGraphFixInstallsOnePackage(unittest.TestCase):

    def _plan(self, only=None, route="pip"):
        with tempfile.TemporaryDirectory() as p:
            return R.plan_extra_install("full", prefix=p, has_pip=route == "pip",
                                        which=lambda n: "/usr/bin/uv" if n == "uv" else None,
                                        executable="/v/bin/python", platform="linux",
                                        requires=["%s; extra == 'full'" % r for r in FULL],
                                        only=only)

    def test_only_names_the_package_the_capability_needs(self):
        for route in ("pip", "uv"):
            line = self._plan(only=("code-review-graph",), route=route).display
            self.assertIn("code-review-graph", line, route)
            for other in ("model2vec", "numpy", "pywinpty"):
                self.assertNotIn(other, line, "%s: the fix line installs the whole extra" % route)

    def test_without_only_the_whole_extra_is_installed(self):
        line = self._plan().display
        for pkg in ("code-review-graph", "model2vec", "numpy"):
            self.assertIn(pkg, line)

    def test_doctor_asks_for_the_graph_package_alone(self):
        from mokata.govern import doctor as D
        self.assertEqual(("code-review-graph",), D._NEEDS_OF["graph"])
        line = D.fix_line("graph", "not-installed")
        if "pip install" in line and "mokata[" not in line:     # a pip / uv-pip route with metadata
            self.assertIn("code-review-graph", line)
            self.assertNotIn("model2vec", line)


class SingleKeysAreThePersonsChoice(unittest.TestCase):

    def test_on_by_default_and_only_an_explicit_false_turns_them_off(self):
        self.assertIs(True, W.empty()["single_keys"])
        for given, want in ((False, False), (True, True), (None, True), ("false", True), (0, True)):
            doc, _ = W.normalize({"schema": W.SCHEMA, "single_keys": given} if given is not None
                                 else {"schema": W.SCHEMA})
            self.assertIs(want, doc["single_keys"], repr(given))

    def test_the_choice_is_saved_per_repo(self):
        with tempfile.TemporaryDirectory() as d:
            W.save(d, {"single_keys": False})
            self.assertIs(False, W.load(d)[0]["single_keys"])
            W.save(d, {"single_keys": True})
            self.assertIs(True, W.load(d)[0]["single_keys"])

    def test_the_workspace_keeps_no_side_panel_apart_from_its_view(self):
        doc, _ = W.normalize({"schema": W.SCHEMA, "mode": "proof", "panel": "changes"})
        self.assertEqual("proof", doc["mode"])
        self.assertNotIn("panel", doc)
        self.assertNotIn("panel", W.empty())


class TheReplayScrubberIsGone(unittest.TestCase):

    def test_no_route_in_the_contract(self):
        with open(os.path.join(ROOT, "src", "mokata", "ide", "api_contract.json"), encoding="utf-8") as fh:
            paths = [r["path"] for r in json.load(fh)["routes"]]
        for gone in ("/api/timelapse", "/api/treemap"):
            self.assertNotIn(gone, paths)


if __name__ == "__main__":
    unittest.main()
