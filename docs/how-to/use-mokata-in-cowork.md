# Use mokata in Cowork

Cowork supports plugins, so mokata installs there the same way it does in Claude Code — as a
plugin from the `mostack` marketplace. The pipeline, the gated writes, the knowledge graph, and
the memory all work. One capability differs, and mokata is honest about it: **the PreToolUse
secret-guard hook may not be enforced in Cowork**, so durable-write protection there relies on
mokata's own gated write path rather than the hook. This guide covers the install, the
`/mokata:*` surface, and exactly what degrades and why.

## Install

In Cowork, add the marketplace and install the plugin:

```text
/plugin marketplace add JasGujral/mokata-oss
/plugin install mokata@mostack
```

(That's the public mirror. From a local clone you can add the marketplace by directory path
instead.) After installing, restart the session so the commands load.

`mokata setup` is **not** used for Cowork — `setup` is for harnesses that have *no* plugin
path. Cowork has one, so the plugin install above is the supported route.

## The `/mokata:*` surface

Everything the plugin ships is harness-agnostic — the same `templates/commands/*.md` and the
bundled `mokata-mcp` server — so the slash commands work in Cowork:

- `/mokata:brainstorm`, `/mokata:spec`, `/mokata:test`, `/mokata:develop`, `/mokata:review`,
  `/mokata:ship` — the spec-driven TDD pipeline.
- `/mokata:refine`, `/mokata:debug`, `/mokata:bug`, `/mokata:optimize`, `/mokata:onboard`,
  `/mokata:version`.
- The MCP tools (init, memory, vault, spec-check, …) — including the **gated write tools**,
  which scan for secrets, human-gate, and audit *inside the tool*.

Run `mokata harness cowork` any time to see Cowork's capability matrix.

## Capability differences vs Claude Code

mokata models Cowork honestly through its harness boundary — it never pretends a capability
exists. Cowork's profile:

| Capability | Cowork | Notes |
|---|---|---|
| commands | ✅ | the `/mokata:*` slash commands load from the plugin |
| context_injection | ✅ | the SessionStart briefing is injected |
| subagents | ✅ | parallel/fan-out execution is available |
| **hooks** | ❌ | **the PreToolUse secret-guard hook may not run in Cowork** |

**What this means in practice.** In Claude Code, the secret-guard runs as a PreToolUse hook on
every `Write`/`Edit`/`Bash`, blocking a secret *before* the tool acts. In Cowork that hook may
not fire, so **do not rely on it**. mokata degrades clearly: its durable writes still go through
the universal **WriteGate** (used by `mokata memory`, the vault, the MCP write tools, and the
CLI), which **scans for secrets, requires human approval, and records the decision to the audit
ledger** — independent of any hook. So a secret in a *mokata-gated* write is still blocked in
Cowork; a secret written by a *raw* tool call that bypasses mokata is not caught the way the
Claude Code hook would catch it. When in doubt, route durable writes through mokata's gated
paths (the CLI / the MCP write tools), not raw edits.

Everything stays **local-first** — nothing leaves the machine unless you wire an external tool.

## Validate it loaded

- The commands appear under `/mokata:*` in the command list.
- `mokata harness` lists `cowork` with the matrix above.
- `mokata doctor` reports the resolved config/providers.

If a capability you need isn't available in your Cowork build, mokata will say so plainly rather
than silently no-op — and the gated CLI/MCP path is always the fallback.
