"""0.0.21 stage 06 — `UNITTEST-BARE-INVOCATION-GRADES-EVERYTHING`. PURE, and that is the point.

⚠ THREE MODULES FOR ONE STAGE, SPLIT BY COST, AND THE SPLIT IS A FINDING RATHER THAN TIDINESS.
Every mutant pays whatever its target module costs, so a module that spawns subprocesses makes its
own mutants expensive — and one of them made the batch UNRUNNABLE:

  * **this file** — pure argv/text rules, no subprocess. Milliseconds. The `W`/`B` mutants point
    here.
  * `test_a35_the_tty_sweep_instrument_works.py` — the sweep MECHANISM, on planted modules. ~2 s.
    The `T` mutants point there.
  * `test_a36_no_test_reaches_a_prompt_on_the_ambient_stdin.py` — the whole-tree READING. ~48 s. No
    mutant points there, because there is nothing in it to mutate: it is a reading, not a rule.

⛔ WHAT FORCED THE SPLIT, MEASURED TWICE. With the sweep tests in this module, the mutant that
turns the argv builder into a `-p` glob made the planted sweeps discover the WHOLE tests tree under
a forced TTY — including the modules that RUN the sweep — so the mutant HUNG instead of producing a
verdict. And the mutant that removes the empty-list refusal did the same thing by a different
route. **A mutant whose effect is a hang grades nothing and costs the most.**

TWO ROWS, ONE SUBJECT: a suite whose verdict can be decided by something other than the code.

  * `SUITE-HANGS-AT-A-TTY-ON-THREE-TESTS` — **PARTIAL coming in.** The three instances were closed
    at 0.0.20 and verified by hand; the CLASS GUARD the row also asks for was not built, so the
    instrument that found them (`tests/_tty_prompt_sweep.py`) is **run by nothing that actually
    runs**: its only caller is a weekly opt-in workflow on a repo with no Actions minutes since
    2026-08-17 (`§5.5`, stage 08's blocker). ⭐ *A guard whose only runner is disabled is a guard
    nobody has.*
  * `UNITTEST-BARE-INVOCATION-GRADES-EVERYTHING` — `python -m unittest` with no names falls back to
    DISCOVERY, so a blinded derivation that resolved to an empty list ran all 7,357 tests and
    reported success. **It was caught only because the mutant took eleven minutes instead of
    thirty seconds.**

⭐ **THE TWO ROWS MEET IN ONE PLACE, which is why they are one stage.** The guard this file adds for
the first row IS a harness that computes a test list — exactly the shape the second row's hazard
needs — so it runs through `_graded_invocation`, and the second row gets a live consumer rather than
a helper nobody calls (§7i).

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
#: ⚠ DELIBERATELY OVER-WIDE RATHER THAN EXACT. The sweep's own decision is dynamic — it watches the
#: IDENTITY of the stdin object at the moment `input()` is called — so a static set cannot be
#: precise. An over-wide set costs seconds; an under-wide one is the gap. The cost is measured
#: below and stated in the stage report.
_PROMPT_REACHING = re.compile(r"read_yes_no|_cli_ask|confirm_|stdin_is|NoTtyStdin|input\(")


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
    # CORPUS: THE WORKING TREE — an UNCOMMITTED module that runs the sweep is exactly the one
        # whose recursion would hang the guard, so the index would be the wrong corpus here.
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


# ================================================================ row 2: it cannot silently widen
class AComputedTestListCannotBecomeTheWholeSuite(unittest.TestCase):
    """`UNITTEST-BARE-INVOCATION-GRADES-EVERYTHING`."""

    def test_an_empty_derivation_is_REFUSED_and_names_itself(self):
        with self.assertRaises(GI.EmptyGradingSet) as caught:
            GI.unittest_argv(sys.executable, [], "tty-sweep module derivation")
        message = str(caught.exception)
        self.assertIn("tty-sweep module derivation", message,
                      "the message must name WHICH derivation came back empty — 'the list was "
                      "empty' sends a reader to the runner instead of to the rule")
        self.assertIn("falls back to DISCOVERY", message)
        self.assertIn("7,357", message, "the measurement is part of the refusal, because the "
                                        "number is what makes it obviously not pedantry")

    def test_whitespace_only_names_are_not_a_list_either(self):
        with self.assertRaises(GI.EmptyGradingSet):
            GI.unittest_argv(sys.executable, ["", None], "a derivation of blanks")

    def test_the_CONTROL_a_real_list_builds_a_NAMED_argv(self):
        argv = GI.unittest_argv("py", ["test_a", "test_b"], "x")
        self.assertEqual(["py", "-m", "unittest", "test_a", "test_b"], argv)

    def test_it_builds_NAMES_and_never_a_glob(self):
        """⚠ A `-p` GLOB THAT MATCHES NOTHING IS A DIFFERENT FALSE GREEN: `unittest` prints
        `Ran 0 tests ... OK`. That is the shape `mutate.sh`'s exit-6 contract exists for. Explicit
        module names make a missing module an ERROR carrying the module's name."""
        argv = GI.unittest_argv("py", ["test_a"], "x")
        self.assertNotIn("-p", argv)
        self.assertNotIn("discover", argv)

    def test_the_refusal_is_an_AssertionError_so_a_forgetful_test_FAILS(self):
        """Not a bespoke exception hierarchy: a test that forgets to catch this should land in the
        failure bucket, where someone reads it, rather than in `errors` beside import problems."""
        self.assertTrue(issubclass(GI.EmptyGradingSet, AssertionError))

    def test_the_helper_HAS_a_live_consumer_in_this_tree(self):
        """§7i, and it is the whole reason these two rows are one stage. A refusal helper nobody
        calls grades nothing; the guard for the OTHER row is a harness that computes a test list,
        which is exactly the shape this hazard needs."""
        consumers = []
        # CORPUS: THE WORKING TREE — same reason as above.
        for name in sorted(os.listdir(_TESTS)):
            if not name.endswith(".py") or name == "_graded_invocation.py":
                continue
            with io.open(os.path.join(_TESTS, name), encoding="utf-8") as fh:
                body = fh.read()
            if "unittest_argv(" in body or "names_or_refuse(" in body:
                consumers.append(name)
        self.assertTrue(consumers,
                        "_graded_invocation has no caller, so the refusal it provides protects "
                        "nothing")


#: `python -m unittest` followed by nothing but end-of-line, a redirection or a pipe — i.e. the
#: bare form that falls back to discovery. A trailing `discover`, `-p`, `-k` or a module name is
#: the safe form and is not matched.
_BARE = re.compile(
    r"(?:python[\d.]*|\$PYTHON|\"\$py\"|\$\{?PYTHON\}?|sys\.executable)"
    r"[^\n|&;]{0,40}?-m\s+unittest\s*(?:$|[|&;><])", re.M)

#: ⚠ THE MATCH MUST BE IN A COMMAND POSITION, and the first version of this rule was not — it
#: convicted six sites on its first run, every one of them PROSE: a docstring in `_pyyaml_sweep.py`
#: describing the hazard, and this module's own fixture strings. **A sentence about a command is
#: not a command** (§7g, in the reader rather than the subject), and a detector that reds on the
#: documentation of the defect it hunts is one nobody can keep.
#:
#: A command starts a line, or follows `;`, `&`, `|`, `(` or a YAML `run:`. Prose has it after a
#: word, a backtick or a quote.
#:
#: ⛔ AND THIS REPLACED A SECOND DEFENCE RATHER THAN JOINING IT. The first version also excluded
#: THIS module from its own corpus, the way `disclosure.DECLARATION_MODULE` does. A mutant deleting
#: that exclusion ran GREEN — because the position rule already covers every one of this module's
#: fixture strings (each is preceded by a quote). Two defences that cannot be told apart are
#: untestable (§7f), so the weaker one is DELETED rather than documented: the position rule is the
#: single graded defence, and `test_the_POSITION_rule_rejects_PROSE_about_the_hazard` is what reds
#: if it goes.
_COMMAND_POSITION = ";&|(:"


def in_command_position(body, start):
    before = body[:start].rstrip(" \t")
    if not before or before.endswith("\n"):
        return True
    return before[-1] in _COMMAND_POSITION


def bare_invocations(corpus):
    """Every bare `python -m unittest` in a SUPPLIED corpus, as `((name, line, text), …)`.

    ⭐ A PURE FUNCTION OVER A SUPPLIED CORPUS, and it became one because a mutant survived. The
    first version walked the tree and judged it in one method, so **gutting the loop was GREEN** —
    a detector that convicts nothing and a tree with no offenders give the same answer, and the
    tree has no offenders (checked at the row's review: all 46 batch drivers pass an explicit
    `-p`). §7i: a guard that both discovers its corpus and judges it has no offender in it."""
    found = []
    for name in sorted(corpus):
        body = corpus[name]
        if body is None:
            continue
        lines = body.splitlines()
        for match in _BARE.finditer(body):
            if not in_command_position(body, match.start()):
                continue            # a sentence ABOUT a command is not a command
            line = body[:match.start()].count("\n") + 1
            text = lines[line - 1].strip() if line <= len(lines) else ""
            if text.startswith("#"):
                continue            # a shell comment is not a command either
            found.append((name, line, text[:110]))
    return tuple(found)


def _runner_corpus():
    """Where a harness can invoke the runner, read off the tree."""
    corpus = {}
    for rel in ("tests", "scripts", os.path.join(".github", "workflows")):
        root = os.path.join(_REPO, rel)
        if not os.path.isdir(root):
            continue
        # CORPUS: THE WORKING TREE — a harness added and not yet committed is precisely the one
            # that could carry a bare invocation, so this asks the tree rather than the index.
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames
                           if d not in ("__pycache__", ".mokata", "_tty_sweep")]
            for name in sorted(filenames):
                if not name.endswith((".py", ".sh", ".yml", ".yaml")):
                    continue
                path = os.path.join(dirpath, name)
                try:
                    with io.open(path, encoding="utf-8") as fh:
                        # ⚠ `_support.posix_rel` — ONE place converts a repo-relative NAME in
                        # this tree, and `test_repo_paths_invariant` convicted my hand-rolled
                        # `.replace(os.sep, "/")` the first time it ran. Two spellings of the same
                        # conversion is the drift that detector exists for.
                        corpus[_support.posix_rel(path, _REPO)] = fh.read()
                except (OSError, UnicodeDecodeError):
                    continue
    return corpus


class NoHarnessInvokesUnittestWithNoNamesAtAll(unittest.TestCase):
    """⭐ THE REACHABILITY HALF. The row says the hazard is *reachable and unguarded*, not that
    there are live offenders — checked at its review, where all 46 batch drivers were found to pass
    an explicit `-p`. This keeps that true, over the tree rather than over a memory of it."""

    def test_nothing_invokes_the_runner_with_no_names_and_no_pattern(self):
        offenders = bare_invocations(_runner_corpus())
        self.assertEqual(
            (), offenders,
            "a bare `python -m unittest` falls back to DISCOVERY, so these grade the WHOLE suite "
            "while looking like they grade a selection — and the only symptom is that the run "
            "takes eleven minutes instead of thirty seconds:\n%s"
            % "\n".join("  %s:%d  %s" % o for o in offenders))

    def test_the_corpus_is_REAL_and_not_an_empty_read(self):
        """§7i's other half: an empty corpus makes the assertion above vacuously true."""
        corpus = _runner_corpus()
        self.assertGreater(len(corpus), 100, "the runner corpus collapsed: %d files" % len(corpus))
        self.assertTrue(any(n.endswith(".sh") for n in corpus), "no shell drivers were read")

    def test_the_DETECTOR_CONVICTS_a_planted_offender(self):
        """🔴 ADDED AFTER A MUTANT SURVIVED, and the survivor is the whole detector. Gutting the
        loop was GREEN, because *convicts nothing* and *nothing to convict* give the same answer
        over a tree that is clean. The offender has to be SUPPLIED."""
        planted = {
            "scripts/planted.sh": "set -e" + chr(10) + "cd tests" + chr(10)
                                  + "python -m unittest" + chr(10),
            "tests/_planted_driver.sh": '"$py" -m unittest | tail -3' + chr(10),
            ".github/workflows/planted.yml": "      - run: python3 -m unittest" + chr(10),
        }
        offenders = bare_invocations(planted)
        self.assertEqual(3, len(offenders), offenders)
        self.assertEqual({"scripts/planted.sh", "tests/_planted_driver.sh",
                          ".github/workflows/planted.yml"}, {o[0] for o in offenders})

    def test_the_detector_does_NOT_convict_the_SAFE_forms(self):
        nl = chr(10)
        safe = {
            "a.sh": "python -m unittest discover -s tests -t tests" + nl,
            "b.sh": "python -m unittest test_a test_b" + nl,
            "c.sh": '"$py" -m unittest discover -s tests -t tests -p "test_x.py"' + nl,
            "d.sh": "python -m unittest -k something" + nl,
        }
        self.assertEqual((), bare_invocations(safe))

    def test_the_POSITION_rule_rejects_PROSE_about_the_hazard(self):
        """🔴 ADDED AFTER THE DETECTOR CONVICTED SIX SITES ON ITS FIRST RUN, every one of them
        prose: a docstring in `_pyyaml_sweep.py` describing this exact hazard, and this module's own
        fixture strings. ⛔ **A detector that reds on the documentation of the defect it hunts is
        one nobody can keep** — the first exception teaches the reader to ignore it, which is the
        same lesson stage 02 recorded about an over-broad `2>/dev/null` rule.

        ⭐ AND IT IS NOW THE SINGLE DEFENCE. A self-exclusion for this module was deleted when a
        mutant proved it redundant with this rule (§7f): every fixture string here is preceded by a
        quote, so the position rule already declines it."""
        nl = chr(10)
        prose = {
            "tests/_pyyaml_sweep.py":
                '"""Does this `python -m unittest <args>` load AND run every module?' + nl
                + '"""' + nl,
            "tests/_fixtures.py": "    bare = 'python3 -m unittest > /tmp/out'" + nl,
            "scripts/x.sh": "# a bare python -m unittest falls back to discovery" + nl,
        }
        self.assertEqual((), bare_invocations(prose), "prose convicted as a command")

    def test_the_REAL_tree_contains_prose_this_rule_must_decline(self):
        """⚠ THE PREMISE OF THE TEST ABOVE, asked of the tree rather than assumed. If nothing in
        the tree documents the hazard any more, the position rule is ungraded by the real corpus
        and the planted cases above are all that hold it."""
        corpus = _runner_corpus()
        documenting = [n for n, body in corpus.items()
                       if body and _BARE.search(body)
                       and not bare_invocations({n: body})]
        self.assertTrue(documenting,
                        "no file in the tree mentions a bare `python -m unittest` in prose, so "
                        "the position rule has no live subject — the planted cases are then its "
                        "only grading, which is weaker than it looks")


if __name__ == "__main__":
    unittest.main()
