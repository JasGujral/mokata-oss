---
name: progress
description: mokata · Show where the run is — the 7-phase tracker + the parallel-agent lanes (read-only).
argument-hint: "[--lanes] [--run <id>]"
allowed-tools: mcp__mokata__progress, mcp__mokata__lanes, Bash, Read
---

# mokata · progress (where is the run, and what are the agents doing?)

A **read-only** glance at the active run — never mutates anything. Two views:

1. **Phase tracker** — the ordered 7 phases marked done / current / pending, with the
   `[done/total]` count and what's next.
2. **Parallel-agent lanes** — when mokata runs subagents in parallel, one lane per concurrent
   agent with its state (**running / done / blocked / degraded**).

## How to run

**Prefer the MCP tools** (server `mokata`, no shell needed):

- Call the **`progress`** tool → render its `block` (the phase tracker).
- When agents are fanning out — or the user asked for `--lanes` — also call the **`lanes`**
  tool → render its `block` (the per-subagent parallel lanes). Pass `run` to target a specific
  run id; both are READ-ONLY.

**CLI fallback** — resolve the bundled engine (read `~/.mokata/plugin-root` → `ROOT`, or use a
`mokata` CLI on PATH), then:

```bash
PY="$(command -v python3 || command -v python)"
ENGINE="PYTHONPATH=\"$ROOT/src\" \"$PY\" -m mokata"
eval "$ENGINE progress --path ."            # phase tracker
eval "$ENGINE progress --lanes --path ."     # + parallel lanes  (add --run <id> to target one)
```

Both degrade clean: with no active run they return a friendly empty view — surface it, never
invent it.
