"""Stage 68 — supply-chain trust (enterprise-approvable).

Signed releases (build provenance attestation) + SBOM + reproducible builds + a real
coordinated-disclosure policy. The actual signing/attestation + SBOM generation EXECUTE at
release time in CI (when the user cuts the tagged release); these tests assert the workflow
DECLARES those steps, is least-privilege, and is gated to the real repo — and that the local,
verifiable pieces (reproducible-build settings, the disclosure policy, the Stage-61b fail-closed
ordering) are present. YAML is PARSED; PyYAML is a required test dependency (requirements/ci.txt)
and its absence RAISES. This file was the worst of PYYAML-SKIP-CLUSTER (0.0.18 stage 2) — it held
the suite's only bare `if not _HAVE_YAML: return`, which reported a pass for the per-job repo
gating loop having checked one substring, with no skip marker to show for it.

What runs WHERE:
  * release-time (CI, on a `v*` tag, gated to the public repo): build -> reproducible-build
    check -> SBOM -> build-provenance attestation -> attach artifacts to the GitHub Release.
  * locally / in this suite: the workflow shape + least-privilege + repo gating, the
    reproducibility script + settings, SECURITY.md's policy, and the Stage-61b release order.
"""

import os
import unittest

import _release_repo_guards as rg
from _workflow_pins import safe_load

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
        doc = safe_load(self.text, "verify release.yml parses at all")
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
        # ⚠ THE SITE THE BACKLOG ROW CALLED THE WORST OF THE FOUR SHAPES. What stood here was a
        # bare `if not _HAVE_YAML: return` — no assertion, no fallback, no skip marker — so on a
        # runner without the parser this test reported PASS having checked that the gate string
        # appears SOMEWHERE in the file, never that each job carries it. A skip is at least
        # visible in the run summary; this was indistinguishable from a test that ran.
        # ⚠ AND `assertIn(REAL_REPO, …)` USED TO STAND HERE, WHICH IS THE SAME HOLE ONE LAYER IN:
        # `github.repository != 'JasGujral/mokata-oss'` CONTAINS the repository name, so a
        # substring test cannot tell a publish guard from its exact inverse. The class is now
        # decided by EVALUATING the condition against two synthetic repository names
        # (0.0.18 stage 7 — see tests/_release_repo_guards.py).
        doc = safe_load(self.text, "check every release job is gated to the real repo")
        for name, cls in rg.guard_classes(doc).items():
            with self.subTest(job=name):
                self.assertIn(cls, (rg.GUARD_PUBLISH, rg.GUARD_REFUSAL),
                              "release job '" + name + "' is neither gated to the real repo nor "
                              "an explicit refusal off it (class: " + cls + ")")

    def test_least_privilege_permissions(self):
        # Default (top-level) permission is read; only the job that attests elevates id-token.
        self.assertIn("id-token: write", self.text)
        self.assertIn("attestations: write", self.text)
        # The deleted fallback asserted `contents: read` appears somewhere in the file — true of
        # any job-level block, and of a comment, while the TOP-LEVEL default could be anything.
        doc = safe_load(self.text, "verify release.yml permissions are least-privilege")
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
    `github.repository == 'JasGujral/mokata-oss'` appears SOMEWHERE in release.yml, and it
    matches only the bare repo NAME. That does not make it impossible to add a NEW job with no
    `if:` that would run the GitHub release / OIDC PyPI publish from the PRIVATE
    `JasGujral/mokata` repo.

    This asserts EVERY job under `jobs:` carries an `if:` containing the full
    `github.repository == 'JasGujral/mokata-oss'` equality. If some job legitimately should NOT
    be gated, this test must FAIL and name it (update the test to exempt it explicitly) — never
    a silent exemption.

    ⚠⚠ IT DID FAIL, AND HERE IS THE EXEMPTION, NAMED (0.0.18 stage 7). Exactly ONE job carries
    the INVERSE guard `github.repository != 'JasGujral/mokata-oss'`:
    `refuse-to-publish-from-a-non-publishing-repository`. It exists because the equality guards,
    correct as they are, are SILENT — a tag pushed to the private repo skipped all five jobs and
    the run concluded GREEN, which is `TAG-ON-THE-DEV-REPO-IS-A-SILENT-NO-OP` (doc 84 §9). The
    refusal runs ONLY where publishing does not, so it cannot reach the publish path.

    The exemption is not a hole: the equality check below is replaced by a CLASSIFICATION over
    two synthetic repository names (`tests/_release_repo_guards.py`), so the guarded set is still
    derived, an unguarded sixth job still reddens, and a SECOND refusal job would too. A
    substring test could not have expressed this — `!=` contains `==`'s repository name.

    ⚠ THE INDENTATION-PARSE FALLBACK THAT LIVED HERE IS DELETED, AND ITS STATED REASON WAS
    FALSE (doc 85 §7h — a pin that justifies a behaviour is an argument, and arguments can be
    wrong). It said it existed "for the PyYAML-absent leg, so the invariant holds in CI's
    jsonschema/pyyaml-absent matrix legs". There is no pyyaml-absent leg: the `jsonschema` axis
    varies jsonschema, and every job that RUNS this module installs requirements/ci.txt —
    derived per job at 0.0.18 stage 2. So it was a second, never-exercised code path standing
    between a reader and the parse, which is where false greens live.
    """

    GUARD = "github.repository == '" + REAL_REPO + "'"

    # THE ONE EXEMPTION, NAMED. Not a list this test may grow quietly: `test_the_exemption_is_one
    # _job_and_it_is_this_one` asserts the refusal set is EXACTLY this, so a second refusal job —
    # or a rename of this one — reddens as loudly as an unguarded job would.
    REFUSAL_JOB = "refuse-to-publish-from-a-non-publishing-repository"

    def setUp(self):
        self.text = _read(RELEASE_YML)

    def _job_classes(self):
        """Map {job_name: guard class}, from the PARSED workflow.

        Attribution is the whole point: the guard must sit under the job it gates, not merely
        appear somewhere in the file. Only a parse can tell those apart — and only an EVALUATION
        can tell `==` from `!=`, which differ by one character and share every other one.
        """
        doc = safe_load(self.text, "attribute the repo gate to each release job")
        return rg.guard_classes(doc)

    def test_every_job_is_gated_to_the_oss_repo(self):
        classes = self._job_classes()
        self.assertTrue(classes, "release.yml has no jobs — parse failed")
        ungated = sorted(name for name, cls in classes.items()
                         if cls != rg.GUARD_PUBLISH and name != self.REFUSAL_JOB)
        self.assertEqual(
            ungated, [],
            "release.yml job(s) not gated to " + REAL_REPO + " — they could run the release / "
            "OIDC PyPI publish from the PRIVATE repo: " + ", ".join(ungated) + ". Every job's "
            "`if:` must contain \"" + self.GUARD + "\". If a job legitimately must NOT be gated, "
            "update this test to name the exemption explicitly.")

    def test_the_exemption_is_one_job_and_it_is_this_one(self):
        """A named exemption that could quietly cover a second job is a silent exemption with a
        label. The refusal set is asserted EXACT, in both directions."""
        classes = self._job_classes()
        self.assertEqual(
            sorted(n for n, c in classes.items() if c == rg.GUARD_REFUSAL), [self.REFUSAL_JOB])
        self.assertEqual(classes[self.REFUSAL_JOB], rg.GUARD_REFUSAL,
                         "the named exemption is no longer the inverse guard it was exempted for")


class TestPyPIPublishJob(unittest.TestCase):
    """Stage 4 — the tag-triggered PyPI publish job. It must publish the SAME reproducible,
    Sigstore-attested wheel+sdist the `build` job produced (via OIDC Trusted Publishing), never
    a fresh rebuild, and only after the whole matrix+validate+build is green. This freezes that
    CI/CD-linked design so a later edit can't silently rebuild or publish on a red gate."""

    def setUp(self):
        self.text = _read(RELEASE_YML)

    def _job(self):
        return safe_load(self.text, "verify the pypi publish job")["jobs"].get("pypi")

    def test_pypi_job_exists_gated_and_oidc(self):
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
        # The deleted fallback called two `assertIn`s "the two load-bearing invariants". They
        # are not: the load-bearing one is that `python -m build` does NOT appear in this job's
        # steps, and a substring search over the whole file cannot express a negative scoped to
        # one job. It reported a pass for the assertion it could not make.
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

    # `test_pypi_actions_are_sha_pinned` lived here and is DELETED (0.0.17 stage 10). It walked
    # this one job's steps asserting a 40-hex ref — the suite's ONLY generic pinning check, over
    # 1 job of 1 of the 9 workflows, and it skipTest'd itself away without PyYAML. That coverage
    # is now `tests/test_s10_workflow_pins.py`, which sweeps all nine, walks job-level `uses:`
    # as well as steps, and RAISES rather than skips when the parser is absent.
    # Subsumption was PROVEN before deletion, not assumed: mutant T06 in `_stage10_mutants.sh`
    # drops this exact pypi step from its SHA pin to the matching `@v<tag>` pin, and the new
    # sweep goes RED on it. The version is deliberately NOT named here — this comment used to
    # say `@v1.14.1`, dependabot moved the action to v1.14.2 mid-release, and prose that quotes
    # a pinned version is wrong from the next bump onward while reading as though it were fact.


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
    # Stage 3c.2 + `_to_delete/` (TODELETE-LEAK, 0.0.16) + the VS Code extension's build
    # artifacts (NODE_MODULES-LEAK, 0.0.16). Each must appear in BOTH the rsync --exclude list
    # and the INTERNAL_PATHS guard.
    REQUIRED = (
        "docs/build", "docs/launch", "docs/marketing", "docs/talks", "CLAUDE.md",
        "scripts/sync-public.sh", "scripts/release.sh",
        "build", "dist", "release-backup-*",
        "_to_delete",
        "editors/vscode/node_modules", "editors/vscode/out",
    )

    @staticmethod
    def _code_only(block):
        """`block` with every `#` comment line dropped.

        Load-bearing, not tidiness. Both controls are documented IN PLACE, and those comments name
        the very paths being pinned — the `_to_delete` entry sits under six lines of prose that say
        "_to_delete" four times. A raw substring check therefore passes on the COMMENT after the
        array entry it describes has been deleted, which is a pin that reports GREEN precisely when
        the control it guards is gone. Caught by mutation (TD-2 survived until this stripped)."""
        return "\n".join(ln for ln in block.splitlines() if not ln.lstrip().startswith("#"))

    def setUp(self):
        self.sh = _read(SYNC_SH)
        # Isolate the two controls so we assert against each independently.
        guard_start = self.sh.find("INTERNAL_PATHS=(")
        self.assertNotEqual(guard_start, -1, "sync-public.sh lost its INTERNAL_PATHS guard")
        # The guard is the ARRAY LITERAL, not "everything after it" — the array is followed by the
        # enforcement loop and its commentary, which mention internal paths in prose and would
        # otherwise satisfy a membership check on their own.
        guard_end = self.sh.find("\n)", guard_start)
        self.assertNotEqual(guard_end, -1, "INTERNAL_PATHS array is unterminated")
        self.exclude_block = self._code_only(self.sh[:guard_start])
        self.guard_block = self._code_only(self.sh[guard_start:guard_end])

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

    def test_to_delete_scratch_is_in_BOTH_places(self):
        """TODELETE-LEAK (0.0.16). `_to_delete/` held 1.2GB of snapshot tarballs, ~90 git lock
        files and `_secrets_before.py` — mokata's OWN source module, sitting untracked at the repo
        root. `.gitignore` covered it and that is EXACTLY why it was dangerous: rsync copies the
        working tree and ignores `.gitignore`, so the gitignore entry made the directory invisible
        to `git status` while leaving it fully live to the mirror.

        Asserted as its own case, not only as a `REQUIRED` row, because the two controls fail
        DIFFERENTLY and the failure text has to say which one went missing: losing the `--exclude`
        leaks the bytes, losing the `INTERNAL_PATHS` entry removes the backstop that would have
        caught it. Both directions are mutation-proven."""
        self.assertIn("_to_delete", self.exclude_block,
                      "sync-public.sh lost its `--exclude='_to_delete/'` — scratch (and the "
                      "`_secrets_before.py` that lived in it) would rsync to the public mirror")
        self.assertIn("_to_delete", self.guard_block,
                      "sync-public.sh lost `_to_delete` from INTERNAL_PATHS — the hard-guard "
                      "would no longer abort a sync that carried it")

    def test_vscode_build_artifacts_are_in_BOTH_places(self):
        """NODE_MODULES-LEAK (0.0.16). Found by re-running TODELETE-LEAK's question against the
        rest of the tree: `editors/vscode/node_modules/` (240 files, 26MB of third-party npm
        packages) and `editors/vscode/out/` (10 compiled JS artifacts) were in NEITHER control.

        WHAT ACTUALLY HAPPENED, measured rather than assumed — and it is the reason this case
        exists. All 250 files WERE copied into the public checkout (they are in the 1126-file
        dry-run manifest), but they never reached the public repo: `.gitignore` is itself mirrored,
        so its lines 64-66 were sitting in the DEST checkout and `git add -A` skipped them.

        So the only thing standing between 26MB of vendored dependencies and the public repo was
        an ignore file that exists for an unrelated reason and is one edit from being changed —
        no sync control named these paths at all. That is an accident, not a control: `git add -f`,
        a decision to vendor the extension, or any tidy-up of those three lines publishes the lot,
        and nothing in `sync-public.sh` would have said a word. Both dirs are regenerable
        (`npm ci && npm run compile`), so the mirror loses nothing by dropping them.

        Asserted as its own case for the same reason `_to_delete` is: the two controls fail
        DIFFERENTLY and the message must say which one went missing. All four directions
        mutation-proven independently via `scripts/mutate.sh`."""
        for p in ("editors/vscode/node_modules", "editors/vscode/out"):
            self.assertIn(p, self.exclude_block,
                          "sync-public.sh lost its `--exclude='" + p + "/'` — the VS Code "
                          "extension's build artifacts would rsync to the public mirror "
                          "(.gitignore does NOT govern what rsync copies)")
            self.assertIn(p, self.guard_block,
                          "sync-public.sh lost `" + p + "` from INTERNAL_PATHS — the hard-guard "
                          "would no longer catch a sync that carried it")


if __name__ == "__main__":
    unittest.main()
