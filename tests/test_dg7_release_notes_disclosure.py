"""DG-7 — the release-notes disclosure gate (0.0.16, from the DB.S10 audit's one NO-GO).

The audit did not fail this release for hiding anything. `CHANGELOG.md` carried the FTS
recall regression in plain, quantitative terms. It failed because **nothing would have
noticed if the disclosure vanished**: no test, script or workflow bound `RELEASE_NOTES.md`
— the file the GitHub Release and the mirror PR body are built from, and the only text most
readers ever see — to the CHANGELOG's `### Known limitations`. `release.sh` rewrites the
notes wholesale at every cut, so a measured regression was one careless rewrite from being
something an auditor discovers rather than something we published.

This battery proves the gate is real:

  * the LIVE tree discloses everything the CHANGELOG declares for the version it is writing
    notes for (this is the assertion that would have gone red on 2026-08-02);
  * a note that DROPS a limitation, WATERS IT DOWN (keeps the prose, loses the numbers), or
    carries the facts without ever FRAMING them as a limitation, all go RED and name what is
    missing — the three ways the disclosure dies quietly;
  * rewriting the prose entirely while keeping the facts PASSES — the notes are not the
    changelog, and a gate that demanded the wording would be a gate nobody could ship past;
  * a notes file announcing the wrong version FAILS (the literal 0.0.16 case: 0.0.14 notes
    at a 0.0.16 cut);
  * `scripts/release.sh` runs it fail-closed at the dev checkout BEFORE the first push and
    again at the merged mirror commit, so the cut cannot skip it.

The version half is deliberately NOT asserted against the live tree: writing the notes ahead
of the version bump is the normal pre-cut state, so that half belongs to `release.sh`'s
preflight (proven here from the script) and to the fixtures below, not to a unit test that
would be red for a whole development window.

Pure/offline; dependency-free; deterministic.
"""

import os
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path-fix side-effect)

from mokata.packaging import (
    changelog_section,
    check_release_notes,
    known_limitations,
    read_release_notes_version,
    signature_tokens,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELEASE_SH = os.path.join(ROOT, "scripts", "release.sh")

# A limitation shaped like the real one: a code identifier, a scheduled fix version, grouped
# integers, unit-bearing deltas and precise decimals — every fact class the gate extracts.
FIXTURE_CHANGELOG = """# Changelog

## [Unreleased] — 9.9.9

### Added

- Something unrelated that mentions 42 and 7 items.

### Known limitations

- **The lexical tier ranks worse than the floor it replaced.** `normalize_lexical_scores`
  scales against the best score in its own result set. At 100,000 items it measures
  **-5.6pp recall (0.5000 -> 0.4444)**. The fix is scheduled for 9.9.10.

### Fixed

- Unrelated fix.

## [9.9.8] — 2026-01-01

### Known limitations

- **An older limitation nobody must be forced to re-disclose.** Measured at 1.1pp.
"""

FULL_NOTES = """mokata **9.9.9 — "the release."**

### Known limitations

- `normalize_lexical_scores` normalizes against each engine's own best score, so at
  100,000 items the lexical tier loses 5.6pp recall (0.5000 -> 0.4444). Scheduled for 9.9.10.
"""


def _tree(tmp, *, changelog=FIXTURE_CHANGELOG, notes=FULL_NOTES, omit=()):
    """Lay down the two files the gate reads; either can be omitted to prove the missing-file
    path is a NAMED failure rather than a crash."""
    if "changelog" not in omit:
        with open(os.path.join(tmp, "CHANGELOG.md"), "w", encoding="utf-8") as fh:
            fh.write(changelog)
    if "notes" not in omit:
        with open(os.path.join(tmp, "RELEASE_NOTES.md"), "w", encoding="utf-8") as fh:
            fh.write(notes)
    return tmp


class TestSignatureExtraction(unittest.TestCase):
    """What the gate demands is FACTS, not phrasing — so what it extracts has to be exactly
    the actionable set. Too little and the gate is decorative; too much and every cut fights
    it over `MRR@10`'s `10`."""

    def setUp(self):
        section = changelog_section(FIXTURE_CHANGELOG, "9.9.9")
        self.entries = known_limitations(section)

    def test_only_the_known_limitations_of_the_target_section_are_read(self):
        # Not the "Added" bullet above it, and not 9.9.8's limitation below it.
        self.assertEqual(len(self.entries), 1)
        self.assertIn("normalize_lexical_scores", self.entries[0])
        self.assertNotIn("nobody must be forced", self.entries[0])

    def test_a_section_with_no_known_limitations_declares_nothing(self):
        self.assertEqual(known_limitations("### Added\n\n- a thing\n"), [])

    def test_the_signature_is_the_actionable_facts(self):
        self.assertEqual(
            signature_tokens(self.entries[0]),
            ["normalize_lexical_scores", "9.9.10", "100,000", "5.6pp", "0.5000", "0.4444"],
        )

    def test_bare_small_integers_are_not_treated_as_measurements(self):
        # "42" and "7" live in the Added bullet; even inside a limitation they would be
        # noise. A gate that demanded them would be turned off within a release.
        tokens = signature_tokens("- a limitation about 42 things and MRR@10 and 7 probes")
        self.assertEqual(tokens, [])


class TestTheLiveTree(unittest.TestCase):
    """The assertion that would have caught the DB.S10 blocker."""

    def setUp(self):
        with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as fh:
            self.changelog = fh.read()
        # the version this repo is currently writing notes for = the newest CHANGELOG section
        head = next(l for l in self.changelog.splitlines() if l.startswith("## "))
        self.version = read_release_notes_version(head)
        self.assertIsNotNone(self.version, f"cannot read a version out of {head!r}")

    def test_release_notes_announce_the_version_the_changelog_is_writing(self):
        result = check_release_notes(self.version, root=ROOT)
        self.assertEqual(
            result.notes_version, self.version,
            "RELEASE_NOTES.md is stale — it announces a different release than the CHANGELOG's "
            "newest section. This is the literal 0.0.16 finding: 0.0.14 notes at a 0.0.16 cut.",
        )

    def test_every_declared_limitation_is_disclosed_in_the_release_notes(self):
        result = check_release_notes(self.version, root=ROOT)
        self.assertTrue(result.section_found)
        self.assertFalse(
            result.framing_missing,
            "RELEASE_NOTES.md must FRAME the limitation as one, not scatter its numbers",
        )
        self.assertEqual(
            result.missing_tokens, [],
            "RELEASE_NOTES.md drops a fact the CHANGELOG declares as a known limitation:\n"
            + result.render(),
        )

    def test_the_gate_is_not_vacuous_on_this_tree(self):
        # If 0.0.16 ever stops declaring a limitation this test says so LOUDLY rather than
        # letting the two assertions above pass by having nothing to check.
        result = check_release_notes(self.version, root=ROOT)
        self.assertGreaterEqual(
            result.limitation_count, 1,
            "this release declares no known limitation — the two assertions above are now "
            "vacuous; delete this pin deliberately rather than letting it rot into a no-op",
        )


class TestItActuallyFails(unittest.TestCase):
    """Each of these is a way the disclosure dies. All of them must be RED."""

    def _check(self, **kw):
        with tempfile.TemporaryDirectory() as tmp:
            return check_release_notes("9.9.9", root=_tree(tmp, **kw))

    def test_the_matching_case_passes(self):
        res = self._check()
        self.assertTrue(res.ok, res.render())

    def test_prose_may_be_rewritten_entirely_as_long_as_the_facts_survive(self):
        rewritten = (
            "mokata **9.9.9**\n\n"
            "A known limitation you should know about before upgrading a big store: our\n"
            "in-database keyword ranking (`normalize_lexical_scores`) grades each engine\n"
            "against its own best hit. On a 100,000-item store that costs 5.6pp of recall —\n"
            "0.5000 falls to 0.4444. We are fixing it in 9.9.10.\n"
        )
        res = self._check(notes=rewritten)
        self.assertTrue(res.ok, res.render())

    def test_dropping_the_disclosure_entirely_is_red(self):
        res = self._check(notes="mokata **9.9.9 — everything is wonderful.**\n")
        self.assertFalse(res.ok)
        self.assertTrue(res.framing_missing)
        self.assertIn("normalize_lexical_scores", res.render())

    def test_watering_it_down_to_prose_without_numbers_is_red(self):
        watered = (
            "mokata **9.9.9**\n\n### Known limitations\n\n"
            "- `normalize_lexical_scores` may mis-order some lexical results on larger stores.\n"
        )
        res = self._check(notes=watered)
        self.assertFalse(res.ok, "a disclosure with every number removed must not pass")
        missing = [tok for _, tok in res.missing_tokens]
        self.assertEqual(missing, ["9.9.10", "100,000", "5.6pp", "0.5000", "0.4444"])

    def test_dropping_one_single_measurement_is_red_and_names_it(self):
        # The finest-grained mutation: the disclosure is otherwise complete and honest.
        one_short = FULL_NOTES.replace(" (0.5000 -> 0.4444)", "")
        res = self._check(notes=one_short)
        self.assertFalse(res.ok)
        self.assertEqual([tok for _, tok in res.missing_tokens], ["0.5000", "0.4444"])

    def test_facts_present_but_never_framed_as_a_limitation_is_red(self):
        unframed = (
            "mokata **9.9.9**\n\n### Performance\n\n"
            "- `normalize_lexical_scores` at 100,000 items: 5.6pp, 0.5000 -> 0.4444, 9.9.10.\n"
        )
        res = self._check(notes=unframed)
        self.assertFalse(res.ok)
        self.assertTrue(res.framing_missing)
        self.assertEqual(res.missing_tokens, [])

    def test_notes_announcing_the_wrong_version_is_red(self):
        stale = FULL_NOTES.replace("9.9.9 —", "9.9.7 —", 1)
        res = self._check(notes=stale)
        self.assertFalse(res.version_ok)
        self.assertFalse(res.ok)
        self.assertTrue(res.disclosure_ok, "only the VERSION half should be failing here")
        self.assertIn("9.9.7", res.render())

    def test_a_changelog_with_no_section_for_the_target_fails_closed(self):
        res = self._check(changelog="# Changelog\n\n## [9.9.8] — 2026-01-01\n\n- nothing\n")
        self.assertFalse(res.ok)
        self.assertFalse(res.section_found)
        self.assertIn("no `## …` section", res.render())

    def test_a_release_declaring_no_limitation_has_nothing_to_disclose(self):
        clean = "# Changelog\n\n## [Unreleased] — 9.9.9\n\n### Added\n\n- a thing.\n"
        res = self._check(changelog=clean, notes="mokata **9.9.9 — a clean release.**\n")
        self.assertTrue(res.ok, res.render())
        self.assertEqual(res.limitation_count, 0)

    def test_missing_files_are_named_failures_not_crashes(self):
        for omit in (("changelog",), ("notes",), ("changelog", "notes")):
            with self.subTest(omit=omit):
                res = self._check(omit=omit)      # must not raise
                self.assertFalse(res.ok)
                self.assertIn("FAIL", res.render())

    def test_an_empty_target_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(check_release_notes("", root=_tree(tmp)).ok)


@unittest.skipUnless(os.path.exists(RELEASE_SH),
                     "release.sh is dev-only, excluded from the public mirror")
class TestTheCutCannotSkipIt(unittest.TestCase):
    """A gate nothing invokes is the defect this whole battery exists for."""

    def setUp(self):
        with open(RELEASE_SH, encoding="utf-8") as fh:
            self.sh = fh.read()

    def test_release_sh_invokes_the_disclosure_check(self):
        self.assertIn("release-notes-check", self.sh)

    def test_it_runs_before_the_first_push_not_after_the_tag(self):
        preflight = self.sh.find('verify_release_notes "." ')
        first_push = self.sh.find("git push origin master")
        real_tag = self.sh.find('git tag -a "$TAG"')
        self.assertNotEqual(preflight, -1, "the dev-checkout disclosure preflight is gone")
        self.assertNotEqual(first_push, -1)
        self.assertLess(preflight, first_push,
                        "the disclosure must be checked while nothing has left this machine")
        self.assertLess(preflight, real_tag)

    def test_it_also_verifies_the_merged_mirror_commit(self):
        self.assertRegex(self.sh, r'verify_release_notes\s+"\$PUB_CHECKOUT"',
                         "the notes published from the mirror must be checked at the mirror")

    def test_it_is_fail_closed(self):
        body = self.sh[self.sh.find("verify_release_notes() {"):]
        body = body[:body.find("\n}\n")]
        self.assertIn("REFUSING TO TAG", body)
        self.assertIn("exit 1", body)


class TestTheTagTimeBackstop(unittest.TestCase):
    """`release.sh` is dev-only and a tag can be pushed without it, so the check also runs in
    the tag-triggered workflow — on the exact content being published. This one ships, so it
    is asserted on the mirror too."""

    def setUp(self):
        with open(os.path.join(ROOT, ".github", "workflows", "release.yml"), encoding="utf-8") as fh:
            self.yml = fh.read()

    def test_the_release_workflow_runs_the_disclosure_check(self):
        self.assertIn("release-notes-check", self.yml)

    def test_it_checks_against_the_tag_being_published(self):
        self.assertRegex(
            self.yml, r"release-notes-check\s+\"\$\{GITHUB_REF_NAME\}\"",
            "the workflow must check the notes against the TAG, not against whatever the "
            "package version happens to say",
        )


class TestParitySurface(unittest.TestCase):
    def test_the_new_command_is_declared_in_the_matrix(self):
        from mokata.parity import SURFACE_MATRIX, verify_parity
        self.assertIn("release-notes-check", SURFACE_MATRIX)
        self.assertTrue(SURFACE_MATRIX["release-notes-check"].exempt,
                        "release plumbing is CLI-only by design — say so explicitly")
        report = verify_parity()
        self.assertTrue(report.ok, report.render())


if __name__ == "__main__":
    unittest.main()
