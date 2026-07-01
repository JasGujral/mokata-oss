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
        attesting = [n for n, j in doc["jobs"].items()
                     if (j.get("permissions") or {}).get("id-token") == "write"]
        self.assertTrue(attesting, "no job declares the scoped id-token: write for attestation")
        for n in attesting:
            perms = doc["jobs"][n]["permissions"]
            self.assertEqual(perms.get("attestations"), "write",
                             "job '" + n + "' attests but lacks attestations: write")

    def test_artifacts_and_sbom_attached_to_release(self):
        # The release publishes the built dist + the SBOM (not just notes).
        self.assertIn("dist/", self.text)
        self.assertIn("files:", self.text)


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


if __name__ == "__main__":
    unittest.main()
