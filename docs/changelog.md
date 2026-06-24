# Changelog

The full, versioned changelog lives in the repository's
[`CHANGELOG.md`](https://github.com/JasGujral/mokata-oss/blob/main/CHANGELOG.md)
(Keep a Changelog format).

## 1.2.2

- **Docs** standardized into three clear tiers everywhere: (1) the **Claude Code plugin**
  (standard, public marketplace); (2) **Claude Code without the public marketplace** (no
  registration) — the plugin from a local clone, or `mokata setup claude`; (3) the **CLI,
  with any AI tool** (Gemini, Codex, scripts, CI). The local-clone plugin install is now a
  first-class no-registration option.

## 1.2.1

- **Docs** present a consistent three-tier priority everywhere: (1) Claude Code plugin,
  (2) Claude Code without the plugin via `mokata setup claude`, (3) the CLI for
  scripting/inspection (last). The CLI is clearly the engine's mechanics (no LLM), not the
  primary way to build.

## 1.2.0

- **`mokata setup claude`** — one human-gated command to use mokata in Claude Code
  **without the plugin**: it runs `init`, copies the slash commands, registers the
  `mokata-mcp` server, and wires the hooks (`--scope`, `--profile`, `--no-hooks` options).
  `mokata unsetup claude` reverses it. Runs locally via your existing Claude Code sign-in —
  no API key.
- New "Use mokata without the plugin" how-to, covering the one-command setup, the manual
  wiring it automates, and harness-agnostic notes for other tools (Gemini, Codex).

## 1.1.0

- **Bundled MCP server** — Claude Code (and any MCP client) can call mokata operations as
  native tools: read tools (query, recall, doctor, coverage, budget, audit, status,
  preview) and human-gated write tools (remember, import_stack, reset, apply_proposal —
  propose-only unless explicitly confirmed; secrets are an un-overridable hard block).
  Installed via the optional `mokata[mcp]` extra; the core package and CLI run without it.
- **Integration test suite** — end-to-end pipeline across every profile and both execution
  modes, wired as a required release gate; plus a release CD pipeline.
- Default profile is `standard` (lean, local), with documented `mokata init --profile full`.
- Docs are plugin-first, with a three-panel Material docs site.

## 1.0.0

First public release — the full feature set (Parts A–L): the spec-driven engine, knowledge
graph, self-healing memory, token governance, execution modes, governance & audit, config
& composability, and distribution. Clean-room, local-first, Apache-2.0, no required runtime
dependencies; the test suite passes with `jsonschema` both absent and present.
