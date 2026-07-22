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
| `backups/` | `mokata memory export` backups — `memory-<UTC>.json`, UTC-stamped so a new backup never clobbers a prior one | **committed** (your choice) |
| `vault/` | the design-artifact vault (`mokata vault push`) + `vault/sessions/` for vault-transport session bundles | **committed** |
| `session-bundles/` | `mokata session push --to local` bundles | **committed** (your choice) |
| `skills/` | skills you authored with `mokata skill author` | **committed** |
| `temp_local/` | all transient/runtime data (below) | **gitignored** |

Everything above `temp_local/` sits at the `.mokata/` root **on purpose**: a backup, a shared
design artifact, a session bundle and an authored skill are things you may want to commit and
travel with the repo, so they are deliberately outside the gitignored transient tree.

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
  "mokata": { "version": "0.0.14" },
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
      "fallback": ["code-review-graph", "serena", "ast", "ripgrep", "grep"]
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
| `manifest_version` | int | currently `1`. **An exact-equality check** — this build reads manifest v1 and refuses any other value (it is not a range; the shared *team-DB* schema is the only thing versioned as a range) |
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
  | `pgvector` | `config.dsn_env`, `config.embedder` | the vector-backed Postgres store: the env-var **name** for the DSN, plus the embedder to index with (default `auto` — `model2vec` when the `mokata[embeddings]` extra is present, else the hashing floor) |

  **Never put a secret (an inline DSN, password, or token) in the manifest** — it's a
  committed, reviewable artifact, and the secret-guard hard-blocks any write that contains
  one. A remote store (Postgres) is opt-in `external`, accounted by local-first netguard,
  and degrades to the SQLite floor if `dsn_env` is unset, `psycopg` (the optional
  `mokata[postgres]` extra) is absent, or the database is unreachable.

## Settings (the generic toggle store)

`settings` is an open-ended key/value block. The user-facing keys mokata reads:

| Key | Shape | Default | Feature |
|---|---|---|---|
| `memory` | `{persistent: bool, decision: bool, episodic: bool}` | all on | per-type memory toggles (C9) |
| `ux.progress` | `"terminal"`/`"dashboard"`/`"both"` | `terminal` | run-observability tier (Stage 40) |
| `ux.statusline` | bool | `true` | the always-on pipeline-stage badge (Stage 54b) — opt-out |
| `ux.badge_verbosity` | `"full"`/`"minimal"` | `full` | badge detail: `full` (everything on) or `minimal` (just the current stage) — opt-DOWN; any other value reads as `full` |
| `review.independent` | `"on"`/`"off"` | `on` | run the closing `/review` as a fresh-context subagent (`on`) or the inline two-pass (`off`); any other value reads as `on` |
| `brainstorm.auto` | `"on"`/`"off"`/`"ask"` | `on` | auto-engage brainstorm when exploring: `on` (dive in), `ask` (offer first), `off` (never) |
| `governance.output_density` | bool | `false` | output-density compression (F4) |
| `graph.required` | bool | `true` | REFUSE a degraded (grep-floor) blast radius as decision input in brainstorm Lens-1 / spec-check / domain classification (GR.S3) — opt-out; the escape is a ledgered `--allow-degraded` |
| `governance.karpathy.<id>` | bool per gate id | all on | Karpathy gate toggles (G3) — ids: `think-first`, `simplicity`, `surgical-scope`, `verify` |
| `trust.<surface>` | `"read-only"`/`"propose-only"`/`"gated-write"` | `gated-write` | trust dial for a whole write **surface** — `mcp` or `cli` (K3/SI.4) |
| `trust.<tool>` | `"read-only"`/`"propose-only"`/`"gated-write"` | the surface's level | trust dial for ONE tool (e.g. `remember`, `session_push`) — **overrides** the surface default |

`trust` is one flat map; a key is either a surface or a tool name, and the tool wins:

```json
"trust": { "mcp": "propose-only", "remember": "read-only" }
```

Resolution is **the tool's own level → the surface's → `gated-write`**. Enforced by the
`WriteGate`, so a `read-only` tool cannot write even with a valid human approval. Two floors
are un-loosenable at every level: a **secret** is a hard block, and an MCP write still needs a
human-minted `mokata approve <id>`.

**What it does NOT do (be precise):** on the **`mcp`** surface the real ladder is
**`read-only` ▸ write-allowed** — `propose-only` and `gated-write` behave identically, because
every MCP write *already* requires that out-of-band human approval. Setting `propose-only`
there pins that floor; it does not add a second one. On **`cli`**, the dial is **not yet
enforced**: `trust.cli` is accepted and validated, and writes carry their tool identity, but no
CLI command builds a policy from it today. And the harness's **native `Write`/`Edit`** are outside
the dial **entirely** — those are policed by the PreToolUse hooks (the
[`secret-guard` and the `gate-guard`](../security.md)), not by `trust`. `mokata doctor` prints this
same surface truth as an `info` finding whenever `settings.trust` is set.

The store is intentionally open-ended so future settings (e.g. an execution-mode default)
read from it the same way.

## Settings owned by their own commands

These live in the same `settings` block, but they are **not** part of the `mokata config wizard`
walk — each is written by the command that owns it, because setting it by hand is either unsafe
(a mode switch has a fail-closed preflight) or meaningless (a project identity a joiner must
inherit, not invent). They are listed here so the block above is a complete picture, not so you
edit them directly.

| Key | Shape | Default | Written by |
|---|---|---|---|
| `mode` | `"local"`/`"team"` | `local` | `mokata mode set local\|team` — a team switch is **fail-closed** behind its preflight |
| `project.id` | string | derived | `mokata team init` — a joiner **inherits** it (see [multi-project](../how-to/multi-project-shared-backend.md)) |
| `audit.shared` | bool | `false` | `mokata audit --consent grant` — opt in to publishing your audit entries to the team's own Postgres |
| `audit.dsn_env` | string | — | team setup — the **name of an env var** holding the shared-audit DSN, never the DSN itself |
| `baseline.test_command` | string | — | `mokata config set` — the test command `mokata baseline` runs (mokata never guesses a framework) |
| `memory.embedder` | string | unset (`off`) | the embeddings **consent offer** (`mokata init --mode memory\|full`, interactive only) or `mokata config set`. The **opt-in** semantic retrieval tier — `model2vec:<model>` (real meaning, needs the `mokata[embeddings]` extra) or `hashing` (the zero-dep token-hash floor, **not** semantic). Unset means recall ranks lexically only; `mokata doctor` prints the resolved tier, and changing it needs a gated [`mokata memory reembed`](cli.md#mokata-memory-reembed-yes) |
| `execution.default` | string | `sequential` | `mokata config set` — the default execution mode a run uses (`mokata exec`) |
| `approvals.in_chat` | bool | `false` | a human-gated, ledgered config write — opts in to the `mcp__mokata__approve` in-chat approval tool. **Default-off**; the tool performs the same single-use, content-hash-bound, expiring approval as `mokata approve <id>`, and never rides the `mcp__mokata__*` auto-grant |

## Profiles (deterministic enabled sets)

| Profile | Layers | `code_graph` chain | `memory_store` chain | Network |
|---|---|---|---|---|
| `minimal` | engine, governance | — | — | **zero egress** |
| `standard` *(default)* | all | ast → ripgrep → grep | sqlite | local-only |
| `full` | all | code-review-graph → serena → ast → ripgrep → grep | native-memory → obsidian → sqlite | only present tools, all gated |
| `custom` | all | full chains (hand-tune) | full chains (hand-tune) | — |

grep is the universal floor for `code_graph` — with the embedded stdlib-**AST** backend one step
above it, answering structural queries on Python repos without any external graph tool. SQLite
(stdlib) is the guaranteed floor for `memory_store`. See [how-to: configure a profile](../how-to/configure-a-profile.md).

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
