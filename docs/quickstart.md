# mokata — Quickstart

A fresh user, from zero to a full pipeline run. mokata installs from PyPI with `pip` — no
clone required — and one command wires the whole workflow into Claude Code. It's also a plain
Python package you can run as a CLI anywhere. The full narrative lives in
[Getting started](getting-started.md); the essentials are below.

## 1. Install

### In Claude Code (recommended)

Install once, then let `mokata setup claude` wire the full experience (slash commands + Agent
Skills + MCP tools + hooks + status line) into Claude Code. Runs on your existing Claude Code
sign-in — **no API key**:

```bash
pip install mokata               # MCP server works out of the box on Python ≥ 3.10 (SDK is a default dep)
mokata setup claude              # --profile / --scope options; reverse with `mokata unsetup claude`
# restart Claude Code, then:
mokata mcp status                # expect: mokata-mcp: CONNECTED ✓
```

> Requires **Python ≥ 3.10**.

You now have the workflow commands — `/mokata:brainstorm`, `/mokata:refine`, `/mokata:spec`,
`/mokata:test`, `/mokata:develop`, `/mokata:review`, `/mokata:debug`, `/mokata:optimize`, `/mokata:bug` — plus the SessionStart briefing
and two blocking guards on Claude's own file writes, all automatic: the **secret-guard** (a
secret-bearing write is blocked outright — never overridable) and the **gate-guard** (inside an
active run, an implementation write that skips the spec or the failing test, or strays outside the
spec's scope, is blocked — overridable, with a reason, on the ledger). Hooks are on by default;
`mokata setup claude --no-hooks` opts out. Full guide: [Use mokata without the plugin](how-to/use-without-plugin.md).

A typical run: `/mokata:brainstorm` → approve an approach → `/mokata:spec` (blocked until acceptance
criteria map to tests) → `/mokata:test` → `/mokata:develop` (RED-before-GREEN) → `/mokata:review`. Working on code
you already have? Start with [`/mokata:refine`](how-to/refine-existing-code.md) instead of
`/mokata:brainstorm` — review → approve a scoped set → the same flow.

### The CLI, with any AI tool

Harness-agnostic: use the `mokata` CLI and `mokata-mcp` from any shell- or MCP-capable
assistant (Gemini, Codex), or in scripts and CI — same `pip install`, no clone:

```bash
pip install mokata
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

<!-- mokata:directory-listing:start -->
> ⏳ **Pending Claude plugin-directory approval.** A one-click Claude Code **plugin** is
> **planned, not yet available** — mokata isn't registered on any Claude Code marketplace.
> The supported way to run mokata inside Claude Code today is the pip-first path:
> `pip install mokata` → `mokata setup claude`
> (see [Getting started](https://mokata.ai/getting-started/)).
> _(This notice auto-flips once the listing is approved — single source:
> `scripts/directory_listing.py`.)_
<!-- mokata:directory-listing:end -->

> **Contributing (developers only).** To work on mokata itself, clone the repo and install it
> editable — end users never need this: `git clone https://github.com/JasGujral/mokata-oss.git
> && cd mokata-oss && pip install -e .` (on Python ≥ 3.10 this also pulls the MCP SDK).

The rest of this quickstart shows the **CLI** path; inside Claude Code the slash commands
above do the same thing.

## 2. Initialize a project (CLI)

```bash
mokata init                 # default profile: standard (lean, local: grep + SQLite)
# mokata init --profile full  # or: wire every graph + memory provider (degrade to floors)
```

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
the id no longer matches). A model cannot approve its own write — there is no approve tool or
slash command for it to reach.

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
