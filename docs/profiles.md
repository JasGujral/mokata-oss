# mokata — Profiles

A profile is a named bundle that decides which layers and capabilities a fresh config
turns on. `mokata init --profile <name>` picks one; everything stays toggleable
afterward (per-layer and per-tool), and the manifest is a committed, reviewable artifact.

Each profile yields a **deterministic enabled set** — same profile in, same stack out.

| Profile | Layers enabled | code_graph chain | memory_store chain | Network egress |
|---|---|---|---|---|
| `minimal` | engine, governance | — (none) | — (none) | **zero** |
| `standard` **(default)** | engine, knowledge, memory, governance | ripgrep → grep | sqlite | local-only |
| `full` | engine, knowledge, memory, governance | code-review-graph → serena → ripgrep → grep | native-memory → obsidian → sqlite | only present tools, all gated |
| `custom` | all (starting point) | full chains (hand-tune) | full chains (hand-tune) | — |

## How toggling works

- **Layers** (`engine`, `knowledge`, `memory`, `governance`) toggle in the manifest. A
  disabled layer's capabilities are dropped from the router at resolution time — they
  simply don't resolve.
- **Tools** carry an `enabled` flag. A disabled tool is treated as absent, so the router
  degrades to the next provider in the chain.
- **Memory types** (`persistent`, `decision`) toggle independently under
  `settings.memory`. Memory is on by default; turning a type off removes it cleanly
  (writes refused, reads never surface it).
- **Capabilities are chosen through one router.** There is a single detection path:
  `router.resolve(<need>)` picks the first present provider in the declared fallback
  order. grep is the universal floor for `code_graph`; SQLite (stdlib) is the guaranteed
  floor for `memory_store`.

## Local-first

`minimal` wires no network-capable tools and is proven to perform **zero network egress**.
`standard` (the default) stays fully local (grep + SQLite). `full` *declares* egress-capable
providers (MCP / external tools), but they only act when actually present and every durable
action is human-gated — nothing leaves the machine unless you wire it, and there is no
telemetry.

## Default & how to change your profile

`standard` is the **default** — the spec-driven engine plus the codebase graph and
decision/persistent memory on lean, local, dependency-free defaults (grep + SQLite). It is
the safest first run, and the one we recommend starting from.

To use a different profile:

- **At init:** `mokata init --profile full` (or `minimal` / `custom`). To switch an existing
  project, re-run with `--force`: `mokata init --profile full --force` (human-gated; an
  overwrite guard protects your committed config).
- **Fine-tune by hand:** edit `.mokata/manifest.json` — flip a layer's `enabled`, or a
  tool's `enabled` flag — then `mokata validate`. `mokata doctor` flags any problems.
- **See what's active:** `mokata status` (live capabilities) and `mokata coverage`
  (coverage + gaps + overlaps).

Reach for `full` when you want every graph/memory provider wired (each still degrades to its
floor when the tool is absent), or `minimal` for just the governed TDD engine.
