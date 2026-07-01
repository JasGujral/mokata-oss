# Team audit / shared activity log — shared OR local, conflict-free

Your audit ledger records **every gate decision, tool call, and durable write** — who did what
across the governed brain. By default that log is **local**. A team can *optionally* make it
**shared** so everyone sees the team-wide who-did-what and why — on the team's **own** storage.

!!! warning "NO telemetry — nothing is ever phoned home"
    mokata sends **no telemetry**. Your audit log is **never** transmitted to mokata or Anthropic —
    there is no such endpoint anywhere in the code. A *shared* team log lives on **your team's own
    managed Postgres** (Supabase / Neon / Amazon RDS — just a connection string, the same BYO DB as
    [team setup](team-setup.md)). The data is the team's, on the team's storage, full stop. mokata
    **never stores the DSN secret** — only the *name* of the environment variable that holds it.

## Local is the default

Do nothing and your audit log stays **local** (`.mokata/…/audit/ledger.jsonl`), exactly as before:

```bash
mokata audit             # the local, append-only ledger
mokata audit --why       # a readable what + decision + why timeline (bounded)
```

## Opt in to sharing

Sharing is **opt-in** and **local-first**. Turn it on in the committed config, and name the
environment variable that holds your team's DSN (the same variable [team setup](team-setup.md)
uses, so memory, sessions, and audit resolve the same DB out of the box):

```bash
mokata config set settings.audit.shared true            # opt in (human-gated write)
mokata config set settings.audit.dsn_env MOKATA_PG_DSN  # the env-var NAME only (never the DSN)

export MOKATA_PG_DSN='postgresql://…@your-managed-host/db'   # the DSN lives in your env
pip install 'mokata[postgres]'                               # the optional driver
```

## Publish your entries — `mokata audit --share`

```bash
mokata audit --share          # human-gated: approve publishing your new entries
mokata audit --share --yes    # scripted / non-interactive (still secret-scanned)
```

Publishing is the **only** moment data leaves your machine, so it goes through the same universal
gate as every other outbound write:

- **Human-gated** — you approve the publish; decline and **nothing** is written.
- **Secret-scanned** — a secret anywhere in the payload is a **hard block**, even when approved.
- **Append-only + per-actor + namespaced** — each entry becomes its **own** row tagged with **who**
  (the actor) and the **repo namespace**. Two teammates publishing at the same time each get their
  own rows and **never clobber** each other — conflict-free by construction.
- **Idempotent** — only your *new* entries publish; re-running when nothing changed is a clean no-op.
- **The DSN secret is never stored** — only the env-var name lives in the manifest.

Attribution (**who**) comes from `MOKATA_ACTOR`, else `USER` / `USERNAME` — mokata adds no auth
system of its own; it records who, human-gates the write, and audits it.

## Read the team-wide log — `mokata audit --team`

```bash
mokata audit --team              # who-did-what / why across ALL teammates (read-only)
mokata audit --team --tail 20    # bounded to the most recent entries
```

The read **spans the shared log** for this repo — every actor's entries, oldest first, each line
prefixed with who did it. It is read-only and touches nothing local.

## Inside Claude Code

Everything is reachable in-harness (no dropping to a terminal):

- **`audit`** MCP read tool with **`team=true`** → the shared team-wide who-did-what.
- **`audit_share`** MCP **write** tool → publish your entries; propose-only until you pass
  `approve=true`, then it commits through the WriteGate (a secret is hard-blocked).

## Degrade-clean

No driver installed, or no DSN exported? Sharing and the team read **stay local** with a clear
message — never a crash, and never a silent downgrade to a less-secure path:

```text
shared audit unavailable (… needs a DSN in $MOKATA_AUDIT_PG_DSN / $MOKATA_PG_DSN …) —
your log stays LOCAL (degrade-clean). Export $MOKATA_PG_DSN and `pip install 'mokata[postgres]'`
to publish.
```

## What you get

Shared visibility into your team's **own** governed activity — who set which guardrail, which gates
fired, what was shipped — with **zero telemetry** and **zero trust trade-off**. It's your data, on
your storage, conflict-free, and reversible (turn sharing off with
`mokata config set settings.audit.shared false`).
