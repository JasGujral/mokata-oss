---
name: bug
description: mokata · Start from a reproducer and a failing test, then fix.
when_to_use: Engage when the user supplies a concrete reproducer or bug report and wants it fixed, when a known defect needs a regression test then a fix, or when turning a reported failure into a captured, guarded fix. Do NOT engage without a reproducer to start from, or to change unrelated behaviour beyond the reported bug.
---

# mokata · /bug

Start from a reproducer. Write a failing test that captures the bug, then fix to green and leave the test as a regression guard. Root-cause from the REAL code — read the failing path and trace it with the structural queries before fixing; don't guess at code you haven't read. Labels progress reported -> reproduced -> fixing -> verified; the fix is gated behind a reproducer. Record every test run you make: `mokata progress test-run --passed|--failed --command "<the command>" --test tests/test_x.py::TestX::test_y --ac <AC>=tests/test_x.py::TestX::test_y` — each test by its FULL id, the node id pytest prints with `-rA` (or pass that output with `--runner-report <file>`); a bare name never proves.

## Gate (check)
A bug fix requires a reproducer and a failing test before the fix.

## Standalone
This command runs on its own — no upstream pipeline phase is required. It applies only its own gate above, and never silently skips a gate of a phase you did run.

<!-- mokata:grounding -->
