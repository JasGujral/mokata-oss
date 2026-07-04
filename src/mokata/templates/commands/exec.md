---
name: exec
description: mokata · Show or choose the execution mode for a run — sequential (default) or parallel subagents.
argument-hint: "[--parallel] [--isolation] [--fanout]"
allowed-tools: Bash, Read
---

# mokata · exec (sequential or parallel?)

Choose how a run executes: the **sequential gated flow** (the default, lowest-cost path) or
**parallel subagents** (fresh-context isolation and/or concurrent fan-out). Parallel modes
surface a token/cost estimate **before** running, stay under the gates + audit ledger + token
budget, and **degrade to sequential** if the harness has no subagents. mokata never fans out
without an explicit choice.

## How to run

Resolve the bundled engine (read `~/.mokata/plugin-root` → `ROOT`, or a `mokata` CLI on PATH):

```bash
PY="$(command -v python3 || command -v python)"
ENGINE="PYTHONPATH=\"$ROOT/src\" \"$PY\" -m mokata"
eval "$ENGINE exec --path ."                          # show the resolved mode (honors the saved default)
eval "$ENGINE exec --parallel --isolation --path ."   # fresh-subagent isolation + two-stage review
eval "$ENGINE exec --parallel --fanout --path ."      # concurrent fan-out
```

With no flags it reports the mode resolved from `settings.execution.default` (asking once if
that's `ask`). Watch a parallel run with `/mokata:progress --lanes`. This only *selects* the
mode for the run; the gates still apply to every step.
