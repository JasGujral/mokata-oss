"""TM.S5 — the ONE health surface renders identically in badge / mode / doctor / in-chat.

All four surfaces read the SAME cached verdict (doc 48 P-11): a troubled connection shows ⚠ in
the badge and OFFLINE + a work-locally offer everywhere else; a healthy one shows no warning.
Local mode is completely unaffected (no health, no ⚠).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

import _support  # noqa: F401

from mokata import MANIFEST_FILENAME, MOKATA_DIR, run_mode, team_health, teamdb
from mokata.config import Surface
from mokata.init import init_repo


def _repo(d, mode="team"):
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    if mode is not None:
        path = os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME)
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        data.setdefault("settings", {})["mode"] = mode
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    return Surface.load(d)


def _cache_trouble(surface):
    env = {run_mode.CREDENTIAL_ENV: "postgres://x"}
    team_health.check(surface, environ=env,
                      probe=lambda dsn: teamdb.ProbeResult(reachable=False, compatible=False,
                                                           detail="unreachable — connect refused"))


def _cache_healthy(surface):
    env = {run_mode.CREDENTIAL_ENV: "postgres://x"}
    team_health.check(surface, environ=env,
                      probe=lambda dsn: teamdb.ProbeResult(
                          reachable=True, schema_present=True,
                          schema_version=teamdb.TEAM_SCHEMA_VERSION, compatible=True,
                          elapsed_ms=9.0, detail="reachable + schema v2 compatible"))


class TestBadge(unittest.TestCase):
    def test_badge_shows_warning_on_trouble(self):
        from mokata.progress import statusline_badge
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _cache_trouble(surface)
            self.assertIn("⚠", statusline_badge(surface))
            self.assertIn("[!]", statusline_badge(surface, ascii_only=True))

    def test_badge_clean_when_healthy(self):
        from mokata.progress import statusline_badge
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _cache_healthy(surface)
            self.assertNotIn("⚠", statusline_badge(surface))
            self.assertIn("team", statusline_badge(surface))

    def test_local_badge_never_warns(self):
        from mokata.progress import statusline_badge
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, mode=None)
            self.assertNotIn("⚠", statusline_badge(surface))
            self.assertIn("local", statusline_badge(surface))


class TestDoctorAndChat(unittest.TestCase):
    def test_doctor_surfaces_the_same_verdict(self):
        from mokata.govern.doctor import diagnose
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            # pre-seed the cache with trouble; doctor reuses the fresh cache (within TTL) so the
            # surfaced verdict is deterministic without a live DB.
            _cache_trouble(surface)
            os.environ.pop(run_mode.CREDENTIAL_ENV, None)
            report = diagnose(surface)
            self.assertIn("OFFLINE", report.mode_report)
            self.assertIn("mokata sync", report.mode_report)

    def test_in_chat_briefing_surfaces_trouble_and_offer(self):
        from mokata.bootstrap import _render
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _cache_trouble(surface)
            os.environ.pop(run_mode.CREDENTIAL_ENV, None)
            text = _render(surface)
            self.assertIn("OFFLINE", text)
            self.assertIn("work", text.lower())

    def test_in_chat_local_has_no_health_line(self):
        from mokata.bootstrap import _render
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, mode=None)
            text = _render(surface)
            self.assertNotIn("Team connection", text)


class TestModeShow(unittest.TestCase):
    def test_mode_show_surfaces_health(self):
        from mokata.cli_commands.mode import cmd_mode
        import argparse
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _cache_trouble(surface)
            os.environ.pop(run_mode.CREDENTIAL_ENV, None)
            buf = io.StringIO()
            args = argparse.Namespace(path=d, action=None, mode=None, yes=False)
            with redirect_stdout(buf):
                cmd_mode(args)
            self.assertIn("OFFLINE", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
