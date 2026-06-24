# Reference: manifest & configuration

mokata's configuration is a committed, reviewable artifact under `.mokata/`. The manifest
is validated by a built-in structural validator; when `jsonschema` is installed it adds a
richer pass, and its absence is degraded over (never fatal).

## `.mokata/` layout

| Path | What |
|---|---|
| `manifest.json` | the stack manifest (below) |
| `constitution.md` | governing articles, read before non-trivial work |
| `state/` | durable pipeline state (JSON) — see below |
| `audit/ledger.jsonl` | the append-only audit ledger |
| `memory/memory.db` | SQLite memory backend (and `memory/vault/` for Obsidian) |

State files include `approved_approach.json` (brainstorm handoff), `emitted_spec.json`,
`memory_stats.json`, `knowledge_index.json`, `story_analysis__<id>.json`, `undo_log.json`,
and `pipeline_run__<id>.json` (resume checkpoints).

## Manifest schema

```json
{
  "manifest_version": 1,
  "mokata": { "version": "1.1.0" },
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
- `detect` — `{ "type": "command"|"python_module"|"path"|"always", "name": "…" }`
  (`name` required for all but `always`).

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

## Sharing a stack

`mokata export [file]` writes the current manifest as a shareable artifact;
`mokata import <file>` validates it and applies it as this repo's config (human-gated;
rejects an invalid manifest). See [how-to: share a stack](../how-to/share-a-stack.md).
