"""Stage 8 — CLAUDE-MD-SHIPS-LIST-INCOMPLETE: the mirror-boundary lists are DERIVED, both ways.

doc 84 §9. The row's sentence, and everything here follows from it: **the list is not wrong about
anything it says; it is wrong about being complete** — and *"the reverse mapping is the direction
this class leaks"*. A reader who trusts an enumeration to be current concludes that anything
UNLISTED is public, and the same `CLAUDE.md` section instructs them to register a new internal file
in two places. So an incomplete list does not merely fail to help; it actively misdirects.

WHAT WAS MEASURED, 2026-08-13, BEFORE THE FIX
----------------------------------------------
    OMITTED (the leak direction)              2   `docs/talks/`, `scripts/check-tracker-tables.py`
    OVERCLAIMED (the false-reassurance one)   0
    stays-public helpers CI runs, unlisted    1   `scripts/check-release-assets.sh`

⚠ **THE ROW NAMED ONE OF THE TWO.** It cites `scripts/check-tracker-tables.py` and stops there.
`docs/talks/` — twelve tracked files, excluded by both controls since it was added — is in neither
the row nor the brief. The row that exists because a list was incomplete was itself incomplete,
which is why nothing here reads a path out of the row.

⚠ **AND THE THIRD ONE IS STAGE 6's, TEN DAYS OLD.** `scripts/check-release-assets.sh` is executed
by `release.yml` in the PUBLIC repo, so it must be excluded by NEITHER control — and the
stays-public bullet, which is where that reason lives, was never updated. The list drifted further
while its own row sat open, which is `BOOKKEEPING-SWEEP-MANUAL`'s argument arriving on schedule.

THE TWO DIRECTIONS ARE GRADED SEPARATELY BECAUSE THEY ARE DIFFERENT FAILURES (§7g)
-----------------------------------------------------------------------------------
  * OMITTED is a LEAK waiting to happen and it is judged against the TRACKED excludes.
  * OVERCLAIMED is a FALSE REASSURANCE — nothing ships today, the document asserts a control that
    is not there — and it is judged against `_mirror_bookkeeping.resolve()`'s both-controls verdict.

Collapsing them into one predicate is not a simplification, it is a bug: `docs/marketing/` is
gitignored (in no clone, so not in the tracked set) yet named by both controls, so a single
predicate convicts a prose entry that is entirely correct. The one direction the tree is currently
clean in gets a SYNTHETIC OFFENDER (§7i) — a pin that has only ever seen a healthy corpus grades
nothing.

WHY THIS FILE IS CLASS-DECORATOR GUARDED
-----------------------------------------
It reads `CLAUDE.md` and `scripts/sync-public.sh`, and BOTH are excluded from the public mirror. A
shipped test that reads a file which does not ship ERRORS on the mirror
(`SHIPPED-TEST-READS-INTERNAL-FILE`, stage 28), and `tests/_shipped_reads.py` fails the build on
one. The guard is the CLASS DECORATOR shape and only that shape: a `setUpClass` skip collapses the
class into one skip and diverges the mirror's test count from this repo's. Each guarded class
carries the §7g companion, so "the guard was skipped" and "the guard passed" cannot look alike.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import unittest

from _support import sample_manifest_data  # noqa: F401  (path-fix side-effect)

import _mirror_bookkeeping as mb
import _release_assets as ra
import _shipped_reads as sr
import _supply_chain_sweep as sc
import _workflow_pins as wp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOWS = os.path.join(ROOT, ".github", "workflows")
# Internal: excluded from the public mirror. Every read below is class-decorator guarded.
CLAUDE_MD = os.path.join(ROOT, "CLAUDE.md")
SYNC_SH = os.path.join(ROOT, "scripts", "sync-public.sh")

INTERNAL_PRESENT = os.path.exists(CLAUDE_MD) and os.path.exists(SYNC_SH)
INTERNAL_REASON = ("CLAUDE.md and sync-public.sh are dev-only, excluded from the public mirror in "
                   "both controls")


def _read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _executed_scripts():
    """Every `scripts/…` file the nine workflows RUN. Derived, and it is not a grep — see
    `_supply_chain_sweep`, where `echo "… scripts/release.sh …"` is the counter-example."""
    found = set()
    for name in wp.workflow_files(WORKFLOWS):
        doc = ra.safe_load(_read(os.path.join(WORKFLOWS, name)), "derive executed helper scripts")
        for _where, text in ra.run_blocks(doc):
            found |= sc.executed_scripts(text)
    return frozenset(found)


# =================================================================================================
# THE PARSER — a claim's own text, read into an entry set
# =================================================================================================

class TestTheProseIsReadAsAList(unittest.TestCase):
    """Pure functions over SUPPLIED prose (§7i). Nothing here touches the repo."""

    BULLET = ("- **NEVER ships to public:** `docs/build/`, `CLAUDE.md`,\n"
              "  `scripts/release.sh`.\n")

    def test_a_bullets_backticked_paths_are_the_list(self):
        self.assertEqual(
            mb.listed_paths(self.BULLET, mb.NEVER_SHIPS_HEADING),
            frozenset({"docs/build", "CLAUDE.md", "scripts/release.sh"}))

    def test_a_trailing_slash_is_not_a_different_path(self):
        """`docs/build/` in the prose and `docs/build` in the guard array are the same path, and a
        derivation that thought otherwise would report every directory as both omitted and
        overclaimed at once."""
        self.assertIn("docs/build", mb.listed_paths(self.BULLET, mb.NEVER_SHIPS_HEADING))

    def test_an_absent_heading_is_None_not_an_empty_list(self):
        """§7g. An empty set would make every excluded path an offender AND make a stays-public
        check vacuously clean; None says the derivation itself has broken."""
        self.assertIsNone(mb.listed_paths("- something else entirely\n",
                                          mb.NEVER_SHIPS_HEADING))

    def test_the_enumeration_stops_at_the_caveat_marker(self):
        """★ THE PARSER'S OWN FOOTNOTE PROBLEM. The real bullet explains itself after the list, and
        the explanation names paths — this test file, `sync-public.sh`. Reading those as members
        would make the guard grade its own commentary."""
        bullet = ("- **NEVER ships to public:** `docs/build/`.\n"
                  "  ⚠ graded by `tests/test_claude_md_ships_list.py` against "
                  "`scripts/sync-public.sh`.\n")
        self.assertEqual(mb.listed_paths(bullet, mb.NEVER_SHIPS_HEADING),
                         frozenset({"docs/build"}))

    def test_prose_words_in_backticks_are_not_paths(self):
        bullet = "- **NEVER ships to public:** `docs/build/` and the `rsync` mirror.\n"
        self.assertEqual(mb.listed_paths(bullet, mb.NEVER_SHIPS_HEADING),
                         frozenset({"docs/build"}))

    def test_the_next_bullet_is_a_different_list(self):
        """Two bullets, two claims. A parser that ran to the end of the section would fold the
        stays-public list into the never-ships one and each would vouch for the other."""
        prose = ("- **NEVER ships to public:** `docs/build/`.\n"
                 "- **Stays public (do not exclude):** `src/`, `scripts/normalize_sdist.py`.\n")
        self.assertEqual(mb.listed_paths(prose, mb.NEVER_SHIPS_HEADING),
                         frozenset({"docs/build"}))
        self.assertEqual(mb.listed_paths(prose, mb.STAYS_PUBLIC_HEADING),
                         frozenset({"src", "scripts/normalize_sdist.py"}))


# =================================================================================================
# SYNTHETIC OFFENDERS — one defect per corpus, BOTH directions (§7i)
# =================================================================================================

class TestSyntheticOffenders(unittest.TestCase):
    """The tree is clean in one direction and near-clean in the other, so the offenders are planted.

    `SCRIPT` is a minimal `sync-public.sh` carrying both controls. It is a fixture, not a copy of
    the real script: the real one is read by the guarded classes below, and grading a fixture
    against itself proves nothing about the repo.
    """

    SCRIPT = (
        "rsync -a --delete \\\n"
        "  --exclude='docs/build/' \\\n"
        "  --exclude='CLAUDE.md' \\\n"
        "  --exclude='scripts/release.sh' \\\n"
        "  \"$SRC\"/ \"$DEST\"/\n"
        "INTERNAL_PATHS=(\n"
        "  docs/build CLAUDE.md scripts/release.sh\n"
        ")\n")
    TRACKED = frozenset({"docs/build", "CLAUDE.md", "scripts/release.sh"})
    COMPLETE = ("- **NEVER ships to public:** `docs/build/`, `CLAUDE.md`, "
                "`scripts/release.sh`.\n")

    def resolve(self, prose, tracked=None, script=None):
        return mb.resolve_ships_list(
            prose, self.SCRIPT if script is None else script,
            self.TRACKED if tracked is None else tracked)

    def test_the_matching_list_is_GREEN(self):
        """THE NEGATIVE. A guard that reds on everything is a guard nobody keeps."""
        result = self.resolve(self.COMPLETE)
        self.assertEqual(result.verdict, mb.GREEN, result.render())
        self.assertEqual(result.basis, mb.BASIS_LIST_MATCHES_CONTROLS)

    def test_an_excluded_tracked_path_missing_from_the_prose_is_the_LEAK_direction(self):
        """★ OFFENDER A — today's real defect, planted. `scripts/release.sh` is excluded by both
        controls and absent from the list, so a reader concludes `scripts/` is public."""
        result = self.resolve("- **NEVER ships to public:** `docs/build/`, `CLAUDE.md`.\n")
        self.assertEqual(result.verdict, mb.RED, result.render())
        self.assertEqual(result.basis, mb.BASIS_LIST_OMITS_AN_EXCLUDED)
        self.assertEqual(result.omitted, ("scripts/release.sh",))
        self.assertIn("unlisted to mean public", result.render())

    def test_a_prose_entry_no_control_excludes_is_the_FALSE_REASSURANCE_direction(self):
        """★ OFFENDER B — the direction the tree is clean in, so it exists only here. Nothing
        leaks; the document asserts a control that is not there, and the next reader relies on it."""
        result = self.resolve(self.COMPLETE.replace(
            "`scripts/release.sh`", "`scripts/release.sh`, `docs/secret-plans/`"))
        self.assertEqual(result.verdict, mb.RED, result.render())
        self.assertEqual(result.basis, mb.BASIS_LIST_CLAIMS_AN_UNEXCLUDED)
        self.assertEqual(result.overclaimed, ("docs/secret-plans",))
        self.assertIn("FALSE-REASSURANCE", result.render())

    def test_a_path_in_ONE_control_only_is_still_overclaimed(self):
        """The prose promises the pair. An entry rsync drops but the hard guard never checks is
        `BASIS_EXCLUDE_ONLY` — RED at `resolve()` — and the prose must not launder it to green."""
        script = self.SCRIPT.replace("  docs/build CLAUDE.md scripts/release.sh\n",
                                     "  docs/build CLAUDE.md\n")
        result = self.resolve(self.COMPLETE, script=script)
        self.assertEqual(result.basis, mb.BASIS_LIST_CLAIMS_AN_UNEXCLUDED)
        self.assertEqual(result.overclaimed, ("scripts/release.sh",))

    def test_drifting_BOTH_ways_is_its_own_basis_not_either_one(self):
        """Two failures at once is a third fact. Reporting only the first would hide the leak
        behind the reassurance or the reassurance behind the leak, depending on the order."""
        result = self.resolve("- **NEVER ships to public:** `docs/build/`, `docs/secret-plans/`.\n")
        self.assertEqual(result.basis, mb.BASIS_LIST_DRIFTED_BOTH_WAYS)
        self.assertEqual(result.omitted, ("CLAUDE.md", "scripts/release.sh"))
        self.assertEqual(result.overclaimed, ("docs/secret-plans",))

    def test_an_empty_tracked_set_is_UNDECIDABLE_never_green(self):
        """★ THE VACUITY TRAP. With nothing to derive from, 'the prose names everything it must'
        is TRUE and byte-identical to a complete list. It must not be green."""
        result = self.resolve(self.COMPLETE, tracked=frozenset())
        self.assertEqual(result.verdict, mb.UNDECIDABLE)
        self.assertEqual(result.basis, mb.BASIS_NO_TRACKED_EXCLUDES)
        self.assertTrue(result.detail, "an UNDECIDABLE with no reason is a shrug")
        self.assertIn("UNKNOWN", result.render())

    def test_a_missing_bullet_is_UNDECIDABLE_not_a_pass(self):
        result = self.resolve("- some other bullet\n")
        self.assertEqual(result.verdict, mb.UNDECIDABLE)
        self.assertEqual(result.basis, mb.BASIS_LIST_ABSENT)

    def test_an_undecidable_that_cannot_say_why_is_refused_at_construction(self):
        with self.assertRaises(ValueError):
            mb.ShipsListResolution("h", mb.BASIS_LIST_ABSENT)

    def test_the_verdict_cannot_be_set_beside_the_basis(self):
        result = self.resolve(self.COMPLETE)
        with self.assertRaises(AttributeError):
            result.basis = mb.BASIS_LIST_OMITS_AN_EXCLUDED

    # ---- the other list ------------------------------------------------------------------------

    STAYS = ("- **Stays public (do not exclude):** `src/`, `tests/`, and the release-CI helpers "
             "`scripts/check-reproducible.sh` + `scripts/normalize_sdist.py`.\n")

    def test_a_helper_CI_runs_but_the_prose_omits_is_an_offender(self):
        """★ OFFENDER C — stage 6's drift, planted. Excluding a helper `release.yml` runs breaks
        every cut in the repo the cut actually happens in."""
        self.assertEqual(
            mb.unlisted_public_helpers(
                self.STAYS, {"scripts/check-reproducible.sh", "scripts/normalize_sdist.py",
                             "scripts/check-release-assets.sh"}),
            frozenset({"scripts/check-release-assets.sh"}))

    def test_a_listed_helper_CI_does_not_run_is_NOT_an_offender(self):
        """One direction only, declared. `scripts/directory_listing.py` is listed because a shipped
        TEST imports it; convicting it would force the prose to describe only CI."""
        self.assertEqual(
            mb.unlisted_public_helpers(
                self.STAYS + "", {"scripts/check-reproducible.sh"}),
            frozenset())

    def test_an_absent_stays_public_bullet_is_None(self):
        self.assertIsNone(mb.unlisted_public_helpers("- nothing\n", {"scripts/x.sh"}))

    def test_a_prose_entry_covered_only_by_a_GLOB_is_overclaimed_not_waved_through(self):
        """★ UNDECIDABLE IS NOT GREEN, and a check written as `!= RED` would make it so.

        `resolve()` returns `BASIS_PATTERN_COVERAGE` — UNDECIDABLE — for a path a GLOB would
        match, because whether an rsync pattern covers a path is not decidable by string
        comparison. A prose entry resting on one is a claim nobody has checked, and the whole
        module exists because 'we could not tell' and 'it is fine' must not share a
        representation."""
        script = ("rsync -a --exclude='release-backup-*/' \"$SRC\"/ \"$DEST\"/\n"
                  "INTERNAL_PATHS=(\n  'release-backup-*'\n)\n")
        result = mb.resolve_ships_list(
            "- **NEVER ships to public:** `release-backup-2026/`.\n",
            script, frozenset({"release-backup-2026"}))
        self.assertEqual(result.basis, mb.BASIS_LIST_CLAIMS_AN_UNEXCLUDED, result.render())
        self.assertEqual(result.overclaimed, ("release-backup-2026",))


class TestTheCommandPositionReader(unittest.TestCase):
    """`executed_scripts`' three rules, each with the offender only it can see (§7f's 2026-08-04
    amendment: separately gradable, not two defences covering for each other)."""

    def test_a_script_run_inside_an_ASSIGNED_command_substitution_is_found(self):
        """The live shape: `release.yml` writes
        `inputs="$(bash scripts/check-release-assets.sh dist --inputs)"`."""
        self.assertEqual(sc.executed_scripts('inputs="$(bash scripts/x.sh --inputs)"'),
                         frozenset({"scripts/x.sh"}))

    def test_a_script_run_inside_a_BARE_command_substitution_is_found(self):
        """★ THE OFFENDER ONLY THE `$(` SPLIT CAN SEE (§7f, the 2026-08-04 amendment).

        The assigned form above is caught TWICE — by the `$(` separator and, independently, by
        the leading `VAR=` strip, which leaves `bash` as the first word all by itself. Two
        defences of one property are not twice as safe, they are untestable: dropping `$(` from
        the splitter left that test green. With no assignment in front there is nothing to strip,
        so this case is the `$(` separator's alone."""
        self.assertEqual(sc.executed_scripts('echo "$(bash scripts/x.sh --inputs)"'),
                         frozenset({"scripts/x.sh"}))

    def test_a_scripts_path_as_an_ARGUMENT_to_a_non_interpreter_is_not_a_run(self):
        """★ THE INVERSION THIS GUARD MUST NEVER MAKE. `release.yml`'s refusal job ECHOES
        `scripts/release.sh`. Reading that as a run would say the file CI executes must ship —
        of the one file that must never."""
        self.assertEqual(sc.executed_scripts("echo scripts/release.sh"), frozenset())

    def test_only_the_SCRIPT_is_taken_not_its_arguments(self):
        """The first non-flag word is the script; everything after it belongs to the script."""
        self.assertEqual(sc.executed_scripts("bash scripts/x.sh scripts/y.sh"),
                         frozenset({"scripts/x.sh"}))

    def test_a_script_invoked_directly_is_found(self):
        self.assertEqual(sc.executed_scripts("./scripts/x.sh --flag"), frozenset({"scripts/x.sh"}))

    def test_a_flag_before_the_script_is_skipped(self):
        self.assertEqual(sc.executed_scripts("bash -e scripts/x.sh"), frozenset({"scripts/x.sh"}))

    def test_a_commented_out_run_is_not_a_run(self):
        self.assertEqual(sc.executed_scripts("# bash scripts/x.sh"), frozenset())


# =================================================================================================
# THE REAL TREE — the derived axis (§7j)
# =================================================================================================

@unittest.skipUnless(os.path.exists(CLAUDE_MD) and os.path.exists(SYNC_SH), INTERNAL_REASON)
class TestTheRealListsMatchTheRealControls(unittest.TestCase):

    def setUp(self):
        self.prose = _read(CLAUDE_MD)
        self.script = _read(SYNC_SH)
        self.tracked = sr.tracked_excludes(ROOT, mb.exclude_entries(self.script))

    def test_the_tracked_exclude_set_is_not_empty(self):
        """The anti-vacuity floor. Everything below is vacuously true on an empty deriving set."""
        self.assertIsNotNone(self.tracked, "the git index could not be read, so nothing is derived")
        self.assertGreaterEqual(
            len(self.tracked), 5,
            "the tracked-exclude set collapsed to %r — the check below would pass on nothing"
            % (sorted(self.tracked),))

    def test_the_never_ships_list_matches_the_controls_in_BOTH_directions(self):
        result = mb.resolve_ships_list(self.prose, self.script, self.tracked)
        self.assertEqual(result.verdict, mb.GREEN, result.render())

    def test_every_helper_the_workflows_RUN_is_named_as_staying_public(self):
        executed = _executed_scripts()
        self.assertTrue(
            executed, "no workflow was found to run any scripts/ helper — the derivation is "
                      "vacuous, so the check below grades nothing")
        unlisted = mb.unlisted_public_helpers(self.prose, executed)
        self.assertEqual(
            unlisted, frozenset(),
            "release.yml RUNS these and the stays-public list does not name them, so a future "
            "sync could exclude one and break every cut: %s" % sorted(unlisted))

    def test_no_helper_the_workflows_RUN_is_excluded_from_the_mirror(self):
        """The reason the list exists, asserted directly rather than through the prose. The prose
        is where the REASON lives; this is the control."""
        for helper in sorted(_executed_scripts()):
            result = mb.resolve(helper, self.script)
            self.assertEqual(
                result.verdict, mb.RED,
                "%s is executed by a workflow and %s — a helper CI runs must be excluded by "
                "NEITHER control" % (helper, result.render()))
            self.assertEqual(result.basis, mb.BASIS_VERIFIED_NEITHER)

    def test_the_two_dev_only_scripts_are_NOT_read_as_executed(self):
        """★ THE GREP THAT WOULD HAVE BEEN WRONG. `release.yml`'s refusal job ECHOES instructions
        naming `scripts/release.sh` and `scripts/sync-public.sh`. A substring reader promotes both
        to helpers CI runs — and they are the two files that must NEVER ship."""
        executed = _executed_scripts()
        for dev_only in ("scripts/release.sh", "scripts/sync-public.sh"):
            self.assertNotIn(dev_only, executed)
            self.assertIn(dev_only, mb.listed_paths(self.prose, mb.NEVER_SHIPS_HEADING))

    def test_the_two_lists_do_not_overlap(self):
        """A path cannot be both. Nothing enforced it, and the two bullets are edited by hand."""
        never = mb.listed_paths(self.prose, mb.NEVER_SHIPS_HEADING)
        public = mb.listed_paths(self.prose, mb.STAYS_PUBLIC_HEADING)
        self.assertEqual(never & public, frozenset(),
                         "a path is claimed by both mirror-boundary lists")


@unittest.skipUnless(os.path.exists(CLAUDE_MD) and os.path.exists(SYNC_SH), INTERNAL_REASON)
class TestTheKnownBadListIsRED(unittest.TestCase):
    """doc 00 step 6, at one commit's distance: the guard is graded against the text that was
    actually wrong, not only against the text this stage wrote."""

    #: `CLAUDE.md`'s never-ships bullet EXACTLY as it read before this stage — transcribed, because
    #: history cannot be derived (§7j). It is short by two, and the row names only one of them.
    BEFORE = ("- **NEVER ships to public:** `docs/build/`, `docs/launch/`, `docs/marketing/`, "
              "`CLAUDE.md`,\n  `scripts/sync-public.sh`, `scripts/release.sh`.\n")

    def test_the_pre_stage_list_reds_on_exactly_the_two_it_omitted(self):
        script = _read(SYNC_SH)
        tracked = sr.tracked_excludes(ROOT, mb.exclude_entries(script))
        result = mb.resolve_ships_list(self.BEFORE, script, tracked)
        self.assertEqual(result.verdict, mb.RED, "the known-bad list no longer reds")
        self.assertEqual(result.omitted, ("docs/talks", "scripts/check-tracker-tables.py"))
        self.assertEqual(
            result.overclaimed, (),
            "the pre-stage list over-claimed nothing — everything it said was true, which is the "
            "row's own point and must not be lost in a message about what it missed")


@unittest.skipUnless(os.path.exists(CLAUDE_MD) and os.path.exists(SYNC_SH), INTERNAL_REASON)
class TestTheGuardedReadsAreGuardedForTheRightReason(unittest.TestCase):
    """§7g's companion. Without it, 'skipped on the mirror' and 'passed here' look identical."""

    def test_the_files_this_module_needs_are_absent_ONLY_because_of_the_mirror(self):
        if INTERNAL_PRESENT:
            return
        self.assertFalse(                                      # pragma: no cover - mirror only
            os.path.exists(os.path.join(ROOT, "docs", "build")),
            "CLAUDE.md is missing but docs/build/ is present, so this is NOT the public mirror — "
            "the dev checkout has lost a file rather than crossing a boundary")


if __name__ == "__main__":
    unittest.main()
