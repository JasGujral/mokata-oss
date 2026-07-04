"""Stage 3b.2 — the `mokata mcp` command group (start / status / install).

Covers the three durable guarantees:
  * install writes a merge-safe, idempotent Claude Code registration whose command is
    resolved to an ABSOLUTE path when `mokata-mcp` is on PATH (bare fallback otherwise);
  * status performs a REAL MCP `initialize` handshake against the REGISTERED command and
    fails CLOSED with a specific cause (not-registered / command-not-found / sdk-absent /
    timeout / error) — never a false CONNECTED, never a traceback;
  * the group dispatches cleanly and does not clobber the existing `mcp` discovery default.
"""

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

import _support  # noqa: F401  (path fix: puts src/ on sys.path)

from mokata import harness_setup as HS
from mokata import mcp_admin
from mokata.cli import build_parser


# --------------------------------------------------------------------------------------
# Fake stdio servers (pure Python — no MCP SDK needed) for the handshake tests.
# --------------------------------------------------------------------------------------
_CONNECTED = (
    "import sys, json\n"
    "line = sys.stdin.readline()\n"
    "req = json.loads(line)\n"
    "resp = {'jsonrpc': '2.0', 'id': req.get('id'),\n"
    "        'result': {'protocolVersion': '2025-06-18', 'capabilities': {},\n"
    "                   'serverInfo': {'name': 'fake', 'version': '0'}}}\n"
    "sys.stdout.write(json.dumps(resp) + '\\n'); sys.stdout.flush()\n"
)
_DEAD = "import sys\nsys.exit(1)\n"
_SDK_ABSENT = (
    "import sys\n"
    "sys.stderr.write(\"ModuleNotFoundError: No module named 'mcp'\\n\")\n"
    "sys.exit(1)\n"
)
_HANG = "import time\ntime.sleep(30)\n"


def _write_server(dirpath, body):
    path = os.path.join(dirpath, "fake_server.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


class TestInstallMerge(unittest.TestCase):
    def test_resolved_command_is_absolute_when_on_path(self):
        cmd = HS.resolved_mcp_command()
        self.assertTrue(cmd)
        # Either an absolute path (mokata-mcp installed) or the bare fallback name.
        self.assertTrue(os.path.isabs(cmd) or cmd == HS.MCP_COMMAND)

    def test_install_writes_registration(self):
        with tempfile.TemporaryDirectory() as d:
            path = HS.mcp_install("project", d)
            self.assertEqual(Path(path).resolve(), (Path(d) / ".mcp.json").resolve())
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            entry = data["mcpServers"][HS.MCP_SERVER_NAME]
            self.assertEqual(entry["command"], HS.resolved_mcp_command())
            self.assertEqual(entry["args"], [])

    def test_install_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(HS.mcp_install("project", d))
            first = path.read_text(encoding="utf-8")
            HS.mcp_install("project", d)
            second = path.read_text(encoding="utf-8")
            self.assertEqual(first, second)  # second run == byte-identical, no diff

    def test_install_never_clobbers_other_servers_or_keys(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".mcp.json"
            p.write_text(json.dumps({
                "mcpServers": {"other": {"command": "other-mcp"}},
                "unrelatedKey": {"keep": "me"},
            }), encoding="utf-8")
            HS.mcp_install("project", d)
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(data["mcpServers"]["other"], {"command": "other-mcp"})
            self.assertEqual(data["unrelatedKey"], {"keep": "me"})
            self.assertIn(HS.MCP_SERVER_NAME, data["mcpServers"])

    def test_default_merge_keeps_bare_command_for_setup(self):
        # The shared merge must not change the existing `setup` behaviour (bare command);
        # only `mcp install` opts into the resolved absolute path.
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".mcp.json"
            HS._merge_mcp(p)  # default command
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(data["mcpServers"][HS.MCP_SERVER_NAME]["command"],
                             HS.MCP_COMMAND)


class TestConfigWriteSafety(unittest.TestCase):
    """Stage 3c.3 (audit #3/#5): a config mokata can't understand is NEVER clobbered, and
    every write is atomic. Fail-closed per P2 — refuse rather than risk the user's config."""

    # ---- #3: malformed/unusable existing config → refuse, never clobber -------------------
    def test_malformed_mcp_json_refuses_and_leaves_file_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".mcp.json"
            p.write_text("{ this is : not json ", encoding="utf-8")
            before = p.read_text(encoding="utf-8")
            with self.assertRaises(HS.SetupError):
                HS.mcp_install("project", d)
            # the whole point: the unparseable file is byte-for-byte unchanged
            self.assertEqual(p.read_text(encoding="utf-8"), before)

    def test_error_message_names_the_file_and_the_fix(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".mcp.json"
            p.write_text("{bad", encoding="utf-8")
            with self.assertRaises(HS.SetupError) as cm:
                HS._load_json(p)
            msg = str(cm.exception)
            self.assertIn(str(p), msg)            # names the offending file
            self.assertIn("re-run", msg)          # tells the user what to do

    def test_non_object_json_is_refused_not_merged(self):
        # A top-level array/string parses fine but isn't a merge target — refuse, don't clobber.
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".mcp.json"
            p.write_text('["not", "an", "object"]', encoding="utf-8")
            before = p.read_text(encoding="utf-8")
            with self.assertRaises(HS.SetupError):
                HS.mcp_install("project", d)
            self.assertEqual(p.read_text(encoding="utf-8"), before)

    def test_missing_file_is_still_fresh(self):
        # No file yet → we create one (the common first-run path must NOT regress).
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(HS._load_json(Path(d) / "nope.json"), {})

    def test_valid_config_still_merges_and_preserves_others(self):
        # Regression guard: fail-closed must not break the merge-safe path (Stage 3b.2/3b.3).
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".mcp.json"
            p.write_text(json.dumps({
                "mcpServers": {"other": {"command": "/x"}},
                "unrelatedKey": {"keep": "me"},
            }), encoding="utf-8")
            HS.mcp_install("project", d)
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(data["mcpServers"]["other"], {"command": "/x"})
            self.assertEqual(data["unrelatedKey"], {"keep": "me"})
            self.assertIn(HS.MCP_SERVER_NAME, data["mcpServers"])

    # ---- #5: writes are atomic (temp-in-same-dir + os.replace) ----------------------------
    def test_write_is_atomic_via_os_replace(self):
        import inspect
        src = inspect.getsource(HS._write_json)
        self.assertIn("os.replace", src)
        self.assertIn("mkstemp", src)

    def test_write_leaves_no_temp_files_behind(self):
        with tempfile.TemporaryDirectory() as d:
            HS.mcp_install("project", d)
            # only the final .mcp.json should remain — no stray .*.tmp turds.
            names = os.listdir(d)
            self.assertIn(".mcp.json", names)
            self.assertFalse([n for n in names if n.endswith(".tmp")],
                             f"atomic write left temp files: {names}")

    def test_write_replaces_target_content_exactly(self):
        # Round-trip: after a write the file parses to exactly what we wrote (no truncation).
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cfg.json"
            payload = {"a": 1, "nested": {"b": [1, 2, 3]}}
            HS._write_json(p, payload)
            self.assertEqual(json.loads(p.read_text(encoding="utf-8")), payload)


class TestResolveRegistered(unittest.TestCase):
    def test_finds_project_registration(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".mcp.json").write_text(json.dumps({
                "mcpServers": {HS.MCP_SERVER_NAME: {"command": "mokata-mcp", "args": ["--x"]}}
            }), encoding="utf-8")
            reg = mcp_admin.resolve_registered(root=d, home=d)
            self.assertIsNotNone(reg)
            self.assertEqual(reg.command, "mokata-mcp")
            self.assertEqual(reg.args, ["--x"])

    def test_falls_back_to_user_registration(self):
        with tempfile.TemporaryDirectory() as proj, tempfile.TemporaryDirectory() as home:
            (Path(home) / ".claude.json").write_text(json.dumps({
                "mcpServers": {HS.MCP_SERVER_NAME: {"command": "usr-mokata-mcp"}}
            }), encoding="utf-8")
            reg = mcp_admin.resolve_registered(root=proj, home=home)
            self.assertIsNotNone(reg)
            self.assertEqual(reg.command, "usr-mokata-mcp")

    def test_none_when_not_registered(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(mcp_admin.resolve_registered(root=d, home=d))


class TestHandshake(unittest.TestCase):
    def test_connected_against_a_live_server(self):
        with tempfile.TemporaryDirectory() as d:
            srv = _write_server(d, _CONNECTED)
            res = mcp_admin.handshake(sys.executable, [srv], timeout=10.0)
            self.assertTrue(res.ok, res.detail)
            self.assertEqual(res.code, "connected")

    def test_dead_server_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            srv = _write_server(d, _DEAD)
            res = mcp_admin.handshake(sys.executable, [srv], timeout=10.0)
            self.assertFalse(res.ok)
            self.assertEqual(res.code, "error")

    def test_sdk_absent_is_classified(self):
        with tempfile.TemporaryDirectory() as d:
            srv = _write_server(d, _SDK_ABSENT)
            res = mcp_admin.handshake(sys.executable, [srv], timeout=10.0)
            self.assertFalse(res.ok)
            self.assertEqual(res.code, "sdk_absent")
            self.assertTrue(res.fix)

    def test_command_not_found_is_classified(self):
        res = mcp_admin.handshake("mokata-not-a-real-binary-xyz", [], timeout=10.0)
        self.assertFalse(res.ok)
        self.assertEqual(res.code, "command_not_found")

    def test_timeout_is_classified(self):
        with tempfile.TemporaryDirectory() as d:
            srv = _write_server(d, _HANG)
            res = mcp_admin.handshake(sys.executable, [srv], timeout=1.0)
            self.assertFalse(res.ok)
            self.assertEqual(res.code, "timeout")


class TestCliDispatch(unittest.TestCase):
    def _run(self, argv):
        args = build_parser().parse_args(argv)
        return args.func(args)

    def test_install_via_cli(self):
        with tempfile.TemporaryDirectory() as d:
            rc = self._run(["mcp", "install", "--scope", "project", "--path", d])
            self.assertEqual(rc, 0)
            self.assertTrue((Path(d) / ".mcp.json").exists())

    def test_install_twice_no_diff_via_cli(self):
        with tempfile.TemporaryDirectory() as d:
            self._run(["mcp", "install", "--scope", "project", "--path", d])
            first = (Path(d) / ".mcp.json").read_text(encoding="utf-8")
            self._run(["mcp", "install", "--scope", "project", "--path", d])
            self.assertEqual(first, (Path(d) / ".mcp.json").read_text(encoding="utf-8"))

    def test_status_connected_via_cli(self):
        with tempfile.TemporaryDirectory() as d:
            srv = _write_server(d, _CONNECTED)
            (Path(d) / ".mcp.json").write_text(json.dumps({
                "mcpServers": {HS.MCP_SERVER_NAME: {"command": sys.executable, "args": [srv]}}
            }), encoding="utf-8")
            rc = self._run(["mcp", "status", "--path", d, "--home", d])
            self.assertEqual(rc, 0)

    def test_status_not_registered_fails_closed_via_cli(self):
        with tempfile.TemporaryDirectory() as d:
            rc = self._run(["mcp", "status", "--path", d, "--home", d])
            self.assertEqual(rc, 1)

    def test_status_dead_server_fails_closed_via_cli(self):
        with tempfile.TemporaryDirectory() as d:
            srv = _write_server(d, _DEAD)
            (Path(d) / ".mcp.json").write_text(json.dumps({
                "mcpServers": {HS.MCP_SERVER_NAME: {"command": sys.executable, "args": [srv]}}
            }), encoding="utf-8")
            rc = self._run(["mcp", "status", "--path", d, "--home", d])
            self.assertEqual(rc, 1)

    def test_discover_remains_the_default_action(self):
        # The existing H4 discovery must survive as the default (no action) — bare `mokata mcp`
        # still routes to discover (unchanged; discover itself needs an initialized surface).
        args = build_parser().parse_args(["mcp"])
        self.assertEqual(args.action, "discover")
        self.assertIs(args.func, __import__("mokata.cli", fromlist=["cmd_mcp"]).cmd_mcp)


class TestParityStillGreen(unittest.TestCase):
    def test_mcp_command_stays_covered(self):
        from mokata.parity import SURFACE_MATRIX
        self.assertIn("mcp", SURFACE_MATRIX)
        self.assertTrue(SURFACE_MATRIX["mcp"].covered)


if __name__ == "__main__":
    unittest.main()
