---
name: tour
description: mokata · A 60-second read-only demo — a graph query, a memory recall, and a gate catch. Writes nothing.
argument-hint: ""
allowed-tools: Bash, Read
---

# mokata · tour (see it work in 60 seconds)

You are giving the user a short, **read-only** demo of what mokata does — the **memory + seatbelt
for their AI coding agent**. It writes **nothing** to their repo: the memory recall runs in an
in-memory store and the gate-catch only *scans* a sample line. Safe to run anytime.

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

## 2. Run the tour (read-only)

```bash
eval "$ENGINE tour --path ."
```

Show the output **verbatim**. It walks three things on a tiny sample:

1. **Graph query** — ask the codebase a structural question (`mokata query callers <symbol>`)
   instead of grepping.
2. **Memory recall** — mokata remembers your project's decisions, so the agent stops re-asking.
3. **Gate catch** — every durable write is scanned; a secret is a HARD block approval can't
   override. Nothing is committed.

(The same demo is available as the read-only `tour` MCP tool.)

## 3. Next step

After the demo, point the user at the real thing: `/mokata:setup` (or `mokata init`) to wire
mokata into THIS repo, then `/mokata:brainstorm` to start their first governed change.
