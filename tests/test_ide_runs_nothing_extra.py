"""0.0.25 — "make sure the IDE doesn't run anything unnecessary" (Jas, 2026-10-19).

Measured with an audit hook on `subprocess.Popen` and the page's network log, on a 9-file repo:

  * ONE click on "Show" (a ripple) spawned 24 git processes: every graph question front-runs the
    freshness reconcile (`git diff --name-only <sha>` + `git ls-files --others`, a walk of the whole
    working tree), and a ripple asks ~10 questions. Now one reconcile per page action: 2 spawns.
  * The page loaded everything twice: the event stream's opening fingerprint triggered a full
    refresh right after the first load (41 requests and 30 git spawns at load; now 30 and 7).
  * An empty search box asked the server to search (a repo walk) at page load. Now nothing is asked.
  * Idle, with the page open on any view: 0 requests, 0 spawns. Page closed: the watcher stops.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from dataclasses import dataclass, field
from typing import List
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "..", "src", "mokata", "ide", "static")


@dataclass
class _Outcome:
    fresh: bool = True
    rebuilt: bool = False
    changed: List[str] = field(default_factory=list)
    answer_from_floor: bool = False
    note: str = ""


class _Inner:
    def __init__(self):
        self.calls = 0

    def ensure_fresh(self, _layer):
        self.calls += 1
        return _Outcome(rebuilt=True, changed=["a.py"], note="rebuilt")


class OneReconcilePerWindow(unittest.TestCase):

    def test_questions_inside_the_window_share_one_reconcile(self):
        from mokata.ide.nav import _OncePerWindow
        now = [100.0]
        inner = _Inner()
        w = _OncePerWindow(inner, window=2.0, clock=lambda: now[0])
        first = w.ensure_fresh(None)
        for _ in range(10):
            again = w.ensure_fresh(None)
        self.assertEqual(inner.calls, 1)
        self.assertTrue(first.rebuilt)
        self.assertFalse(again.rebuilt, "a reused outcome must not claim a second rebuild")
        self.assertEqual(again.changed, [])
        self.assertEqual(again.note, "rebuilt", "the classed note still rides the answer")

    def test_the_next_action_after_the_window_reconciles_again(self):
        from mokata.ide.nav import _OncePerWindow
        now = [100.0]
        inner = _Inner()
        w = _OncePerWindow(inner, window=2.0, clock=lambda: now[0])
        w.ensure_fresh(None)
        now[0] += 2.5
        w.ensure_fresh(None)
        self.assertEqual(inner.calls, 2)

    def test_a_broken_graph_stays_broken_across_the_window(self):
        from mokata.ide.nav import _OncePerWindow

        class Broken(_Inner):
            def ensure_fresh(self, _layer):
                self.calls += 1
                return _Outcome(fresh=False, answer_from_floor=True, note="graph rebuild failed")
        w = _OncePerWindow(Broken(), window=2.0, clock=lambda: 1.0)
        w.ensure_fresh(None)
        self.assertTrue(w.ensure_fresh(None).answer_from_floor)


def _git_repo(root):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    os.makedirs(os.path.join(root, "pkg"))
    files = {
        "pkg/__init__.py": "",
        "pkg/money.py": "def round_money(x):\n    return round(x, 2)\n\n\n"
                        "def tax(a):\n    return round_money(a * 0.2)\n\n\n"
                        "def disc(a):\n    return round_money(a * 0.9)\n",
        "pkg/cart.py": "from pkg.money import tax, disc, round_money\n\n\n"
                       "def total(a):\n    return round_money(disc(a) + tax(a))\n",
        "pkg/api.py": "from pkg.cart import total\n\n\ndef checkout(a):\n    return total(a)\n",
    }
    for rel, text in files.items():
        with open(os.path.join(root, rel), "w", encoding="utf-8") as fh:
            fh.write(text)
    for argv in (["init", "-q"], ["add", "."], ["commit", "-qm", "x"]):
        subprocess.run(["git", "-C", root] + argv, check=True, env=env, capture_output=True,
                       stdin=subprocess.DEVNULL)


class OneClickOneReconcile(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mk-ide-light-")
        self.addCleanup(shutil.rmtree, self.root, True)
        from mokata.init import init_repo
        _git_repo(self.root)
        init_repo(root=self.root, profile="standard", assume_yes=True, out=lambda *a, **k: None)
        from mokata.ide import nav
        self.addCleanup(nav.close_layers)
        nav.close_layers()

    def test_a_ripple_runs_the_freshness_git_reads_once(self):
        from mokata.ide import visuals
        from mokata.knowledge import freshness as F
        real = F._default_git_run
        seen = []

        def counting(root, args):
            seen.append(args[0])
            return real(root, args)
        with mock.patch.object(F, "_default_git_run", counting):
            out = visuals.ripple(self.root, "round_money", depth=3)
        labels = {n["label"] for n in out["nodes"]}
        self.assertTrue({"tax", "disc", "total", "checkout"} <= labels, labels)
        self.assertLessEqual(seen.count("ls-files"), 1, f"untracked walks per ripple: {seen}")
        self.assertLessEqual(seen.count("diff"), 1, f"diffs per ripple: {seen}")

    def test_an_edit_is_seen_by_the_next_action(self):
        from mokata.ide import nav, visuals
        visuals.focus(self.root, "total")
        with open(os.path.join(self.root, "pkg", "api.py"), "a", encoding="utf-8") as fh:
            fh.write("\n\ndef refund(a):\n    return total(a) * -1\n")
        layer = nav._layer(self.root)
        layer.freshness._at = None          # the next page action, past the window
        labels = {n["label"] for n in visuals.focus(self.root, "total")["nodes"]}
        self.assertIn("refund", labels)


class ThePageAsksOnce(unittest.TestCase):

    def _read(self, name):
        with open(os.path.join(STATIC, name), encoding="utf-8") as fh:
            return fh.read()

    def test_the_streams_opening_fingerprint_does_not_reload_the_page(self):
        js = self._read("app.js")
        at = js.index('es.addEventListener("changed"')
        handler = js[at:js.index("});", at)]
        self.assertIn("lastFp", handler)
        self.assertIn("if (!first) softRefresh()", handler)

    def test_an_empty_search_box_asks_nothing(self):
        js = self._read("search.js")
        run = js[js.index("async function run()"):]
        guard, call = run.index("if (!q.trim())"), run.index('api("/api/search"')
        self.assertLess(guard, call)


if __name__ == "__main__":
    unittest.main()
