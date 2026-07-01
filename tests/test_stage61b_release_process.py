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

Pure/offline; dependency-free (yaml is already present via mkdocs; the script checks are
parse-level — no shellcheck needed). Deterministic.
"""

import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _support import sample_manifest_data  # noqa: F401  (path-fix side-effect)

from mokata import __version__
from mokata.packaging import (
    check_release_consistency,
    read_version_fields,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELEASE_SH = os.path.join(ROOT, "scripts", "release.sh")
DOCS_YML = os.path.join(ROOT, ".github", "workflows", "docs.yml")


def _make_repo(tmp, *, pyproject="0.0.4", plugin="0.0.4", mp_meta="0.0.4",
               mp_plugin="0.0.4", pkg="0.0.4", omit=()):
    """Lay down a minimal repo carrying the five version fields, each independently
    settable (and any of them omittable) so a planted mismatch / missing file can be
    asserted to be NAMED, not a no-op."""
    os.makedirs(os.path.join(tmp, ".claude-plugin"), exist_ok=True)
    os.makedirs(os.path.join(tmp, "src", "mokata"), exist_ok=True)
    if "pyproject" not in omit:
        with open(os.path.join(tmp, "pyproject.toml"), "w") as fh:
            fh.write(f'[project]\nname = "mokata"\nversion = "{pyproject}"\n')
    if "plugin" not in omit:
        with open(os.path.join(tmp, ".claude-plugin", "plugin.json"), "w") as fh:
            json.dump({"name": "mokata", "version": plugin}, fh)
    if "marketplace" not in omit:
        with open(os.path.join(tmp, ".claude-plugin", "marketplace.json"), "w") as fh:
            json.dump({"name": "mostack", "metadata": {"version": mp_meta},
                       "plugins": [{"name": "mokata", "source": ".",
                                    "version": mp_plugin}]}, fh)
    if "pkg" not in omit:
        with open(os.path.join(tmp, "src", "mokata", "__init__.py"), "w") as fh:
            fh.write(f'__version__ = "{pkg}"\n')
    return tmp


class TestReleaseConsistency(unittest.TestCase):
    def test_passes_on_the_real_repo_at_its_own_version(self):
        # The committed repo must be internally consistent at __version__ (the live invariant).
        res = check_release_consistency(__version__, root=ROOT)
        self.assertTrue(res.consistent, res.render())
        self.assertEqual(res.mismatches, [])

    def test_reads_all_five_version_fields(self):
        tmp = _make_repo(self.tmp())
        fields = read_version_fields(tmp)
        # five fields: pyproject, plugin, marketplace×2, package __version__
        self.assertEqual(len(fields), 5)
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
        self.assertEqual(len(res.mismatches), 5)

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
    def setUp(self):
        import yaml
        with open(DOCS_YML) as fh:
            self.doc = yaml.safe_load(fh)
        with open(DOCS_YML) as fh:
            self.raw = fh.read()

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


class TestReleaseShHardening(unittest.TestCase):
    def setUp(self):
        with open(RELEASE_SH) as fh:
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
