"""0.0.22 stage D5 — `bash-lane`: SP-BLIND + the Bash write side door (+ HP.S4).

Before D5 a write the run-state gates would block through `Write` went straight through as
`sed -i … src/app.py` or `echo … > src/app.py`: the gates decide from a path, and a Bash call has
none. Now:
  1. every destination the parser can see (bare names included) meets the SAME gates a Write would;
  2. in a session that is in a pipeline, a command whose writes mokata cannot read is REFUSED;
  3. reads are never refused, and a session in no pipeline sees nothing new.
Parallel runs: each session's Bash writes are judged by its OWN run.
"""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import bash_lane as BL
from mokata import gate_hook as G
from mokata import hook_cli
from mokata import run_intent as RI
from mokata import tdd_state
from mokata.config import Surface
from mokata.govern.resume import PipelineCheckpoint
from mokata.init import init_repo
from mokata.run_resolver import APPROACH_PREFIX
from mokata.state import StateStore
from mokata.tdd_state import state_dir

# The eight PARSER_BLIND_SPOTS shapes (tests/test_self_protect.py), each written so it WRITES.
UNREADABLE = {
    "variable expansion": 'T=src/app.py; tee "$T" </dev/null',
    "command substitution": 'tee "$(cat /tmp/where)/app.py" </dev/null',
    "eval": 'eval "tee src/app.py </dev/null"',
    "runtime-built path": "python3 -c \"import os;open(os.path.join('src','app.py'),'w')\"",
    "heredoc into a shell": "bash <<'EOF'\necho x > src/app.py\nEOF",
    "find -exec": "find . -name '*.py' -exec sed -i s/a/b/ {} +",
    "xargs": "echo src/app.py | xargs -I{} touch {}",
    "an unlisted interpreter": "node -e \"require('fs').writeFileSync('src/app.py','')\"",
}
READS = ("git ls-files | xargs grep foo", "ls -la 2>/dev/null", "cat <<'EOF'\nsrc/app.py\nEOF",
         "python -m unittest -q", "python3 -c \"print(open('README.md').read())\"",
         "node -e \"console.log(1 > 0)\"", "find . -name '*.py'", "git commit -m 'a > b'",
         "bash scripts/check.sh")


def _silent(_):
    pass


def _repo(d, *runs):
    init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
    surface = Surface.load(d)
    for rid in runs:
        PipelineCheckpoint(surface.state, rid).ensure_registered()
    os.makedirs(os.path.join(d, "src"), exist_ok=True)
    return Surface.load(d)


def _in_tdd(d, rid):
    """A run whose implementation writes are allowed: approach approved, spec emitted, a red test."""
    store = StateStore(state_dir(d))
    store.write(APPROACH_PREFIX + rid, {"run_id": rid, "approach": "a"})
    store.write(G.SPEC_PREFIX + rid, {"run_id": rid})
    tdd_state.record(store, rid, red=["tests/test_app.py::test_x"])


def _bash(d, sid, command):
    payload = {"cwd": d, "session_id": sid, "tool_name": "Bash", "tool_input": {"command": command}}
    err = io.StringIO()
    with mock.patch.object(hook_cli, "_read_stdin_bounded", return_value=json.dumps(payload)), \
            redirect_stderr(err):
        code = hook_cli.gate_guard_main([])
    return code, err.getvalue()


def _write(d, sid, rel):
    payload = {"cwd": d, "session_id": sid, "tool_name": "Write",
               "tool_input": {"file_path": os.path.join(d, rel), "content": "x = 1\n"}}
    err = io.StringIO()
    with mock.patch.object(hook_cli, "_read_stdin_bounded", return_value=json.dumps(payload)), \
            redirect_stderr(err):
        code = hook_cli.gate_guard_main([])
    return code, err.getvalue()


class TheSideDoorMeetsTheSameGatesAsWrite(unittest.TestCase):

    def setUp(self):
        os.environ.pop("MOKATA_SESSION_ID", None)

    def test_a_bash_write_is_blocked_exactly_where_a_Write_is(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            RI.bind_registered(d, "S1", "rA")          # no approach yet → impl writes blocked
            native_code, native_err = _write(d, "S1", "src/app.py")
            self.assertEqual(hook_cli.BLOCK_EXIT, native_code)
            gate = native_err.split("]")[0]
            for cmd in ("echo x > src/app.py", "sed -i s/a/b/ src/app.py",
                        "cp /tmp/x.py src/app.py", "cd src && tee app.py </dev/null",
                        "python3 -c \"open('src/app.py','w').write('x')\""):
                with self.subTest(cmd=cmd):
                    code, err = _bash(d, "S1", cmd)
                    self.assertEqual(hook_cli.BLOCK_EXIT, code, err)
                    self.assertEqual(gate, err.split("]")[0], "a different gate than Write's")

    def test_a_test_file_is_writable_by_bash_as_by_Write(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            RI.bind_registered(d, "S1", "rA")
            self.assertEqual(0, _bash(d, "S1", "echo 'def test_x(): assert 0' > tests/test_app.py")[0])

    def test_in_tdd_the_bash_write_is_allowed_like_the_Write(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            _in_tdd(d, "rA")
            RI.bind_registered(d, "S1", "rA")
            self.assertEqual(_write(d, "S1", "src/app.py")[0],
                             _bash(d, "S1", "sed -i s/a/b/ src/app.py")[0])


class RefuseWhatWeCannotRead(unittest.TestCase):

    def setUp(self):
        os.environ.pop("MOKATA_SESSION_ID", None)

    def test_each_blind_spot_is_refused_in_a_run_with_its_reason(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            _in_tdd(d, "rA")                           # even where Write WOULD be allowed
            RI.bind_registered(d, "S1", "rA")
            for name, cmd in UNREADABLE.items():
                with self.subTest(blind_spot=name):
                    code, err = _bash(d, "S1", cmd)
                    self.assertEqual(hook_cli.BLOCK_EXIT, code, err)
                    self.assertIn(f"BLOCKED [{BL.GATE_UNREADABLE}]", err)
                    self.assertIn("Write/Edit", err, "the way out is named")

    def test_a_session_in_no_pipeline_is_not_refused(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            for name, cmd in UNREADABLE.items():
                with self.subTest(blind_spot=name):
                    self.assertEqual(0, _bash(d, "S9", cmd)[0])

    def test_reads_are_never_refused_even_in_a_run(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            RI.bind_registered(d, "S1", "rA")
            for cmd in READS:
                with self.subTest(cmd=cmd):
                    code, err = _bash(d, "S1", cmd)
                    self.assertEqual(0, code, err)

    def test_an_unreadable_refusal_counts_as_an_in_loop_catch(self):
        from mokata.progress_events import review_yield
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, "rA")
            RI.bind_registered(d, "S1", "rA")
            _bash(d, "S1", UNREADABLE["eval"])
            self.assertEqual(1, review_yield(surface, "rA").in_loop)

    def test_the_lane_sees_what_it_claims_and_names_why(self):
        w = BL.analyze("sed -i s/a/b/ notes.py && echo x >> ./log.txt", "/repo")
        self.assertEqual(("/repo/notes.py", "/repo/log.txt"), w.targets)
        self.assertTrue(w.readable)
        self.assertIn("built at runtime", BL.analyze('tee "$OUT"', "/r").unreadable[0])

    def test_a_literal_cd_is_followed_and_a_computed_one_is_unreadable(self):
        self.assertEqual(("/tmp/a.py",), BL.analyze("cd /tmp && echo x > a.py", "/repo").targets)
        self.assertEqual(("/repo/src/app.py",),
                         BL.analyze("cd src; sed -i s/a/b/ app.py", "/repo").targets)
        self.assertIn("built at runtime",
                      BL.analyze('cd "$D" && echo x > a.py', "/repo").unreadable[0])


class ParallelRunsEachJudgeTheirOwn(unittest.TestCase):

    def setUp(self):
        os.environ.pop("MOKATA_SESSION_ID", None)

    def test_two_sessions_two_runs_two_verdicts_on_the_same_bash_write(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA", "rB")
            _in_tdd(d, "rB")                           # rB may write; rA has no approach
            RI.bind_registered(d, "S1", "rA")
            RI.bind_registered(d, "S2", "rB")
            self.assertEqual(hook_cli.BLOCK_EXIT, _bash(d, "S1", "echo x > src/app.py")[0])
            self.assertEqual(0, _bash(d, "S2", "echo x > src/app.py")[0])


class ReadOnlySubagentsAreProvablyReadOnly(unittest.TestCase):

    def test_a_bare_name_or_an_unreadable_command_is_a_write(self):
        from mokata import subagent_contract as SC
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            RI.bind_registered(d, "S1", "rA")
            SC.start_contract(d, "S1", "a1", "Explore")
            for cmd in ("sed -i s/a/b/ notes.md", UNREADABLE["xargs"]):
                with self.subTest(cmd=cmd):
                    payload = {"cwd": d, "session_id": "S1", "agent_id": "a1",
                               "tool_name": "Bash", "tool_input": {"command": cmd}}
                    err = io.StringIO()
                    with mock.patch.object(hook_cli, "_read_stdin_bounded",
                                           return_value=json.dumps(payload)), redirect_stderr(err):
                        code = hook_cli.gate_guard_main([])
                    self.assertEqual(hook_cli.BLOCK_EXIT, code)
                    self.assertIn(SC.GATE_READ_ONLY, err.getvalue())


class HpS4ContainmentIsOnForEveryGatedWrite(unittest.TestCase):
    """HP.S4 — the WriteGate's containment rule was OFF for every caller that threaded no root (~26
    of them). It now uses the gate's own workspaces (process cwd + the ledger's repo) and refuses a
    write outside both, except the named user-harness-config allowance for CONFIG writes. The temp
    allowance is switched off here so the test proves the gate's decision, not the scratch rule."""

    def setUp(self):
        self._tmp = mock.patch("mokata.selfprotect._temp_roots", return_value=())
        self._tmp.start()
        self.addCleanup(self._tmp.stop)

    def _gate(self, repo):
        from mokata.govern.gate import WriteGate
        from mokata.govern.ledger import AuditLedger
        return WriteGate(ledger=AuditLedger.from_mokata_dir(os.path.join(repo, ".mokata")))

    def _submit(self, gate, kind, target):
        from mokata.govern.gate import WriteRequest
        return gate.submit(WriteRequest(kind=kind, target=target, content="x"),
                           commit=lambda: None, assume_yes=True)

    def test_a_write_outside_every_workspace_is_refused_without_a_threaded_root(self):
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as elsewhere:
            _repo(repo)
            with mock.patch("os.getcwd", return_value=repo):
                out = self._submit(self._gate(repo), "code", os.path.join(elsewhere, "x.py"))
            self.assertFalse(out.committed)
            self.assertIn("OUTSIDE the workspace root", out.reason)

    def test_the_ledger_repo_counts_even_from_another_cwd(self):
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as cwd:
            _repo(repo)
            with mock.patch("os.getcwd", return_value=cwd):
                out = self._submit(self._gate(repo), "code", os.path.join(repo, "src", "a.py"))
            self.assertTrue(out.committed, out.reason)

    def test_user_harness_config_is_the_one_named_way_out_and_only_for_config(self):
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as home:
            _repo(repo)
            with mock.patch.dict(os.environ, {"HOME": home}), \
                    mock.patch("os.getcwd", return_value=repo):
                gate = self._gate(repo)
                for target, kind, ok in (
                        (os.path.join(home, ".claude.json"), "config", True),
                        (os.path.join(home, ".claude", "settings.json"), "config", True),
                        (os.path.join(home, ".claude", "skills", "x", "SKILL.md"), "config", True),
                        (os.path.join(home, ".claude.json"), "code", False),
                        (os.path.join(home, ".claude", "projects", "p", "t.jsonl"), "config", False),
                        (os.path.join(home, ".ssh", "config"), "config", False)):
                    with self.subTest(target=target, kind=kind):
                        self.assertEqual(ok, self._submit(gate, kind, target).committed)

    def test_setup_config_writes_meet_the_gates_destination_layers(self):
        from mokata import harness_setup as HS
        from pathlib import Path
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as home, \
                tempfile.TemporaryDirectory() as elsewhere:
            _repo(repo)
            with mock.patch.dict(os.environ, {"HOME": home}), \
                    mock.patch("os.getcwd", return_value=repo):
                HS._write_json(Path(home) / ".claude.json", {"mcpServers": {}})
                HS._write_json(Path(repo) / ".mcp.json", {"mcpServers": {}})
                with self.assertRaises(HS.ConfigWriteRefused):
                    HS._write_json(Path(elsewhere) / "evil.json", {})
                site = Path(elsewhere) / "lib" / "python3.12" / "site-packages" / "m.json"
                with self.assertRaises(HS.ConfigWriteRefused):
                    HS._write_json(site, {})
            self.assertFalse((Path(elsewhere) / "evil.json").exists(), "refused, and nothing written")


if __name__ == "__main__":
    unittest.main()
