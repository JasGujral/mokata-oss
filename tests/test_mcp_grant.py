"""MCP permission-grant + status surface (2026-07-07).

Root cause of the "MCP tools get stuck / press ESC to escape the agent loop": `mokata
setup claude` registers the server and wires hooks, but NEVER grants Claude Code
permission for mokata's MCP tools, so Claude Code GATES every `mcp__mokata__*` call
(approval prompt / loop) and it hangs.

Fix (this file's contract):
  * setup + `mokata mcp install` MERGE two keys into `.claude/settings.json` — never
    clobber, dedup arrays:
      - enabledMcpjsonServers += "mokata"   (surgical — trust mokata's project server)
      - permissions.allow      += "mcp__mokata__*"
  * default ON, opt out with `--no-grant`; `--no-hooks` leaves settings.json alone entirely
  * unsetup removes EXACTLY those keys (preserving the user's other allow/enabled entries)
  * `mokata doctor` + `mokata mcp status` SURFACE registered / enabled / permitted /
    CONNECTED, each with the one-line fix (`mokata mcp install`) when missing — via the
    SHARED mcp_admin reporter so they can't drift.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import _support  # noqa: F401  (puts src/ on the path)

from mokata import harness_setup as HS
from mokata import mcp_admin
from mokata.cli import build_parser, main

GRANT_PERM = "mcp__mokata__*"


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _settings(root):
    return Path(root) / ".claude" / "settings.json"


def _run_cli(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(argv)
    return rc, buf.getvalue()


# --------------------------------------------------------------------------------------
# _merge_grant — the low-level merge (fresh + existing, dedup, union)
# --------------------------------------------------------------------------------------
class TestMergeGrant(unittest.TestCase):
    def test_grant_into_fresh_settings(self):
        with tempfile.TemporaryDirectory() as d:
            p = _settings(d)
            HS._merge_grant(p)
            data = _read(p)
            self.assertEqual(data["enabledMcpjsonServers"], ["mokata"])
            self.assertIn(GRANT_PERM, data["permissions"]["allow"])

    def test_grant_preserves_and_unions_existing(self):
        with tempfile.TemporaryDirectory() as d:
            p = _settings(d)
            p.parent.mkdir(parents=True)
            p.write_text(json.dumps({
                "enabledMcpjsonServers": ["other"],
                "permissions": {"allow": ["Read"], "deny": ["Bash(rm*)"]},
                "statusLine": {"command": "keepme"},
            }), encoding="utf-8")
            HS._merge_grant(p)
            data = _read(p)
            # unioned, not clobbered
            self.assertEqual(sorted(data["enabledMcpjsonServers"]), ["mokata", "other"])
            self.assertIn("Read", data["permissions"]["allow"])
            self.assertIn(GRANT_PERM, data["permissions"]["allow"])
            # unrelated keys untouched
            self.assertEqual(data["permissions"]["deny"], ["Bash(rm*)"])
            self.assertEqual(data["statusLine"], {"command": "keepme"})

    def test_grant_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            p = _settings(d)
            HS._merge_grant(p)
            HS._merge_grant(p)
            data = _read(p)
            self.assertEqual(data["enabledMcpjsonServers"], ["mokata"])
            self.assertEqual(data["permissions"]["allow"].count(GRANT_PERM), 1)


# --------------------------------------------------------------------------------------
# setup wires the grant (default ON); --no-grant / --no-hooks opt out
# --------------------------------------------------------------------------------------
class TestSetupGrant(unittest.TestCase):
    def test_setup_grants_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            HS.setup_harness("claude", root=d, scope="project", home=d,
                             assume_yes=True, out=lambda _: None)
            data = _read(_settings(d))
            self.assertIn("mokata", data["enabledMcpjsonServers"])
            self.assertIn(GRANT_PERM, data["permissions"]["allow"])
            # hooks still there (grant is additive, merge-safe)
            self.assertIn("SessionStart", data["hooks"])

    def test_setup_prints_a_clear_grant_line(self):
        lines = []
        with tempfile.TemporaryDirectory() as d:
            HS.setup_harness("claude", root=d, scope="project", home=d,
                             assume_yes=True, out=lines.append)
            blob = "\n".join(lines)
            self.assertIn("Granted", blob)
            self.assertIn("Restart Claude Code", blob)

    def test_plan_preview_mentions_the_grant(self):
        with tempfile.TemporaryDirectory() as d:
            plan = HS.plan_setup("claude", root=d, home=d)
            text = HS.render_setup_plan(plan)
            self.assertIn("mcp__mokata__*", text)
            self.assertIn("enabledMcpjsonServers", text)

    def test_no_grant_flag_skips_the_grant_but_keeps_hooks(self):
        with tempfile.TemporaryDirectory() as d:
            HS.setup_harness("claude", root=d, scope="project", home=d,
                             assume_yes=True, grant=False, out=lambda _: None)
            data = _read(_settings(d))
            self.assertIn("SessionStart", data["hooks"])          # hooks wired
            self.assertNotIn("enabledMcpjsonServers", data)       # grant skipped
            self.assertNotIn(GRANT_PERM,
                             (data.get("permissions") or {}).get("allow", []))

    def test_no_hooks_skips_grant_too(self):
        # --no-hooks means "leave my Claude settings.json alone" — so no grant either.
        with tempfile.TemporaryDirectory() as d:
            HS.setup_harness("claude", root=d, scope="project", home=d,
                             with_hooks=False, assume_yes=True, out=lambda _: None)
            self.assertFalse(_settings(d).exists())

    def test_grant_via_cli_flag(self):
        with tempfile.TemporaryDirectory() as d:
            rc, _ = _run_cli(["setup", "claude", "--yes", "--no-grant", "--path", d])
            self.assertEqual(rc, 0)
            self.assertNotIn("enabledMcpjsonServers", _read(_settings(d)))


# --------------------------------------------------------------------------------------
# mcp install also grants
# --------------------------------------------------------------------------------------
class TestInstallGrant(unittest.TestCase):
    def test_install_grants(self):
        with tempfile.TemporaryDirectory() as d:
            HS.mcp_install("project", d, home=d)
            data = _read(_settings(d))
            self.assertIn("mokata", data["enabledMcpjsonServers"])
            self.assertIn(GRANT_PERM, data["permissions"]["allow"])

    def test_install_no_grant(self):
        with tempfile.TemporaryDirectory() as d:
            HS.mcp_install("project", d, home=d, grant=False)
            self.assertFalse(_settings(d).exists())

    def test_install_via_cli_prints_grant(self):
        with tempfile.TemporaryDirectory() as d:
            rc, out = _run_cli(["mcp", "install", "--scope", "project", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("Granted", out)
            self.assertIn("mokata", _read(_settings(d))["enabledMcpjsonServers"])


# --------------------------------------------------------------------------------------
# unsetup removes exactly the grant keys
# --------------------------------------------------------------------------------------
class TestUnsetupGrant(unittest.TestCase):
    def test_unsetup_removes_grant_keys_only(self):
        with tempfile.TemporaryDirectory() as d:
            # a user with their own enabled server + allow entry
            p = _settings(d)
            p.parent.mkdir(parents=True)
            p.write_text(json.dumps({
                "enabledMcpjsonServers": ["other"],
                "permissions": {"allow": ["Read"]},
            }), encoding="utf-8")
            HS.setup_harness("claude", root=d, scope="project", home=d,
                             assume_yes=True, out=lambda _: None)
            HS.unsetup_harness("claude", root=d, scope="project", home=d,
                               assume_yes=True, out=lambda _: None)
            data = _read(p)
            # mokata's grant gone, the user's own entries survive
            self.assertNotIn("mokata", data.get("enabledMcpjsonServers", []))
            self.assertIn("other", data["enabledMcpjsonServers"])
            self.assertNotIn(GRANT_PERM, data["permissions"]["allow"])
            self.assertIn("Read", data["permissions"]["allow"])


# --------------------------------------------------------------------------------------
# mcp_admin.grant_status — read the enablement + permission grant
# --------------------------------------------------------------------------------------
class TestGrantStatus(unittest.TestCase):
    def test_detects_enabled_and_permitted(self):
        with tempfile.TemporaryDirectory() as d:
            p = _settings(d)
            p.parent.mkdir(parents=True)
            p.write_text(json.dumps({
                "enabledMcpjsonServers": ["mokata"],
                "permissions": {"allow": [GRANT_PERM]},
            }), encoding="utf-8")
            g = mcp_admin.grant_status(root=d, home=d)
            self.assertTrue(g.enabled)
            self.assertTrue(g.permitted)

    def test_missing_grant_is_not_enabled_or_permitted(self):
        with tempfile.TemporaryDirectory() as d:
            g = mcp_admin.grant_status(root=d, home=d)
            self.assertFalse(g.enabled)
            self.assertFalse(g.permitted)

    def test_enable_all_project_servers_counts_as_enabled(self):
        with tempfile.TemporaryDirectory() as d:
            p = _settings(d)
            p.parent.mkdir(parents=True)
            p.write_text(json.dumps({"enableAllProjectMcpServers": True}),
                         encoding="utf-8")
            g = mcp_admin.grant_status(root=d, home=d)
            self.assertTrue(g.enabled)

    def test_never_raises_on_garbage(self):
        with tempfile.TemporaryDirectory() as d:
            p = _settings(d)
            p.parent.mkdir(parents=True)
            p.write_text("{ not json", encoding="utf-8")
            g = mcp_admin.grant_status(root=d, home=d)   # must not raise
            self.assertFalse(g.enabled)


# --------------------------------------------------------------------------------------
# full_status — the shared reporter doctor + `mcp status` both use
# --------------------------------------------------------------------------------------
_CONNECTED = (
    "import sys, json\n"
    "req = json.loads(sys.stdin.readline())\n"
    "sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': req.get('id'),\n"
    "  'result': {'protocolVersion': '2025-06-18', 'capabilities': {}}}) + '\\n')\n"
    "sys.stdout.flush()\n"
)


def _write_connected_reg(d):
    srv = os.path.join(d, "fake_server.py")
    with open(srv, "w", encoding="utf-8") as fh:
        fh.write(_CONNECTED)
    (Path(d) / ".mcp.json").write_text(json.dumps({
        "mcpServers": {"mokata": {"command": sys.executable, "args": [srv]}}
    }), encoding="utf-8")


class TestFullStatus(unittest.TestCase):
    def test_reports_all_four_dimensions_when_missing(self):
        with tempfile.TemporaryDirectory() as d:
            fs = mcp_admin.full_status(root=d, home=d)
            blob = "\n".join(fs.lines)
            # not registered / not enabled / not permitted → each names the fix
            self.assertFalse(fs.registered)
            self.assertFalse(fs.enabled)
            self.assertFalse(fs.permitted)
            self.assertIn("mokata mcp install", blob)

    def test_fully_wired_reports_ok(self):
        with tempfile.TemporaryDirectory() as d:
            _write_connected_reg(d)
            HS._merge_grant(_settings(d))
            fs = mcp_admin.full_status(root=d, home=d)
            self.assertTrue(fs.registered)
            self.assertTrue(fs.enabled)
            self.assertTrue(fs.permitted)
            self.assertTrue(fs.connected, "\n".join(fs.lines))
            self.assertTrue(fs.ok)

    def test_never_raises(self):
        with tempfile.TemporaryDirectory() as d:
            fs = mcp_admin.full_status(root=d, home="/definitely/not/a/dir")
            self.assertIsInstance(fs.lines, list)


# --------------------------------------------------------------------------------------
# doctor + mcp status surface the grant
# --------------------------------------------------------------------------------------
class TestSurfaces(unittest.TestCase):
    def test_mcp_status_reports_enabled_permitted(self):
        with tempfile.TemporaryDirectory() as d:
            _write_connected_reg(d)   # connected but NOT granted
            args = build_parser().parse_args(["mcp", "status", "--path", d, "--home", d])
            buf = io.StringIO()
            err = io.StringIO()
            from contextlib import redirect_stderr
            with redirect_stdout(buf), redirect_stderr(err):
                args.func(args)
            blob = buf.getvalue() + err.getvalue()
            self.assertIn("permitted", blob.lower())
            self.assertIn("mokata mcp install", blob)

    def test_doctor_shows_an_mcp_line(self):
        with tempfile.TemporaryDirectory() as d:
            HS.setup_harness("claude", root=d, scope="project", home=d,
                             assume_yes=True, out=lambda _: None)
            args = build_parser().parse_args(["doctor", "--path", d])
            buf = io.StringIO()
            with redirect_stdout(buf):
                args.func(args)
            self.assertIn("mokata-mcp", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
