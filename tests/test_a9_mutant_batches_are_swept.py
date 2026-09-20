"""EVERY MUTANT BATCH IN THE TREE IS RUN BY SOMETHING — and a batch that rots reds. (0.0.20 s09)

`MUTANT-BATCHES-RUN-BY-NOBODY` (doc 84).  Measured on this tree at 0.0.19: **45 batch drivers
declaring 817 mutants, referenced by zero workflows and zero scripts, and executed by exactly one
thing — `_run_mutants.sh`, by `test_mutant_batch_driver`, against a stub.**  All 45 were graded for
SHAPE by `_mutant_driver_contract.py` and none for RESULTS, so every *"9/9 killed"* in this
project's record rested on a batch nothing re-ran.

WHAT THIS FILE GRADES, AND WHAT IT DELIBERATELY DOES NOT
-------------------------------------------------------
It grades the half that actually ROTS.  A mutant's `old` string is a quotation of somebody else's
file; the day that file changes the quotation stops matching, `scripts/mutate.sh` exits 3, the
driver aborts, and **every mutant after it in the list is never graded again** — silently.  Four
mutants died exactly that way in one batch and it took five days to notice.  Deciding whether a
quotation still matches is a string count, so it costs seconds and belongs on every push.

⛔ **WHAT IT DOES NOT CATCH, STATED HERE RATHER THAN DISCOVERED LATER:** a mutant that still
APPLIES but is no longer KILLED — a real survivor from a weakened pin.  Nothing here applies a
mutation or runs a test, so nothing here is a mutation score.  That is the nightly job's work and
only the nightly job's.  A green run of this file means *the corpus is still addressed to this
tree*, never *the corpus still catches anything*.

⭐ HOW THIS FILE IS ITSELF GRADED (doc 85 §7i, and §7f one layer down).  A sweep is only as honest
as its stand-in mutator, and a stand-in that reported *"found"* for every string would green all 45
batches for nothing.  So `TheSweepDoesNotTakeTheStubsWordForIt` runs the whole sweep against a stub
that LIES ON PURPOSE and requires the sweep to notice.  Without that test, this file is the defect
it was written to close, wearing the other hat.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import unittest

import _support  # noqa: F401
import _internal_subject as isub
import _mutant_sweep as MS

from _retired_mutant_batches import RETIRED
from _mutant_sweep import (
    discover_drivers,
    grader_skip_census,
    globs_matching_no_test,
    patterns_matching_no_test,
    unverifiable_records,
    drivers_without_the_seam,
    reconcile,
    repo_root,
    run_sweep,
    verify_records,
)

ROOT = repo_root()

#: The sweep is one subprocess per driver and is the expensive part of this file, so it is run ONCE
#: for the whole module and every assertion below reads the same reading.  A per-test sweep would
#: multiply a ~90-second cost by the number of questions asked of it, which is how a PR gate stops
#: being run at all — and a gate nobody runs is this row's own subject.
_SWEEP = None


def setUpModule():
    global _SWEEP
    _SWEEP = run_sweep()


class TheCorpusIsDerivedAndIsNotEmpty(unittest.TestCase):
    """§7f: a sweep over nothing passes every assertion below it and describes nothing."""

    def test_the_driver_set_is_read_from_the_tree(self):
        drivers = discover_drivers()
        self.assertGreater(len(drivers), 30,
                           "the batch corpus collapsed to %d drivers — either the tree lost them "
                           "or the discovery stopped finding them; both are findings"
                           % len(drivers))

    def test_the_sweep_actually_swept_something(self):
        swept = sum(len(r.records) for r in _SWEEP)
        self.assertGreater(swept, 500,
                           "only %d mutants were swept — a sweep that reaches almost nothing "
                           "reports 'no stale patterns' for the same reason a broken one does"
                           % swept)

    def test_every_driver_can_be_swept_at_all(self):
        """The seam is one line and it is what makes a driver dry-runnable.

        ⚠ Detected by the seam's EXPANSION, never by the name appearing in the file: a substring
        test read `_stage18c_mutants.sh` as seamed because it exports an unrelated variable called
        `MUTATE_SH_UNDER_TEST`, and that driver was then quietly running the REAL mutator inside
        the sweep."""
        unseamed = [p.name for p in drivers_without_the_seam()]
        self.assertEqual([], unseamed,
                         "these drivers hard-code the mutator, so they can be neither swept nor "
                         "dry-run: %s" % unseamed)


class TheBatchesStillAddressThisTree(unittest.TestCase):
    """The gate itself."""

    def test_no_mutant_pattern_has_gone_stale(self):
        offenders = [(r.driver, o) for r in _SWEEP for o in r.offenders]
        detail = "\n".join(
            "  %-46s %-56s %3d occurrence(s) in %s"
            % (driver, o["label"][:56], o["occurrences"], o["target"])
            for driver, o in offenders)
        self.assertEqual(
            [], offenders,
            "%d mutant pattern(s) no longer occur exactly once in their target. In a real run each "
            "of these exits 3 and ABORTS ITS BATCH, so every mutant after it in the list is "
            "ungraded too:\n%s" % (len(offenders), detail))

    def test_no_mutant_mutates_nothing(self):
        """`old == new` applies cleanly, runs the suite, and grades exactly nothing — the shape a
        pattern check cannot see, because the pattern is present and unique."""
        noops = [(r.driver, o["label"]) for r in _SWEEP for o in r.no_op_mutations]
        self.assertEqual([], noops, "these mutants replace a string with itself: %s" % noops)

    def test_an_UNREADABLE_target_is_the_BOUNDARY_here_and_a_DELETION_in_a_dev_tree(self):
        """⛔ THE TWO FACTS THE SWEEP USED TO REPORT AS ONE, split and each given its own answer.

        A mutant whose target FILE cannot be read is not a stale pattern. On the public mirror the
        internal targets — `scripts/release.sh` and the rest — are absent BY DESIGN, and 35 of them
        were reported as *"no longer occur exactly once in their target"*: a sentence asking
        somebody to repair a driver that is not broken, on a tree where the file's absence is the
        boundary working. In a DEV checkout the same record means a driver aimed at a file that has
        been deleted, and that is a real fault this must still catch.

        Found 2026-09-13 by running the release's own public-subset preflight — the only instrument
        that can see this class, because the target's absence is a fact about the TREE and no
        static read of the driver can know which tree it will run in."""
        missing = [(r.driver, rec) for r in _SWEEP for rec in r.unreadable_targets]
        deleted = [(driver, rec) for driver, rec in missing
                   if isub.subject_state(ROOT, (rec["target"],)) != isub.ABSENT_BY_DESIGN]
        detail = "\n".join("  %-46s %-56s target %s missing: %s"
                            % (d, rec["label"][:56], rec["target"], rec["unreadable"])
                            for d, rec in deleted)
        self.assertEqual(
            [], deleted,
            "%d mutant(s) point at a target this tree should HAVE and does not — a driver aimed at "
            "a deleted file grades nothing and aborts its batch:\n%s" % (len(deleted), detail))

    def test_the_SUBJECT_ABSENT_exit_is_honest_about_THIS_tree(self):
        """⛔ ANTI-VACUITY FOR THE EXEMPTION ABOVE. `reconcile` no longer counts a driver that
        exited `SUBJECT_ABSENT_RC` as a gap — so that code must not be usable as a way to opt out.
        In a DEV checkout every internal target exists, so no driver may claim it; on the mirror a
        driver that claims it must really be missing its subject.

        Without this the exemption is an exit code any driver could print to go quiet."""
        claimed = [r.driver for r in _SWEEP if r.returncode == MS.SUBJECT_ABSENT_RC]
        if isub.tree_state(ROOT).kind == isub.TREE_DEV:
            self.assertEqual([], claimed,
                             "a driver claims its subject is absent in a DEV checkout, where every "
                             "internal target exists: %s" % claimed)
        else:
            for driver in claimed:
                with self.subTest(driver=driver):
                    self.assertIn("PUBLIC subset",
                                  (ROOT / "tests" / driver).read_text(encoding="utf-8"),
                                  "%s exits %d without saying which subject it is missing"
                                  % (driver, MS.SUBJECT_ABSENT_RC))

    def test_every_driver_reaches_the_end_of_its_own_list(self):
        """A driver's `TOTAL=` is its own claim about its size; the sweep counts what it produced.

        ⛔ The two numbers disagreeing is a DIFFERENT fact from a stale pattern and is not folded
        into it (§7g): a driver that refuses at its own green-baseline step is telling the truth
        about a red tree, while a driver that dies in its scaffolding is telling the truth about
        nothing. Either way its mutants were not swept, and 'not swept' must never be counted as
        'clean'."""
        # ⚠ A DRIVER WHOSE SUBJECT THIS TREE DOES NOT CARRY NEVER HAD A LIST TO REACH THE END OF.
        # On the public mirror `_tag_guard_mutants.sh` stops at 28 of 45 because `release.sh` is
        # excluded — "not swept" there is the boundary, not a gap. The test above owns the absent
        # targets; this one owns the drivers that stopped for any OTHER reason, so neither answer
        # covers for the other (§7g).
        subject_absent = {r.driver for r in _SWEEP
                          if r.returncode == MS.SUBJECT_ABSENT_RC} | {
            r.driver for r in _SWEEP
            if r.unreadable_targets and all(
                isub.subject_state(ROOT, (rec["target"],)) == isub.ABSENT_BY_DESIGN
                for rec in r.unreadable_targets)}
        gaps = [(name, declared, swept, rc)
                for name, declared, swept, rc in reconcile(_SWEEP)
                if declared is not None and declared != swept and name not in subject_absent]
        detail = "\n".join(
            "  %-46s declared=%-4s swept=%-4d rc=%s%s"
            % (n, d, s, rc, "   (rc 75 = the driver's own green-baseline step refused)"
               if rc == 75 else "")
            for n, d, s, rc in gaps)
        self.assertEqual([], gaps,
                         "%d driver(s) never reached the end of their own mutant list, so those "
                         "mutants are ungraded and no existing check compares the two "
                         "numbers:\n%s" % (len(gaps), detail))


    def test_every_mutants_graders_still_EXIST(self):
        """A mutant names the tests that are supposed to catch it. If that pattern matches no file,
        `unittest discover` prints `Ran 0 tests ... OK` and exits 0 — so the mutant comes back
        GREEN, i.e. *"nothing caught me"*, when the truth is *"nobody was asked"*. Those are two
        different facts and the second one sends a reader to hunt a weakened pin that never
        existed."""
        orphans = [(driver, rec["label"], rec["pattern"])
                   for driver, rec in patterns_matching_no_test(_SWEEP)]
        self.assertEqual([], orphans,
                         "%d mutant(s) name a test pattern that matches no file under tests/, so a "
                         "real run grades them against an empty suite and reports a SURVIVOR: %s"
                         % (len(orphans), orphans[:6]))


    def test_the_grader_skip_census_covers_every_pattern_the_sweep_SAW(self):
        """The census reports how many of each mutant's graders are asleep on this host — a
        survivor under a mostly-skipped pattern is a finding about the RUNNER, not about the pin.

        ⛔ IT IS DELIBERATELY NOT A GATE. Decorator skips are evaluated against THIS host, so a
        pinned count would red everywhere but the machine it was recorded on; and the honest gate
        needs whether the mutant SURVIVED, which lives in the driver. What IS host-independent, and
        is what this asserts, is that **no pattern goes missing from the census** — an instrument
        that silently stopped covering a pattern would report `0 asleep` for it forever."""
        seen = {rec["pattern"] for r in _SWEEP for rec in r.records if rec.get("pattern")}
        census = grader_skip_census(_SWEEP)
        self.assertEqual(set(), seen - set(census),
                         "these mutant test-patterns are absent from the skip census: %s"
                         % sorted(seen - set(census))[:6])
        for pattern, (skipped, total, mutants) in census.items():
            self.assertLessEqual(skipped, total, pattern)
            self.assertGreater(mutants, 0, pattern)

    def test_no_driver_names_a_test_GLOB_that_matches_nothing(self):
        """Wider than the check above, and it exists because that one missed a live instance.

        A driver's green-baseline step runs a glob the mutator never sees, so an argv-based check
        is blind to it — and `_stage12_migrate_slice_mutants.sh` was running a baseline over
        `test_stage17_vault_rehome_gated.py` long after that file was deleted, passing, because
        `unittest discover` over an empty match prints `Ran 0 tests ... OK`. ⛔ A green baseline
        over no tests is the same lie as a mutant graded by no tests, one level up."""
        dead = globs_matching_no_test()
        self.assertEqual([], dead,
                         "%d driver glob(s) match no test file, so whatever they gate is ungraded "
                         "and reports success: %s" % (len(dead), dead))


class TheRetirementsAreRecordedRatherThanInferred(unittest.TestCase):
    """§7g on the corpus itself: a batch that left on purpose and one that fell out of a merge are
    the same absence unless somebody wrote down which it was."""

    def test_every_retired_driver_is_actually_gone(self):
        present = {p.name for p in discover_drivers()}
        still_here = sorted(present & set(RETIRED))
        self.assertEqual([], still_here,
                         "these drivers are recorded as retired but are still in the tree, where "
                         "somebody will run them: %s" % still_here)

    def test_no_LIVE_driver_is_recorded_as_retired(self):
        """The other direction, which is the one that rots: a name that stays in the register after
        the file comes back turns the register into decoration."""
        for name in RETIRED:
            self.assertFalse(
                (ROOT / "tests" / name).exists(),
                "%s is recorded as retired and exists — the register is describing a tree that is "
                "no longer this one" % name)

    def test_a_retirement_caused_by_a_deleted_subject_reds_if_the_subject_COMES_BACK(self):
        """⭐ The clause that keeps the register live rather than historical."""
        returned = [(name, r.subject) for name, r in RETIRED.items()
                    if r.subject and (ROOT / r.subject).exists()]
        self.assertEqual([], returned,
                         "the subject of a retired batch is back in the tree, so the batch that "
                         "graded it should come back too — or the retirement needs re-reasoning: "
                         "%s" % returned)


class TheSweepDoesNotTakeTheStubsWordForIt(unittest.TestCase):
    """§7i — the sweep's own defence, and it is the reason this file may be believed."""

    def test_the_swept_patterns_are_counted_a_second_time_by_this_process(self):
        wrong = verify_records(_SWEEP)
        self.assertEqual([], wrong,
                         "the stand-in mutator's verdict disagrees with an independent count for "
                         "%d pattern(s): %s"
                         % (len(wrong), [(r["label"], r["occurrences"], c) for r, c in wrong[:5]]))

    def test_what_cannot_be_second_counted_is_NAMED_and_is_confined_to_a_declared_mechanism(self):
        """A record the sweep cannot re-read is the one hole a dishonest stub could hide in: claim
        an absolute `/tmp` target for everything and `verify_records` returns an immaculate empty
        list.  So the unverifiable set is not skipped quietly — it is required to come only from
        drivers that DECLARE they grade a copied subject (`SUBJECT_SRC`), which is a mechanism in
        the tree rather than a tolerance in this test."""
        offenders = set()
        for driver, _rec in unverifiable_records(_SWEEP):
            body = (ROOT / "tests" / driver).read_text(encoding="utf-8", errors="replace")
            if "SUBJECT_SRC" not in body:
                offenders.add(driver)
        self.assertEqual(set(), offenders,
                         "these drivers swept targets this process cannot re-read, and they do not "
                         "declare a copied subject: %s" % sorted(offenders))

    def test_a_LYING_stub_is_caught(self):
        """The control on everything above.

        A stub that claims every pattern applies satisfies `test_no_mutant_pattern_has_gone_stale`
        for free.  The sweep is run here against exactly that stub — over one small driver, because
        the point is the detection and not the coverage — and the second count must contradict it.
        If this test ever passes trivially, the whole file is decorative."""
        import tempfile
        liar = ('import json, os, sys\n'
                'log = os.environ.get("MUTANT_SWEEP_LOG")\n'
                'if log:\n'
                '    with open(log, "a", encoding="utf-8") as fh:\n'
                '        fh.write(json.dumps({"label": sys.argv[1], "target": sys.argv[2],\n'
                '                             "pattern": sys.argv[5], "occurrences": 1,\n'
                '                             "unreadable": "", "applies": True,\n'
                '                             "is_a_no_op_mutation": False,\n'
                '                             "old_text": "<<a string no file in this repo '
                'contains>>"}) + "\\n")\n'
                'print("RED   %s  [LYING STUB]" % sys.argv[1])\n')
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "lying_stub.py")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(liar)
            import pathlib
            victim = [p for p in discover_drivers() if p.name == "_sync_marker_drift_mutants.sh"]
            self.assertTrue(victim, "the driver this control is driven through has been renamed")
            results = run_sweep(drivers=victim, stub=pathlib.Path(path))
            self.assertTrue(any(r.records for r in results),
                            "the lying stub produced no records at all, so nothing was under test")
            self.assertTrue(
                verify_records(results),
                "a stub that reported `applies` for a string no file contains was believed — the "
                "second count is not actually independent")


class TheSeamIsShellSourceAndIsSpelledForAShell(unittest.TestCase):
    """⛔ THE 0.0.20 WINDOWS RED, AND THE REASON IT LOOKED LIKE NOTHING.

    `_stub_wrapper` writes a `/bin/sh` script that execs this interpreter. That file is SHELL
    SOURCE, and on Windows `sys.executable` is `C:\\hostedtoolcache\\...\\python.exe` — interpolated
    raw, every backslash is an escape, the shell reads `C:hostedtoolcachewindows...`, and the
    driver exits **127**. Every driver returns 127, the sweep records nothing, and the suite above
    reports *"0 mutants were swept"* — which is §7f arriving through a path separator: a corpus
    that reaches nothing says *no stale patterns* for the same reason a working one does.

    ⭐ DRIVEN ON POSIX, because the platform that breaks is the one nobody here runs. `as_posix`
    takes `sep` precisely so its Windows branch executes where somebody is looking (§7i), and the
    assertion is made with `shlex`, which MODELS the shell's own word splitting rather than
    grepping for a character — the question is not "is there a backslash" but "will the shell read
    this as ONE word".
    """

    WINDOWS_EXE = r"C:\Program Files\Python310\python.exe"
    WINDOWS_STUB = r"C:\a\mokata-oss\tests\_mutant_pattern_stub.py"

    def _seam_line(self, exe, stub, sep):
        return "exec '%s' '%s' \"$@\"" % (_support.as_posix(exe, sep=sep),
                                          _support.as_posix(stub, sep=sep))

    def test_a_windows_spelled_seam_is_ONE_word_per_path_to_a_shell(self):
        import shlex
        line = self._seam_line(self.WINDOWS_EXE, self.WINDOWS_STUB, "\\")
        words = shlex.split(line)
        self.assertEqual(
            ["exec", "C:/Program Files/Python310/python.exe",
             "C:/a/mokata-oss/tests/_mutant_pattern_stub.py", "$@"], words,
            "a shell does not read this seam as three words — the driver will exit 127 and the "
            "sweep will report an empty corpus as if nothing were stale:\n%s" % line)

    def test_the_UNQUOTED_RAW_form_is_what_it_used_to_be_and_is_still_broken(self):
        """ANTI-VACUITY. The assertion above is only evidence if the form it replaced FAILS it —
        otherwise it would pass against the very line that reded three Windows legs."""
        import shlex
        broken = 'exec %s %s "$@"' % (self.WINDOWS_EXE, self.WINDOWS_STUB)
        words = shlex.split(broken)
        self.assertNotIn("C:/Program Files/Python310/python.exe", words)
        self.assertIn("C:Program", words[1],
                      "the old form no longer mangles the path, so this suite has stopped "
                      "grading the defect it was written for: %r" % (words,))

    def test_the_real_seam_writer_produces_a_shell_safe_line_here_too(self):
        """And the function itself, on this host, with a path that carries a SPACE — the other
        half a bare `as_posix` would not have fixed."""
        import shlex
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            spaced = Path(d) / "a dir with spaces"
            spaced.mkdir()
            stub = spaced / "stub.py"
            stub.write_text("", encoding="utf-8")
            wrapper = MS._stub_wrapper(d, stub)
            with open(wrapper, encoding="utf-8") as fh:
                line = [ln for ln in fh.read().splitlines() if ln.startswith("exec ")][0]
            words = shlex.split(line)
            self.assertEqual(4, len(words), "seam is not exec + exe + stub + $@: %r" % (words,))
            self.assertEqual(str(stub), words[2].replace("/", os.sep) if os.sep != "/" else words[2],
                             "the stub path did not survive quoting: %r" % (words,))
