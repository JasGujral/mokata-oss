"""CI-JOBS-WITHOUT-A-TIMEOUT (doc 84, filed 2026-08-18) — 0.0.19 stage 09, row B4.

THE ROW. `ci.yml`'s `test` job carried `timeout-minutes: 44`, derived from the slowest leg that
had ever completed x 1.5. `hooks-execute` and `live-db` carried none, so a hang in either ran to
GitHub's default of 360 minutes — and round 2 of the Windows wedge demonstrated the real cost:
AN UNFINISHED JOB BLOCKS LOG DOWNLOAD FOR EVERY OTHER JOB IN THE RUN, so one wedge cost the
evidence from all twelve.

⚠ THE CENSUS WAS WIDER THAN THE ROW'S EVIDENCE. Doc 84 named two uncovered jobs, observed in one
workflow. Across all ten workflows the count at `864b245` was THIRTEEN of nineteen, including every
job in `release.yml` — the workflow where a wedge is most expensive, because a stuck `pypi` job
holds the logs of the build that produced the artifact it was publishing.

WHAT MAKES THIS A ROW RATHER THAN A CHORE, and it is the half this file exists for. Adding thirteen
ceilings closes today's gap and nothing else; the next workflow added re-opens it and nothing
notices. That is doc 85 §7i — the same shape that let `WINDOWS-UNIT-LEG-WEDGED` cost 54 minutes.
So the graded property is COVERAGE OVER A CORPUS, `tests/_timeout_sweep.py` takes that corpus as a
PARAMETER, and the load-bearing test below plants a NEW job with no ceiling and requires a red.

⛔ A test that only checked today's jobs have ceilings would pass forever and grade nothing new.
Exactly one test here reads the real tree; every other assertion is made against a planted corpus.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import ast
import os
import shutil
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401 - puts src/ on the path
import _timeout_sweep as SWEEP
import _workflow_pins as wp

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REAL_CORPUS = os.path.join(ROOT, ".github", "workflows")

# A minimal, VALID workflow with one job that carries a ceiling. Everything planted below is a
# variation on this, so a diff between two fixtures is the property under test and nothing else.
COVERED_JOB = """\
name: fixture
on: [push]
jobs:
  already-has-one:
    runs-on: ubuntu-latest
    timeout-minutes: 7
    steps:
      - run: echo ok
"""

# The offender. Same file, same job, MINUS the ceiling — this is the thing the row exists to catch.
NEW_JOB_WITH_NO_CEILING = """\
name: a-workflow-somebody-added-later
on: [push]
jobs:
  the-new-job:
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
"""

# Present, so a key-presence check reads it as coverage; at the platform default, so it cuts
# nothing. This is the one-line bypass a coverage guard has to refuse.
CEILING_AT_THE_PLATFORM_DEFAULT = """\
name: fixture
on: [push]
jobs:
  silenced:
    runs-on: ubuntu-latest
    timeout-minutes: 360
    steps:
      - run: echo ok
"""

# A reusable-workflow call. GitHub REJECTS `timeout-minutes` on a job of this shape, so demanding
# one would be a red nobody can clear.
REUSABLE_WORKFLOW_CALL = """\
name: fixture
on: [push]
jobs:
  calls-another-workflow:
    uses: ./.github/workflows/ci.yml
"""


def _corpus(tmpdir, **files):
    """Write a planted corpus and return its path. Files are named, never discovered."""
    for name, text in files.items():
        with open(os.path.join(tmpdir, name + ".yml"), "w", encoding="utf-8") as handle:
            handle.write(text)
    return tmpdir


class PlantedCorpus(unittest.TestCase):
    """The sweep graded on offenders it can actually be fed. §7i."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="b4-timeout-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    # ---- THE LOAD-BEARING TEST ---------------------------------------------------------------

    def test_a_new_job_arriving_with_no_ceiling_reds(self):
        """The half that makes this a row. Without it the next workflow re-opens the gap."""
        corpus = _corpus(self.tmp, healthy=COVERED_JOB, newcomer=NEW_JOB_WITH_NO_CEILING)
        offenders = SWEEP.without_a_ceiling(corpus)
        self.assertEqual(
            [job.where for job in offenders], ["newcomer.yml:jobs.the-new-job"],
            "a job added with no `timeout-minutes` must be caught by name; anything else and the "
            "next workflow added runs to GitHub's 360-minute default and takes the run's logs "
            "with it. Sweep saw: %r" % (SWEEP.jobs(corpus),))
        self.assertEqual(offenders[0].disposition, SWEEP.UNCOVERED)

    def test_the_planted_corpus_is_actually_reached(self):
        """The fixture's own reachability, asserted rather than assumed.

        If the sweep's file discovery stopped finding the planted files, the test above would
        report "no offenders" and read as a pass — a guard that grades nothing while looking
        green, which is this release's entire subject.
        """
        corpus = _corpus(self.tmp, healthy=COVERED_JOB, newcomer=NEW_JOB_WITH_NO_CEILING)
        self.assertEqual(SWEEP.workflow_files(corpus), ("healthy.yml", "newcomer.yml"))
        self.assertEqual(len(SWEEP.jobs(corpus)), 2)

    # ---- THE NEGATIVE: a working guard must not be a noisy one -------------------------------

    def test_a_job_that_already_has_a_ceiling_is_not_flagged(self):
        corpus = _corpus(self.tmp, healthy=COVERED_JOB)
        self.assertEqual(SWEEP.without_a_ceiling(corpus), ())
        only = SWEEP.jobs(corpus)[0]
        self.assertEqual((only.disposition, only.ceiling), (SWEEP.COVERED, 7))

    def test_a_job_that_already_has_a_ceiling_is_left_byte_identical(self):
        """The change this stage makes is ADDITIVE. A file already carrying a ceiling is read,
        never rewritten — the sweep opens files and nothing else."""
        corpus = _corpus(self.tmp, healthy=COVERED_JOB)
        path = os.path.join(corpus, "healthy.yml")
        with open(path, "rb") as handle:
            before = handle.read()
        SWEEP.without_a_ceiling(corpus)
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), before)

    # ---- THE FOUR DISPOSITIONS (§7g) ----------------------------------------------------------

    def test_a_ceiling_at_the_platform_default_is_not_coverage(self):
        """`timeout-minutes: 360` is the one-line way to silence a key-presence check while the
        wedge stays. It is present and it cuts nothing, which is why INEFFECTIVE exists."""
        corpus = _corpus(self.tmp, silenced=CEILING_AT_THE_PLATFORM_DEFAULT)
        offenders = SWEEP.without_a_ceiling(corpus)
        self.assertEqual([job.where for job in offenders], ["silenced.yml:jobs.silenced"])
        self.assertEqual(offenders[0].disposition, SWEEP.INEFFECTIVE)

    def test_a_reusable_workflow_call_is_not_asked_for_a_ceiling(self):
        """It cannot carry one — GitHub rejects the key on such a job. Reporting it as UNCOVERED
        would be a red nobody can clear, which is how a guard gets deleted."""
        corpus = _corpus(self.tmp, reused=REUSABLE_WORKFLOW_CALL)
        self.assertEqual(SWEEP.without_a_ceiling(corpus), ())
        self.assertEqual(SWEEP.jobs(corpus)[0].disposition, SWEEP.NOT_APPLICABLE)

    # ---- ABSENT ANSWERS MUST NOT WEAR A PASS --------------------------------------------------

    def test_an_empty_corpus_refuses_rather_than_reading_as_clean(self):
        """"Every job carries a ceiling" is vacuously true of no jobs. Returning () here is how a
        guard survives having its own corpus made unreachable."""
        with self.assertRaises(SWEEP.EmptyCorpus):
            SWEEP.without_a_ceiling(_corpus(self.tmp))

    def test_the_sweep_refuses_without_a_parser_rather_than_skipping(self):
        corpus = _corpus(self.tmp, newcomer=NEW_JOB_WITH_NO_CEILING)
        with mock.patch("builtins.__import__", _import_without_yaml):
            with self.assertRaises(wp.MissingParser):
                SWEEP.without_a_ceiling(corpus)


class TheRealTree(unittest.TestCase):
    """The ONLY test here that reads `.github/workflows/`. It grades today; the class above
    grades tomorrow."""

    def test_every_job_in_every_workflow_carries_a_ceiling(self):
        offenders = SWEEP.without_a_ceiling(REAL_CORPUS)
        self.assertEqual(
            [job.where for job in offenders], [],
            "a job that can consume the clock and carries nothing that cuts it runs to GitHub's "
            "360-minute default, and an unfinished job blocks log download for the whole run")

    def test_the_sweep_reads_every_workflow_file_in_the_tree(self):
        """Derived, not declared: the domain is whatever `.github/workflows/` holds. A sweep that
        read nine of ten would report a clean tree it had not finished reading."""
        # CORPUS: THE WORKING TREE — deliberately, and it is the only honest choice here. The
        # question is "did the sweep read everything `.github/workflows/` HOLDS", so the answer
        # has to come from the same disk the sweep read; asking the index instead would let a
        # workflow file that exists but is untracked go ungraded while this reported a match.
        on_disk = tuple(sorted(n for n in os.listdir(REAL_CORPUS)
                               if n.endswith(SWEEP.WORKFLOW_SUFFIXES)))
        self.assertEqual(SWEEP.workflow_files(REAL_CORPUS), on_disk)
        self.assertEqual(
            sorted({job.workflow for job in SWEEP.jobs(REAL_CORPUS)}), sorted(on_disk),
            "every workflow in the tree must contribute at least one job to the census")


class TheGuardItself(unittest.TestCase):

    def test_this_guard_cannot_skip_itself(self):
        """A guard against checks that vanish quietly must not be able to vanish quietly. Same
        rule `test_pyyaml_skip_cluster` holds itself to, asserted against this file's own source.

        It PARSES rather than greps, and that is not decoration: a substring scan for the skip
        idioms is satisfied by this very docstring naming them, which is
        PIN-SUBSTRING-COMMENT-HOLE arriving inside the guard written to honour it.
        """
        with open(os.path.abspath(__file__), encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for dec in node.decorator_list:
                    target = dec.func if isinstance(dec, ast.Call) else dec
                    label = getattr(target, "attr", getattr(target, "id", ""))
                    if label.startswith("skip"):
                        found.append("decorator %s on %s" % (label, node.name))
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "").startswith("skip"):
                found.append("call %s at line %d" % (node.func.attr, node.lineno))
        self.assertEqual(found, [],
                         "a skip here would let the coverage check disappear from a run while "
                         "the run still reported OK")


_REAL_IMPORT = __import__


def _import_without_yaml(name, *args, **kwargs):
    if name == "yaml" or name.startswith("yaml."):
        raise ImportError("No module named 'yaml'")
    return _REAL_IMPORT(name, *args, **kwargs)


if __name__ == "__main__":                                  # pragma: no cover
    unittest.main()
