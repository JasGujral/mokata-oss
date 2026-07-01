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

## Assisted task decomposition (Stage 54f)

The selector and orchestrator answer *how* to run a set of tasks — but where do the tasks
come from? `mokata decompose` (and the `decompose` MCP tool / `/mokata:decompose`) **splits
the approved work into the tasks it runs**, then hands them straight to the flow above. It
adds **no** fan-out logic of its own — it reuses the selector and `run_tasks`.

**decompose → confirm → parallel/sequential:**

1. **Decompose (read-only, derived).** From the emitted spec's acceptance criteria it
   proposes **one independent subtask per AC** and infers **dependencies**: two subtasks that
   touch the **same symbol or file** are kept ordered (a `depends_on` edge). The **code
   graph** verifies independence when one is wired (it expands each symbol's blast-radius
   neighbourhood, catching links the text alone would miss); otherwise the **lexical floor**
   is used and independence is flagged **unverified**.
2. **Confirm (human-gated, logged).** The proposed split + dependency plan is presented
   compactly; **nothing fans out until you confirm**. You can approve as-is, **edit** (name
   the subtasks to keep), or reject. The decision is recorded in the audit ledger.
3. **Run (the existing flow).** Confirmed tasks flow into `resolve_execution_choice` (shows
   the cost estimate, asks parallel-vs-sequential, default **sequential**) → `run_tasks`
   (isolation + two-stage review + degrade-clean).

**Conservative by construction.** It **never silently parallelizes** work that might be
dependent: when dependencies exist — or independence is unverified because no graph is wired
— concurrent **fan-out is withheld** and isolated tasks run in **declared order** (you're told
why). Degrade-clean: no spec/ACs → a friendly "nothing to split"; subagents unavailable →
sequential.

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
