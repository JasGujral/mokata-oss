# How-to: set the execution mode

**Every implementation asks first.** At the start of any implementation run (the `develop`
skill, `playbook`, `exec`) mokata presents the choice — **parallel subagents vs. the
sequential gated flow** — and never fans out without your pick. The default is the
sequential gated flow (lowest cost), and it's asked **once per run**, not per sub-task.

```bash
mokata exec                        # sequential gated flow (default, lowest cost)
mokata exec --parallel             # parallel subagents (fresh-context isolation + review)
mokata exec --parallel --isolation # explicit isolation + two-stage review (E2/E3)
mokata exec --parallel --fanout    # concurrent fan-out (tasks at once)
```

Run the pipeline in a mode:

```bash
mokata playbook              # sequential
mokata playbook --parallel   # parallel (degrades to sequential without a harness)
mokata playbook --parallel --fanout
```

## Save a preference (skip the prompt)

Power users can avoid the per-run prompt with a saved default in the manifest:

```bash
mokata config set settings.execution.default sequential   # always sequential, no prompt
mokata config set settings.execution.default parallel      # always parallel (isolation)
mokata config set settings.execution.default ask           # the default — ask each run
```

`ask` is the default: always offered, never friction-by-design. A saved `sequential` /
`parallel` honors the choice without re-prompting.

What parallel guarantees: a **token/cost estimate before running** (shown when the choice
is offered), staying inside the existing gates + audit ledger + token budget, **every
subagent decision logged**, and a clean **degrade to sequential** — with a clear message —
when the harness has no subagents. See
[execution modes](../concepts/execution-modes.md).
