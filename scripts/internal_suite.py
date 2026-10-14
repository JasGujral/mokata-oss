#!/usr/bin/env python3
"""INTERNAL SUITE — the recorded local gate for the tests that grade the release tooling.

`INTERNAL-ONLY-TESTS-RUN-ON-NO-RUNNER` (doc 84), 0.0.21 stage 08 re-scoped, built at 0.0.22 B2.

WHY THIS EXISTS
---------------
About a hundred shipped tests grade `scripts/release.sh`, `scripts/sync-public.sh`, `CLAUDE.md`'s
ships-list and `docs/build/`. Those subjects are excluded from the public mirror by design, so on the
mirror's CI every one of those tests SKIPS — correctly — and the dev repo, the only checkout holding
the subjects, has no Actions minutes (standing ruling, 2026-10-04: "not fixing billing for now").
So nothing but a human remembering to run the suite on the cut machine has ever graded them.

This tool is doc 84's third option for that row: an explicitly-invoked local gate that the cut
checklist RUNS and records. ⚠ A local gate is not a runner, and this says so rather than presenting
it as one: what it buys is that SKIPPING the internal suite becomes VISIBLE — a recorded line with a
commit, a count and a zero — not that a second machine ever ran it.

WHAT IT DOES
------------
1. Refuses unless this checkout is the DEV tree (`_internal_subject.tree_state`): on the mirror the
   subjects are absent by design and there is nothing to grade; on an incoherent tree a skip would
   mean a lost file, so it refuses rather than excuse it.
2. Derives the guarded classes from the shipped test corpus — never a typed list.
3. Runs exactly those classes, in-process, and REFUSES (exit 1) on any failure, error, OR SKIP: in
   the dev tree every one of them must grade its subject.
4. Prints ONE record line for the cut checklist:
       INTERNAL-SUITE <iso-utc> <HEAD sha> classes=<k> tests=<n> skipped=0 failed=0 GRADED

Exit: 0 graded · 1 a test failed or SKIPPED · 2 not a dev tree / nothing derived (refused).
`scripts/release.sh` does not call it; the cut checklist does, and pastes the record line.
"""

from __future__ import annotations

import datetime
import os
import subprocess
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(REPO, "tests")


def _corpus():
    # CORPUS: THE WORKING TREE — the tests this checkout would run, tracked or not.
    out = {}
    for name in sorted(os.listdir(TESTS)):
        if name.startswith("test_") and name.endswith(".py"):
            with open(os.path.join(TESTS, name), encoding="utf-8") as fh:
                out[name] = fh.read()
    return out


def _head():
    p = subprocess.run(["git", "-C", REPO, "rev-parse", "HEAD"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    return p.stdout.strip() or "unknown"


def record_line(sha, classes, tests, skipped, failed, now=None):
    """The one line the cut checklist pastes. Pure, so its shape is graded."""
    when = (now or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    verdict = "GRADED" if not (skipped or failed) else "REFUSED"
    return ("INTERNAL-SUITE %s %s classes=%d tests=%d skipped=%d failed=%d %s"
            % (when, sha, classes, tests, skipped, failed, verdict))


def main(argv):
    sys.path.insert(0, TESTS)
    import _internal_subject as IS                                   # noqa: E402

    state = IS.tree_state(REPO)
    if state.kind != IS.TREE_DEV:
        print("INTERNAL-SUITE REFUSED — %s" % state.render(), file=sys.stderr)
        return 2
    classes = IS.guarded_classes(_corpus())
    if not classes:
        print("INTERNAL-SUITE REFUSED — the derivation found NO guarded class. A gate that grades "
              "nothing must not print a green line; fix the derivation.", file=sys.stderr)
        return 2
    os.chdir(TESTS)
    loader = unittest.TestLoader()
    suite = unittest.TestSuite(
        loader.loadTestsFromName("%s.%s" % (c.filename[:-3], c.classname)) for c in classes)
    result = unittest.TextTestRunner(verbosity=1, stream=sys.stderr).run(suite)
    skipped, failed = len(result.skipped), len(result.failures) + len(result.errors)
    for test, reason in result.skipped:
        print("  SKIPPED IN THE DEV TREE: %s — %s" % (test.id(), reason), file=sys.stderr)
    print(record_line(_head(), len(classes), result.testsRun, skipped, failed))
    return 0 if not (skipped or failed) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
