"""O4 (0.0.26) — `--ide-bridge` costs nothing while the IDE is closed, on every OS.

The problem (0.0.25 cut, windows-latest): Windows retries a refused localhost connect for ~1 s, and the
`--ide-bridge` http hooks are connected by Claude Code itself, so every PostToolUse/Stop/SessionEnd
hook cost ~1 s with the IDE closed. Nothing in mokata could check first.

Business rules — what must be true for the user:
  * With the IDE closed, a bridge hook returns in well under 100 ms and NEVER attempts a connect:
    "closed" is decided from the IDE's own record (`temp_local/ide/server.json`, written on start,
    removed on a clean exit) and a live-pid probe — no socket.
  * A record left behind by a crash or a hard kill (its pid is dead) reads as closed; so does a
    SAMPLE (demo) window, which takes no hook events.
  * With the IDE open, the hook's payload reaches the open page (the same bus, the same hook token —
    least privilege — as the http hook), at the server's actual port.
  * `mokata setup claude --ide-bridge` on Windows wires the command form (`python -m
    mokata.ide.bridge_client --root <repo>`) into the machine-local settings — no URL, no token in
    the file; merge-safe, idempotent, removed by unsetup. POSIX keeps the http hook (no spawn).
  * The check is stdlib-only and importable on its own, so the in-repo observe emitter (O3) can
    call it from a hook it already runs.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import functools
import http.client
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import harness_setup as HS
from mokata.ide import bridge_client as BC
from mokata.ide import bus as B
from mokata.quiet_spawn import hidden

from test_id_s3_cli_ide_bridge import _SSE, _cc_post, _repo, _start, _stop

SRC = str(Path(__file__).resolve().parents[1] / "src")


def _no_socket(*_a, **_k):
    raise AssertionError("a connect was attempted with no live IDE")


def _dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"], stdin=subprocess.DEVNULL, **hidden())
    p.wait()
    return p.pid


def _write_record(d: str, **rec) -> None:
    path = os.path.join(d, ".mokata", "temp_local", "ide", "server.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rec, fh)


class ClosedIdeNoConnect(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.d = os.path.realpath(self._t.name)
        B.ensure_bridge(self.d)                     # the bridge is set up; only the IDE is closed
        self.body = json.dumps(_cc_post(self.d)).encode("utf-8")
        for target in ("http.client.HTTPConnection", "socket.socket", "socket.create_connection"):
            p = mock.patch(target, side_effect=_no_socket)
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self._t.cleanup()

    def _fast_and_false(self):
        xs = []
        for _ in range(20):
            t0 = time.perf_counter()
            self.assertFalse(BC.forward(self.d, self.body))
            xs.append((time.perf_counter() - t0) * 1000)
        self.assertLess(max(xs), 100.0, xs)

    def test_no_record_returns_fast_without_a_connect(self):
        self.assertIsNone(BC.live_ide(self.d))
        self._fast_and_false()

    def test_a_stale_record_with_a_dead_pid_is_closed(self):
        _write_record(self.d, port=23457, pid=_dead_pid(), token="t", demo=False)
        self.assertIsNone(BC.live_ide(self.d))
        self._fast_and_false()

    def test_a_sample_window_takes_no_hook_events(self):
        _write_record(self.d, port=23457, pid=os.getpid(), token="t", demo=True)
        self.assertIsNone(BC.live_ide(self.d))
        self._fast_and_false()

    def test_a_live_pid_is_live(self):
        _write_record(self.d, port=23456, pid=os.getpid(), token="t", demo=False)
        self.assertEqual({"port": 23456, "pid": os.getpid()}, BC.live_ide(self.d))

    def test_the_check_is_stdlib_only(self):
        src = Path(BC.__file__).read_text(encoding="utf-8")
        imports = [ln.strip() for ln in src.splitlines() if ln.startswith(("import ", "from "))]
        self.assertEqual(["from __future__ import annotations", "import http.client",
                          "import json", "import os", "import sys",
                          "from typing import Any, Dict, Optional"], imports)


class TheCommandHookFromAClosedIde(unittest.TestCase):
    def test_the_hook_process_exits_0_silent_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as t:
            d = os.path.realpath(t)
            # the bridge is made by `mokata setup claude`, i.e. in a repo mokata is set up in
            # (0.0.26 P29: a repo with no `.mokata/` keeps IDE records in the user profile)
            os.makedirs(os.path.join(d, ".mokata"))
            B.ensure_bridge(d)
            env = dict(os.environ, PYTHONPATH=SRC)
            r = subprocess.run([sys.executable, "-m", "mokata.ide.bridge_client", "--root", d],
                               input=json.dumps(_cc_post(d)).encode("utf-8"), env=env,
                               capture_output=True, timeout=30, **hidden())
            self.assertEqual((0, b"", b""), (r.returncode, r.stdout, r.stderr))
            self.assertEqual(["bridge.json"], os.listdir(os.path.join(d, ".mokata", "temp_local",
                                                                      "ide")))


class LiveIdeReceivesTheForwardedHook(unittest.TestCase):
    """A real `mokata ide` (child process) and its real SSE stream."""

    @classmethod
    def setUpClass(cls):
        cls._t = tempfile.TemporaryDirectory()
        cls.d = os.path.realpath(cls._t.name)
        _repo(cls.d)
        cls.bridge = B.ensure_bridge(cls.d)
        cls.rec = _start(cls.d)

    @classmethod
    def tearDownClass(cls):
        _stop(cls.d)
        cls._t.cleanup()

    def test_live_record_connects_and_the_edit_reaches_the_page(self):
        self.assertEqual(int(self.rec["port"]), BC.live_ide(self.d)["port"])
        sse = _SSE(int(self.rec["port"]), self.rec["token"])
        try:
            self.assertIsNotNone(sse.wait(lambda ev, _o: ev == "focus", 5.0), "stream not up")
            seen = []
            real = http.client.HTTPConnection

            def spy(*a, **k):
                seen.append((a, k))
                return real(*a, **k)
            with mock.patch.object(BC.http.client, "HTTPConnection", side_effect=spy):
                ok = BC.forward(self.d, json.dumps(_cc_post(self.d, "src/o4.py")).encode("utf-8"))
            self.assertTrue(ok)
            self.assertEqual(1, len(seen))
            self.assertEqual(BC.CONNECT_SECS, seen[0][1]["timeout"])
            got = sse.wait(lambda ev, o: ev == "bus" and o and o.get("path") == "src/o4.py", 2.0)
            self.assertIsNotNone(got, "the forwarded edit never reached the page")
        finally:
            sse.close()

    def test_a_wrong_token_is_refused(self):
        with mock.patch.object(BC, "_hook_token", lambda _r: "wrong"):
            self.assertFalse(BC.forward(self.d, json.dumps(_cc_post(self.d)).encode("utf-8")))


class SetupWiresTheCommandFormOnWindows(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        base = Path(self._t.name)
        self.root = base / "repo"
        self.root.mkdir()
        self.home = base / "home"
        self.home.mkdir()
        p = mock.patch.object(HS, "ide_bridge_hooks",
                              functools.partial(HS.ide_bridge_hooks, windows=True))
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        self._t.cleanup()

    def _setup(self, **kw):
        HS.apply_setup(HS.plan_setup("claude", root=str(self.root), home=str(self.home), **kw),
                       assume_yes=True, out=lambda *_a, **_k: None)

    def _local(self):
        p = self.root / ".claude" / "settings.local.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def test_command_hooks_check_before_connecting_and_carry_no_token(self):
        self._setup(ide_bridge=True)
        rec = B.read_bridge(str(self.root.resolve()))
        hooks = self._local()["hooks"]
        self.assertEqual({"PostToolUse", "Stop", "SessionEnd"}, set(hooks))
        self.assertEqual(HS.HOOK_DIRTY_MATCHER, hooks["PostToolUse"][0]["matcher"])
        for event, groups in hooks.items():
            (h,) = groups[0]["hooks"]
            self.assertEqual("command", h["type"], event)
            self.assertEqual(sys.executable, h["command"])
            self.assertEqual(["-m", "mokata.ide.bridge_client", "--root",
                              str(self.root.resolve())], h["args"])
            self.assertNotIn("onFailure", h)
        self.assertNotIn(rec["token"], json.dumps(self._local()))

    def test_idempotent_merge_safe_and_reversible(self):
        local = self.root / ".claude" / "settings.local.json"
        local.parent.mkdir(parents=True, exist_ok=True)
        mine = {"type": "command", "command": "echo mine"}
        local.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [mine]}]}}), encoding="utf-8")
        self._setup(ide_bridge=True)
        self._setup(ide_bridge=True)
        self.assertEqual(2, len(self._local()["hooks"]["Stop"]))
        HS.apply_unsetup(HS.plan_unsetup("claude", root=str(self.root), home=str(self.home)))
        self.assertEqual({"hooks": {"Stop": [{"hooks": [mine]}]}}, self._local())

    def test_posix_keeps_the_http_hook(self):
        wiring = HS.ide_bridge_hooks.func("http://127.0.0.1:1/api/bus", "tok", root="/r",
                                          windows=False)
        self.assertEqual("http", wiring["Stop"][0]["hooks"][0]["type"])


if __name__ == "__main__":
    unittest.main()
