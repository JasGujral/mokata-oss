---
name: refine
description: mokata · Deep, user-steerable review of EXISTING code → propose prioritized refinements → HARD-GATE a scoped set, then hand off to spec.
argument-hint: "[scope]   # e.g. focus auth + security, or exclude performance"
---

# mokata · /refine

You are running mokata's REFINE phase — a deep, comprehensive review of code the user
ALREADY has, to propose concrete improvements. This is for EXISTING code (brainstorm is for
new problems). You are NOT writing a spec or code yet; you produce an approved set of
refinements and hand off to the `spec` skill.

## 1. Ground in the real code (don't guess, don't file-dump)

Use the codebase graph for structure — callers, callees, imports, and blast_radius of the
target — and memory for prior decisions/conventions. Read ONLY the code the user points at;
pull related context through the graph + memory, not by pasting the repo. If the graph or
memory is absent, read/grep the target and state your structural assumptions. Depth comes
from better grounding, not from spending more tokens.

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

## Grounding discipline
Decide from the code, not from assumption. Before you assert anything about types, signatures, behaviour, control flow, conventions, dependencies, error handling, or file layout, VERIFY it against the actual code: read the relevant source, run structural queries (`mokata query callers|callees|implementers|imports|blast_radius <symbol>`), and check memory for prior decisions and conventions. Consult the project brain: honour the captured rules and guardrails, and pull in only the context, references, and best-practices RELEVANT to the symbols/topic in play (just-in-time — never the whole corpus). The graph + memory are the source of truth; where they're absent, read or grep the code and state what you read. If a fact CANNOT be determined from the code, state the assumption explicitly and ASK — never silently assume. Cite what you verified. And continuously: if at any point you find a decision rested on an assumption, or the code contradicts something you assumed, STOP — surface it (what you assumed vs. what the code shows), CONFIRM with the user, and re-plan (route it through the deviation gate and amend the spec/ACs so they stay grounded and provable). There is no "assumed and continued" path.

## Progress
At the START and END of this phase, show where the run is: print the mokata run-progress block (the ordered phases marked done/current/pending with the [done/total] count and what's next) and a one-line banner naming what's running now — e.g. `mokata · refine (running)` then `mokata · refine (done)`. This is read-only over the persisted run-state (`mokata progress` / the `progress` MCP tool) — surface it, don't invent it. So the user never wonders whether mokata is running or which part.
