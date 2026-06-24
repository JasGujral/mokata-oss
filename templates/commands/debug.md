---
name: debug
description: Reproduce first, capture in a failing test, then fix.
---

# mokata · /debug

Reproduce the failure before changing anything, then find the smallest change that fixes it. Form hypotheses and rule them out; after N strikes without a root cause, escalate to a stronger model. Root-cause before fix.

## Gate (check)
No fix before the bug is reproduced and the root cause is identified.

## Standalone
This command runs on its own — no upstream pipeline phase is required. It applies only its own gate above, and never silently skips a gate of a phase you did run.
