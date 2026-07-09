---
name: playbook
description: mokata · Run the full v1 story end-to-end on this repo (integration check).
when_to_use: Engage when the user EXPLICITLY asks to run mokata's full pipeline end-to-end as an integration check, when they want to smoke-test the whole brainstorm-to-ship story on a repo, or when validating a fresh install performs a complete run. Do NOT engage implicitly or on your own initiative — it is a long, multi-write flow — and do NOT run it on a dirty working tree without confirmation.
argument-hint: "[--parallel] [--fanout] [--dense]"
allowed-tools: Bash, Read
---

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
