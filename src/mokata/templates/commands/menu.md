---
name: menu
description: mokata · The command palette — every /mokata command and bundled skill on one screen, with gate markers. Read-only.
argument-hint: ""
allowed-tools: Bash, Read
---

# mokata · menu (the whole surface, one screen)

Show the user the **command palette**: every shipped `/mokata:` command and every bundled skill,
each with a one-line description and a marker for whether it has a **gate**. It is **read-only** —
it just enumerates the installed command/skill files and prints them; it writes nothing.

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

## 2. Print the palette (read-only)

```bash
eval "$ENGINE menu"
```

Show the output **verbatim**. It renders two grouped tables:

1. **Commands** — every `/mokata:<name>` command, its one-line description, and a `✓` gate marker
   for the ones that apply a gate (a human-approval or verifiable check).
2. **Skills** — the bundled skills Claude can auto-engage when the moment fits, same layout.

The list is derived from the installed command/skill files, so it is always complete and never
drifts. The palette itself has **no gate** — it only reads and formats.

## 3. Next step

Point the user at whatever they want to run next — e.g. `/mokata:setup` to wire mokata into the
repo, `/mokata:brainstorm` to start a governed change, or any command they spotted in the list.
