# Multi-project on one shared backend — project scoping

mokata's shared backends (a team **Postgres** DSN for memory, semantic vectors, portable sessions,
and the [team audit log](team-audit.md)) are **owned + namespaced** so they never collide with
another app's tables. Stage 71a adds the missing layer: they are also **scoped per project**, so
**one shared database can safely host many projects** with no cross-project bleed.

!!! note "Local backends were always clean"
    Local SQLite (`.mokata/…`) and the committed vault (`.mokata/vault/`) are per-repo already. This
    page is only about the **shared** Postgres backends.

## The project key

Every project has a stable, deterministic **project key**:

- Configured: `settings.project.id` if you set one (`mokata config set settings.project.id <id>`).
- Otherwise derived: the **git remote** URL — normalized so an `ssh` clone and an `https` clone of
  the same repo agree — else the repo's path, hashed to a short token (`p_…`).

It is stable across sessions (same repo → same key) and machine-path-free. Two clones of the same
repo on different machines resolve to the **same** key (via the shared remote), so they share one
project's data; two unrelated repos never do.

Set an explicit, human-friendly key when a team wants everyone to agree regardless of clone URL:

```bash
mokata config set settings.project.id acme-web    # human-gated config write
```

## How every shared row is scoped

Each shared table carries a `project` column; **every** write, read, list, and delete filters by the
current project key. So a `recall`, a `session list`, or an `audit --team` returns **only your
project's rows** even when several projects share one DSN. A session tag like `auth` no longer
collides across projects — each project keeps its own bundle.

## Reviewing memory / sessions / audit

Review **defaults to the current project**. Three flags open it up:

| Flag | Effect |
|---|---|
| *(none)* | the **current project** only (the default) |
| `--all` | span **every** project on the shared backend |
| `--project <id>` | a **specific** project |
| `--list-projects` | print the projects present on the shared backend, then exit |

```bash
mokata memory --list-projects          # which projects are on this shared DB?
mokata memory --project acme-web       # review another project's brain
mokata audit --team --all              # who-did-what across every project
mokata session list --all              # every project's portable sessions
```

**Outside a project** (a bare shell, no `.mokata/`) pointed at a shared DSN, mokata **never silently
dumps every project**. It asks you to choose a scope — `--all`, `--project <id>`, or
`--list-projects` to see what's there first.

## One DSN per project vs one shared DB

Both work:

- **One shared DB (recommended now):** point every project at the same DSN; project scoping keeps
  them isolated. Simplest to operate — one database for the whole team.
- **One DSN per project:** give each project its own database/schema. Also fine (the scoping is
  simply a no-op when only one project is present). This was the interim operating model before
  Stage 71a.

## Migration — pre-existing shared tables

Tables created before Stage 71a gain the `project` column automatically (an idempotent
`ADD COLUMN IF NOT EXISTS` on connect). Their **old rows have no project key** and read back as a
**`legacy`** bucket:

- Scoped reads (the default) **do not** show legacy rows — no crash, no surprise.
- `--all` **surfaces** them, and `--list-projects` shows the `legacy` bucket, so nothing is hidden
  silently.

To fold legacy rows into a real project, do a one-time backfill against your database, e.g.:

```sql
UPDATE mokata_memory        SET project = 'acme-web' WHERE project IS NULL;
UPDATE mokata_memory_vectors SET project = 'acme-web' WHERE project IS NULL;
UPDATE mokata_session_bundle SET project = 'acme-web' WHERE project IS NULL;
```

(Use the key `mokata config get settings.project.id` reports, or your chosen id.) After the backfill
those rows scope to that project like any other. Everything stays **human-gated + secret-scanned**
on write, and the **DSN secret is never stored** — only the env-var name.
