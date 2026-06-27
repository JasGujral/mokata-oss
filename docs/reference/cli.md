# Reference: CLI

> The CLI is the engine's mechanics — best for scripting, CI, and inspection. It runs the
> deterministic engine with **no LLM attached**: `pip install mokata` gives you the `mokata`
> command **in your terminal only**. It does **not** put mokata inside Claude Code — to drive
> the gated workflow with Claude as the brain, install the
> [Claude Code plugin](../how-to/use-the-plugin.md) or run
> [`mokata setup claude`](../how-to/use-without-plugin.md). (`mokata setup` itself is
> documented below.) Why two ways:
> [How mokata uses an LLM: harness vs CLI](../concepts/execution-model.md).

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
| `--preview` | print the plan and exit **without writing** (dry-run for the human gate) |

`--preview` is the side-effect-free dry-run the `/mokata:init` plugin command runs before
asking you to approve the real write.

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

### `mokata progress [--lanes] [--run <id>] [--ascii]`
Read-only run-progress tracker (done/current/pending + `[done/total]`). **`--lanes`** renders
the **parallel-aware** multi-lane view (one line per concurrent subagent lane; sequential → one
lane), derived from run-state + the execmode ledger records. Degrades cleanly with no active run.

### `mokata watch [--once] [--open] [--run <id>]`
Write a **self-contained** clickable local HTML dashboard of the active run (parallel lanes +
7-phase pipeline + a bounded gate/decision feed) under gitignored `.mokata/temp_local/watch.html`.
`--once` writes one snapshot; otherwise it live-refreshes every 2s; `--open` opens it in a
browser. **Read-only** (never writes durable state / never gates). Respects
`settings.ux.progress` — with the default `terminal` it writes no HTML. Set the tier with
`mokata config set settings.ux.progress {terminal|dashboard|both}`.

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

### `mokata spec-check --symbols <a,b> [--files <x,y>] [--phase <p>] [--yes]`
**Regression guard (Stage 37).** Cross-check a change's touch-set against the **saved specs**
(emitted spec + archive) and **decision memory**; the touch-set is **graph-expanded** so a spec
about an impacted caller is caught. On a hit it surfaces the conflict and routes it through the
**deviation gate**: exit 1 (BLOCKED) until you confirm with `--yes` (amend/supersede) or re-plan;
the conflict **and** resolution are logged. Exit 0 with no conflict. **Degrade-clean:** no saved
specs ⇒ a no-op (no false alarm); no code graph ⇒ a lexical/file-overlap check that says so. Only
the touch-set is checked (frugal). (MCP: `spec_check`, propose-only — `blocked` without
`confirm`.)

## Memory (Part C)

### `mokata memory [--kind <k>]`
Read-only: surface the project "brain" **grouped by kind** (rule / guardrail / best-practice /
context / reference / decision / episodic), the read/write ratio, and any pending self-healing
proposals. `--kind` filters to one category. Commits nothing.

### `mokata memory edit <subject> --value <new> [--kind <k>] [--yes]`
Update an entry (a formula changes, a guardrail is revised). **Human-gated** and routed through
**self-healing**: the old value is **superseded** (kept in the record), the new becomes active —
surfaced, never silently overwritten. `--kind` optionally retypes the entry.

### `mokata onboard`
Launch the guided, LLM-driven capture of typed project knowledge (rules, guardrails,
conventions, domain context, reference docs) — the same protocol as `/mokata:onboard`. Inputs
are distilled, typed, deduped, and **human-gated** before they are stored. Re-runnable any time.

### `mokata memory export [file]` · `mokata memory import <file> [--yes]`
Share memory across repos. **export** writes a committable artifact (default
`<path>/.mokata/memory-share.json` — at the `.mokata/` root, *not* `temp_local/`) carrying the
active items **with provenance**; it's read-only on the source. **import** is a **human-gated**
merge into local memory: it dedups, gate-adds new items, and routes a same-subject-different-
value conflict through the self-healing old→new surface — **never a silent overwrite**;
provenance is preserved. (MCP: `memory_export` / `memory_import`, propose-only without
`confirm`.)

### `mokata memory export [file]` · `mokata memory import <file> [--yes]`
Share memory across repos. **export** writes a committable artifact (default
`<path>/.mokata/memory-share.json` — at the `.mokata/` root, *not* `temp_local/`) carrying the
active items **with provenance**; it's read-only on the source. **import** is a **human-gated**
merge into local memory: it dedups, gate-adds new items, and routes a same-subject-different-
value conflict through the self-healing old→new surface — **never a silent overwrite**;
provenance is preserved. (MCP: `memory_export` / `memory_import`, propose-only without
`confirm`.)

### `mokata memory migrate --to <backend> [--from <backend>] [--drop-source] [--yes]`
Port the **live store** between backends (`sqlite` / `obsidian` / `postgres`) via the
`MemoryBackend` contract — e.g. local SQLite → a shared Postgres, or → the Obsidian vault, and
back. Reads all items and writes them **with provenance** into the destination (resolved from
the manifest's `tools.<backend>.config`). **Human-gated** (previews count + destination),
**idempotent** (re-run upserts by id — no duplicates), and **non-destructive**: the source is
left intact unless you pass `--drop-source` (separately gated). **Degrade-clean** — if the
destination can't be built (e.g. Postgres unreachable) it reports and writes nothing; the
source is never partially migrated. `export/import` shares content as a *file*; `migrate` moves
the *store* between databases.

## Design vault (Part 35d)

### `mokata vault push <name> <file> [--kind brainstorm|spec] [--author NAME] [--force] [--yes]`
Share a brainstorm-plan or spec markdown under `<name>` in the committed/synced vault at
`.mokata/vault/` (the `.mokata/` root, *not* `temp_local/`), carrying provenance (author,
source, kind, timestamps) + a content hash. **Human-gated** (secret-scan + approval, audit-
logged). **Never a silent clobber:** identical re-push is a no-op; a *changed* re-push is
refused unless `--force`, which **versions** it (keeping prior-version metadata).

### `mokata vault list`
List entries (name · kind · version · author · date). Read-only.

### `mokata vault search <query>`
Rank entries by name/title/body overlap (quote a multi-word query). Read-only.

### `mokata vault pull <name> [--dest FILE]`
Write the named artifact to a file for review (default `<name>.md`); verifies the content hash.
Read-only on the vault; provenance preserved. (MCP: `vault_list` / `vault_search` / `vault_pull`
read-only; `vault_push` propose-only without `confirm`.)

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

### `mokata config get <key>` · `mokata config set <key> <value> [--yes]`
Read or update a dotted manifest key — e.g. backend paths (`tools.sqlite.config.path`,
`tools.obsidian.config.vault`, `tools.postgres.config.dsn_env`). `set` is **human-gated**
(preview → confirm; `--yes` skips), validates the result, and **hard-blocks any secret**
(an inline DSN/credential is refused — use an env-var reference). `get` exits non-zero if
the key is unset. See [configure storage backends & paths](../how-to/configure-storage-backends.md).

### `mokata reset [--keep-config] [--backup DIR] [--yes]`
Remove mokata state (`.mokata/`). `--keep-config` keeps `manifest.json` + `constitution.md`
and removes only `memory/`, `state/`, `audit/`. `--backup DIR` moves state there instead of
deleting (reversible). Human-gated unless `--yes`.

### `mokata exec [--parallel] [--isolation] [--fanout]`
Show/select the execution mode for a run (default: sequential gated flow).
