"""K5 — `mokata doctor`: diagnose the manifest/config (missing providers, broken
adapters, role conflicts, bad trust levels)."""

import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.config import Constitution, Surface
from mokata.detect import Detector
from mokata.govern import diagnose
from mokata.manifest import Manifest


def surface_with_problems():
    data = {
        "manifest_version": 1, "mokata": {"version": "0.1.0"}, "profile": "custom",
        "layers": {"engine": {"enabled": True}, "knowledge": {"enabled": True},
                   "memory": {"enabled": True}, "governance": {"enabled": True}},
        "capabilities": {
            "code_graph": {"description": "g", "layer": "knowledge",
                           "fallback": ["toolA", "toolB"]},   # 2 providers (conflict)
        },
        "tools": {
            "toolA": {"provides": "code_graph", "kind": "mcp", "version": None,
                      "enabled": True, "detect": {"type": "command", "name": "nope-a"}},
            "toolB": {"provides": "code_graph", "kind": "cli", "version": None,
                      "enabled": True, "detect": {"type": "command", "name": "nope-b"}},
        },
        "settings": {"trust": {"toolA": "bogus-level"}},
    }
    m = Manifest.from_dict(data)
    det = Detector(overrides={"toolA": False, "toolB": False})   # both absent
    return Surface(m, Constitution("", None), root=".", detector=det)


def surface_with_degraded():
    """A capability whose PREFERRED provider is absent but a fallback IS present — the router
    resolves it available-but-degraded (no diagnose() error, a finer 'degraded' in the matrix)."""
    data = {
        "manifest_version": 1, "mokata": {"version": "0.1.0"}, "profile": "custom",
        "layers": {"engine": {"enabled": True}, "knowledge": {"enabled": True},
                   "memory": {"enabled": True}, "governance": {"enabled": True}},
        "capabilities": {
            "code_graph": {"description": "g", "layer": "knowledge",
                           "fallback": ["toolA", "toolB"]},
        },
        "tools": {
            "toolA": {"provides": "code_graph", "kind": "cli", "version": None,
                      "enabled": True, "detect": {"type": "command", "name": "nope-a"}},
            "toolB": {"provides": "code_graph", "kind": "cli", "version": None,
                      "enabled": True, "detect": {"type": "command", "name": "yes-b"}},
        },
    }
    m = Manifest.from_dict(data)
    det = Detector(overrides={"toolA": False, "toolB": True})  # preferred absent, fallback present
    return Surface(m, Constitution("", None), root=".", detector=det)


class TestDoctorTableRendering(unittest.TestCase):
    """RT.S2 A4 — the doctor report routes through the shared box/table + colour helpers.
    Presentation only: the same findings, the same content, the same ok/exit semantics."""

    ESC = "\x1b["

    def test_render_content_unchanged_findings_present(self):
        report = diagnose(surface_with_problems())
        out = report.render()
        # every finding's severity / code / detail still appears verbatim
        for f in report.findings:
            self.assertIn(f.code, out)
            self.assertIn(f.detail, out)
        self.assertIn("PROBLEMS FOUND", out)

    def test_render_routes_through_table_helper(self):
        report = diagnose(surface_with_problems())
        out = report.render()                       # default: unicode box table
        self.assertIn("│", out)                     # column separator from legibility.table
        self.assertIn("─", out)

    def test_ascii_only_has_no_escapes_and_no_unicode_box(self):
        report = diagnose(surface_with_problems())
        out = report.render(ascii_only=True)
        self.assertNotIn(self.ESC, out)
        for ch in "┌┐└┘─│┼┬┴├┤":
            self.assertNotIn(ch, out)
        self.assertIn("|", out)                     # ascii column separator instead
        # content still intact
        self.assertTrue(any(f.detail in out for f in report.findings))

    def test_default_render_emits_no_escape_codes(self):
        # no color unless a caller explicitly asks (deterministic; safe for pipes/MCP/JSON)
        self.assertNotIn(self.ESC, diagnose(surface_with_problems()).render())

    def test_color_render_emits_escapes_for_status(self):
        out = diagnose(surface_with_problems()).render(color=True)
        self.assertIn(self.ESC, out)                # red PROBLEMS FOUND

    def test_clean_report_still_reads_all_checks_passed(self):
        from mokata.govern import DoctorReport
        report = DoctorReport(findings=[])              # zero findings -> the pass line
        self.assertIn("all checks passed", report.render())
        self.assertIn("all checks passed", report.render(ascii_only=True))
        self.assertNotIn(self.ESC, report.render(ascii_only=True))


class TestDoctor(unittest.TestCase):
    def test_flags_missing_provider(self):
        report = diagnose(surface_with_problems())
        self.assertFalse(report.ok)
        self.assertTrue(any(f.code == "missing-provider" for f in report.findings))

    def test_flags_role_conflict(self):
        report = diagnose(surface_with_problems())
        conflicts = [f for f in report.findings if f.code == "role-conflict"]
        self.assertTrue(conflicts)
        self.assertIn("code_graph", conflicts[0].detail)

    def test_flags_bad_trust_level(self):
        report = diagnose(surface_with_problems())
        self.assertTrue(any(f.code == "bad-trust" for f in report.findings))

    def test_clean_manifest_is_ok(self):
        from mokata.profiles import build_manifest_data
        m = Manifest.from_dict(build_manifest_data("standard", "0.1.0"))
        surface = Surface(m, Constitution("# c\n## A\n", None), root=".",
                          detector=Detector())
        report = diagnose(surface)
        # standard resolves code_graph (grep floor) + memory_store (sqlite) -> no errors
        self.assertTrue(report.ok)


class TestDoctorCoverageMatrix(unittest.TestCase):
    """RT.S3b (R13) — doctor gains a COMPLETE capability coverage matrix: every declared
    capability + wiring point classified pass / degraded / fail, using the SAME resolver
    diagnose() uses (one source of truth). Rendered through the A4 legibility helpers."""

    ESC = "\x1b["

    def test_matrix_covers_all_harness_capabilities(self):
        # completeness: every HARNESS_CAPABILITIES wiring point is a row (fails if one is missing)
        from mokata.govern import coverage_matrix
        from mokata.harness import HARNESS_CAPABILITIES
        mx = coverage_matrix(surface_with_problems())
        wired = {r.name for r in mx.rows if r.kind == "wiring"}
        for cap in HARNESS_CAPABILITIES:
            self.assertIn(cap, wired)

    def test_matrix_covers_all_manifest_needs(self):
        # completeness: every declared capability need is a row
        from mokata.govern import coverage_matrix
        surface = surface_with_problems()
        capped = {r.name for r in coverage_matrix(surface).rows if r.kind == "capability"}
        for need in surface.manifest.capabilities:
            self.assertIn(need, capped)

    def test_missing_provider_shows_fail_matching_diagnose(self):
        from mokata.govern import coverage_matrix
        surface = surface_with_problems()
        row = next(r for r in coverage_matrix(surface).rows
                   if r.kind == "capability" and r.name == "code_graph")
        self.assertEqual(row.status, "fail")
        # one source of truth: diagnose() flags the SAME capability as an error
        report = diagnose(surface)
        self.assertTrue(any(f.severity == "error" and "code_graph" in f.detail
                            for f in report.findings))

    def test_degraded_capability_shows_degraded_matching_diagnose(self):
        from mokata.govern import coverage_matrix
        surface = surface_with_degraded()
        row = next(r for r in coverage_matrix(surface).rows
                   if r.kind == "capability" and r.name == "code_graph")
        self.assertEqual(row.status, "degraded")
        # available-but-degraded is NOT a fail: diagnose() raises no missing/bad-capability error
        report = diagnose(surface)
        self.assertFalse(any(f.code in ("missing-provider", "bad-capability")
                             and "code_graph" in f.detail for f in report.findings))

    def test_reference_harness_wiring_all_pass(self):
        from mokata.govern import coverage_matrix
        for r in coverage_matrix(surface_with_degraded()).rows:
            if r.kind == "wiring":
                self.assertEqual(r.status, "pass")

    def test_status_values_are_pass_degraded_fail_only(self):
        from mokata.govern import coverage_matrix
        for surf in (surface_with_problems(), surface_with_degraded()):
            for r in coverage_matrix(surf).rows:
                self.assertIn(r.status, ("pass", "degraded", "fail"))

    def test_matrix_ascii_only_has_zero_escapes_and_no_unicode_box(self):
        from mokata.govern import coverage_matrix
        out = coverage_matrix(surface_with_problems()).render(ascii_only=True)
        self.assertNotIn(self.ESC, out)
        for ch in "┌┐└┘─│┼┬┴├┤":
            self.assertNotIn(ch, out)
        self.assertIn("|", out)              # ascii column separator instead

    def test_matrix_default_render_emits_no_escapes(self):
        from mokata.govern import coverage_matrix
        self.assertNotIn(self.ESC, coverage_matrix(surface_with_problems()).render())

    def test_matrix_color_emits_escapes_for_status(self):
        from mokata.govern import coverage_matrix
        out = coverage_matrix(surface_with_problems()).render(color=True)
        self.assertIn(self.ESC, out)

    def test_matrix_render_routes_through_table_helper(self):
        from mokata.govern import coverage_matrix
        out = coverage_matrix(surface_with_problems()).render()  # default: unicode box table
        self.assertIn("│", out)
        self.assertIn("─", out)


class TestDoctorMatrixCLI(unittest.TestCase):
    """The `--matrix` flag surfaces the full matrix WITHOUT changing doctor's ok/exit semantics
    (exit stays derived from diagnose(), not the matrix) and doctor stays READ-ONLY."""

    def _run(self, root, matrix):
        import argparse
        import contextlib
        import io
        from mokata.cli_commands.diagnostics import cmd_doctor
        ns = argparse.Namespace(path=root, matrix=matrix)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cmd_doctor(ns)
        return rc, buf.getvalue()

    def test_matrix_flag_does_not_change_exit_code(self):
        import tempfile
        from mokata.init import init_repo
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
            rc_plain, out_plain = self._run(d, False)
            rc_matrix, out_matrix = self._run(d, True)
            self.assertEqual(rc_plain, rc_matrix)
            self.assertIn("coverage matrix", out_matrix)
            self.assertNotIn("coverage matrix", out_plain)


class TestDoctorCalibrationDrift(unittest.TestCase):
    """RT.S3c (R11) — doctor flags token-estimate calibration DRIFT (a logged real `actual`
    that blew past the chars/4 estimate's safety margin) and stays silent within margin.
    Read-only; reuses the ledger + the R13 finding path."""

    def _repo(self):
        import tempfile
        from mokata.init import init_repo
        d = tempfile.mkdtemp()
        init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
        return Surface.load(d)

    def test_over_margin_actual_is_flagged(self):
        from mokata.govern import AuditLedger, diagnose, log_calibration
        surface = self._repo()
        led = AuditLedger.from_mokata_dir(surface.mokata_dir)
        log_calibration(led, "bootstrap", estimate=100, actual=140)   # margin blown
        report = diagnose(surface)
        drift = [f for f in report.findings if f.code == "calibration-drift"]
        self.assertTrue(drift)
        self.assertIn("bootstrap", drift[0].detail)

    def test_within_margin_actual_is_silent(self):
        from mokata.govern import AuditLedger, diagnose, log_calibration
        surface = self._repo()
        led = AuditLedger.from_mokata_dir(surface.mokata_dir)
        log_calibration(led, "bootstrap", estimate=100, actual=70)    # estimate ran high (good)
        report = diagnose(surface)
        self.assertFalse(any(f.code == "calibration-drift" for f in report.findings))

    def test_no_calibration_records_no_drift_finding(self):
        from mokata.govern import diagnose
        surface = self._repo()
        report = diagnose(surface)
        self.assertFalse(any(f.code == "calibration-drift" for f in report.findings))


if __name__ == "__main__":
    unittest.main()
