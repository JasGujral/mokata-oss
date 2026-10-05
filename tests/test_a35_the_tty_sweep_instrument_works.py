"""0.0.21 stage 06 — does the TTY-PROMPT SWEEP INSTRUMENT actually work? Planted subjects only.

`SUITE-HANGS-AT-A-TTY-ON-THREE-TESTS`, the MECHANISM half. The whole-tree reading is
`test_a36_no_test_reaches_a_prompt_on_the_ambient_stdin`; the cost split and why it was forced is
documented in `test_a34_a_suite_that_cannot_hang_or_silently_widen`.

⭐ EVERYTHING HERE IS GRADED ON A PLANTED SUBJECT, which is the only way a sweep's own correctness
can be checked: a sweep over the real tree finds nothing (the tree is clean) and so says nothing
about whether the sweep works. §7i, applied to an instrument rather than to a guard.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

import _graded_invocation as GI

_TESTS = os.path.abspath(os.path.dirname(__file__))
_REPO = os.path.dirname(_TESTS)
SWEEP = os.path.join(_TESTS, "_tty_prompt_sweep.py")
SWEEP_HOOK_DIR = os.path.join(_TESTS, "_tty_sweep")

#: A test module can reach a consent prompt exactly when it mentions one of these. The entry points
#: are `prompt.read_yes_no`, `cli_commands/_common._cli_ask` and `docsync.read_yes_no_prompt`; a
#: module that installs its own stdin double (`stdin_is`/`NoTtyStdin`) is in scope too, because
#: those are the CORRECT interactive tests and the sweep must keep proving it does not flag them.
#:
#: ⚠ A STATIC SET CANNOT BE EXACT, because the sweep's own decision is dynamic — it watches the
#: IDENTITY of the stdin object at the moment `input()` is called. What an under-wide set gives up
#: is stated in `test_a36`, and the weekly whole-suite leg is the complete pass.
#: ⚠ NARROWED FROM THE FIRST VERSION ON MEASURED COST (48 s → 17 s; see `test_a36`'s header). It
#: was `read_yes_no|_cli_ask|confirm_|stdin_is|NoTtyStdin|input\(` — 32 modules — and
#: `test_b1_internal_tests_meet_their_subject` runs the whole suite twice, so that cost ~144 s per
#: full run. These three are the PROMPT ENTRY POINTS: `prompt.read_yes_no`,
#: `cli_commands/_common._cli_ask`, `docsync.read_yes_no_prompt`. A module that installs a stdin
#: double but reaches no entry point cannot reach `input()` on the ambient stream, and the
#: "a correct interactive test is not flagged" property is graded on a PLANTED subject below rather
#: than by sweeping the real ones.
_PROMPT_REACHING = re.compile(r"read_yes_no|_cli_ask|read_yes_no_prompt")


#: 🔴 THIS MODULE IS EXCLUDED FROM ITS OWN CORPUS, AND IT IS NOT A CONVENIENCE — IT IS A HANG.
#:
#: The derivation above matches `input(` and `stdin_is`, and this file contains both (in the
#: planted fixtures and in the rule itself). So the first run put THIS module in the swept set,
#: the sweep subprocess ran it, and it launched a sweep of its own, which ran it again. Measured:
#: the test did not fail, it **never returned** — killed at 170 s having produced no verdict.
#:
#: ⭐ **A GUARD THAT INCLUDES ITSELF IN ITS OWN CORPUS DOES NOT JUST GRADE NOTHING — IT CANNOT
#: FINISH**, and the symptom is a hang, which is the exact symptom of the defect this stage is
#: about. The same self-exclusion bargain as `disclosure.DECLARATION_MODULE` and
#: `_deprecation_removal._DECLARATION_MODULE`, reached the expensive way.
_SELF = "test_a34_a_suite_that_cannot_hang_or_silently_widen"

#: ⚠ AND THE EXCLUSION IS DERIVED, NOT A NAME, BECAUSE NAMING ONE MODULE WAS WRONG WITHIN THE HOUR.
#: The first version excluded `_SELF` only — and then `test_a35`, which holds the slow reading, was
#: swept too: its own docstring says the words `input()` and so matches the rule. The sweep swept
#: it, it ran a sweep, and the run was killed at 150 s.
#:
#: ⭐ **ANY module that RUNS the sweep must be out of the swept set, and "runs the sweep" is
#: readable: it calls `run_sweep(`.** A derived exclusion cannot be short by one the way a typed
#: name can — which is the same argument this repo makes about every other derived population, now
#: applied to the one that recurses.
_RUNS_THE_SWEEP = "run_sweep("


def _prompt_reaching_modules():
    """The module names the sweep must cover, DERIVED from the tree."""
    found = []
    # CORPUS: THE WORKING TREE — the swept set must cover modules that are not committed yet;
    # a new interactive test is exactly what this instrument exists to catch.
    for name in sorted(os.listdir(_TESTS)):
        if not (name.startswith("test_") and name.endswith(".py")):
            continue
        with io.open(os.path.join(_TESTS, name), encoding="utf-8") as probe:
            if _RUNS_THE_SWEEP in probe.read():
                continue        # it runs the sweep; sweeping it makes the sweep sweep itself
        with io.open(os.path.join(_TESTS, name), encoding="utf-8") as fh:
            if _PROMPT_REACHING.search(fh.read()):
                found.append(name[:-3])
    return found


def run_sweep(modules, extra_tree=None):
    """Run the sweep over `modules` and return (proc, journal records)."""
    env = dict(os.environ)
    env["TTYSWEEP"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(
        [SWEEP_HOOK_DIR] + ([extra_tree] if extra_tree else [])
        + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    with tempfile.TemporaryDirectory() as tmp:
        journal = os.path.join(tmp, "ttysweep.jsonl")
        env["TTYSWEEP_OUT"] = journal
        # ⭐ THROUGH `_graded_invocation`, WHICH IS THE OTHER ROW. This is a harness that
        # COMPUTES a test list; an empty derivation here would invoke bare `unittest`, fall
        # back to discovery, run the whole suite under a forced TTY and report a clean journal.
        # That is `UNITTEST-BARE-INVOCATION-GRADES-EVERYTHING` arriving inside the guard for
        # the other row — so the refusal is not decoration, it is load-bearing right here.
        argv = GI.unittest_argv(sys.executable, modules,
                                "tty-sweep prompt-reaching module derivation")
        # ⛔ A BOUNDED TIMEOUT, AND IT IS THE POINT RATHER THAN HYGIENE. This is the guard
        # for a row about a suite that HANGS, so its own failure mode must not be a hang.
        # Measured: the subset takes ~48 s, so 150 s is headroom — and it is what turns a
        # recursion (the self-exclusion removed; see `_SELF`) from a stalled run into a
        # `TimeoutExpired`, i.e. a RED with a name on it.
        try:
            proc = subprocess.run(argv, cwd=_TESTS, env=env, capture_output=True, text=True,
                                  stdin=subprocess.DEVNULL, timeout=150)
        except subprocess.TimeoutExpired as expired:
            raise AssertionError(
                "the tty sweep did not finish within 150s over %d module(s). The measured "
                "cost is ~48s, so this is a HANG rather than a slow box — and a guard for a "
                "row about hanging suites must fail with a name rather than stall. The usual "
                "cause is the swept set including THIS module, which makes the sweep sweep "
                "itself (see `_SELF`).\n--- tail ---\n%s"
                % (len(modules), (expired.output or b"")[-1500:])) from expired
        records = []
        if os.path.exists(journal):
            with io.open(journal, encoding="utf-8") as fh:
                records = [json.loads(ln) for ln in fh if ln.strip()]
    return proc, records


# ================================================================ row 1: the suite cannot hang
# ⛔ NO `skipUnless` GUARD, AND THE ABSENCE IS DELIBERATE RATHER THAN AN OVERSIGHT. The first
# version guarded on `_tty_prompt_sweep.py` and `_tty_sweep/` existing — and **both SHIP**: they
# live under `tests/` and no control in `sync-public.sh` excludes them. So the guard could never
# fire, and `test_b1_internal_tests_meet_their_subject` said so three ways at once: the class read
# as UNDECIDABLE in the dev census, the mirror's skip count disagreed with the derived one by 6,
# and the guarded class named no internal subject. ⭐ A guard over a file that ships is not a
# weaker guard, it is a dead one (§7f/§7d) — so it is deleted, and the class is simply shippable.
class NoTestReachesAPromptOnTheAmbientStdin(unittest.TestCase):
    """`SUITE-HANGS-AT-A-TTY-ON-THREE-TESTS`, the CLASS half.

    ⛔ WHY CI CANNOT CATCH THIS BY RUNNING THE SUITE: CI is never a TTY, so it takes the SAME
    fail-closed arm the defective tests take, and reports green. Every consent gate in mokata is
    fail-closed off a TTY — which is correct at runtime and a trap in a suite, because **the
    machine decides which arm a test grades.** At a real terminal the same test reaches `input()`,
    and if it also redirected stdout the prompt is swallowed and the run **hangs with nothing on
    screen** until a human types into the void. That happened on 2026-08-26: a stray `yes` typed
    into a blank screen was consumed, mokata approved, and the failure read like a regression.

    ⭐ THE ONE DISTINCTION THAT MAKES THE READING WORTH ANYTHING: a test that installs its own stdin
    double and lets the real reader read it owns its answer and can never block a human. The sweep
    decides by the IDENTITY of the stream object, not by a guess, so those stay green."""

    @classmethod
    def setUpClass(cls):
        cls.modules = _prompt_reaching_modules()

    def test_the_derived_set_EXCLUDES_this_module_or_the_sweep_sweeps_itself(self):
        """🔴 MEASURED AS A HANG, not reasoned about. This file matches its own rule (`input(`,
        `stdin_is`), so the first run swept it — and the swept copy launched a sweep of its own,
        which swept it again. The test did not fail; it **never returned**, killed at 170 s having
        produced no verdict.

        ⭐ **A guard that includes itself in its own corpus cannot finish**, and the symptom is a
        hang — the exact symptom of the defect this stage exists for."""
        self.assertNotIn(_SELF, self.modules)
        with io.open(__file__, encoding="utf-8") as fh:
            self.assertTrue(_PROMPT_REACHING.search(fh.read()),
                            "THE PREMISE: this module DOES match its own rule, so the exclusion "
                            "is load-bearing rather than tidy. If this ever stops being true the "
                            "exclusion is dead code and should go.")
        # ⭐ AND NAMING ONE MODULE WAS NOT ENOUGH — measured. `test_a35` holds the slow reading and
        # its own docstring says `input()`, so the first, name-based exclusion let it into the
        # swept set and the run was killed at 150 s. EVERY module that runs the sweep is excluded,
        # derived from the tree, so the exclusion cannot be short by one.
        runners = []
        # CORPUS: THE WORKING TREE — same reason as the derivation above.
        for name in sorted(os.listdir(_TESTS)):
            if not (name.startswith("test_") and name.endswith(".py")):
                continue
            with io.open(os.path.join(_TESTS, name), encoding="utf-8") as fh:
                if _RUNS_THE_SWEEP in fh.read():
                    runners.append(name[:-3])
        self.assertGreaterEqual(len(runners), 2,
                                "the premise: more than one module runs the sweep, which is why "
                                "a typed name was the wrong shape")
        for runner in runners:
            self.assertNotIn(runner, self.modules,
                             f"{runner} runs the sweep and is IN the swept set — that recursion "
                             f"is a hang, not a failure")

    def test_the_derived_module_set_is_NON_EMPTY(self):
        """§7i and the second row at once: an empty set would make every assertion below vacuous,
        AND would be the exact input that turns this harness into a whole-suite run."""
        self.assertGreaterEqual(len(self.modules), 10,
                                "the prompt-reaching derivation collapsed: %r" % (self.modules,))

    def test_the_INSTRUMENT_FIRES_before_its_clean_reading_is_trusted(self):
        """⛔ THE ANTI-VACUITY STEP, AND IT COMES BEFORE THE CLEAN READING. The failure family is
        the whole point: `TTYSWEEP=1` unset, a renamed `sitecustomize.py`, a `PYTHONPATH` that
        does not reach it — **every one of those produces a clean journal and a green test**, which
        is indistinguishable from a clean tree. So the instrument is proved to fire on a synthetic
        offender first."""
        with tempfile.TemporaryDirectory() as planted:
            with io.open(os.path.join(planted, "test_planted_offender.py"), "w",
                         encoding="utf-8") as fh:
                fh.write(
                    "import unittest\n\n\n"
                    "class PlantedOffender(unittest.TestCase):\n"
                    "    def test_reaches_a_prompt_on_the_ambient_stream(self):\n"
                    "        input('this is the offender')\n")
            proc, records = run_sweep(["test_planted_offender"], extra_tree=planted)
        self.assertTrue(records,
                        "⛔ THE INSTRUMENT DID NOT FIRE on a test that calls input() with the "
                        "ambient stream installed. Every 'clean' reading from it is therefore "
                        "worthless — a disarmed sweep and a clean tree are the same green.\n"
                        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}")
        self.assertIn("test_reaches_a_prompt_on_the_ambient_stream", records[0]["test"])

    def test_a_CORRECT_interactive_test_is_NOT_flagged(self):
        """⭐ THE DISTINCTION, graded on a planted CORRECT test rather than assumed. The first form
        of this instrument flagged all ten correct interactive tests as offenders, which would have
        made the reading useless. An offender reaches a prompt with the AMBIENT stream installed;
        a test that installs its own double does not."""
        with tempfile.TemporaryDirectory() as planted:
            with io.open(os.path.join(planted, "test_planted_correct.py"), "w",
                         encoding="utf-8") as fh:
                fh.write(
                    "import io, sys, unittest\n\n\n"
                    "class PlantedCorrect(unittest.TestCase):\n"
                    "    def test_installs_its_own_stdin_and_lets_the_reader_read_it(self):\n"
                    "        real, sys.stdin = sys.stdin, io.StringIO('no\\n')\n"
                    "        try:\n"
                    "            self.assertEqual('no', input('ask'))\n"
                    "        finally:\n"
                    "            sys.stdin = real\n")
            proc, records = run_sweep(["test_planted_correct"], extra_tree=planted)
        self.assertEqual([], records,
                         "a test that owns its stdin was flagged — the reading is then useless, "
                         f"because the correct pattern is the common one:\n{records}")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)

    # ⚠ THE WHOLE-SUBSET READING LIVES IN `test_a35_...`, AND THE SPLIT IS A COST DECISION.
    # It takes ~48 s (a second run of 32 modules), and every mutant in
    # `_stage06_no_hang_no_widen_mutants.sh` pays the cost of whatever module it points at — at
    # 11 mutants that is nine minutes, which also blows `test_a9`'s per-driver budget. So the
    # MECHANISM is graded here, on planted modules, in about a second: the instrument fires, a
    # correct interactive test is not flagged, the derivation is non-empty, and this module
    # excludes itself. The MEASUREMENT over the real tree is `test_a35`, which no mutant points at
    # because there is nothing in it to mutate — it is a reading, not a rule.
    def test_the_instrument_FOOTPRINT_is_known_and_the_journal_is_what_is_graded(self):
        """⚠ A LIMITATION, PINNED SO NOBODY RE-TIGHTENS THE ASSERTION ABOVE. The forced `isatty()`
        is inherited by SUBPROCESSES (it installs through `PYTHONPATH` + `TTYSWEEP`), and
        `hook_cli.py` correctly uses `sys.stdin.isatty()` to decide whether a JSON payload was
        piped. So any test that drives a hook child fails under the sweep **while being correct**.

        ⭐ The weekly workflow's run step already ends in `|| true` for exactly this reason. The
        JOURNAL is the measurement; the exit code is not."""
        wf = os.path.join(_REPO, ".github", "workflows", "tty-sweep.yml")
        if not os.path.exists(wf):
            self.skipTest("tty-sweep.yml is not in this tree")
        with io.open(wf, encoding="utf-8") as fh:
            body = fh.read()
        self.assertRegex(body, r"-m unittest discover[^\n]*\|\| true",
                         "the weekly leg must keep NOT grading the exit code — the forced TTY "
                         "changes correct product behaviour in subprocesses, so a red subset is "
                         "the instrument's footprint and the journal is the finding")
        with io.open(os.path.join(_REPO, "src", "mokata", "hook_cli.py"), encoding="utf-8") as fh:
            hook = fh.read()
        self.assertIn("sys.stdin.isatty()", hook,
                      "THE PREMISE of the footprint: the hook decides whether a payload was piped "
                      "by asking isatty(), which the instrument forces. If that ever stops being "
                      "true, this limitation is gone and the exit code becomes assertable.")

    def test_the_sweep_is_still_wired_to_its_WEEKLY_runner_too(self):
        """⚠ TWO RUNNERS, AND THEY ARE NOT REDUNDANT (§7f). This test covers the 32 prompt-reaching
        modules every suite run; the workflow covers ALL 7,616 tests weekly, which is the only
        thing that can catch an offender in a module whose text the static rule does not match.
        Different corpora, different cadence, both gradeable."""
        wf = os.path.join(_REPO, ".github", "workflows", "tty-sweep.yml")
        if not os.path.exists(wf):
            self.skipTest("tty-sweep.yml is not in this tree")
        with io.open(wf, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("TTYSWEEP", body)
        # ⚠ `discover -s . -t .` — the workflow runs from `tests/`, so its discovery root is `.`
        # rather than `tests`. My first version asserted the spelling used elsewhere in the repo
        # and was simply wrong about this file; the PROPERTY is "it discovers the whole tree", so
        # the assertion asks for that instead of for a string I expected.
        self.assertRegex(body, r"-m unittest discover -s \S+ -t \S+",
                         "the weekly leg must still DISCOVER the whole suite — that is the half "
                         "this in-suite guard cannot do, because the static module rule cannot "
                         "see an offender whose text it does not match")
        self.assertNotRegex(body, r"-m unittest discover[^\n]*-p ",
                            "and it must not narrow to a pattern: a weekly whole-suite pass is "
                            "the only thing that covers the modules the static rule misses")


if __name__ == "__main__":
    unittest.main()
