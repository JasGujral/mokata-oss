---
name: watch
description: mokata · Open the live, clickable dashboard of the run — parallel lanes + pipeline + gate feed (read-only).
argument-hint: "[--run <id>]"
allowed-tools: mcp__mokata__watch, Bash, Read
---

# mokata · watch (the clickable dashboard of what's happening now)

A **self-contained** local HTML dashboard of the active run — the **parallel lanes** (click a
lane to drill into its ledger rows), the **7-phase pipeline**, and a **bounded** tail of the
gate/decision feed. No network, no server, no external assets. It lives under gitignored
`.mokata/temp_local/` and is **read-only** — it only *reflects* run-state + the audit ledger.

## How to run

**Prefer the MCP tool** (server `mokata`):

- Call the **`watch`** tool (pass `run` to target a specific run id). It returns:
  - `enabled: true` + `path` → tell the user the dashboard was written and give the path to
    open in a browser; or
  - `enabled: false` + `note` → the dashboard tier is off; relay the note (enable it with
    `mokata config set settings.ux.progress dashboard`).

**CLI fallback** — resolve the bundled engine (read `~/.mokata/plugin-root` → `ROOT`, or a
`mokata` CLI on PATH), then:

```bash
PY="$(command -v python3 || command -v python)"
ENGINE="PYTHONPATH=\"$ROOT/src\" \"$PY\" -m mokata"
eval "$ENGINE watch --once --path ."          # write a single snapshot (add --open to open it)
```

It respects `settings.ux.progress` (default `terminal` writes no HTML) and degrades clean with
no active run (a friendly empty dashboard). It never writes durable state.
