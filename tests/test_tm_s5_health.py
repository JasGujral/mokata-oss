"""TM.S5 — ONE health surface: a single cached probe verdict rendered identically in the
statusline badge (⚠), `mokata mode`/status, `mokata doctor`, and in-chat (doc 48 P-11 / E2).

A broken/unreachable connection is ALWAYS highlighted, never silent; failure surfaces a
work-locally offer. Bounded, cached, lazily re-checked. Local mode is a `local` verdict with
NO probe (zero-config untouched).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import MANIFEST_FILENAME, MOKATA_DIR, run_mode, team_health, teamdb
from mokata.config import Surface
from mokata.init import init_repo


def _repo(d, mode=None):
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    if mode is not None:
        # write settings.mode directly (bypass the gate — this is test setup, not the flow).
        import json
        path = os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME)
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        data.setdefault("settings", {})["mode"] = mode
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    return Surface.load(d)


def _res(**kw):
    """A ProbeResult with sensible healthy defaults, overridable per case."""
    base = dict(driver_present=True, reachable=True, schema_present=True,
                schema_version=teamdb.TEAM_SCHEMA_VERSION, compatible=True,
                elapsed_ms=12.0, detail="reachable + schema v2 compatible")
    base.update(kw)
    return teamdb.ProbeResult(**base)


class TestClassify(unittest.TestCase):
    def test_healthy(self):
        state, _ = team_health.classify(_res())
        self.assertEqual(state, team_health.HEALTHY)

    def test_unreachable_is_offline(self):
        state, _ = team_health.classify(_res(reachable=False, compatible=False))
        self.assertEqual(state, team_health.OFFLINE)

    def test_driver_absent_is_offline(self):
        state, _ = team_health.classify(_res(driver_present=False, reachable=False,
                                             compatible=False))
        self.assertEqual(state, team_health.OFFLINE)

    def test_schema_absent_is_degraded(self):
        state, _ = team_health.classify(_res(schema_present=False, schema_version=None,
                                             compatible=False))
        self.assertEqual(state, team_health.DEGRADED)

    def test_incompatible_version_is_degraded(self):
        state, _ = team_health.classify(_res(schema_version=1, compatible=False))
        self.assertEqual(state, team_health.DEGRADED)


class TestLocalUnaffected(unittest.TestCase):
    def test_local_mode_returns_local_and_never_probes(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)                      # default = local
            called = []

            def _probe(dsn):
                called.append(dsn)
                return _res()

            v = team_health.check(surface, probe=_probe, environ={})
            self.assertEqual(v.state, team_health.LOCAL)
            self.assertTrue(v.ok)
            self.assertFalse(v.trouble)
            self.assertEqual(called, [], "local mode must NOT probe the network")


class TestOneCachedProbe(unittest.TestCase):
    def test_team_healthy_caches_and_reuses(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, mode="team")
            calls = []

            def _probe(dsn):
                calls.append(dsn)
                return _res()

            env = {run_mode.CREDENTIAL_ENV: "postgres://x"}
            t = [1000.0]
            v1 = team_health.check(surface, probe=_probe, environ=env, now=lambda: t[0])
            self.assertEqual(v1.state, team_health.HEALTHY)
            # a second call within the TTL reuses the cache — no second probe.
            t[0] = 1005.0
            v2 = team_health.check(surface, probe=_probe, environ=env, now=lambda: t[0])
            self.assertEqual(v2.state, team_health.HEALTHY)
            self.assertEqual(len(calls), 1, "a fresh cached verdict must not re-probe")

    def test_stale_cache_is_lazily_rechecked(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, mode="team")
            seq = [_res(), _res(reachable=False, compatible=False, detail="dropped")]

            def _probe(dsn):
                return seq.pop(0)

            env = {run_mode.CREDENTIAL_ENV: "postgres://x"}
            t = [1000.0]
            v1 = team_health.check(surface, probe=_probe, environ=env, now=lambda: t[0])
            self.assertTrue(v1.ok)
            t[0] = 1000.0 + team_health.CACHE_TTL_S + 1
            v2 = team_health.check(surface, probe=_probe, environ=env, now=lambda: t[0])
            self.assertEqual(v2.state, team_health.OFFLINE, "a stale cache must be re-probed")

    def test_missing_dsn_is_offline_with_a_fix(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, mode="team")
            v = team_health.check(surface, probe=lambda dsn: _res(), environ={})
            self.assertEqual(v.state, team_health.OFFLINE)
            self.assertIn(run_mode.CREDENTIAL_ENV, v.detail)

    def test_probe_exception_fails_closed_to_offline(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, mode="team")

            def _boom(dsn):
                raise RuntimeError("kaboom")

            env = {run_mode.CREDENTIAL_ENV: "postgres://x"}
            v = team_health.check(surface, probe=_boom, environ=env)
            self.assertEqual(v.state, team_health.OFFLINE)
            self.assertFalse(v.ok)


class TestSameVerdictEverySurface(unittest.TestCase):
    """The badge, `mode`, doctor and in-chat all render the SAME cached verdict (P-11)."""

    def _troubled(self, d):
        surface = _repo(d, mode="team")
        env = {run_mode.CREDENTIAL_ENV: "postgres://x"}
        team_health.check(surface, probe=lambda dsn: _res(reachable=False, compatible=False,
                                                          detail="unreachable — connect refused"),
                          environ=env)
        return surface

    def test_badge_shows_warning_glyph_on_trouble(self):
        with tempfile.TemporaryDirectory() as d:
            surface = self._troubled(d)
            v = team_health.cached_or_neutral(surface)
            self.assertTrue(v.trouble)
            self.assertIn("⚠", v.badge_suffix())
            self.assertIn("[!]", v.badge_suffix(ascii_only=True))

    def test_badge_neutral_before_first_probe(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, mode="team")           # team, but nothing probed yet
            v = team_health.cached_or_neutral(surface)
            self.assertEqual(v.badge_suffix(), "", "no probe yet → no fabricated ⚠")

    def test_all_surfaces_agree_and_offer_work_locally(self):
        with tempfile.TemporaryDirectory() as d:
            surface = self._troubled(d)
            v = team_health.cached_or_neutral(surface)
            block = team_health.status_block(v)
            # the same OFFLINE verdict, and the explicit work-locally offer, in the block.
            self.assertIn("OFFLINE", block)
            self.assertIn("work", block.lower())
            self.assertIn("mokata sync", block)

    def test_healthy_block_has_no_warning_or_offer(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, mode="team")
            env = {run_mode.CREDENTIAL_ENV: "postgres://x"}
            team_health.check(surface, probe=lambda dsn: _res(), environ=env)
            v = team_health.cached_or_neutral(surface)
            self.assertTrue(v.ok)
            block = team_health.status_block(v)
            self.assertIn("HEALTHY", block)
            self.assertNotIn("⚠", v.badge_suffix())


if __name__ == "__main__":
    unittest.main()
