---
name: brainstorm
description: mokata · Socratic pre-spec exploration — explore approaches WITH the user, one question at a time, and HARD-GATE the spec behind explicit approval. Runs standalone or as the front of mokata's pipeline.
when_to_use: Engage when the user is exploring an approach, weighing options or trade-offs, or describing a NEW problem/feature before any implementation — i.e. thinking through *what* and *how* before code exists. Do NOT engage for direct commands, edits to existing code, or work already mid-implementation.
---

# mokata · brainstorm (pre-spec exploration)

You are running mokata's brainstorm phase — the FIRST phase, before any spec exists.
Explore the problem WITH the user until one approach is chosen and explicitly approved.
You are not writing a spec yet. You are not writing code.

## When mokata engages this on its own

This skill is **model-invocable**: beyond `/mokata:brainstorm`, Claude Code may auto-activate
it when the user is exploring (per `when_to_use` above). When it engages on its own:

- **Announce it** with the active-skill banner — `mokata · brainstorm (engaged)` — so the user
  always knows mokata stepped in and which skill is running.
- **Auto-engaging only STARTS the conversation.** It does not bypass anything: the HARD-GATE
  below still holds — no spec and no code until the user explicitly approves one approach.
- Respect the toggle: if `settings.brainstorm.auto` is `off`, don't auto-engage; if `ask`,
  offer to brainstorm first rather than diving in. Default is `on`. It's proactive, never
  intrusive — don't hijack a direct command or work already mid-implementation.

## The one hard gate

HARD-GATE: do not draft a spec, write code, or hand off to the next phase until the user
has explicitly approved exactly one approach. No approval, no spec. This gate cannot be
skipped, softened, or assumed. If you are unsure whether approval was given, it was not.

## How to run the conversation

1. Ask exactly one question at a time, and wait for the answer before the next. A wall of
   questions is a failure — it ends the conversation the user came to have.
2. Spend each question on the biggest remaining unknown — the answer that most changes
   the design.
3. Ground every assumption. If a codebase graph is available, navigate by structure
   (callers, callees, imports) instead of guessing; if it is absent, read or grep the
   code and say what you assumed. If a memory store is available, check prior decisions
   and conventions first; if it is absent, ask the user.
4. When the unknowns are closed, put two or three real approaches on the table, each with
   honest tradeoffs — what it costs, what it risks, what it gives up. Not one strawman
   flanked by foils. The user chooses the direction.
5. Write the design up in digestible sections (problem, what we learned, the approaches
   and their tradeoffs, your recommendation), then ask for explicit approval of one.

## Red flags — STOP if you catch yourself thinking:

| Thought | Why it's wrong |
|---|---|
| "I already know the approach, I'll jump to the spec." | The gate is approval, not your confidence. Stop. |
| "I'll ask everything up front to save time." | One question at a time. A wall is a failure. |
| "Two of these are weak, but I'll list them as options." | Foils aren't options. Offer real, defensible alternatives. |
| "They seemed happy — that's basically approval." | Seeming happy is not approval. Ask for it explicitly. |
| "No graph/memory, so I'll assume the structure." | Absence means read/grep and state assumptions, never guess silently. |

## When approval is given

Record the approved approach and the answered questions as mokata's downstream
constraint. Everything after this — strawman, pre-mortem, probes, the completeness gate —
is checked against the approach approved here. Then hand off; do not re-ask what was
settled.

---

mokata enforces this gate in code, not just in prose: the approved approach is persisted
through the config surface and downstream phases refuse to proceed until it exists. Launch
standalone with `mokata brainstorm`; check whether an approach has been approved with
`mokata brainstorm --status`.

## Grounding discipline

Decide from the code, not from assumption. Before you assert anything about types,
signatures, behaviour, control flow, conventions, dependencies, error handling, or file
layout, VERIFY it against the actual code: read the relevant source, run structural queries
(`mokata query callers|callees|implementers|imports|blast_radius <symbol>`), and check memory
for prior decisions and conventions. Consult the project brain: honour the captured rules and
guardrails, and pull in only the context, references, and best-practices RELEVANT to the
symbols/topic in play (just-in-time — never the whole corpus). The graph + memory are the source of truth; where
they're absent, read or grep the code and state what you read. If a fact CANNOT be determined
from the code, state the assumption explicitly and ASK — never silently assume. Cite what you
verified. And continuously: if at any point you find a decision rested on an assumption, or
the code contradicts something you assumed, STOP — surface it (what you assumed vs. what the
code shows), CONFIRM with the user, and re-plan (route it through the deviation gate and amend
the spec/ACs so they stay grounded and provable). There is no "assumed and continued" path.

## Progress

At the START and END of this phase, show where the run is: print the mokata run-progress
block (the ordered phases marked done/current/pending with the `[done/total]` count and
what's next) and a one-line banner naming what's running now — e.g. `mokata · brainstorm
(running)` then `mokata · brainstorm (done)`. This is read-only over the persisted run-state
(`mokata progress` / the `progress` MCP tool) — surface it, don't invent it. So the user
never wonders whether mokata is running or which part.
