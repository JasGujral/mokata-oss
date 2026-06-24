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

## Verify

```bash
mokata validate       # manifest parses + validates
mokata doctor         # missing providers, conflicts, bad trust, oversized rule tiers
mokata coverage       # which capabilities are covered + any gaps/overlaps
mokata status         # what each capability resolves to right now
```
