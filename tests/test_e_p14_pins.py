"""0.0.29 lane E `IDE-UI-PIN-GAPS` - the two P14 (`ID-NEXT-STEP-HINTS`) rows that had no direct test.

Business rules:
  * AC5 - the hints door is a READ and only a read. Through the real app: `/api/hints` is mounted
    for GET (and Starlette's HEAD twin) and for nothing else, a POST is refused with 405 and runs
    nothing, and every route the feature registers is a `json` read - a hint can never be an action
    (every command a hint offers asks for its own consent when the person runs it).
  * "runstate pushed" - a test run, a review verdict, a spec or a finish recorded in a TERMINAL emits
    no event, so the IDE's watcher must notice the run-state record move and push `runstate` to the
    open pages, naming the run (or none, for the shared progress log: "any run"). Without that push
    the Activity card keeps telling the person to re-run something they already re-ran. Nothing
    moved -> nothing pushed.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
import types
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

try:
    from starlette.testclient import TestClient
    from mokata.ide import server as SRV
    from mokata.ide.server import AppState, build_app
    NO_IDE = None
except ImportError as exc:                       # pragma: no cover - depends on the extras
    NO_IDE = f"the [ide] extra is not installed ({exc})"

from mokata.progress_events import PROGRESS_EVENTS_FILENAME
from mokata.tdd_state import state_dir

TOKEN, PORT = "tok", 9999
BASE = f"http://127.0.0.1:{PORT}"
ORIGIN = {"Origin": BASE}
RUN = "R-e-pin"


def _repo():
    d = tempfile.mkdtemp(prefix="mk-e-p14-")
    subprocess.run(["git", "init", "-q", d], check=True, stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return d


@unittest.skipIf(NO_IDE, NO_IDE or "")
class TheHintsDoorIsAReadNeverAnAction(unittest.TestCase):
    """AC5."""

    def setUp(self):
        self.d = _repo()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.app = build_app(AppState(self.d, TOKEN, PORT, None, False))
        self.c = TestClient(self.app, base_url=BASE, follow_redirects=False)
        self.c.get(f"/pair?t={TOKEN}")

    def test_the_hints_path_is_mounted_for_get_and_nothing_else(self):
        methods = {}
        for r in self.app.routes:
            if getattr(r, "path", "").startswith("/api/hints"):
                methods.setdefault(r.path, set()).update(r.methods or ())
        self.assertEqual({"/api/hints": {"GET", "HEAD"}}, methods)

    def test_a_post_to_the_hints_path_is_refused_and_a_get_is_answered(self):
        self.assertEqual(200, self.c.get("/api/hints", params={"session": "s1"}).status_code)
        for verb in ("post", "put", "patch", "delete"):
            r = getattr(self.c, verb)("/api/hints", headers=ORIGIN)
            self.assertEqual(405, r.status_code, verb)

    def test_every_route_the_hints_feature_registers_is_a_json_read(self):
        from mokata.ide.features import hints as F
        seen = []

        class Reg:
            def register(self, path, fn, kind, feature):
                seen.append((path, kind, feature))

        F.routes(Reg(), types.SimpleNamespace(state=None))
        self.assertEqual([("/api/hints", "json", "hints")], seen)


@unittest.skipIf(NO_IDE, NO_IDE or "")
class TheWatcherPushesRunstateWhenARunsRecordMoves(unittest.TestCase):
    """The watcher runs for real (real `Tails`, real `_fingerprint`, the real `_runstate_fp`); the
    test controls WHEN the record moves and WHEN the loop ends, so no clock decides anything. The
    tick delay is set to zero: a tick is a loop turn, not a wait."""

    def setUp(self):
        self.d = _repo()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        os.makedirs(state_dir(self.d), exist_ok=True)
        self.pushed = []
        pushed = self.pushed

        class Bus:
            async def publish(self, evt):
                pushed.append(evt)

        self.state = types.SimpleNamespace(demo=False, root=self.d, clients=1, watcher=object(),
                                           self_seqs=set(), bus=Bus())
        p = mock.patch("mokata.ide.bus.TAIL_SECS", 0)
        p.start()
        self.addCleanup(p.stop)

    def _drive(self, move, ticks):
        """Run `_watch` until `ticks` run-state reads have happened; `move(n)` is called at the start
        of read number n (1 = the priming read) and may write the record. Returns the runstate pushes."""
        real = SRV._runstate_fp
        calls = {"n": 0}
        state = self.state

        def fp(root):
            calls["n"] += 1
            move(calls["n"])
            if calls["n"] >= ticks:
                state.clients = 0                 # the page left: the watcher exits at its next turn
            return real(root)

        real_all = SRV._fingerprint
        turns = {"n": 0}

        def every_turn(root):
            # a loop turn that never reads the run-state records (a watcher that stopped looking) must
            # still end, by the turn count, never by a wall clock
            turns["n"] += 1
            if turns["n"] > ticks + 2:
                state.clients = 0
            return real_all(root)

        with mock.patch.object(SRV, "_runstate_fp", fp), mock.patch.object(SRV, "_fingerprint", every_turn):
            asyncio.run(SRV._watch(self.state))
        return [e for e in self.pushed if e.get("kind") == "runstate"]

    def _write(self, name):
        with open(os.path.join(state_dir(self.d), name), "w", encoding="utf-8") as fh:
            fh.write("{}")

    def test_a_test_run_recorded_in_a_terminal_is_pushed_naming_its_run(self):
        got = self._drive(lambda n: self._write(f"test_runs__{RUN}.json") if n == 2 else None, ticks=3)
        self.assertEqual([{"kind": "runstate", "runs": [RUN]}], got)

    def test_a_progress_log_append_is_pushed_as_any_run(self):
        got = self._drive(lambda n: self._write(PROGRESS_EVENTS_FILENAME) if n == 2 else None, ticks=3)
        self.assertEqual([{"kind": "runstate", "runs": []}], got)

    def test_nothing_moved_pushes_nothing(self):
        self._write(f"test_runs__{RUN}.json")
        got = self._drive(lambda n: None, ticks=4)
        self.assertEqual([], got)


if __name__ == "__main__":
    unittest.main()
