---
name: reconfigure
description: mokata · Change what's wired later — add/remove an integration, switch a backend, change profile. Idempotent, human-gated, reversible.
argument-hint: "[--add TOOL] [--remove TOOL] [--profile minimal|standard|full]"
allowed-tools: Bash, Read
---

# mokata · reconfigure (update your setup any time — you're never locked in)

You are changing what mokata has wired in an **already-initialized** repo: add or remove an
integration, switch a backend, change the profile, or pick up a newly-installed tool. It's the
same guided Q&A as first-run setup, re-runnable any time — **idempotent** (no changes → nothing
written), **human-gated** (you see a current→proposed diff and approve before any write), and
**reversible** (removing an integration leaves no residue). mokata **detects → recommends → runs
with approval**; it **never silently installs** a third-party tool.

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

## 2. Show the current setup + re-detect (read-only)

```bash
eval "$ENGINE status --path ."
eval "$ENGINE detect --path ."     # re-detect — catches anything newly installed
```

## 3. Ask what to change

Ask the user what they want to change — add/remove an integration (e.g. wire a now-installed
Postgres or Obsidian, or unwire one), switch a backend setting, or change the profile. For
anything detected-but-not-installed, **recommend** the install command (e.g.
`pip install 'mokata[postgres]'`) and let them run it — **never install it for them**.

## 4. Preview the diff + human gate

Show the current→proposed diff and ask for an explicit yes. Re-running with no changes is a
**no-op** — say so and stop. Do **not** write without approval.

## 5. Apply (only after approval)

```bash
# switch the profile:
eval "$ENGINE reconfigure --profile full --yes --path ."
# add a now-installed integration (only wired if detected; absent → recommended):
eval "$ENGINE reconfigure --add postgres --yes --path ."
# cleanly remove one (reversible — no residue):
eval "$ENGINE reconfigure --remove obsidian --yes --path ."
# switch a backend setting:
eval "$ENGINE reconfigure --set tools.sqlite.config.path=mem/custom.db --yes --path ."
```

(The `reconfigure` MCP tool does the same from inside Claude Code, but it is PROPOSE-ONLY: it
returns the diff plus a `proposal_id` and writes nothing. `approve=true` does **not** apply it —
consent is not something you can type. Ask the user to run `mokata approve <proposal-id>` in their
terminal, then re-call `reconfigure` with that `proposal_id` to apply it once.)

## 6. Report

Tell the user what changed (or that nothing did), and remind them to restart Claude Code if the
harness wiring changed so the SessionStart briefing reflects the new stack.
