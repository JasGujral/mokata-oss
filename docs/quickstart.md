# mokata — Quickstart

A fresh user, from zero to a full pipeline run. mokata is **primarily a Claude Code
plugin** — install it and drive the whole workflow with slash commands inside Claude Code.
If you'd rather not use the marketplace, one command wires the same experience into Claude
Code from a checkout. And it's also a plain Python package you can run as a CLI anywhere.

## 1. Install

Three tiers, in order of how most people should reach for them.

### Option 1 (recommended) — the Claude Code plugin

The standard install, from the public marketplace, in Claude Code:

> ⏳ **Pending directory approval (June 2026):** mokata is awaiting review for the Claude plugin
> directory, so it is **not yet installable from Claude's in-app "Browse plugins" directory** —
> use the command below. _(Temporary notice; removed once the listing is live.)_

```text
/plugin marketplace add https://github.com/JasGujral/mokata-oss.git
/plugin install mokata@mostack
```

Restart Claude Code. You now have the workflow commands — `/mokata:brainstorm`, `/mokata:refine`, `/mokata:spec`,
`/mokata:test`, `/mokata:develop`, `/mokata:review`, `/mokata:debug`, `/mokata:optimize`, `/mokata:bug` — plus the SessionStart briefing
and the secret-guard hook, all automatic. Full guide: [Use the plugin in Claude Code](how-to/use-the-plugin.md).

A typical run: `/mokata:brainstorm` → approve an approach → `/mokata:spec` (blocked until acceptance
criteria map to tests) → `/mokata:test` → `/mokata:develop` (RED-before-GREEN) → `/mokata:review`. Working on code
you already have? Start with [`/mokata:refine`](how-to/refine-existing-code.md) instead of
`/mokata:brainstorm` — review → approve a scoped set → the same flow.

### Option 2 — in Claude Code without the public marketplace

Two no-registration ways to get the **same in-Claude-Code experience** without the public
marketplace — handy for the freshest local copy, air-gapped setups, or testing before a
release is public.

**2a — the plugin, from a local clone:**

```text
/plugin marketplace add ~/path/to/mokata-oss
/plugin install mokata@mostack
```

**2b — one command (`mokata setup claude`):** install the CLI once, then let it wire the
full experience (slash commands + MCP tools + hooks) into Claude Code. Runs on your existing
Claude Code sign-in — **no API key**:

```bash
git clone https://github.com/JasGujral/mokata-oss.git
cd mokata-oss
pip install -e ".[mcp]"          # CLI + the bundled mokata-mcp server

cd /path/to/your/project
mokata setup claude              # --profile / --scope options; reverse with `mokata unsetup claude`
```

Either way, restart Claude Code and you have the same commands, briefing, secret-guard, and
MCP tools as the plugin. Full guide: [Use mokata without the plugin](how-to/use-without-plugin.md).

### Option 3 — the CLI, with any AI tool

Harness-agnostic: use the `mokata` CLI and `mokata-mcp` from any shell- or MCP-capable
assistant (Gemini, Codex), or in scripts and CI:

```bash
git clone https://github.com/JasGujral/mokata-oss.git
cd mokata-oss
pip install -e .                 # core (Python ≥ 3.9, no required deps)
# pip install -e ".[schema]"     # optional: richer manifest validation via jsonschema
```

This puts the `mokata` command on your PATH; run `mokata --help` to confirm. The CLI is the
engine's mechanics (gates, queries, state) — it doesn't supply an LLM, so it's for scripting,
inspection, and wiring into other harnesses rather than writing code on its own. See
[Integrate with other AI tools](how-to/integrate-other-ai-tools.md).

> **A `pip` CLI install is terminal-only.** It does **not** put mokata inside Claude Code. To
> use mokata *in* Claude Code (Claude as the brain, gated workflow), install the plugin
> (Option 1/2a) or run **`mokata setup claude`** (Option 2b). Why there are two ways:
> [How mokata uses an LLM: harness vs CLI](concepts/execution-model.md).

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
```

Memory is **on by default** (standard/full). It heals by *surfacing* contradictions and
stale facts as an old → new diff for you to approve, edit, or reject — never a silent
rewrite. Every durable memory write is human-gated.

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
