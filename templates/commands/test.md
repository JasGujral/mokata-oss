---
name: test
description: mokata · Write failing tests first (RED); no implementation.
---

# mokata · /test

Do NOT write tests until the spec is emitted and SAVED: if there is no `emitted_spec.json` (the persisted, completeness-gate-passed spec), STOP and produce + emit the spec first (`/mokata:spec`). Then write tests that express the desired behaviour and watch them FAIL first (RED). Do NOT write implementation here. One behaviour per test, clear names, real code over mocks. Reference the REAL names, signatures, and return types found in the code — never invent an interface; verify each symbol you call exists and has the shape you expect. Test ONLY the approved acceptance criteria — do not invent ACs or cover behaviour the approved spec doesn't state. If an AC is wrong, missing, or untestable, STOP and ask to amend the spec (so ACs and tests stay provable); never silently add or drop coverage.

## Gate (check)
Tests must be shown to FAIL before any implementation exists. Writing implementation in this step is a gate violation.

## Standalone
This command runs on its own — no upstream pipeline phase is required. It applies only its own gate above, and never silently skips a gate of a phase you did run.

## Grounding discipline
Decide from the code, not from assumption. Before you assert anything about types, signatures, behaviour, control flow, conventions, dependencies, error handling, or file layout, VERIFY it against the actual code: read the relevant source, run structural queries (`mokata query callers|callees|implementers|imports|blast_radius <symbol>`), and check memory for prior decisions and conventions. Consult the project brain: honour the captured rules and guardrails, and pull in only the context, references, and best-practices RELEVANT to the symbols/topic in play (just-in-time — never the whole corpus). The graph + memory are the source of truth; where they're absent, read or grep the code and state what you read. If a fact CANNOT be determined from the code, state the assumption explicitly and ASK — never silently assume. Cite what you verified. And continuously: if at any point you find a decision rested on an assumption, or the code contradicts something you assumed, STOP — surface it (what you assumed vs. what the code shows), CONFIRM with the user, and re-plan (route it through the deviation gate and amend the spec/ACs so they stay grounded and provable). There is no "assumed and continued" path.

## Precondition
Precondition (spec-persisted): a saved spec with at least one acceptance criterion must exist (`emitted_spec.json`, written by the human-gated `emit` after the completeness gate passes). If it's absent, STOP and produce + emit the spec first (`/mokata:spec`) — do not write code or tests against an unsaved spec.

## Progress
At the START and END of this phase, show where the run is: print the mokata run-progress block (the ordered phases marked done/current/pending with the [done/total] count and what's next) and a one-line banner naming what's running now — e.g. `mokata · test (running)` then `mokata · test (done)`. This is read-only over the persisted run-state (`mokata progress` / the `progress` MCP tool) — surface it, don't invent it. So the user never wonders whether mokata is running or which part.
