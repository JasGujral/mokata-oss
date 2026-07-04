---
name: govern
description: mokata · See the governed state — rules, memory-by-kind, read/write ratio, pending proposals (read-only).
---

> **mokata Agent Skill.** This is mokata's `govern` capability, surfaced so Claude can engage it
> automatically when the moment fits. It runs the SAME protocol as the `/mokata:govern` command,
> from one shared source — follow that protocol directly here; do not hand off to a parallel
> flow. mokata's non-negotiables still hold: durable writes are **human-gated** (preview, then
> explicit approval), and this capability's own gate is never silently skipped.

# mokata · govern (a clickable view of the governed state)

A **self-contained**, read-only view of what mokata is governing: the always-on **rules**
tier (rules + guardrails, budget-capped), **memory grouped by kind**, the **read/write
ratio**, and any **pending self-healing proposals**. Same engine and constraints as `watch`
(inline CSS, no network/server/assets, under gitignored `.mokata/temp_local/`). It **surfaces**
the gated `mokata memory edit` manage path for each item — it never writes from the view.

## How to run

**Prefer the MCP tool** (server `mokata`):

- Call the **`govern`** tool. It writes the governance HTML and returns its `path` plus a
  structured summary (`version`, `profile`, `rules`, `reads`/`writes`/`ratio`, `proposals`).
  Tell the user the dashboard was written, give the path to open, and summarize the counts —
  especially any **pending proposals** that need a decision.

**CLI fallback** — resolve the bundled engine (read `~/.mokata/plugin-root` → `ROOT`, or a
`mokata` CLI on PATH), then:

```bash
PY="$(command -v python3 || command -v python)"
ENGINE="PYTHONPATH=\"$ROOT/src\" \"$PY\" -m mokata"
eval "$ENGINE govern --path ."                 # write the governance dashboard (add --open)
```

It is **read-only** and degrades clean (no/empty memory → an empty view). To act on a
proposal, use the **human-gated** `mokata memory edit` (or `/mokata:onboard`) — never from the
dashboard.
