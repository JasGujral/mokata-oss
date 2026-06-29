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
**Human-gated**; JSON files are merged (never clobbered); idempotent. Setup is
**capability-aware**: `claude` wires all three pieces; a portable harness like `codex` (which
lacks PreToolUse hooks + native subagents) wires only what it supports — its commands go to
`.codex/prompts/` and the missing capabilities are stated clearly, never silently skipped (MCP
registration for codex is a documented manual step). See
[Use mokata without the plugin](../how-to/use-without-plugin.md).

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

### `mokata playbook [--parallel] [--fanout] [--dense]`
Run the full story end-to-end on this repo (brainstorm → completeness gate → tests →
implement → review). Prints PASS/FAIL per checkpoint; exit non-zero on failure. `--parallel`
uses subagents (degrades to sequential without a harness); `--fanout` runs concurrently.
**`--dense`** turns on output-density compression of sub-agent handbacks (whitespace/dupe-only,
content-preserving) — frugal, OFF by default; also settable via `settings.governance.output_density`.

### `mokata progress [--lanes] [--run <id>] [--ascii]`
Read-only run-progress tracker (done/current/pending + `[done/total]`). **`--lanes`** renders
the **parallel-aware** multi-lane view (one line per concurrent subagent lane; sequential → one
lane), derived from run-state + the execmode ledger records. Degrades cleanly with no active run.

### `mokata sessions`
List past + active runs — for each: the run id, `[done/total]` phases, the last passed gate,
and the resume point (or `complete ✓`), with the active run flagged. **Read-only**, bounded
(one row per recorded run), friendly empty state when there are none.

### `mokata resume [<id>]`
Preview where a run continues: the phase to resume at (the first phase after the last passed
gate) and the gate that **still applies** there — mokata never auto-runs the pipeline, so the
gates hold on resume. Defaults to the active/most-recent run; pass an `<id>` to target one.
**Read-only**; degrades cleanly with no run (and reports a complete run as nothing to resume).
Continue the run with `mokata enter <phase>` (or the `/mokata:<phase>` command).
A **mid-brainstorm** checkpoint is also resumable: an in-progress `/mokata:brainstorm` (answered
questions + the approaches being weighed) can be left at any step and resumed later — the
HARD-GATE still holds (no spec until an approach is explicitly approved).

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

### `mokata skill author <name> --content-file <f> [--require DOC:MUST-CONTAIN …] [--summary …] [--gate-desc …] [--out …] [--yes]`
Author a new skill via **RED-GREEN-for-docs**: declare doc requirements (`--require`, RED), the
`--content-file` content must satisfy them (GREEN), then the rendered command template is written
through the **universal human-gated WriteGate** (`--yes` approves non-interactively). A RED draft
(unmet requirements) writes nothing; on approval it lands at `.mokata/skills/<name>.md`.

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

### `mokata memory consolidate`
Surface **proposal-only** consolidations of the memory store — merges of duplicate facts,
summaries, and prunes — one bounded line each (silent when there's nothing to propose).
**Read-only: it writes nothing.** Applying a proposal stays the existing human-gated path
(`apply_consolidation`); this command only shows what *could* be consolidated.

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

### `mokata audit [--why] [--tail N]`
Show the append-only audit ledger (every gate decision, tool call, write, …). Add `--why`
for a readable **what + decision + why** timeline of the run — for each entry, what happened,
the decision, and the reason (the deviation's why, the spec-conflict's affected spec/decision,
the self-healing rationale, the gate's message). It is **read-only** and **bounded** (`--tail`,
default 50 — a tail, not the whole history); local-first, and degrades clean when there's no
ledger yet.

### `mokata budget`
Show token savings — a live budget readout (aggregated from the ledger) + a statusline.

## Adapters & distribution (Parts A6/H, J)

### `mokata coverage`
Report capability coverage + unmet gaps + role overlaps (resolved by precedence).

### `mokata mcp`
Discover MCP servers (from `.mokata/mcp.json`) and map them to roles; degrades cleanly
("no servers discovered") when none are present.

### `mokata harness [<name>]`
List the available harnesses and their **capability matrix** (commands / hooks /
context_injection / subagents) — the reference `claude` (all four), the portable `codex`
(commands + context_injection), and `cowork` (commands + context_injection + subagents, but
**not** the PreToolUse hook — see [Use mokata in Cowork](../how-to/use-mokata-in-cowork.md)).
Add a `<name>` to show just one. The engine is
harness-agnostic: a harness lacking a capability degrades with a clear message, never a crash
and never a silent no-op of a gate.

### `mokata export [file]`
Export the current manifest as a shareable stack file (default `<path>/mokata-stack.json`).

### `mokata import <file> [--yes] [--force]`
Validate + apply a shared stack manifest as this repo's config (**human-gated**; rejects an
invalid manifest with exit 1; `--force` overwrites an existing config).

## Lifecycle (Part K)

### `mokata doctor`
Diagnose the manifest/config: missing providers, broken adapters, role conflicts, bad
trust levels, oversized rule tiers. Exit non-zero if any error.

### `mokata baseline [--cmd <test command>]`
Report whether the test suite is **green or red at baseline** before you start — so any new
failure is attributable to your change. Read-only; uses `settings.baseline.test_command` (or
`--cmd`). Degrades clean if no test command is known (mokata never guesses a framework);
exit non-zero only on a red baseline.

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

### `mokata version [--check]`
Print the installed version, the project profile, the install method (pip / plugin /
source), and the Python version. **Offline by default** — local-first, zero network. Add
`--check` to **opt in** to a single outbound call that compares your version to the latest
published release; it is accounted in the audit ledger and **degrades clean** offline (a
blocked/failed check just says it couldn't check — it never errors the command).

### `mokata upgrade [--check] [--method auto|pip|plugin] [--yes]`
Upgrade mokata. For a **pip** install it proposes `pip install -U mokata` and runs it only
after you confirm (**human-gated**; `--yes` approves non-interactively — it never auto-runs
without one or the other). For a **plugin** install it prints the Claude Code steps
(`/plugin marketplace update mostack` + reinstall) since the CLI can't upgrade the plugin
itself. `--check` only reports whether a newer release exists (same opt-in outbound check as
`version --check`). `--method` overrides install-method detection. Inside Claude Code, the
`/mokata:version` command surfaces the same.

### `mokata govern [--open]`
Write a **self-contained, clickable local HTML view of the governed state** — the same
read-only engine/constraints as `mokata watch` (inline CSS, no network/server/assets, under
gitignored `.mokata/temp_local/`). It shows: the **always-on rules & guardrails** (with
line-budget usage), **memory grouped by kind** (rule / guardrail / best-practice / context /
reference / decision — each item with subject, value, and provenance), the **read/write
ratio**, and any **pending self-healing proposals**. Each item surfaces its gated manage
command (`mokata memory edit "<subject>"`) — the dashboard never performs a write. `--open`
opens it in your browser. Degrades clean (no memory → a friendly empty state).
