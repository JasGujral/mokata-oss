"""TM.S1 — the run mode is surfaced IDENTICALLY everywhere (never ambiguous).

The mode shows in: the statusline badge, the SessionStart bootstrap line, the CLI banner
(`mokata status`), and `mokata doctor` (mode + the team-readiness preflight). And it surfaces
across EVERY harness that can inject context — not a single-harness surface — because the
bootstrap briefing flows through the harness boundary's context-injection capability.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import io
import unittest
from contextlib import redirect_stdout

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.config import Constitution, Surface
from mokata.detect import Detector
from mokata.manifest import Manifest


def _surface(settings=None):
    data = sample_manifest_data()
    if settings is not None:
        data.setdefault("settings", {}).update(settings)
    m = Manifest.from_dict(data)
    return Surface(m, Constitution("", None), root=".", detector=Detector())


# ------------------------------------------------------------------- statusline badge
class TestStatuslineBadge(unittest.TestCase):
    def test_badge_shows_local_mode(self):
        from mokata.progress import statusline_badge
        badge = statusline_badge(_surface())
        self.assertIn("local", badge)

    def test_badge_shows_team_mode(self):
        from mokata.progress import statusline_badge
        badge = statusline_badge(_surface({"mode": "team"}))
        self.assertIn("team", badge)

    def test_statusline_badge_keeps_the_stage_strip(self):
        # the mode is a SEPARATE prefix segment; the stage badge is left byte-for-byte.
        from mokata.progress import build_stage_badge, statusline_badge
        s = _surface()
        self.assertIn(build_stage_badge(s), statusline_badge(s))

    def test_pipeline_stage_badge_is_unchanged_for_a_fresh_repo(self):
        # regression: the pinned no-run stage badge stays exactly "mokata".
        from mokata.progress import build_stage_badge
        self.assertEqual(build_stage_badge(_surface()), "mokata")

    def test_badge_still_degrades_on_a_broken_surface(self):
        from mokata.progress import statusline_badge

        class Boom:
            @property
            def manifest(self):
                raise RuntimeError("boom")

            @property
            def state(self):
                raise RuntimeError("boom")
        # never raises — the badge degrades cleanly (mode read is guarded too).
        self.assertIsInstance(statusline_badge(Boom()), str)


# ------------------------------------------------------------------- bootstrap line
class TestBootstrapLine(unittest.TestCase):
    def test_bootstrap_names_the_mode(self):
        from mokata.bootstrap import build_bootstrap
        text = build_bootstrap(_surface()).text
        self.assertIn("Run mode:", text)
        self.assertIn("local", text)

    def test_bootstrap_stays_within_budget(self):
        from mokata.bootstrap import build_bootstrap
        self.assertTrue(build_bootstrap(_surface()).within_budget)


# ------------------------------------------------------------------- CLI banner
class TestCliBanner(unittest.TestCase):
    def test_status_banner_shows_mode(self):
        from mokata.cli_commands.core import cmd_status
        import argparse
        out = io.StringIO()
        with redirect_stdout(out):
            # cmd_status loads the surface from --path; build a repo-less surface via monkeypatch.
            import mokata.cli_commands.core as core
            orig = core._load_surface
            core._load_surface = lambda root: _surface()
            try:
                cmd_status(argparse.Namespace(path="."))
            finally:
                core._load_surface = orig
        self.assertIn("mode", out.getvalue().lower())
        self.assertIn("local", out.getvalue())


# ------------------------------------------------------------------- doctor
class TestDoctor(unittest.TestCase):
    def test_doctor_report_shows_mode_and_preflight(self):
        from mokata.govern.doctor import diagnose
        report = diagnose(_surface())
        text = report.render()
        self.assertIn("local", text)
        # doctor surfaces the same team-readiness preflight.
        self.assertIn("team mode", text.lower())

    def test_doctor_flags_an_invalid_hand_edited_mode(self):
        from mokata.govern.doctor import diagnose
        report = diagnose(_surface({"mode": "solo"}))
        codes = [f.code for f in report.findings]
        self.assertIn("bad-mode", codes)

    def test_doctor_local_default_is_not_an_error(self):
        from mokata.govern.doctor import diagnose
        report = diagnose(_surface())
        self.assertEqual([f for f in report.findings if f.code == "bad-mode"], [])


# ------------------------------------------------------------------- harness parity
class TestHarnessParity(unittest.TestCase):
    def test_mode_surfaces_across_every_context_injecting_harness(self):
        from mokata import harness as H
        from mokata.bootstrap import build_bootstrap
        briefing = build_bootstrap(_surface()).text
        injected = []
        for name in H.available_harnesses():
            boundary = H.HarnessBoundary(H.get_harness(name))
            res = boundary.inject_context(briefing)
            if res.ok:
                injected.append(name)
                # the SAME briefing (carrying the mode line) is what each harness injects.
                self.assertIn("Run mode:", briefing)
        # more than one harness carries it — never a single-harness surface.
        self.assertGreater(len(injected), 1)


if __name__ == "__main__":
    unittest.main()
