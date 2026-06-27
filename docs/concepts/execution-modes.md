# Concept: execution modes

At the start of **every** pipeline run — and every implementation (`develop`, `playbook`,
`exec`) — mokata asks which execution mode to use. The default is the sequential gated flow;
parallel is the governed opt-in.

## We always ask first

The choice — **parallel subagents vs. sequential flow** — is offered up front, every time,
and mokata **never fans out without your pick**. It's kept light (progressive disclosure):
asked **once per run**, not per sub-task, with a sensible default (sequential = lowest
cost). A saved `settings.execution.default` (`ask` | `sequential` | `parallel`, default
`ask`) lets power users skip the prompt without ever making parallel a silent default. The
chosen fan-out maps to the harness's real subagents (Claude Code's Task mechanism); a
harness without subagents degrades to sequential with a clear message.

## The selector (E8)

```bash
mokata exec                        # sequential gated flow (default, lowest cost)
mokata exec --parallel             # parallel subagents
mokata exec --parallel --fanout    # + concurrent fan-out
```

With no choice (non-interactive), the default is **sequential**. For parallel you further
choose **fresh-subagent isolation** and/or **concurrent fan-out** — both selectable.

## Sequential gated flow

The default and lowest-cost path: mokata processes tasks in-loop, no subagent runner
required. This is the floor that always works.

## Parallel subagents (E2/E3)

- **Fresh-subagent-per-task isolation (E2)** — each task is given *only* its own context
  (clean per task); the handback is a summary, not raw context.
- **Two-stage review (E3)** — when isolation is on, each task result is reviewed in two
  passes: **spec-compliance** then **code-quality**.
- **Concurrent fan-out** — tasks run at once (a thread pool).

Parallel runs **surface a token/cost estimate before running**, stay inside the existing
gates + audit ledger + token budget, **log every subagent decision**, and **degrade to the
sequential flow** when subagent execution is unavailable — never a hard failure.

## Per-task model routing (E4)

`ModelRouter` picks the **cheapest capable** model for a task and **escalates on a BLOCKED
signal** to the next stronger tier. The model set is a pluggable policy (generic
`fast`/`balanced`/`deep` tiers by default — override with your own); cost is computed
through the same `TokenTracker`.

## Depth engines (E5/E6)

- **`/mokata:bug` (E5)** — capture a reproducer **first**, then fix; labels progress
  `reported → reproduced → fixing → verified`; reproducer-before-fix is gated.
- **`/mokata:debug` (E6)** — **root-cause-before-fix** with **N-strikes escalation** (after N
  ruled-out hypotheses, escalate the model).
- **`/mokata:optimize` (E6)** — **measure-first**: no change before a baseline; an optimization is
  kept only when a before/after measurement shows it faster with behavior preserved.
