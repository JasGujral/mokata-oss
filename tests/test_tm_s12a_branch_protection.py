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
from contextlib import redirect_stderr, redirect_stdout

try:                                        # test-only dep (requirements/ci.txt), not a mokata dep
    import yaml
    _HAVE_YAML = True
except ImportError:                         # absence is FAILED LOUD at the pin, never skipped
    yaml = None
    _HAVE_YAML = False

import _support  # noqa: F401  (puts src/ on the path)

from mokata.branch_protection import (
    EXIT_DEGRADED,
    EXIT_NOT_PROTECTED,
    EXIT_PROTECTED,
    EXIT_UNREADABLE,
    RESTORE_ROW,
    STATE_NOT_PROTECTED,
    STATE_PROTECTED,
    STATE_UNREADABLE,
    UNOBTAINED_ASSURANCES,
    check_branch_protection,
    evaluate_protection_payload,
    evaluate_rulesets_payload,
    reads_as_unprotected,
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

# The three API paths the check reads, spelled for the default repo/branch.
REPO = "JasGujral/mokata-oss"
DETAIL_PATH = "repos/JasGujral/mokata-oss/branches/main/protection"
BRANCH_PATH = "repos/JasGujral/mokata-oss/branches/main"
RULESETS_PATH = "repos/JasGujral/mokata-oss/rulesets"

# What the real outage looked like: 503 on the protection endpoint (REST) while every other
# endpoint answered 200 and the rate limit sat untouched at 5000/5000.
UNREADABLE_503 = (1, "", "gh: HTTP 503: Service Unavailable (https://api.github.com/...)")


def _runner_returning(code, out, err=""):
    """A fake gh runner answering EVERY path identically: (api_path) -> (rc, stdout, stderr)."""
    def _run(path):
        return code, out, err
    return _run


def _runner_raising(exc):
    def _run(path):
        raise exc
    return _run


def _routed(responses, default=UNREADABLE_503):
    """A fake gh runner answering PER PATH. A value that is an exception is raised.

    Per-path routing is what makes state 3 testable at all: the corroboration reads two further
    endpoints, and a runner that can only answer one URL cannot distinguish "the detail was
    unreadable and the corroboration succeeded" from "everything failed"."""
    def _run(path):
        value = responses.get(path, default)
        if isinstance(value, BaseException):
            raise value
        code, out = value[0], value[1]
        return code, out, (value[2] if len(value) > 2 else "")
    return _run


def _degraded_runner(branch=(0, '{"protected": true}'), rulesets=(0, "[]")):
    """The real-world state 3: the DETAIL 503s, the two corroborating reads answer."""
    return _routed({DETAIL_PATH: UNREADABLE_503, BRANCH_PATH: branch, RULESETS_PATH: rulesets})


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


class EvaluateRulesets(unittest.TestCase):
    """The PURE predicate over `repos/<repo>/rulesets` — the second half of state 3's corroboration.

    Empty is CONSISTENT: that is exactly what a repo protected the classic way returns, and it is
    the picture observed the day this state was first hit. A ruleset that is configured but NOT
    enforced is not — it makes the picture ambiguous, and an ambiguous corroboration is none."""

    def test_empty_array_is_consistent(self):
        ok, reasons = evaluate_rulesets_payload([])
        self.assertTrue(ok)
        self.assertEqual(reasons, [])

    def test_active_rulesets_are_consistent(self):
        ok, _ = evaluate_rulesets_payload([{"name": "main", "enforcement": "active"}])
        self.assertTrue(ok)

    def test_an_unenforced_ruleset_is_not_consistent(self):
        ok, reasons = evaluate_rulesets_payload([{"name": "main", "enforcement": "evaluate"}])
        self.assertFalse(ok)
        self.assertTrue(any("evaluate" in r for r in reasons))

    def test_a_non_array_response_is_not_consistent(self):
        ok, reasons = evaluate_rulesets_payload({"message": "Not Found"})
        self.assertFalse(ok)
        self.assertTrue(reasons)


class ReadsAsUnprotected(unittest.TestCase):
    """The line between state 2 and state 3. GitHub's `404 Branch not protected` IS the document,
    and it says no; a 503 is the absence of a document and says nothing at all."""

    def test_branch_not_protected_is_a_statement(self):
        self.assertTrue(reads_as_unprotected("gh exit 1: gh: Branch not protected (HTTP 404)"))

    def test_a_503_is_not_a_statement(self):
        self.assertFalse(reads_as_unprotected(UNREADABLE_503[2]))

    def test_a_403_is_not_a_statement(self):
        self.assertFalse(reads_as_unprotected("Resource not accessible by integration (HTTP 403)"))


class State1Protected(unittest.TestCase):
    """STATE 1 — unchanged, and pinned BYTE-FOR-BYTE. The three-state rebuild was not allowed to
    move this path, so the pass line is asserted whole rather than by substring."""

    def test_passes_only_when_protected(self):
        import json
        v = check_branch_protection(runner=_runner_returning(0, json.dumps(SAFE_PAYLOAD)))
        self.assertTrue(v.ok)
        self.assertEqual(v.state, STATE_PROTECTED)
        self.assertFalse(v.degraded)
        self.assertIn("PASS", v.render())

    def test_the_pass_line_is_byte_identical_to_the_two_state_version(self):
        import json
        v = check_branch_protection(runner=_runner_returning(0, json.dumps(SAFE_PAYLOAD)))
        self.assertEqual(
            v.render(),
            "branch-protection PASS — JasGujral/mokata-oss@main is protected "
            "(no force-push, no deletion, required status checks).")

    def test_state_1_exits_zero(self):
        import json
        v = check_branch_protection(runner=_runner_returning(0, json.dumps(SAFE_PAYLOAD)))
        self.assertEqual(v.exit_code, EXIT_PROTECTED)


class State2NotProtected(unittest.TestCase):
    """STATE 2 — a read SUCCEEDED and shows protection absent or too weak. It refuses, and it is
    the ONE state that carries the apply-protection remedy, because here we KNOW it is missing."""

    def test_fail_closed_when_branch_not_protected_404(self):
        v = check_branch_protection(
            runner=_runner_returning(1, "", "gh: Branch not protected (HTTP 404)"))
        self.assertFalse(v.ok)
        self.assertEqual(v.state, STATE_NOT_PROTECTED)
        self.assertIn("FAIL", v.render())
        self.assertEqual(v.exit_code, EXIT_NOT_PROTECTED)

    def test_fail_closed_when_force_push_enabled(self):
        import json
        payload = dict(SAFE_PAYLOAD, allow_force_pushes={"enabled": True})
        v = check_branch_protection(runner=_runner_returning(0, json.dumps(payload)))
        self.assertFalse(v.ok)
        self.assertEqual(v.state, STATE_NOT_PROTECTED)

    def test_state_2_still_carries_the_apply_protection_remedy(self):
        """The destructive PUT is CORRECT here and nowhere else — a successful read said protection
        is missing, so applying it destroys nothing that was there."""
        v = check_branch_protection(
            runner=_runner_returning(1, "", "gh: Branch not protected (HTTP 404)"))
        text = v.render()
        self.assertIn("-X PUT", text)
        self.assertIn("apply protection (admin)", text)

    def test_state_2_carries_the_remedy_on_a_weak_readable_payload_too(self):
        import json
        payload = dict(SAFE_PAYLOAD, allow_deletions={"enabled": True})
        v = check_branch_protection(runner=_runner_returning(0, json.dumps(payload)))
        self.assertIn("-X PUT", v.render())


class State3UnreadableMustEarnItsPass(unittest.TestCase):
    """STATE 3 — THE WHOLE RISK OF THIS CHANGE, so it is pinned hardest.

    It passes ONLY on evidence that was actually READ. Every refusal below is the same rule stated
    once more: a read that did not succeed proves nothing, and must not be allowed to mean "fine"
    merely because it happened one level below the read that failed."""

    def test_passes_when_corroboration_succeeds_and_says_protected(self):
        v = check_branch_protection(runner=_degraded_runner())
        self.assertTrue(v.ok)
        self.assertTrue(v.degraded)
        self.assertEqual(v.state, STATE_UNREADABLE)
        self.assertEqual(v.exit_code, EXIT_DEGRADED)

    def test_refuses_when_protected_is_false(self):
        v = check_branch_protection(runner=_degraded_runner(branch=(0, '{"protected": false}')))
        self.assertFalse(v.ok)
        self.assertFalse(v.degraded)
        self.assertEqual(v.exit_code, EXIT_UNREADABLE)
        self.assertTrue(any("protected" in f for f in v.failures))

    def test_refuses_when_protected_is_missing(self):
        v = check_branch_protection(runner=_degraded_runner(branch=(0, '{"name": "main"}')))
        self.assertFalse(v.ok)

    def test_refuses_when_protected_is_truthy_but_not_the_boolean_true(self):
        """`is not True`, not `not protected` — and the narrowing is load-bearing. The whole pass
        rests on ONE boolean; accepting the string "true", or `1`, would mean accepting a shape
        this code has never actually seen from GitHub on the strength of it being truthy.
        (Added after the mutation batch: `is not True` -> `not protected` SURVIVED — nothing fed a
        truthy non-True value, so the narrowing was unpinned.)"""
        for spelling in ('"true"', "1", '"protected"', "[1]"):
            with self.subTest(protected=spelling):
                v = check_branch_protection(
                    runner=_degraded_runner(branch=(0, '{"protected": %s}' % spelling)))
                self.assertFalse(v.ok, f"`protected: {spelling}` must not corroborate")

    def test_refuses_when_the_corroborating_branch_payload_is_not_an_object(self):
        """A JSON array or scalar where the branch document should be is not a branch document.
        (Added after the mutation batch: replacing this guard with a synthesised
        `{"protected": True}` SURVIVED — nothing fed a non-object body.)"""
        for body in ("[]", '["main"]', "42", '"main"', "null"):
            with self.subTest(body=body):
                v = check_branch_protection(runner=_degraded_runner(branch=(0, body)))
                self.assertFalse(v.ok, f"a branch body of {body} must not corroborate")

    def test_refuses_when_the_corroborating_branch_read_itself_fails(self):
        """THE BUG BEING FIXED, one level down. A failed read is not a pass."""
        v = check_branch_protection(runner=_degraded_runner(branch=UNREADABLE_503))
        self.assertFalse(v.ok)
        self.assertFalse(v.degraded)
        self.assertTrue(any("ALSO failed" in f for f in v.failures))

    def test_refuses_when_the_corroborating_rulesets_read_itself_fails(self):
        v = check_branch_protection(runner=_degraded_runner(rulesets=UNREADABLE_503))
        self.assertFalse(v.ok)
        self.assertTrue(any("ALSO failed" in f for f in v.failures))

    def test_refuses_when_the_corroborating_reads_return_unparseable_bodies(self):
        v = check_branch_protection(runner=_degraded_runner(branch=(0, "not-json{")))
        self.assertFalse(v.ok)
        v2 = check_branch_protection(runner=_degraded_runner(rulesets=(0, "not-json{")))
        self.assertFalse(v2.ok)

    def test_refuses_when_the_rulesets_are_inconsistent(self):
        v = check_branch_protection(runner=_degraded_runner(
            rulesets=(0, '[{"name": "main", "enforcement": "disabled"}]')))
        self.assertFalse(v.ok)
        self.assertTrue(any("not consistent" in f for f in v.failures))

    def test_fail_closed_when_gh_unavailable(self):
        """`gh` missing fails EVERY read, so nothing can be corroborated."""
        v = check_branch_protection(runner=_runner_raising(FileNotFoundError("gh")))
        self.assertFalse(v.ok)
        self.assertEqual(v.state, STATE_UNREADABLE)
        self.assertTrue(any("gh" in f.lower() for f in v.failures))

    def test_fail_closed_when_api_errors(self):
        # e.g. an unauthed/underprivileged token: "Resource not accessible by integration".
        v = check_branch_protection(
            runner=_runner_returning(1, "", "Resource not accessible by integration (HTTP 403)"))
        self.assertFalse(v.ok)

    def test_fail_closed_on_invalid_json(self):
        v = check_branch_protection(runner=_runner_returning(0, "not-json{"))
        self.assertFalse(v.ok)

    def test_render_names_a_fix_on_failure(self):
        v = check_branch_protection(runner=_runner_raising(FileNotFoundError("gh")))
        self.assertIn("fix", v.render().lower())


class AReadFailureNeverSuggestsAWrite(unittest.TestCase):
    """THE SECOND DEFECT. The old function printed an APPLY-PROTECTION remedy — a destructive PUT —
    on a READ failure. At a cut, under time pressure, that gets pasted, and a transient 503 becomes
    an overwrite of real protection with a template. Asserted on the MESSAGE TEXT, because the
    message is the thing an operator acts on."""

    def _unreadable_verdicts(self):
        return [
            check_branch_protection(runner=_runner_returning(0, "not-json{")),
            check_branch_protection(
                runner=_runner_returning(1, "", "Resource not accessible by integration (HTTP 403)")),
            check_branch_protection(runner=_runner_raising(FileNotFoundError("gh"))),
            check_branch_protection(runner=_runner_raising(OSError("boom"))),
            check_branch_protection(runner=_degraded_runner(branch=(0, '{"protected": false}'))),
            check_branch_protection(runner=_degraded_runner(branch=UNREADABLE_503)),
            check_branch_protection(runner=_degraded_runner(rulesets=UNREADABLE_503)),
            check_branch_protection(runner=_degraded_runner()),   # the DEGRADED PASS text too
        ]

    def test_no_unreadable_verdict_ever_emits_a_put(self):
        for v in self._unreadable_verdicts():
            text = v.render()
            self.assertEqual(v.state, STATE_UNREADABLE)
            self.assertNotIn("PUT", text, f"a read failure emitted a PUT remedy:\n{text}")
            self.assertNotIn("--input", text)

    def test_the_refusal_tells_the_operator_not_to_apply_protection(self):
        v = check_branch_protection(runner=_degraded_runner(branch=UNREADABLE_503))
        self.assertIn("do NOT apply protection", v.render())

    def test_the_refusal_points_at_retry_and_status_instead(self):
        v = check_branch_protection(runner=_degraded_runner(branch=UNREADABLE_503))
        text = v.render()
        self.assertIn("githubstatus.com", text)
        self.assertIn("retry", text.lower())


class TheDegradedNoticeIsLoudDatedAndTemporary(unittest.TestCase):
    """State 3's pass is DEGRADED, not green. It must say so, say exactly what was NOT obtained,
    carry a date, and name the row that removes it. An exemption with no expiry is
    RELEASE-SH-DEV-CI-WAIVER-OUTLIVED-ITS-SCOPE, which survived seven releases."""

    def setUp(self):
        self.text = check_branch_protection(runner=_degraded_runner()).render()

    def test_it_says_it_is_not_a_green(self):
        self.assertIn("DEGRADED", self.text)
        self.assertIn("THIS IS NOT A GREEN", self.text)

    def test_it_names_all_four_unobtained_assurances(self):
        for term in ("allow_force_pushes", "allow_deletions",
                     "required_status_checks.strict", "required_status_checks.contexts"):
            self.assertIn(term, self.text, f"the notice does not name {term}")
        for assurance in UNOBTAINED_ASSURANCES:
            self.assertIn(assurance, self.text)

    def test_it_says_it_proceeded_on_the_corroborating_boolean_alone(self):
        self.assertIn("CORROBORATING BOOLEANS ALONE", self.text)
        self.assertIn("protected: true", self.text)

    def test_it_is_dated(self):
        import datetime
        self.assertIn(datetime.date.today().isoformat(), self.text)

    def test_it_names_the_row_that_expires_it(self):
        self.assertIn(RESTORE_ROW, self.text)
        self.assertIn("0.0.19", self.text)

    def test_it_is_not_silent(self):
        """A degraded pass whose render is indistinguishable from the PASS line is a silent
        exemption. The two must not be confusable."""
        import json
        green = check_branch_protection(runner=_runner_returning(0, json.dumps(SAFE_PAYLOAD)))
        self.assertNotEqual(self.text, green.render())
        self.assertNotIn("branch-protection PASS", self.text)


class CliExitCode(unittest.TestCase):
    """The CLI subcommand maps the three states onto FOUR exit codes, so `release.sh` can tell
    "not protected" (apply) from "could not read" (retry) from "degraded" (proceed, loudly)."""

    def _run_cmd(self, runner):
        from mokata import branch_protection, degrade
        from mokata.cli_commands.core import cmd_branch_protection_check
        import argparse
        # Patch the default gh runner the CLI reaches for.
        orig = branch_protection._default_gh_runner
        branch_protection._default_gh_runner = runner
        degrade.reset_degrade_notices()
        try:
            args = argparse.Namespace(repo="JasGujral/mokata-oss", branch="main")
            buf = io.StringIO()
            err = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(err):
                rc = cmd_branch_protection_check(args)
            return rc, buf.getvalue(), err.getvalue()
        finally:
            branch_protection._default_gh_runner = orig
            degrade.reset_degrade_notices()

    def test_exit_zero_when_protected(self):
        import json
        rc, out, _err = self._run_cmd(_runner_returning(0, json.dumps(SAFE_PAYLOAD)))
        self.assertEqual(rc, EXIT_PROTECTED)
        self.assertIn("PASS", out)

    def test_exit_one_when_unprotected(self):
        rc, out, _err = self._run_cmd(_runner_returning(1, "", "Branch not protected (HTTP 404)"))
        self.assertEqual(rc, EXIT_NOT_PROTECTED)
        self.assertIn("FAIL", out)

    def test_exit_two_when_unreadable_and_uncorroborated(self):
        rc, out, _err = self._run_cmd(_degraded_runner(branch=UNREADABLE_503))
        self.assertEqual(rc, EXIT_UNREADABLE)
        self.assertIn("UNREADABLE", out)

    def test_exit_three_when_unreadable_but_corroborated(self):
        rc, out, _err = self._run_cmd(_degraded_runner())
        self.assertEqual(rc, EXIT_DEGRADED)
        self.assertIn("DEGRADED", out)

    def test_the_degraded_pass_also_lands_in_the_d5_degrade_register(self):
        """So `mokata doctor` can answer "what degraded this session?" after it scrolled away."""
        rc, _out, err = self._run_cmd(_degraded_runner())
        self.assertEqual(rc, EXIT_DEGRADED)
        self.assertIn("branch-protection: DEGRADED", err)

    def test_a_green_run_emits_no_degrade_notice(self):
        import json
        _rc, _out, err = self._run_cmd(_runner_returning(0, json.dumps(SAFE_PAYLOAD)))
        self.assertNotIn("DEGRADED", err)


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


@unittest.skipUnless(os.path.exists(_RELEASE_SH),
                     "scripts/release.sh is dev-only — not shipped to the public mirror")
class ReleaseScriptThreeStates(unittest.TestCase):
    """release.sh's `verify_branch_protection` must DISPATCH on the three states, and the
    apply-protection remedy must live in exactly ONE of its arms.

    ⚠ EVERY assertion here is made against the function body with `#` comment lines STRIPPED.
    That is not tidiness — it is PIN-SUBSTRING-COMMENT-HOLE (0.0.16, the class two below this
    one): the comment block introducing this function explains the defect it fixes and therefore
    names every literal under pin. A whole-file grep would be satisfied by the prose after the
    control it guards had been deleted — green precisely when the control was gone."""

    @classmethod
    def setUpClass(cls):
        with open(_RELEASE_SH, encoding="utf-8") as fh:
            sh = fh.read()
        start = sh.find("verify_branch_protection() {")
        if start == -1:
            raise AssertionError("release.sh lost verify_branch_protection()")
        end = sh.find("\n}", start)
        if end == -1:
            raise AssertionError("release.sh's verify_branch_protection() is unterminated")
        body = sh[start:end]
        cls.body = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))

    def test_it_dispatches_on_the_exit_code_rather_than_on_truthiness(self):
        """`if cmd; then ... else ...` is the two-state shape. Three states need the code."""
        self.assertIn('case "$rc" in', self.body,
                      "the function must branch on the check's EXIT CODE — an `if` has two arms "
                      "and there are three states")
        for arm in ("0)", "1)", "2)", "3)"):
            self.assertIn(arm, self.body, f"no arm for exit {arm.rstrip(')')}")

    def test_the_apply_protection_remedy_appears_exactly_once(self):
        """State 2's arm, and nowhere else."""
        self.assertEqual(self.body.count("Apply protection (admin)"), 1,
                         "the apply-protection remedy must appear in exactly one arm — the one "
                         "reached when a SUCCESSFUL read said protection is missing")

    def test_no_arm_of_the_function_suggests_a_put(self):
        self.assertNotIn("-X PUT", self.body)
        self.assertNotIn("PUT", self.body)

    def test_the_unreadable_arm_forbids_applying_protection(self):
        self.assertIn("do NOT apply protection to clear it", self.body)

    def test_the_unreadable_arm_refuses(self):
        """Exit 2 must abort the cut — corroboration failing is still a refusal."""
        arm = self.body[self.body.find("    2)"):self.body.find("    *)")]
        self.assertIn("REFUSING TO RELEASE", arm)
        self.assertIn("exit 1", arm)
        self.assertIn("githubstatus.com", arm)

    def test_the_degraded_arm_proceeds_but_says_so_loudly_and_dated(self):
        arm = self.body[self.body.find("    3)"):self.body.find("    1)")]
        self.assertIn("DEGRADED", arm)
        self.assertIn("date -u +%Y-%m-%d", arm,
                      "the degraded notice must be DATED — an undated exemption cannot be aged out")
        self.assertNotIn("exit 1", arm, "state 3 proceeds; it does not refuse")

    def test_the_degraded_arm_names_all_four_unobtained_assurances(self):
        arm = self.body[self.body.find("    3)"):self.body.find("    1)")]
        for term in ("allow_force_pushes", "allow_deletions",
                     "required_status_checks.strict", "required_status_checks.contexts"):
            self.assertIn(term, arm, f"the degraded notice does not name {term}")

    def test_the_degraded_arm_names_where_it_must_be_recorded_and_what_expires_it(self):
        arm = self.body[self.body.find("    3)"):self.body.find("    1)")]
        self.assertIn("02-mokata-build-status.md", arm)
        self.assertIn("handoff/STATUS.md", arm)
        self.assertIn("BRANCH-PROTECTION-DEGRADED-PASS", arm)
        self.assertIn("0.0.19", arm)

    def test_an_unexpected_exit_code_refuses_without_suggesting_a_write(self):
        arm = self.body[self.body.find("    *)"):]
        self.assertIn("REFUSING TO RELEASE", arm)
        self.assertIn("exit 1", arm)
        self.assertNotIn("Apply protection", arm)

    def test_there_is_no_operator_settable_override(self):
        """No flag, no env var, no skip switch — the whole point is that the third state is earned
        from evidence, not granted by whoever is running the cut."""
        for smell in ("SKIP_BRANCH_PROTECTION", "--skip", "ALLOW_UNPROTECTED", "FORCE"):
            self.assertNotIn(smell, self.body)


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
        """KEPT, deliberately, after 0.0.17 stage 10's sweep replaced the other bespoke pin.

        NOT SUBSUMED, and the difference is the whole point: `test_s10_workflow_pins.py` asserts
        the SHAPE of every ref in every workflow (some 40-hex commit SHA), which is a supply-chain
        property. This asserts the IDENTITY of one — that scorecard-action is still pinned to the
        exact commit that was reviewed. A silent bump to a different, equally well-formed 40-hex
        SHA is green under the sweep and red here, and 'somebody repointed our Scorecard action at
        an unreviewed commit' is precisely the event worth catching.
        """
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
