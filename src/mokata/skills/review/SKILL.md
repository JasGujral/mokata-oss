---
name: review
description: mokata · Two-pass review: against the spec, then quality.
when_to_use: Engage when an implementation has just finished and its tests are GREEN, when the user asks to check/review a diff or change, or before merging/shipping — review is the closing gate of the mokata pipeline. Do NOT engage mid-implementation, or for a brand-new problem with no code yet (that's brainstorm).
---

> **mokata Agent Skill.** This is mokata's `review` capability, surfaced so Claude can engage it
> automatically when the moment fits. It runs the SAME protocol as the `/mokata:review` command,
> from one shared source — follow that protocol directly here; do not hand off to a parallel
> flow. mokata's non-negotiables still hold: durable writes are **human-gated** (preview, then
> explicit approval), and this capability's own gate is never silently skipped.

# mokata · /review

Review a diff in two passes. (1) Against the approved plan: does it do EXACTLY what was specified and approved — the approved acceptance criteria and the approved approach/refinements, nothing more? Flag any UNAPPROVED divergence (added scope, a changed approach, a changed or dropped AC, a redesign) as a finding — never a silent pass. Check the diff against the ACTUAL code it touches — do the calls, signatures, contracts, and conventions match the real symbols (verify with the structural queries)? Flag anything that looks ASSUMED rather than verified. (2) Quality: correctness, clarity, simplicity. Surface findings clearly; any fix is human-gated.

Run this review INDEPENDENTLY by default (this is the closing gate, not a self-check). Spawn a FRESH-CONTEXT subagent and hand it a SELF-CONTAINED brief — the emitted spec + its acceptance criteria, the approved approach/refinement set, the DIFF under review, and how to run the tests — and explicitly NO builder conclusions or claims. The subagent re-derives its verdict from the code and its OWN test runs (the doc-00 release-gate pattern applied per-feature); it must reach the two-pass verdict above on its own, not ratify yours. Degrade-clean: where the harness has NO subagents (or `settings.review.independent=off`), fall back to the inline two-pass review and SAY SO honestly — print `review: inline — this harness has no subagents, so this review shares the builder's context` (or the config note) and continue. NEVER block on a missing subagent capability; independence is the default, not a requirement.

## Gate (human)
Review checks the diff against the spec (no extra features) first, then quality. Findings are surfaced for human-gated fixes.

## Standalone
This command runs on its own — no upstream pipeline phase is required. It applies only its own gate above, and never silently skips a gate of a phase you did run.

## Grounding discipline
Decide from the code, not from assumption. Before you assert anything about types, signatures, behaviour, control flow, conventions, dependencies, error handling, or file layout, VERIFY it against the actual code: read the relevant source, run structural queries (`mokata query callers|callees|implementers|imports|blast_radius <symbol>`), and check memory for prior decisions and conventions. Consult the project brain: honour the captured rules and guardrails, and pull in only the context, references, and best-practices RELEVANT to the symbols/topic in play (just-in-time — never the whole corpus). The graph + memory are the source of truth; where they're absent, read or grep the code and state what you read. If a fact CANNOT be determined from the code, state the assumption explicitly and ASK — never silently assume. Cite what you verified. And continuously: if at any point you find a decision rested on an assumption, or the code contradicts something you assumed, STOP — surface it (what you assumed vs. what the code shows), CONFIRM with the user, and re-plan (route it through the deviation gate and amend the spec/ACs so they stay grounded and provable). There is no "assumed and continued" path.

## Progress
At the START and END of this phase, show where the run is: print the mokata run-progress block (the ordered phases marked done/current/pending with the [done/total] count and what's next) and a one-line banner naming what's running now — e.g. `mokata · review (running)` then `mokata · review (done)`. This is read-only over the persisted run-state (`mokata progress` / the `progress` MCP tool) — surface it, don't invent it. So the user never wonders whether mokata is running or which part. Where the harness has a NATIVE to-do list (a summary line + steps you can mark done / in-progress / pending), render THIS SAME run-progress there — a summary line plus one item per phase, each done / in-progress / pending — and keep it in sync as each gate passes. DERIVE those items from mokata's run-state (`mokata progress` / `build_todo_items`), never invent steps of your own; YOU render the widget (mokata drives it through this prompt — it cannot call the to-do tool itself). Where there is NO native to-do surface, fall back to printing the run-progress block above. It is one run-progress, shown on whichever channel the user is looking at. When the phase FINISHES, also print a one-line recap + the single next step — `✓ review done — <one-line recap>. Next: `/mokata:<next>`` (include the in-stage counter, e.g. `[3/7 ACs]`, when one applies). The next step reaches the user through the `/` command autocomplete (click-to-fill) and your own follow-up offer — you CANNOT pre-fill the prompt box or rebind Tab, so never imply you can; just NAME the command and offer to proceed. If a gate fired, print its one-line verdict and, on a block, the single action that clears it (`→ to unblock: …`).

## Record stage entry
On ENTRY to this phase — before anything else — record the stage transition so the always-on mokata badge can tell develop/review/ship apart: run `mokata progress mark review`. This appends a single `stage_enter` event to the append-only progress-event log — OBSERVABILITY, like the audit ledger: it is UNGATED (it writes no durable code/memory/config, so it never prompts for approval) and best-effort (if it fails, keep going — it must never block the phase). It exists so the badge shows the true current stage instead of guessing; it fabricates nothing.

## Record verdict
When the review reaches its verdict, PERSIST it so `/mokata:ship` can verify the record (evidence over vibes): run `mokata progress record-review --passed` (or `--failed`), adding `--independent` when it ran as a fresh-context subagent and OMITTING it when it degraded to the inline two-pass. This appends a single `review_verdict` event to the append-only progress-event log — OBSERVABILITY, like the stage-entry mark: UNGATED (no durable code/memory/config write, so no approval prompt) and best-effort (if it fails, keep going). Ship reads this record and BLOCKS when it is absent, so recording the verdict is what closes the pipeline.
