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

**Magical first run (Stage 56):** run interactively on a fresh repo (or `mokata init --wizard`)
and `init` becomes a guided **Q&A wizard** — it **asks** the profile, **detects** your
integrations (graph backend, memory backend, Postgres / Obsidian / vector), **asks which to
wire**, then **wires them with your approval** (orchestrating `init` + `config` + `setup`).
mokata **detects → recommends → runs with approval** — it **never silently installs** a
third-party tool (an absent one is recommended, not installed). It finishes with a 30-second
"here's what I just did" recap + the next step. The non-interactive `--yes`/`--profile` path is
unchanged for CI.

| Flag | Meaning |
|---|---|
| `--profile {minimal,standard,full,custom}` | starting profile (default: `standard`) |
| `--yes` | non-interactive; skip the write prompt (no wizard) |
| `--force` | overwrite an existing manifest |
| `--preview` | print the plan and exit **without writing** (dry-run for the human gate) |
| `--wizard` | force the guided interactive first-run wizard |
| `--setup-harness` | in the wizard, also wire mokata into the harness (commands + MCP + hooks) |

`--preview` is the side-effect-free dry-run the `/mokata:init` plugin command runs before
asking you to approve the real write.

### `mokata tour`
A 60-second, **read-only** demo of mokata on a tiny sample — a structural **graph query**, a
**memory recall** (in an in-memory store), and a **gate catch** (a real secret scan that hard-
blocks). Writes **nothing** to your repo; safe to run anytime. `--ascii` for ASCII-only glyphs.
Also available as the read-only `tour` MCP tool and the `/mokata:tour` slash command.

### `mokata reconfigure`
The re-runnable reconfigure wizard (Stage 56b): the **same guided Q&A** as first-run setup, run
any time on an **already-initialized** repo to **change what's wired** — add/remove an
integration, switch a backend, change profile, or pick up a newly-installed tool. Composes
`init` / `config` / `setup` / `unsetup` (nothing rebuilt). It **re-detects** your tools, shows a
**current→proposed diff**, then applies behind **one human gate**.

- **Idempotent** — re-running with no changes is a **no-op** (nothing written).
- **Human-gated** — decline and nothing changes.
- **Reversible** — `--remove` cleanly unwinds an integration with **no residue** (gone from the
  capability chain *and* the tools table; ties to `unsetup`/`reset`).
- **Never silently installs** — an absent `--add` tool is **recommended** (e.g.
  `pip install 'mokata[postgres]'`), not installed.

| Flag | Meaning |
|---|---|
| `--profile {minimal,standard,full,custom}` | switch the profile (default: keep current) |
| `--add TOOL` | wire a **detected** integration (repeatable; absent → recommended) |
| `--remove TOOL` | cleanly unwire an integration (repeatable; no residue) |
| `--set KEY=VALUE` | switch a backend setting in the manifest (repeatable; gated) |
| `--wire-harness` / `--unwire-harness` | add/remove the harness wiring (commands + MCP + hooks) |
| `--scope {project,user}` | harness scope for the harness flags |
| `--yes` | non-interactive; apply the explicit changes without prompting |

Inside Claude Code this is the **`/mokata:reconfigure`** slash command and the gated `reconfigure`
MCP tool (returns the diff with no `approve`, applies with `approve=true`).

### `mokata setup <harness>`
One command to use mokata in a harness **without the plugin**: runs `init` (if needed),
materializes the `/mokata:` command set into the harness's NATIVE surface, registers the
`mokata-mcp` server where the agent's MCP schema matches, and wires the SessionStart +
secret-guard hooks where supported. **Human-gated**; JSON files are merged (never clobbered);
idempotent; reversible (`unsetup` leaves no residue). Setup is **capability-aware**: it wires
ONLY what a harness actually supports and states the rest clearly, never silently skipped or
pretended.

Supported harnesses (Stage 52 + Stage 63): `claude`, `codex`, `cursor`, `copilot`,
`windsurf`, `gemini`, `aider`. Each maps to its native command surface — e.g. Cursor
`.cursor/commands/*.md`, Copilot `.github/prompts/*.prompt.md`, Windsurf
`.windsurf/workflows/*.md`, Gemini `.gemini/commands/*.toml`, Aider reference prompts
(Aider has no native slash-command files). MCP is auto-registered for `claude`, `cursor`, and
`gemini` (`mcpServers` schema); a documented manual step for `codex`/`copilot`/`windsurf`.
See [Use mokata with other AI agents](../how-to/use-with-other-agents.md). Inside Claude Code,
the **`/mokata:setup`** guided wizard (Stage 56) drives the same detect → ask → wire flow.

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

### `mokata release-check [version] [--root <checkout>]`
Release plumbing (pure/offline). Assert every version field — `pyproject.toml`,
`.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` (metadata + `plugins[0]`),
and `src/mokata/__init__.py` `__version__` — equals the intended tag (default: this
package's version; `--root` checks another checkout, e.g. the public mirror before tagging).
Exits non-zero **naming each offender** — the `scripts/release.sh` preflight that refuses to
tag a commit whose versions lag the tag (the 0.0.4 lesson).

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

### `mokata skills [name]` · `mokata skills search <query>`
List the skill/command catalog (cheap — names + summaries). With a `name`, reveal that
skill's gate, phase, and full prompt (progressive disclosure). `search <query>` filters the
catalog by keyword — a discoverable skill catalog (Stage 70). Read-only.

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

### `mokata ci-check [--files <a,b>] [--base <ref>] [--symbols <s,…>] [--comment-file <p>] [--no-fail] [--ascii]`
**mokata as a CI / PR check (Stage 58).** Runs two gates over a pull request's **changed files**
and reports PASS/BLOCK (exit non-zero on a real block): the **completeness gate** (does the saved
spec still map every acceptance criterion to a test?) and the **spec-awareness** regression guard
(does the change touch a previously saved spec/decision?). Changed files come from `--files`
(comma-separated) or `--base <ref>` (via `git diff`); `--symbols` default to the symbols defined in
those files. `--comment-file` writes the **PR review-comment body** (markdown); `--no-fail` makes
it report-only (always exit 0). **READ-ONLY** — it *surfaces* blocks and *produces* the comment; it
never posts to GitHub (the workflow's own `GITHUB_TOKEN` does). **DEGRADE-CLEAN — it never
false-blocks:** an uninitialized repo, no saved spec, no spec corpus, or a repo that doesn't tag
tests with AC ids all PASS. Used by the reusable [`mokata-check` GitHub Action](../how-to/mokata-as-a-pr-check.md). (MCP: `ci_check`, read-only.)

## Memory (Part C)

### `mokata memory [--kind <k>] [--all] [--project <id>] [--list-projects]`
Read-only: surface the project "brain" **grouped by kind** (rule / guardrail / best-practice /
context / reference / decision / episodic), the read/write ratio, and any pending self-healing
proposals. `--kind` filters to one category. Commits nothing.

**Project scoping (Stage 71a).** On a **shared** backend (a team Postgres DSN that can host many
projects), review **defaults to the current project** — no cross-project bleed. `--all` reviews
across every project; `--project <id>` reviews a specific one; `--list-projects` prints the projects
present on the shared backend and exits. Local SQLite/Obsidian are already per-repo and ignore these.
See [Multi-project on one shared backend](../how-to/multi-project-shared-backend.md).

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

### `mokata session push <tag> [--to local|vault|postgres] [--run ID] [--author NAME] [--force] [--yes]`
Package the **current session** (the resumable run checkpoint(s) + approved approach + emitted
spec + in-progress brainstorm) into a **machine-path-free, versioned** bundle carrying provenance
(author, source, created) + a content hash + a repo fingerprint, shared over the chosen
**transport** (`--to`): `local` (default, `.mokata/session-bundles/<tag>.json`), `vault` (the
committed/synced `.mokata/vault/sessions/`, so it travels with the repo), or `postgres` (a shared,
owned DB table reached by `$MOKATA_SESSION_PG_DSN` / `$MOKATA_PG_DSN`). **Human-gated +
secret-scanned on EVERY transport** (secret = hard block, audit-logged). **Never a silent
clobber:** an identical re-push is a no-op; a *changed* re-push is refused unless `--force`. No
session in progress → a friendly no-op. `--run` scopes to one run id (default: every recorded run).
The Postgres leg is **opt-in & local-first** — no psycopg/DSN → degrades clean (clear message, no
crash, never a silent fallback to a less-secure store).

### `mokata session pull <tag> [--from local|vault|postgres] [--into REPO] [--force] [--yes]`
Read the tagged bundle over the chosen transport (`--from`, default `local`), **verify its content
hash** (corruption caught from any source, not served), then **re-hydrate** it into the target repo
(`--into`, default this repo) so `mokata resume` continues. The bundle is **untrusted**, so this is
**human-gated + secret-scanned on pull, on every transport** (a secret is a hard block approval
can't override). A **cross-codebase fingerprint mismatch** is **surfaced** and *not* applied unless
`--force`. The HARD-GATE survives the round-trip: a not-yet-approved brainstorm stays **not**
approved after pull.

### `mokata session name <tag> <new> [--to local|vault|postgres] [--force] [--yes]`
Rename a tagged session to a human-friendly name (what `push`/`pull`/`resume` and the status badge
read). **Human-gated** where it writes durable; **idempotent** (renaming to the current name is a
no-op); a name **collision is refused unless `--force`** (never a silent clobber). **Provenance is
preserved** (original author/source/created + a `prior_names` trail) and the content-hash is
untouched.

### `mokata session list [--all] [--project <id>] [--list-projects]`
List the tagged bundles, **spanning local + the committed vault (+ shared Postgres when a DSN is
set)** — each row tagged with its transport (tag @transport · resume point · author · date).
Read-only; degrade-clean (an unavailable remote is skipped). (MCP: `session_list` read-only and
transport-spanning; `session_push` / `session_pull` / `session_name` propose-only without
`confirm`.)

**Project scoping (Stage 71a).** On a shared Postgres backend the listing **defaults to the current
project** (a tag like `auth` never collides across projects). `--all` spans every project;
`--project <id>` selects one; `--list-projects` enumerates the projects present. Run from **outside**
a project (a bare directory) against a shared DSN, mokata **refuses to dump every project's
sessions** — it asks you to choose `--all` / `--project` or run `--list-projects`.

## Governance & token (Parts F, G, I)

### `mokata rules`
Show the 4-tier rules and their line budgets; exit non-zero if a tier is over cap.

### `mokata audit [--why] [--team] [--share] [--tail N] [--yes]`
Show the append-only audit ledger (every gate decision, tool call, write, …). Add `--why`
for a readable **what + decision + why** timeline of the run — for each entry, what happened,
the decision, and the reason (the deviation's why, the spec-conflict's affected spec/decision,
the self-healing rationale, the gate's message). It is **read-only** and **bounded** (`--tail`,
default 50 — a tail, not the whole history); local-first, and degrades clean when there's no
ledger yet.

**Team audit / shared activity log (Stage 71) — shared OR local, conflict-free, NO telemetry.**
By default your audit log is **LOCAL** (the JSONL above). A team can *optionally* publish those
same entries to the team's **OWN** managed Postgres (Stage 69's BYO DB — an env-var DSN) so
everyone can see **who did what** across the governed brain — **without anything ever being
phoned home** to mokata/Anthropic. The data is the team's, on the team's storage.

- `mokata audit --team` — the team-wide **who-did-what / why** over the **shared** log (spans all
  actors). Read-only; degrades clean (sharing off / backend absent → a clear message, your local
  log unaffected).
- `mokata audit --share [--yes]` — publish **your new** local entries to the team's shared log.
  **Opt-in** (`mokata config set settings.audit.shared true`, plus `settings.audit.dsn_env` for
  the env-var name). The publish is the only moment data leaves the machine, so it is
  **human-gated + secret-scanned** (a secret is a hard block). Entries are **append-only +
  per-actor + namespaced**, so concurrent teammates never clobber each other. The **DSN secret is
  never stored** (only the env-var name). No driver/DSN → it stays **LOCAL** (degrade-clean, no
  crash). See [Team audit / shared activity log](../how-to/team-audit.md).

**Project scoping of the shared read (Stage 71a).** The team read is namespaced by the same stable
project key every shared backend uses, so `mokata audit --team` **defaults to the current project**.
Add `--all` to span every project, `--project <id>` for a specific one, or `--list-projects` to see
the projects present on the shared log.

### `mokata budget`
Show token savings — a live budget readout (aggregated from the ledger) + a statusline.

### `mokata bench`
Measure **wall-clock latency** of the hot paths (statusline, briefing, secret scan, grep query,
recall, status) against their budget — read-only, dependency-free (median of N). Distinct from
`mokata budget` (tokens). `--repeat N` sets the sample count. See
[performance / latency budget](performance-budget.md).

## Adapters & distribution (Parts A6/H, J)

### `mokata coverage`
Report capability coverage + unmet gaps + role overlaps (resolved by precedence).

### `mokata mcp`
Discover MCP servers (from `.mokata/mcp.json`) and map them to roles; degrades cleanly
("no servers discovered") when none are present.

### `mokata harness [<name>]`
List the available harnesses and their **capability matrix** (commands / hooks /
context_injection / subagents) — the reference `claude` (all four), the portable `codex`
(commands + context_injection), `cowork` (commands + context_injection + subagents, but
**not** the PreToolUse hook — see [Use mokata in Cowork](../how-to/use-mokata-in-cowork.md)),
and the Stage-63 agents `cursor` / `copilot` / `windsurf` / `gemini` (commands +
context_injection) and `aider` (context_injection only — no native slash commands). Add a
`<name>` to show just one. The engine is harness-agnostic: a harness lacking a capability
degrades with a clear message, never a crash and never a silent no-op of a gate. See
[Use mokata with other AI agents](../how-to/use-with-other-agents.md).

### `mokata export [file]`
Export the current manifest as a shareable stack file (default `<path>/mokata-stack.json`).

### `mokata import <file> [--yes] [--force]`
Validate + apply a shared stack manifest as this repo's config (**human-gated**; rejects an
invalid manifest with exit 1; `--force` overwrites an existing config).

### `mokata stacks <list|search|show|install> [target] [--source <dir>] [--yes] [--force]`
Community stacks & skill marketplace (Stage 70) — **no hosted marketplace**; publish over
git/the vault, discover a reviewable versioned `index.json`, install via the gated adopt path.
`list` (default) / `search <query>` / `show <name>` **read** the curated catalog (bundled, or a
git-org/vault one via `--source`); read-only, degrade-clean (no index/source → a clear message).
`install <name>` is the **human-gated, secret-scanned adopt** path: it secret-scans the stack
manifest (a secret is hard-blocked), then applies it as your config (`--yes` approves;
declining writes nothing; `--force` overwrites an existing config). The curated guardrails +
recommended skills land in your manifest's `settings.stack` (reviewable). See
[community stacks](../how-to/community-stacks.md) and [install mokata](../how-to/install-mokata.md).

### `mokata team <join|status|adopt|connect|disconnect>`
Zero-setup team sync. **`join <source>`** (Stage 70b) is the guided onboarding path: it runs
`adopt` → `connect` → vault `pull` → `onboard` → `doctor` **in order**, each a confirmable step,
and prints a "here's what you're now wired to" summary. Options: `--dsn-env <ENV>` (shared
memory), `--vault <repo-or-dir>` (pull the shared design/spec vault), `--yes` (non-interactive),
`--force` (overwrite config on adopt). Every writing step is **human-gated**, the untrusted pulls
are **secret-scanned**, and a step whose source/backend/driver is absent is **skipped with a
note** (never a blocker); it is **idempotent** and **reversible**. The individual steps still
exist: `status` (read-only) shows whether shared memory/sessions are local-only or pointed at a
managed Postgres; `adopt <source>` pulls a teammate's governed stack (shared manifest + vault +
shared-memory pointer) in one **human-gated, secret-scanned** step (`--force` to overwrite);
`connect --dsn-env <ENV>` points shared memory + sessions at your **own managed Postgres** via an
env-var DSN (the DSN value is **never stored** — only the env-var name); `disconnect` reverses it.
**mokata hosts nothing**; degrade-clean with no driver/DSN. See
[team setup](../how-to/team-setup.md).

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

### `mokata decompose [--run] [--ascii] [--yes]`
Propose an **independent-subtask split** of the emitted spec's acceptance criteria (one
subtask per AC) plus a **dependency plan** — subtasks that touch the same symbol/file are
kept ordered (`depends_on`), using the code graph to verify independence when one is wired,
the lexical floor otherwise. With no flags it prints the **read-only** split. `--run`
**human-gates** the confirm, then feeds the confirmed tasks into the *existing* flow
(`resolve_execution_choice` → `run_tasks`): the cost estimate is shown, parallel-vs-sequential
is asked (default sequential), isolation + two-stage review apply, and it degrades to
sequential when subagents are unavailable. **Conservative:** it never silently parallelizes
work that might be dependent — when independence is unverified (no graph) or dependencies
exist, concurrent fan-out is withheld and isolated tasks run in declared order. Inside Claude
Code: the `decompose` MCP read tool (proposes the split) and `/mokata:decompose`. Degrades
clean with no spec/ACs.

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

### `mokata govern [--open] [--live] [--once]`
Write a **self-contained, clickable local HTML view of the governed state** — the same
read-only engine/constraints as `mokata watch` (inline CSS, no network/server/assets, under
gitignored `.mokata/temp_local/`). It shows: the **"what changed since last session" diff**
(new/changed memory, new rules, and the gate decisions made since the last session baseline),
the **always-on rules & guardrails** (with line-budget usage), **memory grouped by kind** (rule
/ guardrail / best-practice / context / reference / decision — each item with subject, value,
and provenance), the **read/write ratio + memory-health nudge**, and any **pending self-healing
proposals**. Each item surfaces its gated manage command (`mokata memory edit "<subject>"`) —
the dashboard never performs a write. `--open` opens it in your browser. `--live` auto-refreshes
(re-writes on a 2s interval + a self meta-refresh, honouring `settings.ux.progress` — the
dashboard tier; Ctrl-C to stop); `--once` forces a single static snapshot. Degrades clean (no
memory → a friendly empty state; first session → "no prior snapshot to compare yet").
