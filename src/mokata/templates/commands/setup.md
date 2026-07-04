---
name: setup
description: mokata · Guided first-run setup — detect your tools, ask what to wire, then wire it with your approval. Human-gated.
argument-hint: "[profile]   # minimal | standard | full (default: ask)"
allowed-tools: Bash, Read
---

# mokata · setup (the magical first run — from zero to wired in minutes)

You are giving the user a **guided first run**: detect what's installed, ask which integrations
to wire and which profile to use, then wire it all — **with their explicit approval at every
durable step**. mokata **detects → recommends → runs with approval**; it **never silently
installs** a third-party tool. Writing config / harness wiring is a durable write, so this is
**human-gated**: preview → approval → apply.

## 1. Resolve the engine

`${CLAUDE_PLUGIN_ROOT}` is NOT expanded inside command bodies, so discover the bundled engine:

- Read the cached plugin root: `cat ~/.mokata/plugin-root` → `ROOT`.
- If that file is missing/empty, search the Claude Code plugins directory for a `mokata` plugin
  containing `src/mokata/__init__.py` and set `ROOT` to it. (If a `mokata` CLI is on PATH, use
  it directly.)
- Build the engine command with the **absolute interpreter**:

  ```bash
  PY="$(command -v python3 || command -v python)"
  ENGINE="PYTHONPATH=\"$ROOT/src\" \"$PY\" -m mokata"
  ```

## 2. Detect the environment (read-only)

Show the user what's actually installed — graph backends, memory backends, Postgres / Obsidian /
vector — so the choices are grounded:

```bash
eval "$ENGINE detect --path ."
eval "$ENGINE init --profile standard --preview --path ."   # detected tools + what each profile wires
```

## 3. Ask the profile + what to wire

Ask the user (briefly explaining the trade-off):

- **profile** — `minimal` (engine only), `standard` (engine + graph + memory on lean local
  defaults), or `full` (every known provider).
- **which detected integrations to wire** — e.g. a present Obsidian vault or Postgres
  (`MOKATA_PG_DSN`). For anything detected-but-not-installed, **recommend** the install command
  (e.g. `pip install 'mokata[postgres]'`) and let the user run it — **never install it for them**.
- **wire the harness?** — copy the slash commands, register the `mokata` MCP server, and wire the
  SessionStart + secret-guard hooks + the stage badge.

## 4. Human gate

Show exactly what will be written/wired and ask for an explicit yes. Do **not** proceed without
it. If they decline, stop — and don't ask again.

## 5. Apply (only after approval)

```bash
# scaffold config for the chosen profile:
eval "$ENGINE init --profile <profile> --yes --path ."
# wire a detected integration into the manifest (example — Postgres memory via an env-var DSN):
# eval "$ENGINE config set capabilities.memory_store.fallback '[\"postgres\",\"sqlite\"]' --yes --path ."
# wire mokata into Claude Code (commands + MCP + hooks + badge):
# eval "$ENGINE setup claude --yes --path ."
```

The CLI's own interactive wizard (`mokata init` in a terminal, or `mokata init --wizard`) does
the same detect → ask → wire flow end-to-end; from inside Claude Code you drive the gated steps
above.

## 6. The 30-second recap + next step

After applying, run `eval "$ENGINE status --path ."` and tell the user, in one short block: what
was detected, what got wired, the graph/memory that's now standing, the 5 starter guardrails (the
constitution), and the **one next step** — `/mokata:brainstorm` to start their first governed
change (or `/mokata:tour` for a 60-second demo). Remind them to restart Claude Code so the
SessionStart briefing reflects the new stack.
