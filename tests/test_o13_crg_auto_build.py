"""0.0.26 O13 — `CRG-AUTO-BUILD`: code-review-graph's index is built, once, and nobody waits for it.

Rulings (Jas, 2026-10-21):
  (R-a) installed but never built here -> auto-build on FIRST USE, in the background; the query is
        answered by the floor with a note, and so is every query while it builds; then CRG answers;
  (R-b) `mokata setup claude --mode full` builds the index, with progress, bounded, Ctrl+C-safe,
        and the outcome table says built / failed + why (only `--mode full`);
  (R-c) approving setup is the consent for its writes, the build among them.
Plus O11's findings: F2 (status says "active" only when the graph answers), F3 (node ids do not
depend on the backend), F4 (`update` only on an index that exists), and the `.code-review-graph/`
directory stays out of git and out of mokata's own walks.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)
from _support import _crg_present, crg_present_unbuilt

from mokata.knowledge import crg_build as B
from mokata.knowledge.crg_client import CodeReviewGraphClient


def _silent(*_a, **_k):
    pass


def _repo(prefix="o13-", callers=3, profile="standard"):
    from mokata.init import init_repo
    root = tempfile.mkdtemp(prefix=prefix)
    init_repo(root=root, profile=profile, assume_yes=True, out=_silent)
    os.makedirs(os.path.join(root, "src", "app"))
    with open(os.path.join(root, "src", "app", "hub.py"), "w", encoding="utf-8") as fh:
        fh.write("def hub(x):\n    return x\n")
    for i in range(callers):
        with open(os.path.join(root, "src", "app", f"m{i:03d}.py"), "w", encoding="utf-8") as fh:
            fh.write(f"from app.hub import hub\n\n\ndef c{i}():\n    return hub({i})\n")
    return root


def _layer(tc, root):
    from mokata.config import Surface
    from mokata.knowledge import KnowledgeLayer
    with redirect_stderr(io.StringIO()):
        layer = KnowledgeLayer.from_surface(Surface.load(root))
    tc.addCleanup(lambda: getattr(getattr(layer.primary, "client", None), "close",
                                  lambda: None)())
    return layer


def _built_reply(root):
    """What a BUILT code-review-graph answers for the `_repo` fixture (2.3.9 shapes)."""
    def reply(args):
        target = args.get("target", "")
        rows = []
        if args.get("pattern") == "callers_of" and target.rsplit("::", 1)[-1] == "hub":
            for i in range(3):
                f = os.path.join(root, "src", "app", f"m{i:03d}.py")
                rows.append({"name": f"c{i}", "qualified_name": f"{f}::c{i}", "kind": "Function",
                             "file_path": f, "line_start": 4, "line_end": 5, "is_test": False,
                             "call_site": {"line": 5}})
        payload = {"status": "ok", "results": rows, "edges": [], "result_count": len(rows),
                   "results_omitted": 0}
        return {"content": [{"type": "text", "text": json.dumps(payload)}]}
    return reply


def _builds(calls):
    return [c for c in calls if c[0] == "build"]


def _tools(calls):
    return [c for c in calls if c[0] not in ("build", "index-probe")]


# ======================================================================= (2) first use — R-a
class FirstUseStartsOneBackgroundBuildAndNeverWaits(unittest.TestCase):

    def setUp(self):
        self.root = _repo()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.calls = crg_present_unbuilt(self)

    def test_the_first_query_is_answered_by_the_floor_and_starts_the_build(self):
        res = _layer(self, self.root).callers("hub")
        self.assertEqual("ast", res.backend)
        self.assertEqual(3, len(res.references))
        self.assertRegex(res.note, r"code-review-graph is building its index \(started \d\d:\d\d\)"
                                   r" — answered by the 'ast' floor")
        self.assertEqual([("build", self.root)], _builds(self.calls))
        self.assertEqual([], _tools(self.calls),
                         "an unbuilt graph was asked anyway (a `serve` for nothing — O11 F1)")

    def test_queries_while_building_start_no_second_build(self):
        for _ in range(3):
            res = _layer(self, self.root).callers("hub")      # a NEW layer each time, as the CLI
            self.assertIn("is building its index", res.note)
        self.assertEqual(1, len(_builds(self.calls)), "one build per repo, not one per query")

    def test_parallel_sessions_start_one_build(self):
        barrier = threading.Barrier(6)
        out = []

        def go():
            barrier.wait()
            out.append(B.ensure_building(self.root).state)
        threads = [threading.Thread(target=go) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(["building"] * 6, out)
        self.assertEqual(1, len(_builds(self.calls)), "the lock let two builds start")

    def test_a_query_never_blocks_on_a_slow_build(self):
        gate = threading.Event()

        def slow_builder(root):              # the fake slow builder: holds the lock for a while
            threading.Thread(target=gate.wait, args=(30,), daemon=True).start()
            return os.getpid()
        with mock.patch.object(B, "_spawn_worker", slow_builder):
            t0 = time.monotonic()
            res = _layer(self, self.root).callers("hub")
            took = time.monotonic() - t0
        gate.set()
        self.assertEqual("ast", res.backend)
        self.assertLess(took, 5.0, "the query waited for the build")


class WhenTheBuildLandsTheGraphAnswers(unittest.TestCase):

    def setUp(self):
        self.root = _repo()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.built = False
        self.calls = _crg_present(self, index_built=lambda: self.built,
                                  reply=_built_reply(self.root))

    def test_floor_while_building_then_code_review_graph(self):
        layer = _layer(self, self.root)
        first = layer.callers("hub")
        self.assertEqual("ast", first.backend)
        self.built = True                              # the background build finished
        B._release(self.root)
        second = layer.callers("hub")                  # the SAME long-lived layer (the IDE's)
        self.assertEqual("code-review-graph", second.backend)
        self.assertNotIn("building", second.note)
        self.assertEqual(1, len(_builds(self.calls)))


class AFailedBuildIsSurfacedAndBacksOff(unittest.TestCase):

    def setUp(self):
        self.root = _repo()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.calls = crg_present_unbuilt(self)

    def _fail_once(self, now=None):
        B._record(self.root, ok=False, error="tree-sitter exploded", now=now)

    def test_no_retry_storm(self):
        self._fail_once()
        for _ in range(3):
            res = _layer(self, self.root).callers("hub")
            self.assertIn("index build failed", res.note)
            self.assertIn("tree-sitter exploded", res.note)
            self.assertIn("mokata graph build", res.note)
        self.assertEqual([], _builds(self.calls), "a failed build was retried on every query")

    def test_the_back_off_expires_and_grows(self):
        t = time.time()
        self._fail_once(now=t - 3 * B.BACKOFF_BASE_S)          # 1st failure: 60 s back-off, over
        self.assertEqual("building", B.ensure_building(self.root).state)
        self.assertEqual(1, len(_builds(self.calls)))
        B._release(self.root)
        self._fail_once(now=t)                                  # 2nd: 120 s back-off
        with open(B._last_path(self.root), encoding="utf-8") as fh:
            last = json.load(fh)
        self.assertEqual(2, last["attempts"])
        self.assertAlmostEqual(t + 2 * B.BACKOFF_BASE_S, last["retry_after"], delta=1)
        self.assertEqual("failed", B.ensure_building(self.root, now=t + 60).state)
        self.assertEqual(1, len(_builds(self.calls)))

    def test_doctor_status_and_ide_health_name_the_error_and_the_rebuild(self):
        from mokata.config import Surface
        from mokata.ide import health
        from mokata.knowledge import graph_guidance
        self._fail_once()
        line = graph_guidance(Surface.load(self.root))
        self.assertIn("index build FAILED", line)
        self.assertIn("tree-sitter exploded", line)
        self.assertIn("mokata graph build", line)
        self.assertNotIn("code graph active", line)
        row = [r for r in health._graph_rows(self.root, _layer(self, self.root))
               if r["key"] == "graph"][0]
        self.assertEqual(health.BROKEN, row["state"])
        self.assertIn("tree-sitter exploded", row["detail"])
        self.assertEqual("rebuild-graph", row["action"])

    def test_a_dead_builder_is_reaped_not_trusted(self):
        os.makedirs(B._run_dir(self.root), exist_ok=True)
        with open(B._lock_path(self.root), "w", encoding="utf-8") as fh:
            json.dump({"pid": 2 ** 22 + 12345, "started": time.time() - 30}, fh)
        os.makedirs(B.index_dir(self.root), exist_ok=True)
        open(B.db_path(self.root), "wb").close()               # what it left behind
        st = B.build_status(self.root)
        self.assertEqual("failed", st.state)
        self.assertIn("ended without reporting", st.error)
        self.assertFalse(os.path.exists(B.db_path(self.root)), "a partial index survived")
        self.assertFalse(os.path.exists(B._lock_path(self.root)))


class TheProbeWritesNothing(unittest.TestCase):
    """`probe_index` is called on every query; it must never create or touch the index."""

    def test_absent_stays_absent_and_built_keeps_its_bytes(self):
        root = tempfile.mkdtemp(prefix="o13-probe-")
        self.addCleanup(shutil.rmtree, root, True)
        self.assertEqual(B.INDEX_ABSENT, B.probe_index(root))
        self.assertFalse(os.path.exists(B.index_dir(root)), "the probe created the index dir")
        _fake_index(root, complete=True)
        before = (os.stat(B.db_path(root)).st_mtime_ns, open(B.db_path(root), "rb").read())
        self.assertEqual(B.INDEX_BUILT, B.probe_index(root))
        after = (os.stat(B.db_path(root)).st_mtime_ns, open(B.db_path(root), "rb").read())
        self.assertEqual(before, after)
        self.assertFalse([f for f in os.listdir(B.index_dir(root)) if f != "graph.db"],
                         "the probe left a journal/WAL behind")


# ======================================================================= (3) F2 — four states
class StatusSaysActiveOnlyWhenTheGraphAnswers(unittest.TestCase):

    def setUp(self):
        self.root = _repo(profile="full")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.built = False
        _crg_present(self, index_built=lambda: self.built, reply=_built_reply(self.root))

    def _say(self):
        from mokata.config import Surface
        from mokata.knowledge import graph_guidance
        with redirect_stderr(io.StringIO()):
            return graph_guidance(Surface.load(self.root))

    def test_detected_unbuilt_building_failed_and_active_are_distinct(self):
        unbuilt = self._say()
        self.assertIn("detected, unbuilt", unbuilt)
        self.assertTrue(B._acquire(self.root, os.getpid(), time.time()))
        building = self._say()
        self.assertIn("building its index (started", building)
        B._release(self.root)
        B._record(self.root, ok=False, error="boom")
        failed = self._say()
        self.assertIn("FAILED", failed)
        self.built = True
        active = self._say()
        self.assertIn("code graph active (code-review-graph)", active)
        for line in (unbuilt, building, failed):
            self.assertNotIn("code graph active", line)

    def test_graph_status_command_agrees(self):
        from mokata import cli
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            cli.main(["graph", "status", "--path", self.root])
        self.assertIn("detected, unbuilt", out.getvalue())
        self.assertNotIn("code graph active", out.getvalue())


# ======================================================================= (4) F3 — one identity
class NodeIdsDoNotDependOnTheBackend(unittest.TestCase):

    def setUp(self):
        self.root = _repo()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.built = False
        _crg_present(self, index_built=lambda: self.built, reply=_built_reply(self.root))

    def test_the_adapter_speaks_the_floors_names_and_keeps_the_graphs_id(self):
        layer = _layer(self, self.root)
        self.built = True
        res = layer.callers("hub")
        self.assertEqual("code-review-graph", res.backend)
        self.assertEqual({"c0", "c1", "c2"}, {r.symbol for r in res.references})
        self.assertTrue(all(r.metadata["qualified_name"].endswith(f"::{r.symbol}")
                            for r in res.references))

    def test_a_symbol_picked_from_the_floor_still_resolves_after_crg_takes_over(self):
        from mokata.ide import visuals as V
        layer = _layer(self, self.root)
        floor = V.ripple(self.root, "hub", 1, layer=layer)
        picked = sorted(n["id"] for n in floor["nodes"] if n["kind"] == "caller")
        self.assertEqual(["sym:c0", "sym:c1", "sym:c2"], picked)
        self.built = True                                       # CRG takes over
        graph = V.ripple(self.root, "hub", 1, layer=layer)
        ids = {n["id"] for n in graph["nodes"]}
        for nid in picked:
            self.assertIn(nid, ids, f"{nid} picked under the floor is gone under the graph")


# ======================================================================= (5) F4 — no double build
class FreshnessNeverBuildsAnUnbuiltIndex(unittest.TestCase):

    def _client(self, built):
        self.cli = []
        return CodeReviewGraphClient(
            root=".", call_tool=lambda _t, _p: {"status": "ok", "results": []},
            run_cli=lambda a: self.cli.append(list(a)) or (0, "", ""),
            index_probe=lambda _r: built)

    def test_update_runs_only_on_an_index_that_exists(self):
        from mokata.knowledge.graph_backend import CodeReviewGraphBackend
        unbuilt = CodeReviewGraphBackend("code-review-graph", ".", client=self._client(False))
        self.assertTrue(unbuilt.refresh_index(), "an unbuilt index is not a FAILED refresh")
        self.assertFalse(unbuilt.recover())
        self.assertFalse(unbuilt.client.refresh(".", full=False))
        self.assertEqual([], self.cli, "`update` ran on an unbuilt index (a build in disguise)")
        built = CodeReviewGraphBackend("code-review-graph", ".", client=self._client(True))
        self.assertTrue(built.refresh_index())
        self.assertEqual([["update"]], self.cli)


# ======================================================================= (1) setup — R-b
class _FakeProc:
    """A `code-review-graph build` that prints what 2.3.9 prints, at a pace the test controls."""

    def __init__(self, lines, rc=0, root=None, complete=True, hang=False):
        self._lines = list(lines)
        self.rc, self.root, self.complete, self.hang = rc, root, complete, hang
        self.killed = False
        r, w = os.pipe()
        self.stdout = os.fdopen(r, "r", encoding="utf-8")
        self._w = os.fdopen(w, "w", encoding="utf-8")
        self._t = threading.Thread(target=self._feed, daemon=True)
        self._t.start()

    def _feed(self):
        for ln in self._lines:
            if self.killed:
                break
            self._w.write(ln + "\n")
            self._w.flush()
            time.sleep(0.01)
        if self.hang:
            while not self.killed:
                time.sleep(0.02)
        if self.root and self.complete and not self.killed:
            _fake_index(self.root, complete=True)
        try:
            self._w.close()
        except OSError:
            pass

    def poll(self):
        return None if self._t.is_alive() else (-9 if self.killed else self.rc)

    def wait(self, timeout=None):
        self._t.join(timeout)
        if self._t.is_alive():
            raise subprocess.TimeoutExpired("crg", timeout)
        return -9 if self.killed else self.rc

    def terminate(self):
        self.killed = True

    kill = terminate


def _fake_index(root, complete=True, ever_completed=True):
    """`complete=False, ever_completed=False` is a FIRST build that never finished (no
    `last_updated`: code-review-graph writes it only once every file is stored) — the only index
    mokata may discard (P35)."""
    import sqlite3
    os.makedirs(B.index_dir(root), exist_ok=True)
    conn = sqlite3.connect(B.db_path(root))
    conn.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
    if ever_completed:
        conn.execute("INSERT OR REPLACE INTO metadata VALUES ('last_updated', "
                     "'2026-10-21T10:00:00')")
    conn.execute("INSERT OR REPLACE INTO metadata VALUES ('build_state', ?)",
                 ("complete" if complete else "in-progress",))
    conn.commit()
    conn.close()


_PROGRESS_LINES = ["INFO: Running migration v8", "INFO: Progress: 50/100 files parsed",
                   "INFO: Progress: 100/100 files parsed", "INFO: FTS index rebuilt: 5 rows",
                   "Full build: 100 files, 900 nodes, 2000 edges (postprocess=full)"]


class TheSetupBuildShowsProgressAndNeverLeavesAHalfIndex(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="o13-build-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_progress_comes_from_the_tools_own_counts(self):
        said = []
        res = B.run_build(self.root, command="crg", on_progress=said.append,
                          popen=lambda *a, **k: _FakeProc(_PROGRESS_LINES, root=self.root))
        self.assertTrue(res.ok, res.error)
        self.assertIn("indexing: 50/100 files (50%)", " ".join(said))
        self.assertEqual("100 files, 900 nodes, 2000 edges (postprocess=full)", res.summary)
        self.assertEqual(B.INDEX_BUILT, B.probe_index(self.root))

    def test_a_silent_build_gets_a_heartbeat(self):
        said = []
        res = B.run_build(self.root, command="crg", on_progress=said.append, heartbeat=0.05,
                          popen=lambda *a, **k: _FakeProc(["INFO: x"] * 30, root=self.root))
        self.assertTrue(res.ok)
        self.assertTrue(any(s.startswith("still building") for s in said), said)

    def test_there_is_no_time_limit(self):
        """Jas, 2026-10-21: a million-line repo builds for many minutes — no arbitrary timeout.
        A clock that jumps two hours per tick still lets the build finish, with heartbeats."""
        said, now = [], [0.0]

        def clock():
            now[0] += 7200.0
            return now[0]
        res = B.run_build(self.root, command="crg", on_progress=said.append, clock=clock,
                          popen=lambda *a, **k: _FakeProc(_PROGRESS_LINES, root=self.root))
        self.assertTrue(res.ok, res.error)
        self.assertTrue(any(":" in s and "elapsed" in s for s in said) or said, said)
        self.assertRegex(res.detail, r"in \d+:\d\d:\d\d$", "hours are shown as hours")

    def test_a_rerun_over_a_built_index_is_incremental(self):
        os.makedirs(os.path.join(self.root, ".mokata"))
        _fake_index(self.root, complete=True)
        argv = []

        def popen(cmd, **k):
            argv.append(cmd[1])
            return _FakeProc(["INFO: Incremental: 3 files updated"], root=self.root)
        res = B.build_now(self.root, runner=lambda root, **k: B.run_build(
            root, command="crg", popen=popen, **k))
        self.assertTrue(res.ok, res.error)
        self.assertEqual(["update"], argv, "a built index was rebuilt from scratch")
        self.assertIn("incrementally", res.detail)
        argv.clear()
        B.build_now(self.root, full=True, runner=lambda root, **k: B.run_build(
            root, command="crg", popen=popen, **k))
        self.assertEqual(["build"], argv, "`--full` / the IDE's rebuild is a full build")

    def test_ctrl_c_kills_it_and_removes_the_partial_index(self):
        _fake_index(self.root, complete=False, ever_completed=False)

        def say(_s):
            raise KeyboardInterrupt
        res = B.run_build(self.root, command="crg", on_progress=say,
                          popen=lambda *a, **k: _FakeProc(_PROGRESS_LINES, hang=True))
        self.assertTrue(res.interrupted)
        self.assertFalse(os.path.exists(B.db_path(self.root)))

    def test_a_failed_build_says_why(self):
        res = B.run_build(self.root, command="crg", popen=lambda *a, **k: _FakeProc(
            ["ERROR: could not parse pkg/x.py"], rc=2))
        self.assertFalse(res.ok)
        self.assertIn("exited 2", res.error)
        self.assertIn("could not parse pkg/x.py", res.error)

    def test_a_complete_index_survives_a_failed_rebuild_cleanup(self):
        _fake_index(self.root, complete=True)
        self.assertFalse(B.discard_partial(self.root))
        self.assertTrue(os.path.exists(B.db_path(self.root)))


class SetupFullBuildsTheIndexAndSaysHowItWent(unittest.TestCase):
    ALL = {"embeddings": lambda: True, "code-graph": lambda: True, "graph-ts": lambda: True,
           "lexical": lambda: True}

    def _wire(self, mode, build, search=(True, "9 chunks from 2 files", False), rg="/x/rg"):
        from mokata import adoption_modes as A
        seen = []

        def build_fn(root, out):
            seen.append(root)
            return build
        with tempfile.TemporaryDirectory() as d:
            res = A.wire_mode(
                d, mode, out=_silent, present=self.ALL, warm_fn=lambda: "model2vec:m",
                crg_command_fn=lambda: "/x/crg", graph_pinned_fn=lambda _r: False,
                embedder_wired_fn=lambda _r: True, install_line_fn=lambda e: "LINE",
                adopt_fn=lambda root, **k: mock.Mock(committed=True), build_index_fn=build_fn,
                build_search_fn=lambda root, out: seen.append(("search", root)) or search,
                rg_command_fn=lambda: rg)
        return res, [x for x in seen if not isinstance(x, tuple)], A

    def test_full_builds_and_the_table_says_built(self):
        res, seen, A = self._wire("full", B.BuildResult(ok=True, summary="2 files", elapsed=3))
        self.assertEqual(1, len(seen))
        self.assertEqual(["embeddings", "meaning-index", "code-graph", "code-graph-index",
                          "graph-ts", "lexical"], [r.key for r in res.rows])
        self.assertTrue(res.complete)
        table = A.render_outcome(res)
        self.assertRegex(table, r"✓ meaning search index \(IDE\) +active \(9 chunks from 2 files\)")
        self.assertRegex(table, r"✓ word search \(ripgrep\) +active \(ripgrep: /x/rg\)")
        self.assertRegex(A.render_outcome(res),
                         r"✓ code-review-graph index +active \(built: 2 files in 0:03\)")

    def test_a_failed_build_is_a_row_with_its_cause_and_fix(self):
        res, _seen, A = self._wire("full", B.BuildResult(ok=False, error="exited 2: boom"))
        table = A.render_outcome(res)
        self.assertFalse(res.complete)
        self.assertIn("✗ code-review-graph index", table)
        self.assertIn("exited 2: boom", table)
        self.assertIn("fix: mokata graph build", table)

    def test_ctrl_c_is_a_skipped_build_not_a_lost_setup(self):
        res, _seen, A = self._wire("full", B.BuildResult(ok=False, interrupted=True,
                                                          error="interrupted (Ctrl+C)"))
        self.assertIn("skipped with Ctrl+C", A.render_outcome(res))
        self.assertEqual("active", res.row("code-graph").status, "the pin stands")

    def test_an_interrupted_meaning_index_says_it_resumes(self):
        res, _seen, A = self._wire("full", B.BuildResult(ok=True), search=(
            False, "interrupted (Ctrl+C) — what was embedded is kept and the next run resumes",
            True))
        row = res.row("meaning-index")
        self.assertEqual(A.BLOCKED, row.status)
        self.assertIn("next run resumes", row.cause)
        self.assertIn("mokata setup claude --mode full", row.fix)

    def test_no_ripgrep_wheel_here_is_the_floor_said_plainly(self):
        res, _seen, _A = self._wire("full", B.BuildResult(ok=True), rg=None)
        self.assertIn("pure-Python floor", res.row("lexical").detail)

    def test_only_full_mode_builds(self):
        for mode in ("memory", "seatbelt"):
            with self.subTest(mode=mode):
                res, seen, _A = self._wire(mode, B.BuildResult(ok=True))
                self.assertEqual([], seen)
                self.assertIsNone(res.row("code-graph-index"))

    def test_the_default_build_holds_the_repo_lock(self):
        # setup's build and a first-use background build can never run together
        root = tempfile.mkdtemp(prefix="o13-lock-")
        self.addCleanup(shutil.rmtree, root, True)
        os.makedirs(os.path.join(root, ".mokata"))
        self.assertTrue(B._acquire(root, os.getpid(), time.time()))
        res = B.build_now(root, runner=lambda *a, **k: self.fail("ran beside another build"))
        self.assertIsNotNone(res.busy)
        self.assertEqual("building", res.busy.state)


# ======================================================================= (6) the directory
class TheIndexDirectoryIsNotSource(unittest.TestCase):

    def test_mokatas_walks_skip_it(self):
        from mokata.ide import nav
        from mokata.knowledge.ast_backend import AstBackend
        from mokata.knowledge.grep_backend import GrepBackend
        root = _repo(callers=1)
        self.addCleanup(shutil.rmtree, root, True)
        os.makedirs(B.index_dir(root), exist_ok=True)
        with open(os.path.join(B.index_dir(root), "decoy.py"), "w", encoding="utf-8") as fh:
            fh.write("def hub():\n    return hub()\n")
        self.assertFalse([f for f in nav.walk_files(root) if f.startswith(".code-review-graph")])
        ast_defs = AstBackend(root=root).query("defs", "hub")
        self.assertEqual(["src/app/hub.py"], sorted({r.path for r in ast_defs.references}))
        grep = GrepBackend(root=root).query("callers", "hub")
        self.assertFalse([r for r in grep.references if ".code-review-graph" in r.path])


# ======================================================================= real code-review-graph
def _real_crg():
    from mokata.detect import find_command
    return find_command("code-review-graph")


def _floor_callers(root, name):
    from mokata.knowledge.ast_backend import AstBackend
    return {r.symbol for r in AstBackend(root=root).query("callers", name).references}


@unittest.skipUnless(_real_crg(), "code-review-graph is not installed in this environment")
class ARealBuildOnATinyRepo(unittest.TestCase):
    """`/home/claude/venv` has code-review-graph 2.3.9: one real build, one real first-use build."""

    def setUp(self):
        self.root = _repo(prefix="o13-real-")
        self.addCleanup(shutil.rmtree, self.root, True)
        # A name that is ONE node to the graph (`hub` is also the module `hub.py`: ambiguous).
        with open(os.path.join(self.root, "src", "app", "engine.py"), "w", encoding="utf-8") as fh:
            fh.write("def crunch(x):\n    return x\n")
        with open(os.path.join(self.root, "src", "app", "use.py"), "w", encoding="utf-8") as fh:
            fh.write("from app.engine import crunch\n\n\ndef u0():\n    return crunch(0)\n"
                     "\n\nclass K:\n    def u1(self):\n        return crunch(1)\n")
        for args in (["init", "-q"], ["add", "-A"],
                     ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"]):
            subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True)

    def test_setup_style_build_then_the_graph_answers_in_floor_identity(self):
        said = []
        res = B.build_now(self.root, on_progress=said.append)
        self.assertTrue(res.ok, res.error)
        self.assertEqual(B.INDEX_BUILT, B.probe_index(self.root))
        with open(os.path.join(B.index_dir(self.root), ".gitignore"), encoding="utf-8") as fh:
            self.assertIn("*", fh.read().split())               # it self-gitignores
        status = subprocess.run(["git", "status", "--porcelain"], cwd=self.root,
                                capture_output=True, text=True).stdout
        self.assertNotIn(".code-review-graph", status)
        res = _layer(self, self.root).callers("crunch")
        self.assertEqual("code-review-graph", res.backend, res.note)
        floor = _floor_callers(self.root, "crunch")
        self.assertEqual({"u0", "u1"}, {r.symbol for r in res.references})
        self.assertEqual(floor, {r.symbol for r in res.references},
                         "the graph and the floor name the same callers differently")

    def test_first_use_builds_in_the_background_then_the_graph_answers(self):
        first = _layer(self, self.root).callers("crunch")
        self.assertEqual("ast", first.backend)
        self.assertIn("is building its index", first.note)
        deadline = time.time() + 120
        while B.build_status(self.root).state == B.BUILDING and time.time() < deadline:
            time.sleep(0.5)
        st = B.build_status(self.root)
        with open(B.log_path(self.root), encoding="utf-8") as fh:
            log = fh.read()
        self.assertEqual(B.BUILT, st.state, log)
        later = _layer(self, self.root).callers("crunch")
        self.assertEqual("code-review-graph", later.backend, later.note)


if __name__ == "__main__":
    unittest.main()


# ======================================================================= the meaning index (scope+)
class _Emb:
    """A deterministic embedder with the batch path the real one has."""
    embedder_id = "fake-o13"
    dim = 3
    noise_floor = 0.0

    def __init__(self, interrupt_after=None):
        self.batches = 0
        self.interrupt_after = interrupt_after

    def __call__(self, text):
        return [1.0, float(len(text) % 7), 0.5]

    def embed_many(self, texts):
        self.batches += 1
        if self.interrupt_after is not None and self.batches > self.interrupt_after:
            raise KeyboardInterrupt
        return [self(t) for t in texts]


def _many_files(root, n):
    os.makedirs(os.path.join(root, "pkg"), exist_ok=True)
    for i in range(n):
        with open(os.path.join(root, "pkg", f"m{i:04d}.py"), "w", encoding="utf-8") as fh:
            fh.write(f"def f{i}(x):\n    return x + {i}\n\n\ndef g{i}():\n    return f{i}(1)\n")


class TheMeaningIndexHoldsOnABigRepo(unittest.TestCase):

    def setUp(self):
        from mokata.ide import search as SR
        self.SR = SR
        self.root = tempfile.mkdtemp(prefix="o13-sem-")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, ".mokata"))
        _many_files(self.root, 60)
        for p in (mock.patch.object(SR, "EMBED_BATCH", 20),):
            p.start()
            self.addCleanup(p.stop)

    def test_progress_is_counted_and_batched(self):
        said = []
        with mock.patch.object(self.SR, "PROGRESS_EVERY_S", 0.0):
            res = self.SR.build_index(self.root, _Emb(), on_progress=said.append)
        self.assertEqual(120, res["index"]["rows"])
        self.assertTrue(res["index"]["complete"])
        self.assertEqual(60, said[-1]["total"])
        self.assertEqual(60, said[-1]["done"])
        self.assertTrue(any(0 < p["done"] < 60 for p in said), "no progress between start and end")

    def test_ctrl_c_keeps_the_work_and_the_next_run_resumes(self):
        emb = _Emb(interrupt_after=3)                      # 3 batches of 20 chunks = 30 files
        with self.assertRaises(KeyboardInterrupt):
            self.SR.build_index(self.root, emb)
        meta = self.SR._read_meta(self.root)
        self.assertFalse(meta["complete"])
        kept = len(meta["files"])
        self.assertGreaterEqual(kept, 25, "the checkpoint on Ctrl+C lost the embedded files")
        again = _Emb()
        res = self.SR.build_index(self.root, again)
        self.assertTrue(res["index"]["complete"])
        self.assertEqual(120, res["index"]["rows"])
        self.assertEqual(2 * (60 - kept), res["embedded"], "the resumed run re-embedded done work")

    def test_a_checkpoint_never_pairs_rows_with_the_wrong_vectors(self):
        self.SR.build_index(self.root, _Emb())
        with mock.patch.object(self.SR, "CHECKPOINT_S", 0.0):
            with open(os.path.join(self.root, "pkg", "m0003.py"), "a", encoding="utf-8") as fh:
                fh.write("\n\ndef extra():\n    return 0\n")
            res = self.SR.build_index(self.root, _Emb(), checkpoint_s=0.0)
        rows, m = self.SR._matrix(self.root, res["index"])[:2]      # P36: (rows, map, …)
        emb = _Emb()
        for (rel, c), vec in zip(rows, m):
            with open(os.path.join(self.root, *rel.split("/")), encoding="utf-8") as fh:
                chunk = [x for x in self.SR.chunks_of(rel, fh.read()) if x["line"] == c["line"]][0]
            self.assertEqual([round(v, 4) for v in emb(chunk["text"])],
                             [round(float(v), 4) for v in vec], f"{rel}:{c['line']}")

    def test_a_big_first_build_runs_in_the_background_and_the_query_says_so(self):
        gate = threading.Event()

        class Slow(_Emb):
            def embed_many(self, texts):
                gate.wait(20)
                return super().embed_many(texts)
        emb = Slow()
        with mock.patch.object(self.SR, "EMBEDDER_FACTORY", lambda: emb), \
                mock.patch.object(self.SR, "INLINE_BUILD_MAX_FILES", 10):
            t0 = time.monotonic()
            first = self.SR.meaning(self.root, "f1")
            took = time.monotonic() - t0
            self.assertLess(took, 5.0, "the query waited for the build")
            self.assertEqual("building", first["state"])
            again = self.SR.meaning(self.root, "f1")
            self.assertEqual("building", again["state"], "a second query started a second build")
            gate.set()
            deadline = time.time() + 30
            while (self.SR.build_state(self.root) or {}).get("state") == "building" \
                    and time.time() < deadline:
                time.sleep(0.05)
            done = self.SR.meaning(self.root, "f1")
        self.assertEqual("ready", done["state"])
        self.assertTrue(done["results"])
        self.assertEqual(120, done["indexed_chunks"])

    def test_word_search_finds_ripgrep_installed_beside_mokata(self):
        # `mokata[full]` puts the ripgrep wheel's `rg` in mokata's own env, which uv tool / pipx
        # never put on PATH.
        from mokata import detect
        with mock.patch.object(detect, "find_command", lambda n: "/env/bin/rg" if n == "rg" else None):
            self.assertEqual("/env/bin/rg", self.SR.rg_path())


class APathologicalFileNeverTakesABigBuildDown(unittest.TestCase):
    """Measured on microsoft/TypeScript: one test file nested ~1000 deep raised RecursionError in
    the TS edge walk and aborted the meaning-index build at 51% of 26,804 files."""

    def test_the_ts_walk_is_not_recursive(self):
        from mokata.knowledge import ts_edges
        if not ts_edges.available():
            self.skipTest("graph-ts extra not installed here")
        src = "export function top() {\n  return " + "f(" * 3000 + "1" + ")" * 3000 + ";\n}\n"
        edges = ts_edges.parse_source(src, "deep.ts")
        self.assertIsNotNone(edges)
        self.assertEqual(["top"], [d[0] for d in edges.defs])
        self.assertEqual(3000, sum(1 for c in edges.calls if c[0] == "f"))

    def test_the_index_build_survives_one_bad_file(self):
        from mokata.ide import search as SR
        root = tempfile.mkdtemp(prefix="o13-bad-")
        self.addCleanup(shutil.rmtree, root, True)
        os.makedirs(os.path.join(root, ".mokata"))
        _many_files(root, 5)
        real = SR.chunks_of

        def chunks(rel, text):
            if rel.endswith("m0002.py"):
                raise RecursionError("maximum recursion depth exceeded")
            return real(rel, text)
        with mock.patch.object(SR, "chunks_of", chunks):
            res = SR.build_index(root, _Emb())
        self.assertTrue(res["index"]["complete"])
        self.assertEqual(5, len(res["index"]["files"]))
        self.assertEqual(["module"], [c["kind"] for c in res["index"]["files"]["pkg/m0002.py"]["chunks"]])


class GoIsInTheMeaningIndex(unittest.TestCase):
    """Measured on kubernetes (18k .go files): the meaning index held 34 chunks from 9 files."""

    def test_go_is_cut_at_its_top_level_declarations(self):
        from mokata.ide import search as SR
        src = ("package pods\n\ntype Pod struct {\n\tName string\n}\n\n"
               "func (p *Pod) Ready() bool {\n\treturn true\n}\n\nfunc NewPod() *Pod {\n"
               "\treturn &Pod{}\n}\n")
        got = [(c["name"], c["kind"], c["line"], c["end"]) for c in SR.chunks_of("pkg/pod.go", src)]
        self.assertEqual([("Pod", "class", 3, 6), ("Ready", "method", 7, 10),
                          ("NewPod", "function", 11, 13)], got)
        self.assertIn(".go", SR._SEMANTIC_EXTS)


class AnAmbiguousNameIsAnsweredByTheGraph(unittest.TestCase):
    """Measured on kubernetes: a bare name matched 12 graph nodes, and every query by name fell to
    the grep floor although the index was built."""

    def _client(self, replies):
        self.asked = []

        def call(_tool, params):
            self.asked.append(params["target"])
            return replies[params["target"]]
        return CodeReviewGraphClient(root="/r", call_tool=call,
                                     run_cli=lambda a: (0, "code-review-graph 2.3.9", ""))

    @staticmethod
    def _ok(*callers):
        return {"status": "ok", "results": [
            {"name": c, "qualified_name": f"/r/{c}.go::{c}", "file_path": f"/r/{c}.go",
             "line_start": 1, "call_site": {"line": 2}} for c in callers], "edges": []}

    def test_the_union_over_the_candidates(self):
        amb = {"status": "ambiguous", "summary": "'New' matches 2 node(s).",
               "candidates": [{"qualified_name": "/r/a.go::New"}, {"qualified_name": "/r/b.go::New"}]}
        c = self._client({"New": amb, "/r/a.go::New": self._ok("x"),
                          "/r/b.go::New": self._ok("y", "z")})
        rows = c.query("callers", "New", root="/r")
        self.assertEqual(["x.go", "y.go", "z.go"], sorted(r["path"] for r in rows))
        self.assertEqual(["New", "/r/a.go::New", "/r/b.go::New"], self.asked)

    def test_no_candidate_answering_is_not_a_zero(self):
        from mokata.knowledge.query import GraphNotIndexed
        amb = {"status": "ambiguous", "candidates": [{"qualified_name": "/r/a.go::New"}]}
        c = self._client({"New": amb, "/r/a.go::New": {"status": "not_found"}})
        with self.assertRaises(GraphNotIndexed):
            c.query("callers", "New", root="/r")
