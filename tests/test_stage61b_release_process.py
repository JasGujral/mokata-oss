"""Stage 61b — release-process hardening (from the 0.0.4 cut's lessons).

At the 0.0.4 cut the `v0.0.4` tag landed on a 0.0.3-content commit during manual recovery,
so the tag-triggered version-consistency CI went red, and the docs-on-tag deploy failed
benignly (Pages env protection). This battery proves neither can recur:

  * `release-check` (pure/offline) PASSES only when all version fields equal the intended
    tag, and FAILS naming the offender on any mismatch (proven NOT a no-op);
  * `scripts/release.sh` verifies version-consistency AT THE EXACT COMMIT being tagged and
    FAILS CLOSED — the tag is created only AFTER the public sync + a passing check;
  * `.github/workflows/docs.yml` gates the Pages DEPLOY to `main` only (not tags), while the
    docs BUILD still runs on every trigger (verification kept);
  * the new `release-check` CLI command stays declared in the 54e parity matrix.

Pure/offline; dependency-free. PyYAML is NOT a mokata dependency, so the docs-deploy-gating
checks (which need a parsed workflow) skip when it's absent and run for real in CI, which
installs it for exactly these workflow-lint tests. The script checks are parse-level — no
shellcheck needed. Deterministic.
"""

import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _support import sample_manifest_data  # noqa: F401  (path-fix side-effect)
from _release_repo_guards import call_arguments, live_calls
from _workflow_pins import safe_load

from mokata import __version__
from mokata.packaging import (
    ACTION_PIN_PATHS,
    check_release_consistency,
    read_version_fields,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELEASE_SH = os.path.join(ROOT, "scripts", "release.sh")
DOCS_YML = os.path.join(ROOT, ".github", "workflows", "docs.yml")


def _make_repo(tmp, *, pyproject="0.0.4", plugin="0.0.4", mp_meta="0.0.4",
               mp_plugin="0.0.4", pkg="0.0.4", omit=()):
    """Lay down a minimal repo carrying every GUARDED field — the five version fields plus
    the two PIN-DRIFT `action-pin` entries — each independently settable (and any of them
    omittable) so a planted mismatch / missing file can be asserted to be NAMED, not a
    no-op. The pins always track `pyproject` here; their own drift cases live in
    tests/test_pin_drift.py."""
    os.makedirs(os.path.join(tmp, ".claude-plugin"), exist_ok=True)
    os.makedirs(os.path.join(tmp, "src", "mokata"), exist_ok=True)
    if "pyproject" not in omit:
        with open(os.path.join(tmp, "pyproject.toml"), "w", encoding="utf-8") as fh:
            fh.write(f'[project]\nname = "mokata"\nversion = "{pyproject}"\n')
    if "plugin" not in omit:
        with open(os.path.join(tmp, ".claude-plugin", "plugin.json"), "w", encoding="utf-8") as fh:
            json.dump({"name": "mokata", "version": plugin}, fh)
    if "marketplace" not in omit:
        with open(os.path.join(tmp, ".claude-plugin", "marketplace.json"), "w", encoding="utf-8") as fh:
            json.dump({"name": "mostack", "metadata": {"version": mp_meta},
                       "plugins": [{"name": "mokata", "source": ".",
                                    "version": mp_plugin}]}, fh)
    if "pkg" not in omit:
        with open(os.path.join(tmp, "src", "mokata", "__init__.py"), "w", encoding="utf-8") as fh:
            fh.write(f'__version__ = "{pkg}"\n')
    for rel in ACTION_PIN_PATHS:                       # PIN-DRIFT (0.0.15)
        path = os.path.join(tmp, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("uses: JasGujral/mokata-oss/.github/actions/"
                     f"mokata-check@v{pyproject}\n")
    return tmp


class TestReleaseConsistency(unittest.TestCase):
    def test_passes_on_the_real_repo_at_its_own_version(self):
        # The committed repo must be internally consistent at __version__ (the live invariant).
        res = check_release_consistency(__version__, root=ROOT)
        self.assertTrue(res.consistent, res.render())
        self.assertEqual(res.mismatches, [])

    def test_reads_all_guarded_version_fields(self):
        tmp = _make_repo(self.tmp())
        fields = read_version_fields(tmp)
        # five version fields (pyproject, plugin, marketplace×2, package __version__)
        # + the two PIN-DRIFT action pins
        self.assertEqual(len(fields), 5 + len(ACTION_PIN_PATHS))
        self.assertTrue(all(v == "0.0.4" for v in fields.values()), fields)

    def test_passes_when_all_match_the_target(self):
        tmp = _make_repo(self.tmp())
        res = check_release_consistency("0.0.4", root=tmp)
        self.assertTrue(res.consistent, res.render())

    def test_v_prefix_is_normalized(self):
        tmp = _make_repo(self.tmp())
        self.assertTrue(check_release_consistency("v0.0.4", root=tmp).consistent)

    def test_fails_on_a_planted_single_mismatch_naming_the_offender(self):
        # Plant ONE wrong field (the 0.0.4 saga: one location lagged the tag). It must FAIL
        # and the offending location must be named — proving the check is not a no-op.
        tmp = _make_repo(self.tmp(), plugin="0.0.3")
        res = check_release_consistency("0.0.4", root=tmp)
        self.assertFalse(res.consistent)
        self.assertEqual(len(res.mismatches), 1)
        report = res.render()
        self.assertIn("plugin.json", report)
        self.assertIn("0.0.3", report)
        # the single offender is the planted field — the matching fields are NOT offenders
        self.assertEqual(res.mismatches[0][0], "plugin.json:version")
        offenders_line = [ln for ln in report.splitlines() if "offenders" in ln][0]
        self.assertIn("plugin.json", offenders_line)
        self.assertNotIn("pyproject.toml", offenders_line)

    def test_fails_when_tag_differs_from_all_fields(self):
        # The 0.0.4-tag-on-0.0.3-commit shape: every field == 0.0.3 but the intended tag 0.0.4.
        tmp = _make_repo(self.tmp(), pyproject="0.0.3", plugin="0.0.3", mp_meta="0.0.3",
                         mp_plugin="0.0.3", pkg="0.0.3")
        res = check_release_consistency("0.0.4", root=tmp)
        self.assertFalse(res.consistent)
        self.assertEqual(len(res.mismatches), 5 + len(ACTION_PIN_PATHS))

    def test_missing_file_is_a_named_mismatch_not_a_crash(self):
        tmp = _make_repo(self.tmp(), omit=("plugin",))
        res = check_release_consistency("0.0.4", root=tmp)   # must not raise
        self.assertFalse(res.consistent)
        self.assertIn("plugin.json", res.render())

    def test_empty_target_fails_closed(self):
        tmp = _make_repo(self.tmp())
        self.assertFalse(check_release_consistency("", root=tmp).consistent)

    def tmp(self):
        import tempfile
        return tempfile.mkdtemp()


class TestReleaseCheckCLI(unittest.TestCase):
    def _run(self, argv):
        from mokata.cli import main
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(argv)
        return rc, out.getvalue() + err.getvalue()

    def test_release_check_passes_on_the_repo_default_version(self):
        rc, _ = self._run(["release-check", "--root", ROOT])
        self.assertEqual(rc, 0)

    def test_release_check_explicit_matching_version_passes(self):
        rc, _ = self._run(["release-check", __version__, "--root", ROOT])
        self.assertEqual(rc, 0)

    def test_release_check_fails_nonzero_on_mismatch(self):
        rc, text = self._run(["release-check", "9.9.9", "--root", ROOT])
        self.assertEqual(rc, 1)                    # fail-closed exit code for release.sh
        self.assertIn("9.9.9", text)               # names the intended tag it checked against


class TestDocsDeployGatedToMain(unittest.TestCase):
    """The Pages deploy is main-only. This class used to `skipUnless(_HAVE_YAML, …)`, which on a
    runner without the parser reported OK for a gate it had not looked at (PYYAML-SKIP-CLUSTER)."""

    def setUp(self):
        with open(DOCS_YML, encoding="utf-8") as fh:
            self.raw = fh.read()
        self.doc = safe_load(self.raw, "verify the Pages deploy is gated to the main branch")

    def test_a_deploy_job_is_gated_to_main_only(self):
        jobs = self.doc.get("jobs", {})
        deploy_jobs = [j for j in jobs.values()
                       if "deploy-pages" in json.dumps(j)]
        self.assertTrue(deploy_jobs, "no job runs actions/deploy-pages")
        for j in deploy_jobs:
            cond = str(j.get("if", ""))
            self.assertIn("refs/heads/main", cond,
                          "the Pages deploy must be gated to the main branch (not tags)")

    def test_deploy_is_not_reachable_on_a_tag_or_release_ref(self):
        # No deploy-pages step may live in a job that lacks the main-branch gate.
        jobs = self.doc.get("jobs", {})
        for name, j in jobs.items():
            if "deploy-pages" in json.dumps(j):
                self.assertIn("refs/heads/main", str(j.get("if", "")),
                              f"job '{name}' deploys without a main-only gate")

    def test_docs_build_still_runs_on_every_trigger(self):
        # The build (mkdocs --strict) must NOT be gated to main — it verifies docs on tags
        # / releases / PRs too. Find a job that builds but is not main-gated.
        jobs = self.doc.get("jobs", {})
        build_jobs = [j for j in jobs.values()
                      if "mkdocs build" in json.dumps(j)]
        self.assertTrue(build_jobs, "no job runs `mkdocs build --strict`")
        self.assertTrue(
            any("refs/heads/main" not in str(j.get("if", "")) for j in build_jobs),
            "the docs BUILD must still run on non-main refs (tags/releases) — keep it")


@unittest.skipUnless(os.path.exists(RELEASE_SH),
                     "release.sh is dev-only, excluded from the public mirror")
class TestReleaseShHardening(unittest.TestCase):
    def setUp(self):
        with open(RELEASE_SH, encoding="utf-8") as fh:
            self.sh = fh.read()

    def test_fails_closed(self):
        self.assertIn("set -euo pipefail", self.sh)

    def test_invokes_release_check_preflight(self):
        self.assertIn("release-check", self.sh)

    def test_verifies_at_the_mirror_checkout(self):
        # The exact commit being tagged on the PUBLIC mirror is verified: release-check is
        # invoked with --root, and there is a verify call against the mirror checkout.
        self.assertRegex(self.sh, r"release-check[^\n]*--root",
                         "release-check must verify a given checkout via --root")
        self.assertRegex(self.sh, r'verify_versions\s+"\$PUB_CHECKOUT"',
                         "the public mirror checkout must be version-verified before tagging")

    def test_tag_is_created_only_after_the_public_sync(self):
        # The 0.0.4 lesson: a tag must NEVER precede the version bump landing on the mirror.
        # Check the REAL executed calls (not the echoed manual-runbook lines).
        real_sync = self.sh.find('sync-public.sh "$PUB_CHECKOUT"')
        real_tag = self.sh.find('git tag -a "$TAG"')
        self.assertNotEqual(real_sync, -1, "release.sh no longer syncs the public mirror")
        self.assertNotEqual(real_tag, -1, "release.sh no longer tags")
        self.assertLess(real_sync, real_tag,
                        "git tag -a must come AFTER the public mirror sync")

    def test_release_check_runs_before_any_tag(self):
        check_pos = self.sh.find("release-check")
        real_tag = self.sh.find('git tag -a "$TAG"')
        self.assertNotEqual(check_pos, -1)
        self.assertLess(check_pos, real_tag,
                        "the version-consistency check must run BEFORE tagging")

    # --- Stage 2 (0.0.9): the release must be unreachable while any test is red ----------
    def test_local_test_preflight_runs_both_jsonschema_states_and_aborts(self):
        # A fail-closed LOCAL preflight runs the unit + integration suites the way CI does —
        # with jsonschema PRESENT and again ABSENT — up front, aborting on any failure, so a
        # locally-red suite never reaches a push/tag. (The cross-platform matrix is covered by
        # the wait-for-CI-green gate; this is the cheap first line of defence.)
        self.assertIn("run_test_preflight", self.sh,
                      "release.sh must define a local test preflight")
        # it runs the actual suites (unit + integration), mirroring CI's discover invocations
        self.assertRegex(self.sh, r"unittest discover -s tests\b",
                         "the preflight must run the unit suite")
        self.assertRegex(self.sh, r"unittest discover -s tests/integration",
                         "the preflight must run the integration suite")
        # both jsonschema legs are exercised — jsonschema installed for one, uninstalled for the other
        self.assertRegex(self.sh, r"pip install[^\n]*jsonschema",
                         "the preflight must run a jsonschema-PRESENT leg")
        self.assertRegex(self.sh, r"pip uninstall -y jsonschema",
                         "the preflight must run a jsonschema-ABSENT leg")
        # aborts on failure
        self.assertIn("REFUSING TO RELEASE", self.sh,
                      "the preflight must abort (fail-closed) on a red suite")
        # and it is actually invoked BEFORE the master push (no push on a locally-red tree)
        #
        # ⚠ `rfind` ON THE RAW TEXT USED TO STAND HERE and it was PIN-SUBSTRING-COMMENT-HOLE: the
        # last occurrence of the name is not the last CALL of it, so a comment mentioning the
        # preflight moved this assertion's idea of the call site past the push and reddened a
        # correct tree (0.0.18 stage 7). The call sites are now derived from the LIVE shell.
        calls = live_calls(self.sh, "run_test_preflight")
        self.assertTrue(calls, "release.sh never CALLS run_test_preflight")
        call_pos = len("\n".join(self.sh.splitlines()[:max(n for n, _a in calls) - 1]))
        push_pos = self.sh.find("git push origin master")
        self.assertNotEqual(call_pos, -1)
        self.assertNotEqual(push_pos, -1)
        self.assertLess(call_pos, push_pos,
                        "the local test preflight must run BEFORE pushing master")

    def test_ci_green_wait_for_the_publishing_repo_before_any_tag(self):
        """The MIRROR's CI gates the tag. The dev repo's does not, and never did since 0.0.10.

        ⚠⚠ THIS PIN USED TO ASSERT THE OPPOSITE, IT WAS GREEN FOR SEVEN RELEASES, AND IT IS WHERE
        THE FALSE CLAIM AT THE 0.0.17 CUT CAME FROM (doc 85 §7h — a pin that encodes a false
        premise then protects the defect; reversed at 0.0.18 stage 7). It was named
        `test_ci_green_wait_for_both_repos_before_any_tag`, its comment read *"on BOTH the dev repo
        and the public mirror, BEFORE the tag. A red CI on either repo can never reach a tag"*, and
        it asserted:

            self.assertRegex(self.sh, r'wait_for_ci_green\\s+"\\$DEV_REPO"',
                             "the dev repo's CI must be waited on before tagging")
            dev_wait = self.sh.find('wait_for_ci_green "$DEV_REPO"')
            self.assertLess(dev_wait, real_tag, ...)

        Every one of those was satisfied by the COMMENTED-OUT call at `release.sh:340` —
        `PIN-SUBSTRING-COMMENT-HOLE` exactly: a scanned region containing a disabled copy of the
        thing being scanned for. `RELEASE-SH-DEV-CI-WAIVER-OUTLIVED-ITS-SCOPE` says the belief was
        *"read from the function DEFINITION and never from the call site"*. It was ALSO read from
        this test, which is a stronger provenance than a misread line: the suite asserted it, in
        prose, and stayed green while it was false.

        The call sites are now derived from the LIVE shell (`live_calls`), so a commented-out call
        can never satisfy this again — and `test_tag_is_not_a_publish.py` fails on a disabled call
        anywhere in the script.
        """
        self.assertIn("wait_for_ci_green", self.sh,
                      "release.sh must define a CI-green wait helper")
        # uses gh to resolve the commit's CI run and watch it fail-closed
        self.assertRegex(self.sh, r"gh run watch[^\n]*--exit-status",
                         "the wait must use `gh run watch --exit-status` (fail-closed)")
        self.assertRegex(self.sh, r"gh run list[^\n]*--workflow CI",
                         "the wait must resolve the run via `gh run list ... --workflow CI`")
        self.assertRegex(self.sh, r"--commit",
                         "the wait must resolve the run for the EXACT commit")
        self.assertIn("REFUSING TO TAG", self.sh,
                      "a non-success / missing / timed-out CI run must abort before tagging")
        # Invoked for the repo that actually publishes — twice: the release PR branch, and the
        # merged main commit that is tagged. Derived from LIVE call sites, never from the text.
        calls = live_calls(self.sh, "wait_for_ci_green")
        repos = [call_arguments(arguments) for _line, arguments in calls]
        self.assertEqual(repos, ["$PUB_REPO", "$PUB_REPO"],
                         "the mirror's CI must be waited on twice (PR branch, merged main) and "
                         "nothing else may be waited on: found %s" % repos)
        # Both waits are positioned BEFORE the real tag step (a red CI can't reach a tag).
        real_tag_line = next(
            number for number, line in enumerate(self.sh.splitlines(), start=1)
            if 'git tag -a "$TAG"' in line)
        for line, _arguments in calls:
            self.assertLess(line, real_tag_line,
                            "a mirror CI-green wait sits after the tag step")


class TestParityStaysGreen(unittest.TestCase):
    def test_release_check_is_declared_in_the_matrix(self):
        from mokata.parity import SURFACE_MATRIX
        self.assertIn("release-check", SURFACE_MATRIX)
        # release plumbing → intentionally CLI/preflight, declared exempt with a reason
        self.assertTrue(SURFACE_MATRIX["release-check"].exempt)

    def test_parity_report_is_green(self):
        from mokata.parity import verify_parity
        report = verify_parity()
        self.assertTrue(report.ok, report.render())


if __name__ == "__main__":
    unittest.main()
