"""0.0.28 lane I (B–I edge) — mokata's own MCP probes go through the process door.

`mcp_admin.handshake` (the `initialize` probe behind `mokata mcp status` / `doctor`),
`mcp_admin._probe_version` (version parity), `branch_protection`'s `gh` runner and the doctor's
plugin-pin probe each start a process tree (`uvx` → python, `gh` → its helpers). Before lane B's
door they used `Popen`/`subprocess.run`, which end only the leader: a grandchild could outlive the
probe and hold its pipes. Now each one is a `quiet_spawn` door call, and the handshake ends the
whole tree on every exit path. FIRING CONTROLS (mutants): put `_terminate` back to ending only the
leader (the grandchild survives); call `subprocess.run` again (the spy sees nothing).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import shutil
import sys
import tempfile
import time
import threading
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import branch_protection as BP  # noqa: E402
from mokata import mcp_admin as MA  # noqa: E402
from mokata import quiet_spawn as Q  # noqa: E402

#: A fake MCP server: starts a grandchild that would live forever (it records its pid), then either
#: answers `initialize` (connected) or exits without a word (died).
_SERVER = r'''
import json, os, subprocess, sys
d, mode = sys.argv[1], sys.argv[2]
g = subprocess.Popen([sys.executable, "-c", "import time\nwhile True: time.sleep(1)"])
open(os.path.join(d, "grandchild"), "w", encoding="utf-8").write(str(g.pid))
sys.stdin.readline()
if mode == "connected":
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}) + "\n")
    sys.stdout.flush()
    sys.stdin.readline()            # wait for EOF: the probe must end us, we never leave
else:
    sys.stderr.write("boom\n")
    os._exit(3)                     # the leader dies; its grandchild would live on
'''


#: The handshake's reply budget in these tests (the CLI default is 10 s).
HS_BUDGET_S = 5.0


def _alive(pid: int) -> bool:
    if os.name == "nt":             # signal 0 TERMINATES on Windows; the only callers are in a
        return False                # class skipped on nt, so this arm never runs a test
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class TestI1_ProbesUseTheDoor(unittest.TestCase):

    def _spy(self, name):
        real = getattr(Q, name)
        seen = []

        def spy(*a, **k):
            seen.append(a[0] if a else k.get("argv"))
            return real(*a, **k)
        p = mock.patch.object(Q, name, spy)
        p.start()
        self.addCleanup(p.stop)
        return seen

    def test_I1_handshake_spawns_through_spawn_jobbed(self):
        seen = self._spy("spawn_jobbed")
        res = MA.handshake(sys.executable, ["-c", "import sys; sys.exit(0)"])
        self.assertFalse(res.ok)
        self.assertEqual(1, len(seen))

    def test_I1_version_probe_and_gh_runner_use_run_jobbed(self):
        seen = self._spy("run_jobbed")
        status, ver, _ = MA._probe_version(sys.executable, ["-c", "print('mokata 9.9.9')", "--"],
                                           timeout=Q.END_BOUND_S)
        self.assertEqual(("ok", "9.9.9"), (status, ver))
        try:
            BP._default_gh_runner("repos/x/y")
        except OSError:
            pass                                   # no gh on this machine: the door was the call
        self.assertEqual(2, len(seen))
        self.assertEqual(["gh", "api", "repos/x/y"], list(seen[1]))


@unittest.skipIf(os.name == "nt", "POSIX liveness check; Windows trees end through the Job Object "
                                  "(lane B's own Windows cells)")
class TestI1_HandshakeEndsItsTree(unittest.TestCase):

    def _grandchild_after(self, mode):
        d = tempfile.mkdtemp(prefix="i1-hs-")
        self.addCleanup(shutil.rmtree, d, True)
        # The probe owes an answer within its own budget plus one door end; a probe that never
        # returns is a RED here, not a hung module (the close-before-end deadlock was exactly that).
        box = {}
        t = threading.Thread(target=lambda: box.update(
            res=MA.handshake(sys.executable, ["-c", _SERVER, d, mode], timeout=HS_BUDGET_S)),
            daemon=True)
        t.start()
        t.join(HS_BUDGET_S + Q.END_BOUND_S)
        gpath = os.path.join(d, "grandchild")
        if os.path.exists(gpath):
            with open(gpath, encoding="utf-8") as fh:
                pid = int(fh.read())
            self.addCleanup(lambda: _alive(pid) and os.kill(pid, 9))
        self.assertFalse(t.is_alive(), "handshake did not return within its own budget plus one "
                                       "door end — the probe hangs")
        res = box["res"]
        owed_by = time.monotonic() + Q.END_BOUND_S      # the door's own bound for an end
        while _alive(pid) and time.monotonic() < owed_by:
            time.sleep(0.02)
        return res, _alive(pid)

    def test_I1_a_connected_probe_leaves_nothing_running(self):
        res, alive = self._grandchild_after("connected")
        self.assertTrue(res.ok, res)
        self.assertFalse(alive, "the server's grandchild outlived the handshake")

    def test_I1_a_server_that_died_leaves_nothing_running(self):
        # The grandchild holds the server's stdout, so the probe sees no EOF and answers at its own
        # bound (`timeout`). Before the fix the probe then blocked forever closing that stream.
        res, alive = self._grandchild_after("died")
        self.assertFalse(res.ok)
        self.assertEqual("timeout", res.code, res)
        self.assertFalse(alive, "the dead server's grandchild outlived the handshake")


if __name__ == "__main__":
    unittest.main()
