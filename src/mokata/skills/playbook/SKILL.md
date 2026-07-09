---
name: playbook
description: mokata · Run the full v1 story end-to-end on this repo (integration check).
when_to_use: Engage when the user EXPLICITLY asks to run mokata's full pipeline end-to-end as an integration check, when they want to smoke-test the whole brainstorm-to-ship story on a repo, or when validating a fresh install performs a complete run. Do NOT engage implicitly or on your own initiative — it is a long, multi-write flow — and do NOT run it on a dirty working tree without confirmation.
---

> **mokata Agent Skill.** This is mokata's `playbook` capability, surfaced so Claude can engage it
> automatically when the moment fits. It runs the SAME protocol as the `/mokata:playbook` command,
> from one shared source — follow that protocol directly here; do not hand off to a parallel
> flow. mokata's non-negotiables still hold: durable writes are **human-gated** (preview, then
> explicit approval), and this capability's own gate is never silently skipped.

⛭ mokata playbook active — gate: a long multi-write flow — run on explicit request only; confirm on a dirty tree

# mokata · playbook (the whole story, end-to-end)

Drive mokata's full v1 pipeline end-to-end on this repo as an **integration check** — every
phase and gate in sequence. `--parallel` runs it with subagents (degrading to sequential when
the harness has none); `--dense` compresses sub-agent hand-backs (F4 output density). The
gates and audit ledger apply throughout.

## How to run

Resolve the bundled engine (read `~/.mokata/plugin-root` → `ROOT`, or a `mokata` CLI on PATH):

```bash
PY="$(command -v python3 || command -v python)"
ENGINE="PYTHONPATH=\"$ROOT/src\" \"$PY\" -m mokata"
eval "$ENGINE playbook --path ."                       # sequential (default)
eval "$ENGINE playbook --parallel --path ."            # parallel subagents (degrades clean)
eval "$ENGINE playbook --parallel --fanout --dense --path ."
```

It announces the active stage (banner) at the start and on completion, and reports each
phase's result. Watch a parallel run live with `/mokata:progress --lanes` or `/mokata:watch`.

## Rationalizations — stop if you catch yourself thinking any of these

| Excuse | Reality |
|---|---|
| "This seems like a good moment to run the whole flow." | Run only on an EXPLICIT request — never implicitly, it's a long multi-write flow. |
| "The tree's a bit dirty but it'll be fine." | Confirm on a dirty tree before running — uncommitted work is at risk. |
| "It's just an integration check — writes don't need gating." | Every durable write it performs is still human-gated. |

## Verification — confirm each before you claim this skill is done

Evidence, not "seems right" — check every box or say which is unmet and why:

- [ ] the run was EXPLICITLY requested by the human
- [ ] a dirty working tree was confirmed before starting
- [ ] every durable write went through its gate
- [ ] it ran on a repo the user accepts test writes in

## Contract

**CAN**
- run the full v1 story end-to-end as an integration check

**MUST NOT**
- run implicitly — explicit user request only; or run on a dirty tree without confirmation (advisory)
- skip the human gate on any durable write it performs (gate: write-gate)

**DEPENDS ON**
- the full toolchain and a repo the user accepts test writes in (advisory)

> Grounding: `(gate: …)` boundaries are enforced by that gate in code; `(advisory)` ones are protocol discipline this skill follows, not a hard block.
