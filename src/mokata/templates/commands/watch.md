---
name: watch
description: mokata · Open the live, clickable dashboard of the run — the IDE's Sessions view of its gate feed and changes (pipeline on Plan), re-using the open IDE; plain `mokata watch` prints the parallel lanes (read-only).
argument-hint: "[--run <id>]"
allowed-tools: Bash, Read
---

# mokata · watch (the run, live, in the IDE)

Watching a run in a browser IS the `mokata ide` window's **Sessions** screen: the run's session
with its changed files and diffs, the gates it fired or that blocked it, its spend, and the
needs-you queue. There is one browser window, not two — if a `mokata ide` window is already
serving this repository it is **re-used** (re-focused on this run's session), never a second
server.

## How to run

Run it through Bash (pass `--run <id>` when the user named a run):

```bash
mokata watch --open --path .
```

It prints `mokata ide opened: <link>` (a new window) or `mokata ide re-focused: <link>` (the
open one), followed by the run and session it focused, and opens the user's browser on it. Give
the user that one line. If it also prints `no browser was opened` (a headless box), the link is
what they open themselves — following it lands on **Sessions**. Do not open anything yourself.

On the plugin route there is no `mokata` on PATH: run it through the plugin's pinned launcher —
`sh "$(cat ~/.mokata/plugin-root)/src/mokata/hooks/mokata-launch" watch --open --path .`
(the same `mokata[full]` version as the plugin's MCP server). If it prints an install line, pass
that on instead.

Without `--open`, `mokata watch` prints the run's **terminal lanes** (one line per parallel
subagent) — the same view as `/mokata:progress --lanes`; `settings.ux.progress` (`terminal` |
`ide` | `both`) picks what plain `mokata watch` shows. For the lanes as data, call the
**`lanes`** MCP tool; for the session list as data, **`ide_sessions`**.

## Gate (check)
Read-only: it writes nothing to the repository and never mutates a run. It starts the local IDE
server (loopback only, exits when idle) when none is running.

## Standalone
This command runs on its own — no upstream pipeline phase is required. It applies only its own
gate above, and never silently skips a gate of a phase you did run.
