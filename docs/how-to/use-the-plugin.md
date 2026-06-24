# Using mokata in Claude Code (the plugin)

mokata is **primarily a Claude Code plugin**. Once installed, you drive the whole
spec-driven TDD workflow from inside Claude Code — slash commands for the workflow and
hooks that run automatically. If you'd rather not use the marketplace, you get the same
in-Claude-Code experience with one command from a checkout —
[`mokata setup claude`](use-without-plugin.md). The [CLI](../reference/cli.md) comes last:
it's the engine's mechanics for scripting and inspection outside any harness, not the
primary way to build.

## Install

```text
/plugin marketplace add JasGujral/mokata-oss
/plugin install mokata@mostack
```

Then **restart Claude Code** so the commands and hooks load. Confirm with `/plugin` —
`mokata` should appear under the `mostack` marketplace. (Full detail:
[Install the Claude plugin](install-plugin.md).)

## What you get

### 1. Slash commands (the workflow)

Each runs standalone — no full-pipeline prerequisite, and each applies its own gate:

| Command | What it does | Gate id |
|---|---|---|
| `/brainstorm` | Socratic, one-question-at-a-time exploration; proposes 2–3 approaches | `approach-approval` (HARD-GATE) |
| `/spec` | Draft the spec from the approved approach | `completeness` |
| `/test` | Generate/define tests for the acceptance criteria | `red-before-green` |
| `/develop` | Implement against the failing tests | `no-code-without-failing-test` |
| `/review` | Review a diff in two passes: spec-compliance, then quality | `spec-then-quality` |
| `/debug` | Root-cause-before-fix with N-strikes escalation | `repro-first` |
| `/optimize` | Measure-first optimization (keep only if faster + behavior preserved) | `measure-first` |
| `/bug` | Reproduce-first bug fix with label progression | `reproducer-required` |

You can also **enter the pipeline at any phase** — run just the completeness gate on a
hand-written spec, or jump straight to `/test` for existing code. Upstream phases aren't
forced, but every phase you run applies its own gate.

### 2. Hooks (automatic)

Both hooks are declared in `hooks/hooks.json`:

- **SessionStart** (`session_start.py`, async/observability) — injects a sub-2k-token
  briefing at the start of each session: which stack you're in, which capabilities are
  live, and the inviolable gates. Nothing to run.
- **Secret guard** (`secret_guard.py`, **PreToolUse, sync security, exit code 2**) — blocks
  a write/edit/command that would commit or send a secret. Un-overridable.

### 3. The CLI alongside the plugin

Everything the slash commands don't surface is available from the same engine via the
[`mokata` CLI](../reference/cli.md) — e.g. `mokata audit`, `mokata budget`, `mokata doctor`,
`mokata coverage`, `mokata query`, `mokata memory`. Any AI tool that can run shell commands
can call these too (see [Integrate with other AI tools](integrate-other-ai-tools.md)).

> mokata **orchestrates external MCP servers** it discovers (H4, `mokata mcp`) and maps
> them to capabilities; in v1.0 it does not itself expose an MCP server.

## A typical session

1. `/brainstorm` your change — answer the questions, pick an approach, approve it.
2. Let mokata draft the spec; the **completeness gate** refuses to emit until every
   acceptance criterion maps to a test.
3. `/test` then `/develop` — RED-before-GREEN is enforced (a test must fail before its
   implementation is allowed).
4. `/review` the diff — spec-compliance first, then quality.
5. Memory captures the decisions (human-gated); next session inherits them.

## Configuration

mokata is fully configurable from `.mokata/` (created by `mokata init`, default profile
`standard`). Toggle layers/tools, pick a profile, or set per-tool trust — see
[Profiles & toggles](../profiles.md) and [Manifest & config](../reference/manifest.md).
Everything is local-first; nothing leaves your machine unless you wire an external tool,
and every durable write is human-gated.
