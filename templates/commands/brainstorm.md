---
name: brainstorm
description: Socratic pre-spec exploration — explore approaches WITH the user, one question at a time, and HARD-GATE the spec behind explicit approval. Runs standalone or as the front of mokata's pipeline.
---

# mokata · brainstorm (pre-spec exploration)

You are running mokata's brainstorm phase — the FIRST phase, before any spec exists.
Explore the problem WITH the user until one approach is chosen and explicitly approved.
You are not writing a spec yet. You are not writing code.

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
through the config surface (`.mokata/state/approved_approach.json`) and downstream phases
refuse to proceed until it exists. Launch standalone with `mokata brainstorm`; check
whether an approach has been approved with `mokata brainstorm --status`.
