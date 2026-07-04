---
name: chain
description: mokata · Plan a manual chain of skills to run in order — each step keeps its own gate.
argument-hint: "<skill> <skill> [...]"
allowed-tools: Bash, Read
---

# mokata · chain (compose skills, gates intact)

Plan a manual sequence of skills to run in order — e.g. `spec test develop review`. Each step
in the chain **keeps its own gate**, so composing skills never bypasses governance. This
*plans* the chain (so you see the steps and their gates); you then run each step with its
`/mokata:<skill>` command.

## How to run

Resolve the bundled engine (read `~/.mokata/plugin-root` → `ROOT`, or a `mokata` CLI on PATH):

```bash
PY="$(command -v python3 || command -v python)"
ENGINE="PYTHONPATH=\"$ROOT/src\" \"$PY\" -m mokata"
eval "$ENGINE chain <skill> <skill> [...] --path ."   # e.g. chain spec test develop
```

The output lists each step with the gate it applies. Walk the chain by invoking each
`/mokata:<skill>` in turn — the gate of every phase you run holds. Read-only planning; it
writes nothing.
