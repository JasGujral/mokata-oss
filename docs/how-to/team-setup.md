# Team mode — setup & operations

This is the one page for running mokata as a **team**: a team spins up shared state **once**, and
every new member onboards to **CONNECTED without reading source**. It is the page every team-mode
error links to, so if you get stuck, you're already here.

!!! note "Honest scope — mokata hosts nothing"
    mokata is local-first and OSS; it **runs no hosted service**. "Team mode" means the team's
    **own vanilla Postgres** (Supabase / Neon / Amazon RDS free tiers all work — just a connection
    string; **no extensions needed**). mokata **never stores your DSN secret**: it records only the
    *name* of the environment variable that holds it, and the DSN stays in your environment.

Local mode is the zero-config default and is **completely unaffected** by any of this — team mode is
opt-in and never becomes mandatory.

## The pitch

> Bring one Postgres. Export one env var (`MOKATA_PG_DSN`). Run `mokata team init`. That's the
> whole setup: your team now shares memory, sessions, and a tamper-resistant audit trail. New
> members reach CONNECTED with one guided command. mokata hosts nothing and never stores your
> credentials.

---

## 1. Golden-path setup (the first person) — `mokata team init`

The **first** person on the team provisions the shared state **once**. `team init` is the **sole
owner of DDL** — it is the only thing in mokata that ever creates tables.

```bash
# your environment — the DSN lives here, never in the repo:
export MOKATA_PG_DSN='postgresql://…@your-managed-host/db'
pip install 'mokata[postgres]'        # the optional driver (the core stays dependency-free)

mokata team init                      # guided: provision → pin identity → live CONNECTED test
```

`team init`:

1. **guides a backend pick** — `--backend managed` (a managed DSN, the golden path; default),
   `--backend compose` (self-host — a one-command stack ships in a later step), or
   `--backend local` (a local Postgres to try team mode on one machine). All three end at the same
   green preflight;
2. **fails closed with a named fix** if `$MOKATA_PG_DSN` is unset — **writing nothing**;
3. runs **one idempotent provision pass** — the shared tables (`mokata_memory`,
   `mokata_session_bundle`, `mokata_audit_log`, `mokata_events`) plus the `mokata_schema_version`
   row (which records both the version the schema **is** and the oldest client it still **serves**)
   — on **vanilla Postgres ≥14, no extensions**. Re-running is safe: an already-current schema is a
   reported no-op, and only a *real* change asks for your approval;
4. **pins the team project identity** (`settings.project.id`, human-gated) so every teammate's
   clients agree on the same shared namespace instead of splitting by local path-hash;
5. runs the **live CONNECTED test** (the same probe activation uses) and reports green.

Then activate team mode for yourself:

```bash
mokata mode set team                  # fail-closed preflight → human-gated activation
```

Finally, **share the governed stack** so teammates can inherit it (the pinned identity + the shared-
memory pointer travel with it):

```bash
mokata export                         # writes .mokata/mokata-stack.json — commit it, or share the file
```

---

## 2. New-member onboarding (everyone else) — `mokata team join`

A new teammate does **not** run `team init` (joiners never run DDL). They point at the already-
provisioned shared DB and run **one guided command** — from a DSN to **CONNECTED**, without reading
any source. Inside Claude Code this is also `/team join`.

```bash
# point at the SAME managed Postgres the team provisioned (env-var only — never inline):
export MOKATA_PG_DSN='postgresql://…@your-managed-host/db'
pip install 'mokata[postgres]'

mokata team join ../teammate-repo --dsn-env MOKATA_PG_DSN --vault ../teammate-repo
mokata team join ./shared/mokata-stack.json --yes          # scripted / non-interactive
```

`team join` runs these steps **in order**, each **confirmable**:

1. **adopt** the governed stack — and here the teammate **INHERITS** the team's pinned
   `settings.project.id`; a joiner **never re-pins or forks** the identity;
2. **connect** shared memory at the managed Postgres (`--dsn-env`);
3. **activate** — run the connection **preflight → CONNECTED** and switch on team mode. This step is
   **fail-closed**: if the DB is unreachable, the schema is missing, or the version is incompatible,
   it stops with the **named fix** and **writes no activation** (see
   [Connection health & fallback](#3-connection-health-fallback) below);
4. **pull** the shared design/spec vault (`--vault`);
5. **onboard** the project knowledge — hands you `/onboard` (the guided capture);
6. **consent** — capture the revocable [standing audit-publish consent](#security) (once);
7. **verify** with `mokata doctor`, then print a **"here's what you're now wired to"** summary.

The DSN pointer must be the env-var **NAME** (e.g. `MOKATA_PG_DSN`) — passing an **inline DSN value**
is **refused** (fail-closed), because the secret must never be typed on a command line or stored. A
step whose source, DSN, or driver is absent is **skipped with a note** (never a blocker); the flow is
**idempotent** (re-joining converges) and **reversible** (`mokata team disconnect`).

Check where you stand any time:

```bash
mokata mode              # the run mode + the full team-readiness preflight (each blocker + its fix)
mokata team status       # local-only, or pointed at your managed Postgres (and whether it's active)
```

---

## 3. Connection health & fallback

When the preflight refuses, it always names **why** and **how to fix it** — you never get a silent
failure:

| Symptom | What it means | Fix |
|---|---|---|
| **unreachable / timeout** | the host didn't answer within the ≤500ms probe (DNS / host down / port closed / firewall) | check the host and port, the firewall, and that the database is running |
| **auth failed** | the host **answered** and rejected the credentials — the network is fine | check the role and password in that env var |
| **schema not provisioned** | you reached the DB, but no shared schema is there | **ask whoever ran `mokata team init`** to provision it — joiners never run DDL |
| **schema-too-old** | the shared schema is *below* the oldest version this build can serve | ask the owner to **re-run `mokata team init`** to upgrade the shared schema |
| **client-too-old** | the shared schema has moved *ahead* and no longer serves this build | **upgrade mokata** — `pip install -U mokata` |
| **driver absent** | the optional Postgres driver isn't installed | `pip install 'mokata[postgres]'` |
| **transaction-mode pooler** | you connected fine, but the DSN is a **pooled** endpoint — `LISTEN/NOTIFY` (team push) and session features break silently behind one | point the env var at the **direct/session** connection string, not the pooler (see below) |

Every one of these messages links back to this page. Note that `schema-too-old` and `client-too-old`
are the **only two** version refusals: an ordinary version difference does **not** refuse — it warns
and keeps working (see [the schema range](#the-shared-schema-is-a-range-not-an-exact-match) below).

### The pooler trap — use a DIRECT/SESSION connection string

The **single most common** silent team-DB failure is pointing mokata at a managed provider's
**transaction-mode pooler**. It connects, it authenticates, everything looks green — and then
`LISTEN/NOTIFY` (which team push rides) and session features quietly do nothing. So mokata checks
the *shape* of the DSN **before** it wires anything, by pure string parsing — **no network call,
and it never echoes the DSN value, host, user, or password**.

`mokata team connect` prints the detected provider and, if the string is pooled, **loudly warns and
asks** before writing. Decline and nothing is written:

```text
not connected — pooled DSN declined at the pooler-trap check (nothing written). Use a
direct/session connection string.
```

Say yes anyway and it proceeds, but the override is **recorded in the audit ledger** (provider +
bare port only — never the credential). If the env var isn't exported yet, `team connect` tells you
which kind of string to reach for up front.

**The direct string, per provider:**

| Provider | Use this | Not this |
|---|---|---|
| **Supabase** | the Direct/Session string — host `db.<ref>.supabase.co`, port **5432** | `*.pooler.supabase.com:6543` (transaction mode) |
| **Neon** | the endpoint **without** the `-pooler` host segment | the `-pooler` pooled endpoint |
| **Amazon RDS** | the instance/cluster endpoint | an RDS Proxy endpoint (`*.proxy-*`) |
| anything else | a non-pooler host / port 5432 | a `pgbouncer`/`pooler` host, or a transaction-pooler port |

mokata **never rewrites your DSN** — it names the problem and the fix, and the change is yours to
make.

### `mokata doctor` — the DSN deep-check

`mokata doctor` runs the same probe and reports a **named, secret-free finding per layer** under a
`database (team DSN)` section, so you learn *which* layer failed instead of reading a psycopg
stack trace:

```bash
mokata doctor            # includes the DSN deep-check on a team-connected repo
```

| Finding code | Severity | Means |
|---|---|---|
| `db-driver-absent` | ✗ error | `psycopg` isn't installed — `pip install 'mokata[postgres]'` |
| `db-network` | ✗ error | unreachable — no response in the probe budget, or the connect failed outright |
| `db-auth` | ✗ error | the host answered and rejected the credentials |
| `db-schema-absent` | ✗ error | connected + authed, but the shared schema isn't provisioned — `mokata team init` |
| `db-schema-old` | ✗ error | the shared schema is older than this build serves — `mokata team init` |
| `db-client-old` | ✗ error | the shared schema is newer than this build — `pip install -U mokata` |
| `db-schema-version` | ⚠ warning | connected, versions differ but are in range — you keep working |
| `db-pooler` | ⚠ warning | the connection works but is a **transaction-mode pooler** (co-reported alongside whichever primary finding applies) |
| `db-ok` | ✓ info | provider, schema version, and round-trip time — all green |

Exactly **one primary finding** is reported (the most fundamental failing layer), plus the pooler
warning **orthogonally** when a *reachable* connection turns out to be pooled. Only the errors flip
doctor's exit code; a pooler and an in-range version difference warn and keep working. The check is
**silent on a local/solo repo** — no probe, no noise — and it never renders the DSN value, host,
user, or password.

### Health is always visible (never silent)

Team mode probes the shared connection **once** per session (a bounded ≤500ms check, cached and
lazily re-checked) and renders **one verdict** everywhere: the statusline badge (a **⚠** on
trouble), `mokata mode`, `mokata doctor`, and the SessionStart briefing all read the **same** cached
result. A broken or unreachable connection is **always highlighted** — never a silent gap — and the
warning carries the work-locally offer below. Local mode shows no connection health (there's nothing
shared to check).

### Offline never blocks — one write path

Every durable team write lands in a **crash-safe local journal first**, then flushes to Postgres in
batches. **Healthy and offline use the same path** — offline just means the flush *waits*. So a
dropped connection never blocks you: you keep working, your writes are **journaled and not lost**,
and nothing diverges silently. This replaces the old fallback that could strand writes on the local
floor.

### `mokata sync` — flush + reconcile

When the connection is healthy again, **`mokata sync`** flushes the journal and reconciles:

- **Approval is inherited, never bypassed.** Each flushed write carries the **ledger id** of the
  human approval that authorised it — deferred *durability*, never deferred *consent*. A per-publish
  **secret-scan** still applies at flush time.
- **Conflicts surface and are human-gated.** Team memory rows are **compare-and-set** (a `revision`
  column). If a teammate changed the same row while you were offline, the conflict **surfaces** and
  `mokata sync` asks you: **keep your local version** (overwrite remote) or **keep remote**. There is
  **never a silent last-writer-wins**; a conflict you don't decide stays *deferred* for a later
  interactive run. Every decision is audit-ledgered.
- **Stranded rows are recovered.** Any writes the old fallback left on the local floor are picked up
  and flushed through the same gated path.

Run it non-interactively with `mokata sync --yes` (flush only; conflicts are deferred, never
silently overwritten).

### The shared schema is a RANGE, not an exact match

This build **speaks `mokata_schema_version` v3** and **serves shared schemas back to v2**.
Compatibility is a **range overlap**, not an equality check — because *a version bump must not
partition a team mid-upgrade*. Whoever upgrades mokata first must not be locked out of the team's
database, and the moment the database is migrated the teammates still on the old build must not be
locked out either.

So a version **difference in either direction only WARNS** and keeps working:

| Situation | What you see | What happens |
|---|---|---|
| schema **behind** your build (e.g. DB v2, you speak v3) | *the shared schema is v2, behind this build (v3) — run `mokata team init` to upgrade it (working normally meanwhile)* | you keep working |
| schema **ahead** of your build (e.g. DB v4, you speak v3) | *the shared schema is v4, ahead of this build (v3) — it still serves v3 clients, but upgrade mokata when you can* | you keep working |

Only **two** states are genuine hard refusals — the two real incompatibilities:

- **`schema-too-old`** — the schema is below this build's floor. Fix: **`mokata team init`** to
  upgrade the shared schema.
- **`client-too-old`** — the schema no longer serves this build. Fix: **`pip install -U mokata`**.

Both refuse **fail-closed** (no half-connect), and both name the fix.

### Upgrading the shared schema

All DDL is owned by `team init`, so an existing team upgrades by **re-running `mokata team init`**
(the owner, or anyone holding the migration role). The migration is idempotent
(`ADD COLUMN IF NOT EXISTS`; a re-init on an already-current schema is reported as a no-op and
doesn't even ask for approval). What the versions added: **v2** the compare-and-set columns
(`revision`, `updated_at`) behind the conflict handling above — existing rows default to revision 1;
**v3** the memory scope + precedence columns.

`team init` will **refuse to rewrite a schema that is ahead of it** — *the shared schema is v4,
ahead of this build (v3) — it must not be rewritten by an older client* — so an older teammate can
never silently downgrade the team's database.

---

## Security

Team mode is designed so that going shared **adds** a reviewable trail without adding new attack
surface. The guarantees:

- **Credentials are env-var only — never stored.** mokata records only the **name** of the
  environment variable that holds your DSN (`MOKATA_PG_DSN`); the DSN **value** never lands in the
  manifest, a stack file, a vault, or the audit log. Every durable write is **secret-scanned**, and a
  detected secret is a **hard block** that approval cannot override. Passing an inline DSN where a
  name is expected is refused.
- **No extensions on the golden path.** The shared schema is plain Postgres ≥14 — no `CREATE
  EXTENSION`, no pgvector required. Fewer moving parts, less to trust.
- **The runtime role needs no DDL rights at all.** Everyday runtime connections run **zero DDL** —
  schema checking is a cached, SELECT-only probe, and *all* DDL belongs to `mokata team init` alone.
  So a **DML-only runtime role is sufficient**, not a downgrade: see
  [least-privilege runtime role](#least-privilege-the-runtime-role-needs-no-ddl) below.
- **Attribution, not authentication.** mokata does **not** add its own auth or roles. Author
  attribution on writes is **advisory** (env-derived); your Git host and database grants remain the
  access-control system of record. What mokata adds is **human-gated, secret-scanned, audited**
  provenance — a reviewable record of *who changed what and when*.
- **Standing audit-publish consent (revocable).** Publishing your audit entries to the shared log is
  the one moment data leaves your machine, so it is human-gated. To avoid a prompt on every batch, you
  grant an **explicit standing consent once** (during `team join`, or `mokata audit --consent grant`)
  — it is **ledgered** and **revocable** any time with `mokata audit --consent revoke`. It replaces the
  per-batch prompt **without weakening the gate**: the per-publish **secret-scan still hard-blocks**,
  so it is never a governance bypass.

### Least-privilege: the runtime role needs NO DDL

Run **`mokata team init` as a migration role** that owns the tables, and point **everyday runtime at
a DML-only role**. This is not a hardening nicety you trade convenience for — it is simply what
mokata needs. Runtime connects issue **no DDL whatsoever**: memory and session bundles are
`INSERT … ON CONFLICT DO UPDATE` / `SELECT` / `DELETE`, the audit log is `INSERT`-only, and the
schema check is a **cached `SELECT`** against `mokata_schema_version`.

A correct runtime role therefore needs **no `CREATE`, no table ownership, and no `CREATE
EXTENSION`** — only:

- `USAGE` on the schema;
- **DML** on `mokata_memory` and `mokata_session_bundle`;
- **`SELECT, INSERT` only** on `mokata_audit_log` — so revoking `UPDATE`/`DELETE` makes the audit log
  **append-only by grant**, not merely by convention;
- `SELECT` on `mokata_schema_version`;
- `USAGE` on the backing sequences.

!!! warning "mokata does not manage your roles"
    mokata **creates no roles and issues no grants**, and it prints no GRANT script — `team init`
    offers guidance in prose, and that is all. Roles and grants are **yours to run**, with your
    database's own tooling. As a starting point, something like this (**your SQL, not mokata's**):

    ```sql
    GRANT USAGE ON SCHEMA public TO mokata_runtime;
    GRANT SELECT, INSERT, UPDATE, DELETE ON mokata_memory, mokata_session_bundle TO mokata_runtime;
    GRANT SELECT, INSERT                 ON mokata_audit_log        TO mokata_runtime;
    GRANT SELECT                         ON mokata_schema_version   TO mokata_runtime;
    GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO mokata_runtime;
    ```

**This used to be broken, and the fix is why the guidance changed.** Postgres checks the schema ACL
*before* the `IF NOT EXISTS` short-circuit, and `ADD COLUMN IF NOT EXISTS` demands table
**ownership** — so runtime backends that re-ran their own `CREATE TABLE …` on every connect got a
DML-only role **denied `CREATE`** (SQLSTATE 42501) *even against a perfectly provisioned, current
database* — and that denial quietly degraded the team to the local SQLite floor. Runtime DDL is now
gone. A DML-only runtime role **just works**; and if the schema really is absent or out of range,
mokata degrades **loudly** — naming the failure class and the exact fix command — instead of
silently.

---

## Under the hood — the individual steps

`team join` orchestrates the primitives below; you can also run each on its own.

### Adopt a teammate's stack — `mokata team adopt`

```bash
mokata team adopt ../teammate-repo                  # a repo/dir holding mokata-stack.json
mokata team adopt ./shared/mokata-stack.json        # or the file directly
mokata team adopt ./shared/mokata-stack.json --force # overwrite an existing local config
```

Because the shared stack is **untrusted content**, `team adopt` **secret-scans it first** (it must
never carry a credential), then **human-gates** applying it. It is **idempotent** and **reversible**
(an audited config write). It reports what it wired: the config, the **vault** (which travels with the
repo), the **pinned project identity** (inherited, not re-pinned), and the **shared-memory pointer** —
the env-var name to export, never a DSN. Decline the gate and **nothing is written**.

### Bring your own managed Postgres — `mokata team connect`

```bash
export MOKATA_PG_DSN='postgresql://…@your-managed-host/db'
mokata team connect --dsn-env MOKATA_PG_DSN         # gated; wires shared memory + sessions
```

This records only `MOKATA_PG_DSN` (the **name**) in the manifest — **never the DSN value**.
Degrade-clean: with no driver or no DSN exported, mokata falls back to the **local SQLite floor** and
the **local session transport** until you install the driver / export the DSN. Reverse it any time
with `mokata team disconnect`.

## Who can set a guardrail? (attribution, not a new auth system)

mokata leans on the access control you already have — **the repo** (your Git host's branch
protection / reviews decide who can commit `.mokata/` config) and **the database** (whoever your DB
grants can write to shared memory). What mokata adds is **attribution and governance**: every durable
write is **human-gated**, **secret-scanned**, recorded with **provenance**, and **audited**. You keep
your existing access model and gain a reviewable trail of *who changed which guardrail and when* —
without mokata hosting or guarding anything itself.
