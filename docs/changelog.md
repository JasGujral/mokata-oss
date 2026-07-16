# Changelog

The full, versioned changelog lives in the repository's
[`CHANGELOG.md`](https://github.com/JasGujral/mokata-oss/blob/main/CHANGELOG.md)
(Keep a Changelog format).

> **Re-baselined at 0.0.1** — mokata's inaugural public release. Earlier internal iterations were
> a stabilizing phase and are collapsed into this entry; 0.0.1 is the honest starting point for an
> early, fast-moving project.

## 0.0.14

**Graph mandatory + trust fixes. No breaking changes; additive; no schema change; local stays the
zero-config default.** The codebase graph becomes a first-class, always-on structural layer with an
honest fallback. An **embedded stdlib-AST floor** ships in the box — on a Python repo it answers
callers/callees/imports/blast-radius structurally (`degraded=false`) with zero dependencies, a floor
**above** grep; adopt a richer graph with `mokata graph adopt [code-review-graph|serena]` (human-gated)
and see which backend answers with `mokata graph status`. **`graph.required` now defaults on**: a
degraded (grep-floor) blast radius is **refused** as a decision input unless you accept it for the
session with the TTY-reconfirmed, ledgered `--allow-degraded` escape (the result stays marked
degraded). Every graph query now **checks freshness before answering** — a known-stale graph rebuilds
first, and a rebuild failure degrades loudly to the AST floor on current files, never stale structure.
**Trust:** a **ninth backed gate** (`approach-approval`) physically blocks the idea→code jump — a
native `Write`/`Edit` before an approved approach is refused (exit 2); an **opt-in, default-OFF**
in-chat `mcp__mokata__approve` tool performs the same human-minted, single-use, content-bound approval
and still prompts you on every call, so the model can't approve its own write; approved approaches
carry typed **`decisions[]`** the spec's deferred scope derives from (never hand-written twice) behind
a **prior-art bound step**; the statusline is **session-true** and a **shipped run retires** from the
badge and `mokata progress` (nothing deleted). The six CLI setup one-shots 0.0.13 filed are now
**ledgered** (`KNOWN_BYPASS` is empty; a sweep fails CI on any ungated durable writer), and `mokata
reset` writes a tombstone that survives `.mokata`'s removal. **Fixes:** simulated exec batches report
zero actual spend and a `simulated` (never green) review; `offer_text_once` never raises; the `reset`
propose→approve→redeem round trip no longer crashes.

## 0.0.13

**Correctness & Trust — the seatbelt is enforced, not advertised. No breaking changes; additive; no
schema change.** Every change fixes silent data loss, a race, or a gate that could be walked around.
**Live bugs fixed:** teams on a custom DSN (`--dsn-env CUSTOM`) read fine but **never flushed a
single write** — one DSN resolver now serves health, flush, sync, reads, audit and transport; the
spec gate could **brick every implementation write** after a real approval by pointing at a
spec-emit surface that did not exist (new `mokata spec emit` / `spec show` + a gated `spec_emit`
tool); a WAL-switch race; approval misattribution under a two-process race; team access control that
**failed open** (now deny-by-default); a sequential floor that **fabricated `ok=true` ledger rows**
for work nothing ran (now labelled `simulated`, never counted); and 30 silent degrades — including a
tampered ledger that reported *intact* and a secret-guard hook that left every write **unscanned for
secrets**. **The seatbelt:** a `PreToolUse` hook enforces run-state gates on **native Write/Edit**
(no code before a persisted spec; no code without a failing test — RED is the permission to
implement), overrides named/reasoned/ledgered with no MCP surface; **the model can no longer mint its
own consent** — `approve=true` is dead, a commit needs a human-minted, content-bound, single-use
approval from `mokata approve <id>` in a separate terminal; the **trust dial** (previously dead code)
is wired into every writing gate; a **zero-bypass audit** classifies every durable-write site and
fails CI on an unregistered writer, closing three real side doors; and **scope binding** — a feature
the spec deferred cannot be built without a re-gated `spec amend` (a forced phase regression). *An
instruction is authorization to ASK, not to build.* **Multi-session safety:** atomic state (a torn
write used to silently erase it), real session identity + scoped keys, worktree detection,
hash-chained ledger, single-flusher sync, and a two-process stress test in CI. **Session save &
share:** the save path *did not exist in production* — now `session save` survives `kill -9`, a
per-turn autosave bounds a crash to **at most one lost brainstorm turn**, bundles are versioned and
secret-scanned, and **approval never crosses machines** (the receiver's gate owns approval).
**Honest boundaries:** "zero bypasses" holds for the memory/export/migrate funnel, **not repo-wide**
— six CLI setup one-shots still write outside the gate (registered, filed for 0.0.14); an agent with
arbitrary **shell** access is out of scope; the trust dial is not yet enforced on the CLI; the
Windows stress proof lands on public CI at the cut.

## 0.0.12

**A legible skills pipeline, native domain knowledge, and a docs↔code reconciler. No breaking
changes; additive; no schema change.** Every skill now carries a `## Contract` (what it can do,
what it must not, and the real gate backing each boundary) plus an active-skill banner
single-sourced with the statusline and `mokata progress`; skills auto-engage when the moment
fits, and `mokata skills` lists the complete curated catalog — runnable pipeline skills plus
standalone/auto-firing `docsync`, `govern`, `session`, `playbook`, `mcp-repair`. Ten clean-room
**domain-knowledge skills** (API design, security, performance, frontend/accessibility, browser
testing, CI/CD, git workflow, deprecation, docs/ADRs, shipping) attach to the pipeline phase
where they apply and feed the gate already running there. New **`mokata docsync`** audits docs
against the code (commands, config keys, counts, versions) and offers **human-gated** fixes; it
auto-fires when a change touches a documented symbol. Develop shifts ambiguity left (stop → ask
one question → amend the spec, human-gated); brainstorm gains a design pre-mortem and a
doc-freshness check. Fixes: hooks resolve reliably under a GUI-launched minimal PATH; team-mode
precedence resolves scoped conflicts to one winner; a team-Postgres read-through cache. Hardening:
hash-pinned CI dependency installs; a new "How it works" developer docs section.

## 0.0.11

**Team mode — a shared, governed team brain over your own Postgres. No breaking changes; additive;
local stays the zero-config default.** mokata gains an explicit **run mode**: `mokata mode` shows the
current mode + a team-readiness preflight, and `mokata mode set local|team` switches it through the
human-gated write path — `local` a zero-config no-op, `set team` a **fail-closed** preflight (run
identity, `$MOKATA_PG_DSN`, DB reachable in a ≤500ms probe, compatible schema) that never
half-activates. Team setup runs on the team's **own** Postgres: `mokata team init` is the sole owner
of DDL (guides `--backend managed|compose|local`, fails closed on a missing DSN, provisions the
shared tables idempotently on vanilla Postgres ≥14, pins the team project identity, runs a live
CONNECTED test), and `mokata team join <source>` is the new-member path chaining adopt → connect →
activate → vault pull → onboard → consent → doctor; `status`/`adopt`/`connect`/`disconnect` ship too.
The DSN value is never stored (only the env-var name) and mokata hosts nothing. **Shared memory over
Postgres** adds a scope hierarchy (personal → project → team → global), typed items
(rule/guardrail/best-practice/context/reference/decision) with an enforcement binding
(advisory/soft/hard), in-run hard-rule enforcement, and shared formulas; `mokata memory promote`
moves a rule's enforcement (human-gated) and `mokata memory review` runs the Draft → In-Review →
Approved proposal workflow (proposer ≠ approver). Team writes are **journal-first**: every durable
write lands in a crash-safe local journal (offline never blocks), and `mokata sync` flushes +
reconciles — each write inherits the ledger id of its original human approval and is re-secret-scanned,
and each memory write is compare-and-set on a revision column so a conflict surfaces through the human
gate (never silent last-writer-wins). Access is governed by **scoped consent**; `mokata audit
--consent show|grant|revoke` manages a revocable standing consent for the batched audit-publish
(per-publish secret-scan still hard-blocks). Also: `mokata branch-protection-check` verifies the
public default branch is protected (fail-closed); a team ops kit (`docker-compose.team.yml` +
`.env.example` + `llms.txt`); the in-Claude-Code MCP repair skill is renamed to **`/mokata:mcp-repair`**;
`mokata setup claude` surfaces a permission-grant step when wiring the MCP server; and **Python ≥ 3.10**
is the supported floor.

## 0.0.10

**"Inside Claude Code" — richer in-terminal UX, a gated settings wizard, a `doctor` coverage
matrix, and three hook/setup fixes. No breaking changes; additive; no new dependencies.** A new
command palette (`mokata menu` / `/mokata:menu`) lists every mokata command and skill on one screen
with gate markers, derived from the shipped files (single source, no drift). `/mokata:docs [topic]`
points to the published docs — listing topics with their URLs and resolving a topic to its page
(read online; not bundled in the wheel; never fetches at runtime). An interactive, gated settings
wizard (`mokata config wizard`) walks you through mokata's settings, routing every change through the
same human-gated write path (secret-scan + schema validation + write gate + audit ledger) and
failing closed when non-interactive. Output is now consistent across verdicts, progress, and doctor
tables (colour on a TTY, clean ASCII when piped or under `NO_COLOR`), and `mokata doctor` gains an
opt-in capability coverage matrix (`mokata doctor --matrix`) — pass / degraded / fail per capability,
single-sourced from the resolver. Under the hood, the tokenizer-free chars÷4 token estimate now logs
estimate-vs-actual to the ledger so the ~2k briefing budget's safety margin is measured. **Fixes:**
hooks no longer hang when stdin is an open pipe with no writer (bounded read + safe fallback);
`mokata-hook` with a missing/unknown subcommand now exits non-zero (exit 1, never the reserved
security-block code) so a mis-wired hook is visible; and `mokata unsetup claude` removes config files
it created once they're empty instead of leaving `{}` husks (files with your own content are preserved).

## 0.0.9

**Installs from PyPI, no clone — and the MCP server works out of the box. No breaking changes.**
`pip install mokata` now ships everything (command templates, hooks, and Agent Skills are packaged in
the wheel) — no repo clone needed; the bundled MCP server's SDK is a default dependency on Python
3.10+, so `mokata-mcp` runs out of the box (on 3.9 the CLI still works and the MCP server prints a
clear upgrade message). `mokata setup claude` registers the MCP server at an absolute path, verifies
the connection (`CONNECTED ✓`), and wires commands + skills + the status line; new `mokata mcp start |
status | install` plus a `/mokata:mcp-repair` repair skill re-register the server from inside Claude Code.
Re-running `mokata setup claude` now syncs the Agent Skills and prunes stale/removed mokata skills
(your own skills are never touched).

## 0.0.8

**Fix: no duplicate Agent Skills when the plugin is installed.** `mokata setup claude` now detects
an installed mokata plugin and skips writing project-scope Agent Skills (the plugin already
provides them) — running both previously listed every skill twice in Claude Code. Commands, hooks,
and MCP wiring unchanged; no effect when the plugin isn't installed.

## 0.0.7

**Agent Skills surface. No breaking changes; additive.** mokata's core capabilities now also
register as Claude Code **Agent Skills** (auto-engaged from their `description`), alongside the
`/mokata:*` slash commands. The 0.0.7 skill set (14 then; the curated catalog is 16 today) ships as `skills/<name>/SKILL.md`, each rendered from the one
command template (single source + drift guard — no duplication). Installed by both the plugin
(`skills/` + `"skills"` in `plugin.json`) and `mokata setup claude` (`.claude/skills/`, reversible
via `mokata unsetup claude`). Non-Claude harnesses degrade clean.

## 0.0.6

**Windows portability fix. No breaking changes; Linux/macOS behavior unchanged.** The Windows
CI matrix first ran on the 0.0.5 re-cut and surfaced two real Windows-only bugs (prior green runs
were Linux-only). Fixed: (1) the SQLite memory backend held a persistent file handle across
operations, so a tempdir teardown failed on Windows with `PermissionError [WinError 32]` — the
file-backed backend now opens a short-lived connection per operation (an in-memory `:memory:` DB
keeps its connection, having no file to leak); (2) text files written without an explicit encoding
landed as cp1252 on Windows (em-dash → `0x97`) and broke the utf-8 read — every text-mode file
I/O now passes `encoding="utf-8"`. Guarded by a lint test (encoding on all text I/O) and a
portability test (no lingering handle) that run on every OS.

## 0.0.5

**Portable sessions, in-Claude-Code UX, every-agent reach & supply-chain trust.
No breaking changes.**
Fixed: hook invocation now uses a PATH-resolved `mokata-hook` console entry, fixing the
`python3: command not found` pre-hook error class for PATH-resolved installs (the GUI-launched
minimal-PATH variant was fully closed in 0.0.12, which pins the absolute path). New: portable/shareable
**sessions** (`session push`/`pull`/`list`/`name` — machine-path-free, secret-scanned,
human-gated); an always-on **stage badge** + flow legibility + parallel-agent **lanes** + full
Claude-Code **command parity** (CI-enforced) + task decomposition + brainstorm anti-drift anchor +
native **to-do widget** (one `RunProgress`, many renderers); a **first-run/`reconfigure` wizard**;
**memory intelligence** (explainable retrieval, health nudges, proposed guardrails); a **CI/PR
check** GitHub Action; reach under **Cursor/Copilot/Windsurf/Codex/Gemini/Aider**, language + OS
matrix; and publishable community **stacks** (`mokata stacks`, no telemetry). Hardened: supply-chain
(**SBOM + Sigstore provenance**, least-privilege SHA-pinned CI); reliability fuzz pass + a measured
**performance budget**; release-process version-at-tag verification. (A **VS Code extension** +
**Copilot Chat `@mokata`** are **planned — not available**; team mode over a shared backend lands in
0.0.11.)

## 0.0.4

**Governance transparency, session lifecycle, portability & hardening. No breaking changes.**
New: `mokata govern` (clickable governed-state dashboard — rules/guardrails + memory-by-kind +
proposals, read-only); `mokata audit --why` (what + decision + why timeline; decisions now record
rationale); `mokata sessions`/`resume` + a mid-brainstorm checkpoint (leave a brainstorm and come
back, HARD-GATE intact); opt-in git-worktree isolation for parallel/paused work; cross-harness
portability (claude/codex/cowork adapters + `mokata harness` matrix, degrade-clear); `mokata
version`/`upgrade` (offline info, opt-in update check, human-gated upgrade) + `/mokata:version`.
Hardened: secret guard broadened to 18 formats + fuzz invariant (paths/URLs/UUIDs/hex digests no
longer false-positive); Dependabot/CodeQL/Scorecard/CODEOWNERS; live-DB CI (Postgres+pgvector+
Neo4j containers); README + CLI-reference audit with a docs-vs-code drift guard.

## 0.0.3

**Wires up governance/token features that previously had no runtime path, plus a second
secret-guard precision fix. No breaking changes.** New/reachable: `mokata memory consolidate`
(proposal-only), `mokata skill author` (RED-GREEN-for-docs, human-gated), `mokata playbook
--dense` (output-density compression). Karpathy gates now run per pipeline phase, lethal-trifecta
gating guards a private outbound `vault push`, rules-learning surfaces proposal-only promotions in
`mokata rules`, per-task model routing is opt-in, and the briefing emits a cache-stable prefix —
all off-by-default / degrade-clean / human-gated where they write. **Fixed:** the secret-guard
entropy layer no longer flags long file paths / URLs / UUIDs as secrets (real-secret detection
unchanged; complements the 0.0.2 envelope fix).

## 0.0.2

**Critical fix.** The PreToolUse secret-guard hook scanned the whole hook payload — including
Claude Code's high-entropy `session_id` and `transcript_path` — which tripped the secret detector
and **blocked every Write/Edit/Bash call** for installed plugin users. It now scans **only the
tool's content and target path**, never the envelope metadata. Real-secret detection is unchanged
(secrets in a command, file content, or a `.env`/`.pem` path still hard-block). No feature changes.

## 0.0.1

The inaugural public release — clean-room, local-first, Apache-2.0. A spec-driven, test-first
framework for Claude Code whose spine is a real codebase **knowledge graph**, persistent
**self-healing, shareable memory**, and **human-gated, audited governance**.

- **Knowledge graph** — typed structural queries (callers/callees/blast-radius) over an adopted
  graph with a grep floor; incremental index + `lat-check` drift; an external **Neo4j** adapter
  (degrade-clean to grep).
- **Memory** — persistent + decision + **typed** parts (rule/guardrail/best-practice/context/
  reference), on by default, self-healing; **tiered retrieval** (lexical → graph → semantic, with
  a pluggable embedder / pgvector); **sharing** via export/import, migrate, and a team Postgres
  store mokata owns; **`/mokata:onboard`** guided typed capture; a **team design vault**.
- **Engine & correctness** — the 7-phase pipeline, a provable completeness gate (RED before
  GREEN), spec-persisted precondition, ground-in-code discipline, **spec-awareness regression
  guard**, and a verified `ship` step (never auto-merge).
- **Governance & UX** — universal human-gated writes (secret-scan → approval → audit ledger),
  deviation gate, reversible/resumable, local-first with **zero telemetry**; trust dials;
  parallel-aware progress lanes + an opt-in clickable local **dashboard** (`mokata watch`);
  profiles, per-layer/tool toggles, standalone skills.

**Early & stabilizing:** expect rapid iteration; pin the version if you need stability. No
required runtime dependencies (`jsonschema`/`mcp`/`postgres`/`neo4j` are optional extras); the
suite passes with `jsonschema` both absent and present.
