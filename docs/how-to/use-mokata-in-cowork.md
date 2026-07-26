# Use mokata in Cowork

Cowork supports plugins, so mokata installs there the same way it does in Claude Code — as a
plugin from the `mostack` marketplace. The pipeline, the gated writes, the knowledge graph, and
the memory all work. One capability differs, and mokata is honest about it: **PreToolUse hook
enforcement is not guaranteed in Cowork**, so mokata declares `hooks` **absent** there. That
costs you *both* PreToolUse hooks — the **secret-guard** and the run-state **gate-guard** — so
durable-write protection relies on mokata's own gated write path, and the run-state gates
enforce nothing. This guide covers the install, the `/mokata:*` surface, and exactly what
degrades and why.

## Install

In Cowork, add the marketplace and install the plugin:

```text
/plugin marketplace add JasGujral/mokata-oss
/plugin install mokata@mostack
```

(That's the public mirror. From a local clone you can add the marketplace by directory path
instead.) After installing, restart the session so the commands load.

> **Honest about what this is.** `marketplace add` here registers **the mokata repo itself** as
> a local marketplace (it reads that repo's `.claude-plugin/marketplace.json`) — mokata is **not
> listed in any public plugin directory** yet; that listing is pending approval. This is the same
> manual route described in [Install the plugin](install-plugin.md).

`mokata setup` is **not** used for Cowork — `setup` accepts `claude`, `codex`, `cursor`,
`copilot`, `windsurf`, `gemini`, and `aider`, i.e. harnesses with *no* plugin path. Cowork has
one, so the plugin install above is the route here.

## The `/mokata:*` surface

> **Command form ↔ install route.** This page uses the **plugin** render, `/mokata:<name>`. Via
> the pip-first `mokata setup claude` path the same commands appear **bare** — `/<name>` (drop the
> `mokata:` prefix).

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
| **hooks** | ❌ | **neither PreToolUse hook may run in Cowork** — not the secret-guard, not the run-state gate-guard |

**What this means in practice.** In Claude Code, mokata wires two PreToolUse hooks. Neither is
guaranteed to fire in Cowork, so **do not rely on either** — and they fail differently:

- **The secret-guard** (in Claude Code: blocks a secret on every `Write`/`Edit`/`MultiEdit`/`Bash`
  *before* the tool acts). If it doesn't fire, mokata still degrades clearly: its durable writes
  go through the universal **WriteGate** (used by `mokata memory`, the vault, the MCP write
  tools, and the CLI), which **scans for secrets, requires a human-minted approval, and records
  the decision to the audit ledger** — independent of any hook. So a secret in a *mokata-gated*
  write is still blocked in Cowork; a secret written by a *raw* tool call that bypasses mokata is
  not caught the way the Claude Code hook would catch it.
- **The run-state gate-guard** (in Claude Code: blocks a native implementation write that breaks
  the run's methodology — all four of `approach-approval`, `spec-persisted`,
  `no-code-without-failing-test`, `spec-scope`). Here
  there is **no fallback at all.** These gates live *only* in the hook, so in Cowork they
  **enforce nothing**: the agent can write implementation code before an approach is approved,
  before the spec is emitted, before a failing test exists, or outside the spec's authorized
  surface, and nothing intercepts it. The
  `/mokata:*` pipeline still runs and `/mokata:review` still flags divergence after the fact —
  but that is a review, not a seatbelt. Say it plainly: **hard TDD enforcement is Claude-Code-only.**

When in doubt, route durable writes through mokata's gated paths (the CLI / the MCP write tools),
not raw edits.

Everything stays **local-first** — nothing leaves the machine unless you wire an external tool.

## Validate it loaded

- The commands appear under `/mokata:*` in the command list.
- `mokata harness` lists `cowork` with the matrix above.
- `mokata doctor` reports the resolved config/providers.

If a capability you need isn't available in your Cowork build, mokata will say so plainly rather
than silently no-op — and the gated CLI/MCP path is always the fallback.
