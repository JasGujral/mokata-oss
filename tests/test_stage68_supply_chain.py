"""Stage 68 — supply-chain trust (enterprise-approvable).

Signed releases (build provenance attestation) + SBOM + reproducible builds + a real
coordinated-disclosure policy. The actual signing/attestation + SBOM generation EXECUTE at
release time in CI (when the user cuts the tagged release); these tests assert the workflow
DECLARES those steps, is least-privilege, and is gated to the real repo — and that the local,
verifiable pieces (reproducible-build settings, the disclosure policy, the Stage-61b fail-closed
ordering) are present. YAML is parsed when PyYAML is available (not a mokata dependency); the
structural text checks run either way.

What runs WHERE:
  * release-time (CI, on a `v*` tag, gated to the public repo): build -> reproducible-build
    check -> SBOM -> build-provenance attestation -> attach artifacts to the GitHub Release.
  * locally / in this suite: the workflow shape + least-privilege + repo gating, the
    reproducibility script + settings, SECURITY.md's policy, and the Stage-61b release order.
"""

import os
import unittest

try:
    import yaml
    _HAVE_YAML = True
except ImportError:
    _HAVE_YAML = False

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELEASE_YML = os.path.join(ROOT, ".github", "workflows", "release.yml")
REPRO_SH = os.path.join(ROOT, "scripts", "check-reproducible.sh")
SECURITY_MD = os.path.join(ROOT, "SECURITY.md")
RELEASE_SH = os.path.join(ROOT, "scripts", "release.sh")
SYNC_SH = os.path.join(ROOT, "scripts", "sync-public.sh")
REAL_REPO = "JasGujral/mokata-oss"


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class TestReleaseWorkflowSigningAndSBOM(unittest.TestCase):
    def setUp(self):
        self.text = _read(RELEASE_YML)

    def test_release_yaml_parses(self):
        if not _HAVE_YAML:
            self.skipTest("PyYAML not installed (not a mokata dependency)")
        doc = yaml.safe_load(self.text)
        self.assertIn("jobs", doc)

    def test_declares_build_provenance_attestation(self):
        # GitHub's native SLSA build-provenance attestation signs the built artifacts at release.
        self.assertIn("attest-build-provenance", self.text)

    def test_declares_sbom_generation(self):
        # CycloneDX SBOM for the package, attached to the release.
        self.assertIn("cyclonedx", self.text.lower())
        self.assertRegex(self.text, r"sbom[\w.-]*\.json",
                         "the SBOM output file isn't named/attached")

    def test_signing_steps_are_gated_to_the_real_repo(self):
        # Signing/attestation must be a no-op on a fork/mirror.
        self.assertIn("github.repository == '" + REAL_REPO + "'", self.text)
        if not _HAVE_YAML:
            return
        doc = yaml.safe_load(self.text)
        for name, job in doc["jobs"].items():
            with self.subTest(job=name):
                self.assertIn(REAL_REPO, str(job.get("if", "")),
                              "release job '" + name + "' is not gated to the real repo")

    def test_least_privilege_permissions(self):
        # Default (top-level) permission is read; only the job that attests elevates id-token.
        self.assertIn("id-token: write", self.text)
        self.assertIn("attestations: write", self.text)
        if not _HAVE_YAML:
            self.assertIn("contents: read", self.text)
            return
        doc = yaml.safe_load(self.text)
        top = doc.get("permissions", {})
        self.assertEqual(top.get("contents"), "read",
                         "top-level permissions must default to least-privilege (contents: read)")
        # id-token:write must be scoped to a job, NOT granted workspace-wide at the top level
        self.assertNotEqual(top.get("id-token"), "write",
                            "id-token: write must be per-job, not a top-level default")
        idtoken = [n for n, j in doc["jobs"].items()
                   if (j.get("permissions") or {}).get("id-token") == "write"]
        self.assertTrue(idtoken, "no job declares the scoped id-token: write")
        # Any job that ATTESTS build provenance must ALSO hold id-token: write (Sigstore needs
        # OIDC). The reverse does not hold: a job may hold id-token: write WITHOUT attesting —
        # e.g. the Stage-4 `pypi` job uses OIDC purely for PyPI Trusted Publishing.
        attesting = [n for n, j in doc["jobs"].items()
                     if (j.get("permissions") or {}).get("attestations") == "write"]
        self.assertTrue(attesting, "no job declares the scoped attestations: write for provenance")
        for n in attesting:
            perms = doc["jobs"][n]["permissions"]
            self.assertEqual(perms.get("id-token"), "write",
                             "job '" + n + "' attests but lacks the id-token: write it needs")

    def test_artifacts_and_sbom_attached_to_release(self):
        # The release publishes the built dist + the SBOM (not just notes).
        self.assertIn("dist/", self.text)
        self.assertIn("files:", self.text)


class TestEveryReleaseJobIsRepoGated(unittest.TestCase):
    """Stage 4g — the release/publish pipeline is OSS-only, un-regressably.

    The other guards (above, and test_repo_hardening.py) assert the gate string
    `github.repository == 'JasGujral/mokata-oss'` appears SOMEWHERE in release.yml, and the
    YAML leg of test_signing_steps_are_gated_to_the_real_repo loops the jobs — but that leg
    is skipped when PyYAML is absent, and it matches only the bare repo NAME. Neither makes
    it impossible to add a NEW job with no `if:` that would run the GitHub release / OIDC
    PyPI publish from the PRIVATE `JasGujral/mokata` repo.

    This asserts EVERY job under `jobs:` carries an `if:` containing the full
    `github.repository == 'JasGujral/mokata-oss'` equality — with a real plain-text fallback
    (an indentation parse of the `jobs:` block) for the PyYAML-absent leg, so the invariant
    holds in CI's jsonschema/pyyaml-absent matrix legs too. If some job legitimately should
    NOT be gated, this test must FAIL and name it (update the test to exempt it explicitly) —
    never a silent exemption.
    """

    GUARD = "github.repository == '" + REAL_REPO + "'"

    def setUp(self):
        self.text = _read(RELEASE_YML)

    def _job_if_conditions(self):
        """Map {job_name: its `if:` text}. PyYAML when present; an indentation parse otherwise.

        The fallback splits the top-level `jobs:` block by indentation into one text blob per
        job, so the guard is attributed to the job it actually sits under (not merely present
        somewhere in the file).
        """
        if _HAVE_YAML:
            doc = yaml.safe_load(self.text)
            return {name: str(job.get("if", "")) for name, job in doc["jobs"].items()}
        return self._job_blocks_textual()

    def _job_blocks_textual(self):
        lines = self.text.splitlines()
        # Find the top-level `jobs:` key.
        i = 0
        while i < len(lines) and lines[i].rstrip() != "jobs:":
            i += 1
        i += 1
        blocks, current, child_indent = {}, None, None
        for line in lines[i:]:
            if not line.strip() or line.lstrip().startswith("#"):
                if current is not None:
                    blocks[current] += line + "\n"
                continue
            indent = len(line) - len(line.lstrip())
            if child_indent is None:
                child_indent = indent
            if indent < child_indent:
                break                                   # dedented out of the `jobs:` block
            if indent == child_indent and line.rstrip().endswith(":"):
                current = line.strip().rstrip(":")      # a top-level job header
                blocks[current] = ""
            elif current is not None:
                blocks[current] += line + "\n"
        return blocks

    def test_every_job_is_gated_to_the_oss_repo(self):
        conditions = self._job_if_conditions()
        self.assertTrue(conditions, "release.yml has no jobs — parse failed")
        ungated = sorted(name for name, cond in conditions.items() if self.GUARD not in cond)
        self.assertEqual(
            ungated, [],
            "release.yml job(s) not gated to " + REAL_REPO + " — they could run the release / "
            "OIDC PyPI publish from the PRIVATE repo: " + ", ".join(ungated) + ". Every job's "
            "`if:` must contain \"" + self.GUARD + "\". If a job legitimately must NOT be gated, "
            "update this test to name the exemption explicitly.")


class TestPyPIPublishJob(unittest.TestCase):
    """Stage 4 — the tag-triggered PyPI publish job. It must publish the SAME reproducible,
    Sigstore-attested wheel+sdist the `build` job produced (via OIDC Trusted Publishing), never
    a fresh rebuild, and only after the whole matrix+validate+build is green. This freezes that
    CI/CD-linked design so a later edit can't silently rebuild or publish on a red gate."""

    def setUp(self):
        self.text = _read(RELEASE_YML)

    def _job(self):
        return yaml.safe_load(self.text)["jobs"].get("pypi")

    def test_pypi_job_exists_gated_and_oidc(self):
        if not _HAVE_YAML:
            self.skipTest("PyYAML not installed (not a mokata dependency); run in CI")
        job = self._job()
        self.assertIsNotNone(job, "release.yml has no `pypi` publish job")
        # runs ONLY after the reproducible-build job — a red matrix/validate/build never publishes
        self.assertEqual(job.get("needs"), ["build"],
                         "pypi must `needs: [build]` (publish only on a successful version)")
        # gated to the real repo like the sibling jobs (no publish from a fork/mirror)
        self.assertIn(REAL_REPO, str(job.get("if", "")),
                      "the pypi job is not gated to the real repo")
        # OIDC trusted publishing — id-token scoped to THIS job, no token secret
        self.assertEqual((job.get("permissions") or {}).get("id-token"), "write",
                         "pypi needs id-token: write for OIDC trusted publishing")

    def test_pypi_publishes_the_built_artifact_not_a_rebuild(self):
        if not _HAVE_YAML:
            # text-level fallback still proves the two load-bearing invariants
            self.assertIn("gh-action-pypi-publish", self.text)
            self.assertIn("mokata-dist", self.text)
            return
        steps = self._job()["steps"]
        uses = [str(s.get("uses", "")) for s in steps]
        runs = [str(s.get("run", "")) for s in steps]
        # publishes via the official PyPA action...
        self.assertTrue(any("pypa/gh-action-pypi-publish" in u for u in uses),
                        "pypi must publish via pypa/gh-action-pypi-publish")
        # ...reusing the SAME artifact the build job produced (download-artifact, mokata-dist)...
        self.assertTrue(any("actions/download-artifact" in u for u in uses),
                        "pypi must download the built artifact, not rebuild")
        self.assertIn("mokata-dist", str(steps),
                      "pypi must reuse the `mokata-dist` bundle the build job uploaded")
        # ...and MUST NOT re-run `python -m build` (a rebuild ≠ the signed, attested wheel).
        self.assertTrue(all("python -m build" not in r for r in runs),
                        "pypi must NOT rebuild — publish the reproducible, attested artifact")

    def test_pypi_actions_are_sha_pinned(self):
        # Supply-chain hygiene: third-party actions pinned to a 40-hex commit SHA, like the rest.
        import re
        if not _HAVE_YAML:
            self.skipTest("PyYAML not installed (not a mokata dependency); run in CI")
        for s in self._job()["steps"]:
            u = str(s.get("uses", ""))
            if not u:
                continue
            ref = u.split("@", 1)[1] if "@" in u else ""
            self.assertRegex(ref, r"^[0-9a-f]{40}$",
                             "action '" + u + "' must be pinned to a 40-char commit SHA")


class TestReproducibleBuild(unittest.TestCase):
    def test_repro_script_exists_and_is_double_build_compare(self):
        self.assertTrue(os.path.isfile(REPRO_SH), "missing scripts/check-reproducible.sh")
        sh = _read(REPRO_SH)
        self.assertIn("SOURCE_DATE_EPOCH", sh)         # honor the canonical determinism knob
        self.assertIn("set -euo pipefail", sh)         # fail-closed like release.sh
        # builds TWICE and compares the artifacts (sha256 / cmp / diff)
        builds = sh.count("python -m build") + sh.count("python3 -m build")
        self.assertGreaterEqual(builds, 2, "the script must build twice")
        self.assertTrue(any(tok in sh for tok in ("sha256", "shasum", "cmp ", "diff ")),
                        "the script must compare the two builds byte-for-byte")

    def test_workflow_sets_source_date_epoch(self):
        # Reproducibility honored at release: SOURCE_DATE_EPOCH is set from the commit time.
        self.assertIn("SOURCE_DATE_EPOCH", _read(RELEASE_YML))

    def test_sdist_normalizer_exists_and_is_wired(self):
        norm = os.path.join(ROOT, "scripts", "normalize_sdist.py")
        self.assertTrue(os.path.isfile(norm), "missing scripts/normalize_sdist.py")
        # the reproducible-build check AND the release build both normalize the sdist
        self.assertIn("normalize_sdist.py", _read(REPRO_SH))
        self.assertIn("normalize_sdist.py", _read(RELEASE_YML))

    def test_normalizer_only_touches_metadata_not_contents(self):
        # honest claim: normalization rewrites tar/gzip metadata only, never file contents.
        src = _read(os.path.join(ROOT, "scripts", "normalize_sdist.py"))
        self.assertIn("mtime", src)
        self.assertIn("SOURCE_DATE_EPOCH", src)

    def test_normalizer_makes_two_tarballs_byte_identical(self):
        # Build-free, dependency-free proof: two tarballs with identical CONTENTS but different
        # member mtimes/ownership normalize to byte-identical archives (the sdist gap we closed).
        import gzip
        import hashlib
        import io
        import tarfile
        import tempfile

        sys_path = os.path.join(ROOT, "scripts")
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "normalize_sdist", os.path.join(sys_path, "normalize_sdist.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        def make(path, mtime, uname):
            buf = io.BytesIO()
            tf = tarfile.open(fileobj=buf, mode="w")
            payload = b"print('mokata')\n"
            for name in ("pkg/PKG-INFO", "pkg/mod.py"):
                ti = tarfile.TarInfo(name)
                ti.size = len(payload)
                ti.mtime = mtime           # differs between the two
                ti.uname = uname           # differs between the two
                tf.addfile(ti, io.BytesIO(payload))
            tf.close()
            with open(path, "wb") as fh:
                fh.write(gzip.compress(buf.getvalue()))

        with tempfile.TemporaryDirectory() as d:
            a, b = os.path.join(d, "a.tar.gz"), os.path.join(d, "b.tar.gz")
            make(a, 1000, "alice")
            make(b, 9999, "bob")
            os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
            try:
                mod.normalize(a)
                mod.normalize(b)
            finally:
                os.environ.pop("SOURCE_DATE_EPOCH", None)
            with open(a, "rb") as fa, open(b, "rb") as fb:
                ha = hashlib.sha256(fa.read()).hexdigest()
                hb = hashlib.sha256(fb.read()).hexdigest()
            self.assertEqual(ha, hb, "normalized tarballs are not byte-identical")


class TestSecurityDisclosurePolicy(unittest.TestCase):
    def setUp(self):
        self.text = _read(SECURITY_MD)

    def test_coordinated_disclosure_policy(self):
        low = self.text.lower()
        self.assertIn("coordinated", low)
        self.assertIn("disclos", low)

    def test_private_reporting_via_github_advisories(self):
        self.assertIn("security/advisories/new", self.text)
        self.assertIn("Report a vulnerability", self.text)

    def test_supported_versions_table(self):
        low = self.text.lower()
        self.assertIn("supported versions", low)
        self.assertIn("|", self.text)   # a markdown table

    def test_scope_and_response_expectations(self):
        low = self.text.lower()
        self.assertIn("scope", low)
        # reasonable, non-binding language about response times (no over-promised hard SLA)
        self.assertTrue(any(p in low for p in ("aim to", "best effort", "endeavou", "target")),
                        "response expectations should use reasonable, non-binding language")

    def test_points_at_artifact_verification(self):
        # an enterprise reviewer can verify the supply chain
        low = self.text.lower()
        self.assertTrue("sbom" in low or "attest" in low or "provenance" in low,
                        "SECURITY.md should reference the signed-release / SBOM verification")


@unittest.skipUnless(os.path.exists(RELEASE_SH),
                     "release.sh is dev-only, excluded from the public mirror")
class TestReleaseOrderIntact(unittest.TestCase):
    """Stage 68 must not weaken the prior (Stage-61b) fail-closed release order."""

    def setUp(self):
        self.sh = _read(RELEASE_SH)

    def test_release_sh_still_fail_closed(self):
        self.assertIn("set -euo pipefail", self.sh)
        self.assertIn("release-check", self.sh)

    def test_tag_only_after_sync_and_check(self):
        sync = self.sh.find("scripts/sync-public.sh")
        check = self.sh.find("release-check")
        tag = self.sh.find('git tag -a "$TAG"')
        self.assertNotEqual(tag, -1)
        self.assertNotEqual(sync, -1)
        self.assertLess(sync, tag, "tagging must come AFTER the public mirror sync")
        self.assertLess(check, tag, "the version-consistency check must run BEFORE tagging")

    def test_release_yml_still_has_the_version_validate_gate(self):
        text = _read(RELEASE_YML)
        self.assertIn("validate", text)
        self.assertIn("Version consistency", text)


@unittest.skipUnless(os.path.exists(SYNC_SH),
                     "sync-public.sh is dev-only, excluded from the public mirror")
class TestSyncPublicBoundaryHardened(unittest.TestCase):
    """The public mirror is an rsync of the WORKING TREE — .gitignore does not protect it, so
    regenerable/internal artifacts (build/, dist/, release-backup-*) would leak if they reappear.
    Both the --exclude list AND the INTERNAL_PATHS hard-guard must cover every internal path; a
    future edit that drops one from either place fails here (the guard's comment requires them to
    stay in step)."""

    # The seven long-standing internal paths + the three regenerable/backup artifacts added in
    # Stage 3c.2. Each must appear in BOTH the rsync --exclude list and the INTERNAL_PATHS guard.
    REQUIRED = (
        "docs/build", "docs/launch", "docs/marketing", "CLAUDE.md",
        "scripts/sync-public.sh", "scripts/release.sh", "scripts/release-0.0.1.sh",
        "build", "dist", "release-backup-*",
    )

    def setUp(self):
        self.sh = _read(SYNC_SH)
        # Isolate the two controls so we assert against each independently.
        guard_start = self.sh.find("INTERNAL_PATHS=(")
        self.assertNotEqual(guard_start, -1, "sync-public.sh lost its INTERNAL_PATHS guard")
        self.exclude_block = self.sh[:guard_start]
        self.guard_block = self.sh[guard_start:]

    def test_exclude_list_covers_every_internal_path(self):
        for p in self.REQUIRED:
            self.assertIn(p, self.exclude_block,
                          "rsync --exclude list is missing internal path '" + p + "'")

    def test_guard_covers_every_internal_path(self):
        for p in self.REQUIRED:
            self.assertIn(p, self.guard_block,
                          "INTERNAL_PATHS hard-guard is missing internal path '" + p + "'")

    def test_regenerable_artifacts_are_new_this_stage(self):
        # Stage 3c.2 specifically: these must never leak even though .gitignore covers them.
        for p in ("build", "dist", "release-backup-*"):
            self.assertIn(p, self.exclude_block, "missing --exclude for '" + p + "'")
            self.assertIn(p, self.guard_block, "missing INTERNAL_PATHS entry for '" + p + "'")


if __name__ == "__main__":
    unittest.main()
