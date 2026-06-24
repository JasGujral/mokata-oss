# Reference: CLI

> The CLI is the engine's mechanics — best for scripting, CI, and inspection. For
> day-to-day building, the primary paths are the [Claude Code plugin](../how-to/use-the-plugin.md)
> or [`mokata setup claude`](../how-to/use-without-plugin.md), where the LLM drives these
> same operations. (`mokata setup` itself is documented below.)

Invoke as `mokata <command>` (console script) or `python -m mokata <command>`.
`mokata --version` prints the version. Most commands accept a shared **`--path PATH`**
(repo root to operate on; default the current directory). Commands that need an
initialized repo load the `Surface` and exit non-zero with a clear error if `.mokata/`
is missing.

## Spine (Part A)

### `mokata init`
Scaffold a valid config: detect installed tools, pick a profile, write
`.mokata/manifest.json` + `.mokata/constitution.md`. **Human-gated** (shows a preview and
waits for confirmation).

| Flag | Meaning |
|---|---|
| `--profile {minimal,standard,full,custom}` | starting profile (default: `standard`) |
| `--yes` | non-interactive; skip the write prompt |
| `--force` | overwrite an existing manifest |

### `mokata setup <harness>`
One command to use mokata in a harness **without the plugin**: runs `init` (if needed),
copies the slash commands into `.claude/commands/`, registers the `mokata-mcp` server in
`.mcp.json`, and wires the SessionStart + secret-guard hooks into `.claude/settings.json`.
**Human-gated**; JSON files are merged (never clobbered); idempotent. Currently supports the
`claude` harness. See [Use mokata without the plugin](../how-to/use-without-plugin.md).

| Flag | Meaning |
|---|---|
| `--scope {project,user}` | install into this repo (default) or `~/.claude` (every project) |
| `--profile {minimal,standard,full,custom}` | profile to init with if not already set up |
| `--no-hooks` | wire only commands + MCP; skip the hooks |
| `--yes` | non-interactive; skip the confirmation prompt |
| `--force` | re-init even if a manifest already exists |

### `mokata unsetup <harness>`
Reverse `mokata setup`: remove the copied commands, the `mokata` MCP entry, and the mokata
hook entries (other entries are preserved). Leaves `.mokata/` config intact. Flags:
`--scope {project,user}`, `--yes`.

### `mokata bootstrap`
Print the SessionStart briefing (which stack you're in, live capabilities, inviolable
gates), capped at a 2,000-token budget. `--show-tokens` prints the token estimate + budget
check to stderr; exit is non-zero if over budget.

### `mokata validate`
Parse + validate the committed manifest; prints a one-line summary. Exit non-zero on an
invalid manifest.

### `mokata route [need]`
Resolve a capability to its tool, showing the attempted fallback chain and the reason.
With no `need`, resolves every declared capability.

### `mokata detect`
Show tool-presence for the whole catalog (present/absent) — no manifest required.

### `mokata status`
One-line stack summary: version, profile, and what each capability resolves to right now.

## Engine & pipeline (Parts D, L)

### `mokata brainstorm [--status]`
Launch the Socratic pre-spec brainstorm (the clean-room protocol + live grounding).
`--status` instead reports whether an approved approach is persisted.

### `mokata enter <phase> [--to <phase>]`
Enter the pipeline at `<phase>` (one of the 7 `PIPELINE_PHASES`); `--to` extends to a
slice. Applies only the run phases' gates; upstream phases are skipped explicitly.

### `mokata preview [--start <phase>] [--to <phase>]`
Dry-run: list planned actions, gates, and file touches with **zero side effects**.

### `mokata playbook [--parallel] [--fanout]`
Run the full story end-to-end on this repo (brainstorm → completeness gate → tests →
implement → review). Prints PASS/FAIL per checkpoint; exit non-zero on failure. `--parallel`
uses subagents (degrades to sequential without a harness); `--fanout` runs concurrently.

## Composability (Part L)

### `mokata skills [name]`
List the skill/command catalog (cheap — names + summaries). With a `name`, reveal that
skill's gate, phase, and full prompt (progressive disclosure).

### `mokata run <name>`
Run a skill standalone (no pipeline prerequisite). `name` is one of the 8 skills. Works
with no init (grounding degrades cleanly).

### `mokata chain <skill> [<skill> …]`
Plan a manual chain of skills; each step keeps its own gate (gates are never bypassed).

### `mokata suggest [flags]`
Suggest a relevant command for the context — **suggest only, never runs**. Flags (all
boolean): `--fresh`, `--spec`, `--failing-test`, `--implementation`, `--diff`, `--bug`,
`--stacktrace`, `--perf`.

## Knowledge (Part B)

### `mokata query <kind> <target> [--depth N]`
Run a structural query: `kind` is `callers`/`callees`/`implementers`/`imports`/
`blast_radius`; `--depth` (default 2) applies to `blast_radius`. Uses the graph if present,
else the grep floor.

### `mokata index`
Build/refresh the per-file freshness index (incremental); report added/changed/removed and
current stale files.

### `mokata lat-check`
Scan `@lat` anchors and flag concept drift. Exit 1 on drift (gate-usable), exit 0 when
clean or inactive (degrades when no anchors/registry).

## Memory (Part C)

### `mokata memory`
Read-only: surface active memory items, the read/write ratio, and any pending self-healing
proposals. Commits nothing.

## Governance & token (Parts F, G, I)

### `mokata rules`
Show the 4-tier rules and their line budgets; exit non-zero if a tier is over cap.

### `mokata audit`
Show the append-only audit ledger (every gate decision, tool call, write, …).

### `mokata budget`
Show token savings — a live budget readout (aggregated from the ledger) + a statusline.

## Adapters & distribution (Parts A6/H, J)

### `mokata coverage`
Report capability coverage + unmet gaps + role overlaps (resolved by precedence).

### `mokata mcp`
Discover MCP servers (from `.mokata/mcp.json`) and map them to roles; degrades cleanly
("no servers discovered") when none are present.

### `mokata harness`
Show the harness boundary's capabilities (commands / hooks / context_injection /
subagents) for the reference Claude Code harness.

### `mokata export [file]`
Export the current manifest as a shareable stack file (default `<path>/mokata-stack.json`).

### `mokata import <file> [--yes] [--force]`
Validate + apply a shared stack manifest as this repo's config (**human-gated**; rejects an
invalid manifest with exit 1; `--force` overwrites an existing config).

## Lifecycle (Part K)

### `mokata doctor`
Diagnose the manifest/config: missing providers, broken adapters, role conflicts, bad
trust levels, oversized rule tiers. Exit non-zero if any error.

### `mokata reset [--keep-config] [--backup DIR] [--yes]`
Remove mokata state (`.mokata/`). `--keep-config` keeps `manifest.json` + `constitution.md`
and removes only `memory/`, `state/`, `audit/`. `--backup DIR` moves state there instead of
deleting (reversible). Human-gated unless `--yes`.

### `mokata exec [--parallel] [--isolation] [--fanout]`
Show/select the execution mode for a run (default: sequential gated flow).
