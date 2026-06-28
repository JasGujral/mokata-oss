# Changelog

All notable changes to mokata are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> **Re-baselined at 0.0.1.** mokata is published fresh at **0.0.1** as its inaugural public
> release. Earlier internal iterations (the pre-1.x and 1.x series) were a stabilizing phase and
> are intentionally collapsed into this entry — 0.0.1 is the honest starting point for an
> early-stage, fast-moving project. The detailed build history lives in the repository's internal
> build log.

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
