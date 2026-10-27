"""0.0.26 O12 — USER-PROMPT-SUBMIT-6S: the per-prompt hook meets its 150 ms context budget (U14).

Business rules — what must be true for the user:
  * Every prompt the user types waits on mokata's UserPromptSubmit hook. It answers inside the
    `context` kind's 150 ms (p50, cold process, normalised like HOOK-BUDGET) on a ~1M-line repo with
    a code graph wired and a populated memory store — never the 5-6 s it took when the graph tier ran
    live graph lookups (a server start + a freshness `update`) on the prompt path.
  * The graph tier is not dropped: its verdicts are precomputed OFF the prompt path (a detached
    warmer) and read from a freshness-keyed cache. A turn whose ranking could not use them SAYS so in
    the injected context, and the lookups are queued so the next turn has them.
  * A healthy graph that simply holds no node for an ordinary word ("fix") is a confirmed "no", not
    a graph failure (that raised a "graph lookup failed" notice on every recall).
  * The develop-time evidence keeps its own 2 s share; when it runs out the pack says so.

Measured before/after: `/home/claude/reports/O12.report.md`.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)
from _support import sqlite_disk_ok

from mokata import hook_cli, perf
from mokata import project as P
from mokata.config import Surface
from mokata.init import init_repo
from mokata.knowledge.query import (BASIS_STRUCTURAL, BackendError, GraphNotIndexed,
                                    QueryResult, Reference)
from mokata.memory import MemoryStore
from mokata.memory import graph_anchors as GA
from mokata.memory.item import MemoryItem
from mokata.quiet_spawn import hidden

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
PROMPT = "fix the parser bug in SessionMiddleware"


def _silent(*_a, **_k):
    pass


def _repo(d: str, n_items: int = 6) -> None:
    subprocess.run(["git", "init", "-q", d], check=True, capture_output=True,
                   stdin=subprocess.DEVNULL, **hidden())
    init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
    store = MemoryStore.from_surface(Surface.load(d), knowledge=False)
    for i in range(n_items):
        word = ("parser", "SessionMiddleware", "deploy")[i % 3]
        store.backend.put(MemoryItem.create(
            f"{word} note {i}", f"the {word} path: fix the parser bug carefully (item {i})",
            kind="context", id=f"c{i}"))


def _run_hook(d: str, prompt: str = PROMPT, sid: str = "S-o12"):
    payload = json.dumps({"prompt": prompt, "cwd": d, "session_id": sid})
    out = io.StringIO()
    saved, sys.stdin = sys.stdin, io.StringIO(payload)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = hook_cli.main(["user-prompt-submit"])
    finally:
        sys.stdin = saved
    text = out.getvalue()
    return code, (json.loads(text)["hookSpecificOutput"]["additionalContext"] if text else "")


class _FakeGraphLayer:
    """A wired, HEALTHY-but-slow code graph: every lookup is recorded (and would cost seconds)."""

    uses_graph = True
    freshness = None

    def __init__(self, known=("parser",), fail=()):
        self.calls = []
        self.known, self.fail = set(known), set(fail)
        layer = self

        class _Primary:
            name = "code-review-graph"
            client = None

            def query(self, kind, target, depth=1):
                layer.calls.append((kind, target))
                if target in layer.fail:
                    raise BackendError("graph down")
                if target not in layer.known:
                    raise GraphNotIndexed(f"no node {target}")
                return QueryResult(kind=kind, target=target, backend="code-review-graph",
                                   basis=BASIS_STRUCTURAL,
                                   references=[Reference(path="a.py", line=1, symbol="caller")])

        self.primary = _Primary()

    def callers(self, symbol):
        return self.primary.query("callers", symbol)


# ======================================================================================
# 1 · the root cause: no live graph work on the prompt path (in-process, fast, falsifiable)
# ======================================================================================

@unittest.skipUnless(sqlite_disk_ok(), "sandbox cannot back an on-disk SQLite DB")
class TheGraphTierNeverRunsOnThePrompt(unittest.TestCase):

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.d = os.path.realpath(self._t.name)
        _repo(self.d)
        self.layer = _FakeGraphLayer()
        self.built = []

        def _from_surface(*_a, **_k):
            self.built.append(1)
            return self.layer
        self.spawned = []
        patches = [
            # Were the hook to build a knowledge layer, it would get this wired graph.
            mock.patch("mokata.knowledge.layer.KnowledgeLayer.from_surface",
                       side_effect=_from_surface),
            mock.patch.object(GA, "graph_possible", return_value=True),
            mock.patch("mokata_emit.spawn_mokata_emit",
                       side_effect=lambda args, cwd: self.spawned.append(args) or True),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self._t.cleanup()

    def test_R_O12_a_prompt_never_builds_a_layer_or_asks_the_graph(self):
        code, ctx = _run_hook(self.d)
        self.assertEqual(0, code)
        self.assertIn("parser note", ctx, "the pack must still be delivered")
        self.assertEqual([], self.layer.calls, "a live graph lookup ran on the prompt path")
        self.assertEqual([], self.built, "the prompt path built a knowledge layer")

    def test_a_turn_without_verdicts_says_so_and_queues_the_lookups(self):
        _code, ctx = _run_hook(self.d)
        self.assertIn("code-graph ranking not applied to", ctx)
        self.assertEqual([[GA.WARM_FLAG, self.d]], self.spawned)
        with open(os.path.join(GA.cache_dir(self.d), GA.WANT_FILENAME), encoding="utf-8") as fh:
            self.assertEqual(GA.query_tokens(PROMPT), fh.read().split())

    def test_with_fresh_verdicts_the_tier_applies_silently_and_nothing_is_queued(self):
        GA.warm(self.d, GA.query_tokens(PROMPT), layer=self.layer)
        self.layer.calls.clear()
        _code, ctx = _run_hook(self.d)
        self.assertNotIn("code-graph ranking", ctx)
        self.assertEqual([], self.spawned)
        self.assertEqual([], self.layer.calls)
        first = [ln for ln in ctx.splitlines() if ln.startswith("- ")][0]
        self.assertIn("parser", first, "the graph-confirmed anchor did not lift its item")

    def test_a_graph_change_makes_the_verdicts_stale(self):
        GA.warm(self.d, GA.query_tokens(PROMPT), layer=self.layer)
        self.assertIsNotNone(GA.read_cache(self.d))
        manifest = os.path.join(self.d, ".mokata", "manifest.json")
        st = os.stat(manifest)
        os.utime(manifest, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
        self.assertIsNone(GA.read_cache(self.d))
        _code, ctx = _run_hook(self.d)
        self.assertIn("code-graph ranking not applied to", ctx)

    def test_a_graph_that_failed_is_said_and_retried_later_not_every_turn(self):
        self.layer.fail = {"SessionMiddleware"}
        GA.warm(self.d, GA.query_tokens(PROMPT), layer=self.layer)
        _code, ctx = _run_hook(self.d)
        self.assertIn("skipped for 1 term(s) — the graph did not answer", ctx)
        self.assertEqual([], self.spawned, "a recently failed lookup must not respawn every turn")
        later = GA.CachedGraphScorer(self.d, PROMPT, now=time.time() + GA.RETRY_FAILED_SECS + 1)
        later(PROMPT, MemoryItem.create("x", "y"))
        self.assertEqual(["SessionMiddleware"], later.pending)

    def test_with_no_graph_tool_installed_nothing_is_queued_said_or_written(self):
        with mock.patch.object(GA, "graph_possible", return_value=False):
            _code, ctx = _run_hook(self.d)
        self.assertIn("parser note", ctx)
        self.assertNotIn("code-graph", ctx)
        self.assertEqual([], self.spawned)
        self.assertFalse(os.path.exists(GA.cache_dir(self.d)))

    def test_an_empty_store_never_consults_the_tier(self):
        with tempfile.TemporaryDirectory() as e:
            e = os.path.realpath(e)
            _repo(e, n_items=0)
            _code, ctx = _run_hook(e)
        self.assertEqual("", ctx)
        self.assertEqual([], self.spawned)


# ======================================================================================
# 2 · the warmer and the shared verdict
# ======================================================================================

@unittest.skipUnless(sqlite_disk_ok(), "sandbox cannot back an on-disk SQLite DB")
class TheWarmerComputesWhatTheLiveScorerWould(unittest.TestCase):

    def test_a_word_the_graph_does_not_hold_is_false_not_a_failure(self):
        from mokata.knowledge.layer import graph_anchor_verdict, make_graph_scorer
        layer = _FakeGraphLayer(known=("parser",), fail=("Broken",))
        self.assertIs(True, graph_anchor_verdict(layer, "parser"))
        self.assertIs(False, graph_anchor_verdict(layer, "fix"))
        self.assertIsNone(graph_anchor_verdict(layer, "Broken"))
        with mock.patch("mokata.degrade.note_degraded") as noted:
            scorer = make_graph_scorer(layer, "fix the parser")
        noted.assert_not_called()          # ordinary words raised a "graph failed" notice before
        self.assertEqual(1.0, scorer("q", MemoryItem.create("parser x", "v")))

    def test_the_drain_merges_verdicts_and_releases_its_lock(self):
        with tempfile.TemporaryDirectory() as d:
            d = os.path.realpath(d)
            _repo(d, n_items=0)
            layer = _FakeGraphLayer()
            cdir = GA.cache_dir(d)
            os.makedirs(cdir)
            with open(os.path.join(cdir, GA.WANT_FILENAME), "w", encoding="utf-8") as fh:
                fh.write("parser\nfix\nnot-an-ident!\n")
            with mock.patch("mokata.knowledge.KnowledgeLayer.from_router", return_value=layer), \
                    mock.patch("mokata.knowledge.KnowledgeLayer.from_surface",
                               side_effect=AssertionError("the warmer must not attach freshness")):
                self.assertEqual(0, GA.warm_main([d]))
            data = GA.read_cache(d)
            self.assertEqual({"parser": True, "fix": False}, data["tokens"])
            self.assertFalse(os.path.exists(os.path.join(cdir, GA.LOCK_FILENAME)))
            self.assertFalse(os.path.exists(os.path.join(cdir, GA.WANT_FILENAME)))

    def test_a_live_recall_with_no_candidates_asks_the_graph_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            d = os.path.realpath(d)
            _repo(d, n_items=0)
            store = MemoryStore.from_surface(Surface.load(d), knowledge=False)
            layer = _FakeGraphLayer()
            store.knowledge_layer = layer
            self.assertEqual([], store.recall_relevant(PROMPT, stamp=False))
            self.assertEqual([], layer.calls)


class TheDevelopEvidenceTimeoutIsSaid(unittest.TestCase):

    @unittest.skipUnless(sqlite_disk_ok(), "sandbox cannot back an on-disk SQLite DB")
    def test_evidence_that_runs_out_of_time_is_said_in_the_pack(self):
        with tempfile.TemporaryDirectory() as d:
            d = os.path.realpath(d)
            _repo(d)
            with mock.patch.object(hook_cli, "_EVIDENCE_BUDGET_SECS", 0.05), \
                    mock.patch.object(hook_cli, "_develop_evidence",
                                      side_effect=lambda *a: time.sleep(1.0) or []), \
                    mock.patch.object(GA, "graph_possible", return_value=False):
                _code, ctx = _run_hook(d)
        self.assertIn(hook_cli.EVIDENCE_SKIPPED_LINE, ctx)


# ======================================================================================
# 2b · coordinator rulings: never a silent cut, no model load on the prompt path, no tree walk
#      on session start
# ======================================================================================

@unittest.skipUnless(sqlite_disk_ok(), "sandbox cannot back an on-disk SQLite DB")
class TheSemanticModelIsNeverLoadedOnThePrompt(unittest.TestCase):

    def test_R_O12_meaning_ranking_is_skipped_and_said_not_loaded(self):
        with tempfile.TemporaryDirectory() as d:
            d = os.path.realpath(d)
            _repo(d)
            mpath = os.path.join(d, ".mokata", "manifest.json")
            with open(mpath, encoding="utf-8") as fh:
                data = json.load(fh)
            data.setdefault("settings", {}).setdefault("memory", {})["embedder"] = "auto"
            with open(mpath, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(data, fh)
            with mock.patch("mokata.memory.embed.would_load_model", return_value=True), \
                    mock.patch("mokata.memory.embed.make_embedder",
                               side_effect=AssertionError("a model loaded on the prompt path")), \
                    mock.patch.object(GA, "graph_possible", return_value=False):
                _code, ctx = _run_hook(d)
        self.assertIn("parser", ctx, "the pack must still be delivered")
        self.assertIn("meaning-ranking skipped this turn", ctx)

    def test_hashing_is_cheap_and_still_runs(self):
        from mokata.memory.embed import would_load_model
        self.assertFalse(would_load_model("hashing"))
        self.assertFalse(would_load_model(None))


@unittest.skipUnless(sqlite_disk_ok(), "sandbox cannot back an on-disk SQLite DB")
class TheR_O12_SessionStartNeverWalksTheTree(unittest.TestCase):

    def setUp(self):
        from mokata.knowledge import structure_cache as SC
        self.SC = SC
        self._t = tempfile.TemporaryDirectory()
        self.d = os.path.realpath(self._t.name)
        _repo(self.d, n_items=0)
        self.spawned = []
        for p in (mock.patch("mokata.knowledge.layer.graph_structure_line",
                             side_effect=AssertionError("full-tree walk on session start")),
                  mock.patch("mokata_emit.spawn_mokata_emit",
                             side_effect=lambda a, cwd: self.spawned.append(a) or True)):
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self._t.cleanup()

    def _briefing(self):
        from mokata.bootstrap import build_bootstrap
        # the fixture is tiny; make it read as a LARGE tree, which is the case this protects
        with mock.patch.object(self.SC, "_small_tree", return_value=False):
            return build_bootstrap(Surface.load(self.d), off_path=True).text

    def test_a_small_tree_is_summarised_inline_and_cached(self):
        with mock.patch("mokata.knowledge.layer.graph_structure_line", return_value="SMALL"):
            from mokata.bootstrap import build_bootstrap
            text = build_bootstrap(Surface.load(self.d), off_path=True).text
        self.assertIn("SMALL", text)
        self.assertEqual([], self.spawned)
        self.assertEqual(("SMALL", False), self.SC.cached_line(self.d))

    def test_the_size_probe_is_bounded(self):
        for n in range(12):
            open(os.path.join(self.d, f"f{n}.py"), "w", encoding="utf-8").close()
        self.assertFalse(self.SC._small_tree(self.d, cap=10))
        self.assertTrue(self.SC._small_tree(self.d, cap=10_000))

    def test_no_summary_yet_is_said_and_the_briefing_spawns_nothing_itself(self):
        # O12b: the ONE refresher is started by the SessionStart hook after the emit
        # (`briefing_cache.start_refresh`), not from inside the briefing.
        self.assertIn(self.SC.PENDING_LINE, self._briefing())
        self.assertEqual([], self.spawned)

    def test_a_fresh_summary_is_served_without_a_walk(self):
        self.SC.store(self.d, "Code structure (ast): 3 Python module(s); top: a.")
        text = self._briefing()
        self.assertIn("Code structure (ast): 3 Python module(s); top: a.", text)
        self.assertNotIn("refreshing", text)
        self.assertEqual([], self.spawned)

    def test_a_stale_summary_is_served_marked_and_refreshed(self):
        self.SC.store(self.d, "Code structure (ast): 3 Python module(s); top: a.")
        manifest = os.path.join(self.d, ".mokata", "manifest.json")
        st = os.stat(manifest)
        os.utime(manifest, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
        self.assertIn("top: a." + self.SC.STALE_SUFFIX, self._briefing())

    def test_the_session_start_hook_asks_for_the_cached_line(self):
        import inspect
        self.assertIn("off_path=off", inspect.getsource(hook_cli.session_start_main))

    def test_the_refresher_walks_and_stores(self):
        with mock.patch("mokata.knowledge.layer.graph_structure_line", return_value="WALKED"):
            self.assertEqual(0, self.SC.warm_main([self.d]))
        self.assertEqual(("WALKED", False), self.SC.cached_line(self.d))
        self.assertFalse(os.path.exists(os.path.join(self.SC.cache_dir(self.d),
                                                     self.SC.LOCK_FILENAME)))


# ======================================================================================
# 3 · the project key no longer forks `git` on every prompt
# ======================================================================================

class TheRemoteIsReadWithoutGit(unittest.TestCase):

    def _git(self, *a):
        subprocess.run(["git", *a], check=True, capture_output=True, stdin=subprocess.DEVNULL,
                       **hidden())

    def test_the_config_reader_agrees_with_git(self):
        with tempfile.TemporaryDirectory() as t:
            t = os.path.realpath(t)
            repo = os.path.join(t, "r")
            self._git("init", "-q", repo)
            self.assertIsNone(P._remote_from_config_file(repo))
            self._git("-C", repo, "remote", "add", "origin", "git@github.com:Org/Repo.git")
            self._git("-C", repo, "-c", "user.email=a@b", "-c", "user.name=a", "commit",
                      "--allow-empty", "-qm", "i")
            self._git("-C", repo, "worktree", "add", "-q", os.path.join(t, "wt"))
            for where in (repo, os.path.join(t, "wt")):
                self.assertEqual(P._git_remote(where, git=P._default_git),
                                 P._remote_from_config_file(where), where)
            with open(os.path.join(repo, ".git", "config"), "a", encoding="utf-8") as fh:
                fh.write("[include]\n\tpath = extra\n")
            self.assertIs(P._UNPARSED, P._remote_from_config_file(repo))
            env = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "remote.origin.url",
                   "GIT_CONFIG_VALUE_0": "x"}
            with mock.patch.dict(os.environ, env):
                self.assertTrue(P._env_overrides_remote())
            with mock.patch.dict(os.environ, dict(env, GIT_CONFIG_KEY_0="core.editor")):
                self.assertFalse(P._env_overrides_remote())

    def test_the_default_path_runs_no_git_process(self):
        with tempfile.TemporaryDirectory() as t:
            self._git("init", "-q", t)
            self._git("-C", t, "remote", "add", "origin", "https://h/o/r.git")
            with mock.patch.dict(os.environ, {"GIT_CONFIG_COUNT": "0"}), \
                    mock.patch.object(P, "_default_git", side_effect=AssertionError("forked git")):
                self.assertEqual("https://h/o/r.git", P._git_remote(t))


# ======================================================================================
# 4 · BUDGET: cold process, ~1M-line repo, populated store, a SLOW graph tool on PATH
# ======================================================================================

_FAKE_GRAPH = "#!/bin/sh\n# a code graph that takes seconds to answer anything\nsleep 3\nexit 1\n"


@unittest.skipUnless(os.name == "posix", "the fake graph tool is a POSIX shell script")
@unittest.skipUnless(sqlite_disk_ok(), "sandbox cannot back an on-disk SQLite DB")
class TheR_O12_PromptHookMeetsItsBudgetOnA1MLineRepo(unittest.TestCase):
    """HOOK-BUDGET's method (`test_hb_hook_budget`): a fresh `python -m mokata_emit` per prompt,
    two warm-ups, the bare-interpreter floor interleaved and divided out (`perf.interpreter_factor`),
    `perf.relax_factor()` for shared CI runners, and up to three batches (a real regression misses
    all three). Asserted on p50 against the `context` kind's ruled 150 ms (U14)."""

    N = 11
    BATCHES = 3
    FILES, LINES = 500, 2000           # 1,000,000 lines on disk

    @classmethod
    def setUpClass(cls):
        cls._t = tempfile.TemporaryDirectory()
        t = os.path.realpath(cls._t.name)
        cls.d = os.path.join(t, "big")
        pkg = os.path.join(cls.d, "pkg")
        os.makedirs(pkg)
        body = "".join(f"def f{i}(x):\n    return x + {i}\n" for i in range(cls.LINES // 2))
        for n in range(cls.FILES):
            with open(os.path.join(pkg, f"m{n}.py"), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(body)
        _repo(cls.d, n_items=200)
        cls.bin = os.path.join(t, "bin")
        os.makedirs(cls.bin)
        fake = os.path.join(cls.bin, "code-review-graph")
        with open(fake, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(_FAKE_GRAPH)
        os.chmod(fake, 0o755)

    @classmethod
    def tearDownClass(cls):
        lock = os.path.join(GA.cache_dir(cls.d), GA.LOCK_FILENAME)
        deadline = time.monotonic() + 60
        while os.path.exists(lock) and time.monotonic() < deadline:
            time.sleep(0.1)                 # let the detached warmer finish before cleanup
        cls._t.cleanup()

    def _batch(self):
        env = dict(os.environ, PYTHONPATH=SRC, PATH=self.bin + os.pathsep + os.environ["PATH"],
                   CLAUDE_PROJECT_DIR=self.d)
        xs, floor, outs = [], [], []
        for i in range(self.N + 2):
            body = json.dumps({"session_id": f"S-{i}-{time.time_ns()}", "cwd": self.d,
                               "hook_event_name": "UserPromptSubmit", "prompt": PROMPT})
            t0 = time.perf_counter()
            p = subprocess.run([sys.executable, "-m", "mokata_emit", "user-prompt-submit"],
                               input=body, cwd=self.d, env=env, text=True, encoding="utf-8",
                               capture_output=True, timeout=60, **hidden())
            t1 = time.perf_counter()
            subprocess.run([sys.executable, "-c", "pass"], stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=60, **hidden())
            if i >= 2:
                xs.append((t1 - t0) * 1000.0)
                floor.append((time.perf_counter() - t1) * 1000.0)
                outs.append(p.stdout)
        return statistics.median(xs), statistics.median(floor), outs

    def test_R_O12_the_prompt_hook_answers_inside_150ms(self):
        budget = perf.HOOK_BUDGET_TARGETS_MS[perf.HOOK_KINDS["user-prompt-submit"]]
        self.assertEqual(150.0, budget)
        relax = perf.relax_factor()
        seen = []
        for _ in range(self.BATCHES):
            p50, floor, outs = self._batch()
            self.assertTrue(all("fix the parser bug carefully" in o for o in outs),
                            "every timed prompt must deliver its pack — a fast empty hook proves nothing")
            seen.append((p50 / perf.interpreter_factor(floor), p50, floor))
            if seen[-1][0] <= budget * relax:
                break
        best = min(seen)
        sys.stderr.write(f"\n[O12] user-prompt-submit @ 1M lines: p50 {best[0]:.1f} normalised "
                         f"({best[1]:.1f} raw, floor {best[2]:.1f}), budget {budget * relax:g}\n")
        self.assertLessEqual(best[0], budget * relax, seen)


if __name__ == "__main__":
    unittest.main()
