---
name: debug
description: mokata · Reproduce first, capture in a failing test, then fix.
when_to_use: Engage when a failure, error, crash, or unexpected behaviour needs root-causing, when the user reports something is broken and asks why, or when a fix must be traced to its cause before any change. Do NOT engage to add new behaviour or a feature (that is develop), or to fix from a description without first reproducing the failure.
---

# mokata · /debug

Reproduce the failure before changing anything, then find the smallest change that fixes it. Root-cause from the REAL code — read the failing path and trace it with the structural queries (callers/callees); don't theorise about code you haven't read. Form hypotheses and rule them out against the actual source; after N strikes without a root cause, escalate to a stronger model. Root-cause before fix.

## Gate (check)
No fix before the bug is reproduced and the root cause is identified.

## Standalone
This command runs on its own — no upstream pipeline phase is required. It applies only its own gate above, and never silently skips a gate of a phase you did run.

<!-- mokata:grounding -->
