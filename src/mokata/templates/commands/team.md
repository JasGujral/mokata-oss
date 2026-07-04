---
name: team
description: mokata · Zero-setup team sync — `team join` wires a new teammate (shared stack + shared memory + vault + onboard) in ONE guided command; or use the individual adopt/connect steps. Human-gated, secret-scanned; mokata hosts nothing.
argument-hint: "join <stack-or-repo> [--dsn-env MOKATA_PG_DSN] [--vault <ref>] | status | adopt <src> [--force] | connect --dsn-env <ENV> | disconnect"
allowed-tools: Bash, Read
---

# mokata · team (collaborate on the governed brain in minutes)

A team shares one **governed stack** — the same config/guardrails, the design+spec vault, and an
optional shared memory — so everyone works from the same brain. `team join` gets a new teammate
there in **one guided command**; every writing step is **human-gated** and reuses mokata's
existing primitives (nothing new to host or run).

> **Honest scope:** mokata runs **no hosted service**. "Hosted sync" means the team's **own
> managed Postgres** (Supabase / Neon / RDS — just a DSN). mokata **never stores the DSN secret**;
> only the env-var *name* is recorded, and the DSN stays in your environment.

## `team join <source>` — wire a new teammate in one command

The zero-to-wired path. It runs five steps **in order**, each **confirmable** and
**degrade-clean** (a step whose source/backend/driver is absent is **skipped with a note**, never
a blocker):

1. **adopt** the governed stack (config + rules/guardrails + the vault & shared-memory *pointers*) — secret-scanned + human-gated;
2. **connect** shared memory at your managed Postgres (`--dsn-env`; the DSN stays in your env, never stored) — skipped, staying on the local floor, when no DSN/driver is present;
3. **pull** the shared design/spec vault (`--vault <repo-or-dir>`) — secret-scanned; skipped when no ref/vault is given;
4. **onboard** the project knowledge — hands you `/mokata:onboard` (the guided capture);
5. **verify** with `mokata doctor` + a **"here's what you're now wired to"** summary (stack, shared-memory backend, vault, and anything pending/skipped).

```bash
export MOKATA_PG_DSN='postgresql://…@your-managed-host/db'   # optional — else stay local
mokata team join ../teammate-repo --dsn-env MOKATA_PG_DSN --vault ../teammate-repo
mokata team join ./shared/mokata-stack.json --yes            # scripted / non-interactive
```

It's **idempotent** (re-joining converges — no duplicate writes) and **reversible** (`mokata team
disconnect` undoes the shared-memory pointer; re-import a prior stack to undo config). Under the
hood it is exactly the individual steps below — nothing new to host or run.

## `team adopt <source>` — pull a teammate's stack (one gated step)

`source` is a teammate's exported stack file (`mokata-stack.json`) or a repo/dir that holds one.
mokata **secret-scans the shared content first** (it's untrusted — like a memory import / vault
pull), then **human-gates** applying it as your config. Idempotent (re-adopting the same stack
changes nothing) and reversible (an audited config write).

```bash
mokata team adopt ../teammate-repo            # or a path to mokata-stack.json
mokata team adopt ./shared/mokata-stack.json --force   # overwrite an existing config
```

It reports what it wired (config, the vault that travels with the repo, and the shared-memory
**pointer** — the env-var name to export, never a DSN).

## `team connect --dsn-env <ENV>` — bring your own managed Postgres

Point shared **memory** + **sessions** at your managed Postgres via an **env-var DSN**. It records
only the env-var *name*; the DSN secret never enters the manifest.

```bash
export MOKATA_PG_DSN='postgresql://…@your-managed-host/db'   # your env, never committed
pip install 'mokata[postgres]'                               # the optional driver
mokata team connect --dsn-env MOKATA_PG_DSN                  # gated; wires memory + sessions
```

**Degrade-clean:** no driver or no DSN exported → mokata says so clearly and falls back to the
local SQLite memory floor + the local session transport until you install the driver / export the
DSN. `mokata team disconnect` reverses it.

## `team status` (read-only)

Shows whether you're local-only or pointed at a managed Postgres (and whether it's active).

## Who can set a guardrail?

mokata doesn't add its own auth — it leans on the **repo's and the database's** access control.
Anyone who can commit the `.mokata/` config (or write the shared DB) can change a guardrail; every
write is **attributed** (provenance records *who added what*) and **audited** in the ledger. Use
your Git host's branch protection / DB grants for who-can-write; mokata records and governs.
