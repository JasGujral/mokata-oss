# Team setup — a shared governed stack

A new teammate can go from zero to the **same governed brain** in **one command** —
`mokata team join` — instead of a manual runbook: the same config and guardrails, shared memory,
the design+spec vault, and the project knowledge, all wired in order. Every writing step is
**human-gated** and built entirely on mokata's existing primitives.

!!! note "Honest scope — mokata hosts nothing"
    mokata is local-first and OSS; it **runs no hosted service**. "Hosted sync" means the team's
    **own managed Postgres** (Supabase / Neon / Amazon RDS — just a connection string). mokata
    **never stores the DSN secret**: it records only the *name* of the environment variable that
    holds it, and the DSN stays in your environment.

## The one-command path — `mokata team join`

One person exports the governed stack (`mokata export`) and commits it (or shares the file). Every
new teammate then runs a single guided command (also `/mokata:team join` inside Claude Code):

```bash
# optional — export your team's managed-Postgres DSN so shared memory activates (else stay local):
export MOKATA_PG_DSN='postgresql://…@your-managed-host/db'

mokata team join ../teammate-repo --dsn-env MOKATA_PG_DSN --vault ../teammate-repo
mokata team join ./shared/mokata-stack.json --yes         # scripted / non-interactive
```

`team join` runs **five steps in order**, each **confirmable** and **degrade-clean** — a step
whose source, backend, or driver is absent is **skipped with a note**, never a blocker:

1. **adopt** the governed stack (config + rules/guardrails + the vault & shared-memory *pointers*);
2. **connect** shared memory at your managed Postgres (`--dsn-env`) — **skipped, staying on the
   local floor**, when no DSN is exported or the driver isn't installed;
3. **pull** the shared design/spec vault (`--vault <repo-or-dir>`) — **skipped** when no ref or no
   vault is present;
4. **onboard** the project knowledge — hands you **`/mokata:onboard`** (the guided capture);
5. **verify** with `mokata doctor`, then print a **"here's what you're now wired to"** summary
   (stack, shared-memory backend, vault, and anything pending or skipped).

It **secret-scans** the untrusted pulls (adopt + vault), **never stores the DSN secret** (only the
env-var name), is **idempotent** (re-joining converges — no duplicate writes), and is **reversible**
(`mokata team disconnect` undoes the shared-memory pointer; re-import a prior stack to undo config).

Check where you stand any time:

```bash
mokata team status      # local-only, or pointed at your managed Postgres (and whether it's active)
```

## Under the hood — the individual steps

`team join` simply orchestrates the primitives below; you can also run each on its own.

### Adopt a teammate's stack — `mokata team adopt`

```bash
mokata team adopt ../teammate-repo                  # a repo/dir holding mokata-stack.json
mokata team adopt ./shared/mokata-stack.json        # or the file directly
mokata team adopt ./shared/mokata-stack.json --force # overwrite an existing local config
```

Because the shared stack is **untrusted content**, `team adopt` **secret-scans it first** (it must
never carry a credential), then **human-gates** applying it. It is **idempotent** (re-adopting the
same stack changes nothing) and **reversible** (an audited config write). It reports what it wired:
the config, the **vault** (which travels with the repo), and the **shared-memory pointer** — the
env-var name to export, never a DSN. Decline the gate and **nothing is written**.

### Bring your own managed Postgres — `mokata team connect`

To share **memory and sessions** across the team, point them at your managed Postgres via an
env-var DSN. mokata reuses its existing Postgres memory backend and the portable-session Postgres
transport — no new infrastructure, nothing self-hosted.

```bash
# your environment — the DSN lives here, never in the repo:
export MOKATA_PG_DSN='postgresql://…@your-managed-host/db'
pip install 'mokata[postgres]'        # the optional driver (the core stays dependency-free)

mokata team connect --dsn-env MOKATA_PG_DSN   # gated; wires shared memory + sessions
```

This records only `MOKATA_PG_DSN` (the **name**) in the manifest — **never the DSN value**.

**Degrade-clean:** with no driver installed or no DSN exported, `team connect` says so plainly and
mokata falls back to the **local SQLite memory floor** and the **local session transport** until
you install the driver / export the DSN. Reverse it any time with `mokata team disconnect`.

## Who can set a guardrail? (attribution, not a new auth system)

mokata does **not** add its own authentication or roles. It leans on the access control you
already have:

- **The repo** — anyone who can commit `.mokata/` config (your Git host's branch protection /
  reviews decide that) can change a guardrail.
- **The database** — anyone your DB grants can write to the shared memory.

What mokata adds is **attribution and governance**: every durable write is **human-gated**,
**secret-scanned**, recorded with **provenance** (who added what), and **audited** in the ledger.
So you keep your existing access model and gain a reviewable trail of *who changed which guardrail
and when* — without mokata hosting or guarding anything itself.
