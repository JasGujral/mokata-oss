"""Stage 6 — NULLGLOB-DISARMS-THE-EXISTENCE-CHECK: the Release's asset set is PINNED.

doc 102 exit criterion 6, second half: *"the Release's asset set is pinned so a missing signature
fails the cut instead of shipping."*

WHAT IS BEING GRADED, in one sentence: **a check whose glob matches nothing must be
distinguishable from a check that passed.** `v0.0.17` shipped a Release missing two whole asset
classes past TWO such checks, and the second, third and fourth diagnoses of that row all cited one
of them as evidence the files existed. The full account is in `tests/_release_assets.py`.

THE SHAPE OF THIS FILE, and every part of it is a doc 85 rule:

  * §7i — the pure functions are handed SYNTHETIC OFFENDERS, one per property. Once `release.yml`
    is fixed the tree contains nothing to catch, so a sweep that only reads the real tree would
    pass having graded nothing. `TestSyntheticOffenders` plants each defect on purpose.
  * §7i again, at the shell level — `TestTheScriptOnFixtures` RUNS `check-release-assets.sh`
    against directories where a file is GENUINELY ABSENT, rather than asserting its source reads
    correctly. A pin that has never seen the failure it exists for is untested.
  * §7g — "the pattern matched nothing" (exit 3) and "the artifact is unsigned" (exit 4) are
    required to be DIFFERENT exit codes, because they are different facts about different parts of
    the pipeline.
  * §7j — the workflow's `files:` block is graded against the pattern list in the SCRIPT, so the
    corpus axis is derived rather than typed. `TestTheRealTreeIsArmed` additionally ranges over
    EVERY run block and EVERY upload step in the document, not over the two we know about.
  * doc 00 step 6 — `TestTheV0017Record` grades the fixed configuration against WHAT WAS ACTUALLY
    PUBLISHED, transcribed from the Release API and the run log, not from the tracker.

⚠ WHAT IS ONLY VERIFIABLE AT THE NEXT CUT. Nothing here publishes a Release. What is proven
in-repo is that the derivation is correct, that both checks are armed, and that the armed checks
red on the historical failure. That the next cut's Release CARRIES the pinned set is a claim about
a run that has not happened — see the stage report's standing obligation.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import _support
from _support import sample_manifest_data  # noqa: F401  (path-fix side-effect)

import _release_assets as ra

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELEASE_YML = os.path.join(ROOT, ".github", "workflows", "release.yml")
CHECK_SCRIPT = os.path.join(ROOT, "scripts", "check-release-assets.sh")
# Internal: excluded from the public mirror, so every read of it is class-decorator guarded below.
SYNC_SH = os.path.join(ROOT, "scripts", "sync-public.sh")

# The script's own exit contract (§7g). Named here so a collapse of two states into one reds.
EXIT_OK = 0
EXIT_USAGE = 1
EXIT_PATTERN_MATCHED_NOTHING = 3
EXIT_ARTIFACT_UNSIGNED = 4


def _read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _release_doc():
    return ra.safe_load(_read(RELEASE_YML), "pin the release asset set")


def _run_check(directory, *args):
    """Run the real script. `(returncode, stdout, stderr)`.

    ⚠ `bash_argv`, NOT `["bash", ...]`, and the script + directory go through `as_posix`. A bare
    "bash" argv reaches CreateProcess, which searches System32 before PATH and finds the WSL
    launcher on a Windows runner — every assertion below then graded WSL's *"has no installed
    distributions"* message instead of this script. That is 11 of the 20 failures that halted the
    0.0.18 cut; `tests/test_windows_shell_and_paths.py` holds the account and the guard."""
    proc = subprocess.run(
        _support.bash_argv(_support.as_posix(CHECK_SCRIPT), _support.as_posix(directory), *args),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    return proc.returncode, proc.stdout, proc.stderr


class _FixtureDir(unittest.TestCase):
    """Builds throwaway `dist/` directories. Deliberately not checked-in fixtures: an incomplete
    dist/ sitting in the tree is something the next sweep would have to be told to ignore."""

    def setUp(self):
        self.dist = tempfile.mkdtemp(prefix="assetset-")
        self.addCleanup(shutil.rmtree, self.dist, ignore_errors=True)

    def make(self, *names):
        for name in names:
            with open(os.path.join(self.dist, name), "w", encoding="utf-8") as handle:
                handle.write("x")

    # The complete set, spelled as the artifacts of a real cut so the fixture cannot drift into
    # something the patterns match only by accident.
    #
    # ⚠ PROVENANCE IS THE FIFTH ASSET CLASS AND IT ARRIVED AT 0.0.18 STAGE 8. The set below is
    # held to the SCRIPT's own declaration by `test_the_fixture_covers_every_declared_pattern`, so
    # a sixth class cannot be added to `SIGNED_PATTERNS` while these fixtures quietly stay at four.
    WHEEL = "mokata-0.0.18-py3-none-any.whl"
    SDIST = "mokata-0.0.18.tar.gz"
    SBOM = "sbom.cdx.json"
    PROVENANCE = "provenance.intoto.jsonl"

    ARTIFACTS = (WHEEL, SDIST, SBOM, PROVENANCE)

    @property
    def complete_set(self):
        """Every file a complete `dist/` holds: each artifact and its one cosign bundle."""
        return tuple(name + suffix
                     for name in self.ARTIFACTS for suffix in ("", ".sigstore.json"))

    def make_complete(self):
        self.make(*self.complete_set)


# =================================================================================================
# THE SCRIPT, RUN — the runtime axis, graded against genuinely absent files (§7i)
# =================================================================================================

@unittest.skipUnless(_support.BASH, _support.NO_BASH)
class TestTheScriptOnFixtures(_FixtureDir):

    def test_the_fixture_covers_every_declared_pattern(self):
        """★ THE FIXTURE IS HELD TO THE DECLARATION, not to a number somebody typed. Stage 8 added
        a fifth asset class; a fixture that had stayed at four would have made every test below
        assert something narrower than the script now asks for, and each would still have passed
        for three of the four patterns."""
        patterns, _suffix = ra.script_patterns(_read(CHECK_SCRIPT))
        self.assertIsNotNone(patterns, "the pattern block could not be read")
        self.make_complete()
        code, _out, err = _run_check(self.dist)
        self.assertEqual(
            code, EXIT_OK,
            "the fixture does not satisfy the script's own %d patterns (%r): %s"
            % (len(patterns), patterns, err))
        self.assertEqual(
            len(self.ARTIFACTS), len(patterns),
            "the declaration has %d patterns and the fixture builds %d artifacts: %r vs %r"
            % (len(patterns), len(self.ARTIFACTS), patterns, self.ARTIFACTS))

    def test_a_complete_set_passes_and_prints_every_asset(self):
        """The NEGATIVE. The signing loop's real job must still work when every input is
        present — a pin that only ever reds is a pin that fails closed on everything."""
        self.make_complete()
        code, out, err = _run_check(self.dist)
        self.assertEqual(code, EXIT_OK, "complete set rejected: %s" % err)
        printed = [line for line in out.splitlines() if line.strip()]
        # ⚠ `as_posix` ON BOTH SIDES. `_run_check` hands the script `as_posix(directory)`, so the
        # script prints `/`-spelled paths; building the expectation with a bare `os.path.join`
        # spells it `\\` on Windows and the two sets never meet. The 0.0.18 repair converted the
        # INPUT to this comparison and left the EXPECTATION alone — a one-sided conversion, and
        # two of the ten Windows failures on run 32094654167. Swept as a class in
        # `tests/_windows_portability.py::one_sided_posix_sites`.
        self.assertEqual(
            sorted(printed),
            sorted(_support.as_posix(os.path.join(self.dist, name))
                   for name in self.complete_set),
            "the script printed a different set than a complete dist/ holds: %r" % (printed,))

    def test_a_missing_bundle_is_caught_and_NAMED(self):
        """THE v0.0.17 SHAPE: the artifact is there, its signature is not. The old `ls` exited 0
        on exactly this because its arguments were globs."""
        self.make_complete()
        os.remove(os.path.join(self.dist, self.SDIST + ".sigstore.json"))
        code, _out, err = _run_check(self.dist)
        self.assertEqual(
            code, EXIT_ARTIFACT_UNSIGNED,
            "an UNSIGNED artifact did not fail the check — this is the defect the stage exists "
            "for. exit=%s stderr=%r" % (code, err))
        self.assertIn(
            self.SDIST + ".sigstore.json", err,
            "the failure does not NAME the missing path, so a reader learns a count and not a "
            "remedy. stderr=%r" % err)

    def test_a_pattern_that_matches_nothing_is_a_DIFFERENT_failure(self):
        """§7g. 'nothing was built' and 'what was built is unsigned' must not share a
        representation — under nullglob the first one is otherwise completely silent."""
        self.make(self.SDIST, self.SBOM,
                  self.SDIST + ".sigstore.json", self.SBOM + ".sigstore.json")
        code, _out, err = _run_check(self.dist)
        self.assertEqual(
            code, EXIT_PATTERN_MATCHED_NOTHING,
            "a pattern matching NOTHING must not report as the unsigned-artifact failure; the "
            "two are different facts. exit=%s stderr=%r" % (code, err))
        self.assertNotEqual(
            EXIT_PATTERN_MATCHED_NOTHING, EXIT_ARTIFACT_UNSIGNED,
            "the two failure modes have collapsed into one exit code")
        self.assertIn("*.whl", err)

    def test_a_LITERAL_pattern_that_matches_nothing_is_also_exit_3(self):
        """★ FOUND AT STAGE 8, IN STAGE 6's OWN SCRIPT. `nullglob` removes an unmatched word only
        when it CONTAINS a metacharacter, so `sbom.cdx.json` survives expansion whether or not the
        file exists — and the loop then reported a build that produced no SBOM as exit 4, *"the
        artifact exists and is UNSIGNED"*. It does not exist. Two facts, one exit code, in the
        script written to keep them apart (§7g). It was one literal until stage 8 made it two."""
        self.make_complete()
        os.remove(os.path.join(self.dist, self.SBOM))
        os.remove(os.path.join(self.dist, self.SBOM + ".sigstore.json"))
        code, _out, err = _run_check(self.dist)
        self.assertEqual(
            code, EXIT_PATTERN_MATCHED_NOTHING,
            "a literal pattern matching nothing reported as the UNSIGNED-artifact failure. "
            "exit=%s stderr=%r" % (code, err))
        self.assertIn(self.SBOM, err)

    def test_a_literal_asset_present_but_UNSIGNED_is_still_exit_4(self):
        """The discrimination, from the other side: the repair must not turn every literal into a
        pattern-matched-nothing and lose the unsigned case entirely."""
        self.make_complete()
        os.remove(os.path.join(self.dist, self.SBOM + ".sigstore.json"))
        code, _out, err = _run_check(self.dist)
        self.assertEqual(code, EXIT_ARTIFACT_UNSIGNED,
                         "an existing but unsigned literal asset no longer reports as unsigned. "
                         "exit=%s stderr=%r" % (code, err))

    def test_nullglob_does_not_leak_the_literal_pattern(self):
        """`nullglob` is LOAD-BEARING and stays on: without it the expansion loop would iterate
        over the literal string `dist/*.whl` and report a file of that name as the input. The
        fix is the explicit assertion, never a shell-option flip — so prove the option still does
        its job."""
        self.make(self.SDIST, self.SBOM,
                  self.SDIST + ".sigstore.json", self.SBOM + ".sigstore.json")
        _code, out, err = _run_check(self.dist)
        self.assertNotIn(
            "*.whl" + ".sigstore.json", out + err,
            "the unmatched pattern was carried forward as a filename — nullglob is off and the "
            "loop is iterating over its own literal")

    def test_inputs_mode_yields_only_the_signing_inputs(self):
        self.make_complete()
        code, out, _err = _run_check(self.dist, "--inputs")
        self.assertEqual(code, EXIT_OK)
        printed = [line for line in out.splitlines() if line.strip()]
        self.assertEqual(
            sorted(printed),
            sorted(_support.as_posix(os.path.join(self.dist, n)) for n in self.ARTIFACTS),
            "the signing loop would sign the wrong set: %r" % printed)

    def test_inputs_mode_refuses_a_short_build_rather_than_signing_nothing(self):
        """The loop is fed by `--inputs`. If that mode returned an empty list quietly, the signing
        step would sign nothing, print nothing, and exit 0 — the same class one level up."""
        self.make(self.SDIST, self.SBOM)
        code, out, _err = _run_check(self.dist, "--inputs")
        self.assertEqual(code, EXIT_PATTERN_MATCHED_NOTHING)
        self.assertEqual(out.strip(), "", "a refused run still emitted inputs to sign")

    def test_a_missing_directory_is_usage_not_a_pass(self):
        code, _out, err = _run_check(os.path.join(self.dist, "nope"))
        self.assertEqual(code, EXIT_USAGE, "a non-existent dist/ must not read as a clean set")
        self.assertIn("no such directory", err)

    def test_the_v0017_asset_names_ALONE_are_now_SHORT_by_the_provenance_class(self):
        """★ THE DECLARATION GREW, AND THE PROOF THAT IT GREW IS THAT v0.0.17 WOULD NOW FAIL.

        Stage 6 could say the six published files were a complete set — the artifacts were never
        the problem, the declaration was. Stage 8 adds provenance, so that is no longer true and
        saying it would be the stale half of a true sentence. A v0.0.17-shaped build now stops at
        exit 3, NAMING the missing class: nothing was built for a pattern, which is a different
        fact from an unsigned artifact (§7g) and must stay one."""
        for asset in ra.V0017_RELEASE_ASSETS:
            self.make(asset.split("/", 1)[1])
        code, _out, err = _run_check(self.dist)
        self.assertEqual(
            code, EXIT_PATTERN_MATCHED_NOTHING,
            "a v0.0.17-shaped dist/ passes the 0.0.18 declaration, so the provenance class is not "
            "actually required by it. stderr=%r" % err)
        self.assertIn(self.PROVENANCE, err)

    def test_the_v0017_assets_PLUS_the_provenance_pair_are_a_complete_set(self):
        """And the complement: nothing else about the corrected declaration changed. The six that
        shipped are still exactly right for their four patterns."""
        for asset in ra.V0017_RELEASE_ASSETS:
            self.make(asset.split("/", 1)[1])
        self.make(self.PROVENANCE, self.PROVENANCE + ".sigstore.json")
        code, out, err = _run_check(self.dist)
        self.assertEqual(
            code, EXIT_OK,
            "the v0.0.17 assets plus provenance fail the check, so something OTHER than the new "
            "class changed in the declaration. stderr=%r" % err)
        self.assertEqual(len([ln for ln in out.splitlines() if ln.strip()]),
                         len(ra.V0017_RELEASE_ASSETS) + 2)


# =================================================================================================
# THE v0.0.17 RECORD — the pin goes RED on what actually shipped (doc 00 step 6)
# =================================================================================================

class TestTheV0017Record(unittest.TestCase):
    """`release.yml` at HEAD was byte-identical to the v0.0.17 cut before this stage
    (`git diff v0.0.17 -- .github/workflows/release.yml` printed nothing), so these fixtures are
    the configuration that shipped rather than a reconstruction of it."""

    def test_the_v0017_declaration_is_RED_against_the_v0017_assets(self):
        """★ THE KNOWN-BAD CASE, and it is free. The Release satisfied FOUR of the six globs its
        own workflow declared. `fail_on_unmatched_files` was off, so the action warned twice and
        published anyway; with it on, this is the cut failing."""
        short = ra.unmatched_patterns(ra.V0017_DECLARED_FILES, ra.V0017_RELEASE_ASSETS)
        self.assertEqual(
            sorted(short), ["dist/*.pem", "dist/*.sig"],
            "the v0.0.17 Release did not satisfy its own files: block on exactly .sig and .pem — "
            "if this no longer reds, the pin cannot see the failure it exists for. got %r" % (short,))

    def test_the_CORRECTED_v0017_DECLARATION_is_GREEN_against_the_same_assets(self):
        """Same six published files, v0.0.17's declaration with the two unsatisfiable globs
        removed, no unmatched pattern. Stage 6's fix was not 'ship more things', it was 'stop
        declaring what cosign cannot write' — and that half is DERIVED here rather than retyped:
        the corrected set is the declared set minus whatever the published assets did not match."""
        short = set(ra.unmatched_patterns(ra.V0017_DECLARED_FILES, ra.V0017_RELEASE_ASSETS))
        corrected = tuple(p for p in ra.V0017_DECLARED_FILES if p not in short)
        self.assertEqual(len(corrected), 4, "the corrected v0.0.17 declaration is %r" % (corrected,))
        self.assertEqual(ra.unmatched_patterns(corrected, ra.V0017_RELEASE_ASSETS), ())

    def test_the_declaration_grew_by_EXACTLY_the_provenance_class(self):
        """★ STAGE 8's OWN DELTA, PINNED. The `files:` block is derived from the script, so this
        is not a second copy — it is the statement that the ONLY thing 0.0.18 adds to what v0.0.17
        should have declared is the provenance asset. A sixth class arriving silently reds here,
        and so does losing one of the four that were already right."""
        short = set(ra.unmatched_patterns(ra.V0017_DECLARED_FILES, ra.V0017_RELEASE_ASSETS))
        corrected = {p for p in ra.V0017_DECLARED_FILES if p not in short}
        steps = ra.upload_steps(_release_doc())
        self.assertTrue(steps, "no upload step found in release.yml at all")
        for where, inputs in steps:
            declared = set(ra.declared_upload_patterns(inputs))
            self.assertEqual(
                declared - corrected, {"dist/" + ra.PROVENANCE_ASSET},
                "%s adds something other than the provenance class to the corrected v0.0.17 "
                "declaration: %r" % (where, sorted(declared - corrected)))
            self.assertEqual(
                corrected - declared, set(),
                "%s DROPPED an asset class v0.0.17 correctly declared: %r"
                % (where, sorted(corrected - declared)))

    def test_the_v0017_signing_block_is_RED_on_both_disarmed_surfaces(self):
        """Both instances of the class, in the text that shipped them."""
        disarmed = ra.nullglob_disarmed_checks(ra.V0017_SIGNING_RUN)
        self.assertEqual(
            len(disarmed), 1,
            "the v0.0.17 signing block's `ls` under nullglob was not caught: %r" % (disarmed,))
        self.assertIn("ls -l", disarmed[0][1])

        doc = {"jobs": {"github-release": {"steps": [{
            "uses": ra.RELEASE_ACTION + "@" + "0" * 40,
            "with": {"files": "\n".join(ra.V0017_DECLARED_FILES)}}]}}}
        self.assertEqual(
            len(ra.unarmed_uploads(doc)), 1,
            "the v0.0.17 upload step's unarmed fail_on_unmatched_files was not caught")

    def test_the_v0017_signing_block_is_RED_on_the_ignored_cosign_flags(self):
        """★ WHY THE PAIR WAS NEVER WRITTEN. Both flags, on a `sign-blob` that uses the new bundle
        format — accepted, warned about, ignored by cosign v3.0.6. Measured, not read."""
        ignored = ra.ignored_cosign_flags(ra.V0017_SIGNING_RUN)
        self.assertEqual(
            sorted({flag for flag, _ in ignored}),
            ["--output-certificate", "--output-signature"],
            "the two flags cosign v3 ignores were not flagged in the block that shipped them: %r"
            % (ignored,))


# =================================================================================================
# SYNTHETIC OFFENDERS — one defect per corpus, so each element is isolated (§7i)
# =================================================================================================

class TestSyntheticOffenders(unittest.TestCase):

    def test_a_glob_check_under_nullglob_is_an_offender(self):
        self.assertEqual(
            len(ra.nullglob_disarmed_checks("shopt -s nullglob\nls -l dist/*.sig\n")), 1)

    def test_the_SAME_check_without_nullglob_is_NOT_an_offender(self):
        """§7g, three states. Without nullglob, `ls dist/*.sig` fails loudly on the literal — a
        check that CAN fail. Charging it here would make the fix unprovable and would convict
        every honest `ls` in the tree."""
        self.assertEqual(ra.nullglob_disarmed_checks("ls -l dist/*.sig\n"), ())

    def test_an_exact_path_check_under_nullglob_is_NOT_an_offender(self):
        """What the fix uses. A concrete filename cannot expand to nothing."""
        self.assertEqual(
            ra.nullglob_disarmed_checks('shopt -s nullglob\n[ -f "$want" ] || missing=1\n'), ())

    def test_a_QUOTED_glob_is_NOT_an_offender(self):
        """`ls "dist/*.sig"` is a literal filename; the shell never expands it, so `ls` fails."""
        self.assertEqual(ra.nullglob_disarmed_checks('shopt -s nullglob\nls "dist/*.sig"\n'), ())

    def test_nullglob_turned_back_OFF_disarms_the_finding_not_the_check(self):
        self.assertEqual(
            ra.nullglob_disarmed_checks(
                "shopt -s nullglob\nshopt -u nullglob\nls -l dist/*.sig\n"), ())

    def test_the_check_is_found_when_it_hides_after_a_semicolon(self):
        """The real defect was written as `echo "Signature assets:"; ls -l dist/*.sig …` — a
        line-anchored scan misses it, and that is the exact line this stage is named for."""
        found = ra.nullglob_disarmed_checks(
            'shopt -s nullglob\necho "Signature assets:"; ls -l dist/*.sig dist/*.pem\n')
        self.assertEqual(len(found), 1, "the post-semicolon check was missed: %r" % (found,))

    def test_a_commented_out_check_is_NOT_an_offender(self):
        """PIN-SUBSTRING-COMMENT-HOLE: this file's own prose describes the defect it forbids, and
        `release.yml` now carries a comment quoting the old `ls` line verbatim. A scan that reads
        comments convicts the documentation."""
        self.assertEqual(
            ra.nullglob_disarmed_checks(
                "shopt -s nullglob\n# ⚠ NOT ls -l dist/*.sig — it cannot fail here\n"), ())
        self.assertEqual(
            ra.nullglob_disarmed_checks(
                'shopt -s nullglob\necho hi   # ls -l dist/*.sig would be wrong\n'), ())

    def test_a_CORRECT_check_annotated_with_the_wrong_one_is_not_convicted(self):
        """★ The offender only comment-stripping can catch, and it is not hypothetical: the fix in
        `release.yml` documents itself by quoting the disarmed line it replaced.

        Both preceding cases are caught by the first-word test instead (a `#` line's first word is
        `#`; a trailing comment on an `echo` never reaches the glob test), so comment-stripping and
        the first-word test were two defences covering for each other and NEITHER was gradable —
        doc 85 §7f, 2026-08-04 amendment. Here the command IS an existence check, its own arguments
        are exact, and only the comment carries a glob."""
        self.assertEqual(
            ra.nullglob_disarmed_checks(
                'shopt -s nullglob\n'
                'ls -l "$want"   # NOT `ls -l dist/*.sig` — that one cannot fail here\n'),
            (), "a correct check was convicted by the comment explaining why it is correct")

    def test_other_existence_commands_are_covered_not_just_ls(self):
        """THE CLASS. `ls` is the instance that shipped; the property is 'a command that answers
        does-this-exist, handed a glob, under nullglob'."""
        for command in ("test -f dist/*.sig", "stat dist/*.pem", "[ -e dist/*.sig ]"):
            self.assertEqual(
                len(ra.nullglob_disarmed_checks("shopt -s nullglob\n%s\n" % command)), 1,
                "%r went uncaught — the sweep is pinned to `ls` rather than to the class"
                % command)

    def test_an_upload_with_files_and_no_arming_is_an_offender(self):
        doc = {"jobs": {"r": {"steps": [
            {"uses": ra.RELEASE_ACTION + "@x", "with": {"files": "dist/*.whl\n"}}]}}}
        self.assertEqual(len(ra.unarmed_uploads(doc)), 1)

    def test_an_upload_armed_with_the_STRING_true_is_not_an_offender(self):
        """GitHub Actions inputs are strings; YAML may hand us either a bool or `'true'`. Reading
        only one of the two is how an armed check would read as unarmed and vice versa."""
        for value in (True, "true", "True", " true "):
            doc = {"jobs": {"r": {"steps": [{
                "uses": ra.RELEASE_ACTION + "@x",
                "with": {"files": "dist/*.whl\n", ra.FAIL_ON_UNMATCHED: value}}]}}}
            self.assertEqual(ra.unarmed_uploads(doc), (), "value %r read as unarmed" % (value,))
        for value in (False, "false", "yes", ""):
            doc = {"jobs": {"r": {"steps": [{
                "uses": ra.RELEASE_ACTION + "@x",
                "with": {"files": "dist/*.whl\n", ra.FAIL_ON_UNMATCHED: value}}]}}}
            self.assertEqual(
                len(ra.unarmed_uploads(doc)), 1, "value %r read as armed" % (value,))

    def test_an_upload_with_no_files_is_not_an_offender(self):
        """A release step that attaches nothing has no glob to be short. Charging it would be a
        false red, and a sweep with false reds gets switched off."""
        doc = {"jobs": {"r": {"steps": [{"uses": ra.RELEASE_ACTION + "@x", "with": {}}]}}}
        self.assertEqual(ra.unarmed_uploads(doc), ())

    def test_unmatched_patterns_reports_only_what_matches_nothing(self):
        listing = ("dist/a.whl", "dist/sbom.cdx.json")
        self.assertEqual(
            ra.unmatched_patterns(("dist/*.whl", "dist/*.sig", "dist/sbom.cdx.json"), listing),
            ("dist/*.sig",))

    def test_unmatched_patterns_does_not_let_a_glob_cross_a_directory(self):
        """`fnmatch` alone would match `dist/nested/a.whl` against `dist/*.whl` and report the
        pattern satisfied by a file the upload would never take from that path."""
        self.assertEqual(
            ra.unmatched_patterns(("dist/*.whl",), ("dist/nested/a.whl",)), ("dist/*.whl",))

    def test_a_path_SHORTER_than_the_pattern_does_not_satisfy_it(self):
        """The offender the depth EQUALITY catches and the segment loop alone does not: `zip`
        truncates to the shorter sequence, so without the length test `dist` — the directory
        itself — matches `dist/*.whl` and the pattern reads as satisfied by nothing at all.

        Kept as a separate case because the deeper-path one above survives a mutation of the
        length test (zip still pairs `nested` against `*.whl` and fails honestly). Two checks, one
        offender each (§7f)."""
        self.assertEqual(
            ra.unmatched_patterns(("dist/*.whl",), ("dist",)), ("dist/*.whl",),
            "a bare `dist` entry satisfied `dist/*.whl` — the pattern would read as matched with "
            "no artifact present anywhere")

    def test_the_ignored_flags_are_HONOURED_under_the_legacy_bundle_format(self):
        """The rule is about the NEW bundle format, which is v3's default. Asking for the old one
        makes the flags work again — so the sweep must not convict that spelling, or the remedy it
        implies would be wrong."""
        self.assertEqual(
            ra.ignored_cosign_flags(
                'cosign sign-blob --new-bundle-format=false --output-signature "$f.sig" "$f"'),
            ())

    def test_a_bundle_only_invocation_is_clean(self):
        self.assertEqual(
            ra.ignored_cosign_flags('cosign sign-blob --bundle "$f.sigstore.json" "$f"'), ())

    def test_a_continued_invocation_is_read_as_ONE_command(self):
        """The block that shipped spread one command over five lines with backslashes. A
        line-at-a-time reader sees `cosign sign-blob \\` and no flags at all."""
        found = ra.ignored_cosign_flags(
            'cosign sign-blob \\\n  --output-signature "$f.sig" \\\n'
            '  --bundle "$f.sigstore.json" \\\n  "$f"\n')
        self.assertEqual(len(found), 1, "the folded invocation was misread: %r" % (found,))


# =================================================================================================
# THE REAL TREE IS ARMED — the derived axis (§7j)
# =================================================================================================

class TestTheRealTreeIsArmed(unittest.TestCase):

    def test_no_run_block_in_release_yml_has_a_disarmed_existence_check(self):
        """Ranges over EVERY step's `run:`, not over the signing step. A guard established for one
        call site protects one call site."""
        doc = _release_doc()
        blocks = ra.run_blocks(doc)
        self.assertTrue(blocks, "no run blocks parsed out of release.yml — the sweep is vacuous")
        offenders = [(where, ra.nullglob_disarmed_checks(text))
                     for where, text in blocks if ra.nullglob_disarmed_checks(text)]
        self.assertEqual(
            offenders, [],
            "a glob-argument existence check survives under nullglob in the release path:\n%s"
            % "\n".join("  %s: %r" % pair for pair in offenders))

    def test_every_upload_step_arms_its_unmatched_glob_check(self):
        doc = _release_doc()
        self.assertTrue(ra.upload_steps(doc), "release.yml no longer uploads anything")
        self.assertEqual(
            ra.unarmed_uploads(doc), (),
            "an upload step declares globs with %s left at its FALSE default; a short list would "
            "warn and publish, which is what v0.0.17 did" % ra.FAIL_ON_UNMATCHED)

    def test_no_cosign_invocation_passes_a_flag_this_version_ignores(self):
        doc = _release_doc()
        offenders = [(where, ra.ignored_cosign_flags(text)) for where, text in ra.run_blocks(doc)
                     if ra.ignored_cosign_flags(text)]
        self.assertEqual(
            offenders, [],
            "release.yml asks cosign for output files it will not write; the step exits 0 and the "
            "asset never exists:\n%s" % "\n".join("  %s: %r" % pair for pair in offenders))

    def test_the_uploaded_patterns_are_DERIVED_from_the_script_that_produces_them(self):
        """§7j, the corpus axis. The `files:` block is a second declaration of the asset set, and
        two of its six entries named things nothing could ever produce. It is now graded against
        `check-release-assets.sh`, which owns the list."""
        patterns, suffix = ra.script_patterns(_read(CHECK_SCRIPT))
        self.assertIsNotNone(
            patterns, "the pattern block could not be read out of check-release-assets.sh — the "
                      "derivation is broken, so the check below would be vacuous")
        expected = set()
        for pattern in patterns:
            expected.add("dist/" + pattern)
            expected.add("dist/*" + suffix)
        for where, inputs in ra.upload_steps(_release_doc()):
            self.assertEqual(
                set(ra.declared_upload_patterns(inputs)), expected,
                "%s publishes a different set than the build job produces.\n  declared: %r\n"
                "  derived : %r" % (where, ra.declared_upload_patterns(inputs), sorted(expected)))

    def test_the_signing_step_asserts_the_set_through_the_shared_script(self):
        """The two surfaces must run the SAME derivation. Two copies of a rule is how the six-glob
        block came to disagree with the signing loop in the first place."""
        callers = [where for where, text in ra.run_blocks(_release_doc())
                   if "check-release-assets.sh" in text]
        self.assertGreaterEqual(
            len(callers), 2,
            "the asset-set assertion runs in fewer than two places (%r). The build job asserts "
            "what it PRODUCED and the release job asserts what ARRIVED — they are different "
            "claims and only the second decides what users get." % (callers,))


@unittest.skipUnless(os.path.exists(SYNC_SH),
                     "sync-public.sh is dev-only, excluded from the public mirror")
class TestTheCheckScriptIsNotExcludedFromTheMirror(unittest.TestCase):
    """`release.yml` runs `check-release-assets.sh` in the PUBLIC repo, so excluding it from the
    mirror would break every cut. The pin has to read `sync-public.sh`, which is itself internal —
    hence the class DECORATOR (the shape `test_s28_shipped_reads_guarded` requires; a setUpClass
    skip does not count) and the companion below, so an absent file is never read as a pass."""

    def test_the_helper_is_not_in_either_mirror_control(self):
        sync = _read(SYNC_SH)
        self.assertNotIn(
            "check-release-assets.sh", sync,
            "check-release-assets.sh appears in sync-public.sh; release.yml runs it in the PUBLIC "
            "repo, so excluding it breaks every cut")

    def test_the_file_this_class_needs_is_absent_ONLY_because_of_the_mirror(self):
        """§7g's companion: without it, "the guard is skipped" and "the guard passed" would look
        the same to a reader of the run. On the mirror the skip is CORRECT and this says why; in
        the dev repo the file is present and this asserts it."""
        if os.path.exists(SYNC_SH):
            return
        self.assertFalse(                                          # pragma: no cover - mirror only
            os.path.exists(os.path.join(ROOT, "CLAUDE.md")),
            "sync-public.sh is missing but CLAUDE.md is present, so this is NOT the public "
            "mirror — the dev checkout has lost a file rather than crossing a boundary")


class TestFailsLoudWithoutPyYAML(unittest.TestCase):

    def test_the_missing_parser_path_raises_rather_than_skipping(self):
        """§7g via `_workflow_pins.safe_load` — re-exported, not re-implemented, because
        `tests/_pyyaml_sweep.py` fails the build on a second spelling."""
        import _workflow_pins
        self.assertIs(ra.safe_load, _workflow_pins.safe_load)
        real = sys.modules.pop("yaml", None)

        class _Blocker:
            def find_module(self, name, path=None):
                if name == "yaml":
                    raise ImportError("blocked for the test")
                return None

            def find_spec(self, name, path=None, target=None):
                if name == "yaml":
                    raise ImportError("blocked for the test")
                return None

        blocker = _Blocker()
        sys.meta_path.insert(0, blocker)
        try:
            with self.assertRaises(_workflow_pins.MissingParser):
                ra.safe_load("jobs: {}", "pin the release asset set")
        finally:
            sys.meta_path.remove(blocker)
            if real is not None:
                sys.modules["yaml"] = real


if __name__ == "__main__":
    unittest.main()
