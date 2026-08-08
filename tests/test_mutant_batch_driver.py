"""Self-tests for `tests/_run_mutants.sh` — the ONLY production consumer of `scripts/mutate.sh`.

WHY THIS FILE EXISTS. 0.0.17 stage 18 gave `mutate.sh` two new hard failures — **exit 4**
(it refused to restore its snapshot over a third party's edit) and **exit 5** (another run
holds the mutation lock). The driver that runs the 26-mutant batch was `set -uo pipefail`
with **no `-e`**, and it invoked the mutator 26 times without ever inspecting a status. Both
new failures were therefore swallowed whole.

That is not merely a missed message. On an **exit 4 the target is DELIBERATELY LEFT MUTATED** —
refusing to restore is the entire point of the guard — so the batch carried on and **every
later mutant was graded against a tree holding an uncontrolled edit**. That is precisely the
compound-mutant contamination `mutate.sh`'s hole 3 exists to prevent, arriving one layer up
where hole 3 cannot see it. A guard whose only consumer ignores its signal is a declared no-op.

THE DISTINCTION THE WHOLE FILE TURNS ON, and it is the one worth keeping in your head:

  a VERDICT (RED / GREEN) is a completed run. The mutation was caught, or it survived.
  a HARNESS FAILURE (1-6) means NO VERDICT WAS PRODUCED and the tree may not be trustworthy.

Those must not share an exit code, or no caller can tell them apart. When this file was
written they DID share one — see `test_mutation_harness.py`'s `TestTheExitContract`, which
pins the mutator side of the same contract. The contract itself is documented in
`scripts/mutate.sh`'s header and that header is the single source; this driver consumes it.

HOW THESE TESTS RUN 26 MUTANTS IN MILLISECONDS. `_run_mutants.sh` takes its mutator from
`MUTATE_SH`, so the batch is driven against a STUB that returns a chosen exit code and logs
every label it was handed. That log is what makes "the batch actually stopped" an assertion
about behaviour rather than about a message: `test_..._aborts_...` checks that the mutants
after the failure were NEVER INVOKED, not merely that something was printed.

The real 26 call sites are exercised — this drives the shipped driver, not a copy of its
logic — which is what makes `test_the_declared_total_matches_the_real_number_of_call_sites`
worth having: a batch that reports "26 of 26" while running 25 is the silent-truncation shape
doc 85 warns about, and it is the one defect these tests could otherwise not see.
"""

import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_MUTANTS = os.path.join(ROOT, "tests", "_run_mutants.sh")
MUTATE_SH = os.path.join(ROOT, "scripts", "mutate.sh")

# The stub mutator. It is deliberately dumb: log the label, then either succeed with a verdict
# line shaped exactly like the real one, or fail with the chosen code and the real message's
# leading token. Shaped like the real output because the driver reads it — a stub that printed
# something else would let a driver that mis-parses real verdicts pass these tests.
STUB = '''\
#!@PYTHON@
import os
import sys

label = sys.argv[1]
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as fh:
    fh.write(label + "\\n")

fail_at = os.environ.get("STUB_FAIL_AT", "")
green_at = os.environ.get("STUB_GREEN_AT", "")
mute_at = os.environ.get("STUB_MUTE_AT", "")

if fail_at and label.startswith(fail_at):
    rc = int(os.environ["STUB_FAIL_RC"])
    if rc == 4:
        print("BROKEN!!  " + label + "   (Ran 3 tests)  <-- TARGET CHANGED UNDER THE RUN")
        print("REFUSED-RESTORE!!  " + label, file=sys.stderr)
    elif rc == 5:
        print("LOCK-BUSY!!  " + label, file=sys.stderr)
    elif rc == 6:
        print("BROKEN!!  " + label + "   (Ran 0 tests)  <-- PATTERN MATCHED NO TESTS")
    else:
        print("BROKEN!!  " + label + "   <-- MUTATION COULD NOT BE APPLIED")
    sys.exit(rc)

if mute_at and label.startswith(mute_at):
    # Exit 0 carrying something that is NOT a verdict. Nothing should be able to do this; the
    # driver must not assume the contract holds just because it is written down.
    print("something that is not a verdict at all")
    sys.exit(0)

if green_at and label.startswith(green_at):
    print("GREEN \\u2717  " + label + "   (Ran 3 tests in 0.1s OK )  <-- SURVIVOR")
    sys.exit(0)

print("RED   \\u2713  " + label + "   (Ran 3 tests in 0.1s FAILED (failures=1) )")
'''

# Every mutant in the batch is `$M "<label>" ...` on a line of its own, and the label opens with
# its ordinal. Both facts are asserted below rather than assumed, because the accounting the
# abort message reports ("19 of 26 never ran") is only as honest as this count.
CALL_SITE = re.compile(r'^mutant "', re.M)
TOTAL_DECL = re.compile(r'^TOTAL=(\d+)\s*$', re.M)


def _read(path):
    return pathlib.Path(path).read_text(encoding="utf-8")


class _DriverFixture(unittest.TestCase):
    """Drives the REAL `_run_mutants.sh` against a stub mutator."""

    def setUp(self):
        # Skipped per-test, never at class level: a `setUpClass` skip collapses the whole class
        # to ONE skip and quietly shrinks `Ran N` (see test_suite_count_integrity.py).
        if sys.platform.startswith("win"):
            self.skipTest("POSIX shell harness; the Windows legs do not run mutation passes")
        if shutil.which("bash") is None:
            self.skipTest("no bash on PATH to run the batch driver with")
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.log = os.path.join(self.tmp, "invocations.log")
        self.stub = os.path.join(self.tmp, "stub-mutate.sh")
        pathlib.Path(self.stub).write_text(STUB.replace("@PYTHON@", sys.executable),
                                           encoding="utf-8")
        os.chmod(self.stub, 0o755)

    def _drive(self, **stub_env):
        env = dict(os.environ, MUTATE_SH=self.stub, STUB_LOG=self.log, PYTHON=sys.executable)
        env.update({k: str(v) for k, v in stub_env.items()})
        proc = subprocess.run(["bash", RUN_MUTANTS], capture_output=True, text=True,
                              env=env, cwd=ROOT)
        return proc, proc.stdout + proc.stderr

    def _invoked(self):
        if not os.path.exists(self.log):
            return []
        return [line for line in _read(self.log).splitlines() if line.strip()]

    def _total(self):
        found = TOTAL_DECL.search(_read(RUN_MUTANTS))
        self.assertIsNotNone(found, "the driver declares no TOTAL, so it cannot report how many "
                                    "mutants never ran when it aborts")
        return int(found.group(1))


class TestAHarnessFailureAbortsTheBatch(_DriverFixture):
    """Exit 4 and exit 5 are the two stage 18 added, and both were being swallowed."""

    def _assert_aborted_at(self, proc, out, ordinal, rc):
        total = self._total()
        invoked = self._invoked()
        self.assertEqual(ordinal, len(invoked),
                         f"the batch did not STOP. mutate.sh returned {rc} on mutant {ordinal} "
                         f"and {len(invoked) - ordinal} further mutants were invoked anyway — "
                         f"each of them graded against a tree this run could not vouch for. "
                         f"Invoked: {invoked!r}")
        self.assertEqual(rc, proc.returncode,
                         f"the batch did not exit with the mutator's own status. Non-zero alone "
                         f"is not enough: 4 (the tree is still mutated) and 5 (nothing was "
                         f"touched) want completely different reactions from whoever reads it, "
                         f"and collapsing them loses that. Got {proc.returncode}: {out!r}")
        self.assertNotEqual(0, proc.returncode,
                            f"the batch reported SUCCESS after a harness failure: {out!r}")
        self.assertIn("BATCH ABORTED", out,
                      f"nothing in the output says the batch stopped early: {out!r}")
        self.assertNotIn("BATCH COMPLETE", out,
                         "a batch that stopped early printed a completion line — the exact "
                         "silent-truncation shape doc 85 warns about, where a truncated run "
                         f"reads as a run that covered everything: {out!r}")
        self.assertIn(invoked[-1], out,
                      f"the abort did not name the mutant it died on, so there is nothing to go "
                      f"and look at: {out!r}")
        self.assertIn(f"{total - ordinal} of {total}", out,
                      f"the abort did not report how many of the {total} mutants never ran. "
                      f"A batch that stops at {ordinal} and does not say so is indistinguishable "
                      f"from one that covered the list: {out!r}")

    def test_a_refused_restore_aborts_the_batch_and_says_the_tree_is_still_mutated(self):
        # Exit 4 is the dangerous one. `mutate.sh` refuses to restore precisely so a third
        # party's edit survives, which means it LEAVES THE TARGET MUTATED on purpose. Carrying
        # on past that grades every later mutant against an uncontrolled edit.
        proc, out = self._drive(STUB_FAIL_AT="M07", STUB_FAIL_RC=4)
        self._assert_aborted_at(proc, out, 7, 4)
        self.assertIn("REFUSED-RESTORE", out,
                      f"the abort did not name the artefact sitting in the tree: {out!r}")
        self.assertIn("still mutated", out.lower(),
                      f"the abort did not warn that the working tree is still carrying the "
                      f"mutation — the reader will assume the trap cleaned up: {out!r}")

    def test_a_busy_lock_aborts_the_batch(self):
        # Exit 5 leaves nothing behind, but a batch that skips mutants because another run held
        # the lock has still not graded what it claims to have graded.
        proc, out = self._drive(STUB_FAIL_AT="M03", STUB_FAIL_RC=5)
        self._assert_aborted_at(proc, out, 3, 5)
        self.assertIn("lock", out.lower(), f"the abort did not say what went wrong: {out!r}")

    def test_a_mutant_that_produced_no_verdict_aborts_the_batch(self):
        # Exit 6 — the test selection produced no result. Not a survivor, not a kill: nothing.
        proc, out = self._drive(STUB_FAIL_AT="M20", STUB_FAIL_RC=6)
        self._assert_aborted_at(proc, out, 20, 6)

    def test_a_mutation_that_could_not_be_applied_aborts_the_batch(self):
        # Exit 3 predates stage 18 and was being swallowed just as thoroughly.
        proc, out = self._drive(STUB_FAIL_AT="M01", STUB_FAIL_RC=3)
        self._assert_aborted_at(proc, out, 1, 3)

    def test_an_exit_zero_that_carries_no_verdict_is_not_taken_on_trust(self):
        # The driver reads the contract off the exit code, but a status of 0 is a CLAIM that a
        # verdict was produced. If the mutator ever drifts — a new branch that prints something
        # else and falls off the end at 0 — the batch must notice rather than count a line it
        # did not understand as a graded mutant. This is the shape every hole in `mutate.sh`'s
        # own header had: a non-result laundered into a result.
        proc, out = self._drive(STUB_MUTE_AT="M11")
        self.assertNotEqual(0, proc.returncode,
                            f"a mutant that exited 0 without a RED or GREEN verdict was counted "
                            f"as graded: {out!r}")
        self.assertNotIn("BATCH COMPLETE", out, out)


class TestACleanBatchIsUnchanged(_DriverFixture):
    """The regression that matters most: none of this may turn a working batch into a crash."""

    def test_every_mutant_runs_and_the_batch_exits_zero(self):
        proc, out = self._drive()
        total = self._total()
        self.assertEqual(total, len(self._invoked()),
                         f"a clean batch did not run all {total} mutants: {self._invoked()!r}")
        self.assertEqual(0, proc.returncode, f"a clean batch reported failure: {out!r}")
        self.assertIn("BATCH COMPLETE", out, f"a clean batch printed no completion line: {out!r}")
        self.assertIn(f"{total}/{total}", out,
                      f"the completion line does not account for every mutant: {out!r}")

    def test_every_verdict_line_still_reaches_the_reader(self):
        # The driver captures the mutator's stdout in order to count verdicts. If that capture
        # ever stops being re-emitted, the batch log goes blank and the whole exercise is
        # pointless — you would have 26 grades and no way to read any of them.
        proc, out = self._drive()
        for label in self._invoked():
            self.assertIn(label, out, f"verdict line for {label!r} never reached stdout: {out!r}")


class TestAGenuineSurvivorIsAFindingNotAFailure(_DriverFixture):
    """The most likely regression, and the worse defect of the two if it lands.

    Confusing "the mutation survived" with "the harness broke" would turn a real finding —
    a pin that does not bite — into a crash that hides it. A survivor is a completed verdict:
    the batch must carry on, grade the rest, and hand the human a survivor to go and look at.
    """

    def test_a_survivor_does_not_abort_the_batch(self):
        proc, out = self._drive(STUB_GREEN_AT="M09")
        total = self._total()
        self.assertEqual(total, len(self._invoked()),
                         f"a GREEN survivor stopped the batch. It is a VERDICT, not a harness "
                         f"failure — the remaining mutants are still worth grading: "
                         f"{self._invoked()!r}")
        self.assertNotIn("BATCH ABORTED", out,
                         f"a survivor was reported as a harness failure: {out!r}")
        self.assertEqual(0, proc.returncode,
                         f"a survivor made the batch exit non-zero: {out!r}")

    def test_the_survivor_is_named_in_the_summary(self):
        # A survivor buried 20 lines up in a 26-mutant log is a survivor nobody reads.
        proc, out = self._drive(STUB_GREEN_AT="M09")
        self.assertIn("SURVIVOR", out, f"the survivor vocabulary is gone entirely: {out!r}")
        tail = out[out.index("BATCH COMPLETE"):]
        self.assertIn("M09", tail,
                      f"the completion summary does not name the surviving mutant, so a clean "
                      f"batch and a batch with a live survivor read the same at the bottom: "
                      f"{tail!r}")


class TestTheAccountingCannotDrift(_DriverFixture):
    """`19 of 26 never ran` is only true if 26 is true."""

    def test_the_declared_total_matches_the_real_number_of_call_sites(self):
        src = _read(RUN_MUTANTS)
        sites = len(CALL_SITE.findall(src))
        self.assertEqual(self._total(), sites,
                         f"the driver declares TOTAL={self._total()} but has {sites} mutant call "
                         f"sites. Every 'N of M never ran' line it prints is wrong by the "
                         f"difference, and a batch that reports more coverage than it has is the "
                         f"defect this whole stage is about.")

    def test_the_total_matches_the_mutants_actually_invoked(self):
        # The static count above and the dynamic one can only disagree if a call site is inside
        # a branch that did not run — which is exactly how a batch silently shrinks.
        self._drive()
        self.assertEqual(self._total(), len(self._invoked()),
                         f"the driver declares TOTAL={self._total()} but invoked "
                         f"{len(self._invoked())} mutants")


class TestTheDriverConsumesTheDocumentedContract(unittest.TestCase):
    """The caller must be written against the contract, not against folklore."""

    def setUp(self):
        if sys.platform.startswith("win"):
            self.skipTest("POSIX shell harness; the Windows legs do not run mutation passes")

    def test_every_exit_code_the_driver_handles_is_one_mutate_sh_documents(self):
        # The driver names each code in its abort message. If it explains a code `mutate.sh`
        # cannot produce, the message is fiction; the reverse gap is caught by
        # `test_mutation_harness.TestTheExitContract`.
        header = _read(MUTATE_SH)
        contract = header[header.index("EXIT CONTRACT"):]
        handled = set(re.findall(r'^\s*(\d)\)\s', _read(RUN_MUTANTS), re.M))
        self.assertTrue(handled, "the driver dispatches on no exit code at all")
        for code in sorted(handled):
            # re.M matters — see the twin pin in test_mutation_harness.TestTheExitContract.
            self.assertRegex(contract, re.compile(rf'^\s*#\s*{code}\s', re.M),
                             f"the driver explains exit {code}, which mutate.sh's EXIT CONTRACT "
                             f"does not document")


if __name__ == "__main__":
    unittest.main()
