---
name: govern
description: mokata · See the governed state — rules, memory-by-kind, read/write ratio, pending proposals (read-only).
when_to_use: Engage when the user wants to see the governed state (active rules, memory by kind, the read/write ratio, or pending self-healing proposals), when they ask what mokata is currently enforcing or remembering, or when reviewing governance before a decision. Do NOT engage to change a rule or memory from the view — it is read-only and edits go through the gated memory path — or to run a pipeline phase.
---

> **mokata Agent Skill.** This is mokata's `govern` capability, surfaced so Claude can engage it
> automatically when the moment fits. It runs the SAME protocol as the `/mokata:govern` command,
> from one shared source — follow that protocol directly here; do not hand off to a parallel
> flow. mokata's non-negotiables still hold: durable writes are **human-gated** (preview, then
> explicit approval), and this capability's own gate is never silently skipped.

⛭ mokata govern active — gate: the governed state is read-only; edits go through the human write gate

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

## Rationalizations — stop if you catch yourself thinking any of these

| Excuse | Reality |
|---|---|
| "I'll fix this memory item straight from the view." | The view is READ-ONLY; edits go through the gated memory-edit path it surfaces. |
| "I'll trigger a heal to tidy things up." | Self-healing writes are not driven from this view. |
| "No MCP tool available — I'll skip it." | Degrade to the CLI; the read-only view still renders. |

## Verification — confirm each before you claim this skill is done

Evidence, not "seems right" — check every box or say which is unmet and why:

- [ ] the view was rendered READ-ONLY (no write performed from it)
- [ ] any edit was routed through the gated memory-edit path
- [ ] pending self-healing proposals are surfaced for a human decision
- [ ] it degraded cleanly when the MCP tool was absent

## Contract

**CAN**
- render the read-only governed-state view (rules, memory by kind, read/write ratio, pending proposals)
- surface the gated memory edit path

**MUST NOT**
- write anything from the view — the edit path it surfaces is the write gate (gate: write-gate)
- trigger self-healing writes (advisory)

**DEPENDS ON**
- the govern MCP tool (degrades to the CLI) (advisory)

> Grounding: `(gate: …)` boundaries are enforced by that gate in code; `(advisory)` ones are protocol discipline this skill follows, not a hard block.
