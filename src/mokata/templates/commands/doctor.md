---
name: doctor
description: mokata · Check mokata's install from inside Claude Code — plugin route (uv, pinned package, versions, MCP server, hooks) or CLI route (wiring + config).
allowed-tools: Bash, Read
---

# mokata · /doctor

Report whether mokata is healthy on THIS machine and, if not, the one fix. Read-only: nothing is
installed, written or uploaded. Run the commands, show their output verbatim, then name the first
failing check's `fix:` line to the user. Do not run any fix for them.

## 1. Which route is this?

- **Plugin route** — `~/.mokata/plugin-root` exists (the plugin's SessionStart hook records it).
  Run mokata through the plugin's own launcher, which always runs the plugin's pinned version:

  ```bash
  ROOT="$(cat ~/.mokata/plugin-root)"
  sh "$ROOT/src/mokata/hooks/mokata-launch" doctor --plugin --plugin-root "$ROOT"
  ```

  It checks: **uv found** · **package resolved** (the pinned `mokata[full]==<version>` environment)
  · **version match** (plugin ↔ MCP pin ↔ package) · **MCP server running** · **hooks firing**.
  If this repo has a `.mokata/`, also run
  `sh "$ROOT/src/mokata/hooks/mokata-launch" doctor --path .` for the repo's own configuration.

- **CLI route** — `mokata` is on PATH (`command -v mokata`). Run `mokata doctor --wiring --path .`
  (are the gates wired and current?) and, in an initialized repo, `mokata doctor --path .`.

If both are true, run both and say which route serves this repo (the CLI route's hooks take over
when both are wired).

## 2. Nothing runs at all?

If the launcher prints `cannot run the plugin's mokata`, uv is missing: give the user the install
line it printed for their OS and tell them to restart Claude Code afterwards.

## Gate (check)
Read-only diagnosis; it changes nothing and makes no network call of its own (the launcher's first
run may let uv fetch the pinned package, which the plugin's MCP server needs anyway).

## Standalone
This command runs on its own — no upstream pipeline phase is required. It applies only its own gate above, and never silently skips a gate of a phase you did run.
