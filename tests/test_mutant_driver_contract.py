"""Grades the mutant-driver conformance sweep, and then runs it over the real corpus.

0.0.18 exit criterion 5. `tests/_mutant_driver_contract.py` says what it does and does not check;
this file proves it. The order matters and is the same order §7i prescribes: the sweep is graded
against SYNTHETIC offenders first — one per contract element, each derived from one conforming base
by a single edit — and only then pointed at the tree. A sweep that has only ever seen a healthy
tree is a comment, and after `_sync_marker_drift_mutants.sh` was completed the `tests/` half of the
tree is healthy.

WHY THE OFFENDERS ARE DERIVED RATHER THAN WRITTEN. Ten hand-authored broken drivers would each be
broken in ten ways, and a sweep that reds on all of them would be graded by the easiest case. Every
offender here is `CONFORMING` with ONE substring replaced, exactly as `scripts/mutate.sh` mutates a
source file, and the assertion is `missing_elements(offender) == {that one element}` — so an
offender proves BOTH that the element is checked AND that checking it does not misfire on the other
nine. The edits are applied with a found-exactly-once check for the same reason `mutate.sh` exits 3
on a pattern that matches twice.
"""

import os
import pathlib
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _mirror_bookkeeping as MB           # noqa: E402
import _mutant_driver_contract as MDC      # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ⚠ THIS FILE SHIPS AND THE THINGS IT REASONS ABOUT DO NOT. `scripts/sync-public.sh` and
# `docs/build/` are both excluded from the public mirror, so on the repo users clone this file runs
# in a tree where the mirror control cannot be read and three of the twenty-nine drivers are not
# there. `SHIPPED-TEST-READS-INTERNAL-FILE` is what happens when a test forgets that: green here,
# ERRORING in every clone. Everything below is written to hold in BOTH trees — and where it cannot
# be, the guard is the class decorator the sweep prescribes, never a setUpClass skip.
SYNC_SH = os.path.join(ROOT, "scripts", "sync-public.sh")
INTERNAL_TREE = os.path.join(ROOT, MDC.INTERNAL_ROOT)

# ---- the conforming base ----------------------------------------------------------------------
# Shaped like the drivers in the tree, because that is what the sweep reads. It is never executed;
# it is a corpus entry.

CONFORMING = '''\
#!/usr/bin/env bash
# A synthetic conforming driver. Not executed — it is corpus.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
M="${MUTATE_SH:-scripts/mutate.sh}"
F=src/example.py
T='test_example.py'

TOTAL=2
ran=0; red=0; green=0; survivors=""

mutant() {
    local label="$1" rc=0 out
    ran=$((ran + 1))
    out="$("$M" "$@")" || rc=$?
    if [ -n "$out" ]; then printf '%s\\n' "$out"; fi
    if [ "$rc" -ne 0 ]; then
        printf 'BATCH ABORTED — mutate.sh exited %s on mutant %s of %s\\n' "$rc" "$ran" "$TOTAL"
        printf '  %s of %s mutants NEVER RAN.\\n' "$((TOTAL - ran))" "$TOTAL"
        exit "$rc"
    fi
    case "$out" in
        RED*)   red=$((red + 1)) ;;
        GREEN*) green=$((green + 1)); survivors="$survivors  - $label"$'\\n' ;;
        *)      printf 'BATCH ABORTED — no verdict for mutant %s of %s.\\n' "$ran" "$TOTAL"
                exit 70 ;;
    esac
}

mutant "M01 the first thing" "$F" 'a' 'b' "$T"
mutant "M02 the second thing" "$F" 'c' 'd' "$T"

printf 'SYNTHETIC MUTANTS: %s ran of %s — %s RED, %s GREEN\\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS:\\n%s' "$survivors"
    exit 1
fi
'''

# The historical defect itself, quoted from the row: `_sync_marker_drift_mutants.sh` as it stood
# from 2026-08-06 to stage 31. Kept as a corpus entry rather than a memory, because the shape this
# sweep exists to stop must remain reachable by a test after the file that had it was repaired.
BARE_PASSTHROUGH = '''\
#!/usr/bin/env bash
set -u
MUTATE="${MUTATE_SH:-scripts/mutate.sh}"
mutant() { "$MUTATE" "$1" "$2" "$3" "$4" "$5"; }
mutant "S1 something" "$T" 'a' 'b' "$PAT"
'''

# element -> (old, new). ONE edit each, against CONFORMING.
OFFENDERS = {
    MDC.RC_CAPTURED: (
        'out="$("$M" "$@")" || rc=$?',
        'out="$("$M" "$@")"'),
    MDC.NONZERO_ABORTS: (
        '''        printf '  %s of %s mutants NEVER RAN.\\n' "$((TOTAL - ran))" "$TOTAL"
        exit "$rc"''',
        '''        printf '  %s of %s mutants NEVER RAN.\\n' "$((TOTAL - ran))" "$TOTAL"'''),
    MDC.ABORT_CARRIES_STATUS: (
        '        exit "$rc"',
        '        exit 1'),
    MDC.NO_STATUS_EXEMPTED: (
        '    if [ "$rc" -ne 0 ]; then',
        '    if [ "$rc" -ne 0 ] && [ "$rc" -ne 7 ]; then'),
    MDC.RUN_COUNTED: (
        '    ran=$((ran + 1))\n',
        ''),
    MDC.VERDICT_COUNTED: (
        '        RED*)   red=$((red + 1)) ;;',
        '        RED*)   : ;;'),
    MDC.NO_VERDICT_ABORTS: (
        '''        *)      printf 'BATCH ABORTED — no verdict for mutant %s of %s.\\n' "$ran" "$TOTAL"
                exit 70 ;;''',
        '''        *)      printf 'BATCH ABORTED — no verdict for mutant %s of %s.\\n' "$ran" "$TOTAL" ;;'''),
    MDC.TOTAL_DECLARED: (
        'TOTAL=2\n',
        ''),
    MDC.TOTAL_MATCHES_CALL_SITES: (
        'TOTAL=2',
        'TOTAL=3'),
    MDC.SUMMARY_EMITTED: (
        '''printf 'SYNTHETIC MUTANTS: %s ran of %s — %s RED, %s GREEN\\n' "$ran" "$TOTAL" "$red" "$green"''',
        """printf 'done\\n'"""),
}


def _offender(element):
    """CONFORMING with exactly one edit applied — or a hard failure, never a silent no-op."""
    old, new = OFFENDERS[element]
    found = CONFORMING.count(old)
    if found != 1:
        raise AssertionError(
            "the %r offender's pattern occurs %d times in the conforming base, not once. An edit "
            "that did not apply produces an offender identical to the base, and the test that "
            "grades it would pass having graded nothing." % (element, found))
    return CONFORMING.replace(old, new)


class TestTheContractSourceIsWhereThisReadsIt(unittest.TestCase):
    """`mutate.sh`'s header is the single source. If it moves, this sweep must say so rather than
    carry on checking a contract nobody wrote down here."""

    def setUp(self):
        with open(os.path.join(ROOT, MDC.MUTATOR_RELPATH), encoding="utf-8") as fh:
            self.mutator = fh.read()

    def test_the_exit_contract_header_is_still_findable(self):
        self.assertIsNotNone(
            MDC.contract_header(self.mutator),
            "scripts/mutate.sh no longer carries an EXIT CONTRACT section, so this sweep is "
            "grading a contract it cannot read. None, not empty: 'the contract moved' and 'the "
            "contract is empty' must not share a representation.")

    def test_the_one_rule_is_still_the_one_rule(self):
        header = MDC.contract_header(self.mutator)
        self.assertIn(
            MDC.THE_ONE_RULE, header,
            "the ONE RULE — exit 0 iff a verdict was produced — is not stated in the header any "
            "more. Every element this sweep checks is a consequence of it; if it has changed, the "
            "elements have to be re-derived rather than left standing.")

    def test_every_status_this_sweep_reasons_about_is_one_the_contract_documents(self):
        documented = MDC.documented_statuses(MDC.contract_header(self.mutator))
        self.assertTrue(documented, "no exit codes could be derived from the EXIT CONTRACT header")
        for status in MDC.STATUSES_REASONED_ABOUT:
            self.assertIn(
                status, documented,
                "this sweep's failure messages explain exit %s, which mutate.sh's EXIT CONTRACT "
                "does not document — the message would be fiction. (The reverse gap is caught by "
                "test_mutation_harness.TestTheExitContract.)" % status)


class TestTheSweepIsGradedAgainstSyntheticOffenders(unittest.TestCase):
    """§7i. The tree holds no `tests/` offender any more, so the sweep is graded against planted
    ones or it is graded against nothing."""

    def test_the_conforming_base_is_clean(self):
        # If this ever fails, every offender assertion below is meaningless: they would each be
        # reporting the base's own defects rather than the one edit under test.
        self.assertEqual(
            frozenset(), MDC.missing_elements(CONFORMING),
            "the conforming base does not satisfy the contract, so no offender derived from it "
            "isolates anything.")

    def test_the_offender_table_covers_every_contract_element(self):
        # Structural, not incidental: an element added to CONTRACT with no offender beside it is
        # an element nothing grades, and this test is what makes that impossible to do quietly.
        self.assertEqual(
            set(MDC.CONTRACT), set(OFFENDERS),
            "the contract and the offender table disagree. An element with no offender is checked "
            "by a function nothing ever proves can fail.")

    def test_each_offender_reds_exactly_its_own_element(self):
        graded = set()
        for element in MDC.CONTRACT:
            with self.subTest(element=element):
                found = MDC.missing_elements(_offender(element))
                self.assertEqual(
                    {element}, set(found),
                    "the %r offender should red on that element ALONE. Got %s. Either the check "
                    "does not fire (the sweep is decorative for this element) or it fires on more "
                    "than the edit changed (one defect reads as several, and the next reader "
                    "cannot tell which is real). WHY IT MATTERS: %s"
                    % (element, sorted(found) or "nothing", MDC.WHY[element]))
                graded.add(element)
        # No silent skips: a subTest that never ran is a check that never ran.
        self.assertEqual(set(MDC.CONTRACT), graded,
                         "not every contract element was actually graded by this run")

    def test_the_bare_passthrough_is_caught_whole(self):
        # The 2026-08-07 defect verbatim. It is missing everything, and the sweep must not report
        # it as one tidy element — a driver that inspects nothing is not "missing a summary line".
        # The expected set is written out rather than counted, so that a check quietly ceasing to
        # fire cannot be absorbed by a `>= N`. The four NOT listed are the conditional ones:
        # nothing aborts, so no abort can carry a status or exempt one, and nothing parses a
        # verdict, so there are no counters for a tally to name.
        self.assertEqual(
            {MDC.RC_CAPTURED, MDC.NONZERO_ABORTS, MDC.RUN_COUNTED, MDC.VERDICT_COUNTED,
             MDC.NO_VERDICT_ABORTS, MDC.TOTAL_DECLARED},
            set(MDC.missing_elements(BARE_PASSTHROUGH)),
            "the bare passthrough — no rc capture, no exit check, no verdict parse, no TOTAL, no "
            "abort, no summary — is the shape that ran the §7i audit, and it ran it for three "
            "days after the row naming it was filed.")

    def test_a_corpus_of_conforming_drivers_reports_nothing(self):
        # The other direction, and the one that keeps this usable: the sweep must not manufacture
        # findings, or the declared-nonconforming table becomes a place to hide real ones.
        self.assertEqual({}, MDC.nonconforming({"a.sh": CONFORMING, "b.sh": CONFORMING}))

    def test_an_offender_hides_in_a_corpus_of_conforming_drivers(self):
        corpus = {"a.sh": CONFORMING, "b.sh": _offender(MDC.NO_VERDICT_ABORTS),
                  "c.sh": CONFORMING}
        self.assertEqual({"b.sh": frozenset({MDC.NO_VERDICT_ABORTS})}, MDC.nonconforming(corpus))


class TestAMentionOfTheMutatorIsNotABindingToIt(unittest.TestCase):
    """§7f — `mutator_vars` carries TWO conditions and both excluded the same real offender, so
    mutating either one alone survived. They are kept (each sees a shape the other cannot) and made
    SEPARATELY GRADABLE, which is the 2026-08-04 amendment rather than "delete one"."""

    def test_a_real_binding_is_found(self):
        self.assertEqual({"M"}, set(MDC.mutator_vars('M="${MUTATE_SH:-scripts/mutate.sh}"\n')))
        self.assertEqual({"MUTATE"}, set(MDC.mutator_vars('MUTATE="$ROOT/scripts/mutate.sh"\n')))

    def test_prose_that_ends_with_the_mutator_name_is_not_a_binding(self):
        # ONLY the single-token condition sees this: the value ends with `mutate.sh`, so an
        # ends-with test alone accepts a sentence as a handle to the mutator.
        text = 'remedy="  see the EXIT CONTRACT in scripts/mutate.sh"\n'
        self.assertEqual(
            frozenset(), MDC.mutator_vars(text),
            "a remedy sentence was promoted to a mutator handle. That promotes the function it "
            "sits in to a dispatch function, and the tree's own reference driver is then reported "
            "non-conforming on four elements it implements correctly.")

    def test_a_single_token_that_merely_contains_the_name_is_not_a_binding(self):
        # ONLY the ends-with condition sees this: one token, so the space test says nothing.
        self.assertEqual(
            frozenset(), MDC.mutator_vars('LOG="$ROOT/mutate.sh.log"\n'),
            "a path that CONTAINS the mutator's name is not a path TO the mutator")


class TestWhereADriverBelongsIsAnsweredForNamesThatDoNotExistYet(unittest.TestCase):
    """§7i, applied to the location verdict rather than only to the contract checks.

    The UNDECLARED state is unreachable in a healthy tree — that is what "declared" means — so a
    check exercised only against the real corpus grades two of its three outcomes and reads as
    grading all three. These are synthetic names, which is the only way the third one is graded.
    """

    def test_a_driver_in_the_home_directory_ships(self):
        self.assertEqual(MDC.LOCATION_SHIPPED, MDC.location_verdict("tests/_new_mutants.sh"))

    def test_a_declared_driver_is_internal(self):
        self.assertEqual(MDC.LOCATION_DECLARED_INTERNAL,
                         MDC.location_verdict("docs/build/handoff/04-mutants.sh"))

    def test_a_FOURTH_handoff_driver_is_undeclared_rather_than_internal_by_association(self):
        # The exact thing this declaration exists to stop: the next stage filing its batch beside
        # its report, inheriting the partition without joining it.
        self.assertEqual(
            MDC.LOCATION_UNDECLARED, MDC.location_verdict("docs/build/handoff/06-mutants.sh"),
            "a new driver in the internal directory read as internal WITHOUT being declared. "
            "Living beside three declared files is not a declaration, and if it were, the "
            "partition would grow silently — which is how it was created.")

    def test_a_driver_somewhere_else_entirely_is_undeclared(self):
        self.assertEqual(MDC.LOCATION_UNDECLARED, MDC.location_verdict("scripts/_odd_mutants.sh"))


class TestTheDomainIsDerivedAndNotTyped(unittest.TestCase):
    """§7j — of any mechanism that derives, ask which axis is derived and which is a literal."""

    def setUp(self):
        self.discovered = MDC.discover_drivers(ROOT)

    def test_discovery_agrees_with_an_independent_enumeration_of_the_tree(self):
        # A SECOND derivation, written differently on purpose: `rglob` with no skip-list logic at
        # all. This is the check that would have caught this sweep's own first defect, where
        # skipping directories named `build` silently dropped `docs/build/handoff/` and shrank the
        # corpus from 29 to 26 while still reading as "every driver in the tree".
        # CORPUS: THE WORKING TREE — deliberately, and for the same reason the sweep itself walks
        # rather than asks git. `sync-public.sh` mirrors with `rsync`, which copies the WORKING
        # TREE and ignores `.gitignore`, so what ships is what is on disk. An index-based
        # enumeration here would answer a different question from the one the mirror asks.
        independent = set()
        for path in pathlib.Path(ROOT).rglob("*.sh"):
            rel = path.relative_to(ROOT).as_posix()
            if rel.startswith(".git/") or rel.split("/")[0] in ("build", "dist"):
                continue
            if rel == MDC.MUTATOR_RELPATH:
                continue
            if MDC.MUTATOR_REFERENCE in path.read_text(encoding="utf-8"):
                independent.add(rel)
        self.assertEqual(
            independent, set(self.discovered),
            "the walk and an independent enumeration of the same tree disagree. Whichever is "
            "right, the sweep's DOMAIN is not what it claims to be — and a sweep pointed at a "
            "subset reads as a general claim while meaning 'the part I remembered'.")

    def test_discovery_reaches_every_naming_and_location_shape_this_tree_holds(self):
        # Anchors spanning the shapes, so that shrinking the domain to `_stage*`, to
        # `_*_mutants.sh`, or to `tests/` each reds here rather than passing quietly.
        #
        # ⚠ The out-of-`tests/` anchor is added only where that partition EXISTS. In the public
        # mirror every driver is under `tests/` by construction, so naming a `docs/build/` file
        # there would fail for the mirror boundary rather than for a shrunken domain — one of the
        # two would be reported as the other. What is lost with it is stated rather than papered
        # over: in the mirror, a domain shrunk to `tests/` is ungradable, because the mirror holds
        # no driver outside it.
        anchors = ["tests/_run_mutants.sh",
                   "tests/_sync_marker_drift_mutants.sh",
                   "tests/_stage19c_mutants.sh"]
        if os.path.isdir(INTERNAL_TREE):
            anchors += sorted(MDC.DECLARED_INTERNAL_DRIVERS)
        for anchor in anchors:
            self.assertIn(anchor, self.discovered,
                          "%s is a driver and the derivation did not find it" % anchor)

    def test_the_mutator_is_not_counted_as_its_own_driver(self):
        self.assertNotIn(MDC.MUTATOR_RELPATH, self.discovered,
                         "scripts/mutate.sh is the contract's source, not a consumer of it")

    @unittest.skipUnless(
        shutil.which("grep"),
        "no grep on PATH; the same property is checked in pure Python by "
        "test_discovery_agrees_with_an_independent_enumeration_of_the_tree, which always runs")
    def test_the_published_derivation_and_the_pin_agree(self):
        # doc 84 publishes `grep -rl 'mutate.sh' --include='*.sh' .` for this number. If the two
        # ever part, the docs' denominator and the pin's stop being the same claim — which is the
        # defect that gave this row family four different denominators (17 -> 23 -> 24 -> 29).
        proc = subprocess.run(
            ["grep", "-rl", r"mutate\.sh", "--include=*.sh", "."],
            capture_output=True, text=True, cwd=ROOT)
        self.assertIn(proc.returncode, (0, 1), proc.stderr)
        published = {line[2:] if line.startswith("./") else line
                     for line in proc.stdout.split("\n") if line.strip()}
        published -= {MDC.MUTATOR_RELPATH}
        published = {p for p in published if p.split("/")[0] not in ("build", "dist")}
        self.assertEqual(
            published, set(self.discovered),
            "the command the backlog publishes for the driver count and the pin's own derivation "
            "return different sets")

    def test_the_call_site_count_ranges_over_every_dispatch_function(self):
        # THE CLEARED FALSE POSITIVE, graded by name. `_stage19c_mutants.sh` declares TOTAL=7
        # against FIVE `mutant` calls, and that is CORRECT: M05 and M06 are declared through
        # `equivalent()`, which counts into `ran` and is reported separately as EQUIVALENT. A sweep
        # keying on the name `mutant` reports a defect that is not there — it was the coordinator's
        # first reading and it was wrong. The derivation, not an exclusion, is what gets it right.
        text = MDC.read_corpus(ROOT, ["tests/_stage19c_mutants.sh"])["tests/_stage19c_mutants.sh"]
        dispatch = MDC.dispatch_functions(text)
        self.assertEqual(
            {"mutant", "equivalent"}, set(dispatch),
            "the dispatch functions of _stage19c_mutants.sh were derived as %s. Both invoke the "
            "mutator and both count into `ran`." % sorted(dispatch))
        self.assertEqual(7, MDC.call_sites(text, dispatch))
        self.assertEqual(7, MDC.declared_total(text))

    def test_stage19c_comes_back_clean(self):
        # Said by name, because an ungraded exclusion is a future re-filing.
        text = MDC.read_corpus(ROOT, ["tests/_stage19c_mutants.sh"])["tests/_stage19c_mutants.sh"]
        self.assertEqual(
            frozenset(), MDC.missing_elements(text),
            "_stage19c_mutants.sh was reported non-conforming. Check the derivation before "
            "touching that driver: its TOTAL=7 against 5 `mutant` calls is CORRECT.")


class TestTheRealCorpus(unittest.TestCase):
    """Pointed at the tree, last — and every literal it compares against is asserted EXACT."""

    def setUp(self):
        self.discovered = MDC.discover_drivers(ROOT)
        self.corpus = MDC.read_corpus(ROOT, self.discovered)
        self.found = MDC.nonconforming(self.corpus)

    def test_every_shipped_driver_implements_the_contract(self):
        broken = {name: sorted(gaps) for name, gaps in self.found.items()
                  if MDC.location_verdict(name) == MDC.LOCATION_SHIPPED}
        self.assertEqual(
            {}, broken,
            "a driver that ships to the public mirror does not implement the exit contract: %s. "
            "Each missing element costs what mutate.sh's header says it costs — see "
            "_mutant_driver_contract.WHY." % broken)

    def test_the_declared_nonconforming_set_is_exact_in_both_directions(self):
        # Restricted to the drivers THIS TREE holds, because the public mirror legitimately holds
        # none of the declared three — and "absent from the mirror" and "repaired" must not reach
        # this assertion as the same fact. Whichever tree it runs in, every driver present is
        # compared against its declaration in both directions.
        expected = {k: set(v) for k, v in MDC.DECLARED_NONCONFORMING.items()
                    if k in self.discovered}
        self.assertEqual(
            expected, {k: set(v) for k, v in self.found.items()},
            "the declaration and the tree disagree. A driver missing from the declaration is a "
            "NEW non-conformance; a driver still in it that now conforms is a stale exemption, "
            "and a stale exemption is where the next real one hides.")

    def test_every_driver_location_is_declared(self):
        undeclared = [name for name in self.discovered
                      if MDC.location_verdict(name) == MDC.LOCATION_UNDECLARED]
        self.assertEqual(
            [], undeclared,
            "these drivers sit outside %s/ and are not declared internal: %s. Location is part of "
            "the contract because it decides whether the driver SHIPS — an undeclared one makes "
            "the mirror's mutation corpus incomplete while it reads as complete."
            % (MDC.DRIVER_HOME, undeclared))

    def test_the_internal_partition_is_present_whole_or_absent_whole(self):
        # NOT a skip — the two trees get two assertions, because "the mirror does not carry these"
        # and "these were deleted" are different facts (§7g). In the dev tree the declaration must
        # name files that exist; in the mirror NONE of them may exist, which is the mirror boundary
        # doing exactly what the declaration claims it does.
        here = [n for n in MDC.DECLARED_INTERNAL_DRIVERS if n in self.discovered]
        if os.path.isdir(INTERNAL_TREE):
            self.assertEqual(
                sorted(MDC.DECLARED_INTERNAL_DRIVERS), sorted(here),
                "%s/ is present, so every declared internal driver must be there. A declaration "
                "about a file that is gone is a claim nothing checks — after the deletion lane "
                "removes drivers, this is the line that says so." % MDC.INTERNAL_ROOT)
        else:
            self.assertEqual(
                [], here,
                "%s/ is excluded from the public mirror, yet drivers declared to live there were "
                "found. Either the boundary leaked or the declaration is about somewhere else."
                % MDC.INTERNAL_ROOT)

    def test_every_declared_internal_driver_lives_under_the_internal_root(self):
        # Without this the mirror control below would be grounding a claim about a different path.
        for name in MDC.DECLARED_INTERNAL_DRIVERS:
            self.assertTrue(
                name.startswith(MDC.INTERNAL_ROOT + "/"),
                "%s is declared internal but does not live under %s/, so the mirror control says "
                "nothing about it" % (name, MDC.INTERNAL_ROOT))


class TestTheMirrorBoundaryIsWhyTheClassBelowCanBeSkipped(unittest.TestCase):
    """The companion the shipped-reads sweep requires: the skip's precondition, asserted.

    A `skipUnless` whose condition nobody checks is a check that quietly stopped running. This
    runs in BOTH trees and pins that the two dev-only facts move together.
    """

    def test_the_control_and_the_partition_are_absent_together_or_present_together(self):
        self.assertEqual(
            os.path.exists(SYNC_SH), os.path.isdir(INTERNAL_TREE),
            "scripts/sync-public.sh and %s/ are excluded from the public mirror by the SAME "
            "boundary, so they are present together or absent together. One without the other "
            "means this file's dev/mirror discriminator is measuring something else, and the "
            "grounding tests below would be skipped for a reason that is not true."
            % MDC.INTERNAL_ROOT)


@unittest.skipUnless(os.path.exists(SYNC_SH),
                     "sync-public.sh is dev-only, excluded from the public mirror")
class TestTheDeclarationIsGroundedInTheControlThatMakesItTrue(unittest.TestCase):
    """"Declared internal" must mean "provably not mirrored", or it is a comment."""

    def setUp(self):
        self.script = MB.read_script(ROOT)
        self.assertIsNotNone(self.script, "scripts/sync-public.sh could not be read")

    def test_the_internal_root_is_excluded_by_both_mirror_controls(self):
        verdict = MB.resolve(MDC.INTERNAL_ROOT, self.script)
        self.assertEqual(
            MB.GREEN, verdict.verdict,
            "%s/ is not excluded from the public mirror by both controls (%s), so drivers "
            "declared internal because they live there would ship after all."
            % (MDC.INTERNAL_ROOT, verdict.basis))

    def test_the_shipped_partition_really_does_ship(self):
        # The other half, and not symmetry for its own sake: if `tests/` were ever excluded, every
        # driver would be internal and the conformance sweep would be grading a set that reaches
        # nobody. RED here is the DESIRED verdict — it means no control excludes the path.
        discovered = MDC.discover_drivers(ROOT)
        shipped = [n for n in discovered if MDC.location_verdict(n) == MDC.LOCATION_SHIPPED]
        self.assertTrue(shipped, "no driver is in the shipped partition at all")
        for name in shipped[:1] + [MDC.DRIVER_HOME]:
            self.assertEqual(
                MB.RED, MB.resolve(name, self.script).verdict,
                "%s is excluded from the public mirror, so the drivers this sweep treats as "
                "shipped do not reach the mirror's corpus" % name)


if __name__ == "__main__":
    unittest.main()
