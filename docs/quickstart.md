# mokata — Quickstart

A fresh user, from zero to a full pipeline run. No clone required: mokata installs as a Claude
Code plugin with two in-app commands, or as an isolated CLI where one command wires the whole
workflow into Claude Code. It's also a plain
Python package you can run as a CLI anywhere. The full narrative lives in
[Getting started](getting-started.md); the essentials are below.

## 1. Install

### In Claude Code (recommended)

**One install line, one setup** (update later with `uv tool upgrade mokata`, which keeps the
extra, then the same setup). Runs on your existing
Claude Code sign-in — **no API key** — and mokata gets its own environment, never your project's:

```bash
uv tool install "mokata[full]"     # mokata + everything full mode uses (or: pipx install "mokata[full]")
mokata setup claude --mode full    # detects + wires it all, installs nothing — runs `mokata init` too if needed
# restart Claude Code, then:
mokata doctor
```

`mokata setup claude` wires the full experience (slash commands + Agent Skills + MCP tools + hooks
+ status line), running `mokata init` for you on a repo with no `.mokata/`. On an existing repo it
keeps your config and memory and refreshes only the wiring — re-run it after every upgrade.
Options: `--profile`, `--scope`; reverse with `mokata unsetup claude`.

**Alternative — the plugin, two commands inside Claude Code** (one route per repo — the plugin or
`setup claude`, not both):

```text
/plugin marketplace add JasGujral/mokata-oss
/plugin install mokata@mostack
```

The plugin's commands are namespaced (`/mokata:<name>`); its MCP server starts through `uvx`, so
[`uv`](https://docs.astral.sh/uv/) must be on PATH. See [Install the plugin](how-to/install-plugin.md).

> Plain `pip install mokata` works too (Python ≥ 3.12; the MCP SDK is a default dep) — but inside
> a project venv this mixes mokata's deps into yours — prefer the above. Every route:
> [Install mokata](how-to/install-mokata.md).

You now have the workflow commands — `/brainstorm`, `/refine`, `/spec`,
`/test`, `/develop`, `/review`, `/debug`, `/optimize`, `/bug` — plus the SessionStart briefing
and two blocking guards on Claude's own file writes, all automatic: the **secret-guard** (a
secret-bearing write is blocked outright — never overridable) and the **gate-guard** (inside an
active run, an implementation write that skips the spec or the failing test, or strays outside the
spec's scope, is blocked — overridable, with a reason, on the ledger). Hooks are on by default;
`mokata setup claude --no-hooks` opts out. Full guide: [Use mokata without the plugin](how-to/use-without-plugin.md).

A typical run: `/brainstorm` → approve an approach → `/spec` (blocked until acceptance
criteria map to tests) → `/test` → `/develop` (RED-before-GREEN) → `/review`. Working on code
you already have? Start with [`/refine`](how-to/refine-existing-code.md) instead of
`/brainstorm` — review → approve a scoped set → the same flow.

### The CLI, with any AI tool

Harness-agnostic: use the `mokata` CLI and `mokata-mcp` from any shell- or MCP-capable
assistant (Gemini, Codex), or in scripts and CI — same isolated install, no clone:

```bash
pipx install mokata              # or: uv tool install mokata · brew · plain pip last
mokata init
```

This puts the `mokata` command on your PATH; run `mokata --help` to confirm. The CLI is the
engine's mechanics (gates, queries, state) — it doesn't supply an LLM, so it's for scripting,
inspection, and wiring into other harnesses rather than writing code on its own. See
[Integrate with other AI tools](how-to/integrate-other-ai-tools.md).

> **A `pip` CLI install is terminal-only.** It does **not** put mokata inside Claude Code. To
> use mokata *in* Claude Code (Claude as the brain, gated workflow), run **`mokata setup
> claude`** (above). Why there are two ways:
> [How mokata uses an LLM: harness vs CLI](concepts/execution-model.md).
>
> `mokata init` configures **this repo** (`.mokata/`); `mokata setup claude` wires **your agent**
> (`.claude/`, and it runs `init` for you). ⚠ Since 0.0.19 the non-interactive
> **`mokata init --yes`** does both — it wires the harness too. Side-by-side, plus the upgrade runbook:
> [Which setup command do I need?](how-to/which-setup-command.md).

<!-- mokata:directory-listing:start -->
> **Installs today as a Claude Code plugin** from mokata's own marketplace — inside Claude
> Code run `/plugin marketplace add JasGujral/mokata-oss`, then `/plugin install mokata@mostack`
> (the plugin starts its MCP server through `uvx`, so [`uv`](https://docs.astral.sh/uv/) must be
> on PATH).
> ⏳ **Pending: the community-marketplace listing** (pending submission) — mokata is not yet
> listed in Claude's community plugin marketplace; until it is, use the two commands above.
> _(This notice auto-flips once the listing exists — single source:
> `scripts/directory_listing.py`.)_
<!-- mokata:directory-listing:end -->

> **Contributing (developers only).** To work on mokata itself, clone the repo and install it
> editable — end users never need this: `git clone https://github.com/JasGujral/mokata-oss.git
> && cd mokata-oss && pip install -e .` (this also pulls the MCP SDK).

The rest of this quickstart shows the **CLI** path; inside Claude Code the slash commands
above do the same thing.

## 2. Initialize a project (CLI)

```bash
mokata init                 # default profile: standard (lean, local: grep + SQLite)
# mokata init --profile full  # or: wire every graph + memory provider (degrade to floors)
```

Prefer to start small and grow? `--mode` is a graduated on-ramp — three named starting points,
each an alias for a profile plus a printed 5-minute quickstart (mutually exclusive with
`--profile`):

```bash
mokata init --mode seatbelt   # just the gates (+ the AST code graph they need) → profile standard
mokata init --mode memory     # the gates + typed project memory that persists → profile standard
mokata init --mode full       # everything the spine can wire → profile full
```

`memory` and `full` also **offer** the optional local embeddings model for semantic recall when
run interactively; `seatbelt` never does. See [Getting started](getting-started.md).

This is a **human-gated** write: `init` shows exactly what it will create
(`.mokata/manifest.json` + `.mokata/constitution.md`) and which tools it detected, then
waits for your confirmation. Use `--yes` for non-interactive setup, `--force` to
overwrite an existing config.

Verify the stack:

```bash
mokata validate     # the committed manifest parses + validates
mokata status       # profile + which capability each need resolves to right now
mokata bootstrap    # the compact SessionStart briefing (under a 2k-token budget)
```

## 3. Brainstorm before you spec (HARD-GATE)

```bash
mokata brainstorm
```

The brainstorm phase explores the problem with you — one question at a time, two or three
real approaches with tradeoffs — and **refuses to let a spec proceed until you explicitly
approve one approach**. The approved approach is persisted to `.mokata/temp_local/state/` and becomes
a constraint the completeness gate checks later. `mokata brainstorm --status` shows whether
an approach has been approved.

## 4. Use any piece on its own

Every capability is a standalone command — no full-pipeline prerequisite:

```bash
mokata skills                  # browse the catalog (cheap; details on demand)
mokata skills test             # reveal one skill's prompt + gate
mokata run review              # run a skill standalone
mokata enter completeness_gate # enter the pipeline at a phase; only that phase's gates apply
```

## 5. Knowledge, memory, governance

```bash
mokata query callers myFunction   # structural query (graph if present, else grep floor)
mokata memory                     # active memory + self-healing proposals (read-only)
mokata rules                      # 4-tier rules and their line budgets
mokata audit                      # the append-only audit ledger
mokata gate status                # what the run-state gates enforce here (read-only)
mokata approve                    # writes waiting on you; `mokata approve <id>` mints the approval
```

Memory is **on by default** (standard/full). It heals by *surfacing* contradictions and
stale facts as an old → new diff for you to approve, edit, or reject — never a silent
rewrite. Every durable memory write is human-gated — and the gate is *yours*: the agent
proposes a write and gets back a proposal id; **you** run `mokata approve <id>` in your own
terminal to mint the approval, which is single-use and content-hashed (change an argument and
the id no longer matches). A model cannot approve its own write: by default there is no approve
tool or slash command for it to reach — and the in-chat MCP approve tool that can be **opted
into** (default-OFF) still forces a human prompt on every call.

## 6. Choose an execution mode, then run end-to-end

```bash
mokata exec                    # default: sequential gated flow (lowest cost)
mokata exec --parallel         # parallel subagents (isolation + two-stage review)
mokata playbook                # drive the full v1 story through the real pipeline
```

`mokata playbook` runs the complete flow — brainstorm → completeness gate (blocked until
ACs map to tests) → tests → RED-before-GREEN implement → review — with the knowledge and
memory layers active, and prints PASS/FAIL per checkpoint. Parallel degrades to the
sequential flow when no subagent harness is available — never a hard failure.

## What you get

- A committed, reviewable `.mokata/` config (manifest + constitution).
- A spec that cannot ship incomplete (every acceptance criterion mapped to a test).
- A full audit trail of every gate decision and tool call.
- Local-first by default — the `minimal` profile performs zero network egress.

**Go deeper:** [The Complete Guide](tutorials/mokata-complete-guide.md) is a hands-on, feature-by-feature tour of every command, gate, and layer — with a downloadable PDF.
