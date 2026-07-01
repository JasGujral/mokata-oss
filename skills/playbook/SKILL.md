---
name: playbook
description: mokata · Run the full v1 story end-to-end on this repo (integration check).
---

> **mokata Agent Skill.** This is mokata's `playbook` capability, surfaced so Claude can engage it
> automatically when the moment fits. It runs the SAME protocol as the `/mokata:playbook` command,
> from one shared source — follow that protocol directly here; do not hand off to a parallel
> flow. mokata's non-negotiables still hold: durable writes are **human-gated** (preview, then
> explicit approval), and this capability's own gate is never silently skipped.

# mokata · playbook (the whole story, end-to-end)

Drive mokata's full v1 pipeline end-to-end on this repo as an **integration check** — every
phase and gate in sequence. `--parallel` runs it with subagents (degrading to sequential when
the harness has none); `--dense` compresses sub-agent hand-backs (F4 output density). The
gates and audit ledger apply throughout.

## How to run

Resolve the bundled engine (read `~/.mokata/plugin-root` → `ROOT`, or a `mokata` CLI on PATH):

```bash
PY="$(command -v python3 || command -v python)"
ENGINE="PYTHONPATH=\"$ROOT/src\" \"$PY\" -m mokata"
eval "$ENGINE playbook --path ."                       # sequential (default)
eval "$ENGINE playbook --parallel --path ."            # parallel subagents (degrades clean)
eval "$ENGINE playbook --parallel --fanout --dense --path ."
```

It announces the active stage (banner) at the start and on completion, and reports each
phase's result. Watch a parallel run live with `/mokata:progress --lanes` or `/mokata:watch`.
