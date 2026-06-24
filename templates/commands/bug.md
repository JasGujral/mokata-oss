---
name: bug
description: Start from a reproducer and a failing test, then fix.
---

# mokata · /bug

Start from a reproducer. Write a failing test that captures the bug, then fix to green and leave the test as a regression guard. Labels progress reported -> reproduced -> fixing -> verified; the fix is gated behind a reproducer.

## Gate (check)
A bug fix requires a reproducer and a failing test before the fix.

## Standalone
This command runs on its own — no upstream pipeline phase is required. It applies only its own gate above, and never silently skips a gate of a phase you did run.
