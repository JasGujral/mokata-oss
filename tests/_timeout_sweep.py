"""CI-JOBS-WITHOUT-A-TIMEOUT — does every job carry a ceiling? A PURE FUNCTION over a SUPPLIED
CORPUS of workflow files.

THE DEFECT, stated from its own evidence (doc 84, 2026-08-18). `ci.yml`'s `test` job carries
`timeout-minutes: 44`; `hooks-execute` and `live-db` carried none, so a hang in either ran to
GitHub's default of 360 minutes. And the cost is not one job's clock: an UNFINISHED job blocks log
download for every other job in the same run, so one wedge costs the evidence from all twelve.

WHAT MAKES THIS A ROW RATHER THAN A CHORE. Adding the two missing ceilings closes today's gap and
nothing more; the next workflow added re-opens it and nothing notices. That is doc 85 §7i — a check
that has only ever seen a healthy tree is a comment. So the property is COVERAGE, graded over a
corpus that is a PARAMETER, and `tests/test_b4_ci_jobs_without_a_timeout.py` hands it a planted job
with no ceiling and requires it to red.

TWO DESIGN CONSTRAINTS, both inherited from `tests/_workflow_pins.py` rather than re-derived:

1. IT TAKES A CORPUS DIRECTORY, it does not go and find the repo. Once every real job carries a
   ceiling the tree holds no offender, so a sweep wired to `.github/workflows/` would run green
   forever while grading nothing.

2. IT PARSES, IT DOES NOT GREP. A `grep -c timeout-minutes` is satisfied by a comment ABOUT a
   ceiling, by a STEP-level ceiling standing in for a job-level one, and by a count that matches
   for the wrong reason. Both of the real workflows here contain prose about `timeout-minutes`
   inside the very jobs being graded (PIN-SUBSTRING-COMMENT-HOLE, doc 84).

FOUR DISPOSITIONS, NEVER TWO (doc 85 §7g). "This job has no ceiling" is not one fact:

  * COVERED       a job-level `timeout-minutes` below the platform default.
  * UNCOVERED     no job-level `timeout-minutes` at all — the row's subject.
  * INEFFECTIVE   a ceiling at or above the 360-minute default it exists to cut. Present, so a
                  key-presence check reads it as coverage; it cuts nothing, so it is not. Without
                  this value the guard has a one-line bypass — write `timeout-minutes: 360` and
                  the red goes away while the wedge stays.
  * NOT_APPLICABLE a job that is a REUSABLE-WORKFLOW CALL (`uses:` at job level). GitHub REJECTS
                  `timeout-minutes` on such a job, so demanding one would be a red nobody can
                  clear. There is no such job in this tree today; the value exists so the first
                  one added fails as "cannot carry a ceiling", not as "forgot a ceiling".

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os

from _workflow_pins import safe_load

#: GitHub's own default job timeout, in minutes. A ceiling at or above this cuts nothing that the
#: platform was not already going to cut, which is what INEFFECTIVE names.
PLATFORM_DEFAULT_MINUTES = 360

COVERED = "covered"
UNCOVERED = "uncovered"
INEFFECTIVE = "ineffective"
NOT_APPLICABLE = "not-applicable"

WORKFLOW_SUFFIXES = (".yml", ".yaml")


class EmptyCorpus(RuntimeError):
    """The corpus directory holds no workflow files, so the sweep CANNOT ANSWER.

    Raised rather than returning an empty result on purpose, and it is the same distinction
    `MissingParser` draws one file over. "Every job carries a ceiling" is vacuously true of no
    jobs, so a sweep that answered () here would report a PASS for a corpus it never read — which
    is precisely how a guard survives having its own fixture made unreachable.
    """


class Job:
    """One job, with enough provenance to name it in a failure message."""

    __slots__ = ("workflow", "job_id", "disposition", "ceiling")

    def __init__(self, workflow, job_id, disposition, ceiling):
        self.workflow = workflow            # e.g. "ci.yml"
        self.job_id = job_id                # the KEY under `jobs:`, not the display `name:`
        self.disposition = disposition
        self.ceiling = ceiling              # the raw `timeout-minutes` value, or None

    @property
    def where(self):
        return "%s:jobs.%s" % (self.workflow, self.job_id)

    def __repr__(self):
        if self.ceiling is None:
            return "%s %s" % (self.where, self.disposition)
        return "%s %s (%s)" % (self.where, self.disposition, self.ceiling)

    def __eq__(self, other):
        return isinstance(other, Job) and repr(self) == repr(other)

    def __hash__(self):
        return hash(repr(self))


def classify(job):
    """The disposition of ONE job mapping. Pure; the four values above and nothing else."""
    if not isinstance(job, dict):
        # A job that is not a mapping cannot carry a ceiling and is not a reusable call either.
        # It is malformed, and malformed is UNCOVERED: the wedge it would cause is the same one.
        return UNCOVERED, None
    if "uses" in job and "steps" not in job:
        return NOT_APPLICABLE, None
    if "timeout-minutes" not in job:
        return UNCOVERED, None
    ceiling = job["timeout-minutes"]
    try:
        minutes = float(ceiling)
    except (TypeError, ValueError):
        # An expression (`${{ ... }}`) or anything else unreadable. It is NOT read as coverage:
        # a ceiling this sweep cannot evaluate is a ceiling it cannot say cuts anything.
        return INEFFECTIVE, ceiling
    if minutes >= PLATFORM_DEFAULT_MINUTES or minutes <= 0:
        return INEFFECTIVE, ceiling
    return COVERED, ceiling


def workflow_files(corpus_dir):
    """Every workflow file in the corpus, sorted. The DERIVED axis — nothing here names a file."""
    names = [n for n in os.listdir(corpus_dir) if n.endswith(WORKFLOW_SUFFIXES)]
    if not names:
        raise EmptyCorpus(
            "no workflow files (%s) under %r — the timeout-coverage sweep read nothing, so it "
            "cannot answer whether every job carries a ceiling. An empty corpus is not a clean "
            "one." % ("/".join(WORKFLOW_SUFFIXES), corpus_dir)
        )
    return tuple(sorted(names))


def jobs(corpus_dir):
    """Every job in every workflow of a SUPPLIED corpus, in (workflow, job_id) order."""
    found = []
    for name in workflow_files(corpus_dir):
        with open(os.path.join(corpus_dir, name), encoding="utf-8") as handle:
            text = handle.read()
        doc = safe_load(text, what="sweep workflow jobs for a timeout ceiling")
        job_map = doc.get("jobs") if isinstance(doc, dict) else None
        if not isinstance(job_map, dict):
            continue
        for job_id in job_map:
            disposition, ceiling = classify(job_map[job_id])
            found.append(Job(name, job_id, disposition, ceiling))
    return tuple(found)


def without_a_ceiling(corpus_dir):
    """Every job that can consume the clock and carries nothing that cuts it.

    UNCOVERED and INEFFECTIVE together, because the failure they produce is the same one: a hang
    runs to the platform default and takes the run's logs with it.
    """
    return tuple(j for j in jobs(corpus_dir)
                 if j.disposition in (UNCOVERED, INEFFECTIVE))
