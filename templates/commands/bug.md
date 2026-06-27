---
name: bug
description: mokata · Start from a reproducer and a failing test, then fix.
---

# mokata · /bug

Start from a reproducer. Write a failing test that captures the bug, then fix to green and leave the test as a regression guard. Root-cause from the REAL code — read the failing path and trace it with the structural queries before fixing; don't guess at code you haven't read. Labels progress reported -> reproduced -> fixing -> verified; the fix is gated behind a reproducer.

## Gate (check)
A bug fix requires a reproducer and a failing test before the fix.

## Standalone
This command runs on its own — no upstream pipeline phase is required. It applies only its own gate above, and never silently skips a gate of a phase you did run.

## Grounding discipline
Decide from the code, not from assumption. Before you assert anything about types, signatures, behaviour, control flow, conventions, dependencies, error handling, or file layout, VERIFY it against the actual code: read the relevant source, run structural queries (`mokata query callers|callees|implementers|imports|blast_radius <symbol>`), and check memory for prior decisions and conventions. Consult the project brain: honour the captured rules and guardrails, and pull in only the context, references, and best-practices RELEVANT to the symbols/topic in play (just-in-time — never the whole corpus). The graph + memory are the source of truth; where they're absent, read or grep the code and state what you read. If a fact CANNOT be determined from the code, state the assumption explicitly and ASK — never silently assume. Cite what you verified. And continuously: if at any point you find a decision rested on an assumption, or the code contradicts something you assumed, STOP — surface it (what you assumed vs. what the code shows), CONFIRM with the user, and re-plan (route it through the deviation gate and amend the spec/ACs so they stay grounded and provable). There is no "assumed and continued" path.
