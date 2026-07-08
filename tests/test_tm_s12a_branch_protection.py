"""TM.S12a — OSS public-`main` branch-protection VERIFICATION (fail-closed) + boundary hardening.

The public mirror (JasGujral/mokata-oss) must ship from a protected `main`: no force-push, no
deletion, required status checks. This battery proves the standalone verification check the release
preflight runs is FAIL-CLOSED — it blocks a release whenever protection is absent, `gh` is
unavailable/unauthed, or the API errors — and passes ONLY when protection is genuinely safe. It also
proves the .env sync-boundary hardening (a real `.env` is EXCLUDED while `.env.example` still ships),
the Scorecard PAT wiring, and that release.sh actually runs the check.

No network: the `gh`/API layer is INJECTED (a fake runner), so every branch is exercised offline and
deterministically. Pure/dependency-free.
"""

import io
import os
import unittest
from contextlib import redirect_stdout

import _support  # noqa: F401  (puts src/ on the path)

from mokata.branch_protection import (
    check_branch_protection,
    evaluate_protection_payload,
)

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


# A protection payload that is SAFE (the shape `gh api .../protection` returns when applied per
# PART A: no force-push, no deletion, required status checks configured).
SAFE_PAYLOAD = {
    "required_status_checks": {"strict": True, "contexts": []},
    "enforce_admins": {"enabled": False},
    "allow_force_pushes": {"enabled": False},
    "allow_deletions": {"enabled": False},
}


def _runner_returning(code, out, err=""):
    """Build a fake gh runner (repo, branch) -> (returncode, stdout, stderr)."""
    def _run(repo, branch):
        return code, out, err
    return _run


def _runner_raising(exc):
    def _run(repo, branch):
        raise exc
    return _run


class EvaluatePayload(unittest.TestCase):
    """The PURE predicate over a parsed protection payload — fail-closed on every unsafe shape."""

    def test_safe_payload_passes(self):
        ok, failures = evaluate_protection_payload(SAFE_PAYLOAD)
        self.assertTrue(ok)
        self.assertEqual(failures, [])

    def test_force_push_enabled_fails(self):
        payload = dict(SAFE_PAYLOAD, allow_force_pushes={"enabled": True})
        ok, failures = evaluate_protection_payload(payload)
        self.assertFalse(ok)
        self.assertTrue(any("force" in f.lower() for f in failures))

    def test_deletion_enabled_fails(self):
        payload = dict(SAFE_PAYLOAD, allow_deletions={"enabled": True})
        ok, failures = evaluate_protection_payload(payload)
        self.assertFalse(ok)
        self.assertTrue(any("delet" in f.lower() for f in failures))

    def test_missing_required_status_checks_fails(self):
        payload = {k: v for k, v in SAFE_PAYLOAD.items() if k != "required_status_checks"}
        ok, failures = evaluate_protection_payload(payload)
        self.assertFalse(ok)
        self.assertTrue(any("status check" in f.lower() for f in failures))

    def test_non_dict_payload_fails_closed(self):
        ok, failures = evaluate_protection_payload(["not", "a", "dict"])
        self.assertFalse(ok)
        self.assertTrue(failures)


class CheckBranchProtection(unittest.TestCase):
    """The gh-driven check — every inability to prove protection is a FAIL, not a pass."""

    def test_passes_only_when_protected(self):
        import json
        v = check_branch_protection(runner=_runner_returning(0, json.dumps(SAFE_PAYLOAD)))
        self.assertTrue(v.ok)
        self.assertIn("PASS", v.render())

    def test_fail_closed_when_branch_not_protected_404(self):
        # gh exits non-zero with "Branch not protected (HTTP 404)" when `main` has no protection.
        v = check_branch_protection(
            runner=_runner_returning(1, "", "gh: Branch not protected (HTTP 404)"))
        self.assertFalse(v.ok)
        self.assertIn("FAIL", v.render())

    def test_fail_closed_when_gh_unavailable(self):
        v = check_branch_protection(runner=_runner_raising(FileNotFoundError("gh")))
        self.assertFalse(v.ok)
        self.assertTrue(any("gh" in f.lower() for f in v.failures))

    def test_fail_closed_when_api_errors(self):
        # e.g. an unauthed/underprivileged token: "Resource not accessible by integration".
        v = check_branch_protection(
            runner=_runner_returning(1, "", "Resource not accessible by integration (HTTP 403)"))
        self.assertFalse(v.ok)

    def test_fail_closed_on_invalid_json(self):
        v = check_branch_protection(runner=_runner_returning(0, "not-json{"))
        self.assertFalse(v.ok)

    def test_fail_closed_when_force_push_enabled(self):
        import json
        payload = dict(SAFE_PAYLOAD, allow_force_pushes={"enabled": True})
        v = check_branch_protection(runner=_runner_returning(0, json.dumps(payload)))
        self.assertFalse(v.ok)

    def test_render_names_a_fix_on_failure(self):
        v = check_branch_protection(runner=_runner_raising(FileNotFoundError("gh")))
        self.assertIn("fix", v.render().lower())


class CliExitCode(unittest.TestCase):
    """The CLI subcommand maps ok -> exit 0 and fail -> exit 1 (so release.sh can abort)."""

    def _run_cmd(self, runner):
        import json
        from mokata import branch_protection
        from mokata.cli_commands.core import cmd_branch_protection_check
        import argparse
        # Patch the default gh runner the CLI reaches for.
        orig = branch_protection._default_gh_runner
        branch_protection._default_gh_runner = runner
        try:
            args = argparse.Namespace(repo="JasGujral/mokata-oss", branch="main")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_branch_protection_check(args)
            return rc, buf.getvalue()
        finally:
            branch_protection._default_gh_runner = orig

    def test_exit_zero_when_protected(self):
        import json
        rc, out = self._run_cmd(_runner_returning(0, json.dumps(SAFE_PAYLOAD)))
        self.assertEqual(rc, 0)
        self.assertIn("PASS", out)

    def test_exit_one_when_unprotected(self):
        rc, out = self._run_cmd(_runner_returning(1, "", "Branch not protected (HTTP 404)"))
        self.assertEqual(rc, 1)
        self.assertIn("FAIL", out)


class ReleaseScriptWiring(unittest.TestCase):
    """release.sh (dev-only) must actually RUN the fail-closed branch-protection check in preflight."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "scripts", "release.sh"), encoding="utf-8") as fh:
            cls.sh = fh.read()

    def test_invokes_the_branch_protection_check(self):
        self.assertIn("branch-protection-check", self.sh,
                      "release.sh must run the branch-protection-check preflight")

    def test_check_gates_before_any_tag(self):
        check_at = self.sh.find("branch-protection-check")
        tag_at = self.sh.find("git tag -a")
        self.assertNotEqual(check_at, -1)
        self.assertNotEqual(tag_at, -1)
        self.assertLess(check_at, tag_at,
                        "branch protection must be verified BEFORE any tag is created")


class SyncBoundaryEnvHardening(unittest.TestCase):
    """scripts/sync-public.sh: a real `.env` is EXCLUDED (would carry live creds) but `.env.example`
    still SHIPS, and .env is in the guard's INTERNAL_PATHS. (String-level: the DRY-RUN is the proof.)"""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "scripts", "sync-public.sh"), encoding="utf-8") as fh:
            cls.sh = fh.read()

    def test_env_example_is_included_before_env_exclude(self):
        inc = self.sh.find("--include='.env.example'")
        exc = self.sh.find("--exclude='.env")
        self.assertNotEqual(inc, -1, ".env.example must be explicitly INCLUDED so it still ships")
        self.assertNotEqual(exc, -1, ".env must be EXCLUDED")
        self.assertLess(inc, exc,
                        "rsync ordering: the .env.example include must precede the .env exclude")

    def test_dotenv_wildcard_excluded(self):
        self.assertIn("--exclude='.env*'", self.sh)

    def test_env_in_guard_internal_paths(self):
        guard = self.sh[self.sh.find("INTERNAL_PATHS=("):]
        self.assertIn(".env", guard, ".env must be in the guard's INTERNAL_PATHS")


class ScorecardPatWiring(unittest.TestCase):
    """scorecard.yml must pass the SCORECARD_PAT to scorecard-action, keeping the SHA pin + trigger."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, ".github", "workflows", "scorecard.yml"), encoding="utf-8") as fh:
            cls.yml = fh.read()

    def test_repo_token_wired_to_pat_secret(self):
        self.assertIn("repo_token: ${{ secrets.SCORECARD_PAT }}", self.yml)

    def test_sha_pin_intact(self):
        self.assertIn("4eaacf0543bb3f2c246792bd56e8cdeffafb205a", self.yml)

    def test_trigger_intact(self):
        self.assertIn("branches: [main, master]", self.yml)
        self.assertIn("workflow_dispatch", self.yml)


if __name__ == "__main__":
    unittest.main()
