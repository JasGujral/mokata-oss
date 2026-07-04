---
name: onboard
description: mokata · Guided capture of the project's rules, guardrails, conventions, domain context & docs into TYPED, human-gated memory the skills then reference.
when_to_use: Engage when the user wants to teach mokata the project's rules, guardrails, conventions, domain facts/formulas, or a reference document — i.e. capturing institutional knowledge mokata should honour, during setup or any time later. Do NOT engage for one-off decisions mid-task (those are remembered inline).
---

> **mokata Agent Skill.** This is mokata's `onboard` capability, surfaced so Claude can engage it
> automatically when the moment fits. It runs the SAME protocol as the `/mokata:onboard` command,
> from one shared source — follow that protocol directly here; do not hand off to a parallel
> flow. mokata's non-negotiables still hold: durable writes are **human-gated** (preview, then
> explicit approval), and this capability's own gate is never silently skipped.

# mokata · /onboard

Capture this project's institutional knowledge as TYPED, human-gated memory — the durable "project brain" that mokata's skills will reference on every relevant run. Guide the user through it as a conversation, not a form; process what they say into structured entries; never store raw input verbatim.

## Guide one focus at a time
Ask about ONE area at a time, briefly, and let the user answer in their words — do not dump a wall of questions. Walk these focuses, skipping any the user waves off:
1. **Rules** — hard constraints mokata must ALWAYS honour here (e.g. "no network in the parser").
2. **Guardrails** — safety/quality constraints (e.g. "all money math uses Decimal, never float"; "never log secrets").
3. **Conventions / best practices** — recommended patterns (e.g. "tests live next to the module as test_<name>.py").
4. **Domain context** — facts, formulas, calculations, business constraints (e.g. "tax_rate = 0.2"; "fiscal year starts in April"; a pricing formula).
5. **Documents to ingest** — offer to take a pasted or linked doc (architecture/domain spec) and distil its key points.

## Process the input — store structured, not raw (this is the point)
For each thing the user gives you (including any ingested document — capture the essential points, NOT the whole text):
- **Distil** it to the essential rule/fact in clear, normalised wording.
- **Assign the right kind**: one of `rule`, `guardrail`, `best-practice`, `context`, `reference` (a `reference` entry keeps a pointer to its source document).
- **Dedup / merge** against what's already captured: check existing memory first (`mokata memory` / the `recall` tool). If it duplicates an entry, skip it; if it MERGES with one, combine them; if it CONTRADICTS an existing entry, do NOT overwrite — route it through the self-healing old→new surface so the change is reviewed (supersede, never silent).
- **Propose** the resulting structured entries back to the user: show each as kind · subject · value, grouped by kind, so they can see exactly what will be remembered.

## Human-gate every write, then persist (typed + shared)
Nothing is stored until the user approves it. For each proposed entry let the user approve / edit / reject, then persist the approved ones through the gated write (the `remember` tool with its `kind` set, or `mokata memory`). Persisted entries are shared per the team's memory backend (Stage 35); rules/guardrails are always-on and committed. A secret in a value is blocked at the gate even when approved — strip it and reference an env var instead.

## Surface the auto-proposed guardrails (recurring corrections)
mokata watches the audit ledger for corrections that recur — writes you declined, changes you reverted, spec conflicts — and distils them into guardrail-rule PROPOSALS (the same proposals `mokata rules` surfaces). When this command runs it lists any such proposals. Treat each as a suggestion the user decides on: read it back, and if they want it, capture it as a `rule`/`guardrail` through the normal gated write above. PROPOSAL-ONLY — mokata never auto-adds a rule; the user approves, edits, or rejects each one.

## Review, edit, and re-run anytime
Show the user how to live with the brain: `mokata memory --kind <rule|guardrail|best-practice|context|reference|decision>` reviews what mokata will honour, grouped by kind; `mokata memory edit <subject>` updates an entry (a formula changes, a guardrail is revised) — human-gated and routed through self-healing, never silent. This skill is re-invokable: run it during setup and any time later to add or update knowledge.

## Frugal by design (P11)
More captured knowledge must NOT mean more tokens per run. Keep each entry terse. Only rule/guardrail go always-on (within the rules budget — if there are too many, help the user prioritise; never blow the budget). context/reference/best-practice are retrieved just-in-time, only when a later skill's task is relevant to them — never loaded wholesale.

## Gate (human)
Every captured entry is distilled, typed, and HUMAN-GATED before it is stored; a conflict routes through self-healing (old→new), never silent.

## Standalone
This command runs on its own — no upstream pipeline phase is required. It applies only its own gate above, and never silently skips a gate of a phase you did run.

## Grounding discipline
Decide from the code, not from assumption. Before you assert anything about types, signatures, behaviour, control flow, conventions, dependencies, error handling, or file layout, VERIFY it against the actual code: read the relevant source, run structural queries (`mokata query callers|callees|implementers|imports|blast_radius <symbol>`), and check memory for prior decisions and conventions. Consult the project brain: honour the captured rules and guardrails, and pull in only the context, references, and best-practices RELEVANT to the symbols/topic in play (just-in-time — never the whole corpus). The graph + memory are the source of truth; where they're absent, read or grep the code and state what you read. If a fact CANNOT be determined from the code, state the assumption explicitly and ASK — never silently assume. Cite what you verified. And continuously: if at any point you find a decision rested on an assumption, or the code contradicts something you assumed, STOP — surface it (what you assumed vs. what the code shows), CONFIRM with the user, and re-plan (route it through the deviation gate and amend the spec/ACs so they stay grounded and provable). There is no "assumed and continued" path.
