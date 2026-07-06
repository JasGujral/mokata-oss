"""A4 — SessionStart bootstrap + token budget."""

import os
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


if __name__ == "__main__":
    unittest.main()
