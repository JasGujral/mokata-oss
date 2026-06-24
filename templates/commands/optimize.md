---
name: optimize
description: Measure first; keep only proven, behaviour-preserving wins.
---

# mokata · /optimize

Measure before you change anything. Apply a change only after a baseline is recorded, and keep it only when a before/after measurement shows it is faster with behaviour unchanged; otherwise revert.

## Gate (check)
No optimisation without a before/after measurement proving the win and preserved behaviour.

## Standalone
This command runs on its own — no upstream pipeline phase is required. It applies only its own gate above, and never silently skips a gate of a phase you did run.
