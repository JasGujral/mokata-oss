---
name: develop
description: Implement the minimum to turn a failing test green.
---

# mokata · /develop

Implement the minimum needed to turn a failing test GREEN. No new behaviour without a failing test that demands it; keep the change surgical and stop when the test passes.

## Gate (check)
Implementation is allowed only against an existing failing test; the change stays minimal.

## Standalone
This command runs on its own — no upstream pipeline phase is required. It applies only its own gate above, and never silently skips a gate of a phase you did run.
