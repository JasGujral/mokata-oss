"""Stage B-VER — version-parity preventive check.

The 2026-07-14 live incident: `pip install -U mokata` + `mokata setup claude` still launched the
OLD MCP server, silently. Setup verified the server was CONNECTED but never that the command
Claude Code will launch serves the SAME VERSION as the CLI that just wrote the registration. Four
undetected stale paths: a stale running process, multi-env drift, scope shadowing, plugin
shadowing. This stage detects and NAMES the guilty environment at setup time.

Every numbered deliverable of the stage maps to at least one test class below:
  1. `mokata-mcp --version`                         -> TestVersionFlag
  2. version-parity probe (MATCH/MISMATCH/…)        -> TestVersionParityFinding, TestSetupSurfacesParity,
                                                        test_b_ver_regression (TestBVerRegression)
  3. both-scope sweep                               -> TestScopeShadow
  4. plugin shadowing                               -> TestPluginShadow
  5. restart hint on every registration path        -> TestRestartHintOnEveryWritePath
  6. shared probe in `mcp status` + doctor          -> TestSharedInStatusAndDoctor
  7. no behaviour change (additive only)            -> TestNoBehaviourChange
  secret-safety                                     -> TestSecretSafety
"""

import argparse
import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

import _support  # noqa: F401  (path fix: puts src/ on sys.path)

from mokata import __version__
from mokata import harness_setup as HS
from mokata import mcp_admin
from mokata import mcp_server as M
from mokata.init import init_repo


# --------------------------------------------------------------------------------------
# Stub-server helpers — a fake `mokata-mcp` whose --version behaviour we control.
# --------------------------------------------------------------------------------------
def _stub_reporting(dirpath, version, name="stub_ok.py"):
    """A python script that prints `version` on `--version` and exits 0 (else exits 0 quietly)."""
    path = os.path.join(dirpath, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(
            "import sys\n"
            "if '--version' in sys.argv:\n"
            f"    sys.stdout.write({version!r} + '\\n')\n"
            "    sys.exit(0)\n"
            "sys.exit(0)\n"
        )
    return path


def _stub_no_version_flag(dirpath, name="stub_old.py"):
    """A fake OLD server that predates --version: argparse-style rejection (exit 2 on --version)."""
    path = os.path.join(dirpath, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(
            "import sys\n"
            "if '--version' in sys.argv:\n"
            "    sys.stderr.write('error: unrecognized arguments: --version\\n')\n"
            "    sys.exit(2)\n"
            "sys.exit(0)\n"
        )
    return path


def _stub_hangs(dirpath, name="stub_hang.py"):
    path = os.path.join(dirpath, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("import time\ntime.sleep(30)\n")
    return path


def _exec_stub(dirpath, body, name="mokata-mcp-stub"):
    """An EXECUTABLE stub (shebang + chmod +x) so it can be registered as a bare `command`
    with `args: []` — which is exactly what `_merge_mcp` writes. POSIX-only."""
    path = os.path.join(dirpath, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("#!/usr/bin/env python3\n" + body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


_EXEC_OK = "import sys\nif '--version' in sys.argv:\n    print({v!r})\n    sys.exit(0)\nsys.exit(0)\n"
_EXEC_OLD = ("import sys\n"
             "if '--version' in sys.argv:\n"
             "    sys.stderr.write('unrecognized arguments: --version\\n')\n"
             "    sys.exit(2)\n"
             "sys.exit(0)\n")


def _write_reg(root, command, args=None, home=None, scope="project"):
    """Write a mokata MCP registration into the scope's config file."""
    path = HS.claude_mcp_config_path(scope, root, home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "mcpServers": {HS.MCP_SERVER_NAME: {"command": command, "args": args or []}}
    }), encoding="utf-8")
    return path


# ======================================================================================
# 1. `mokata-mcp --version`
# ======================================================================================
class TestVersionFlag(unittest.TestCase):
    def test_version_flag_prints_version_and_exits_zero(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as ctx:
                M.main(["--version"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn(__version__, out.getvalue())

    def test_version_flag_works_without_sdk(self):
        # The probe target must never need MCP deps: --version must resolve BEFORE the SDK check,
        # so it works in a stripped env where the SDK is absent.
        original = M.mcp_available
        M.mcp_available = lambda: False
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                with self.assertRaises(SystemExit) as ctx:
                    M.main(["--version"])
        finally:
            M.mcp_available = original
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn(__version__, out.getvalue())

    def test_version_flag_via_subprocess(self):
        # End-to-end: the actual `python -m mokata.mcp_server --version` prints the version, exit 0,
        # with no hang and no MCP import needed.
        import subprocess
        res = subprocess.run([sys.executable, "-m", "mokata.mcp_server", "--version"],
                             capture_output=True, text=True, timeout=15)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn(__version__, res.stdout)


# ======================================================================================
# 2. Version-parity probe — the four named outcomes
# ======================================================================================
class TestVersionParityFinding(unittest.TestCase):
    def _finding(self, d, stub_path, cli_version=None):
        return mcp_admin.version_parity_for(
            sys.executable, [stub_path], source=Path(d) / ".mcp.json",
            cli_version=cli_version or __version__, timeout=5.0)

    def test_match_is_quiet_pass(self):
        with tempfile.TemporaryDirectory() as d:
            f = self._finding(d, _stub_reporting(d, __version__))
            self.assertEqual(f.status, "match")
            self.assertEqual(f.render(quiet_when_ok=True), [])   # quiet

    def test_mismatch_names_both_versions_and_paths(self):
        with tempfile.TemporaryDirectory() as d:
            stub = _stub_reporting(d, "9.9.9")
            f = self._finding(d, stub)
            self.assertEqual(f.status, "mismatch")
            self.assertEqual(f.registered_version, "9.9.9")
            self.assertEqual(f.cli_version, __version__)
            blob = "\n".join(f.render())
            # names BOTH versions
            self.assertIn("9.9.9", blob)
            self.assertIn(__version__, blob)
            # names BOTH sides: the registered command path AND the CLI's own env
            self.assertIn(stub, blob)
            self.assertIn(sys.executable, blob)

    def test_probe_failed_is_staleness_signal(self):
        with tempfile.TemporaryDirectory() as d:
            f = self._finding(d, _stub_no_version_flag(d))
            self.assertEqual(f.status, "probe_failed")
            blob = "\n".join(f.render()).lower()
            self.assertIn("stale", blob)   # the failure itself is named as the staleness signal

    def test_timeout_is_named(self):
        with tempfile.TemporaryDirectory() as d:
            f = mcp_admin.version_parity_for(
                sys.executable, [_stub_hangs(d)], source=Path(d) / ".mcp.json",
                cli_version=__version__, timeout=1.0)
            self.assertEqual(f.status, "timeout")
            self.assertTrue(f.render())    # a finding is printed, never silence

    def test_version_parity_resolves_registration(self):
        # The status/doctor entry point: resolves the registration itself, then classifies.
        with tempfile.TemporaryDirectory() as d:
            _write_reg(d, sys.executable, [_stub_reporting(d, "9.9.9")], home=d)
            f = mcp_admin.version_parity(root=d, home=d, timeout=5.0)
            self.assertEqual(f.status, "mismatch")

    def test_unregistered_is_quiet(self):
        # full_status/status_lines already report "NOT REGISTERED"; parity must not double up.
        with tempfile.TemporaryDirectory() as d:
            f = mcp_admin.version_parity(root=d, home=d, timeout=5.0)
            self.assertEqual(f.status, "unregistered")
            self.assertEqual(f.render(quiet_when_ok=False), [])


# ======================================================================================
# 2 (end-to-end). setup / mcp install surface the parity finding
# ======================================================================================
@unittest.skipIf(sys.platform.startswith("win"), "exec-stub shebang is POSIX-only")
class TestSetupSurfacesParity(unittest.TestCase):
    def _setup(self, d, resolved):
        original = HS.resolved_mcp_command
        HS.resolved_mcp_command = lambda: resolved
        lines = []
        try:
            res = HS.setup_harness("claude", root=d, scope="project", home=d,
                                   assume_yes=True, out=lines.append)
        finally:
            HS.resolved_mcp_command = original
        return res, lines

    def test_setup_mismatch_prints_loud_finding(self):
        with tempfile.TemporaryDirectory() as d:
            stub = _exec_stub(d, _EXEC_OK.format(v="9.9.9"))
            res, lines = self._setup(d, stub)
            self.assertEqual(res.message, "ok")     # probe never gates setup
            blob = "\n".join(lines)
            self.assertIn("MISMATCH", blob.upper())
            self.assertIn("9.9.9", blob)
            self.assertIn(__version__, blob)

    def test_setup_timeout_warns_but_succeeds(self):
        with tempfile.TemporaryDirectory() as d:
            stub = _exec_stub(d, "import time\ntime.sleep(30)\n")
            # a bounded probe must fail-open: setup completes, prints a warning, never hangs.
            res, lines = self._setup(d, stub)
            self.assertFalse(res.aborted)
            self.assertEqual(res.message, "ok")


class TestBVerRegression(unittest.TestCase):
    """test_b_ver_regression — the incident replay. A registration pointing at a fake OLD
    mokata-mcp (a different version / no --version) makes setup surface the MISMATCH /
    PROBE-FAILED finding naming both sides. Old code was SILENT here; this passes now."""

    @unittest.skipIf(sys.platform.startswith("win"), "exec-stub shebang is POSIX-only")
    def test_setup_surfaces_mismatch_for_stale_server(self):
        with tempfile.TemporaryDirectory() as d:
            stub = _exec_stub(d, _EXEC_OK.format(v="0.0.1"))    # a stale, older version
            original = HS.resolved_mcp_command
            HS.resolved_mcp_command = lambda: stub
            lines = []
            try:
                HS.setup_harness("claude", root=d, scope="project", home=d,
                                 assume_yes=True, out=lines.append)
            finally:
                HS.resolved_mcp_command = original
            blob = "\n".join(lines)
            # The guilty environment is NAMED — both versions and both command paths.
            self.assertIn("0.0.1", blob)
            self.assertIn(__version__, blob)
            self.assertIn(stub, blob)
            self.assertIn(sys.executable, blob)

    @unittest.skipIf(sys.platform.startswith("win"), "exec-stub shebang is POSIX-only")
    def test_setup_surfaces_probe_failed_for_pre_version_server(self):
        with tempfile.TemporaryDirectory() as d:
            stub = _exec_stub(d, _EXEC_OLD)     # predates --version
            original = HS.resolved_mcp_command
            HS.resolved_mcp_command = lambda: stub
            lines = []
            try:
                HS.setup_harness("claude", root=d, scope="project", home=d,
                                 assume_yes=True, out=lines.append)
            finally:
                HS.resolved_mcp_command = original
            blob = "\n".join(lines).lower()
            self.assertIn("probe", blob)
            self.assertIn("stale", blob)


# ======================================================================================
# 3. Both-scope sweep — scope shadowing
# ======================================================================================
class TestScopeShadow(unittest.TestCase):
    def test_both_scopes_flags_shadow_and_names_winner_and_loser(self):
        with tempfile.TemporaryDirectory() as d:
            root = os.path.join(d, "repo")
            home = os.path.join(d, "home")
            os.makedirs(root)
            os.makedirs(home)
            proj = _write_reg(root, "mokata-mcp", home=home, scope="project")
            usr = _write_reg(root, "mokata-mcp", home=home, scope="user")
            ss = mcp_admin.scope_shadow(root=root, home=home)
            self.assertTrue(ss.shadowed)
            blob = "\n".join(ss.render())
            # Claude Code uses the project one; the user one is the stale-shadow risk to clean.
            self.assertIn(str(proj), blob)
            self.assertIn(str(usr), blob)
            self.assertEqual(ss.winner_scope, "project")
            self.assertEqual(ss.loser_scope, "user")

    def test_no_user_file_means_no_shadow_warning(self):
        with tempfile.TemporaryDirectory() as d:
            root = os.path.join(d, "repo")
            home = os.path.join(d, "home")
            os.makedirs(root)
            os.makedirs(home)
            _write_reg(root, "mokata-mcp", home=home, scope="project")   # project only
            ss = mcp_admin.scope_shadow(root=root, home=home)
            self.assertFalse(ss.shadowed)
            self.assertEqual(ss.render(), [])


# ======================================================================================
# 4. Plugin shadowing
# ======================================================================================
class TestPluginShadow(unittest.TestCase):
    def _install_fake_plugin(self, home):
        proot = os.path.join(home, "plugin")
        os.makedirs(os.path.join(proot, ".claude-plugin"))
        Path(proot, ".claude-plugin", "plugin.json").write_text(json.dumps({
            "name": "mokata",
            "mcpServers": {HS.MCP_SERVER_NAME: {"command": "mokata-mcp", "args": []}},
        }), encoding="utf-8")
        from mokata import plugin_cache
        plugin_cache.record_plugin_root(proot, home=home)
        return proot

    def test_installed_plugin_is_warned(self):
        with tempfile.TemporaryDirectory() as home:
            proot = self._install_fake_plugin(home)
            ps = mcp_admin.plugin_shadow(home=home)
            self.assertIsNotNone(ps)
            blob = "\n".join(ps.render()).lower()
            self.assertIn("plugin", blob)
            self.assertIn(proot.lower(), blob)
            self.assertIn("pip", blob)      # names the pip-upgrade-won't-help hazard

    def test_no_plugin_is_silent(self):
        with tempfile.TemporaryDirectory() as home:
            self.assertIsNone(mcp_admin.plugin_shadow(home=home))


# ======================================================================================
# 5. Restart hint on EVERY registration-writing path
# ======================================================================================
class TestRestartHintOnEveryWritePath(unittest.TestCase):
    def test_setup_prints_restart_hint(self):
        with tempfile.TemporaryDirectory() as d:
            lines = []
            HS.setup_harness("claude", root=d, scope="project", home=d,
                             assume_yes=True, out=lines.append)
            self.assertTrue(any("restart claude code" in ln.lower() for ln in lines),
                            "setup writes a registration — it must print a restart hint")

    def test_mcp_install_prints_restart_hint(self):
        from mokata.cli_commands.mcp import _cmd_mcp_install
        with tempfile.TemporaryDirectory() as d:
            ns = argparse.Namespace(path=d, home=d, scope="project", no_grant=False)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = _cmd_mcp_install(ns)
            self.assertEqual(rc, 0)
            self.assertIn("restart claude code", out.getvalue().lower())


# ======================================================================================
# 6. Shared probe exposed in `mcp status` AND `doctor` (ONE shared function)
# ======================================================================================
class TestSharedInStatusAndDoctor(unittest.TestCase):
    def _status_output(self, d):
        from mokata.cli_commands.mcp import _cmd_mcp_status
        _write_reg(d, sys.executable, [_stub_reporting(d, "9.9.9")], home=d)
        ns = argparse.Namespace(path=d, home=d)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            _cmd_mcp_status(ns)
        return out.getvalue() + err.getvalue()

    def _doctor_output(self, d):
        from mokata.cli_commands.diagnostics import cmd_doctor
        init_repo(root=d, assume_yes=True, out=lambda *_: None)
        _write_reg(d, sys.executable, [_stub_reporting(d, "9.9.9")], home=d)
        ns = argparse.Namespace(path=d, home=d, matrix=False)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cmd_doctor(ns)
        return out.getvalue()

    def test_mcp_status_shows_parity(self):
        with tempfile.TemporaryDirectory() as d:
            blob = self._status_output(d)
            self.assertIn("MISMATCH", blob.upper())
            self.assertIn("9.9.9", blob)

    def test_doctor_shows_parity(self):
        with tempfile.TemporaryDirectory() as d:
            blob = self._doctor_output(d)
            self.assertIn("MISMATCH", blob.upper())
            self.assertIn("9.9.9", blob)

    def test_status_and_doctor_wording_cannot_drift(self):
        # Proof they share ONE reporter: the parity line is identical across both surfaces.
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            s = self._status_output(d1)
            doc = self._doctor_output(d2)

            def _mismatch_line(blob):
                for ln in blob.splitlines():
                    if "MISMATCH" in ln.upper():
                        return ln.strip()
                return None
            self.assertIsNotNone(_mismatch_line(s))
            self.assertEqual(_mismatch_line(s), _mismatch_line(doc))


# ======================================================================================
# 7. No behaviour change — probe is additive output only
# ======================================================================================
class TestNoBehaviourChange(unittest.TestCase):
    def test_registration_file_is_byte_identical(self):
        # The registration `_merge_mcp` writes is unchanged by this stage.
        with tempfile.TemporaryDirectory() as d:
            HS.setup_harness("claude", root=d, scope="project", home=d,
                             assume_yes=True, out=lambda *_: None)
            data = json.loads((Path(d) / ".mcp.json").read_text(encoding="utf-8"))
            self.assertEqual(data["mcpServers"][HS.MCP_SERVER_NAME],
                             {"command": HS.resolved_mcp_command(), "args": []})

    def test_matching_version_setup_adds_no_parity_noise(self):
        # A clean MATCH with no shadow/plugin is a quiet pass: parity_lines emits nothing, so
        # setup output is unchanged. (Uses the real resolved mokata-mcp, which matches this CLI.)
        with tempfile.TemporaryDirectory() as d:
            lines = []
            HS.setup_harness("claude", root=d, scope="project", home=d,
                             assume_yes=True, out=lines.append)
            blob = "\n".join(lines)
            self.assertNotIn("MISMATCH", blob.upper())
            self.assertNotIn("PROBE", blob.upper())
            # CONNECTED verification (prior stage) still present — not displaced by parity.
            self.assertTrue(any(ln.startswith("mokata-mcp:") for ln in lines))

    def test_parity_lines_never_raises(self):
        # Even a garbage registration yields lines, never an exception (fail-open reporter).
        with tempfile.TemporaryDirectory() as d:
            _write_reg(d, "definitely-not-a-real-binary-xyz", home=d)
            self.assertIsInstance(mcp_admin.parity_lines(root=d, home=d), list)


# ======================================================================================
# Secret-safety — the probe subprocess env + findings carry no DSN/secret values
# ======================================================================================
class TestSecretSafety(unittest.TestCase):
    # A distinctive fake connection value, assembled from parts so no credential-bearing literal
    # sits in this source file (that would trip mokata's own secret-guard). We prove this value
    # NEVER reaches a finding or the probe subprocess env.
    _FAKE_VALUE = "postgres://" + "u" + "@" + "db.invalid" + "/mokata-b-ver-canary"
    _DSN_ENV_KEY = "MOKATA_" + "DSN"

    def test_findings_contain_no_secret(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ[self._DSN_ENV_KEY] = self._FAKE_VALUE
            try:
                f = mcp_admin.version_parity_for(
                    sys.executable, [_stub_reporting(d, "9.9.9")],
                    source=Path(d) / ".mcp.json", cli_version=__version__, timeout=5.0)
                self.assertNotIn(self._FAKE_VALUE, "\n".join(f.render()))
            finally:
                os.environ.pop(self._DSN_ENV_KEY, None)

    def test_probe_subprocess_env_is_scrubbed(self):
        # A stub that records what it saw for the DSN key: the probe must scrub secret-shaped keys
        # from the env it hands the subprocess.
        with tempfile.TemporaryDirectory() as d:
            sidecar = os.path.join(d, "seen.txt")
            key = self._DSN_ENV_KEY
            stub = os.path.join(d, "stub_env.py")
            with open(stub, "w", encoding="utf-8") as fh:
                fh.write(
                    "import os, sys\n"
                    f"open({sidecar!r}, 'w').write(os.environ.get({key!r}, 'NONE'))\n"
                    "if '--version' in sys.argv:\n"
                    "    print('9.9.9')\n"
                    "sys.exit(0)\n"
                )
            os.environ[key] = self._FAKE_VALUE
            try:
                mcp_admin.version_parity_for(
                    sys.executable, [stub], source=Path(d) / ".mcp.json",
                    cli_version=__version__, timeout=5.0)
            finally:
                os.environ.pop(key, None)
            self.assertEqual(Path(sidecar).read_text(encoding="utf-8"), "NONE")


if __name__ == "__main__":
    unittest.main()
