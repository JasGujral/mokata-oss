# Reference: manifest & configuration

mokata's configuration is a committed, reviewable artifact under `.mokata/`. The manifest
is validated by a built-in structural validator; when `jsonschema` is installed it adds a
richer pass, and its absence is degraded over (never fatal).

## `.mokata/` layout

Everything mokata creates as its own data lives under `.mokata/`, with a clear
**committed vs. transient** split inside it:

| Path | What | Tracked? |
|---|---|---|
| `manifest.json` | the stack manifest (below) | **committed** |
| `constitution.md` | governing articles, read before non-trivial work | **committed** |
| `.gitignore` | ignores `temp_local/` (shipped by `mokata init`) | **committed** |
| `mokata-stack.json` | a stack you chose to export here (optional) | **committed** |
| `temp_local/` | all transient/runtime data (below) | **gitignored** |

`temp_local/` (transient, regenerated as you work — safe to delete) holds:

| Path | What |
|---|---|
| `temp_local/state/` | pipeline state (JSON) — see below |
| `temp_local/audit/ledger.jsonl` | the append-only audit ledger |
| `temp_local/memory/memory.db` | SQLite memory backend (and `memory/vault/` for Obsidian) |

State files include `approved_approach.json` (brainstorm handoff), `emitted_spec.json`,
`memory_stats.json`, `knowledge_index.json`, `story_analysis__<id>.json`, `undo_log.json`,
and `pipeline_run__<id>.json` (resume checkpoints). They're runtime artifacts, not config —
hence `temp_local/`. (A user-set `tools.<id>.config.path`/`config.vault` can point a backend
elsewhere; that's the user's explicit choice, overriding the default location.)

> **Harness wiring is *not* mokata data.** `mokata setup claude` writes `.claude/commands/`,
> `.mcp.json`, and `.claude/settings.json` — these are **Claude Code's** config and must live
> at those exact paths, so they stay there by necessity (not a violation of the `.mokata/`
> invariant). `.mokata/` holds mokata's own data; the harness owns its wiring.

## Manifest schema

```json
{
  "manifest_version": 1,
  "mokata": { "version": "0.0.2" },
  "profile": "full",
  "layers": {
    "engine":     { "enabled": true },
    "knowledge":  { "enabled": true },
    "memory":     { "enabled": true },
    "governance": { "enabled": true }
  },
  "capabilities": {
    "code_graph": {
      "description": "…",
      "layer": "knowledge",
      "fallback": ["code-review-graph", "serena", "ripgrep", "grep"]
    },
    "memory_store": {
      "description": "…",
      "layer": "memory",
      "fallback": ["native-memory", "obsidian", "sqlite"]
    }
  },
  "tools": {
    "grep": {
      "provides": "code_graph",
      "kind": "builtin",
      "version": null,
      "enabled": true,
      "detect": { "type": "always" }
    }
  },
  "settings": { }
}
```

### Top-level fields

| Field | Type | Notes |
|---|---|---|
| `manifest_version` | int | currently `1` |
| `mokata.version` | string | the mokata version that wrote it |
| `profile` | string | `minimal` / `standard` / `full` / `custom` |
| `layers.<name>.enabled` | bool | one of `engine`, `knowledge`, `memory`, `governance` |
| `capabilities.<need>` | object | `description`, optional `layer`, required `fallback[]` |
| `tools.<id>` | object | `provides`, `kind`, `version`, optional `enabled`, `detect` |
| `settings` | object | the generic toggle store (below) |

### Capability fields

- `fallback` — ordered provider ids (most-preferred first); this **is** the precedence the
  router honors (H6).
- `layer` — the owning layer; the capability is routable only while that layer is enabled
  (K1). If a layer is declared on a capability it must exist in `layers`.

### Tool fields

- `provides` — the capability id this tool serves.
- `kind` — one of `mcp`, `cli`, `library`, `builtin`, `external`. (`mcp`/`external` are the
  network-capable kinds for local-first accounting.)
- `enabled` — per-tool toggle (default `true`); a disabled tool is treated as absent and
  the router degrades to the next provider (K1).
- `detect` — `{ "type": "command"|"python_module"|"path"|"obsidian"|"always", "name": "…" }`
  (`name` required for `command`/`python_module`/`path`; not used by `obsidian`/`always`).
  The `obsidian` strategy detects a real Obsidian config dir (macOS
  `~/Library/Application Support/obsidian`, Linux `~/.config/obsidian` + Flatpak, Windows
  `%APPDATA%\obsidian`) or a configured `config.vault` that exists.
- `config` — optional per-tool block read by the backend builders (Stage 24A). Defaults are
  unchanged when it's absent:
  | Tool | Key | Effect |
  |---|---|---|
  | `obsidian` | `config.vault` | point the Obsidian backend at an external vault directory |
  | `sqlite` | `config.path` | custom SQLite database path (`~` is expanded) |
  | `postgres` | `config.dsn_env` | **name of an env var** holding the DSN for the hosted Postgres backend |

  **Never put a secret (an inline DSN, password, or token) in the manifest** — it's a
  committed, reviewable artifact, and the secret-guard hard-blocks any write that contains
  one. A remote store (Postgres) is opt-in `external`, accounted by local-first netguard,
  and degrades to the SQLite floor if `dsn_env` is unset, `psycopg` (the optional
  `mokata[postgres]` extra) is absent, or the database is unreachable.

## Settings (the generic toggle store)

`settings` is an open-ended key/value block. The keys mokata reads:

| Key | Shape | Default | Feature |
|---|---|---|---|
| `memory` | `{persistent: bool, decision: bool, episodic: bool}` | all on | per-type memory toggles (C9) |
| `governance.output_density` | bool | `false` | output-density compression (F4) |
| `governance.karpathy.<id>` | bool per gate id | all on | Karpathy gate toggles (G3) — ids: `think-first`, `simplicity`, `surgical-scope`, `verify` |
| `trust.<tool>` | `"read-only"`/`"propose-only"`/`"gated-write"` | `gated-write` | per-adapter trust dial (K3) |

The store is intentionally open-ended so future settings (e.g. an execution-mode default)
read from it the same way.

## Profiles (deterministic enabled sets)

| Profile | Layers | `code_graph` chain | `memory_store` chain | Network |
|---|---|---|---|---|
| `minimal` | engine, governance | — | — | **zero egress** |
| `standard` *(default)* | all | ripgrep → grep | sqlite | local-only |
| `full` | all | code-review-graph → serena → ripgrep → grep | native-memory → obsidian → sqlite | only present tools, all gated |
| `custom` | all | full chains (hand-tune) | full chains (hand-tune) | — |

grep is the universal floor for `code_graph`; SQLite (stdlib) is the guaranteed floor for
`memory_store`. See [how-to: configure a profile](../how-to/configure-a-profile.md).

## Reading & setting config

`mokata config get <dotted.key>` prints a value; `mokata config set <dotted.key> <value>`
updates it. `set` is **human-gated** — it previews the old→new change and waits for
confirmation (`--yes` to skip), validates the result, and hard-blocks any secret. For
example:

```bash
mokata config set tools.sqlite.config.path ~/data/mokata.db
mokata config set tools.postgres.config.dsn_env MOKATA_PG_DSN   # env-var name, not a DSN
```

See [how-to: configure storage backends & paths](../how-to/configure-storage-backends.md).

## Sharing a stack

`mokata export [file]` writes the current manifest as a shareable artifact;
`mokata import <file>` validates it and applies it as this repo's config (human-gated;
rejects an invalid manifest). See [how-to: share a stack](../how-to/share-a-stack.md).
