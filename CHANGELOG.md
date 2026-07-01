# Changelog

All notable changes to mokata are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> **Re-baselined at 0.0.1.** mokata is published fresh at **0.0.1** as its inaugural public
> release. Earlier internal iterations (the pre-1.x and 1.x series) were a stabilizing phase and
> are intentionally collapsed into this entry — 0.0.1 is the honest starting point for an
> early-stage, fast-moving project. The detailed build history lives in the repository's internal
> build log.

## [0.0.8] — 2026-07-01

**Fix: no duplicate Agent Skills when the plugin is installed.**

Fixed: `mokata setup claude` (the no-plugin path) now detects an installed mokata **plugin** and
**skips writing the project-scope Agent Skills**, since the plugin already provides them — running
both previously made Claude Code list every mokata skill twice (`mokata:<name>` from the plugin
plus a bare `<name>` from `.claude/skills/`). Detected via a `plugin.json` named `mokata` under
`~/.claude/plugins/`; the plan output says `Agent Skills: SKIPPED` when suppressed. Commands, hooks,
and MCP wiring are unchanged. No effect when the plugin isn't installed.

## [0.0.7] — 2026-07-01

**Agent Skills surface. No breaking changes; additive.**

Added: mokata's core capabilities now also register as Claude Code **Agent Skills** (which Claude
auto-engages from their `description`), alongside the existing `/mokata:*` slash commands. 14
skills (`brainstorm`, `spec`, `develop`, `review`, `refine`, `test`, `debug`, `bug`, `optimize`,
`ship`, `onboard`, `govern`, `session`, `playbook`) ship as `skills/<name>/SKILL.md`, each
**rendered from the one command template** — a single source with a drift guard, so a skill can
never diverge from or duplicate its command. Installed by **both** paths: the plugin (`skills/` +
`"skills"` in `plugin.json`) and `mokata setup claude` (writes `.claude/skills/<name>/SKILL.md`,
removed cleanly by `mokata unsetup claude` without touching your own skills). Non-Claude harnesses
degrade clean (no skills surface).

## [0.0.6] — 2026-07-01

**Windows portability fix. No breaking changes; Linux/macOS behavior unchanged.**

The Windows CI matrix ran for the first time on the 0.0.5 re-cut and exposed two real,
Windows-only bugs (the prior green runs were Linux-only). Both are fixed:

Fixed:
- **SQLite memory backend held a file handle across operations** — a persistent connection
  kept `memory.db` open, so on Windows a tempdir teardown failed with
  `PermissionError: [WinError 32] … used by another process` (dozens of tests). The
  file-backed SQLite backend now uses a short-lived connection per operation (no OS handle
  outlives a call — also a real resource-leak fix); an in-memory (`:memory:`) DB keeps its
  connection, since it has no file to leak.
- **Text files written without an explicit encoding** landed as cp1252 on Windows (em-dash
  `—` → `0x97`), then the utf-8 read raised `UnicodeDecodeError`. Every text-mode file
  open/read/write now declares `encoding="utf-8"`.

Guarded:
- A lint test fails if any text-mode `open()` / `read_text` / `write_text` omits `encoding=`.
- A portability test exercises the memory store in a temp dir and asserts no lingering file
  handle (removable while the backend is alive) — reproducible on every OS.

## [0.0.5] — 2026-07-01

**Portable sessions, in-Claude-Code UX, every-agent reach, team sharing & supply-chain trust.
No breaking changes.**

Fixed:
- **Hook invocation** — replaced the fragile `sh launch.sh → python3` hook chain with a
  PATH-resolved `mokata-hook` console entry point (the same reliable mechanism `mokata-mcp`
  uses). The `python3: command not found` pre-hook error on Windows / GUI-launched macOS / exotic
  PATHs is gone; `launch.sh` remains only as a last-resort pure-plugin fallback.

Added:
- **Portable / shareable sessions** — `mokata session push <tag>` / `pull <tag>` / `list` / `name`:
  package checkpoints + approach + in-progress brainstorm + relevant memory into a
  machine-path-free, versioned, **secret-scanned + human-gated** bundle (local file or shared
  transport); start on one machine, resume on another, or hand it to a teammate.
- **In-Claude-Code UX** — an always-on **stage badge** (statusline, on by default, merge-safe);
  pipeline flow legibility (gate verdicts, why-blocked + how-to-unblock, one-key gate responses,
  progress counters); the parallel-agent **lanes** view + `/mokata:progress` / `watch` / `govern`
  slash commands + MCP tools; **full command-surface parity** (every user command reachable in
  Claude Code, enforced by a CI parity gate); assisted **task decomposition** + parallel-plan
  confirm; a **brainstorm anti-drift anchor**; and the native **to-do widget** projection — all
  channel-specific renderers over one `RunProgress`.
- **Magical first-run + reconfigure** — an interactive `/mokata:setup` Q&A wizard (detect → wire →
  build → guardrails, human-gated) and a re-runnable `mokata reconfigure` to change what's wired.
- **Memory intelligence** — explainable retrieval (why a memory surfaced), memory-health nudges
  (stale / contradictory / unused), and auto-proposed guardrails from observed corrections
  (proposal-only, human-gated).
- **CI / PR check** — the completeness + spec-awareness gate as a reusable GitHub Action; a
  `/mokata:review` PR comment. Opt-in, degrade-clean.
- **Every agent, in your editor** — in-harness surfaces for **Cursor, GitHub Copilot, Windsurf,
  Codex, Gemini CLI, and Aider** (not just Claude Code); a **VS Code extension**; and a read-only
  **Copilot Chat `@mokata`** participant. Language coverage (Python/JS-TS/Go/Rust/Java) +
  Windows/macOS/Linux CI matrix.
- **Team & sharing** — one guided `mokata team join` (adopt → shared memory → vault → onboard →
  doctor, each human-gated); publishable governed **community stacks** (`mokata stacks`); and
  team **audit/activity logs** shared or local, conflict-free — **no telemetry**. One shared
  backend safely hosts **many projects**: every shared row scoped by a stable project key
  (review defaults to your project; `--all` / `--project` to span or pick).

Hardened:
- **Supply-chain trust** — reproducible sdist+wheel, a **CycloneDX SBOM**, and a **Sigstore
  build-provenance attestation** at tag-time; all five CI workflows least-privilege + SHA-pinned.
- **Reliability** — a seeded fuzz/edge pass across the hot paths (no false-blocks); a
  **performance budget** (`mokata lat-check`) with measured per-operation latencies.
- **Release process** — `scripts/release.sh` tags only after the public sync and **verifies
  version-consistency at the exact commit** (`mokata release-check`); Pages deploy restricted to
  `main`.

## [0.0.4] — 2026-06-28

**Governance transparency, session lifecycle, portability & hardening. No breaking changes.**

Added:
- **`mokata govern`** — a self-contained, clickable local dashboard of the governed state: rules
  & guardrails (with line-budget), memory by kind with provenance, the read/write adoption ratio,
  and pending self-healing proposals — read-only.
- **`mokata audit --why`** — a what + decision + **why** timeline; every gate / deviation /
  spec-conflict / self-healing decision now records its rationale.
- **`mokata sessions` / `mokata resume`** — list past/active runs and resume from the last passed
  gate; plus a **mid-brainstorm checkpoint** so you can leave a brainstorm at any step and come
  back (the approach HARD-GATE still holds).
- **git-worktree isolation** — opt-in (`settings.execution.worktrees`): parallel/fanout tasks and
  paused/WIP sessions run in throwaway worktrees, auto-cleaned, degrade-clean without git.
- **Cross-harness portability** — a `Harness` boundary with **claude** (reference), **codex**, and
  **cowork** adapters; `mokata harness` shows the capability matrix; missing capabilities degrade
  clearly (never pretend). A "use mokata in Cowork" how-to.
- **`mokata version` / `mokata upgrade`** — offline version info; opt-in update check (the one
  outbound call, netguard-accounted); human-gated upgrade; `/mokata:version`.

Hardened:
- **Secret guard** — broadened to 18 credential formats + a seeded fuzz invariant; pure-hex
  digests / paths / URLs / UUIDs no longer false-positive (real secrets in context still block).
- **Repo/OSS hygiene** — Dependabot, CodeQL, Scorecard, CODEOWNERS.
- **Live-DB CI** — Postgres + pgvector + Neo4j service containers exercise the shared-memory /
  semantic / graph paths for real (the core stays dependency-free).
- **Docs** — README + CLI reference audited to match the full command surface, with a docs-vs-code
  drift guard test.

## [0.0.3] — 2026-06-28

**Wires up governance/token features that previously had no runtime path, plus a second
secret-guard precision fix. No breaking changes.**

Added / now reachable:
- **`mokata memory consolidate`** — surface proposal-only memory consolidations (merge/summarize/
  prune); read-only, applying stays the existing human-gated path.
- **`mokata skill author`** — author a skill via RED-GREEN-for-docs, written through the
  human-gated WriteGate.
- **`mokata playbook --dense`** — output-density compression of sub-agent handbacks
  (content-preserving, off by default; `settings.governance.output_density`).
- **Karpathy gates** now run per pipeline phase (toggleable via `settings.governance.karpathy.<id>`,
  audited), **lethal-trifecta gating** now guards a private outbound `vault push` (human-gated +
  logged), **rules-learning** now surfaces proposal-only rule promotions from recurring
  corrections in `mokata rules`, **per-task model routing** is available (opt-in via
  `settings.execution.model_routing`), and the SessionStart briefing emits a **cache-stable
  prefix**. All off-by-default / degrade-clean / human-gated where they write.

Fixed:
- **Secret guard precision** — the entropy layer no longer flags long file paths / URLs / UUIDs
  in content as secrets (it broke writes of any file containing a path); real-secret detection is
  unchanged. (Complements the 0.0.2 envelope fix.)

## [0.0.2] — 2026-06-27

**Critical fix.** The PreToolUse **secret-guard hook** scanned the entire hook payload —
including Claude Code's high-entropy `session_id` and `transcript_path` — which tripped the
secret detector and **blocked every Write/Edit/Bash call** for installed plugin users. The guard
now parses the PreToolUse envelope and scans **only the tool's content and target path**, never
the envelope metadata. Real-secret detection is unchanged (secrets in a command, file content, or
a `.env`/`.pem` path still hard-block); `--text`/`--path` usage and raw-text scanning are
preserved. Added regression tests for the envelope path. No feature changes.

## [0.0.1] — 2026-06-27

The inaugural public release — the full feature set, built clean-room, local-first, Apache-2.0.
A spec-driven, test-first framework for Claude Code with a real codebase **knowledge graph**,
persistent **self-healing, shareable memory**, and **human-gated, audited governance** as its
spine.

### Spine, knowledge & engine
- **Spine.** Stack manifest + schema, capability router with declared fallback, tool detection +
  graceful degradation, sub-2k-token SessionStart briefing, unified config/constitution surface,
  `mokata init`; capability-negotiation + BYO-tool adapter contracts; MCP registry/discovery.
- **Knowledge graph.** Adopted codebase-graph adapter with a grep floor; typed structural queries
  (callers/callees/implementers/imports/blast-radius); incremental re-index with staleness
  surfacing; `@lat` drift anchors / `lat-check`. **External Neo4j adapter** — wire a team graph as
  the `code_graph` provider (env-var credentials), degrade-clean to grep.
- **Engine & TDD.** 7-phase pipeline (brainstorm → analysis → strawman → pre-mortem → probes →
  completeness gate → emit); provable completeness gate (every AC maps to a test, RED before
  GREEN); spec persisted + spec-persisted precondition; **anti-assumption / ground-in-code**
  discipline; per-run execution-mode selector (sequential default / parallel: fresh-subagent
  isolation + two-stage review + fan-out, degrade-safe).

### Memory — the institutional brain
- Persistent / decision / **typed** memory (rule · guardrail · best-practice · context ·
  reference), on by default; self-healing by surfacing old→new diffs; per-type toggles.
- **Tiered retrieval** — lexical floor + graph-proximity + semantic (pluggable embedder / pgvector
  vector backend), fused + ranked, frugal top-k, degrade-clean.
- **Sharing** — `memory export`/`import` (file), `memory migrate` (sqlite ↔ obsidian ↔ postgres),
  and a team-shared **Postgres** store whose schema mokata owns (`mokata_memory`).
- **Guided capture** — `/mokata:onboard` LLM-processes rules/guardrails/conventions/docs/context
  into typed, human-gated memory that the skills reference just-in-time.
- **Team design vault** — push a named brainstorm-plan/spec → teammates search → pull → review
  (versioned, gated, secret-scanned).

### Governance, safety & UX
- **Spec-awareness / regression guard** — a change is checked against saved specs + decisions and
  raised (deviation gate, human-gated, logged) before it can break them.
- **Plan-adherence deviation gate**; **universal human-gated writes** (every code/memory/config
  write through one `WriteGate`: secret-scan hard-block → approval → commit → audit ledger);
  reversible + resumable; local-first, **zero telemetry**; per-adapter trust dials.
- **Run observability** — parallel-aware terminal lanes (`mokata progress --lanes`) and an opt-in
  self-contained clickable HTML dashboard (`mokata watch`); read-only, frugal, local-first.
- **Composability** — profiles (minimal/standard/full/custom), per-layer/tool toggles, standalone
  skills, mid-pipeline entry; verified `mokata ship` (green + ACs met + review passed, then
  human-chosen landing — never auto-merge).

### Notes
- **Early & stabilizing:** 0.0.1 is an early release of a fast-moving project; expect rapid
  iteration. Pin the version if you need stability.
- No required runtime dependencies — `jsonschema`, `mcp`, `postgres` (psycopg), and `neo4j` are
  optional extras, each lazily imported and degraded over. The suite passes with `jsonschema`
  both absent and present.
- Clean-room throughout: no dependency on, or text copied from, any other framework
  (Apache-2.0, under MoStack).

[0.0.1]: https://github.com/JasGujral/mokata-oss/releases/tag/v0.0.1
