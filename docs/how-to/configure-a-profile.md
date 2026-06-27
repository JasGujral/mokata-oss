# How-to: configure a profile

## Pick a profile at init

```bash
mokata init --profile minimal    # engine only, zero network egress
mokata init                      # default: standard — knowledge + memory on lean local defaults
mokata init --profile full       # every known graph/memory provider wired
mokata init --profile custom     # everything wired as a starting point to hand-tune
```

`standard` is the default — engine + graph + memory on lean, local, dependency-free
defaults (grep + SQLite). Use `full` to wire every graph/memory provider (each degrades to
its floor when absent), or `minimal` for just the governed TDD engine.

### From the plugin (no CLI, no pip)

Inside Claude Code you don't need the terminal — type **`/mokata:init full`** (or
`standard` / `minimal`). It previews exactly what it will write, asks you to approve, then
sets the profile — running the bundled engine on your existing Claude Code sign-in. You can
also just say *"set up mokata here"* and Claude will run the gated `init` MCP tool; on a
brand-new project mokata even offers to initialize it for you (once — never a nag).

## Tune the committed manifest

Everything is a toggle in `.mokata/manifest.json` (see the
[manifest reference](../reference/manifest.md)):

- **Layers** — set `layers.<name>.enabled` to `false` and that layer's capabilities drop
  from the router.
- **Tools** — set `tools.<id>.enabled` to `false`; the router degrades to the next provider
  in the capability's `fallback` chain.
- **Memory types** — `settings.memory.{persistent,decision,episodic}` toggle independently.
- **Trust dial** — `settings.trust.<tool>` = `read-only` / `propose-only` / `gated-write`.
- **Output density** — `settings.governance.output_density: true` to enable F4 compression.
- **Karpathy gates** — `settings.governance.karpathy.<id>: false` to disable a gate.

- **Backend paths** — point a backend at a custom location (SQLite path, Obsidian vault,
  hosted Postgres) via each tool's `config` block: see
  [configure storage backends & paths](configure-storage-backends.md).
- **Codebase graph** — `full` wires a real graph (code-review-graph / serena) for structural
  queries, with grep as the safe floor: see [use a codebase graph](use-a-codebase-graph.md).

## Verify

```bash
mokata validate       # manifest parses + validates
mokata doctor         # missing providers, conflicts, bad trust, oversized rule tiers
mokata coverage       # which capabilities are covered + any gaps/overlaps
mokata status         # what each capability resolves to right now
```
