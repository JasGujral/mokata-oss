---
name: mcp-repair
description: mokata · Repair the mokata MCP server when its tools aren't connecting — check the registration, fix it, and tell you the one step left (restart Claude Code). Repair, not first-time setup.
when_to_use: Engage when the user reports mokata's MCP server or tools aren't working — e.g. "mokata mcp isn't working / isn't connected / failed to start", "the mokata MCP server won't start", "mokata's tools are missing / don't show up / aren't available", or "/mcp doesn't list mokata". This REPAIRS an already-installed mokata by re-checking and re-writing its Claude Code registration, then tells the user to restart Claude Code. Do NOT engage for first-time setup or initial wiring — use /mokata:setup for that; this is repair of an existing install, not initial installation.
allowed-tools: Bash, Read
---

# mokata · mcp (repair the MCP server registration)

The user thinks mokata's MCP tools aren't connecting. Your job: **diagnose → repair → re-verify →
report**, using ONLY the existing `mokata mcp` CLI (it already does the real handshake and the
merge-safe, idempotent registration). Then be honest about the one thing you cannot do from inside
the session — **restart Claude Code** — because a newly-registered MCP server only loads on the
next Claude Code launch.

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

## 2. Diagnose (read-only)

Run the real `initialize` handshake against whatever is currently registered:

```bash
eval "$ENGINE mcp status"
```

- **`mokata-mcp: CONNECTED ✓`** → nothing is broken. Tell the user the server is already
  connected and stop (no write). If they still don't see the tools, the fix is almost always a
  **restart of Claude Code** — say so.
- **Any `NOT CONNECTED ✗ (…)` / `NOT REGISTERED ✗`** → note the specific cause the command
  printed (e.g. `not_registered`, `command_not_found`, `sdk_absent`, `timeout`) and go to step 3.
  - If the cause is **`sdk_absent`**, the repair is NOT a re-registration — the MCP SDK needs
    Python ≥ 3.10. Relay the command's own one-line fix (`pip install -U mokata` on ≥ 3.10) and
    stop; do not pretend `install` will fix it.

## 3. Repair (a durable write — be transparent)

`mokata mcp install` **writes/repairs** the Claude Code registration (`.mcp.json`, or
`~/.claude.json` for user scope), resolving `mokata-mcp` to an absolute path. It is **merge-safe**
(never touches your other MCP servers or keys) and **idempotent** (safe to run again). Tell the
user you're about to repair the registration, then run it:

```bash
eval "$ENGINE mcp install"
# user-scope registration instead of this project only:
# eval "$ENGINE mcp install --scope user"
```

## 4. Re-verify

Confirm the repair actually took, using the same handshake:

```bash
eval "$ENGINE mcp status"
```

## 5. Report exactly what happened — and the one step left

In one short block, tell the user plainly:

- **what you found** (the specific status before the repair),
- **what you did** (repaired the registration, or nothing if it was already connected / SDK-absent),
- **the result of the re-check** (connected, or the remaining specific failure + its fix), and
- **the step only they can take:** **restart Claude Code** so it loads the (re-)registered server —
  Claude cannot restart itself from inside the session. After the restart, `/mcp` should list
  **mokata** as connected.

If the re-check still fails after a good `install`, do NOT loop — report the exact failure code and
the one-line fix the command gave, and let the user act on it.
