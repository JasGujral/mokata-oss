"""Stage 3b.3 — setup auto-start (absolute-path registration) + CONNECTED verification,
plus the minimal, subprocess-free SessionStart reachability warning.

Guarantees frozen here:
  * `setup claude` registers the MCP server via the SAME resolver as `mokata mcp install`
    (absolute path when on PATH) — merge-safe + idempotent, no forked registration path;
  * setup ends with an informational CONNECTED verification that NEVER gates setup success
    and NEVER raises;
  * SessionStart warns (one line + fix) IFF mokata is registered but its command can't be
    resolved — a fast local lookup, no handshake subprocess; silent otherwise.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import _support  # noqa: F401  (path fix: puts src/ on sys.path)

from mokata import harness_setup as HS
from mokata import hook_cli
from mokata import mcp_admin
from mokata.init import init_repo


_CONNECTED = (
    "import sys, json\n"
    "req = json.loads(sys.stdin.readline())\n"
    "sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': req.get('id'),\n"
    "    'result': {'protocolVersion': '2025-06-18', 'capabilities': {},\n"
    "               'serverInfo': {'name': 'fake', 'version': '0'}}}) + '\\n')\n"
    "sys.stdout.flush()\n"
)


def _write_reg(root, command, args=None):
    (Path(root) / ".mcp.json").write_text(json.dumps({
        "mcpServers": {HS.MCP_SERVER_NAME: {"command": command, "args": args or []}}
    }), encoding="utf-8")


def _fake_connected_server(dirpath):
    path = os.path.join(dirpath, "fake_server.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_CONNECTED)
    return path


class TestSetupRegistersAbsolutePath(unittest.TestCase):
    def test_setup_uses_the_same_resolver_as_install(self):
        with tempfile.TemporaryDirectory() as d:
            HS.setup_harness("claude", root=d, scope="project", home=d,
                             assume_yes=True, out=lambda *_: None)
            data = json.loads((Path(d) / ".mcp.json").read_text(encoding="utf-8"))
            self.assertEqual(data["mcpServers"][HS.MCP_SERVER_NAME]["command"],
                             HS.resolved_mcp_command())

    def test_setup_registration_is_idempotent_and_merge_safe(self):
        with tempfile.TemporaryDirectory() as d:
            # Pre-existing unrelated server + key must survive re-runs unchanged.
            _write_reg(d, "other-mcp")
            p = Path(d) / ".mcp.json"
            data = json.loads(p.read_text(encoding="utf-8"))
            data["mcpServers"]["other"] = {"command": "other-mcp"}
            data["unrelatedKey"] = {"keep": "me"}
            p.write_text(json.dumps(data), encoding="utf-8")

            HS.setup_harness("claude", root=d, scope="project", home=d,
                             assume_yes=True, out=lambda *_: None)
            first = p.read_text(encoding="utf-8")
            HS.setup_harness("claude", root=d, scope="project", home=d,
                             assume_yes=True, out=lambda *_: None)
            second = p.read_text(encoding="utf-8")

            self.assertEqual(first, second)  # re-run == no new diff
            got = json.loads(second)
            self.assertEqual(got["mcpServers"]["other"], {"command": "other-mcp"})
            self.assertEqual(got["unrelatedKey"], {"keep": "me"})
            self.assertIn(HS.MCP_SERVER_NAME, got["mcpServers"])


class TestResolvedCommandSiblingFallback(unittest.TestCase):
    def test_resolves_interpreter_sibling_when_not_on_path(self):
        # The silent-killer case: the install's bin dir isn't on PATH, so `which` fails —
        # but the `mokata-mcp` console script sits next to the running interpreter and must
        # still resolve to that absolute path (not degrade to the bare command).
        sibling = Path(sys.executable).parent / HS.MCP_COMMAND
        if not sibling.is_file():
            self.skipTest("mokata-mcp console script not installed beside this interpreter")
        old = os.environ.get("PATH")
        try:
            os.environ["PATH"] = ""                 # force shutil.which to find nothing
            cmd = HS.resolved_mcp_command()
        finally:
            if old is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = old
        self.assertEqual(cmd, str(sibling))
        self.assertTrue(os.path.isabs(cmd))


class TestSetupConnectedVerification(unittest.TestCase):
    def test_setup_prints_a_status_line_and_does_not_gate(self):
        lines = []
        with tempfile.TemporaryDirectory() as d:
            res = HS.setup_harness("claude", root=d, scope="project", home=d,
                                   assume_yes=True, out=lines.append)
            self.assertEqual(res.message, "ok")          # verification never gates setup
            self.assertFalse(res.aborted)
            self.assertTrue(any(ln.startswith("mokata-mcp:") for ln in lines),
                            "setup must print a CONNECTED/failure status line")


class TestStatusLinesSharedReporter(unittest.TestCase):
    def test_connected_report(self):
        with tempfile.TemporaryDirectory() as d:
            _write_reg(d, sys.executable, [_fake_connected_server(d)])
            ok, lines = mcp_admin.status_lines(root=d, home=d)
            self.assertTrue(ok, lines)
            self.assertEqual(lines, ["mokata-mcp: CONNECTED ✓"])

    def test_not_registered_report(self):
        with tempfile.TemporaryDirectory() as d:
            ok, lines = mcp_admin.status_lines(root=d, home=d)
            self.assertFalse(ok)
            self.assertTrue(lines[0].startswith("mokata-mcp: NOT REGISTERED"))

    def test_never_raises(self):
        # Even a garbage registration path yields a report, never an exception.
        with tempfile.TemporaryDirectory() as d:
            _write_reg(d, "definitely-not-a-real-binary-xyz")
            ok, lines = mcp_admin.status_lines(root=d, home=d)
            self.assertFalse(ok)
            self.assertTrue(lines)


class TestUnreachableRegistration(unittest.TestCase):
    def test_bad_command_is_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            _write_reg(d, "/nonexistent/mokata-mcp")
            reg = mcp_admin.unreachable_registration(root=d, home=d)
            self.assertIsNotNone(reg)
            self.assertEqual(reg.command, "/nonexistent/mokata-mcp")

    def test_resolvable_absolute_command_is_ok(self):
        with tempfile.TemporaryDirectory() as d:
            _write_reg(d, sys.executable)          # a real, executable absolute path
            self.assertIsNone(mcp_admin.unreachable_registration(root=d, home=d))

    def test_not_registered_is_silent(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(mcp_admin.unreachable_registration(root=d, home=d))


class TestSessionStartWarning(unittest.TestCase):
    @contextlib.contextmanager
    def _stdin(self, cwd):
        old = sys.stdin
        sys.stdin = io.StringIO(json.dumps({"cwd": cwd}))
        try:
            yield
        finally:
            sys.stdin = old

    def _run_session_start(self, root):
        buf = io.StringIO()
        with self._stdin(root), contextlib.redirect_stdout(buf):
            rc = hook_cli.session_start_main([])
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue().strip().splitlines()[-1])
        return payload["hookSpecificOutput"]["additionalContext"]

    def test_warns_when_registered_but_unreachable(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, assume_yes=True, out=lambda *_: None)
            _write_reg(d, "/nonexistent/mokata-mcp")
            ctx = self._run_session_start(d)
            self.assertIn("mokata-mcp", ctx)
            self.assertIn("mokata mcp install", ctx)  # the one-line fix

    def test_silent_when_reachable(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, assume_yes=True, out=lambda *_: None)
            _write_reg(d, sys.executable)           # resolvable
            ctx = self._run_session_start(d)
            self.assertNotIn("isn't reachable", ctx)

    def test_silent_when_not_registered(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, assume_yes=True, out=lambda *_: None)
            ctx = self._run_session_start(d)
            self.assertNotIn("isn't reachable", ctx)


if __name__ == "__main__":
    unittest.main()
