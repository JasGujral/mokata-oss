---
name: mode
description: mokata · Show the run mode (local | team) + the fail-closed team-readiness preflight, or `set local|team`. Team activation is gated; local is the zero-config default.
argument-hint: "[set local|team]"
allowed-tools: Bash, Read
---

# mokata · mode (local | team run mode)

Show or set mokata's **run mode** — a first-class, visible property of every session:

- **local** (the default): zero-config, everything under `.mokata/`, today's behaviour. Requires
  nothing.
- **team**: memory/knowledge/event stores on **shared databases**. Activation is **gated by a
  fail-closed preflight** — missing/incompatible prerequisites block it with a named fix, and the
  shared-DB connection manager + real probe land in **TM.S2**, so `set team` refuses until then.
  Team mode is **never half-activated**.

## 1. Resolve the engine

`${CLAUDE_PLUGIN_ROOT}` is NOT expanded inside command bodies, so discover the bundled engine:

- Read the cached plugin root: `cat ~/.mokata/plugin-root` → `ROOT`. If missing/empty, search the
  Claude Code plugins directory for a `mokata` plugin containing `src/mokata/__init__.py`. (If a
  `mokata` CLI is on PATH, use it directly.)
- Build the engine command with the **absolute interpreter**:

  ```bash
  PY="$(command -v python3 || command -v python)"
  ENGINE="PYTHONPATH=\"$ROOT/src\" \"$PY\" -m mokata"
  ```

## 2. Show or set the mode

```bash
# no argument → show the current mode + the team-readiness preflight
# `set local` / `set team` → change it (team is gated fail-closed; local is the default)
eval "$ENGINE mode ${ARGUMENTS}"
```

Show the output **verbatim**. With no `$ARGUMENTS` it prints the current mode line + the team
preflight (every prerequisite, each blocker with its actionable fix). `set team` **refuses**
fail-closed at this stage and names **TM.S2** as the missing piece — relay that refusal and its
fix; do **not** hand-edit the manifest to force team mode.

## Notes

- **Local-first:** `local` is the default and requires nothing — a fresh repo is already local
  with no `mode` key in the manifest.
- **Human-gated:** switching the committed mode is a durable write and goes through mokata's
  WriteGate (preview → approve → audit), like every other config change.
- **Everywhere:** the mode also shows in the SessionStart bootstrap line, the statusline badge,
  and `mokata doctor` — a session is never ambiguous about which mode it's in.
