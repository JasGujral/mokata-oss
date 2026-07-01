---
name: enter
description: mokata · Enter the pipeline at a phase — only that phase's gates apply (no upstream phase forced).
argument-hint: "<phase> [--to <phase>]"
allowed-tools: Bash, Read
---

# mokata · enter (jump into the pipeline at a phase)

Start the governed pipeline at a chosen phase instead of from the top — run just the
completeness gate on a hand-written spec, or jump straight to `test`/`develop` for existing
code. Upstream phases aren't forced, but **every phase you run applies its own gate** (a gate
of a phase you did run is never silently skipped).

## How to run

Resolve the bundled engine (read `~/.mokata/plugin-root` → `ROOT`, or use a `mokata` CLI on
PATH):

```bash
PY="$(command -v python3 || command -v python)"
ENGINE="PYTHONPATH=\"$ROOT/src\" \"$PY\" -m mokata"
eval "$ENGINE enter <phase> --path ."             # start at <phase>
eval "$ENGINE enter <phase> --to <phase> --path ."  # run a span of phases
```

`<phase>` is one of the pipeline phases (e.g. `spec`, `test`, `develop`, `review`, `ship`).
The output names which gates apply at the entry point. Then drive the phase itself with the
matching `/mokata:<phase>` slash command — the gate holds. Read-only planning; it writes
nothing on its own.
