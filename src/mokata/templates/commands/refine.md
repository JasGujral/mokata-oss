---
name: refine
description: mokata · Deep, user-steerable review of EXISTING code → propose prioritized refinements → HARD-GATE a scoped set, then hand off to spec.
when_to_use: Engage when the user wants a deep, steerable review of EXISTING code to surface improvements, when they ask what to refactor, harden, or clean up in a codebase, or when scoping a set of changes to hand off to spec. Do NOT engage to implement the changes (refine only proposes), or for a brand-new feature with no code yet (that is brainstorm).
argument-hint: "[scope]   # e.g. focus auth + security, or exclude performance"
---

# mokata · /refine

You are running mokata's REFINE phase — a deep, comprehensive review of code the user
ALREADY has, to propose concrete improvements. This is for EXISTING code (brainstorm is for
new problems). You are NOT writing a spec or code yet; you produce an approved set of
refinements and hand off to the `spec` skill.

## 1. Ground in the real code (don't guess, don't file-dump)

Navigate GRAPH-FIRST (see Grounding discipline): use the codebase graph for structure —
`mokata query defs|refs|callers|callees|imports|blast_radius <symbol>` — and memory for prior
decisions/conventions. Locating a symbol, its definition, or its references is a `mokata query`
FIRST; Read ONLY the code the user points at (and the lines the graph found), and pull related
context through the graph + memory, not by pasting the repo. If the graph or memory is absent,
read/grep the target, say that the answer came from the lexical floor (degraded), and state
your structural assumptions. Depth comes from better grounding, not from spending more tokens.

## 2. Deep, comprehensive review (the default is thorough)

Review across ALL dimensions unless the user narrows it: architecture & boundaries, design
patterns and anti-patterns, CS best practices, code quality/readability, testability,
coupling & cohesion, error handling, security, and performance.

## 2a. Honor user-steerable scope

The invocation may include free-form guidance (via $ARGUMENTS) to include, exclude, or focus
— e.g. "focus on the auth module and security", "exclude performance", "only the public API".
State up front, in one line, which dimensions/areas are IN and OUT of scope for this run.
With no guidance, do the full in-depth review.

## 3. Propose changes as a PRIORITIZED list

For each proposed refinement give: the change, its rationale, the principle it serves, the
tradeoff/cost, and a behavior-impact note (behavior-PRESERVING vs behavior-CHANGING). Order
by priority. Surface a prioritized summary first; expand a dimension on demand rather than
emitting an exhaustive wall.

## 4. Offer 2-3 coherent directions

Where refinement directions genuinely differ (e.g. "minimal cleanup" vs "restructure the
boundary"), present 2-3 coherent options — not one strawman flanked by foils — so the user
chooses SCOPE, not just yes/no.

## The one hard gate

HARD-GATE: do NOT draft a spec, write code, or hand off until the user EXPLICITLY approves a
SCOPED SET of refinements. No approval, no spec. This gate cannot be skipped, softened, or
assumed. If you are unsure whether approval was given, it was not.

## Hand off (reuse, don't reinvent)

Once a scoped set is approved, persist it and HAND OFF to the existing `spec` skill — refine
does NOT write the spec itself. `spec` turns the approved changes into acceptance criteria,
INCLUDING "behavior preserved" criteria for any behavior-preserving refinement, so the
completeness gate requires CHARACTERIZATION tests (written RED, before the change) that pin
current behavior. Then the unchanged flow runs: spec → completeness gate → test (RED) →
develop (GREEN) → review. Behavior-preserving by default; structural changes are pinned by
tests written before the change.

## Stick to the approved set

Once a scoped set is approved, implement ONLY that set — do not broaden it. If a needed change
falls outside the approved refinements (a new refinement appears, or one turns out wrong or
infeasible), STOP and get EXPLICIT approval — re-approve an expanded/amended set before
proceeding. Never silently broaden scope or change the approved direction; a plan change is a
durable change, so it is human-gated and audited.
 Spec-awareness (regression guard): before making the change, check it against the SAVED specs and recorded decisions — run `mokata spec-check --symbols <touched> --files <touched>` (or the `spec_check` tool) over the symbols/files in play. If it reports the change affects a saved spec or a recorded decision, STOP and route it through the deviation gate: the human confirms (amend/supersede the affected spec/decision) or you re-plan — never break a previously-approved spec silently. Degrade-clean: no saved specs yet ⇒ it's a no-op (no false alarm); no code graph ⇒ it falls back to a lexical/file-overlap check and says so.

## Gate (human)
HARD-GATE: no spec until the user explicitly approves a scoped set of refinements; the approved set hands off to the existing spec skill.

## Standalone
This command runs on its own — no upstream pipeline phase is required. It applies only its own gate above, and never silently skips a gate of a phase you did run.

<!-- mokata:grounding -->

## Progress
At the START and END of this phase, show where the run is: print the mokata run-progress block (the ordered phases marked done/current/pending with the [done/total] count and what's next) and a one-line banner naming what's running now — e.g. `mokata · refine (running)` then `mokata · refine (done)`. This is read-only over the persisted run-state (`mokata progress` / the `progress` MCP tool) — surface it, don't invent it. So the user never wonders whether mokata is running or which part. Where the harness has a NATIVE to-do list (a summary line + steps you can mark done / in-progress / pending), render THIS SAME run-progress there — a summary line plus one item per phase, each done / in-progress / pending — and keep it in sync as each gate passes. DERIVE those items from mokata's run-state (`mokata progress` / `build_todo_items`), never invent steps of your own; YOU render the widget (mokata drives it through this prompt — it cannot call the to-do tool itself). Where there is NO native to-do surface, fall back to printing the run-progress block above. It is one run-progress, shown on whichever channel the user is looking at. When the phase FINISHES, also print a one-line recap + the single next step — `✓ refine done — <one-line recap>. Next: `/mokata:<next>`` (include the in-stage counter, e.g. `[3/7 ACs]`, when one applies). The next step reaches the user through the `/` command autocomplete (click-to-fill) and your own follow-up offer — you CANNOT pre-fill the prompt box or rebind Tab, so never imply you can; just NAME the command and offer to proceed. If a gate fired, print its one-line verdict and, on a block, the single action that clears it (`→ to unblock: …`).
