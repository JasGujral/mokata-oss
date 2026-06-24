# Changelog

All notable changes to mokata are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.3] — 2026-06-24

### Fixed
- **Plugin manifest:** removed the explicit `hooks` reference from `.claude-plugin/plugin.json`. Claude Code auto-loads the standard `hooks/hooks.json`, so referencing it in the manifest triggered a "Duplicate hooks file detected" error on plugin load. The manifest now only lists additional hook files (there are none); the hooks themselves still load.

## [1.2.2] — 2026-06-24

### Changed
- **Docs:** standardized the install/usage story into three clear tiers everywhere it appears — (1) the **Claude Code plugin** (standard, public marketplace); (2) **Claude Code without the public marketplace** (no registration) — the plugin from a local clone, or `mokata setup claude`; (3) the **CLI, with any AI tool** (Gemini, Codex, scripts, CI). The local-clone plugin install is now documented as a first-class no-registration option alongside `mokata setup claude`.

## [1.2.1] — 2026-06-24

### Changed
- **Docs:** the install/usage story is presented as a consistent three-tier priority across the whole doc set — (1) Claude Code plugin, (2) Claude Code without the plugin via `mokata setup claude`, (3) the CLI for scripting/inspection (last). Quickstart, README, the plugin and no-plugin how-tos, the "integrate other tools" guide, and the run-a-story tutorial all reflect this ordering, making clear the CLI is the engine's mechanics (no LLM) rather than the primary way to build.

## [1.2.0] — 2026-06-24

### Added
- **`mokata setup <harness>`** — one human-gated command to use mokata in Claude Code **without the plugin**: runs `init` (if needed), copies the slash commands into `.claude/commands/`, registers the `mokata-mcp` server in `.mcp.json`, and wires the SessionStart + secret-guard hooks into `.claude/settings.json`. Flags: `--scope {project,user}`, `--profile`, `--no-hooks`, `--yes`, `--force`. JSON files are merged (never clobbered) and the command is idempotent. Runs entirely on the user's machine via their existing Claude Code sign-in — no API key.
- **`mokata unsetup <harness>`** — cleanly reverses `setup` (removes the copied commands, the `mokata` MCP entry, and the mokata hook entries; preserves everything else and the `.mokata/` config).
- **Docs:** new "Use mokata without the plugin" how-to (one-command setup, the manual wiring it automates, and harness-agnostic notes for Gemini/Codex).

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
