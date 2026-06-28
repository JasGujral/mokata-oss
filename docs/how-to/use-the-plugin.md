# Using mokata in Claude Code (the plugin)

mokata is **primarily a Claude Code plugin**. Once installed, you drive the whole
spec-driven TDD workflow from inside Claude Code — slash commands for the workflow and
hooks that run automatically. If you'd rather not use the marketplace, you get the same
in-Claude-Code experience with one command from a checkout —
[`mokata setup claude`](use-without-plugin.md). The [CLI](../reference/cli.md) comes last:
it's the engine's mechanics for scripting and inspection outside any harness, not the
primary way to build. (Inside Claude Code, **Claude is the brain**; mokata never calls a
model itself — see [How mokata uses an LLM](../concepts/execution-model.md).)

## Install

> ⏳ **Pending directory approval (June 2026):** mokata is awaiting review for the Claude plugin
> directory — it is **not yet installable from Claude's in-app "Browse plugins" directory** yet.
> Use the command below. _(Temporary notice; removed once the listing is live.)_

```text
/plugin marketplace add https://github.com/JasGujral/mokata-oss.git
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
| `/mokata:brainstorm` | Socratic, one-question-at-a-time exploration; proposes 2–3 approaches | `approach-approval` (HARD-GATE) |
| `/mokata:spec` | Draft the spec from the approved approach | `completeness` |
| `/mokata:test` | Generate/define tests for the acceptance criteria | `red-before-green` |
| `/mokata:develop` | Implement against the failing tests | `no-code-without-failing-test` |
| `/mokata:review` | Review a diff in two passes: spec-compliance, then quality | `spec-then-quality` |
| `/mokata:debug` | Root-cause-before-fix with N-strikes escalation | `repro-first` |
| `/mokata:optimize` | Measure-first optimization (keep only if faster + behavior preserved) | `measure-first` |
| `/mokata:bug` | Reproduce-first bug fix with label progression | `reproducer-required` |

You can also **enter the pipeline at any phase** — run just the completeness gate on a
hand-written spec, or jump straight to `/mokata:test` for existing code. Upstream phases aren't
forced, but every phase you run applies its own gate.

**You'll see a progress tracker as you go.** Each phase prints where the run is —
done / current / pending with a `[3/7]` count and what's next — plus a one-line banner
naming what's running (`mokata · develop (running)`), so you always know whether mokata is
working and which part. Ask anytime with `mokata progress` (read-only over the run-state;
nothing leaves your machine). See [the pipeline](../concepts/pipeline.md#run-progress-tracker).

**mokata offers to brainstorm when you're exploring.** You don't have to type
`/mokata:brainstorm` — when you're weighing options or describing a new problem before any code,
Claude Code can auto-engage mokata's brainstorm (you'll see `mokata · brainstorm (engaged)`).
It only *starts* the exploration; the HARD-GATE still holds (no spec/code until you approve an
approach), and it won't interrupt a direct command. Don't want it? `mokata config set
settings.brainstorm.auto off` (or `ask` to be offered first; default `on`).

**mokata never silently deviates from the approved plan.** During implementation it sticks to
what you approved — the approach/refinements, the spec, and its acceptance criteria. If a
change becomes necessary, it **stops and asks first** (surfacing *what · why · options*); an
approved change re-enters the approval surface (re-approve the approach/refinements, or amend
the spec) and is logged to the audit ledger. You're asked, never surprised — and the two-pass
`/mokata:review` flags any unapproved divergence as a backstop.

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

> mokata **ships its own MCP server** (`mokata-mcp`) — the plugin registers it so mokata's
> operations (query, recall, doctor, progress, gated writes, …) are callable as native
> tools. It **also orchestrates external MCP servers** it discovers (H4, `mokata mcp`) and
> maps them to capabilities.

## A typical session

1. `/mokata:brainstorm` your change — answer the questions, pick an approach, approve it.
2. Let mokata draft the spec; the **completeness gate** refuses to emit until every
   acceptance criterion maps to a test.
3. `/mokata:test` then `/mokata:develop` — RED-before-GREEN is enforced (a test must fail before its
   implementation is allowed). (`mokata baseline` first confirms a green starting point.)
4. `/mokata:review` the diff — spec-compliance first, then quality.
5. `/mokata:ship` to finish — mokata verifies it's *truly* green (tests + ACs + review) and
   lets **you** choose how to land it (merge / PR / keep / discard). It never merges, opens a
   PR, or deletes on its own — only on your explicit confirmation.
6. Memory captures the decisions (human-gated); next session inherits them.

## Configuration

mokata is fully configurable from `.mokata/` — and you can set it up **without the
terminal**: type **`/mokata:init`** (optionally `/mokata:init full`) and approve the
preview. On a fresh project mokata also offers to initialize it for you at session start
(once). Toggle layers/tools, pick a profile, or set per-tool trust — see
[Profiles & toggles](../profiles.md) and [Manifest & config](../reference/manifest.md).
Everything is local-first; nothing leaves your machine unless you wire an external tool,
and every durable write is human-gated.
