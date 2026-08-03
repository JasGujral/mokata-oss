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

try:                                        # test-only dep (requirements/ci.txt), not a mokata dep
    import yaml
    _HAVE_YAML = True
except ImportError:                         # absence is FAILED LOUD at the pin, never skipped
    yaml = None
    _HAVE_YAML = False

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


# DEV-ONLY SOURCES. These two scripts are excluded from the public mirror, so the classes that
# read them cannot run there. The skip is a CLASS DECORATOR rather than a `raise SkipTest` in
# `setUpClass` **and the difference is not cosmetic** (found 2026-08-02 while explaining the
# DB.S10 audit's unattributed 6-test gap): a SkipTest raised in `setUpClass` never lets the
# class's tests START, so unittest reports the whole class as ONE skip and `Ran N` drops by the
# class size. A decorator skips each test individually, so all six still appear — as skips. That
# is why the mirror's CI said `Ran 5623` where a dev checkout said `Ran 5629`: same files, same
# collection (both discover 5,629), six tests that simply stopped being counted. An unexplained
# count is exactly the "a skipped leg reads green" hazard this project keeps closing, so the two
# numbers are now the same number everywhere.
_RELEASE_SH = os.path.join(ROOT, "scripts", "release.sh")
_SYNC_SH = os.path.join(ROOT, "scripts", "sync-public.sh")


@unittest.skipUnless(os.path.exists(_RELEASE_SH),
                     "scripts/release.sh is dev-only — not shipped to the public mirror")
class ReleaseScriptWiring(unittest.TestCase):
    """release.sh (dev-only) must actually RUN the fail-closed branch-protection check in preflight."""

    @classmethod
    def setUpClass(cls):
        with open(_RELEASE_SH, encoding="utf-8") as fh:
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

    def test_runs_shipped_suite_against_public_subset_before_tagging(self):
        """PROCESS GUARDRAIL: a shipped test that reads an internal-only file passes on the
        private tree but ERRORS on the public mirror (FileNotFoundError) — reddening release-gate
        CI *after* the push. So the preflight must ALSO run the shipped suite against the exact
        public-synced subset, catching that class of bug BEFORE anything is pushed/tagged."""
        check_at = self.sh.find("run_public_subset_preflight")
        self.assertNotEqual(check_at, -1,
                            "release.sh must run the shipped suite against the public-synced subset")
        # It must be reached in the preflight, before the first tag is ever created.
        tag_at = self.sh.find("git tag -a")
        self.assertNotEqual(tag_at, -1)
        self.assertLess(check_at, tag_at,
                        "the public-subset preflight must run BEFORE any tag is created")


@unittest.skipUnless(os.path.exists(_SYNC_SH),
                     "scripts/sync-public.sh is dev-only — not shipped to the public mirror")
class SyncBoundaryEnvHardening(unittest.TestCase):
    """scripts/sync-public.sh: a real `.env` is EXCLUDED (would carry live creds) but `.env.example`
    still SHIPS, and .env is in the guard's INTERNAL_PATHS. (String-level: the DRY-RUN is the proof.)

    PIN-SUBSTRING-COMMENT-HOLE (0.0.16). All three pins below used to grep the WHOLE script — and
    the script documents both controls IN PLACE, in prose that names the very literals being
    pinned (`sync-public.sh:104-112` says `.env` six times and quotes `'.env*'`). So each pin was
    satisfied by the COMMENT after the control it guards had been deleted: green precisely when
    the control was gone, and for all three this class was the SOLE guard. Mutation-proven, then
    fixed with the TD-2 remedy — bound the slice to the rsync ARGUMENT literal / the INTERNAL_PATHS
    ARRAY literal, and strip `#` lines from both before asserting."""

    @staticmethod
    def _code_only(block):
        """`block` with every `#` comment line dropped. Load-bearing, not tidiness — see the
        class docstring: the comments in this script name the exact literals under pin."""
        return "\n".join(ln for ln in block.splitlines() if not ln.lstrip().startswith("#"))

    @staticmethod
    def _slice(text, start_marker, end_marker, what):
        """`text` between the two markers. Raises (never returns a wrong slice) if either marker
        is gone — an explicit raise, not `assert`, so `python -O` cannot delete the check."""
        start = text.find(start_marker)
        if start == -1:
            raise AssertionError("sync-public.sh lost its " + what + " (no '" + start_marker + "')")
        end = text.find(end_marker, start + len(start_marker))
        if end == -1:
            raise AssertionError("sync-public.sh's " + what + " is unterminated (no '"
                                 + end_marker.replace("\n", "\\n") + "')")
        return text[start:end]

    @classmethod
    def setUpClass(cls):
        with open(_SYNC_SH, encoding="utf-8") as fh:
            cls.sh = fh.read()
        # The rsync ARGUMENT literal: `rsync -a --delete \` … `"$SRC"/ "$DEST"/`.
        cls.rsync_args = cls._code_only(
            cls._slice(cls.sh, "rsync -a --delete", '"$SRC"/ "$DEST"/', "rsync invocation"))
        # The INTERNAL_PATHS ARRAY literal: `INTERNAL_PATHS=(` … `\n)`. Bounded on purpose —
        # "everything after `INTERNAL_PATHS=(`" swallows the enforcement loop and its commentary,
        # which discuss `.env` at length and satisfy the membership check on their own.
        cls.guard_block = cls._code_only(
            cls._slice(cls.sh, "INTERNAL_PATHS=(", "\n)", "INTERNAL_PATHS guard"))

    def test_env_example_is_included_before_env_exclude(self):
        inc = self.rsync_args.find("--include='.env.example'")
        exc = self.rsync_args.find("--exclude='.env")
        self.assertNotEqual(inc, -1, ".env.example must be explicitly INCLUDED so it still ships")
        self.assertNotEqual(exc, -1, ".env must be EXCLUDED")
        self.assertLess(inc, exc,
                        "rsync ordering: the .env.example include must precede the .env exclude")

    def test_dotenv_wildcard_excluded(self):
        self.assertIn("--exclude='.env*'", self.rsync_args,
                      "sync-public.sh lost its `--exclude='.env*'` rsync argument — a real .env "
                      "(live DB creds) would be copied to the public mirror")

    def test_env_in_guard_internal_paths(self):
        # Token-level, not substring: `.env` must be an ARRAY ENTRY of its own. The array also
        # holds `.venv` and `'*.egg-info'`, and a looser check invites a future near-miss.
        self.assertIn(".env", self.guard_block.split(),
                      ".env must be an entry in the guard's INTERNAL_PATHS — it is the "
                      "belt-and-suspenders backstop for the live-credential file")


class ScorecardPatWiring(unittest.TestCase):
    """scorecard.yml must pass the SCORECARD_PAT to scorecard-action, keeping the SHA pin + trigger.

    PIN-SUBSTRING-COMMENT-HOLE (0.0.16): `assertIn(<literal>, self.yml)` over the raw file passed
    on the comment left behind when the real line was commented out — the SHA pin stayed green
    with the action re-pointed at the MUTABLE `@v2` tag. Both pins now read the PARSED structure,
    where a comment cannot appear. PyYAML is not a mokata dependency, so its absence is FAILED
    LOUD rather than skipped: a pin that evaporates when a dep is missing is the same bug in a
    different costume. CI installs it (requirements/ci.txt) for exactly this reason."""

    _ACTION = "ossf/scorecard-action"
    _SHA = "4eaacf0543bb3f2c246792bd56e8cdeffafb205a"

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, ".github", "workflows", "scorecard.yml"), encoding="utf-8") as fh:
            cls.yml = fh.read()

    def _scorecard_step(self):
        """The PARSED `ossf/scorecard-action` step. Fails loud without PyYAML."""
        if not _HAVE_YAML:
            self.fail("PyYAML is required to verify the scorecard-action wiring from the PARSED "
                      "workflow (a text-level check passes on a comment). Install it: "
                      "pip install -r requirements/ci.txt")
        doc = yaml.safe_load(self.yml)
        for job in (doc.get("jobs") or {}).values():
            for step in (job or {}).get("steps") or []:
                if self._ACTION in str((step or {}).get("uses", "")):
                    return step
        self.fail("scorecard.yml declares no `" + self._ACTION + "` step at all")

    def test_repo_token_wired_to_pat_secret(self):
        with_ = self._scorecard_step().get("with") or {}
        self.assertEqual(with_.get("repo_token"), "${{ secrets.SCORECARD_PAT }}",
                         "scorecard-action must receive the fine-grained SCORECARD_PAT; without it "
                         "the Branch-Protection check scores 0 ('Resource not accessible')")

    def test_sha_pin_intact(self):
        uses = str(self._scorecard_step().get("uses", ""))
        ref = uses.split("@", 1)[1] if "@" in uses else ""
        self.assertEqual(ref, self._SHA,
                         "scorecard-action must stay pinned to its immutable 40-hex commit SHA, "
                         "not a mutable tag — got '" + uses + "'")

    def test_trigger_intact(self):
        self.assertIn("branches: [main, master]", self.yml)
        self.assertIn("workflow_dispatch", self.yml)


if __name__ == "__main__":
    unittest.main()
