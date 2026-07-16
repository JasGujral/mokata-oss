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
MCP tool — which returns the diff plus a `proposal_id` and writes nothing until you approve it with
[`mokata approve <id>`](#mokata-approve-proposal-id---yes---actor-who).

### `mokata setup <harness>`
One command to use mokata in a harness **without the plugin**: runs `init` (if needed),
materializes the `/mokata:` command set into the harness's NATIVE surface, registers the
`mokata-mcp` server where the agent's MCP schema matches, and wires the hooks where supported — the
SessionStart briefing plus **both** PreToolUse blocks, the `secret-guard` and the
[`gate-guard`](#mokata-gate-statusoverrideclear) (`--no-hooks` opts out). Only the `claude` harness
declares the `hooks` capability. **Human-gated**; JSON files are merged (never clobbered);
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
package's version; `--root` checks another checkout before you tag it).
Exits non-zero **naming each offender** — the release preflight that refuses to tag a commit
whose versions lag the tag (the 0.0.4 lesson).

### `mokata branch-protection-check [--repo <owner/repo>] [--branch <name>]`
Release plumbing (**fail-closed**). Verify the public mirror's default branch is protected —
no force-push, no deletion, required status checks — by reading protection via the `gh` CLI
(which supplies its own auth: your login locally, `GH_TOKEN` in CI). Exits non-zero on **any**
inability to prove it: protection absent, `gh` unavailable/unauthed, the API errors, or unsafe
settings. The release preflight runs this so no release proceeds onto an unprotected `main`.
Defaults: `--repo JasGujral/mokata-oss --branch main`. No token is hard-coded or accepted.

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

### `mokata spec emit --file <spec.json> [--yes]` · `mokata spec show`
**Emit the spec — the only path a spec reaches disk.** In normal use you never hand-write the JSON:
the **model** composes the spec during `/mokata:spec` and emits it for you. `emit --file` is the
**advanced / CI escape hatch** for a scripted flow (`--file -` reads stdin).

`emit` puts the spec through two gates: the **completeness gate** first — every acceptance criterion
must map to a test, or the emit is refused and **nothing is written** — then the **human write gate**
(off a TTY it fails closed; `--yes` is a human's own non-interactive flow). A committed emit writes
**both** this run's `emitted_spec` *and* the shared `spec_corpus` in one gated commit, so a spec on
record is always a spec `spec-check` can see.

This is what unblocks implementation: the `spec-persisted` run-state gate reads exactly the key this
writes. `show` prints the run's persisted spec (read-only).
(MCP: `spec_emit` — human-gated: propose → `mokata approve <id>` → commit.)

**Scope (what the `spec-scope` gate reads).** A spec can carry a machine-checkable **scope**: the
**authorized surface** (where this change is allowed to land) and the **deferred items** — the things
you and the model explicitly agreed *not* to build, each with the paths they'd live in and the
literal **markers** they'd spell in code (e.g. `batch_update`, matched case-insensitively as a
substring of the content being written). Both are **authored by the model** as it emits the spec,
from what you agreed in brainstorm. The `gate-guard` hook then enforces it: a write outside the
authorized surface, or one whose **content spells a deferred marker** — even inside an otherwise
authorized file — is an **exit-2 block** naming the deferred item. Your levers when it fires:
`mokata spec amend` (re-approve the wider scope — see below), `mokata spec amend --abort`, or
`mokata gate override spec-scope --reason "<why>"`. A spec with no scope section is **not policed**
(fail-open) — every spec written before this feature is in that case.

### `mokata spec amend --file <spec.json> --reason <why> [--item <id>] [--yes]` · `mokata spec amend --abort`
**A FORCED PHASE REGRESSION — not a text edit.** The only road back when a write is out of scope.
Calling it regresses the run `develop → SPEC` **immediately**: development writes are blocked
until the amendment lands. The amended spec (send the WHOLE spec, not a patch) must then re-earn
every gate — **completeness** (each criterion, old and new, maps to a test), the **blast-radius
lens** (re-run only when the scope *widens*), and a **human approval**. It persists as **vN+1**
with **vN superseded (never deleted)** and the diff on the audit ledger; the run then resumes from
the last passed gate (P17), and the new criteria **owe a failing test** before implementation of
them proceeds. `--abort` abandons an amendment in progress and unblocks writes (changes no spec;
ledgered). (MCP: `spec_amend` — human-gated; the model may propose an amendment, never approve it.)

### `mokata plan [list | show [<slug>] | export [<slug>] [--to <dir>] [--force]]`
Browse the brainstorm **plan files**. On approach approval mokata saves the design write-up to
an internal `.mokata/temp_local/plans/<slug>.md` (before any spec). `list` shows the saved plans;
`show` prints one (defaults to the sole plan); `export` copies it to a project-visible
`plans/<slug>.md` you can commit (default dir `plans/`, override with `--to`). Export is
user-initiated — it never silently clobbers an existing copy; pass `--force` to overwrite.

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

### `mokata windows`
List the **live Claude Code windows** on this repo — each open window is its own MCP process,
otherwise invisible to the others. For each: the short session id, when it started, `live`/`stale`
(a `stale` window's process has exited), and its current pipeline phase. **Read-only** (the calling
window self-registers and dead-pid windows are pruned lazily; the registry is transient state under
`.mokata/temp_local/`, never a gated durable write). Distinct from `mokata sessions`, which lists
pipeline *runs* (and from `mokata session`, which manages shareable session *bundles*).

Each window row also shows its **worktree** (`main` for the primary checkout, or the worktree's
relative path) and its **scope** (what that session is working on, if recorded). When another
mokata window is live on the same repo, `windows` also surfaces a one-time, human-gated **worktree
offer** — a suggestion to isolate your working tree; it never creates anything.

### `mokata worktree create [<topic>] [--yes]`
Create a **git worktree** to isolate this session's working tree, so two Claude Code windows on one
repo stop colliding on the files themselves. mokata asks what this session is working on (the
*scope*), recommends a topic-aware branch/dir name (derived from the topic and the other live
sessions' phases/scopes), and **confirms explicitly** before running `git worktree add` — the only
durable action here (fail-closed: a non-interactive session declines unless `--yes`). Refuses
politely outside a git repo. Two worktrees of one repo keep **one** team project identity, so team
memory/journal/audit never fork across them.

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
Run a skill standalone (no pipeline prerequisite). `name` is any skill listed by `mokata skills`.
Works with no init (grounding degrades cleanly).

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

### `mokata graph`
Adopt or inspect the code graph. `mokata graph adopt [code-review-graph|serena]` (default
`code-review-graph`) pins a real structural graph into the manifest through the human gate (the
embedded AST floor stays the fallback, so adoption is recommended, never required). `mokata graph
status` reports which backend actually answers today (graph or floor), and whether semantic
search is available.

### `mokata spec-check --symbols <a,b> [--files <x,y>] [--text <desc>] [--phase <p>] [--allow-degraded] [--reason <why>] [--yes]`
**Regression guard (Stage 37).** Cross-check a change's touch-set against the **saved specs**
(emitted spec + archive) and **decision memory**; the touch-set is **graph-expanded** so a spec
about an impacted caller is caught. On a hit it surfaces the conflict and routes it through the
**deviation gate**: exit 1 (BLOCKED) until you confirm with `--yes` (amend/supersede) or re-plan;
the conflict **and** resolution are logged. Exit 0 with no conflict. **Degrade-clean:** no saved
specs ⇒ a no-op (no false alarm); no code graph ⇒ a lexical/file-overlap check that says so. Only
the touch-set is checked (frugal). **`--allow-degraded` (GR.S3):** with `graph.required` on (the
default), a touch-set that fell to the grep floor is refused as decision input; `--allow-degraded`
explicitly accepts the degraded evidence for the session — TTY-reconfirmed, ledgered, and the
result stays marked degraded — with `--reason <why>` recording why. (MCP: `spec_check`,
propose-only — `blocked` without `confirm`.)

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

### `mokata session save`
Snapshot this session's **in-flight** state — the brainstorm progress, the approved approach (if one
is approved), and the pipeline resume checkpoints — so [`mokata resume`](#mokata-resume-id) can
continue it after an interruption. **Ungated and local-only**: a local save is your own transient
state, and mokata's human gate sits at the **share** boundary (`session push`), not here. It writes
nothing durable, sends nothing anywhere, reports the keys + counts it saved (never the content), and
is idempotent. A session with nothing to save is an honest empty result, not an error. mokata also
**autosaves** as you work — that is model-driven, silent on success, and surfaces only on failure;
you never have to call it.

### `mokata session push <tag> [--to local|vault|postgres] [--run ID] [--author NAME] [--save-first] [--allow-in-progress] [--requirements-only] [--force] [--yes]`
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

| Flag | Meaning |
|---|---|
| `--save-first` | run the ungated `session save` **first**, then bundle — one atomic action, no gap between what you see and what you share |
| `--allow-in-progress` | consent to share **unfinished thinking** (a brainstorm with no approved approach). **Required**: without it, such a push is **refused** and nothing is written |
| `--requirements-only` | bundle **only the distilled requirements** (anchor + goal + constraints + requirement lines) as a **cross-repo handoff** — no approaches, no approval, no transcript; the repo-fingerprint check is replaced by an origin label. An *alternative* consent to `--allow-in-progress` |

### `mokata session pull <tag> [--from local|vault|postgres] [--into REPO] [--force] [--yes]`
Read the tagged bundle over the chosen transport (`--from`, default `local`), **verify its content
hash** (corruption caught from any source, not served), then **re-hydrate** it into the target repo
(`--into`, default this repo) so `mokata resume` continues. The bundle is **untrusted**, so this is
**human-gated + secret-scanned on pull, on every transport** (a secret is a hard block approval
can't override). A **cross-codebase fingerprint mismatch** is **surfaced** and *not* applied unless
`--force`.

Bundles are **schema v2**; a v1 bundle still pulls fine, and a bundle **newer** than this reader is
**refused**, never silently downgraded. **The approach approval never crosses machines**: on pull the
`approved_approach` record is dropped and the brainstorm's approved flag is cleared, with
`imported: approval not transferred — re-approve on this machine (HARD-GATE)` appended to the record.
Be precise about the boundary — the **emitted spec does cross intact** (it is not de-approved), and
write proposals, gate overrides, and TDD red/green state are **never bundled at all**.

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

### `mokata gate status|override|clear`
The **run-state gates** — the ones the `gate-guard` PreToolUse hook enforces on *native*
`Write`/`Edit`, so writing code before the spec, before a failing test, or **outside the spec's
approved scope** is blocked by an exit code rather than a sentence the model can ignore.

- `mokata gate status` — what is enforced here, and what is overridden. Read-only.
- `mokata gate override <gate> --reason "<why>"` — stop enforcing **one** gate for **this session**.
- `mokata gate clear` — drop this session's overrides and enforce again.

**Three** gates are enforced, and only inside an **active mokata run** (a repo you're hand-editing
outside a run is never policed):

| gate | blocks a native write to an implementation file when… |
|---|---|
| `spec-persisted` | an approach is approved for this run but **no spec is emitted** |
| `no-code-without-failing-test` | the spec is emitted but **no failing test is on record** |
| `spec-scope` | the write falls **outside the spec's authorized surface**, its content **spells a deferred marker** (even inside an authorized file), or a **`mokata spec amend` is in progress** (the run has regressed to SPEC — every development write is blocked until the amendment lands or is aborted) |

A **test file is always writable** — you must be able to write the failing test. RED is the
*permission* to implement, not the prohibition.

A block is a single stderr line and **exit code 2** — `BLOCKED [<gate>] <reason>` — naming the file,
what to do, and the override. Two honest limits: the `gate-guard` hook matches
`Write|Edit|MultiEdit|NotebookEdit` and **not `Bash`**, so a shelled `sed -i` is *not* policed by the
run-state gates (the `secret-guard` **does** match `Bash`); and only the **`claude`** harness declares
the `hooks` capability, so on `cursor`/`gemini`/`cowork`/`windsurf` the gate-guard is never wired and
the run-state gates enforce **nothing** (`mokata harness` shows the matrix).

Overrides follow P14 exactly: **explicit** (you name the gate and give a `--reason`),
**re-confirmed** (an interactive y/N), **session-scoped** (it expires with the session — nothing to
remember to turn off), and **ledgered** (`mokata audit` shows who/when/which gate/why, forever).
There is deliberately **no env-var kill switch**: an env var is a side door any process can open
silently, not a human decision. The **secret-guard is never overridable** — a security block is not
a methodology gate.

Ambiguity **fails open**. If two mokata runs have state in one repo and none is pinned, the gates turn
**off** for that window and say so once: mokata will not guess which run your edits belong to, because
guessing could block on another window's state. Two ways out — give each window its own working tree
([`mokata worktree create`](#mokata-worktree-create-topic---yes)), or **pin** the run by exporting
`MOKATA_SESSION_ID`, which restores enforcement when you *do* want two windows on one tree.
[`mokata windows`](#mokata-windows) lets you *see* you're in that situation; it is a visibility tool,
not the disambiguator.

### `mokata approve [<proposal-id>] [--yes] [--actor <who>]`
Approve **one** proposed durable write — the act an MCP tool **cannot perform**.

mokata's write tools (`remember`, `config_set`, `session_push`, `init`, …) are **propose-only**:
calling one stages the change, returns a `proposal_id`, and writes nothing. Only an approval *you*
mint here — in your own terminal, in a process the model is not driving — lets that write land.

```
mokata approve                    # list what is waiting on you
mokata approve p-3f9a2c11b4de     # see the write in full, then approve it
```

The model can only **reference** an approval by id; it can never mint one. `approve=true` on a tool
call is accepted for schema stability but **commits nothing** — it never was consent, it was a flag
the model typed itself.

An approval is **single-use** (it licenses exactly one commit, then it is burned),
**content-hashed** (change an argument and the id changes, so "get X approved, then commit Y" is
arithmetically impossible), **session-scoped**, and **expires** (15 minutes). It is **ledgered** —
`mokata audit` shows the proposal hash, who approved it, and the write it licensed, forever.

Off a TTY it **fails closed**: a non-interactive shell cannot approve by accident. A genuinely
non-interactive *human* flow (CI, a script) passes `--yes` — that is your own environment saying it,
which is exactly the thing a tool parameter is not. The **secret-guard still hard-blocks** an
approved write: approval is a methodology gate, never a security override.

### `mokata rules`
Show the 4-tier rules and their line budgets; exit non-zero if a tier is over cap.

### `mokata audit [--why] [--team] [--share] [--consent show|grant|revoke] [--tail N] [--yes]`
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

**Standing audit-publish consent (TM.S4).** `mokata audit --consent show|grant|revoke` manages the
**revocable standing consent** for the batched publish. Granting it once (**human-gated + ledgered**,
captured during `mokata team join`) lets the batched publish proceed **without re-prompting per
batch** — while the **per-publish secret-scan still hard-blocks** (never a governance bypass). Revoke
any time to return to per-batch human-gating. See
[Team mode — setup & operations](../how-to/team-setup.md#security).

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

### `mokata team <init|join|status|adopt|connect|disconnect>`
**`init`** (TM.S3) is first-time team setup — the **sole owner of DDL**. It guides a backend pick
(`--backend managed|compose|local`; managed DSN is the golden path), **fails closed** with a named
fix when `$MOKATA_PG_DSN` is unset (writing nothing), runs **one idempotent provision pass** that
creates the shared tables (`mokata_memory`, `mokata_session_bundle`, `mokata_audit_log`,
`mokata_events`) + the `mokata_schema_version` row on **vanilla Postgres ≥14, no extensions**,
**pins** the team project identity (`settings.project.id`, human-gated) so clients don't split by
path-hash, and runs the **live CONNECTED test** (the same probe `mode set team` uses). The DSN
value is **never persisted** (env-var only, secret-scanned). Re-running is safe (idempotent). After
a green init, `mokata mode set team` activates team mode. Zero-setup team sync.

**DDL is `team init`'s alone.** Runtime connects run **zero DDL** (the schema check is a cached,
SELECT-only probe), so a least-privilege **DML-only** runtime role is *sufficient* — and revoking
`UPDATE, DELETE` on `mokata_audit_log` makes the audit log append-only **by grant**, not by
convention. (mokata does not create or manage roles or grants; `team init` prints the guidance.) The
shared schema is compatible over a **range**, not an exact match: this build speaks **v3** and serves
schemas back to **v2**, and a difference in either direction only **warns** and keeps working — a
version bump must not partition a team mid-upgrade. Only two states are hard refusals, each with its
named fix: a schema **below** the floor (`mokata team init` to upgrade it) and one **ahead** of this
build (`pip install -U mokata`). `team init` likewise refuses to rewrite a schema newer than itself. **`join <source>`**
(the **new-member onboarding** path — a joiner never runs `init`/DDL) takes a teammate from a DSN to
**CONNECTED without reading source**: it runs `adopt` → `connect` → **activate** → vault `pull` →
`onboard` → **consent** → `doctor` **in order**, each a confirmable step, and prints a "here's what
you're now wired to" summary. The joiner **INHERITS** the pinned team project id from the adopted
stack (**never re-pins/forks it**); the **activate** step runs the TM.S2 preflight and reaches
**CONNECTED** or **fails closed** with the S2 named fix (unreachable/pooler-trap, schema-absent → ask
whoever ran `team init`, incompatible version), writing nothing on failure; and the **consent** step
captures the revocable standing audit-publish consent. The **`--dsn-env`** value must be the env-var
**NAME** (e.g. `MOKATA_PG_DSN`), **never an inline DSN** — an inline DSN is refused (fail-closed).
Options: `--dsn-env <ENV>` (shared memory), `--vault <repo-or-dir>` (pull the shared design/spec
vault), `--yes` (non-interactive), `--force` (overwrite config on adopt). Every writing step is
**human-gated**, the untrusted pulls are **secret-scanned**, and a step whose source/backend/driver
is absent is **skipped with a note** (never a blocker); it is **idempotent** and **reversible**. Every
team-mode error links [Team mode — setup & operations](../how-to/team-setup.md). The individual steps still
exist: `status` (read-only) shows whether shared memory/sessions are local-only or pointed at a
managed Postgres; `adopt <source>` pulls a teammate's governed stack (shared manifest + vault +
shared-memory pointer) in one **human-gated, secret-scanned** step (`--force` to overwrite);
`connect --dsn-env <ENV>` points shared memory + sessions at your **own managed Postgres** via an
env-var DSN (the DSN value is **never stored** — only the env-var name); `disconnect` reverses it.
**mokata hosts nothing**; degrade-clean with no driver/DSN. See
[team setup](../how-to/team-setup.md).

### `mokata mode` · `mokata mode set <local|team> [--yes]`
Show or set the **run mode** — a first-class, visible property of every session. **`mokata mode`**
(no argument) prints the current mode line + the **team-readiness preflight** (every prerequisite,
each blocker with its actionable fix). **`mokata mode set local`** is the zero-config default: on an
already-local repo it is a **no-op that writes nothing** (local stays byte-for-byte unchanged); it
only writes — through the same **human-gated** `config set` WriteGate — when switching back from a
non-local setting. **`mokata mode set team`** runs the **fail-closed** preflight — a usable run
identity, `$MOKATA_PG_DSN` present, the shared DB reachable within a ≤500ms probe, and a compatible
`mokata_schema_version` — and only then performs the human-gated mode write. Any failure is
**fail-closed** with its own **named fix** (unreachable/pooler-trap, schema absent → `mokata team
init`, incompatible version), links [Team mode — setup & operations](../how-to/team-setup.md), and
writes nothing; team mode is **never half-activated**. The mode is also surfaced in the
statusline badge, the SessionStart briefing, and `mokata doctor` — a session is never ambiguous
about which mode it's in.

### `mokata sync [--yes]`
**Flush + reconcile** the team write journal (team mode only; a no-op in local). Every durable team
write lands in a **crash-safe local journal first**, so **offline never blocks** and nothing is
lost — `mokata sync` is the manual flush that pushes those writes to the shared database and
reconciles conflicts. Three guarantees ride it: **(1)** each flushed write carries the **ledger id
of its original human approval**, so the flush *inherits* that approval (never a governance bypass)
and a **per-publish secret-scan** still applies; **(2)** each memory write is **compare-and-set** on
a `revision` column — a concurrent-writer conflict **surfaces** and is resolved through the **human
gate** (keep-local overwrites remote, keep-remote drops the local write), **never a silent
last-writer-wins** (a conflict you don't decide stays *deferred* for a later interactive run); and
**(3)** rows the old fallback stranded in the local floor are **recovered** through the same gated
path. It leads with the **connection health** verdict — the SAME one shown in the statusline badge
(⚠), `mokata mode`, `mokata doctor`, and the SessionStart briefing (one probe, cached). When the
connection isn't healthy the flush is **skipped** (work-locally; nothing lost) and the state is
reported. `--yes` is non-interactive (flush without prompting; conflicts are **deferred**, never
silently overwritten). See [Team mode — setup & operations](../how-to/team-setup.md).

## Lifecycle (Part K)

### `mokata menu`
The **command palette** — every shipped `/mokata:` command and every bundled skill on one
screen, each with a one-line description and a `✓` marker for the ones that carry a gate.
Read-only; enumerated from the installed command/skill files (single source, never a
hand-maintained list). Colour + a Unicode box on a real terminal; plain-ASCII with zero
escape codes when piped, redirected, or `NO_COLOR` is set. Backs `/mokata:menu`.

### `mokata docs [topic]`
A **pointer to the published docs site** (<https://mokata.ai/>). With no
topic it lists the top-level topics with their site URLs; with a topic (e.g. `getting-started`,
`concepts/execution-model`) it prints that page's URL and title. **Read-only and local-first** —
it resolves and prints URLs, it **never fetches** the page, and **no doc content ships in the
package** (the docs live at the repo-root `docs/` tree that mkdocs builds into the site). An
unknown topic re-lists the topics with a hint and exits non-zero. Colour + a Unicode box on a
real terminal; plain-ASCII with zero escape codes when piped, redirected, or `NO_COLOR` is set.
Backs `/mokata:docs`.

### `mokata docsync [path] [--reconcile] [--yes]`
Keep the docs **true to the code**. With no target it **sweeps** the public doc tree and
drift-detects; with a `path` it **audits** exactly that doc. The audit is **read-only** — it
cross-references each claim against the code (skill counts, command names, install/getting-started
path, version examples, and — with a code graph wired — symbols) and reports every discrepancy with
a severity (**Blocking / Minor / Info**), highlighting the stale section. A Blocking finding exits
non-zero so a release doc gate can act on it. With `--reconcile` it proposes the fixes, **previews
the diff, and writes ONLY on approval** through the universal write gate (`--yes` approves
non-interactively); a decline writes nothing — there is no silent-write path, and it reconciles the
**doc** to match the code, never the reverse. Backs `/mokata:docsync`.

### `mokata doctor [--matrix]`
Diagnose the manifest/config: missing providers, broken adapters, role conflicts, bad
trust levels, oversized rule tiers, and a broken audit-ledger hash-chain. Exit non-zero if any
error. Read-only.

It is also where **a degrade stops being invisible**. Four honesty surfaces ride it:

- **What degraded this session.** Degrade notices are *remembered*, not just printed once into a
  scrollback, so doctor can report them:
  ```text
  ⚠ degraded this session (2) — a capability fell back to a floor:
  ```
  A team read served from the local floor, a code graph that fell to grep, a secret-scanner that
  could not import. Each names its **failure class** and — for team — the resolved env-var **NAME**,
  never its value. It is **process-lifetime**: it reports what degraded in *this* process (a notice
  emitted inside a short-lived hook process isn't in it), and an empty registry prints **nothing at
  all** (no "0 degrades" line to train you to skip the section that matters).
- **Run mode + the team preflight** — the mode line plus every team prerequisite, each blocker with
  its named fix. Its `team-schema` check reports the **range**: `schema v3 in range (this mokata
  speaks v3)`. A version *difference* in either direction is a **warning that keeps working** (a
  bump must not partition a team mid-upgrade); only a schema below the floor (`mokata team init`) or
  one that no longer serves this build (`pip install -U mokata`) is a refusal.
- **The team write-flush backlog** (team mode only) — approved team writes land in a crash-safe
  local journal first, so doctor says when they haven't reached the shared DB yet:
  ```text
  team pending: ⚠ 3 approved write(s) journaled locally and NOT yet flushed to the team DB;
  oldest waiting 12m; last failure: <class>; auto-retry paused (cap reached) — run `mokata sync` to flush.
  ```
  (This is **not** a count of pending *approvals* — those are listed only by bare
  [`mokata approve`](#mokata-approve-proposal-id---yes---actor-who).)
- **The trust surface truth** (an `info` line, only when `settings.trust` is set) — trust is enforced
  on the **`mcp` write surface**; there the real ladder is `read-only` ▸ write-allowed
  (`propose-only` == `gated-write`, because every MCP write already needs a human-minted `mokata
  approve <id>`). CLI writes carry their tool identity but are **not yet governed by the dial**. See
  [manifest → settings](manifest.md#settings-the-generic-toggle-store).

`--matrix` additionally prints the full **capability coverage matrix** — every harness
wiring point and every declared capability classified **pass / degraded / fail** (degraded =
resolved via a fallback; fail = no present provider). It reuses the same resolver the diagnosis
does (one source of truth), is read-only, and does **not** change the exit code.

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

### `mokata config wizard`
An **interactive, gated walk** through mokata's user-facing settings. For each setting it shows
the current value, a one-line description, the allowed values, and the default; you can keep,
skip, or edit it. Every change is routed through the **same human-gated write path** as `config
set` (preview → confirm → secret-scan → schema-validate → ledger) — the wizard is a front-end,
never a second write authority. **Fail-closed**: on a non-TTY / unreadable stdin it makes no
change and says so (it never hangs or silently writes). Reject leaves the manifest byte-unchanged;
each committed change is recorded in the audit ledger.

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
Upgrade mokata. The **pip** path is the only one that *executes* anything: it proposes
`pip install -U mokata` and runs it only after you confirm (**human-gated**; `--yes` approves
non-interactively — it never auto-runs without one or the other). The **plugin** path only
**prints** the Claude Code steps (`/plugin marketplace update mostack` + reinstall), because the CLI
cannot upgrade the plugin itself; a **source** checkout likewise only prints its steps (`git pull` +
reinstall). `--check` is the one **outbound** call — it just reports whether a newer release exists
(the same opt-in check as `version --check`) and upgrades nothing. `--method` overrides
install-method detection. Inside Claude Code, the `/mokata:version` command surfaces the same.

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
