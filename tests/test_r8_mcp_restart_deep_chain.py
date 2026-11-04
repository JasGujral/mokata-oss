"""0.0.27 R8 — MCP-RESTART-DEEP-CHAIN: a restarted MCP server keeps its session through DEEP chains.

Disclosed in 0.0.26: "A restarted MCP server is recognised only when Claude sits within two launcher
processes of its hooks; a deeper chain (e.g. the Windows `py.exe` stub) refuses as ambiguous."

Real Windows chains are deeper than two launchers on BOTH sides:
  hook:  claude.exe -> Git\\bin\\bash.exe (stub) -> usr\\bin\\bash.exe -> bash.exe (MSYS exec stub of the
         sh shim) -> python.exe (uv venv trampoline) -> python.exe (the hook)
  mcp:   claude.exe -> uvx.exe -> uv.exe -> python.exe (trampoline) -> python.exe (the server)
so a window's MCP server, restarted after a dropped connection or a `/mcp` reconnect, had no session
and, with two runs in the repo, refused as AMBIGUOUS.

The rule now: the walk runs to the root (no hop count); every process between a hook / server and
its Claude must be a NAMED launcher, however many; past HOOK_LAUNCH_DEPTH the Claude must also be
NAMED as a Claude (`claude` / `node`). Pid + start token must match on both sides. Anything else
refuses — a refusal is always better than binding one window's run to another window.

Every test runs on a planted process table, so Windows is modelled on Linux.
"""

import time
import unittest

import _support  # noqa: F401
from test_f1_session_bind_by_ancestor import P, _Base, _pipeline, _repo
from test_o18_mcp_restart_keeps_its_session import NamedMachine

from mokata import session_ancestry as SA
from mokata.run_resolver import BASIS_AMBIGUOUS, BASIS_BOUND

# The launcher shims a real install puts in the way, by the name the OS reports.
SHIMS = {
    "py": "py.exe", "uv": "uv.exe", "uvx": "uvx.exe", "cmd": "cmd.exe", "pwsh": "pwsh.exe",
    "bash": "bash.exe", "python": "python.exe", "cscript": "cscript.exe", "sh": "/bin/sh",
    "comm-truncated-shim": "mokata-hook-lau", "env": "env",
}


class Desk:
    """A Windows desktop: explorer -> Windows Terminal -> two tabs, each `pwsh -> claude.exe`."""

    def __init__(self):
        self.procs = {4000: P(1, "t4000", 10.0, "explorer.exe"),
                      4010: P(4000, "t4010", 20.0, "WindowsTerminal.exe")}
        self.m = NamedMachine(self.procs)
        self._next = 6000

    def pid(self):
        self._next += 1
        return self._next

    def claude(self, born, name="claude.exe"):
        tab, cl = self.pid(), self.pid()
        self.m.procs[tab] = P(4010, f"t{tab}", born - 5.0, "pwsh.exe")
        self.m.procs[cl] = P(tab, f"t{cl}", born, name)
        return cl

    def chain(self, top, names, born):
        """Spawn `names` (outermost first) under `top`; returns the innermost pid."""
        cur = top
        for i, n in enumerate(names):
            p = self.pid()
            self.m.procs[p] = P(cur, f"t{p}", born + 0.01 * i, n)
            cur = p
        return cur

    def hook(self, root, claude, sid, ts, names):
        """A hook through the launcher `names`, ending in the hook's own python.exe; then all exit."""
        before = set(self.m.procs)
        leaf = self.chain(claude, list(names) + ["python.exe"], ts - 0.5)
        try:
            return SA.record_session(root, sid, lookup=self.m.lookup, pid=leaf, now=ts)
        finally:
            for p in set(self.m.procs) - before:
                del self.m.procs[p]

    def mcp(self, claude, names, born):
        return self.chain(claude, list(names) + ["python.exe"], born)

    def kill_below(self, claude):
        """The MCP server (everything under `claude`) dies: a dropped connection."""
        def under(p):
            while p in self.m.procs and p > 1:
                p = self.m.procs[p].ppid
                if p == claude:
                    return True
            return False
        for p in [p for p in self.m.procs if p != claude and under(p)]:
            del self.m.procs[p]


GIT_BASH_HOOK = ["bash.exe", "bash.exe", "bash.exe", "python.exe"]       # 4 launchers + hook
UVX_MCP = ["uvx.exe", "uv.exe", "python.exe"]                            # 3 launchers + server
PY_CMD_HOOK = ["pwsh.exe", "cmd.exe", "py.exe"]                          # the .cmd ladder via py.exe
PY_CMD_MCP = ["cmd.exe", "py.exe"]
PYENV_WIN_HOOK = ["bash.exe", "bash.exe", "cmd.exe", "python.exe"]       # python.bat shim + exec.bat
PYENV_WIN_VBS_HOOK = ["bash.exe", "cmd.exe", "cscript.exe", "python.exe"]  # older vbs-based shim
PYENV_WIN_MCP = ["uvx.exe", "uv.exe", "cmd.exe", "python.exe"]


class _Two(_Base):
    def _two(self):
        d = _repo()
        _pipeline(d, "runA", "S_A")
        _pipeline(d, "runB", "S_B")
        return d


class ADeepChainIsRecognised(_Two):
    """The user-visible rule: after the MCP server restarts, a window's spec/approve writes still land
    on THAT window's run, however many launchers Windows puts in the way."""

    def _restart_keeps(self, hook_names, mcp_names):
        d, w = self._two(), Desk()
        cl = w.claude(born=100.0)
        w.mcp(cl, mcp_names, born=101.0)
        self.assertTrue(w.hook(d, cl, "S_A", ts=300.0, names=hook_names))
        w.kill_below(cl)
        srv = w.mcp(cl, mcp_names, born=5000.0)            # restarted, hours after the last stamp
        res = w.m.mcp(d, srv)
        self.assertEqual(("runA", BASIS_BOUND), (res.run_id, res.basis),
                         f"hook {hook_names} / mcp {mcp_names}")

    def test_git_bash_hook_and_uvx_server(self):
        self._restart_keeps(GIT_BASH_HOOK, UVX_MCP)

    def test_the_cmd_ladder_through_py_exe(self):
        self._restart_keeps(PY_CMD_HOOK, PY_CMD_MCP)
        self._restart_keeps(PY_CMD_HOOK, UVX_MCP)

    def test_pyenv_win_shims(self):
        self._restart_keeps(PYENV_WIN_HOOK, PYENV_WIN_MCP)
        self._restart_keeps(PYENV_WIN_VBS_HOOK, PYENV_WIN_MCP)

    def test_every_depth_from_three_to_six_through_the_known_shims(self):
        pool = list(SHIMS.values())
        for depth in range(3, 7):
            for start in range(len(pool)):
                names = [pool[(start + i) % len(pool)] for i in range(depth)]
                with self.subTest(depth=depth, names=names):
                    self._restart_keeps(names, names)

    def test_claude_running_under_node(self):
        d, w = self._two(), Desk()
        cl = w.claude(born=100.0, name="node.exe")
        w.hook(d, cl, "S_A", ts=300.0, names=GIT_BASH_HOOK)
        srv = w.mcp(cl, UVX_MCP, born=5000.0)
        self.assertEqual("runA", w.m.mcp(d, srv).run_id)


class TwoWindowsAreNeverCrossBound(_Two):

    def test_two_claudes_with_interleaved_shims_each_resolve_their_own(self):
        d, w = self._two(), Desk()
        a, b = w.claude(born=100.0), w.claude(born=101.0)
        # interleave the spawns, so the pids of A's and B's shims alternate
        sa = w.mcp(a, ["uvx.exe"], 102.0)
        sb = w.mcp(b, ["uvx.exe"], 102.1)
        w.hook(d, a, "S_A", ts=300.0, names=GIT_BASH_HOOK)
        w.hook(d, b, "S_B", ts=300.5, names=PYENV_WIN_HOOK)
        self.assertEqual("runA", w.m.mcp(d, sa).run_id)
        self.assertEqual("runB", w.m.mcp(d, sb).run_id)
        for cl in (a, b):
            w.kill_below(cl)
        ra = w.mcp(a, UVX_MCP, born=4000.0)
        rb = w.mcp(b, PYENV_WIN_MCP, born=4000.1)
        self.assertEqual(("runA", "runB"), (w.m.mcp(d, ra).run_id, w.m.mcp(d, rb).run_id))

    def test_a_window_whose_hooks_never_wrote_gets_nothing_from_the_other(self):
        d, w = self._two(), Desk()
        a, b = w.claude(born=100.0), w.claude(born=101.0)
        w.hook(d, a, "S_A", ts=300.0, names=GIT_BASH_HOOK)
        sb = w.mcp(b, UVX_MCP, born=4000.0)
        self.assertEqual(BASIS_AMBIGUOUS, w.m.mcp(d, sb).basis)
        self.assertIsNone(SA.session_for_process(d, lookup=w.m.lookup, pid=sb))

    def test_a_nested_claude_deep_in_the_chain_is_never_walked_through(self):
        """Claude A's Bash tool runs `claude -p` (B) through a deep chain; B's own hooks never
        wrote. B's MCP must not get A's session: a Claude is never a launcher."""
        d, w = self._two(), Desk()
        a = w.claude(born=100.0)
        w.hook(d, a, "S_A", ts=300.0, names=GIT_BASH_HOOK)
        nested = w.chain(a, ["bash.exe", "bash.exe", "claude.exe"], 400.0)
        srv = w.mcp(nested, UVX_MCP, born=401.0)
        self.assertIsNone(SA.session_for_process(d, lookup=w.m.lookup, pid=srv))

    def test_a_nested_claudes_deep_write_never_repoints_its_parent(self):
        d, w = self._two(), Desk()
        a = w.claude(born=100.0)
        sa = w.mcp(a, UVX_MCP, born=101.0)
        w.hook(d, a, "S_A", ts=300.0, names=GIT_BASH_HOOK)
        nested = w.chain(a, ["bash.exe", "bash.exe", "bash.exe"], 400.0)
        nb = w.pid()
        w.m.procs[nb] = P(nested, f"t{nb}", 400.5, "claude.exe")
        w.hook(d, nb, "S_B", ts=401.0, names=GIT_BASH_HOOK)
        self.assertEqual("runA", w.m.mcp(d, sa).run_id)


class EverythingElseStillRefuses(_Two):

    def test_a_reused_claude_pid_with_a_new_start_time_is_refused(self):
        d, w = self._two(), Desk()
        cl = w.claude(born=100.0)
        w.hook(d, cl, "S_A", ts=300.0, names=GIT_BASH_HOOK)
        tab = w.m.procs[cl].ppid
        del w.m.procs[cl]
        w.kill_below(cl)
        w.m.procs[cl] = P(tab, f"t{cl}-reborn", 900.0, "claude.exe")   # same pid, new process
        srv = w.mcp(cl, UVX_MCP, born=901.0)
        self.assertEqual(BASIS_AMBIGUOUS, w.m.mcp(d, srv).basis)

    def test_a_reused_launcher_pid_younger_than_its_child_ends_the_walk(self):
        d, w = self._two(), Desk()
        cl = w.claude(born=100.0)
        w.hook(d, cl, "S_A", ts=300.0, names=GIT_BASH_HOOK)
        srv = w.mcp(cl, UVX_MCP, born=5000.0)
        uv = w.m.procs[srv].ppid                         # recycle a launcher pid mid-chain
        w.m.procs[uv] = w.m.procs[uv]._replace(started=9000.0, token="reborn")
        self.assertEqual(BASIS_AMBIGUOUS, w.m.mcp(d, srv).basis)

    def test_a_chain_with_no_claude_at_all_is_refused(self):
        """Every ancestor a launcher or an unrecognised host, past the launch depth: nobody is
        flagged, and a later server under the same host gets nothing."""
        d, w = self._two(), Desk()
        host = w.chain(4010, ["Code.exe"], 50.0)
        w.hook(d, host, "S_A", ts=300.0, names=GIT_BASH_HOOK)
        pids = SA._store(d).read(SA.MAP_KEY)["pids"]
        self.assertFalse(any(e.get("claude") for e in pids.values()), pids)
        srv = w.mcp(host, UVX_MCP, born=5000.0)
        self.assertIsNone(SA.session_for_process(d, lookup=w.m.lookup, pid=srv))

    def test_launchers_all_the_way_to_the_root_flag_nothing(self):
        d, w = self._two(), Desk()
        top = w.chain(1, ["pwsh.exe", "bash.exe", "bash.exe"], 50.0)
        w.hook(d, top, "S_A", ts=300.0, names=GIT_BASH_HOOK)
        pids = SA._store(d).read(SA.MAP_KEY)["pids"]
        self.assertFalse(any(e.get("claude") for e in pids.values()))

    def test_an_npx_server_is_refused_not_walked_through(self):
        """`node` is a Claude name, never a launcher: a server under an npx `node` stops there."""
        d, w = self._two(), Desk()
        cl = w.claude(born=100.0)
        w.hook(d, cl, "S_A", ts=300.0, names=GIT_BASH_HOOK)
        srv = w.mcp(cl, ["cmd.exe", "node.exe", "uvx.exe", "uv.exe"], born=5000.0)
        self.assertIsNone(SA.session_for_process(d, lookup=w.m.lookup, pid=srv))

    def test_a_deep_chain_through_an_unnamed_process_is_refused(self):
        d, w = self._two(), Desk()
        cl = w.claude(born=100.0)
        w.hook(d, cl, "S_A", ts=300.0, names=GIT_BASH_HOOK)
        srv = w.mcp(cl, ["uvx.exe", "uv.exe"], born=5000.0)
        mid = w.m.procs[srv].ppid
        w.m.procs[mid] = w.m.procs[mid]._replace(name=None)
        self.assertIsNone(SA.session_for_process(d, lookup=w.m.lookup, pid=srv))


class TheWriterIsBoundedByTheRule(_Two):
    """Verification F1/F2: what a hook RECORDS is bounded by what a reader could ever accept, and an
    unrecognised process between a hook and its Claude means refusal, never a reach past it."""

    def test_a_deep_chain_with_no_claude_writes_a_bounded_entry_and_evicts_nobody(self):
        d, w = self._two(), Desk()
        a = w.claude(born=100.0)
        w.hook(d, a, "S_A", ts=300.0, names=GIT_BASH_HOOK)
        before = set(SA._store(d).read(SA.MAP_KEY)["pids"])
        self.assertTrue(SA._store(d).read(SA.MAP_KEY)["pids"][str(a)].get("claude"))
        host = w.chain(4010, ["Code.exe"] + ["bash.exe"] * 2000, 50.0)
        t0 = time.perf_counter()
        w.hook(d, host, "S_X", ts=301.0, names=GIT_BASH_HOOK)
        took = time.perf_counter() - t0
        pids = SA._store(d).read(SA.MAP_KEY)["pids"]
        new = set(pids) - before
        self.assertLessEqual(len(new), SA.HOOK_LAUNCH_DEPTH + 1, "bounded by guard 4's rule")
        self.assertTrue(pids.get(str(a), {}).get("claude"),
                        "another window's Claude entry is never evicted")
        self.assertLess(took, 2.0, f"{took:.2f}s for a 2000-deep chain")
        w.kill_below(a)
        srv = w.mcp(a, UVX_MCP, born=5000.0)
        self.assertEqual("runA", w.m.mcp(d, srv).run_id, "window A still resolves after the write")

    def _unrecognised_below_node(self, launchers):
        d, w = self._two(), Desk()
        cl = w.claude(born=100.0, name="node.exe")
        wrap = w.chain(cl, ["direnv.exe"], 150.0)          # not a launcher, not a Claude
        w.hook(d, wrap, "S_A", ts=300.0, names=launchers)
        pids = SA._store(d).read(SA.MAP_KEY)["pids"]
        self.assertNotIn(str(cl), pids, "the writer never reaches past an unrecognised process")
        self.assertFalse((pids.get(str(cl)) or {}).get("claude"))
        srv = w.mcp(cl, UVX_MCP, born=5000.0)
        self.assertIsNone(SA.session_for_process(d, lookup=w.m.lookup, pid=srv))

    def test_an_unrecognised_process_below_node_is_not_a_launcher_shallow(self):
        self._unrecognised_below_node(["bash.exe"])

    def test_an_unrecognised_process_below_node_is_not_a_launcher_deep(self):
        self._unrecognised_below_node(GIT_BASH_HOOK)


class TheWalkIsBoundedByTheChain(unittest.TestCase):

    def test_the_walk_reaches_the_root_whatever_the_depth(self):
        procs = {2: P(1, "t2", 1.0, "init")}
        for p in range(3, 503):
            procs[p] = P(p - 1, f"t{p}", float(p), "bash")
        self.assertEqual(500, len(SA.ancestors(procs.get, 502)))

    def test_a_looping_table_terminates(self):
        procs = {10: P(20, "a", 1.0, "bash"), 20: P(30, "b", 1.0, "bash"),
                 30: P(10, "c", 1.0, "bash")}
        self.assertEqual([20, 30], [p for p, _ in SA.ancestors(procs.get, 10)])

    def test_the_real_walk_is_cheap(self):
        """The hook path pays one full walk per SessionStart / prompt. On Linux it must stay well
        under a millisecond-scale budget per hop (it is ~one /proc read each)."""
        lookup = SA.system_lookup()
        if lookup is None:
            self.skipTest("no process lookup on this platform")
        n = 200
        t0 = time.perf_counter()
        for _ in range(n):
            chain = SA.ancestors(lookup)
        per = (time.perf_counter() - t0) / n
        self.assertLess(per, 0.05, f"{per * 1e3:.3f} ms for {len(chain)} hops")


if __name__ == "__main__":
    unittest.main()
