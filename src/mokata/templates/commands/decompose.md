---
name: decompose
description: mokata · Split the approved spec into independent subtasks + a dependency plan, then confirm to run.
argument-hint: "[--run]"
allowed-tools: mcp__mokata__decompose, Bash, Read
---

# mokata · decompose (split the work, confirm, then fan out)

The fan-out *engine* already exists (isolation, two-stage review, the parallel-vs-sequential
ask, the cost estimate). What this adds is **splitting the approved work into the tasks it
runs**: from the emitted spec's acceptance criteria it proposes **one independent subtask per
AC**, infers **dependencies** (subtasks that touch the same symbol/file are kept ordered), and
recommends sequential vs parallel. The split is **read-only until you confirm it** — and it
**never silently parallelizes** work that might be dependent.

## 1. See the proposed split (read-only)

**Prefer the MCP tool** (server `mokata`): call the **`decompose`** tool → render its `block`
(the legible split + dependency plan). It returns `subtasks`, `dependency_count`,
`fanout_safe`, `recommended_parallel`, and `warnings`. Nothing runs.

**CLI fallback** — resolve the bundled engine (read `~/.mokata/plugin-root` → `ROOT`, or a
`mokata` CLI on PATH):

```bash
PY="$(command -v python3 || command -v python)"
ENGINE="PYTHONPATH=\"$ROOT/src\" \"$PY\" -m mokata"
eval "$ENGINE decompose --path ."          # show the read-only split
```

If there's no emitted spec with ACs, it says so — run `/mokata:spec` first.

## 2. Confirm and run (human-gated)

Present the split to the user and let them **confirm, edit, or reject**. Nothing fans out
until they confirm. On approval, `--run` drives the **existing** flow — it surfaces the cost
estimate, asks parallel-vs-sequential (default **sequential**), then runs with isolation +
two-stage review, degrading to sequential when subagents are unavailable:

```bash
eval "$ENGINE decompose --run --path ."     # gated confirm → resolve mode → run_tasks
```

If dependencies are present (or independence is unverified because no code graph is wired),
concurrent fan-out is **withheld** — isolated tasks run in declared order — and you're told
why. The confirm decision is logged to the audit ledger.
