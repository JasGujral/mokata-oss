---
name: ship
description: mokata · Verify it's truly done, then let YOU choose how to land it.
---

# mokata · /ship

Close out the work — verify it's actually done, then help the human land it. mokata NEVER merges, opens a PR, or deletes work on its own.

1. VERIFY (evidence over claims — do not take 'done' on faith): the full test suite is GREEN (re-run it; compare against the green baseline you confirmed before starting, so any new failure is attributable to this change), every acceptance criterion in the emitted spec is met (completeness), and `review` passed. If ANYTHING is red or unmet, STOP and report exactly what's missing — do not present landing options for unfinished work.
2. SUMMARIZE what shipped: the spec and its acceptance-criteria-to-tests mapping, the diff surface (files/symbols changed), the decisions captured to memory, and the audit trail — so landing it is a reviewed decision.
3. PRESENT the landing options and let the HUMAN choose: merge, open a PR, keep the branch, or discard. You may PREPARE (stage a commit/branch, draft a PR description), but run a git action ONLY after the human's explicit confirmation of a specific option. Never merge, force, or delete anything unasked; never discard work without explicit confirmation.
4. RECORD the finish decision in the audit ledger, then show the end-of-run "what I changed and WHY" recap — mokata's bounded, read-only `audit --why` over this run (what changed + each gate decision + why) — so finishing the run shows what landed and why. The recap is derived; it never implies mokata merged anything.

## Gate (human)
Shipping verifies done (green tests + met ACs + passed review) and the human chooses how to land it; mokata never merges/PRs/deletes without explicit confirmation.

## Standalone
This command runs on its own — no upstream pipeline phase is required. It applies only its own gate above, and never silently skips a gate of a phase you did run.

## Grounding discipline
Decide from the code, not from assumption. Before you assert anything about types, signatures, behaviour, control flow, conventions, dependencies, error handling, or file layout, VERIFY it against the actual code: read the relevant source, run structural queries (`mokata query callers|callees|implementers|imports|blast_radius <symbol>`), and check memory for prior decisions and conventions. Consult the project brain: honour the captured rules and guardrails, and pull in only the context, references, and best-practices RELEVANT to the symbols/topic in play (just-in-time — never the whole corpus). The graph + memory are the source of truth; where they're absent, read or grep the code and state what you read. If a fact CANNOT be determined from the code, state the assumption explicitly and ASK — never silently assume. Cite what you verified. And continuously: if at any point you find a decision rested on an assumption, or the code contradicts something you assumed, STOP — surface it (what you assumed vs. what the code shows), CONFIRM with the user, and re-plan (route it through the deviation gate and amend the spec/ACs so they stay grounded and provable). There is no "assumed and continued" path.

## Progress
At the START and END of this phase, show where the run is: print the mokata run-progress block (the ordered phases marked done/current/pending with the [done/total] count and what's next) and a one-line banner naming what's running now — e.g. `mokata · ship (running)` then `mokata · ship (done)`. This is read-only over the persisted run-state (`mokata progress` / the `progress` MCP tool) — surface it, don't invent it. So the user never wonders whether mokata is running or which part. Where the harness has a NATIVE to-do list (a summary line + steps you can mark done / in-progress / pending), render THIS SAME run-progress there — a summary line plus one item per phase, each done / in-progress / pending — and keep it in sync as each gate passes. DERIVE those items from mokata's run-state (`mokata progress` / `build_todo_items`), never invent steps of your own; YOU render the widget (mokata drives it through this prompt — it cannot call the to-do tool itself). Where there is NO native to-do surface, fall back to printing the run-progress block above. It is one run-progress, shown on whichever channel the user is looking at. When the phase FINISHES, also print a one-line recap + the single next step — `✓ ship done — <one-line recap>. Next: `/mokata:<next>`` (include the in-stage counter, e.g. `[3/7 ACs]`, when one applies). The next step reaches the user through the `/` command autocomplete (click-to-fill) and your own follow-up offer — you CANNOT pre-fill the prompt box or rebind Tab, so never imply you can; just NAME the command and offer to proceed. If a gate fired, print its one-line verdict and, on a block, the single action that clears it (`→ to unblock: …`).
