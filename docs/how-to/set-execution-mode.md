# How-to: set the execution mode

mokata asks per run which mode to use; the default is the sequential gated flow.

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

What parallel guarantees: a **token/cost estimate before running**, staying inside the
existing gates + audit ledger + token budget, **every subagent decision logged**, and a
clean **degrade to sequential** when subagents are unavailable. See
[execution modes](../concepts/execution-modes.md).
