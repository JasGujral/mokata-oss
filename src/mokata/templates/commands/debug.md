---
name: debug
description: mokata · Reproduce first, capture in a failing test, then fix.
when_to_use: Engage when a failure, error, crash, or unexpected behaviour needs root-causing, when the user reports something is broken and asks why, or when a fix must be traced to its cause before any change. Do NOT engage to add new behaviour or a feature (that is develop), or to fix from a description without first reproducing the failure.
---

# mokata · /debug

Reproduce the failure before changing anything, then find the smallest change that fixes it. Root-cause from the REAL code — read the failing path and trace it GRAPH-FIRST (see Grounding discipline): `mokata query defs|refs|callers|callees <symbol>` to find the symbol and walk the path, then Read the lines it points at; grep is the fallback and its answer is degraded. Don't theorise about code you haven't read. Form hypotheses and rule them out against the actual source; after N strikes without a root cause, escalate to a stronger model. Root-cause before fix.

## Three failed fixes — then stop (the breaker)
Every time a fix you made is checked and the SAME failure is still there, record it: `mokata progress fix-attempt --failure "<the failing test id or the error line>" --failed` (and `--passed` once it is fixed). After THREE failed fix attempts at the same failure, STOP fixing: three failures say the APPROACH is wrong, and a fourth guess on top of three is how a codebase is buried. The breaker trips at the third — implementation writes for the run are then BLOCKED (gate `fix-breaker`) and the ruling is ledgered — and you ESCALATE instead: root-cause it with `/mokata:debug` (reproduce, trace the real path, name the cause before any fix) and bring the human a NEW approach to approve (`/mokata:brainstorm`, the approach-approval gate). An approved new approach clears the breaker; otherwise only the human lifts it (`mokata gate override fix-breaker`).

## Model tier — name it on every spawn
When this phase spawns a subagent, spawn the **debugger** subagent with the Agent tool's `model: "opus"` and `[mokata:debugger]` in its description — or this repo's `settings.models.debugger` (`mokata progress models` shows it). Escalating "to a stronger model" means exactly this tier. A role-tagged spawn that names no tier, or one below its role's floor, is BLOCKED by the gate-guard (gate `model-tier`). The tier is recorded for the run, with the subagent's measured model and token usage from its own transcript (`mokata progress models`). Tiers are Claude Code's own model aliases inside your login — no API key.

## Gate (check)
No fix before the bug is reproduced and the root cause is identified.

## Standalone
This command runs on its own — no upstream pipeline phase is required. It applies only its own gate above, and never silently skips a gate of a phase you did run.

<!-- mokata:grounding -->
