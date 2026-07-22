# How-to: configure storage backends & paths

mokata's memory backend and its on-disk paths are configurable per tool, so you can point
at a custom SQLite location or a hosted Postgres database — without hand-editing JSON. Backends
are selected through the capability router; what you configure here is each backend's
**parameters**.

> **Two stores, one shape.** The canonical memory store is **local SQLite** or **your team's one
> Postgres DSN**. The Obsidian and native-memory backends still work but are **deprecated**
> (removal: 0.0.17) — see [below](#external-obsidian-vault-deprecated).

> **Defaults are unchanged when you set nothing.** Out of the box, memory lives in
> `.mokata/temp_local/memory/memory.db` (SQLite, stdlib, zero dependencies). Everything below is opt-in.

## Set config with `mokata config`

```bash
mokata config get tools.sqlite.config.path          # read a value
mokata config set tools.sqlite.config.path <value>  # update it (human-gated)
```

`config set` is **human-gated** (P2): it previews the old→new change, waits for your
confirmation (`--yes` skips the prompt), validates the resulting manifest, and **hard-blocks
any secret** before writing. The manifest stays a committed, reviewable artifact.

## Custom SQLite path

```bash
mokata config set tools.sqlite.config.path ~/data/mokata-memory.db
```

`~` is expanded. The parent directory is created on first use.

## External Obsidian vault — deprecated

!!! warning "The Obsidian memory backend is deprecated (removal: 0.0.17)"
    It **still works** and nothing has been removed. On first use in a repo mokata prints the
    notice once, naming the replacement and the one-command migration:

    > ⚠ deprecated: the Obsidian memory backend is deprecated and will be REMOVED in mokata
    > 0.0.17. The canonical memory store is local SQLite (or your team's one Postgres DSN).
    > Migrate now with `mokata migrate obsidian` (one-time, human-gated).

    ```bash
    mokata migrate obsidian     # gated · previewed · idempotent · non-destructive
    ```

    Every item lands through the existing WriteGate with provenance, secrets hard-blocked. It is
    **one-time** (a re-run reports "already migrated"; `--force` re-runs it) and **leaves the
    source in place** — deleting the vault copy is your call. The same applies to the
    `native-memory` backend (`mokata migrate native-memory`).

Point the Obsidian backend at a real vault directory (the tool must be wired — it is on the
`full` profile; on `standard` switch with `mokata init --profile full` or add the tool):

```bash
mokata config set tools.obsidian.config.vault ~/Documents/MyVault
```

mokata also now **detects** Obsidian from its real config locations (macOS
`~/Library/Application Support/obsidian`, Linux `~/.config/obsidian` and Flatpak, Windows
`%APPDATA%\obsidian`) and treats a configured `config.vault` that exists as "present" — so a
wired Obsidian backend is actually used instead of silently falling back.

## Hosted Postgres (opt-in, remote)

A hosted Postgres store is an **opt-in** remote backend. Two rules keep it local-first-safe:

1. **The DSN comes from an environment variable — never inline.** You store the *name* of the
   env var in the manifest, and the DSN itself in your shell/secret manager:

   ```bash
   export MOKATA_PG_DSN="postgresql://user:password@db.example.com:5432/mokata"
   mokata config set tools.postgres.config.dsn_env MOKATA_PG_DSN
   ```

   If you try to put a DSN with credentials *into* the manifest, the secret-guard blocks the
   write:

   ```bash
   mokata config set tools.postgres.config.dsn "postgresql://u:pw@host/db"
   # → blocked — secret detected (reference an env var instead, e.g. config.dsn_env)
   ```

2. **Install the optional driver:** `pip install "mokata[postgres]"` (the `psycopg` extra).

3. **Provision the shared schema once:** `mokata team init`. mokata **never runs DDL at runtime** —
   `team init` is the sole owner of it — so the tables must already exist. The upside is that your
   everyday runtime role needs **no `CREATE` and no table ownership**: a DML-only role is enough
   (see [team setup](team-setup.md#least-privilege-the-runtime-role-needs-no-ddl)).

The Postgres backend **degrades to the SQLite floor** — never a hard failure — if `dsn_env` is
unset, the env var is empty, `psycopg` isn't installed, the database is unreachable, or the shared
schema is absent / out of the version range this build serves.

**The fallback is loud, not silent.** A degrade names its failure class and the exact fix (e.g. *run
`mokata team init` to provision the shared schema*), and it is **remembered** — `mokata doctor`
reports what fell back to a floor this session, so you never believe you're reading a shared store
while you're actually reading local SQLite.

### Local-first & trust caveats

- **Nothing egresses unless you wire it.** A remote store is *explicit user wiring*; mokata
  never sends memory off-machine otherwise. It's surfaced by the local-first netguard as a
  network-capable tool.
- **Adopt freely, trust nothing (P15).** A hosted store is an adopted external tool: writes to it
  stay **human-gated** — a write is proposed, and it commits only against a human-minted approval
  (`mokata approve <id>`), with the secret-guard hard-blocking it even then. The
  [trust dial](configure-a-profile.md) sets how freely a tool may write; today it is **enforced on
  the MCP write surface** (`read-only` there is a configuration bound no approval can lift).
- **Provenance is preserved.** Memory carries its origin; putting it in a shared DB doesn't
  strip that.

## See also

- [Reference: manifest & configuration](../reference/manifest.md) — the `config` block per tool.
- [Use & heal memory](use-memory.md) — what's stored and how healing surfaces changes.
- [Configure a profile](configure-a-profile.md) — which backends each profile wires.
