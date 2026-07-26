# Using mokata in Claude Code (the plugin)

mokata runs the whole spec-driven TDD workflow **inside Claude Code** — slash commands for the
workflow and hooks that run automatically. Today you wire it in with the pip-first path
([`mokata setup claude`](use-without-plugin.md)); a one-click Claude Code **plugin** is planned
but **not yet available**. The [CLI](../reference/cli.md) comes last: it's the engine's
mechanics for scripting and inspection outside any harness, not the primary way to build.
(Inside Claude Code, **Claude is the brain**; mokata never calls a model itself — see
[How mokata uses an LLM](../concepts/execution-model.md).)

## Install

<!-- mokata:directory-listing:start -->
> ⏳ **Pending Claude plugin-directory approval.** A one-click Claude Code **plugin** is
> **planned, not yet available** — mokata isn't registered on any Claude Code marketplace.
> The supported way to run mokata inside Claude Code today is the pip-first path:
> `pip install mokata` → `mokata setup claude`
> (see [Getting started](https://mokata.ai/getting-started/)).
> _(This notice auto-flips once the listing is approved — single source:
> `scripts/directory_listing.py`.)_
<!-- mokata:directory-listing:end -->

```bash
pip install mokata               # MCP server works out of the box on Python ≥ 3.10
mokata setup claude              # wires commands + skills + MCP + statusline — human-gated
# restart Claude Code, then:
mokata mcp status                # expect: mokata-mcp: CONNECTED ✓
```

This wires the *same* slash commands, Agent Skills, MCP tools, and hooks the plugin would. Full
narrative: **[Getting started](../getting-started.md)** · full detail:
[Use mokata without the plugin](use-without-plugin.md).

> **Skills stay fresh on update.** Re-running `mokata setup claude` after upgrading mokata is a
> **sync**: it (over)writes the current mokata Agent Skills **and prunes any old/removed mokata
> skills** so you don't keep seeing stale ones. The prune is **marker-based** — only
> mokata-authored skills are touched, so **your own skills are never removed** — and, because a
> durable delete is human-gated, the removal is shown for approval first. Note: Claude Code
> **caches the skill list**, so **restart Claude Code** after the sync to see the change.

> **Command form ↔ install route.** The tables below use the **plugin** render, `/mokata:<name>`.
> Because the supported route today is the pip-first `mokata setup claude` path, the commands
> actually appear **bare** in your `/` menu — `/<name>` (drop the `mokata:` prefix). The one-click
> plugin (which would namespace them) is planned, not yet available.

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

**Observability (read-only, no gate):**

| Command | What it does |
|---|---|
| `/mokata:progress` | The 7-phase tracker **and** the parallel-agent lanes (running / done / blocked) |
| `/mokata:watch` | Open the self-contained, clickable dashboard of the run (lanes + pipeline + gate feed) |
| `/mokata:govern` | The governed-state view — rules, memory-by-kind, read/write ratio, pending proposals |

These call mokata's read-only MCP tools (`progress`/`lanes`/`watch`/`govern`) so you watch the
governed fan-out **without leaving Claude Code**; nothing is written or gated.

**Workflow & utilities (slash commands):**

| Command | What it does |
|---|---|
| `/mokata:enter` | Enter the pipeline at a phase (only that phase's gates apply) |
| `/mokata:exec` | Show or choose the execution mode — sequential (default) or parallel subagents |
| `/mokata:decompose` | Split the approved spec into independent subtasks + a dependency plan, then confirm to run (never silently parallel) |
| `/mokata:chain` | Plan a manual chain of skills; each step keeps its own gate |
| `/mokata:playbook` | Run the full v1 story end-to-end (integration check) |
| `/mokata:resume` | Preview where a run resumes — the phase + the gate that still applies |
| `/mokata:skill` | Author a new skill test-first (RED-GREEN-for-docs); the write is human-gated |
| `/mokata:upgrade` | Update mokata — human-gated pip upgrade or the plugin-update steps |

Everything else is reachable too: read-only inspection (`rules`, `skills`, `suggest`,
`lat_check`, `index_status`, `baseline`, `sessions`, `config_get`, `export_preview`) as MCP
read tools, and durable writes (`config_set`, `export_stack`, …) as **human-gated** MCP write
tools. The complete map — and the install/diagnostic plumbing that stays CLI-or-hook by design
— is the [command-surface reference](../reference/command-surfaces.md), kept honest by a CI
parity test.

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

All three hooks are declared in `hooks/hooks.json`:

- **SessionStart** (`session_start.py`, async/observability) — injects a sub-2k-token
  briefing at the start of each session: which stack you're in, which capabilities are
  live, and the inviolable gates. Nothing to run.
- **Secret guard** (`secret_guard.py`, **PreToolUse, sync security, exit code 2** — matches
  `Write|Edit|MultiEdit|Bash`) — blocks a write, edit, or shell command that would commit or
  send a secret. **Un-overridable:** approval is a methodology gate, never a security override.
- **Gate guard** (`gate_guard.py`, **PreToolUse, sync run-state, exit code 2** — matches
  `Write|Edit|MultiEdit|NotebookEdit`) — blocks a *native* write that breaks the run's
  methodology. Four gates: **`approach-approval`** (the run is still in brainstorm — no approach
  approved yet), **`spec-persisted`** (an approach is approved but no spec is
  emitted), **`no-code-without-failing-test`** (the spec is emitted but no failing test is on
  record), and **`spec-scope`** (the write is outside the spec's authorized surface, spells a
  **deferred** marker you agreed *not* to build, or a spec amend is in progress).

The two PreToolUse blocks are **different in kind**, and mokata treats them differently:

| | secret-guard | gate-guard |
|---|---|---|
| class | **security** | **methodology / run-state** |
| overridable | **never** | **yes — by a human, explicitly** |
| how | — | `mokata gate override <gate> --reason "<why>"` — the reason is required and re-confirmed, the override is **session-scoped**, and it is **written to the audit ledger**. `mokata gate status` shows what's enforced; `mokata gate clear` drops the session's overrides. |

There is deliberately **no env-var kill switch, no MCP tool, and no slash command** for the
override — a model-invocable override would let the model clear its own constraint.

**What it does *not* police, plainly.** The gate-guard fires only inside an **active mokata
run** (an approach approved and/or a spec emitted) — hand-editing a repo outside a run is never
gated. **Test files are always writable** (RED is the *permission* to implement, not a
prohibition). It does **not** match `Bash`, so a `sed -i` through the shell isn't policed. And
if two runs have state in one repo with none pinned, it **fails open** — the gates turn off for
that window and say so once, rather than guess which run your edits belong to
(`mokata windows` shows you when you're in that situation; `MOKATA_SESSION_ID` pins it).

### 3. The CLI alongside the plugin

Everything the slash commands don't surface is available from the same engine via the
[`mokata` CLI](../reference/cli.md) — e.g. `mokata audit`, `mokata budget`, `mokata doctor`,
`mokata coverage`, `mokata query`, `mokata memory`. Any AI tool that can run shell commands
can call these too (see [Integrate with other AI tools](integrate-other-ai-tools.md)).

> mokata **ships its own MCP server** (`mokata-mcp`) — the plugin registers it so mokata's
> operations are callable as native tools. Read tools (query, recall, doctor, coverage, budget,
> audit, status, preview, progress, **lanes / watch / govern**, rules, skills, suggest,
> lat_check, index_status, baseline, sessions, config_get, export_preview, vault_*) expose
> data directly; write tools (remember, init, config_set, export_stack, vault_push,
> import_stack, spec_check, reset, …) are **always human-gated** — a write tool call returns a
> **proposal id** and commits nothing; **you** mint the approval out-of-band in your own terminal
> (`mokata approve <id>`), and only a re-call carrying that id commits, once. Claude cannot
> approve its own write — approve is a **terminal command by default** (`mokata approve <id>`).
> An in-chat MCP approve tool (`mcp__mokata__approve`) also ships but is **opt-in and
> default-OFF** (`settings.approvals.in_chat`); enabling it is itself a human-gated config write,
> and even then Claude Code prompts you on every call, so the model never mints its own consent
> unprompted. There is no approve **slash command**. It **also
> orchestrates external MCP servers** it discovers (H4, `mokata mcp`) and maps them to
> capabilities.
>
> **Full command parity:** every user-facing CLI command is reachable from inside Claude Code
> (a slash command and/or an MCP tool); the install/diagnostic plumbing is explicitly exempted.
> See the [command-surface reference](../reference/command-surfaces.md), enforced by a CI
> parity test.

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
