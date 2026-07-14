"""A4 — SessionStart bootstrap + token budget."""

import io
import os
import sqlite3
import sys
import unittest

from _support import sample_manifest_data

from mokata.bootstrap import (
    BOOTSTRAP_TOKEN_BUDGET,
    build_bootstrap,
    estimate_tokens,
)
from mokata.config import Constitution, Surface
from mokata.detect import Detector
from mokata.manifest import Manifest


def make_surface(overrides=None):
    manifest = Manifest.from_dict(sample_manifest_data())
    constitution = Constitution(
        text="# c\n## Article 1 — x\n## Article 2 — y\n", path="<mem>"
    )
    return Surface(
        manifest,
        constitution,
        root=".",
        detector=Detector(overrides=overrides or {}),
    )


class TestBootstrap(unittest.TestCase):
    def test_estimate_tokens(self):
        self.assertEqual(estimate_tokens(""), 0)
        self.assertEqual(estimate_tokens("abcd"), 1)
        self.assertEqual(estimate_tokens("abcde"), 2)  # ceil(5/4)

    def test_under_budget(self):
        result = build_bootstrap(make_surface())
        self.assertTrue(result.within_budget)
        self.assertLessEqual(result.token_estimate, BOOTSTRAP_TOKEN_BUDGET)
        self.assertEqual(result.budget, BOOTSTRAP_TOKEN_BUDGET)

    def test_contains_inviolable_gates(self):
        text = build_bootstrap(make_surface()).text
        self.assertIn("human-gate", text)
        self.assertIn("local-first", text)

    def test_shows_degraded_capability(self):
        text = build_bootstrap(make_surface(overrides={"graphtool": False})).text
        self.assertIn("degraded", text)

    def test_shows_preferred_when_present(self):
        text = build_bootstrap(make_surface(overrides={"graphtool": True})).text
        self.assertIn("code_graph -> graphtool", text)

    def test_truncates_when_budget_small(self):
        # A budget far below the real briefing size forces truncation; the result must
        # still fit and say so.
        result = build_bootstrap(make_surface(), budget=30)
        self.assertLessEqual(result.token_estimate, 30)
        self.assertIn("truncated", result.text)
        self.assertFalse(result.within_budget is False)

    def test_truncation_fits_even_pathological_budget(self):
        # Even a budget smaller than the truncation notice must not overflow.
        result = build_bootstrap(make_surface(), budget=5)
        self.assertLessEqual(result.token_estimate, 5)

    def test_constitution_article_count_shown(self):
        text = build_bootstrap(make_surface()).text
        self.assertIn("article", text)


class TestBootstrapCalibration(unittest.TestCase):
    """R11 — the SessionStart briefing logs its chars/4 estimate to the ledger (estimate alone,
    since Claude Code reports no real count at inject time). It must NEVER change the briefing's
    output or budget, and never raise."""

    def test_build_bootstrap_output_and_budget_unchanged(self):
        # build_bootstrap stays PURE — calibration lives in the hook, not the estimator.
        a = build_bootstrap(make_surface())
        b = build_bootstrap(make_surface())
        self.assertEqual(a.text, b.text)
        self.assertEqual(a.token_estimate, b.token_estimate)
        self.assertEqual(a.budget, BOOTSTRAP_TOKEN_BUDGET)

    def test_session_start_logs_estimate_only(self):
        import contextlib
        import io
        import json
        import tempfile

        from mokata import hook_cli
        from mokata.govern import AuditLedger
        from mokata.init import init_repo

        d = tempfile.mkdtemp()
        init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)

        payload = json.dumps({"cwd": d})
        old_stdin = sys.stdin
        sys.stdin = io.StringIO(payload)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rc = hook_cli.session_start_main([])
        finally:
            sys.stdin = old_stdin

        self.assertEqual(rc, 0)
        cal = [e for e in AuditLedger.from_mokata_dir(os.path.join(d, ".mokata")).entries()
               if e.get("kind") == "token_calibration"]
        self.assertTrue(cal)                     # the estimate was logged
        self.assertNotIn("actual", cal[-1])      # no real count at inject time — estimate alone
        self.assertGreater(cal[-1]["estimate"], 0)


class TestRulesDegradeIsLoud(unittest.TestCase):
    """D5 — a memory store the briefing cannot read must SAY SO.

    The bug: `_always_on_rule_lines` swallowed every error and returned []. The project's captured
    rules & guardrails then simply never reached the briefing — and the briefing looked completely
    NORMAL, byte-indistinguishable from a project that had captured no rules at all. Claude went on
    to work with NONE of the user's guardrails while the user believed governance was on.

    The [] fallback stays (a briefing must never crash the session). It just stops being a secret."""

    def setUp(self):
        from mokata import degrade
        degrade.reset_degrade_notices()
        self.addCleanup(degrade.reset_degrade_notices)

    def _rule_lines_with_broken_memory(self, exc):
        """Force the memory read to fail with `exc`; return (lines, printed notices)."""
        from mokata import bootstrap, degrade
        import mokata.memory as memory

        def boom(_surface):
            raise exc
        real = memory.MemoryStore.from_surface
        memory.MemoryStore.from_surface = staticmethod(boom)
        self.addCleanup(setattr, memory.MemoryStore, "from_surface", real)

        err = io.StringIO()
        real_stderr, sys.stderr = sys.stderr, err
        try:
            lines = bootstrap._always_on_rule_lines(make_surface())
        finally:
            sys.stderr = real_stderr
        return lines, err.getvalue(), degrade.emitted_notices()

    def test_an_unreadable_memory_store_still_returns_no_lines(self):
        # the fallback SHAPE is unchanged — the briefing never crashes the session.
        lines, _err, _n = self._rule_lines_with_broken_memory(sqlite3.OperationalError("locked"))
        self.assertEqual(lines, [])

    def test_it_is_LOUD_and_says_the_rules_are_not_being_applied(self):
        _lines, err, notices = self._rule_lines_with_broken_memory(
            sqlite3.DatabaseError("file is not a database"))
        self.assertIn("DEGRADED", err)
        self.assertIn("rules are NOT being applied", err)
        self.assertIn("mokata doctor", err)          # the exact remediation, named
        # and it is REMEMBERED, so `mokata doctor` can answer "what degraded?" later.
        self.assertEqual([n.subsystem for n in notices], ["memory-rules"])

    def test_the_notice_goes_to_stderr_not_stdout(self):
        out = io.StringIO()
        real_stdout, sys.stdout = sys.stdout, out
        try:
            _lines, err, _n = self._rule_lines_with_broken_memory(OSError("permission denied"))
        finally:
            sys.stdout = real_stdout
        self.assertIn("DEGRADED", err)
        self.assertEqual(out.getvalue(), "")         # a -quiet/JSON caller's stdout stays clean

    def test_a_bug_is_no_longer_swallowed_as_a_missing_ruleset(self):
        # the whole point of narrowing: an AttributeError from a typo used to read, to the user,
        # as "this project has no rules". It now SURFACES.
        with self.assertRaises(AttributeError):
            self._rule_lines_with_broken_memory(AttributeError("typo"))


class TestTeamHealthLineNeverVanishes(unittest.TestCase):
    """D5 — in TEAM mode the health verdict line must be printed even when the check fails.

    The bug: `except Exception: pass` deleted BOTH the health line and the work-locally offer. Their
    ABSENCE is exactly what a healthy LOCAL briefing looks like, so a broken shared DB rendered as a
    clean session. The fix is the fallback SHAPE (mirroring `degrade.resolve_read_routing`): an
    OFFLINE verdict — which is printed, and which is trouble, so the offer prints too."""

    def _team_surface(self):
        data = sample_manifest_data()
        data.setdefault("settings", {})["mode"] = "team"
        return Surface(Manifest.from_dict(data),
                       Constitution(text="# c\n", path="<mem>"),
                       root=".", detector=Detector(overrides={}))

    def test_a_failing_health_check_still_prints_the_line_and_the_offer(self):
        from mokata import bootstrap, team_health

        def boom(*_a, **_k):
            raise OSError("no route to host")
        real = team_health.check
        team_health.check = boom
        self.addCleanup(setattr, team_health, "check", real)

        text = bootstrap._render(self._team_surface())
        self.assertIn("Team connection:", text)       # the line is PRESENT, not vanished
        self.assertIn("offline", text.lower())        # the fail-closed verdict, named
        self.assertIn("keep working locally", text)   # and the work-locally offer

    def test_team_mode_is_never_indistinguishable_from_local(self):
        from mokata import bootstrap, team_health

        def boom(*_a, **_k):
            raise OSError("down")
        real = team_health.check
        team_health.check = boom
        self.addCleanup(setattr, team_health, "check", real)

        broken_team = bootstrap._render(self._team_surface())
        local = bootstrap._render(make_surface())
        self.assertNotIn("Team connection:", local)   # local says nothing about a team DB...
        self.assertIn("Team connection:", broken_team)  # ...and a broken team mode always does.


if __name__ == "__main__":
    unittest.main()
