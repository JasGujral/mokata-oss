---
name: spec
description: Turn the problem into testable acceptance criteria; map each to a test.
---

# mokata · /spec

Turn the agreed problem into a spec: concrete, testable acceptance criteria. Map every criterion to a test before any code is written. If an approved brainstorm approach exists, the spec must honour it; if not, work from what the user states and mark assumptions.

## Gate (human)
No spec is complete until every acceptance criterion maps to a test (RED before GREEN) and any approved approach is satisfied; human-approve before emit.

## Standalone
This command runs on its own — no upstream pipeline phase is required. It applies only its own gate above, and never silently skips a gate of a phase you did run.
