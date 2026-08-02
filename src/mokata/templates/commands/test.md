---
name: test
description: mokata · Write failing tests first (RED); no implementation.
when_to_use: Engage when a spec has been emitted and its acceptance criteria need failing tests written, when the user asks to write tests for approved behaviour, or when starting TDD on a change before any implementation exists. Do NOT engage without a persisted spec (produce the spec first), or to write implementation code (that is develop).
---

# mokata · /test

Do NOT write tests until the spec is emitted and SAVED: FETCH this run's persisted, completeness-gate-passed spec with the `spec_show` tool (or `mokata spec show`) and work from what it returns; if there is none, STOP and produce + emit the spec first (`/mokata:spec`). Then write tests that express the desired behaviour and watch them FAIL first (RED). Do NOT write implementation here. One behaviour per test, clear names, real code over mocks. Reference the REAL names, signatures, and return types found in the code — never invent an interface; verify each symbol you call exists and has the shape you expect. Test ONLY the approved acceptance criteria — do not invent ACs or cover behaviour the approved spec doesn't state. If an AC is wrong, missing, or untestable, STOP and ask to amend the spec (so ACs and tests stay provable); never silently add or drop coverage.

## Gate (check)
Tests must be shown to FAIL before any implementation exists. Writing implementation in this step is a gate violation.

## Standalone
This command runs on its own — no upstream pipeline phase is required. It applies only its own gate above, and never silently skips a gate of a phase you did run.

<!-- mokata:grounding -->

## Precondition
Precondition (spec-persisted): a saved spec with at least one acceptance criterion must exist — the run's persisted spec, written by the human-gated `emit` after the completeness gate passes. FETCH it with the `spec_show` tool (or `mokata spec show`); it is keyed to this run, not a file you can open by name. If it's absent, STOP and produce + emit the spec first (`/mokata:spec`) — do not write code or tests against an unsaved spec.

## Progress
At the START and END of this phase, show where the run is: print the mokata run-progress block (the ordered phases marked done/current/pending with the [done/total] count and what's next) and a one-line banner naming what's running now — e.g. `mokata · test (running)` then `mokata · test (done)`. This is read-only over the persisted run-state (`mokata progress` / the `progress` MCP tool) — surface it, don't invent it. So the user never wonders whether mokata is running or which part. Where the harness has a NATIVE to-do list (a summary line + steps you can mark done / in-progress / pending), render THIS SAME run-progress there — a summary line plus one item per phase, each done / in-progress / pending — and keep it in sync as each gate passes. DERIVE those items from mokata's run-state (`mokata progress` / `build_todo_items`), never invent steps of your own; YOU render the widget (mokata drives it through this prompt — it cannot call the to-do tool itself). Where there is NO native to-do surface, fall back to printing the run-progress block above. It is one run-progress, shown on whichever channel the user is looking at. When the phase FINISHES, also print a one-line recap + the single next step — `✓ test done — <one-line recap>. Next: `/mokata:<next>`` (include the in-stage counter, e.g. `[3/7 ACs]`, when one applies). The next step reaches the user through the `/` command autocomplete (click-to-fill) and your own follow-up offer — you CANNOT pre-fill the prompt box or rebind Tab, so never imply you can; just NAME the command and offer to proceed. If a gate fired, print its one-line verdict and, on a block, the single action that clears it (`→ to unblock: …`).
