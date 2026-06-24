# mokata — Quickstart

A fresh user, from zero to a full pipeline run. mokata is **primarily a Claude Code
plugin** — install it and drive the whole workflow with slash commands inside Claude Code.
It's also a plain Python package (Python ≥ 3.9, no required deps) you can run as a CLI
anywhere.

## 1. Install

### Primary — as a Claude Code plugin (recommended)

In Claude Code:

```text
/plugin marketplace add JasGujral/mokata-oss
/plugin install mokata@mostack
```

Restart Claude Code. You now have the workflow commands — `/brainstorm`, `/spec`, `/test`,
`/develop`, `/review`, `/debug`, `/optimize`, `/bug` — plus the SessionStart briefing and
the secret-guard hook, all automatic. Full guide: [Use the plugin in Claude Code](how-to/use-the-plugin.md).

A typical run: `/brainstorm` → approve an approach → `/spec` (blocked until acceptance
criteria map to tests) → `/test` → `/develop` (RED-before-GREEN) → `/review`.

### Additional — as a CLI (to use the engine outside Claude Code)

For scripts, CI, or other harnesses — clone the repo from GitHub, then install it:

```bash
git clone https://github.com/JasGujral/mokata-oss.git
cd mokata-oss
pip install -e .                 # core (Python ≥ 3.9, no required deps)
# pip install -e ".[schema]"     # optional: richer manifest validation via jsonschema
# pip install -e ".[mcp]"        # optional: the bundled MCP server (Python ≥ 3.10)
```

This puts the `mokata` command on your PATH; run `mokata --help` to confirm.

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
approve one approach**. The approved approach is persisted to `.mokata/state/` and becomes
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
