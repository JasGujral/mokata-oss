# Changelog

The full, versioned changelog lives in the repository's
[`CHANGELOG.md`](https://github.com/JasGujral/mokata-oss/blob/main/CHANGELOG.md)
(Keep a Changelog format).

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
