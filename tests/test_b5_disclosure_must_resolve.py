"""B5 — a published schedule must RESOLVE, not merely appear. THREE states, and an accounted slip.

0.0.19 stage 06, row B5 (`disclosure-must-resolve`). `release-notes-check` graded whether a promise
was still PRINTED and never whether it was still TRUE, so `CHANGELOG.md` and `RELEASE_NOTES.md`
shipped at `v0.0.18` and went to PyPI carrying five commitments against a release that no longer
contained four of them, with every check green.

WHAT EACH DELIVERABLE IS GRADED BY, named so a reader can check the mapping rather than trust it:

    1  a claim must RESOLVE, not appear    TestResolutionIsNotPresence
    2  three states, never two             TestThreeStates  (+ TestTheMirrorLeg)
    3  a disclosed slip is not broken      TestTheDisclosedSlip / TestTheSilentBreak  ← THE PAIR
    4  offender 1 fixed, as the proof      TestOffenderOne
    5  wired where the check already runs  TestTheWiring
       the population is derived           TestThePopulationIsDerived
       the accounting cannot grow          TestTheAccountingIsNarrow
       the boundary is not crossed         TestNothingShippedReadsThePlans

⭐ DELIVERABLE 3 IS TWO CLASSES ON PURPOSE. Stage 05's first batch scored 14/16 on exactly the
defect of one test covering two clauses: each half covered for the other and neither was graded. The
accounted claim passing and the unaccounted one reding are separate facts about separate inputs, so
they are separate tests, and a mutant that widens the accounting to cover everything kills only one
of them — which is how it becomes visible instead of absorbed.

Pure/offline. Imports via `_support`.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import re
import subprocess
import sys
import unittest

import _support  # noqa: F401  (puts src/ on the path)
from mokata import __version__
from mokata import disclosure as D
from mokata import packaging

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

#: The live offenders this row was held open for (doc 85 §7i). Named as an ASSERTION about the
#: derived population, never as the input to it — see `TestThePopulationIsDerived`.
OFFENDER_CONSTANT = "src/mokata/branch_protection.py"
OFFENDER_KEY = "BRANCH-PROTECTION-DEGRADED-PASS"
CLASS_C_FILES = ("CHANGELOG.md", "RELEASE_NOTES.md")


def _at(*parts):
    return os.path.join(_REPO, *parts)


def _read(rel):
    with open(_at(*rel.split("/")), encoding="utf-8") as fh:
        return fh.read()


def _shipped():
    return packaging.shipped_claim_sources(_REPO)


def _plans():
    """The planning corpus, read HERE because this file is a test and tests may read anything the
    dev tree has. ⚠ It is NOT read by anything that ships — `TestNothingShippedReadsThePlans` holds
    that line, and this class is existence-guarded so the mirror skips it by design rather than by
    accident (stage 05's `_internal_subject` model)."""
    import glob
    corpus = {}
    # CORPUS: THE WORKING TREE — the planning documents are read as they stand on disk, which is
    # what the maintainers actually plan from and what the resolving gate reads at a cut. The index
    # would be blind to a plan document written but not yet committed, i.e. to exactly the release
    # a claim is most likely to name.
    for path in sorted(glob.glob(os.path.join(DOCS_BUILD, "*.md"))):
        rel = _support.posix_rel(path, _REPO)
        with open(path, encoding="utf-8") as fh:
            corpus[rel] = fh.read()
    return corpus


#: THE GUARD SUBJECTS, in the one shape `_shipped_reads` accepts as a guard and `_internal_subject`
#: can resolve: a module-level `NAME = os.path.join(ROOT, "a", "b")` with a constant tail. `tests/`
#: SHIPS and neither of these does, so the classes below that read them carry a CLASS-LEVEL
#: `@unittest.skipUnless(os.path.exists(...))` — not a method-level one. The difference is not
#: style: stage 05's census reads guarded CLASSES, so a method-level guard skips invisibly and
#: `ABSENT_BY_DESIGN` becomes indistinguishable from `grades nothing`, which is the exact §7g
#: collapse that stage existed to remove. A method-guarded read here would also be
#: `SHIPPED-TEST-READS-INTERNAL-FILE` with no accepted guard over it.
DOCS_BUILD = os.path.join(_REPO, "docs", "build")
GATE = os.path.join(_REPO, "scripts", "check-tracker-tables.py")
RELEASE_SH = os.path.join(_REPO, "scripts", "release.sh")
SYNC_SH = os.path.join(_REPO, "scripts", "sync-public.sh")


def assert_no_secret(case, rendered):
    """No credential, DSN or host value in a rendered verdict. One assertion, both legs — a second
    copy of this list would be two lists that can disagree about what a secret looks like."""
    for token in ("postgres://", "postgresql://", "password=", "@localhost", "GH_TOKEN",
                  "ghp_", "Bearer "):
        case.assertNotIn(token, rendered)


def _claim(text, target="0.0.19", where="CHANGELOG.md", line=1, pattern="scheduled-for", key=None):
    return D.Claim(where=where, line=line, target=target, pattern=pattern, text=text, key=key)


# ---- deliverable 1 -------------------------------------------------------------------------------

class TestResolutionIsNotPresence(unittest.TestCase):
    """The predicate is *resolves*, not *is present*. This row exists because a presence test
    passed, so the difference is asserted directly rather than assumed from the implementation."""

    def test_b5_presence_is_not_resolution(self):
        # ONE key, present in the corpus in BOTH runs, with the target the only thing that changes.
        # A presence test cannot distinguish these two; the verdict must.
        plans = {"plan.md": "| Row | Spec | Status | Target |\n|---|---|---|---|\n"
                            "| **A-B-C** | work | open | **0.0.20** |\n"}
        index = D.plan_assignments(plans)
        true_claim = _claim("restore A-B-C in 0.0.20", target="0.0.20", key="A-B-C")
        false_claim = _claim("restore A-B-C in 0.0.19", target="0.0.19", key="A-B-C")
        self.assertIn("A-B-C", plans["plan.md"])          # present, both times
        self.assertEqual(D.resolve(true_claim, index).state, D.RESOLVES)
        self.assertEqual(D.resolve(false_claim, index).state, D.UNRESOLVED)

    def test_b5_a_row_that_only_quotes_a_key_does_not_resolve_it(self):
        """⭐ THE FALSE GREEN A PRESENCE TEST WOULD HAVE PRODUCED, and it is live in this repo:
        the backlog holds `BRANCH-PROTECTION-DEGRADED-PASS` twice — once as the row that OWNS the
        work, and once inside a different row that quotes the offending constant to complain about
        it. Those rows carry different targets. Keying on the row's first cell is the whole
        difference between resolving the promise and resolving the complaint about it."""
        plans = {"plan.md":
                 "| Row | Spec | Status | Target |\n|---|---|---|---|\n"
                 "| **A-B-C** | the work | open | **0.0.20** |\n"
                 "| **X-Y-Z-COMPLAINT** | a row quoting `A-B-C` | filed | **0.0.19** |\n"}
        index = D.plan_assignments(plans)
        self.assertEqual([a.release for a in index["A-B-C"]], ["0.0.20"])
        self.assertEqual(
            D.resolve(_claim("restore A-B-C in 0.0.19", key="A-B-C"), index).state, D.UNRESOLVED)

    def test_b5_a_row_whose_first_cell_is_not_a_key_indexes_nothing(self):
        """⭐ THE MUTANT THAT SURVIVED THE FIRST BATTERY (A02), and the gap it exposed was in the
        TEST, not the code: the original fixture gave the quoting row an ID of its own, so a reader
        that searched the whole row still found that ID first and the false green never appeared.
        The shape that actually produces it is a row with no ID at all — a note, a continuation, a
        cell of prose — whose text happens to quote a key. `match` on cell 0 indexes nothing there;
        `search` over the row indexes the quoted key at the quoting row's target."""
        plans = {"plan.md":
                 "| Row | Spec | Status | Target |\n|---|---|---|---|\n"
                 "| **A-B-C** | the work | open | **0.0.20** |\n"
                 "| a note about `A-B-C` | quoting it to complain | filed | **0.0.19** |\n"}
        index = D.plan_assignments(plans)
        self.assertEqual(sorted({a.release for a in index["A-B-C"]}), ["0.0.20"],
                         "a row that merely QUOTES a key was allowed to assign it a release")
        self.assertEqual(
            D.resolve(_claim("restore A-B-C in 0.0.19", key="A-B-C"), index).state, D.UNRESOLVED)

    def test_b5_an_unknown_key_is_owned_by_nothing(self):
        verdict = D.resolve(_claim("restore Q-R-S in 0.0.19", key="Q-R-S"), {})
        self.assertEqual(verdict.state, D.UNRESOLVED)
        self.assertIn("owned by nothing", verdict.detail)


# ---- deliverable 2: three states, never two ------------------------------------------------------

class TestThreeStates(unittest.TestCase):
    """Resolves · does not resolve · NOT CHECKABLE FROM HERE — three distinct outcomes.

    ⛔ A test covering only the first two passes against a build where the mirror silently passes
    everything, which is the failure mode this class exists to make impossible."""

    PLANS = {"plan.md": "| Row | Spec | Status | Target |\n|---|---|---|---|\n"
                        "| **A-B-C** | work | open | **0.0.20** |\n"}

    def test_b5_three_states_are_three_distinct_outcomes(self):
        index = D.plan_assignments(self.PLANS)
        keyed = _claim("restore A-B-C in 0.0.20", target="0.0.20", key="A-B-C")
        wrong = _claim("restore A-B-C in 0.0.19", key="A-B-C")
        states = {
            D.resolve(keyed, index).state,        # RESOLVES
            D.resolve(wrong, index).state,        # UNRESOLVED
            D.resolve(keyed, None).state,         # NOT_CHECKABLE — no corpus obtainable
        }
        self.assertEqual(states, {D.RESOLVES, D.UNRESOLVED, D.NOT_CHECKABLE},
                         "three states collapsed into fewer: %r" % (sorted(states),))

    def test_b5_not_checkable_is_not_a_pass(self):
        verdict = D.resolve(_claim("restore A-B-C in 0.0.20", target="0.0.20", key="A-B-C"), None)
        self.assertEqual(verdict.state, D.NOT_CHECKABLE)
        self.assertFalse(verdict.passing, "NOT_CHECKABLE was admitted to the passing set")
        self.assertNotIn(D.NOT_CHECKABLE, D.PASSING)

    def test_b5_not_checkable_is_not_a_failure_either(self):
        # ⚠ `accounted=()` BECAUSE THE CORPUS IS SYNTHETIC. Staleness is a property of the
        # DECLARED table against the REAL tree; grading it over a one-line corpus makes every real
        # entry stale and reds this test for a reason it does not state. A test that goes red for
        # the wrong reason is a test that will go green for the wrong reason.
        report = D.check_disclosure_resolves(
            {"CHANGELOG.md": "restore A-B-C in 0.0.20"}, None, cutting="0.0.18", accounted=())
        self.assertTrue(report.undecided)
        self.assertFalse(report.failed, "the mirror's honest 'not from here' was rendered as red")
        self.assertIn("NOT CHECKABLE HERE", report.render())

    def test_b5_an_empty_corpus_is_not_an_absent_one(self):
        """⚠ `{}` and `None` MUST NOT COLLAPSE. `{}` is "consulted, found nothing" and reds; `None`
        is "could not be obtained" and is the third state. Collapsing them reds the mirror on every
        claim, which is the third state lost in the OTHER direction."""
        claim = _claim("restore A-B-C in 0.0.20", target="0.0.20", key="A-B-C")
        self.assertEqual(D.resolve(claim, {}).state, D.UNRESOLVED)
        self.assertEqual(D.resolve(claim, None).state, D.NOT_CHECKABLE)

    def test_b5_the_reason_distinguishes_the_two_ways_a_claim_is_uncheckable(self):
        no_corpus = D.resolve(_claim("restore A-B-C in 0.0.20", target="0.0.20", key="A-B-C"), None)
        no_key = D.resolve(_claim("scheduled for 0.0.20", target="0.0.20"), None)
        self.assertIn(D.REASON_NO_CORPUS, no_corpus.detail)
        self.assertEqual(no_key.state, D.UNRESOLVED)
        self.assertIn(D.REASON_NO_KEY, no_key.detail)


class TestTheMirrorLeg(unittest.TestCase):
    """The SHIPPED leg, which is the one that runs on the mirror at tag time."""

    def test_b5_the_shipped_leg_never_supplies_a_plan_corpus(self):
        result = packaging.check_release_notes("0.0.18", root=_REPO)
        self.assertIsNotNone(result.claims)
        self.assertFalse(result.claims.corpus_supplied)
        self.assertTrue(result.claims_undecided,
                        "the shipped leg claimed to have decided a keyed promise it cannot read")
        self.assertFalse(result.claims_failed)

    def test_b5_the_shipped_leg_still_finds_the_population(self):
        result = packaging.check_release_notes(__version__, root=_REPO)
        self.assertTrue(result.claims.verdicts,
                        "the shipped leg reported NOT CHECKABLE over an EMPTY population, which "
                        "grades nothing and looks identical to a clean tree")

    def test_b5_dg7_verdicts_are_unchanged_for_everything_that_is_not_a_schedule(self):
        """No behaviour change to the existing check. `ok` is the DG-7 axis and it moves only on
        DG-7 inputs; the schedule axis is separate, which is why it got its own exit code."""
        # ⚠ THE TARGET FOLLOWS THE TREE (0.0.19 stage 12). It was typed as "0.0.18" — the version
        # this tree carried while the row was built — and a DG-7 leg pointed at a release the
        # notes no longer announce reds at the first cut after it, on the cut rather than on a
        # defect. `__version__` is what `release-notes-check` itself defaults to.
        result = packaging.check_release_notes(__version__, root=_REPO)
        self.assertTrue(result.version_ok)
        self.assertTrue(result.disclosure_ok)
        self.assertTrue(result.ok)


# ---- deliverable 3: THE LOAD-BEARING PAIR, two classes, two inputs -------------------------------

class TestTheDisclosedSlip(unittest.TestCase):
    """HALF ONE: a commitment published AS slipped PASSES.

    The `0.0.19` strings in the tagged files are Class C — left verbatim by ruling, because
    renumbering a published commitment is the silent move this row forbids. A check that reds on
    them has learned the wrong lesson."""

    def test_b5_the_class_c_notes_strings_pass(self):
        report = D.check_disclosure_resolves(_shipped(), None, cutting="0.0.18")
        offenders = [v for v in report.verdicts
                     if v.claim.where in CLASS_C_FILES and v.claim.target == "0.0.19"]
        self.assertTrue(offenders, "the Class-C population vanished — this test now grades nothing")
        for verdict in offenders:
            self.assertIn(verdict.state, (D.DISCLOSED, D.HELD),
                          "a deliberately un-moved published commitment reded: %s"
                          % verdict.render())

    def test_b5_a_disclosed_slip_names_where_and_when_it_was_published(self):
        report = D.check_disclosure_resolves(_shipped(), None, cutting="0.0.18")
        disclosed = report.of(D.DISCLOSED)
        self.assertTrue(disclosed)
        for verdict in disclosed:
            self.assertIn("disclosed 2026-08-19", verdict.detail)
            self.assertIn("GitHub release body", verdict.detail)

    def test_b5_held_and_disclosed_are_different_states(self):
        """⭐ The PostgreSQL floor commitment did NOT slip — its date is PostgreSQL 14's upstream
        EOL, an external clock, so it stays at the slot it was published against and must be ABSENT
        from the re-scheduled disclosure. Rendering it as DISCLOSED would excuse a promise that
        still has to be kept."""
        report = D.check_disclosure_resolves(_shipped(), None, cutting="0.0.18")
        held = report.of(D.HELD)
        self.assertTrue(held, "the HELD commitment disappeared from the population")
        for verdict in held:
            # DERIVED from the claim, not hand-typed beside it: a release literal in an assertion
            # is a second copy of a mutable promise, and `test_no_test_hand_types_the_removal_
            # release` convicts it — correctly, and it convicted the first draft of this line.
            self.assertIn("stays at %s" % verdict.claim.target, verdict.detail)
            self.assertNotIn("disclosed", verdict.detail)


class TestTheSilentBreak(unittest.TestCase):
    """HALF TWO: a commitment that MOVED WITH NO DISCLOSURE REDS.

    ⛔ Deliberately a separate class from `TestTheDisclosedSlip`. If one test asserted both, each
    clause would cover for the other and a mutant widening the accounting to everything would still
    pass it — stage 05's 14/16 defect, one release later."""

    def test_b5_an_unaccounted_live_promise_reds(self):
        report = D.check_disclosure_resolves(
            {"CHANGELOG.md": "the ranking repair is scheduled for 0.0.19"}, None,
            cutting="0.0.18", accounted=())
        self.assertEqual([v.state for v in report.verdicts], [D.UNRESOLVED])
        self.assertTrue(report.failed)

    def test_b5_a_promise_that_moved_with_no_disclosure_reds_even_beside_accounted_ones(self):
        """The realistic shape: a NEW promise added next to the ones already declared. The
        accounting is keyed on a distinctive fragment, so it cannot spread to the newcomer."""
        sources = dict(_shipped())
        sources["CHANGELOG.md"] = (sources["CHANGELOG.md"]
                                   + "\n- the widget rewrite is scheduled for 0.0.19.\n")
        report = D.check_disclosure_resolves(sources, None, cutting="0.0.18")
        reded = [v for v in report.unresolved if "widget rewrite" in v.claim.text]
        self.assertEqual(len(reded), 1, "an unaccounted new promise was absorbed by the accounting")
        self.assertTrue(report.failed)

    def test_b5_the_pair_disagrees_on_the_same_kind_of_input(self):
        """The two halves are graded against the SAME phrasing, differing only in whether the
        commitment is accounted. If a build makes these agree, one of them is not grading."""
        # DERIVED from the table rather than re-typed: the 0.0.19 cut rewrote RELEASE_NOTES.md
        # wholesale, and the entry this probe used to quote lived there. A fixture that quotes an
        # accounting entry has to be taken FROM the accounting, or the next cut silently turns the
        # accounted half of this pair into the unaccounted one and the test still passes.
        _rehomed = next(e for e in D.PUBLISHED_COMMITMENTS
                        if e.status == D.ACCOUNT_DISCLOSED and "re-homed" in e.fragment)
        accounted = _claim(_rehomed.fragment, target=_rehomed.promised_for, pattern="re-homed-to")
        unaccounted = _claim("The widget rewrite is re-homed to **0.0.19**.", pattern="re-homed-to")
        self.assertEqual(D.resolve(accounted, None, cutting="0.0.18").state, D.DISCLOSED)
        self.assertEqual(D.resolve(unaccounted, None, cutting="0.0.18").state, D.UNRESOLVED)


class TestTheAccountingIsNarrow(unittest.TestCase):
    """The declared table cannot grow to cover everything, and cannot go stale in silence."""

    def test_b5_every_entry_carries_a_fragment_a_date_and_a_venue(self):
        for entry in D.PUBLISHED_COMMITMENTS:
            self.assertTrue(entry.fragment.strip(), "an empty fragment matches every claim")
            self.assertRegex(entry.on, r"^\d{4}-\d{2}-\d{2}$")
            self.assertTrue(entry.venue.strip())
            self.assertIn(entry.status, (D.ACCOUNT_DISCLOSED, D.ACCOUNT_HELD))

    def test_b5_a_disclosed_entry_moved_and_a_held_entry_did_not(self):
        for entry in D.PUBLISHED_COMMITMENTS:
            if entry.status == D.ACCOUNT_DISCLOSED:
                self.assertTrue(entry.moved,
                                "%r is declared DISCLOSED but lands where it was promised — a slip "
                                "that did not slip" % entry.fragment)
            else:
                self.assertFalse(entry.moved,
                                 "%r is declared HELD but names a different release" % entry.fragment)

    def test_b5_no_accounting_entry_is_stale(self):
        claims = D.scheduling_claims(_shipped())
        self.assertTrue(claims)
        self.assertEqual(D.stale_accounts(claims), (),
                         "an accounting entry describes no claim — it grants a pass to nothing "
                         "while the reworded claim goes unguarded")

    def test_b5_a_reworded_claim_makes_its_entry_stale_loudly(self):
        entry = D.PublishedCommitment(
            fragment="a phrasing nobody uses any more", promised_for="0.0.19", now_lands="0.0.20",
            status=D.ACCOUNT_DISCLOSED, on="2026-08-19", venue="somewhere")
        self.assertEqual(D.stale_accounts(D.scheduling_claims(_shipped()), (entry,)), (entry,))

    def test_b5_an_empty_fragment_covers_nothing(self):
        """⛔ THE BLANK CHEQUE (mutant C03). `"" in text` is TRUE for every text, so an entry with
        no fragment silently exempts the entire population. The declared table is checked for
        non-empty fragments above — but that grades the TABLE, and this grades the CODE, which is
        what a future entry would run into. Both are needed: the first survived the mutation."""
        blank = D.PublishedCommitment(
            fragment="", promised_for="0.0.19", now_lands="0.0.20",
            status=D.ACCOUNT_DISCLOSED, on="2026-08-19", venue="somewhere")
        self.assertIsNone(D.account_for(_claim("the fix is scheduled for 0.0.19"), (blank,)))
        report = D.check_disclosure_resolves(
            {"CHANGELOG.md": "the fix is scheduled for 0.0.19"}, None, cutting="0.0.18",
            accounted=(blank,))
        self.assertEqual([v.state for v in report.verdicts], [D.UNRESOLVED])

    def test_b5_the_report_reds_on_a_stale_entry(self):
        """⛔ MUTANT C04. `stale_accounts` is asserted directly above, which leaves the REPORT's
        wiring of it ungraded — and a check whose staleness half is computed and then dropped on
        the floor is exactly as blind as one that never computed it."""
        entry = D.PublishedCommitment(
            fragment="a phrasing nobody uses any more", promised_for="0.0.19", now_lands="0.0.20",
            status=D.ACCOUNT_DISCLOSED, on="2026-08-19", venue="somewhere")
        report = D.check_disclosure_resolves(
            {"CHANGELOG.md": "no promises here"}, None, cutting="0.0.18", accounted=(entry,))
        self.assertTrue(report.failed, "a stale accounting entry did not fail the report")
        self.assertIn("STALE accounting entry", report.render())

    def test_b5_a_keyed_claim_can_never_be_accounted(self):
        """⛔ NARROWING #1. A claim naming an item key is resolved by lookup; letting a declaration
        excuse it is the mutant that turns this check back into the exemption list it replaced."""
        entry = D.PublishedCommitment(
            fragment="restore full verification", promised_for="0.0.19", now_lands="0.0.20",
            status=D.ACCOUNT_DISCLOSED, on="2026-08-19", venue="somewhere")
        keyed = _claim("A-B-C — restore full verification in 0.0.19", key="A-B-C")
        self.assertIsNone(D.account_for(keyed, (entry,)))
        self.assertEqual(D.resolve(keyed, {}, cutting="0.0.18", accounted=(entry,)).state,
                         D.UNRESOLVED)

    def test_b5_the_accounting_never_answers_before_not_checkable(self):
        """⛔ NARROWING #2. A slip we could not check is not a slip we disclosed. If the table could
        answer first, the mirror would pass every keyed claim through the accounting column."""
        entry = D.PublishedCommitment(
            fragment="restore full verification", promised_for="0.0.19", now_lands="0.0.20",
            status=D.ACCOUNT_DISCLOSED, on="2026-08-19", venue="somewhere")
        keyed = _claim("A-B-C — restore full verification in 0.0.19", key="A-B-C")
        self.assertEqual(D.resolve(keyed, None, cutting="0.0.18", accounted=(entry,)).state,
                         D.NOT_CHECKABLE)


# ---- deliverable 4: offender 1, fixed, as the proof ----------------------------------------------

class TestOffenderOne(unittest.TestCase):
    """`branch_protection.RESTORE_ROW` — a RUNTIME CONSTANT, printable to a user and shipped to
    PyPI, that promised a restore 0.0.19's scope does not contain. The row was held unfixed on
    purpose so the check would have a live subject (doc 85 §7i)."""

    def test_b5_the_offender_is_found_in_src_by_derivation(self):
        claims = [c for c in D.scheduling_claims(_shipped()) if c.where == OFFENDER_CONSTANT]
        self.assertEqual(len(claims), 1, "the src/ offender left the derived population")
        self.assertEqual(claims[0].key, OFFENDER_KEY)

    def test_b5_a_grep_for_scheduled_for_could_never_have_found_it(self):
        """⚠ The coordinator's grep was a starting point, not the answer: the constant says
        *"restore full verification in 0.0.19"*, which no `scheduled for` pattern matches. The
        `owed-in` phrasing exists for that shape and this test is why it cannot be dropped."""
        text = _read(OFFENDER_CONSTANT)
        self.assertNotIn("scheduled for", text)
        claims = [c for c in D.scheduling_claims(_shipped()) if c.where == OFFENDER_CONSTANT]
        self.assertEqual([c.pattern for c in claims], ["owed-in"])


@unittest.skipUnless(
    os.path.exists(DOCS_BUILD),
    "the planning documents are dev-only, excluded from the public mirror twice over",
)
class TestOffenderOneAgainstThePlans(unittest.TestCase):
    """The half of offender 1 that needs the planning corpus — everything that RESOLVES.

    Split into its own class so the guard is CLASS-level: see the note on `DOCS_BUILD`. On the
    mirror this is `ABSENT_BY_DESIGN` and stage 05's census asserts that pairing, so the skip is a
    stated fact rather than a silence."""

    def test_b5_regression_the_offender_now_resolves(self):
        """FAIL-ON-OLD: with `0.0.19` in that constant this reds, naming 0.0.20 as the release the
        plan actually assigns the item to. Run before the fix and pasted into the stage report."""
        report = D.check_disclosure_resolves(_shipped(), _plans(), cutting="0.0.18")
        mine = [v for v in report.verdicts if v.claim.where == OFFENDER_CONSTANT]
        self.assertEqual([v.state for v in mine], [D.RESOLVES], "\n".join(v.render() for v in mine))

    def test_b5_regression_no_shipped_surface_publishes_an_unresolved_schedule(self):
        report = D.check_disclosure_resolves(_shipped(), _plans(), cutting="0.0.18")
        self.assertEqual(report.unresolved, (),
                         "\n".join(v.render() for v in report.unresolved))
        self.assertEqual(report.stale, ())

    def test_b5_the_offender_was_false_not_stale(self):
        """The decision, held to the corpus that decided it: the row that OWNS the restore is open
        and assigns it to 0.0.20. STALE would have meant already-done; the assignment says owed."""
        index = D.plan_assignments(_plans())
        self.assertIn(OFFENDER_KEY, index)
        self.assertEqual(sorted({a.release for a in index[OFFENDER_KEY]}), ["0.0.20"])


# ---- deliverable 5: wiring ------------------------------------------------------------------------

class TestTheWiring(unittest.TestCase):
    """A check that exists and runs nowhere is B1's row, in the release that just fixed it."""

    def test_b5_the_ci_leg_handles_exit_2_explicitly(self):
        text = _read(".github/workflows/release.yml")
        self.assertIn("release-notes-check", text)
        self.assertIn("NOT CHECKABLE HERE", text)
        self.assertIn('*) exit "$rc" ;;', text,
                      "the CI leg swallows every non-zero code, not just the one it was taught")

    def test_b5_the_cli_maps_the_three_states_to_three_exit_codes(self):
        text = _read("src/mokata/cli_commands/core.py")
        self.assertIn("return 2 if res.claims_undecided else 0", text)
        self.assertIn("if not res.ok or res.claims_failed:", text)

    def test_b5_the_shipped_command_exits_2_here(self):
        proc = subprocess.run(
            [sys.executable, "-m", "mokata", "release-notes-check", "--root", _REPO],
            cwd=_REPO, capture_output=True, text=True, stdin=subprocess.DEVNULL,
            env=dict(os.environ, PYTHONPATH=os.path.join(_REPO, "src")))
        self.assertEqual(proc.returncode, 2,
                         "expected NOT CHECKABLE (2) from the shipped leg\n%s\n%s"
                         % (proc.stdout[-2000:], proc.stderr[-2000:]))
        self.assertIn("NOT CHECKABLE HERE", proc.stdout)


@unittest.skipUnless(
    os.path.exists(GATE) and os.path.exists(DOCS_BUILD),
    "the internal doc gate and the planning documents are dev-only, excluded from the mirror",
)
class TestTheInternalGate(unittest.TestCase):
    """The resolving leg — the one that HAS the documents and therefore can answer."""

    def test_b5_the_internal_gate_resolves_and_exits_zero(self):
        proc = subprocess.run(
            [sys.executable, GATE, "--disclosure-only"],
            cwd=_REPO, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(proc.returncode, 0, proc.stdout[-3000:] + proc.stderr[-2000:])
        self.assertIn("every published schedule resolves", proc.stdout)


# ---- the population is DERIVED, and non-empty before anything acts on it --------------------------

class TestThePopulationIsDerived(unittest.TestCase):
    """§7i, and stage 05's near-miss: if a harness computes a list, assert it is non-empty BEFORE
    acting on it. `UNITTEST-BARE-INVOCATION-GRADES-EVERYTHING` is that failure caught by a wall
    clock rather than by an assertion."""

    def setUp(self):
        self.claims = D.scheduling_claims(_shipped())
        self.assertTrue(self.claims, "the derived population is EMPTY — every assertion below "
                                     "would pass having graded nothing")

    def test_b5_population_is_derived(self):
        """The offenders are an ASSERTION ABOUT the result, never the input to it."""
        sites = {c.where for c in self.claims}
        self.assertIn(OFFENDER_CONSTANT, sites)
        for rel in CLASS_C_FILES:
            self.assertIn(rel, sites)

    def test_b5_the_published_references_that_survive_a_cut_are_all_found(self):
        """⭐ INDEPENDENT CORROBORATION, AND WHAT THE 0.0.19 CUT DID TO IT.

        The cascade triage counted **nine** `0.0.19` references across the two tagged files, making
        five commitments — a number reached by hand, before this code existed, and the derivation
        found exactly nine. **Five of them lived in `RELEASE_NOTES.md`, which is rewritten WHOLESALE
        at every cut**, so the 0.0.19 cut took them with it: `4 + 5 = 9`, and the four that remain
        are the `CHANGELOG.md` half. A changelog entry is history and is never rewritten — which is
        exactly why renumbering one is the silent move this row forbids, and why those four are the
        half a count can still be pinned to.

        ⚠ A PIN WHOSE NUMBER IS A PROPERTY OF THE TREE IT GRADES DIES AT THE NEXT CUT, and this one
        did — reporting `4 != 9` against a correct tree. Both halves are asserted now, because
        "five went away" and "four are missing" are different facts and a single total cannot tell
        them apart.
        """
        changelog = [c for c in self.claims if c.where == "CHANGELOG.md" and c.target == "0.0.19"]
        notes = [c for c in self.claims if c.where == "RELEASE_NOTES.md" and c.target == "0.0.19"]
        self.assertEqual(len(changelog), 4,
                         "\n".join(c.render() for c in sorted(changelog, key=lambda c: c.site)))
        self.assertEqual(len(notes), 0,
                         "RELEASE_NOTES.md is the notes for the version being cut; a v0.0.18 "
                         "commitment surviving in it means the cut did not rewrite them:\n"
                         + "\n".join(c.render() for c in notes))
        self.assertEqual(len(changelog) + 5, 9, "the hand count was nine, and five were the notes")

    def test_b5_every_claim_pattern_is_exercised(self):
        """The vocabulary is DECLARED, so it is graded: a phrasing nothing probes is a phrasing
        nobody can tell is broken."""
        probes = {
            "scheduled-for": "the fix is scheduled for 0.0.20",
            "re-homed-to": "the work is re-homed to 0.0.20",
            "slated-for": "the work is slated for 0.0.20",
            "planned-for": "the work is planned for 0.0.20",
            "deferred-to": "the work is deferred to 0.0.20",
            "displaced-to": "the work is displaced to 0.0.20",
            "re-scheduled-to": "the work is re-scheduled to 0.0.20",
            "ruled-to": "the enforcement is ruled to 0.0.20",
            "owed-in": "restore full verification in 0.0.20",
        }
        self.assertEqual(sorted(probes), sorted(name for name, _ in D.CLAIM_PATTERNS),
                         "a declared pattern has no probe, or a probe has no pattern")
        for name, probe in probes.items():
            hits = D.scheduling_claims({"CHANGELOG.md": probe})
            self.assertTrue(hits, "%s matched nothing: %r" % (name, probe))
            self.assertIn(name, {h.pattern for h in hits})

    def test_b5_prose_about_a_promise_is_not_a_promise(self):
        """The trap stage 05 reported: a grep of 110 that was really 105, five inside fixtures. A
        docstring and a comment are prose ABOUT the mechanism, and they are not claims."""
        module = ('"""A docstring saying the fix is scheduled for 0.0.20."""\n'
                  '# A comment saying the fix is scheduled for 0.0.20\n'
                  'LIVE = "the fix is scheduled for 0.0.20"\n')
        claims = D.scheduling_claims({"src/mokata/x.py": module})
        self.assertEqual(len(claims), 1, [c.render() for c in claims])
        self.assertEqual(claims[0].line, 3)

    def test_b5_a_wrapped_claim_is_still_a_claim(self):
        """⚠ MEASURED UNDER-REPORT. The published floor commitment wraps: the phrasing ends one
        line and the release begins the next. Two of the nine live references are only visible this
        way, so a line-by-line reader reports a population short by exactly the ones an editor
        happened to wrap."""
        # The release is taken from the declaration rather than typed twice — see the note in
        # `test_b5_held_and_disclosed_are_different_states`. It also makes the fixture the SAME
        # commitment the accounting describes, instead of a lookalike.
        promised = D.PUBLISHED_COMMITMENTS[0].promised_for
        wrapped = ("Enforcement — warn before 2026-11-12, refuse after — lands in\n"
                   "  **%s**, and\n" % promised)
        claims = D.scheduling_claims({"CHANGELOG.md": wrapped})
        self.assertEqual(len(claims), 1, [c.render() for c in claims])
        self.assertEqual(claims[0].target, promised)
        self.assertEqual(claims[0].line, 2, "a wrapped claim must be reported where the RELEASE is")

    def test_b5_the_window_does_not_double_count(self):
        """⛔ The over-report that arrived fixing the under-report: every single-line claim counted
        twice, once from its own line and once from the window beginning above it."""
        text = "filler line with no promise\nthe fix is scheduled for 0.0.20\nmore filler\n"
        self.assertEqual(len(D.scheduling_claims({"CHANGELOG.md": text})), 1)

    def test_b5_past_tense_is_history_not_a_commitment(self):
        self.assertEqual(D.scheduling_claims({"CHANGELOG.md": "this was fixed in 0.0.12"}), ())
        self.assertEqual(D.scheduling_claims({"CHANGELOG.md": "restored in 0.0.18"}), ())

    def test_b5_a_spent_release_is_reported_not_pruned(self):
        """A filtered claim is invisible, and an invisible population is how a count comes to stand
        in for an inventory. SPENT is a reported state."""
        report = D.check_disclosure_resolves(
            {"CHANGELOG.md": "the fix is scheduled for 0.0.17"}, None, cutting="0.0.18",
            accounted=())
        self.assertEqual([v.state for v in report.verdicts], [D.SPENT])
        self.assertFalse(report.failed)
        self.assertIn("SPENT", report.render())

    def test_b5_version_comparison_is_numeric_not_lexical(self):
        self.assertLess(D.version_tuple("0.0.9"), D.version_tuple("0.0.19"))
        self.assertEqual(
            D.resolve(_claim("scheduled for 0.0.19", target="0.0.19"), None, cutting="0.0.9").state,
            D.UNRESOLVED, "0.0.19 was treated as spent at 0.0.9 by string comparison")

    def test_b5_an_unreadable_version_makes_nothing_spent(self):
        """Fail-closed: a gate that could not read the version being cut must not decide that every
        promise is already history."""
        report = D.check_disclosure_resolves(
            {"CHANGELOG.md": "the fix is scheduled for 0.0.11"}, None, cutting=None, accounted=())
        self.assertNotIn(D.SPENT, {v.state for v in report.verdicts})

    def test_b5_the_declaration_module_is_not_its_own_subject(self):
        """`_deprecation_removal._DECLARATION_MODULE`'s bargain: the module that DECLARES the
        accounting cannot be graded by it, or its own fragments become claims about themselves."""
        self.assertEqual(D.DECLARATION_MODULE, "src/mokata/disclosure.py")
        self.assertNotIn(D.DECLARATION_MODULE, {c.where for c in self.claims})

    def test_b5_an_unparseable_module_is_reported_not_counted_clean(self):
        bad = {"src/mokata/x.py": "def f(:\n"}
        self.assertEqual(D.scheduling_claims(bad), ())
        self.assertEqual(len(D.unparseable(bad)), 1)
        self.assertTrue(D.check_disclosure_resolves(bad, None, accounted=()).failed)

    def test_b5_the_two_readers_of_printable_strings_agree(self):
        """doc 85 §7f — two answers to "which strings in this module can reach a user" would each
        cover for the other. `_deprecation_removal` owns the older one; this pins them together
        over the whole shipped corpus rather than refactoring inside an unrelated stage."""
        import _deprecation_removal as R
        for rel, text in sorted(_shipped().items()):
            if not rel.endswith(".py") or text is None:
                continue
            with self.subTest(rel):
                self.assertEqual(sorted(D.printable_constants(text)),
                                 sorted(R._string_constants(text)))


# ---- the boundary ---------------------------------------------------------------------------------

class TestNothingShippedReadsThePlans(unittest.TestCase):
    """⛔ No shipped test or shipped checker reads the planning tree. Filed FOUR times.

    The grader takes its corpus as a parameter and names no internal path at all, which is what lets
    it ship. This class asserts the property over the files rather than trusting the design."""

    INTERNAL = ("docs/build", "docs/launch", "docs/marketing")

    def test_b5_the_grader_names_no_internal_path(self):
        text = _read("src/mokata/disclosure.py")
        for token in self.INTERNAL:
            self.assertNotIn(token, text)

    def test_b5_no_credential_shaped_value_reaches_the_shipped_leg_output(self):
        """Secret-safety, the half that runs everywhere: the shipped leg reads the tagged notes and
        every shipped module. No credential, DSN or host value may reach its output."""
        assert_no_secret(self, D.check_disclosure_resolves(_shipped(), None,
                                                           cutting="0.0.18").render())


@unittest.skipUnless(
    os.path.exists(RELEASE_SH),
    "release.sh is dev-only, excluded from the public mirror",
)
class TestTheReleaseScriptWiring(unittest.TestCase):
    """The dev-side wiring. ⚠ GUARDED, and stage 05's mirror harness is what said so: the first
    draft read `release.sh` from an UNGUARDED class, which is `SHIPPED-TEST-READS-INTERNAL-FILE`
    arriving in the stage whose whole subject is that boundary. It errored on the simulated mirror
    rather than skipping, which is the harness working exactly as it was built to."""

    def test_b5_release_sh_runs_the_resolving_gate(self):
        text = _read("scripts/release.sh")
        self.assertIn("verify_disclosure_resolves", text)
        self.assertIn("--disclosure-only", text)
        self.assertIn("verify_disclosure_resolves\n", text,
                      "the function is defined but never CALLED")

    def test_b5_release_sh_does_not_treat_exit_2_as_a_pass(self):
        text = _read("scripts/release.sh")
        self.assertIn("NOT CHECKABLE THERE", text)
        self.assertIn("This is NOT a pass", text)


@unittest.skipUnless(
    os.path.exists(SYNC_SH),
    "sync-public.sh is dev-only — the mirror does not carry the script that made it",
)
class TestTheMirrorControls(unittest.TestCase):
    """The exclusion set, asserted rather than assumed. Guarded for the same reason as the class
    above, and the irony is the point: the control that keeps internal files out of the mirror is
    itself an internal file."""

    def test_b5_the_resolving_gate_is_the_one_that_never_ships(self):
        """The gate that DOES read the plans is registered in BOTH mirror controls — the `--exclude`
        list and the hard guard — because `.gitignore` does not govern what rsync copies."""
        sync = _read("scripts/sync-public.sh")
        self.assertIn("--exclude='scripts/check-tracker-tables.py'", sync)
        self.assertIn("scripts/check-tracker-tables.py", sync.split("INTERNAL_PATHS=(")[1])

    def test_b5_the_mirror_exclusion_set_is_unchanged(self):
        """Asserted, not assumed: this stage adds no internal file, so it must add no exclusion.
        A new entry here would need the CLAUDE.md ships-list too, and that list was short by two
        for months (`CLAUDE-MD-SHIPS-LIST-INCOMPLETE`)."""
        sync = _read("scripts/sync-public.sh")
        expected = {
            "docs/build/", "docs/launch/", "docs/marketing/", "docs/talks/", "CLAUDE.md",
            "scripts/sync-public.sh", "scripts/release.sh", "scripts/check-tracker-tables.py",
            "push-to-github.command",
        }
        found = set(re.findall(r"--exclude='([^']+)'", sync))
        self.assertTrue(expected <= found,
                        "an internal-path exclusion disappeared: %r" % (sorted(expected - found),))
        self.assertNotIn("src/mokata/disclosure.py", found)
        self.assertNotIn("tests/test_b5_disclosure_must_resolve.py", found)


@unittest.skipUnless(
    os.path.exists(DOCS_BUILD),
    "the planning documents are dev-only, excluded from the public mirror twice over",
)
class TestSecretSafetyAcrossTheBoundary(unittest.TestCase):
    """The other half of secret-safety, and the half that matters most: the RESOLVING leg reads the
    internal plans, so it is the one that could carry an internal value into a rendered verdict."""

    def test_b5_no_credential_shaped_value_crosses_from_the_plans(self):
        assert_no_secret(self, D.check_disclosure_resolves(_shipped(), _plans(),
                                                           cutting="0.0.18").render())


if __name__ == "__main__":
    unittest.main()
