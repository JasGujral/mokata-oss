# How-to: configure storage backends & paths

mokata's memory backend and its on-disk paths are configurable per tool, so you can point
at a custom SQLite location, an existing Obsidian vault, or a hosted Postgres database —
without hand-editing JSON. Backends are selected through the capability router; what you
configure here is each backend's **parameters**.

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

## External Obsidian vault

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

The Postgres backend **degrades to the SQLite floor** — never a hard failure — if `dsn_env`
is unset, the env var is empty, `psycopg` isn't installed, or the database is unreachable.

### Local-first & trust caveats

- **Nothing egresses unless you wire it.** A remote store is *explicit user wiring*; mokata
  never sends memory off-machine otherwise. It's surfaced by the local-first netguard as a
  network-capable tool.
- **Adopt freely, trust nothing (P15).** A hosted store is an adopted external tool: writes
  to it stay **human-gated** and it honors the per-adapter [trust dial](configure-a-profile.md).
- **Provenance is preserved.** Memory carries its origin; putting it in a shared DB doesn't
  strip that.

## See also

- [Reference: manifest & configuration](../reference/manifest.md) — the `config` block per tool.
- [Use & heal memory](use-memory.md) — what's stored and how healing surfaces changes.
- [Configure a profile](configure-a-profile.md) — which backends each profile wires.
