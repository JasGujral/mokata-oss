"""0.0.29 F (W5-HOOK-BUDGET-MARGIN-THIN, matrix #1) — no hook spawns a process to read the process table.

What must be true for the user:
  * Every prompt they type is answered by a hook that reads the process table IN-PROCESS on every OS
    (Linux `/proc`, macOS libproc + sysctl, Windows Toolhelp). On a Mac every prompt used to spawn
    `ps -A` (~25 ms of a 150 ms budget, twice on a session's first prompt) — graded by nothing but the
    clock, which a Linux CI leg never saw.
  * There is NO `ps` fallback on macOS: if the in-process read fails, the session map is simply not
    written (the MCP side then resolves as it always did without it), never a quiet spawn.

Graded by STATE, not time: every wired hook runs in-process with every process start recorded.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

import mokata_emit
from mokata import perf
from mokata import session_ancestry as SA
from mokata.init import init_repo

#: Programs that read the process table. None may be started by a hook.
TABLE_READERS = frozenset({"ps", "wmic", "tasklist", "powershell", "pwsh", "pgrep", "top"})


def _prog(argv) -> str:
    first = argv if isinstance(argv, (str, bytes)) else (list(argv)[:1] or [""])[0]
    name = os.path.basename(os.fsdecode(first).replace("\\", "/")).lower()
    return name[:-4] if name.endswith(".exe") else name


class _Recorder:
    """Every process start in this process: `subprocess.Popen` and `os.posix_spawn[p]` (what the
    front door's detached spawns use). The detached warmer/refresher/drainer door
    (`mokata_emit.spawn_detached`) is recorded and NOT started — it is the one allowed spawn."""

    def __init__(self):
        self.started, self.detached = [], []

    def __enter__(self):
        rec = self
        real_init = subprocess.Popen.__init__

        def popen_init(self_, args, *a, **k):
            rec.started.append(args if isinstance(args, (str, bytes)) else list(args))
            return real_init(self_, args, *a, **k)
        self._p = [mock.patch.object(subprocess.Popen, "__init__", popen_init),
                   mock.patch.object(mokata_emit, "spawn_detached",
                                     lambda argv, **k: rec.detached.append(list(argv)) or True)]
        for name in ("posix_spawn", "posix_spawnp"):
            real = getattr(os, name, None)
            if real is not None:
                def spawn(path, argv, *a, _real=real, **k):
                    rec.started.append(list(argv))
                    return _real(path, argv, *a, **k)
                self._p.append(mock.patch.object(os, name, spawn))
        for p in self._p:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._p):
            p.stop()

    def readers(self):
        return [a for a in self.started if _prog(a) in TABLE_READERS]


def _silent(*_a, **_k):
    pass


class TheHookSpawnsNoProcessTable(unittest.TestCase):
    """force — every wired hook (`perf.HOOK_KINDS`), through the front door as Claude Code runs it,
    in a repo bound to a run, with a FRESH session id (the first-prompt path, where the index row
    is opened at the prompt and names its Claude process) and again with that id known."""

    @classmethod
    def setUpClass(cls):
        from mokata import run_intent as RI
        from mokata.config import Surface
        from mokata.govern.resume import PipelineCheckpoint
        cls._t = tempfile.TemporaryDirectory()
        d = os.path.join(os.path.realpath(cls._t.name), "bound")
        os.makedirs(os.path.join(d, "src"))
        subprocess.run(["git", "init", "-q", d], check=True, capture_output=True,
                       stdin=subprocess.DEVNULL)
        with open(os.path.join(d, "src", "a.py"), "w", encoding="utf-8") as fh:
            fh.write("x = 1\n")
        init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
        PipelineCheckpoint(Surface.load(d).state, "r1").ensure_registered()
        RI.bind_registered(d, "S1", "r1")
        cls.d = d
        cls.spool = os.path.join(os.path.realpath(cls._t.name), "spool")

    @classmethod
    def tearDownClass(cls):
        cls._t.cleanup()

    def _payloads(self, sid):
        from test_hb_hook_budget import _payloads
        out = _payloads(self.d)
        for body in out.values():
            body["session_id"] = sid
        return out

    def _run(self, sub, body):
        env = {"MOKATA_HOOK_SPOOL": self.spool, "CLAUDE_PROJECT_DIR": self.d}
        with mock.patch.dict(os.environ, env), \
                mock.patch("sys.stdin", io.StringIO(json.dumps(body))), \
                mock.patch("sys.stdout", io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            try:
                mokata_emit.main([sub, "--via-plugin"])
            except SystemExit:
                pass
            if os.path.isdir(self.spool):
                mokata_emit.drain(self.spool)     # the drained work runs here too, recorded

    def test_no_wired_hook_starts_a_process_table_reader(self):
        payloads = {}
        for sid in (f"S-fresh-{os.getpid()}", f"S-fresh-{os.getpid()}"):
            payloads = self._payloads(sid)
            for sub in perf.HOOK_KINDS:
                with self.subTest(sub=sub, sid=sid), _Recorder() as rec:
                    self._run(sub, payloads[sub])
                    self.assertEqual([], rec.readers(), f"{sub} spawned a process-table reader")
                    for argv in rec.detached:        # the one allowed door: our own warmer/refresher
                        self.assertTrue(any("mokata_emit" in str(a) for a in argv), argv)


class OneSnapshotPerPrompt(TheHookSpawnsNoProcessTable):
    """force — §3b: the prompt hook reads the process table ONCE and hands that snapshot to both
    the session index row and the ancestry map (a fresh session id: the row is opened at the
    prompt, so both consumers run). Before, each took its own (`ps` twice on a Mac)."""

    def test_no_wired_hook_starts_a_process_table_reader(self):
        pass                                   # the parent's cell runs once, in its own class

    def test_the_prompt_hook_takes_one_snapshot(self):
        calls = []
        real = SA.system_lookup

        def counted():
            calls.append(1)
            return real()
        sid = f"S-one-{os.getpid()}"
        with mock.patch.object(SA, "system_lookup", counted):
            self._run("user-prompt-submit", self._payloads(sid)["user-prompt-submit"])
        self.assertEqual(1, len(calls), "the prompt hook read the process table more than once")


class NoPsFallbackOnDarwin(unittest.TestCase):
    """force — with the platform taken as macOS and the in-process read failing, the lookup is
    None and nothing is started, on EVERY OS (so a `ps` fallback reds on Linux and Windows too)."""

    def test_libproc_failing_is_no_chain_and_no_spawn(self):
        with mock.patch.object(sys, "platform", "darwin"), \
                mock.patch.object(SA, "_libproc_lookup", return_value=None), _Recorder() as rec:
            self.assertIsNone(SA.system_lookup())
            self.assertFalse(SA.record_session(tempfile.gettempdir(), "S1"))
        self.assertEqual([], rec.started)

    def test_libproc_raising_is_no_chain_and_no_spawn(self):
        with mock.patch.object(sys, "platform", "darwin"), \
                mock.patch.object(SA, "_libproc_lookup", side_effect=OSError("no libproc")), \
                _Recorder() as rec:
            self.assertIsNone(SA.system_lookup())
        self.assertEqual([], rec.started)

    def test_ps_survives_only_for_a_non_darwin_bsd(self):
        """guard — the BSD rung is untouched (not a hook platform mokata ships CI for)."""
        with mock.patch.object(sys, "platform", "freebsd14"), \
                mock.patch.object(SA, "_ps_lookup", return_value="ps") as ps:
            self.assertEqual("ps", SA.system_lookup())
        ps.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
