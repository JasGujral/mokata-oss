"""Stage 8 — SIGNED-RELEASES-PROVENANCE (ii): the attestation LEAVES THE RUNNER.

doc 84 §1. The row reads *"(ii) is one job step"* and that is the one thing about it which is
wrong: **the step already exists and predates the row.** `actions/attest-build-provenance` has
been in `release.yml` since stage 68, pinned, scoped, and producing a real Sigstore-signed
attestation on every single release. The gap is not that nothing attests.

    A WORKFLOW THAT ATTESTS AND A RELEASE THAT CARRIES THE ATTESTATION ARE TWO DIFFERENT CLAIMS,
    AND ONLY THE SECOND IS THE ONE A STRANGER CAN VERIFY.

That is P22, and it is **stage 6's finding one attestation over**: there, cosign signed and two
asset classes never reached the Release; here, the build is attested and the attestation never
reaches the Release. In both cases the workflow does the work and the artifact does not carry it.

WHAT THE ANSWER COST, DERIVED FROM THE ACTION'S OWN SOURCE
-----------------------------------------------------------
`actions/attest-build-provenance@0f67c3f4…` (v4.1.1) is a composite that delegates to
`actions/attest@a1948c3f…` (v4.1.1), whose `src/main.ts` reads:

    const ATTESTATION_FILE_NAME = 'attestation.json'
    const outputPath = path.join(await tempDir(), ATTESTATION_FILE_NAME)
    core.setOutput('bundle-path', outputPath)
    await fs.writeFile(outputPath, JSON.stringify(att.bundle) + os.EOL, {flag: 'a'})

So the signed bundle is already on disk, in JSON-Lines form, on every release run — published as
the `bundle-path` OUTPUT and consumed by nobody. The whole of (ii) is: give the step an `id`, copy
that file into `dist/` under a name ending `.intoto.jsonl`, and add ONE line to the declaration in
`scripts/check-release-assets.sh` (from which `files:` is already derived).

⚠ **NOT FOLDED INTO THE SIGNING STEP**, and the row says why: signing attests WHO signed the
artifact, provenance attests WHAT BUILT it. Conflating them is how the original row came to mean
two incompatible things. They remain two steps.

⚠ **(i) IS NOT DONE AND IS NOT COUNTED.** One release in the last-5 window predates cosign signing
and ages out at the next release. No work was done for it and none is claimed.

⚠ **WHAT IS ONLY VERIFIABLE AT A LIVE SCORECARD READ.** Nothing here publishes a Release, and
Scorecard cannot be re-run from this repo. The suffix-to-score rule in `_release_assets` is an
EXTERNAL reading — Scorecard's documented behaviour, cross-checked against our own observed 6 —
and this file grades the CONFIGURATION against it, never the score. See the stage-8 report.

THE FIFTH ASSET CLASS, AND STAGE 6's PIN GRADING THIS STAGE
------------------------------------------------------------
Adding a class to `files:` without adding it to the declaration is stage 6's defect re-created.
`TestStage6sPinCatchesThisStage` plants exactly that — a `files:` entry the script does not
declare, and the reverse — and watches stage 6's own derived pin red on both. A guard is only
worth what it catches on a defect somebody plants.

⚠ AND A **THIRD** COPY OF THE ASSET SET WAS FOUND WHILE DOING IT: the `pypi` job's
`rm -f dist/sbom.cdx.json dist/*.sigstore.json`. `files:` had been derived from the declaration
since stage 6 and this line had not, so a fifth class that reached the Release would have reached
twine too — a failure at the LAST job of a cut, after the Release is already published. It is
derived now.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import shutil
import subprocess
import tempfile
import unittest

import _support
from _support import sample_manifest_data  # noqa: F401  (path-fix side-effect)

import _release_assets as ra

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELEASE_YML = os.path.join(ROOT, ".github", "workflows", "release.yml")
CHECK_SCRIPT = os.path.join(ROOT, "scripts", "check-release-assets.sh")

BUILD_JOB = "build"
PYPI_JOB = "pypi"


def _read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _release_doc():
    return ra.safe_load(_read(RELEASE_YML), "pin the release provenance asset")


def _declared_assets():
    """Every published asset name the SCRIPT declares: each pattern plus its bundle."""
    patterns, suffix = ra.script_patterns(_read(CHECK_SCRIPT))
    if patterns is None:
        return None, None
    return patterns, suffix


# =================================================================================================
# THE STEP THAT ALREADY EXISTED, AND THE OUTPUT NOBODY READ
# =================================================================================================

class TestTheAttestationReachesTheRelease(unittest.TestCase):

    def setUp(self):
        self.doc = _release_doc()
        self.attest = ra.action_steps(self.doc, ra.ATTEST_ACTION)

    def test_the_attestation_step_exists_and_is_sha_pinned(self):
        """Stated first because the row is wrong about it: this predates the row by a release."""
        self.assertEqual(
            len(self.attest), 1,
            "expected exactly one %s step; found %d" % (ra.ATTEST_ACTION, len(self.attest)))
        ref = self.attest[0][2]["uses"].split("@", 1)[1].split()[0]
        self.assertEqual(len(ref), 40, "the attestation action is not SHA-pinned: %r" % ref)

    def test_the_attestation_step_declares_an_id(self):
        """An output nobody can name is an output nobody can read, which is the whole defect: the
        bundle was produced on every release for months and referenced nowhere."""
        step = self.attest[0][2]
        self.assertTrue(step.get("id"), "the attestation step has no `id:`, so its `bundle-path` "
                                        "output cannot be referenced by any later step")

    def test_some_step_READS_the_attestations_bundle_path_output(self):
        """★ THE GAP, ASSERTED DIRECTLY. Not 'a copy step exists' — that a step consumes THIS
        step's output, so renaming the id or dropping the reference reds."""
        attest_id = self.attest[0][2]["id"]
        readers = [(job_id, index) for job_id, index, step in ra.job_steps(self.doc)
                   if (attest_id, "bundle-path") in ra.output_references(
                       "\n".join(str(v) for v in (step.get("env") or {}).values())
                       + "\n" + str(step.get("run") or ""))]
        self.assertTrue(
            readers,
            "no step reads steps.%s.outputs.bundle-path — the attestation is created and stays on "
            "the runner, which is exactly the state this stage exists to end" % attest_id)

    def test_the_copy_runs_AFTER_the_attestation_and_BEFORE_the_signing(self):
        """Order is load-bearing in both directions and neither is obvious.

        BEFORE the attestation there is no bundle to copy. AFTER the signing the provenance asset
        would never be signed, so `check-release-assets.sh` would exit 4 on it — and worse, if the
        attestation's `subject-path: dist/*` ran after the copy, the attestation would include
        itself as a subject of itself.
        """
        job = [(index, step) for job_id, index, step in ra.job_steps(self.doc)
               if job_id == BUILD_JOB]
        self.assertTrue(job, "the build job was not found")
        attest_at = self.attest[0][1]
        copy_at = [index for index, step in job
                   if ra.PROVENANCE_ASSET in str(step.get("run") or "")]
        sign_at = [index for index, step in job
                   if "cosign sign-blob" in str(step.get("run") or "")]
        self.assertTrue(copy_at, "no step in the build job writes %s" % ra.PROVENANCE_ASSET)
        self.assertTrue(sign_at, "no cosign signing step in the build job")
        self.assertLess(attest_at, min(copy_at),
                        "the provenance bundle is copied before it is produced")
        self.assertLess(max(copy_at), min(sign_at),
                        "the provenance asset is attached after the signing loop, so it would "
                        "reach the Release unsigned and the asset-set check would exit 4 on it")

    def test_the_attestations_subjects_do_not_include_the_provenance_asset(self):
        """`subject-path: dist/*` is evaluated when the step runs. If the copy moved earlier, the
        attestation would take its own bundle as a subject."""
        subject = str(self.attest[0][2].get("with", {}).get("subject-path", ""))
        self.assertTrue(subject, "the attestation step declares no subject-path")
        self.assertNotIn(ra.PROVENANCE_ASSET, subject)


# =================================================================================================
# THE ASSET NAME — an external rule, applied to our declaration
# =================================================================================================

class TestTheProvenanceAssetIsDeclared(unittest.TestCase):

    def test_the_script_declares_the_provenance_asset(self):
        patterns, _suffix = _declared_assets()
        self.assertIsNotNone(patterns, "the pattern block could not be read out of the script")
        self.assertIn(
            ra.PROVENANCE_ASSET, patterns,
            "the asset-set declaration does not name the provenance asset, so a Release without "
            "it would pass every check: %r" % (patterns,))

    def test_the_provenance_asset_name_ends_in_the_suffix_scorecard_reads(self):
        """★ THE ONE PROPERTY THE WHOLE ROW TURNS ON. Scorecard matches the ASSET NAME's suffix;
        an attestation published as `attestation.json` scores exactly what publishing nothing
        scores. This is an EXTERNAL rule (see `_release_assets`) applied to a name we choose."""
        self.assertTrue(
            ra.PROVENANCE_ASSET.endswith(ra.SCORECARD_PROVENANCE_SUFFIX),
            "%r does not end in %r, so the Signed-Releases check will not see it as provenance"
            % (ra.PROVENANCE_ASSET, ra.SCORECARD_PROVENANCE_SUFFIX))

    def test_the_provenance_suffix_is_not_one_of_the_signature_suffixes(self):
        """Provenance and signature are scored differently and must not be conflated — the same
        distinction the row makes in words about the two steps."""
        for suffix in ra.SCORECARD_SIGNATURE_SUFFIXES:
            self.assertFalse(ra.SCORECARD_PROVENANCE_SUFFIX.endswith(suffix))

    def test_the_workflow_publishes_the_declared_provenance_asset(self):
        for _where, inputs in ra.upload_steps(_release_doc()):
            self.assertIn("dist/" + ra.PROVENANCE_ASSET, ra.declared_upload_patterns(inputs))


# =================================================================================================
# THE THIRD COPY OF THE ASSET SET — the pypi job's keep-only step
# =================================================================================================

class TestThePypiJobStripsEveryNonDistribution(unittest.TestCase):
    """Derived from the declaration, because a hand-maintained third copy is how the first two
    came to disagree."""

    def setUp(self):
        self.doc = _release_doc()
        self.patterns, self.suffix = _declared_assets()

    def strip_step(self):
        steps = [step for job_id, _index, step in ra.job_steps(self.doc)
                 if job_id == PYPI_JOB and "rm " in str(step.get("run") or "")]
        self.assertEqual(len(steps), 1, "expected one keep-only step in the pypi job: %r" % steps)
        return str(steps[0]["run"])

    def test_every_published_non_distribution_asset_is_removed_before_the_upload(self):
        run = self.strip_step()
        for pattern in self.patterns:
            if pattern.endswith(ra.DISTRIBUTION_SUFFIXES):
                continue
            self.assertIn(
                pattern, run,
                "the pypi job uploads dist/ verbatim and does not strip %r, which PyPI will "
                "reject at the LAST job of a cut — after the Release is already published"
                % pattern)
        self.assertIn(self.suffix, run, "the cosign bundles are not stripped before the upload")

    def test_the_distributions_themselves_are_NOT_stripped(self):
        """The negative. A strip step that removed the wheel would publish nothing, loudly — but
        only at the cut."""
        run = self.strip_step()
        for pattern in self.patterns:
            if pattern.endswith(ra.DISTRIBUTION_SUFFIXES):
                self.assertNotIn(pattern.lstrip("*"), run,
                                 "the pypi job strips %r, which is what it exists to upload"
                                 % pattern)

    def test_the_publish_step_uploads_the_downloaded_dist_not_a_rebuild(self):
        publish = ra.action_steps(self.doc, ra.PYPI_PUBLISH_ACTION)
        self.assertEqual(len(publish), 1)
        self.assertEqual(publish[0][2].get("with", {}).get("packages-dir"), "dist")


# =================================================================================================
# THE COPY STEP, EXECUTED — not asserted about (§7i)
# =================================================================================================

@unittest.skipUnless(_support.BASH, _support.NO_BASH)
class TestTheCopyStepRunsForReal(unittest.TestCase):
    """The step's own `run:` text, executed under bash with `BUNDLE_PATH` set, exactly as stage 7
    executes the refusal step rather than reading it. A step that has never been run is a step
    whose failure mode is a guess."""

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="provenance-")
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)
        os.mkdir(os.path.join(self.work, "dist"))
        job = [step for job_id, _i, step in ra.job_steps(_release_doc())
               if job_id == BUILD_JOB and ra.PROVENANCE_ASSET in str(step.get("run") or "")]
        self.assertEqual(len(job), 1, "expected one provenance-copy step: %r" % job)
        self.run_text = str(job[0]["run"])

    def execute(self, bundle_path):
        """⚠ `bash_argv`, not `["bash", ...]` — see `tests/test_windows_shell_and_paths.py`. A bare
        argv[0] resolves through CreateProcess, which searches System32 before PATH and finds the
        WSL launcher on a Windows runner; these three assertions graded WSL's UTF-16 *"has no
        installed distributions"* text as the step's output during the 0.0.18 cut."""
        proc = subprocess.run(
            _support.bash_argv("-c", self.run_text), cwd=self.work, stdin=subprocess.DEVNULL,
            env=dict(os.environ, BUNDLE_PATH=_support.as_posix(bundle_path)),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        return proc.returncode, proc.stdout

    def test_a_real_bundle_is_copied_to_the_declared_asset_name(self):
        source = os.path.join(self.work, "attestation.json")
        with open(source, "w", encoding="utf-8") as handle:
            handle.write('{"mediaType":"application/vnd.dev.sigstore.bundle+json;version=0.3"}\n')
        code, out = self.execute(source)
        self.assertEqual(code, 0, out)
        landed = os.path.join(self.work, "dist", ra.PROVENANCE_ASSET)
        self.assertTrue(os.path.isfile(landed), "the bundle did not land at %s: %s" % (landed, out))
        self.assertIn("sigstore.bundle", _read(landed))

    def test_an_EMPTY_bundle_path_aborts_rather_than_publishing_without_provenance(self):
        """§7g. "the action produced no bundle" and "the Release has provenance" must not share a
        representation — and the silent version of this is precisely what shipped for months."""
        code, out = self.execute("")
        self.assertEqual(code, 1, "an empty bundle-path did not fail the step: %s" % out)
        self.assertFalse(os.path.exists(os.path.join(self.work, "dist", ra.PROVENANCE_ASSET)))
        self.assertIn("ABORT", out)

    def test_a_bundle_path_naming_a_MISSING_file_aborts(self):
        """A non-empty path is not a file. `cp` would fail anyway — but with a shell error nobody
        reads, and under a step whose message would say nothing about provenance."""
        code, out = self.execute(os.path.join(self.work, "not-there.json"))
        self.assertEqual(code, 1, out)
        self.assertIn("ABORT", out)


# =================================================================================================
# STAGE 6's PIN, GRADED ON THIS STAGE'S DEFECT (§7i — plant it, do not trust it)
# =================================================================================================

class TestStage6sPinCatchesThisStage(unittest.TestCase):
    """The brief's requirement, met by planting the offender rather than by reasoning about it:
    *"a fifth asset class added to `files:` and not to the declaration is stage 6's defect
    re-created, and stage 6's own pin should catch you. Prove it does."*

    The pin under test is `test_release_asset_set
    .TestTheRealTreeIsArmed.test_the_uploaded_patterns_are_DERIVED_from_the_script_that_produces
    _them`, whose rule is reproduced here on SYNTHETIC corpora so the real tree stays clean.
    """

    SCRIPT = ("SIGNED_PATTERNS='*.whl\n"
              "sbom.cdx.json\n"
              "provenance.intoto.jsonl'\n"
              "BUNDLE_SUFFIX='.sigstore.json'\n")

    def derived(self, script):
        patterns, suffix = ra.script_patterns(script)
        expected = set()
        for pattern in patterns:
            expected.add("dist/" + pattern)
            expected.add("dist/*" + suffix)
        return expected

    def declared(self, files):
        doc = {"jobs": {"github-release": {"steps": [{
            "uses": ra.RELEASE_ACTION + "@" + "0" * 40,
            "with": {"files": files, "fail_on_unmatched_files": True}}]}}}
        return set(ra.declared_upload_patterns(ra.upload_steps(doc)[0][1]))

    def test_the_matching_pair_agrees(self):
        """THE NEGATIVE — otherwise the two reds below prove only that the rule reds."""
        files = "dist/*.whl\ndist/sbom.cdx.json\ndist/provenance.intoto.jsonl\ndist/*.sigstore.json"
        self.assertEqual(self.declared(files), self.derived(self.SCRIPT))

    def test_a_files_entry_ABSENT_from_the_declaration_is_caught(self):
        """★ THE PLANTED DEFECT — stage 8's own hazard, made real. A fifth class published and not
        declared: the Release would carry a file nothing asserts the presence of."""
        files = ("dist/*.whl\ndist/sbom.cdx.json\ndist/provenance.intoto.jsonl\n"
                 "dist/*.sigstore.json\ndist/EXTRA.txt")
        self.assertNotEqual(self.declared(files), self.derived(self.SCRIPT))
        self.assertEqual(self.declared(files) - self.derived(self.SCRIPT), {"dist/EXTRA.txt"})

    def test_a_DECLARED_asset_missing_from_files_is_caught(self):
        """And the inverse, which is the one that ships a short Release: the script asserts the
        provenance asset exists and `files:` never uploads it."""
        files = "dist/*.whl\ndist/sbom.cdx.json\ndist/*.sigstore.json"
        self.assertEqual(
            self.derived(self.SCRIPT) - self.declared(files), {"dist/provenance.intoto.jsonl"})

    def test_dropping_the_class_from_the_SCRIPT_moves_the_derived_set(self):
        """The derivation is live in both directions: the script is the owner, so removing the
        pattern there must change what `files:` is graded against."""
        without = self.SCRIPT.replace("\nprovenance.intoto.jsonl", "")
        self.assertNotIn("dist/provenance.intoto.jsonl", self.derived(without))
        self.assertIn("dist/provenance.intoto.jsonl", self.derived(self.SCRIPT))


if __name__ == "__main__":
    unittest.main()
