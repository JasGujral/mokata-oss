#!/usr/bin/env python3
"""CI-CEILING — a workflow job's ceiling, re-derived from what the public mirror's runner measured.

`INTERNAL-ONLY-TESTS-RUN-ON-NO-RUNNER` — the CEILING half (named in the 0.0.21 notes; the local-gate
half shipped at 0.0.22 as `scripts/internal_suite.py`). Built at 0.0.25 Lane C.

WHY THIS EXISTS
---------------
Every job ceiling in `.github/workflows/` is "slowest COMPLETED run x 1.5", derived by hand from
the GitHub jobs API on the day it was set — and then never again (`CI-CEILING-IS-DERIVED-ONCE-AND-
NEVER-RE-DERIVED`). The suite grows every release. At 0.0.23 it grew past `release.yml`'s 22-minute
`test` ceiling: run 38966278058 was CANCELLED with no test failing, so v0.0.23 was tagged and never
reached PyPI. The ceiling was re-derived by hand at 0.0.24 (1240.6 s x 1.5 -> 32). This makes the
re-derivation a STEP: the runner that just ran the suite measures itself and says, on the PR, when
a ceiling no longer clears it — a release cut earlier, while it is a one-line edit.

HOW A WORKFLOW USES IT
----------------------
    - name: Start the ceiling clock
      run: echo "MOKATA_CLOCK_T0=$(date +%s)" >> "$GITHUB_ENV"
    ... the steps the ceiling must cover ...
    - name: Ceiling still clears this job (CI-CEILING)
      run: python scripts/ci_ceiling.py --since "$MOKATA_CLOCK_T0" \\
             --workflow .github/workflows/release.yml --job test

`--workflow/--job` may name ANOTHER workflow's job: `ci.yml`'s ubuntu legs run a superset of
`release.yml`'s `test` job on the same runner image, so every PR re-derives the TAG gate's ceiling
before a tag can meet it.

THE RULE, TWO NUMBERS
---------------------
* `DERIVE_FACTOR` 1.5 — the tree's own derivation rule; the value this prints to set.
* `ALARM_FACTOR` 1.2 — when the ceiling is below 1.2 x the measured time the job is within 20% of
  being cancelled: exit 1. Alarming at 1.5 would red the moment a ceiling is set exactly by the
  rule (32 min over a 1240.6 s run is 1.55x); alarming at 1.0 is the cancellation itself.

Exit: 0 clears · 1 TOO TIGHT (prints the re-derived value) · 2 cannot answer (no such job, no
job-level ceiling, unreadable workflow, a clock that is not a past epoch). Three facts, three codes:
"cannot measure" is never reported as "clears" (doc 85 §7g).

Stdlib-only, no subprocess. Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
import time
from typing import List, Optional

DERIVE_FACTOR = 1.5
ALARM_FACTOR = 1.2

CLEARS, TOO_TIGHT, CANNOT_ANSWER = 0, 1, 2

_JOBS = re.compile(r"^jobs:\s*(#.*)?$")
_TOP_KEY = re.compile(r"^\S")


def job_ceiling(workflow_text: str, job: str) -> Optional[int]:
    """The JOB-LEVEL `timeout-minutes` of `job`, or None when the job or its ceiling is absent.

    A narrow line reader over the shape every workflow here is written in (two-space indent: jobs at
    column 2, job keys at column 4). A step-level ceiling (deeper indent) is NOT the job's, and a
    comment naming `timeout-minutes` is not a key — the two ways a grep reads the wrong number
    (PIN-SUBSTRING-COMMENT-HOLE). `tests/test_c_03_ci_ceiling.py` grades this reader against PyYAML
    for every job in the real tree."""
    lines = workflow_text.splitlines()
    in_jobs, in_job = False, False
    job_re = re.compile(r"^  " + re.escape(job) + r":\s*(#.*)?$")
    for line in lines:
        if _JOBS.match(line):
            in_jobs = True
            continue
        if in_jobs and _TOP_KEY.match(line):
            in_jobs = False
        if not in_jobs:
            continue
        if re.match(r"^  \S", line):
            in_job = bool(job_re.match(line))
            continue
        if in_job:
            m = re.match(r"^    timeout-minutes:\s*(\d+)\s*(#.*)?$", line)
            if m:
                return int(m.group(1))
    return None


def derive_minutes(seconds: float) -> int:
    """The ceiling the tree's rule asks for: measured x 1.5, rounded UP to a whole minute."""
    return max(1, math.ceil(seconds * DERIVE_FACTOR / 60.0))


def verdict(seconds: float, ceiling_minutes: int) -> int:
    """CLEARS while the ceiling is at least `ALARM_FACTOR` x the measured time, else TOO_TIGHT."""
    return CLEARS if ceiling_minutes * 60.0 >= seconds * ALARM_FACTOR else TOO_TIGHT


def record_line(where: str, seconds: float, ceiling: int, code: int) -> str:
    """The one line a run prints. Pure, so its shape is graded."""
    word = {CLEARS: "CLEARS", TOO_TIGHT: "TOO-TIGHT"}[code]
    return ("CI-CEILING %s measured=%ds ceiling=%dm headroom=%.2fx derived=%dm %s"
            % (where, round(seconds), ceiling, (ceiling * 60.0) / max(seconds, 1.0),
               derive_minutes(seconds), word))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="ci_ceiling", description=__doc__.split("\n")[0])
    ap.add_argument("--since", required=True, help="the clock's start, epoch seconds")
    ap.add_argument("--workflow", required=True, help="the workflow file whose ceiling is checked")
    ap.add_argument("--job", required=True, help="the job id inside that workflow")
    ap.add_argument("--now", default=None, help=argparse.SUPPRESS)   # tests pin the clock
    a = ap.parse_args(argv)

    try:
        t0 = float(a.since)
        now = float(a.now) if a.now is not None else time.time()
    except ValueError:
        print("CI-CEILING cannot answer: --since is not an epoch (%r) — the clock step did not "
              "run, so nothing was measured." % a.since, file=sys.stderr)
        return CANNOT_ANSWER
    seconds = now - t0
    if seconds <= 0:
        print("CI-CEILING cannot answer: the clock reads %ds — not a past start." % seconds,
              file=sys.stderr)
        return CANNOT_ANSWER
    try:
        with open(a.workflow, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        print("CI-CEILING cannot answer: %s unreadable (%s)." % (a.workflow, exc), file=sys.stderr)
        return CANNOT_ANSWER
    ceiling = job_ceiling(text, a.job)
    where = "%s:%s" % (os.path.basename(a.workflow), a.job)
    if ceiling is None:
        print("CI-CEILING cannot answer: %s has no job-level timeout-minutes." % where,
              file=sys.stderr)
        return CANNOT_ANSWER
    code = verdict(seconds, ceiling)
    print(record_line(where, seconds, ceiling, code))
    if code == TOO_TIGHT:
        print("::error title=CI ceiling too tight::%s took %ds against a %d-minute ceiling — under "
              "%.1fx headroom, so suite growth is about to CANCEL this job with no test failing "
              "(the v0.0.23 publish was lost this way). Re-derive it: set `timeout-minutes: %d` "
              "(measured x %.1f) and update the derivation comment beside it."
              % (where, round(seconds), ceiling, ALARM_FACTOR, derive_minutes(seconds),
                 DERIVE_FACTOR))
    return code


if __name__ == "__main__":
    sys.exit(main())
