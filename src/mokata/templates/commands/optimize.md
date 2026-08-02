---
name: optimize
description: mokata · Measure first; keep only proven, behaviour-preserving wins.
when_to_use: Engage when the user wants code made faster or lighter and the change can be measured, when profiling or a performance concern points at a hot path, or when a speed or memory improvement must be proven before it is kept. Do NOT engage without a way to measure the before and after, or when the change would alter behaviour (route that through the normal build).
---

# mokata · /optimize

Measure before you change anything — measure the REAL code, don't assume the hot path; confirm where the time actually goes first. Locate the code the measurement points at GRAPH-FIRST (see Grounding discipline) — `mokata query defs|refs|callers <symbol>` to find the hot symbol and everything that reaches it, Read/grep after and marked degraded — so you optimise the path that is actually called, not the one that merely matches a text search. Apply a change only after a baseline is recorded, and keep it only when a before/after measurement shows it is faster with behaviour unchanged; otherwise revert.

## Gate (check)
No optimisation without a before/after measurement proving the win and preserved behaviour.

## Standalone
This command runs on its own — no upstream pipeline phase is required. It applies only its own gate above, and never silently skips a gate of a phase you did run.

<!-- mokata:grounding -->
