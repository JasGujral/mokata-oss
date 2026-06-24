# Changelog

All notable changes to mokata are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] — 2026-06-24

### Added
- **Bundled MCP server** — Claude Code (and any MCP client) can call mokata operations as native tools: read tools (`query`, `recall`, `doctor`, `coverage`, `budget`, `audit`, `status`, `preview`) and human-gated write tools (`remember`, `import_stack`, `reset`, `apply_proposal` — propose-only unless explicitly confirmed; secrets are an un-overridable hard block). Installed via the optional `mokata[mcp]` extra; the core package and CLI run without it.
- **Integration test suite** (`tests/integration/`) — end-to-end pipeline across every profile and both execution modes, plus config/memory/knowledge round-trips; wired as a required release gate.
- **Release CD pipeline** (`release.yml`) — on a version tag, gates on the full test matrix + version/manifest validation, then cuts the GitHub Release.

### Changed
- Default profile is `standard` (lean, local); documented `mokata init --profile full` to wire every graph/memory provider.
- Docs are now plugin-first (Claude Code plugin is the primary install path; CLI is the additional option), with a three-panel Material docs site.

## [1.0.0] — 2026-06-23

First public release — the full feature set, built clean-room, local-first, Apache-2.0.

### Added

- **Spine (Part A).** Stack manifest + schema, capability router with declared fallback,
  tool-presence detection + graceful degradation, sub-2k-token SessionStart bootstrap,
  unified config/constitution surface, `mokata init`; capability negotiation contract,
  MCP registry/discovery, BYO-tool adapter contract, deterministic precedence (A6/H4–H6).
- **Knowledge layer (Part B).** Adopted codebase-graph adapter with a grep floor, typed
  structural query API (callers/callees/implementers/imports/blast-radius),
  retrieval-instead-of-grep, per-story persistence bridge, incremental re-index with
  staleness surfacing, and `@lat` drift anchors / `lat-check`.
- **Memory (Part C).** Persistent + decision + episodic memory, on by default; pluggable
  backends (SQLite default / Obsidian / native); self-healing by surfacing old→new diffs;
  human-gated writes; per-type toggles; adoption instrumentation; proposal-only
  consolidation.
- **Engine (Part D).** 7-phase pipeline (brainstorm → analysis → strawman → pre-mortem →
  probes → completeness gate → emit), provable completeness gate, AC-mapper, pre-mortem +
  probes, spec-compliance review, plan/dry-run preview.
- **TDD & execution (Part E).** RED-before-GREEN enforcement, per-task model routing with
  escalation, bug-fix flow, debug (root-cause, N-strikes) and optimize (measure-first)
  engines, per-run execution-mode selector (sequential default / parallel: fresh-subagent
  isolation + two-stage review + fan-out, degrade-safe).
- **Token governance (Part F).** In-loop token/cost tracker, JIT graph-backed retrieval,
  sub-agent handback caps, output-density compression, savings budget + statusline,
  prompt-cache-stable prefixes.
- **Rules & governance (Part G).** 4-tier rules + constitution, rules-vs-gates-vs-hooks
  taxonomy, sync (exit-2 security) / async hooks, Karpathy gates (hybrid, toggleable,
  audited), rule-learning (proposes only), test-first skill authoring.
- **Safety & audit (Part I).** 4-layer secret protection, universal human-gated writes,
  append-only audit ledger, lethal-trifecta gating, reversible writes, resume-from-last-gate.
- **Config & composability (Parts K, L).** Per-layer/tool toggles, profiles
  (minimal/standard/full/custom), local-first/no-telemetry, config as committed artifact,
  per-adapter trust dial, `doctor`, reversible reset/uninstall; standalone commands,
  mid-pipeline entry, direct skill invocation, skill catalog, manual chaining,
  context-aware suggestions.
- **Distribution (Part J).** Marketplace + plugin packaging, thin cross-harness boundary
  (degrades cleanly), and export/import of shareable, validated stack manifests.

### Notes

- No required runtime dependencies; `jsonschema` is optional and degraded over. The test
  suite passes with `jsonschema` both absent and present.
- Clean-room throughout: no dependency on or text copied from any other framework.

[1.0.0]: https://github.com/JasGujral/mokata-oss/releases/tag/v1.0.0
