---
name: review
description: mokata · Two-pass review: against the spec, then quality.
---

# mokata · /review

Review a diff in two passes. (1) Against the approved plan: does it do EXACTLY what was specified and approved — the approved acceptance criteria and the approved approach/refinements, nothing more? Flag any UNAPPROVED divergence (added scope, a changed approach, a changed or dropped AC, a redesign) as a finding — never a silent pass. Check the diff against the ACTUAL code it touches — do the calls, signatures, contracts, and conventions match the real symbols (verify with the structural queries)? Flag anything that looks ASSUMED rather than verified. (2) Quality: correctness, clarity, simplicity. Surface findings clearly; any fix is human-gated.

## Gate (human)
Review checks the diff against the spec (no extra features) first, then quality. Findings are surfaced for human-gated fixes.

## Standalone
This command runs on its own — no upstream pipeline phase is required. It applies only its own gate above, and never silently skips a gate of a phase you did run.

## Grounding discipline
Decide from the code, not from assumption. Before you assert anything about types, signatures, behaviour, control flow, conventions, dependencies, error handling, or file layout, VERIFY it against the actual code: read the relevant source, run structural queries (`mokata query callers|callees|implementers|imports|blast_radius <symbol>`), and check memory for prior decisions and conventions. Consult the project brain: honour the captured rules and guardrails, and pull in only the context, references, and best-practices RELEVANT to the symbols/topic in play (just-in-time — never the whole corpus). The graph + memory are the source of truth; where they're absent, read or grep the code and state what you read. If a fact CANNOT be determined from the code, state the assumption explicitly and ASK — never silently assume. Cite what you verified. And continuously: if at any point you find a decision rested on an assumption, or the code contradicts something you assumed, STOP — surface it (what you assumed vs. what the code shows), CONFIRM with the user, and re-plan (route it through the deviation gate and amend the spec/ACs so they stay grounded and provable). There is no "assumed and continued" path.

## Progress
At the START and END of this phase, show where the run is: print the mokata run-progress block (the ordered phases marked done/current/pending with the [done/total] count and what's next) and a one-line banner naming what's running now — e.g. `mokata · review (running)` then `mokata · review (done)`. This is read-only over the persisted run-state (`mokata progress` / the `progress` MCP tool) — surface it, don't invent it. So the user never wonders whether mokata is running or which part.
