---
name: resume
description: mokata · Pick up where you left off — preview where a run resumes; the gates still apply (read-only).
argument-hint: "[run-id]"
allowed-tools: mcp__mokata__sessions, Bash, Read
---

# mokata · resume (continue a run — gates hold)

A **read-only** preview of where a run resumes: the phase it stopped at and the gate that
still applies. mokata **never auto-runs the pipeline** — resuming re-hydrates context; every
gate still fires. Use this when reopening a repo with work in progress.

## How to run

**See the runs first** — call the **`sessions`** MCP tool (server `mokata`) to list past +
active runs (id, phases passed, resume point). Pick the one to continue (default: the
active/most-recent).

**Then preview the resume point** — resolve the bundled engine (read `~/.mokata/plugin-root`
→ `ROOT`, or a `mokata` CLI on PATH), then:

```bash
PY="$(command -v python3 || command -v python)"
ENGINE="PYTHONPATH=\"$ROOT/src\" \"$PY\" -m mokata"
eval "$ENGINE resume --path ."            # the active/most-recent run
eval "$ENGINE resume <run-id> --path ."   # a specific run from `sessions`
```

It prints the resume phase and the gate that applies. To continue, run that phase
(`/mokata:<phase>` or `mokata enter <phase>`) — the gate holds. Degrades clean: with no run
to resume it says so. Read-only; it never mutates a run.
