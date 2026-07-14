# Reference: command surfaces (CLI ↔ slash ↔ MCP)

mokata's **hard rule** is that every user-facing capability is reachable from *inside* Claude
Code — as a `/mokata:…` slash command and/or a native MCP tool — not CLI-only. The CLI stays
the secondary "use-anywhere" surface; the harness is the primary one. Two commands —
[`approve` and `gate`](#deliberately-human-only-no-mcp-tool-no-slash-command) — are deliberately
exempt: a surface the *model* can call is not a gate the model is under.

**Beyond Claude Code (Stage 63):** the same `/mokata:` command set is materialized into each
supported agent's **native** surface — Cursor `.cursor/commands/*.md`, Copilot
`.github/prompts/*.prompt.md`, Windsurf `.windsurf/workflows/*.md`, Gemini
`.gemini/commands/*.toml`, Codex `.codex/prompts/*.md`, and Aider reference prompts — via
`mokata setup <agent>`. The MCP tools register where the agent speaks MCP (`claude`, `cursor`,
`gemini`); otherwise a documented manual step. Where an agent lacks a capability the harness
boundary degrades **clearly** (never a silent no-op). See
[Use mokata with other AI agents](../how-to/use-with-other-agents.md).

This table is generated from the **coverage matrix** in `mokata.parity` (the single source of
truth). A CI parity test derives the command set from the live CLI parser and **fails** if any
command lacks a Claude Code surface *or* an explicit exemption — so this can never silently
regress.

**How surfaces are chosen:** read-only inspection → an MCP **read** tool; a durable write → a
**human-gated** MCP **write** tool (the universal WriteGate — secret-scan + human gate + audit;
a secret is an absolute hard block) or a slash that drives the gated path; a workflow/
interactive phase → a `/mokata:…` **slash** command.

## User-facing commands

| CLI command | Slash | MCP read | MCP write |
|---|---|---|---|
| `brainstorm` | `/mokata:brainstorm` | — | — |
| `onboard` | `/mokata:onboard` | — | — |
| `run <skill>` | `/mokata:<skill>` (each skill) | — | — |
| `enter` | `/mokata:enter` | — | — |
| `exec` | `/mokata:exec` | — | — |
| `chain` | `/mokata:chain` | — | — |
| `playbook` | `/mokata:playbook` | — | — |
| `resume` | `/mokata:resume` | — | — |
| `skill` (author) | `/mokata:skill` | — | — (gated via slash) |
| `upgrade` | `/mokata:upgrade` | — | — (gated via slash) |
| `version` | `/mokata:version` | — | — |
| `init` | `/mokata:init` | — | `init` |
| `setup` | `/mokata:setup` | — | — (the guided first-run wizard; gated via slash) |
| `tour` | `/mokata:tour` | `tour` | — |
| `menu` | `/mokata:menu` | — | — (read-only command palette: every command + skill with gate markers) |
| `docs` | `/mokata:docs` | — | — (read-only docs pointer — topics + their site URLs; no fetch, no bundled content) |
| `docsync` | `/mokata:docsync` | — | — (the read-only audit is the surface; the reconcile fix rides the existing gated write) |
| `mode` | `/mokata:mode` | — | — (local\|team + the fail-closed team-readiness preflight; `set` drives the same gated CLI path) |
| `sync` | `/mokata:sync` | — | — (team-mode only: flush the local write journal to the shared DB; conflicts are a human decision, never silent LWW) |
| `reconfigure` | `/mokata:reconfigure` | — | `reconfigure` (gated; idempotent + reversible) |
| `query` | — | `query` | — |
| `status` | — | `status` | — |
| `doctor` | — | `doctor` | — |
| `coverage` | — | `coverage` | — |
| `budget` | — | `budget` | — |
| `audit` | — | `audit` (`team=true` → team-wide who-did-what over the **shared** log; NO telemetry) | `audit_share` — publish your new local entries to the team's **own** shared log (Stage 71): append-only + per-actor + namespaced (conflict-free), secret-scanned egress, human-gated; opt-in + local-first; the DSN secret is never stored |
| `preview` | — | `preview` | — |
| `progress` | `/mokata:progress` | `progress`, `lanes` | — |
| `watch` | `/mokata:watch` | `watch` | — |
| `govern` | `/mokata:govern` | `govern` | — |
| `rules` | — | `rules` | — |
| `skills` | — | `skills` | — |
| `suggest` | — | `suggest` | — |
| `lat-check` | — | `lat_check` | — |
| `index` | — | `index_status` (read-only diff; durable rebuild stays CLI) | — |
| `baseline` | — | `baseline` | — |
| `ci-check` | — | `ci_check` | — (read-only PR check; reuses the gates, posts nothing) |
| `sessions` | — | `sessions` | — |
| `windows` | — | `session_windows` | — (the LIVE Claude Code windows on this repo — each is its own MCP process; transient registry upkeep, ungated) |
| `plan` | — | `plan_list`, `plan_show` | — (`plan export` is a user-run CLI copy into a committable `plans/` — never a silent clobber) |
| `decompose` | `/mokata:decompose` | `decompose` (proposes the split; read-only) | — (confirm + run gated via `decompose --run` / exec) |
| `mcp` | `/mokata:mcp-repair` | — | — (repair is CLI-driven; a self-repair *server* tool can't run when the server is what's down) |
| `config` | — | `config_get` | `config_set` |
| `memory` | — | `recall` | `remember`, `memory_export`, `memory_import`, `apply_proposal` |
| `vault` | `/mokata:vault` | `vault_list`, `vault_search`, `vault_pull` | `vault_push` |
| `session` | `/mokata:session` | `session_list` (spans local + remote transports), `session_save` (an **ungated** local snapshot of your own in-flight session — the gate sits at SHARE, not save) | `session_push`, `session_pull`, `session_name` (secret-scanned + human-gated on EVERY transport — local/vault/postgres; hash-verified + cross-codebase mismatch surfaced on pull; rename never a silent clobber) |
| `export` | — | `export_preview` | `export_stack` |
| `import` | — | — | `import_stack` |
| `stacks` | `/mokata:stacks` | `stacks_list`, `stacks_search`, `stacks_show` (a curated, versioned `index.json`) | `stacks_install` — the human-gated, secret-scanned adopt path (reuses `apply_manifest`). No hosted marketplace: publish over git/the vault; discover a reviewable index; install is gated |
| `team` | `/mokata:team` | — | — (the slash drives the same gated CLI path: **join** — adopt→connect→vault→onboard→doctor, each confirmable, degrade-clean, idempotent, reversible — plus adopt / connect / disconnect, human-gated + secret-scanned; `team status` is read-only. Managed Postgres via an env-var DSN: mokata hosts nothing and the DSN is never stored) |
| `spec` | `/mokata:spec` | — | `spec_emit`, `spec_amend` (the completeness gate, then a human-gated write of the spec — the ONLY writer of the spec that `spec-persisted`, `spec-scope` and `spec-check` read; `mokata spec emit --file` is the use-anywhere CLI twin) |
| `spec-check` | — | — | `spec_check` (deviation gate on a conflict) |
| `reset` | — | — | `reset` |

Every **MCP write** tool is **propose-only**, and the approval that commits it is one the model
**cannot mint**. A call returns the staged change plus a `proposal_id` and writes nothing. To commit,
*you* mint the approval out-of-band — `mokata approve <proposal-id>` in your own terminal — and the
model then re-calls the tool referencing that `proposal_id`. The approval is **single-use**,
**content-hashed** (so what you approved is what commits — change an argument and the id changes),
**session-scoped**, and expires. It commits through the WriteGate, where a detected secret is
hard-blocked **even when approved**.

`approve=true` / `confirm=true` are still accepted on the tool call (schema stability) but **commit
nothing**. They never were consent: they are flags the *model* types, and a gate the gated party can
open is not a gate. See [`mokata approve`](cli.md#mokata-approve-proposal-id---yes---actor-who).

**Project scoping of the shared backends (Stage 71a).** The `memory`, `session`, and `audit --team`
review surfaces are scoped by a stable **project key** (`settings.project.id`, else derived from the
git remote / repo path) so one shared Postgres DSN safely hosts many projects. Each defaults to the
**current project**, with `--all` / `--project <id>` / `--list-projects` escapes on the CLI; the MCP
reads (`recall`, `session_list`, `audit`) resolve the current project the same way. No new
commands/tools — the scoping is a filter on the existing surfaces, so parity is unchanged. See
[Multi-project on one shared backend](../how-to/multi-project-shared-backend.md).

## Deliberately human-only (no MCP tool, no slash command)

Two commands break the hard rule **on purpose**, and the asymmetry is the point: an in-harness
surface for either would hand the model the key to its own constraint. Both are exempted in the
matrix with exactly that rationale, and the parity test asserts the exemption stands.

| CLI command | Why it has no in-harness surface |
|---|---|
| `approve` | It mints the human approval a durable MCP write needs. An in-harness approve tool would let the **model approve its own writes** — which is precisely the hole `approve=true` was. The approval must be minted by a human at a terminal (explicit, shown in full, re-confirmed, single-use, content-hashed, session-scoped, ledgered); the model may only *reference* it by id. The write tools' propose path **is** its in-harness surface: they hand back a `proposal_id` and tell the model to ask you to run the command. |
| `gate` | `gate status` / `gate override <gate> --reason "<why>"` / `gate clear` govern the run-state gates the [`gate-guard` hook](../how-it-works/skills-and-gates.md#the-gate-guard-the-gates-enforced-on-native-edits) enforces on native `Write`/`Edit`. A model-invocable override would make a structural gate advisory again — so the override is a human act at a terminal: explicit, re-confirmed, session-scoped, ledgered. There is no env-var kill switch either (a side door any process can open silently). The hook itself needs no surface: `mokata setup` wires it. |

## Intentionally CLI-or-hook (install / diagnostic plumbing)

These are **not** silent gaps — they are explicitly classified as plumbing, each with a
rationale, and the parity test asserts they carry an exemption:

| CLI command | Why it stays CLI/hook-only |
|---|---|
| `unsetup` | Install plumbing — reverses `setup`; a harness-config + filesystem teardown run from the shell. |
| `worktree` | Git/filesystem plumbing — `worktree create` runs `git worktree add` to give a window its own working tree; a durable shell action gated through the CLI's fail-closed confirm, outside the WriteGate's data model. Its in-harness **detect + offer** is surfaced by `session_windows` and the SessionStart briefing, which point you at this command. |
| `harness` | Diagnostic plumbing — prints the harness capability matrix (the boundary mokata runs inside); host introspection. |
| `route` | Diagnostic plumbing — resolves a capability to its concrete tool + fallback chain; internal routing introspection. |
| `detect` | Diagnostic plumbing — probes tool presence on the host; an environment scan. |
| `validate` | Diagnostic plumbing — parses + validates the committed manifest; a lint/CI check. |
| `bench` | Diagnostic plumbing — measures local wall-clock latency of the hot paths against their budget; read-only, with no in-harness workflow analogue. (Distinct from `budget`, which is *tokens* and has a read tool.) |
| `release-check` | Release plumbing — a pure/offline preflight asserting every version field equals the intended tag; run in CI during a release cut, the version mirror of `validate`. |
| `branch-protection-check` | Release plumbing — a fail-closed preflight asserting the public mirror's default branch is protected before a cut; run from the shell by the release script. |
| `bootstrap` | Hook plumbing — prints the SessionStart briefing; invoked *by* the SessionStart hook, never typed by a user. |
