# Reference: command surfaces (CLI ↔ slash ↔ MCP)

mokata's **hard rule** is that every user-facing capability is reachable from *inside* Claude
Code — as a `/mokata:…` slash command and/or a native MCP tool — not CLI-only. The CLI stays
the secondary "use-anywhere" surface; the harness is the primary one.

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
| `resume` | `/mokata:resume` | `sessions` | — |
| `skill` (author) | `/mokata:skill` | — | — (gated via slash) |
| `upgrade` | `/mokata:upgrade` | — | — (gated via slash) |
| `version` | `/mokata:version` | — | — |
| `init` | `/mokata:init` | — | `init` |
| `setup` | `/mokata:setup` | — | — (the guided first-run wizard; gated via slash) |
| `tour` | `/mokata:tour` | `tour` | — |
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
| `decompose` | `/mokata:decompose` | `decompose` (proposes the split; read-only) | — (confirm + run gated via `decompose --run` / exec) |
| `config` | — | `config_get` | `config_set` |
| `memory` | — | `recall` | `remember`, `memory_export`, `memory_import`, `apply_proposal` |
| `vault` | `/mokata:vault` | `vault_list`, `vault_search`, `vault_pull` | `vault_push` |
| `session` | `/mokata:session` | `session_list` (spans local + remote transports) | `session_push`, `session_pull`, `session_name` (secret-scanned + human-gated on EVERY transport — local/vault/postgres; hash-verified + cross-codebase mismatch surfaced on pull; rename never a silent clobber) |
| `export` | — | `export_preview` | `export_stack` |
| `import` | — | — | `import_stack` |
| `stacks` | `/mokata:stacks` | `stacks_list`, `stacks_search`, `stacks_show` (a curated, versioned `index.json`) | `stacks_install` — the human-gated, secret-scanned adopt path (reuses `apply_manifest`). No hosted marketplace: publish over git/the vault; discover a reviewable index; install is gated |
| `team` | `/mokata:team` | `status` (read-only) | **join** (guided onboarding: adopt→connect→vault→onboard→doctor, each confirmable, degrade-clean, idempotent, reversible) / adopt / connect / disconnect — human-gated, secret-scanned; managed Postgres via env-var DSN (mokata hosts nothing; the DSN is never stored) |
| `spec-check` | — | — | `spec_check` (deviation gate on a conflict) |
| `reset` | — | — | `reset` |

Every **MCP write** tool is propose-only by default: without `approve=true` it returns the
staged change and writes nothing; with `approve=true` it commits through the WriteGate, where a
detected secret is hard-blocked even when approved.

**Project scoping of the shared backends (Stage 71a).** The `memory`, `session`, and `audit --team`
review surfaces are scoped by a stable **project key** (`settings.project.id`, else derived from the
git remote / repo path) so one shared Postgres DSN safely hosts many projects. Each defaults to the
**current project**, with `--all` / `--project <id>` / `--list-projects` escapes on the CLI; the MCP
reads (`recall`, `session_list`, `audit`) resolve the current project the same way. No new
commands/tools — the scoping is a filter on the existing surfaces, so parity is unchanged. See
[Multi-project on one shared backend](../how-to/multi-project-shared-backend.md).

## Intentionally CLI-or-hook (install / diagnostic plumbing)

These are **not** silent gaps — they are explicitly classified as plumbing, each with a
rationale, and the parity test asserts they carry an exemption:

| CLI command | Why it stays CLI/hook-only |
|---|---|
| `unsetup` | Install plumbing — reverses `setup`; a harness-config + filesystem teardown run from the shell. |
| `mcp` | Diagnostic plumbing — discovers external MCP servers from `.mokata/mcp.json` and maps them to roles; introspects the harness wiring itself. |
| `harness` | Diagnostic plumbing — prints the harness capability matrix (the boundary mokata runs inside); host introspection. |
| `route` | Diagnostic plumbing — resolves a capability to its concrete tool + fallback chain; internal routing introspection. |
| `detect` | Diagnostic plumbing — probes tool presence on the host; an environment scan. |
| `validate` | Diagnostic plumbing — parses + validates the committed manifest; a lint/CI check. |
| `release-check` | Release plumbing — a pure/offline preflight asserting every version field equals the intended tag; run from the shell by `release.sh` (and CI) during a release cut, the version mirror of `validate`. |
| `bootstrap` | Hook plumbing — prints the SessionStart briefing; invoked *by* the SessionStart hook, never typed by a user. |
