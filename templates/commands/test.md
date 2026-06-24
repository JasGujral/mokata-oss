---
name: test
description: Write failing tests first (RED); no implementation.
---

# mokata · /test

Write tests that express the desired behaviour and watch them FAIL first (RED). Do NOT write implementation here. One behaviour per test, clear names, real code over mocks.

## Gate (check)
Tests must be shown to FAIL before any implementation exists. Writing implementation in this step is a gate violation.

## Standalone
This command runs on its own — no upstream pipeline phase is required. It applies only its own gate above, and never silently skips a gate of a phase you did run.
