"""Stage 54e — full Claude-Code command parity (enforced).

Every USER-FACING mokata CLI command must be reachable from inside Claude Code — a
`/mokata:` slash command and/or a native MCP tool — OR be an explicitly exempted piece of
install/diagnostic plumbing. The coverage matrix in `mokata.parity` is the single source of
truth; this test is the CI drift guard that makes the rule self-enforcing:

  * the CLI command set is DERIVED from the live argparse parser (not hand-listed);
  * every command has a declared surface OR an explicit exemption — else the guard FAILS,
    naming the offender (proven genuinely-failing by planting a fake un-surfaced command);
  * every MCP tool the matrix references actually exists and is registered read/write as
    declared;
  * the Stage 54e new READ tools are read-only; the new WRITE tools are human-gated
    (propose-only without approval; a secret is hard-blocked even when approved);
  * each new slash template exists, is namespaced, and is marker-prefixed.
"""

import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import mcp_server as M
from mokata import parity
from mokata.config import Surface

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMMANDS_DIR = os.path.join(ROOT, "templates", "commands")

# The install/diagnostic plumbing intentionally CLI-or-hook. `setup` was here at Stage 54e but
# gained an in-harness surface (the /mokata:setup guided wizard) at Stage 56, so it's no longer
# exempt — the rest remain shell/hook-only plumbing. `release-check` (Stage 61b) is release
# plumbing run from the shell/CI during a release cut — the version mirror of `validate`.
EXEMPT = {"unsetup", "mcp", "harness", "route", "detect", "validate", "bootstrap",
          "release-check", "bench"}
# The new in-harness surfaces this stage adds.
NEW_READ_TOOLS = ("rules", "skills", "suggest", "lat_check", "index_status",
                  "baseline", "sessions", "config_get", "export_preview")
NEW_WRITE_TOOLS = ("config_set", "export_stack")
NEW_SLASH = ("enter", "exec", "chain", "playbook", "resume", "upgrade", "skill")

# Assembled from fragments so no literal credential string lives in this file (mokata's own
# secret-guard hook would otherwise block writing/committing it — exactly the thing under test).
_SECRET_DSN = "postgres://app_user" + ":" + "tok3n_pw" + "@" + "db.internal:5432/app"


def _repo(d, profile="standard"):
    from mokata.init import init_repo
    init_repo(root=d, profile=profile, assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _state_snapshot(surface):
    root = surface.state.root
    return sorted(os.listdir(root)) if os.path.isdir(root) else []


# ===================================================================== the parity guard
class TestParityGuard(unittest.TestCase):
    def test_every_cli_command_is_declared(self):
        cli = parity.cli_command_names()
        self.assertTrue(cli, "no subcommands parsed — parser shape changed?")
        missing = sorted(cli - set(parity.SURFACE_MATRIX))
        self.assertEqual(missing, [],
                         f"CLI commands with no entry in the surface matrix: {missing}")

    def test_no_stale_matrix_entries(self):
        stale = sorted(set(parity.SURFACE_MATRIX) - parity.cli_command_names())
        self.assertEqual(stale, [],
                         f"matrix entries for commands that don't exist: {stale}")

    def test_every_command_is_reachable_or_exempt(self):
        report = parity.verify_parity()
        self.assertTrue(report.ok, report.render())

    def test_report_renders_ok(self):
        self.assertIn("parity OK", parity.verify_parity().render())

    def test_exempt_set_matches_the_classified_plumbing(self):
        exempt = {n for n, s in parity.SURFACE_MATRIX.items() if s.exempt}
        self.assertEqual(exempt, EXEMPT,
                         "the exempted plumbing set drifted from the Stage 54e classification")

    def test_every_exemption_has_a_rationale(self):
        for name in EXEMPT:
            reason = parity.SURFACE_MATRIX[name].exempt
            self.assertTrue(reason and len(reason) > 20,
                            f"exemption for '{name}' lacks a real one-line rationale")

    def test_exempt_commands_have_no_in_harness_surface(self):
        # An exemption means intentionally CLI/hook-only — never also a silent surface.
        for name in EXEMPT:
            self.assertFalse(parity.SURFACE_MATRIX[name].in_harness,
                             f"'{name}' is exempt yet also declares an in-harness surface")

    # ---- the proof: the guard genuinely FAILS on an un-surfaced command, then passes ----
    def test_guard_fails_on_a_planted_unsurfaced_command(self):
        original = parity.cli_command_names
        try:
            parity.cli_command_names = lambda: original() | {"frobnicate"}
            report = parity.verify_parity()
            self.assertFalse(report.ok, "guard MUST fail on an un-surfaced CLI command")
            self.assertIn("frobnicate", report.undeclared)
            self.assertIn("frobnicate", report.render())
        finally:
            parity.cli_command_names = original
        # revert → green again (not a no-op guard)
        self.assertTrue(parity.verify_parity().ok)

    def test_guard_fails_on_a_declared_but_unreachable_command(self):
        # A matrix entry with neither a surface nor an exemption is a silent gap → caught.
        real = next(iter(parity.cli_command_names()))
        bare = parity.CommandSurface(real)          # no slash, no mcp, no exempt
        self.assertFalse(bare.covered)
        saved = parity.SURFACE_MATRIX[real]
        try:
            parity.SURFACE_MATRIX[real] = bare
            report = parity.verify_parity()
            self.assertFalse(report.ok)
            self.assertIn(real, report.uncovered)
        finally:
            parity.SURFACE_MATRIX[real] = saved
        self.assertTrue(parity.verify_parity().ok)


# ===================================================================== matrix <-> reality
class TestMatrixMatchesReality(unittest.TestCase):
    def test_declared_mcp_tools_all_exist(self):
        declared = parity.declared_mcp_tools()
        registered = set(M.tool_names())
        missing = sorted(declared - registered)
        self.assertEqual(missing, [],
                         f"matrix references MCP tools that aren't registered: {missing}")

    def test_declared_read_tools_are_registered_read(self):
        reads = set(M.read_tool_names())
        for s in parity.SURFACE_MATRIX.values():
            for t in s.mcp_read:
                self.assertIn(t, reads, f"{t} declared read but not a registered read tool")

    def test_declared_write_tools_are_registered_write(self):
        writes = set(M.write_tool_names())
        for s in parity.SURFACE_MATRIX.values():
            for t in s.mcp_write:
                self.assertIn(t, writes, f"{t} declared write but not a registered write tool")

    def test_new_surfaces_are_present(self):
        for t in NEW_READ_TOOLS:
            self.assertIn(t, M.read_tool_names())
        for t in NEW_WRITE_TOOLS:
            self.assertIn(t, M.write_tool_names())


# ===================================================================== new READ tools
class TestNewReadTools(unittest.TestCase):
    def test_rules(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            res = M.rules(path=d)
            self.assertIn("tiers", res)
            self.assertIsInstance(res["proposals"], list)

    def test_skills_catalog_and_detail(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            cat = M.skills(path=d)
            self.assertTrue(cat["skills"])
            detail = M.skills(path=d, name="spec")
            self.assertEqual(detail["name"], "spec")
            self.assertIn("gate", detail)

    def test_suggest_never_runs(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            res = M.suggest(path=d, fresh=True)
            self.assertIn("suggestions", res)

    def test_lat_check(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            res = M.lat_check(path=d)
            self.assertIn("has_drift", res)

    def test_index_status_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            before = _state_snapshot(surface)
            res = M.index_status(path=d)
            self.assertIn("built", res)
            self.assertIn("uses_graph", res)
            # read-only: status (a diff) must NOT create/refresh the persisted index
            self.assertEqual(before, _state_snapshot(surface))

    def test_baseline_degrades_clean(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            res = M.baseline(path=d)
            self.assertIn("report", res)

    def test_sessions_empty_state(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            res = M.sessions(path=d)
            self.assertEqual(res["count"], 0)
            self.assertEqual(res["sessions"], [])

    def test_export_preview_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            from mokata import MOKATA_DIR
            from mokata.share import SHARE_FILENAME
            _repo(d)
            dest = os.path.join(d, MOKATA_DIR, SHARE_FILENAME)
            res = M.export_preview(path=d)
            self.assertEqual(res["dest"], dest)
            self.assertIn("preview", res)
            self.assertFalse(os.path.exists(dest), "preview must not write the stack file")

    def test_config_get(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            res = M.config_get(path=d, key="profile")
            self.assertTrue(res["found"])
            self.assertEqual(res["value"], "standard")


# ===================================================================== new WRITE tools
class TestConfigSetGated(unittest.TestCase):
    def test_propose_only_without_approval(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            res = M.config_set(path=d, key="settings.ux.progress", value="dashboard")
            self.assertEqual(res["status"], "proposed")
            self.assertNotEqual(res.get("committed"), True)
            # nothing was written
            self.assertFalse(M.config_get(path=d, key="settings.ux.progress")["found"])

    def test_approve_commits(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            res = M.config_set(path=d, key="settings.ux.progress", value="dashboard",
                               approve=True)
            self.assertTrue(res["committed"])
            got = M.config_get(path=d, key="settings.ux.progress")
            self.assertEqual(got["value"], "dashboard")

    def test_secret_is_hard_blocked_even_when_approved(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            res = M.config_set(path=d, key="tools.pg.config.dsn", value=_SECRET_DSN,
                               approve=True)
            self.assertFalse(res.get("committed", False))
            self.assertEqual(res["status"], "blocked")
            self.assertTrue(res["findings"])
            # the secret never reached the manifest
            self.assertFalse(M.config_get(path=d, key="tools.pg.config.dsn")["found"])


class TestExportStackGated(unittest.TestCase):
    def _dest(self, d):
        from mokata import MOKATA_DIR
        from mokata.share import SHARE_FILENAME
        return os.path.join(d, MOKATA_DIR, SHARE_FILENAME)

    def test_propose_only_without_approval(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            res = M.export_stack(path=d)
            self.assertEqual(res["status"], "proposed")
            self.assertFalse(os.path.exists(self._dest(d)))

    def test_approve_writes_the_stack(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            res = M.export_stack(path=d, approve=True)
            self.assertTrue(res["committed"])
            self.assertTrue(os.path.exists(self._dest(d)))


# ===================================================================== new slash templates
class TestNewSlashTemplates(unittest.TestCase):
    MARKER = "mokata ·"

    def _read(self, name):
        with open(os.path.join(COMMANDS_DIR, f"{name}.md"), encoding="utf-8") as fh:
            return fh.read()

    def test_each_template_exists(self):
        for name in NEW_SLASH:
            self.assertTrue(os.path.exists(os.path.join(COMMANDS_DIR, f"{name}.md")),
                            f"{name}.md missing")

    def test_namespaced_and_marker_prefixed(self):
        for name in NEW_SLASH:
            md = self._read(name)
            self.assertIn(f"name: {name}", md)
            self.assertIn(f"description: {self.MARKER}", md)


if __name__ == "__main__":
    unittest.main()
