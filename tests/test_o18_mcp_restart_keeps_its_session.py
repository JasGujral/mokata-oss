"""O18 — an MCP server (re)started AFTER its Claude's last hook stamp still knows its session.

Live report (Jas, 2026-10-27): registering / amending a spec fails "on later resume", or after "the
Claude chat [was left] open for long — maybe the connection broke in between". The CLI works; the
MCP side does not.

Grounded cause. `session_ancestry.session_for_process` guard 3 ("STALE WRITER") refused any map
entry written before the reader's own branch below the match started. Right when the match sits
ABOVE a Claude (a pane shell / IDE host that a previous, now-dead Claude's hook reached); wrong when
the match IS the reader's own Claude process: an MCP server Claude Code (re)starts after a dropped
connection or a `/mcp` reconnect, or one that came up a few seconds after the SessionStart hook,
had no session until a prompt at least `REFRESH_SECS` later re-stamped the map — and with more
than one run in the repo the resolver went AMBIGUOUS.

The fix: the writer flags the ONE entry that is its Claude, identified POSITIVELY by name (the
first ancestor that is not a shell / interpreter / exec-wrapper), never by age — an independent
review showed an age test also flags the pane shell when Claude is seconds old at SessionStart.
Only a flagged entry is exempt from guard 3.
"""

import unittest

import _support  # noqa: F401
from test_f1_session_bind_by_ancestor import P, Machine, _Base, _pipeline, _repo

from mokata import session_ancestry as SA
from mokata.run_resolver import BASIS_AMBIGUOUS, BASIS_BOUND

# The F1 machine, with names: 1000 tmux -> 1100 zsh (pane A) -> 1110 claude A -> 1150 uv -> 1151 mcp
#                                       -> 1200 zsh (pane B) -> 1210 claude B -> 1251 mcp B
NAMED = {
    1000: P(1, "t1000", 10.0, "tmux"),
    1100: P(1000, "t1100", 100.0, "-zsh"),
    1110: P(1100, "t1110", 110.0, "claude"),
    1150: P(1110, "t1150", 111.0, "uv"),
    1151: P(1150, "t1151", 111.5, "python3.12"),
    1200: P(1000, "t1200", 200.0, "-zsh"),
    1210: P(1200, "t1210", 210.0, "claude"),
    1251: P(1210, "t1251", 211.0, "python3.12"),
}


class NamedMachine(Machine):
    def __init__(self, procs=None):
        super().__init__(dict(NAMED if procs is None else procs))

    def hook(self, root, claude_pid, session_id, ts):
        """`/bin/sh -c` -> python3, as Claude Code runs a command hook."""
        sh, py = self._next, self._next + 1
        self._next += 2
        self.procs[sh] = P(claude_pid, f"t{sh}", ts - 0.2, "/bin/sh")
        self.procs[py] = P(sh, f"t{py}", ts - 0.1, "Python3.12")
        try:
            return SA.record_session(root, session_id, lookup=self.lookup, pid=py, now=ts)
        finally:
            del self.procs[py], self.procs[sh]


class _Two(_Base):
    def _two(self):
        d = _repo()
        _pipeline(d, "runA", "S_A")
        _pipeline(d, "runB", "S_B")
        return d


class AnMcpStartedAfterTheStampKeepsItsSession(_Two):

    def test_an_mcp_restarted_after_a_dropped_connection_still_resolves_its_run(self):
        d, m = self._two(), NamedMachine()
        m.hook(d, 1110, "S_A", ts=300.0)
        self.assertEqual("runA", m.mcp(d, 1151).run_id, "control: the original MCP resolves")
        for pid in (1150, 1151):                     # the MCP server dies (connection dropped)
            del m.procs[pid]
        m.procs[1160] = P(1110, "t1160", 5000.0, "uv")       # restarted, hours later
        m.procs[1161] = P(1160, "t1161", 5000.5, "python3")
        res = m.mcp(d, 1161)
        self.assertEqual(("runA", BASIS_BOUND), (res.run_id, res.basis),
                         "a restarted MCP under the SAME Claude process serves the same session")

    def test_an_mcp_that_came_up_after_the_session_start_hook_resolves(self):
        procs = dict(NAMED)
        procs[1150] = P(1110, "t1150", 125.0, "uv")          # MCP up 15 s after claude started
        procs[1151] = P(1150, "t1151", 125.5, "python3")
        d, m = self._two(), NamedMachine(procs)
        m.hook(d, 1110, "S_A", ts=112.0)             # SessionStart fired first
        self.assertEqual("runA", m.mcp(d, 1151).run_id)

    def test_windows_names_identify_the_claude_too(self):
        procs = {3000: P(1, "t3000", 10.0, "explorer.exe"),
                 3010: P(3000, "t3010", 50.0, "WindowsTerminal.exe"),
                 3020: P(3010, "t3020", 60.0, "pwsh.exe"),
                 3030: P(3020, "t3030", 70.0, "claude.exe"),
                 3051: P(3030, "t3051", 900.0, "python.exe")}
        d, m = self._two(), NamedMachine(procs)
        sh, py = 3100, 3101
        m.procs[sh] = P(3030, "t3100", 299.8, "bash.exe")
        m.procs[py] = P(sh, "t3101", 299.9, "python.exe")
        SA.record_session(d, "S_A", lookup=m.lookup, pid=py, now=300.0)
        del m.procs[py], m.procs[sh]
        self.assertEqual("runA", m.mcp(d, 3051).run_id)


class TheGuardStaysClosedAboveAClaude(_Two):
    """The independent review's three breaks of an AGE-based exemption, kept as controls: a Claude
    whose only write was its SessionStart (seconds old) exits; whatever runs next in that pane or
    IDE host must not inherit its session."""

    def _dead_short_session(self, m, d, claude=1110, below=(1150, 1151)):
        m.hook(d, claude, "S_A", ts=112.0)           # SessionStart 2 s after claude A started
        for pid in (claude,) + tuple(below):
            del m.procs[pid]

    def test_a_new_claude_in_the_same_pane(self):
        d, m = self._two(), NamedMachine()
        self._dead_short_session(m, d)
        m.procs[1310] = P(1100, "t1310", 900.0, "claude")
        m.procs[1351] = P(1310, "t1351", 901.0, "python3")
        self.assertEqual(BASIS_AMBIGUOUS, m.mcp(d, 1351).basis)

    def test_the_mokata_cli_typed_in_that_pane(self):
        d, m = self._two(), NamedMachine()
        self._dead_short_session(m, d)
        m.procs[1400] = P(1100, "t1400", 900.0, "python3")
        self.assertIsNone(SA.session_for_process(d, lookup=m.lookup, pid=1400))

    def test_an_ide_host_that_spawns_claude_directly(self):
        procs = {2000: P(1, "t2000", 10.0, "Code Helper"), 2010: P(2000, "t2010", 50.0, "claude"),
                 2051: P(2010, "t2051", 51.0, "python3")}
        d, m = self._two(), NamedMachine(procs)
        m.hook(d, 2010, "S_A", ts=53.0)
        for pid in (2010, 2051):
            del m.procs[pid]
        m.procs[2110] = P(2000, "t2110", 700.0, "claude")
        m.procs[2151] = P(2110, "t2151", 701.0, "python3")
        self.assertIsNone(SA.session_for_process(d, lookup=m.lookup, pid=2151))

    def test_a_recycled_claude_pid_is_still_ignored(self):
        d, m = self._two(), NamedMachine()
        m.hook(d, 1110, "S_A", ts=300.0)
        m.procs[1110] = P(1100, "t1110-reborn", 600.0, "claude")
        m.procs[1150] = P(1110, "t1150-reborn", 601.0, "uv")
        m.procs[1151] = P(1150, "t1151-reborn", 601.5, "python3")
        self.assertEqual(BASIS_AMBIGUOUS, m.mcp(d, 1151).basis)

    def test_no_names_means_the_strict_guard(self):
        """A lookup that gives no names (an unnamed fake, an exotic platform) flags nothing."""
        d, m = self._two(), Machine()
        m.hook(d, 1110, "S_A", ts=300.0)
        for pid in (1150, 1151):
            del m.procs[pid]
        m.procs[1160] = P(1110, "t1160", 5000.0)
        m.procs[1161] = P(1160, "t1161", 5000.5)
        self.assertEqual(BASIS_AMBIGUOUS, m.mcp(d, 1161).basis)


class ANestedClaudeNeverRepointsItsParent(_Two):
    """Independent review (pre-existing since F1): Claude A's Bash tool runs `claude -p`; that
    nested Claude B's SessionStart walked up THROUGH claude A and re-stamped A's own entry with
    S_B, so A's MCP served B's run until A's next prompt. A write now stops at its own Claude."""

    def test_claude_p_from_the_bash_tool(self):
        d, m = self._two(), NamedMachine()
        m.hook(d, 1110, "S_A", ts=300.0)
        m.procs[7000] = P(1110, "t7000", 400.0, "claude")    # `claude -p` under claude A
        m.hook(d, 7000, "S_B", ts=401.0)
        del m.procs[7000]
        self.assertEqual("runA", m.mcp(d, 1151).run_id)

    def test_the_nested_claudes_mcp_never_gets_the_parents_session(self):
        """Third review: B (a `claude -p` from A's Bash tool) whose own hook has not written yet
        (or never will: hooks off). Its MCP walks up through `claude` B to A's flagged entry —
        a non-launcher sits between, so guard 3 applies and the answer is None."""
        d, m = self._two(), NamedMachine()
        m.hook(d, 1110, "S_A", ts=300.0)
        m.procs[7000] = P(1110, "t7000", 400.0, "bash")
        m.procs[7010] = P(7000, "t7010", 400.5, "claude")
        m.procs[7051] = P(7010, "t7051", 401.0, "python3")
        self.assertIsNone(SA.session_for_process(d, lookup=m.lookup, pid=7051))

    def test_a_wrapper_the_user_put_on_the_hook_is_a_launcher(self):
        d, m = self._two(), NamedMachine()
        sh, tmo, py = 5000, 5001, 5002
        m.procs[sh] = P(1110, "t5000", 299.7, "/bin/sh")
        m.procs[tmo] = P(sh, "t5001", 299.8, "timeout")
        m.procs[py] = P(tmo, "t5002", 299.9, "python3")
        SA.record_session(d, "S_A", lookup=m.lookup, pid=py, now=300.0)
        for pid in (py, tmo, sh, 1150, 1151):
            del m.procs[pid]
        m.procs[1160] = P(1110, "t1160", 5000.0, "uv")
        m.procs[1161] = P(1160, "t1161", 5000.5, "python3")
        self.assertEqual("runA", m.mcp(d, 1161).run_id)

    def test_an_unchanged_mapping_is_still_not_rewritten_every_turn(self):
        d, m = self._two(), NamedMachine()
        self.assertTrue(m.hook(d, 1110, "S_A", ts=300.0))
        self.assertFalse(m.hook(d, 1110, "S_A", ts=310.0), "fresh and unchanged: no write")


class TheClaudeIsNamedNotAged(unittest.TestCase):

    def test_short_names(self):
        for raw, want in (("/bin/sh", "sh"), ("-zsh", "zsh"), ("Python3.12", "python3"),
                          ("C:\\Git\\bin\\bash.exe", "bash"), ("claude", "claude"), ("", None),
                          (None, None)):
            self.assertEqual(want, SA._short_name(raw), raw)

    def test_the_first_non_launcher_within_the_launch_depth_is_the_claude(self):
        chain = [(5, P(4, "a", 1.0, "sh")), (4, P(3, "b", 1.0, "claude")), (3, P(2, "c", 1.0, "zsh"))]
        self.assertEqual(4, SA._writers_claude(chain))
        self.assertIsNone(SA._writers_claude([(5, P(4, "a", 1.0, None))] + chain[1:]))
        deep = [(9, P(8, "x", 1.0, "sh")), (8, P(7, "y", 1.0, "bash")), (7, P(6, "z", 1.0, "sh")),
                (6, P(5, "w", 1.0, "claude"))]
        self.assertIsNone(SA._writers_claude(deep), "beyond HOOK_LAUNCH_DEPTH: no answer")


if __name__ == "__main__":
    unittest.main()
