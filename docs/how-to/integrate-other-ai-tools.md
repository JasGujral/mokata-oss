# Integrating mokata with other AI tools

mokata is **plugin-first for Claude Code**, but the engine is harness-agnostic — you can
use it with other AI coding tools and share a governed stack across a team. There are four
integration paths.

## 1. The CLI works anywhere

The [`mokata` CLI](../reference/cli.md) is a dependency-light Python tool (no required
runtime deps; `jsonschema` optional). It runs in any terminal, script, or CI, independent
of Claude Code. Any AI tool that can run shell commands can therefore call mokata — e.g.
have your assistant run `mokata query callers foo`, `mokata preview`, or `mokata doctor`
and read the output. This is the lowest-common-denominator integration.

## 2. Orchestrate external MCP servers (H4)

mokata has an **MCP registry + discovery** layer: drop a `.mokata/mcp.json` listing the MCP
servers you use (`{name, provides, command}`), and `mokata mcp` enumerates them and maps
them to stack roles/capabilities. mokata then orchestrates those servers through its own
gates and audit trail. Discovery is pluggable and **degrades cleanly** — with no config
present, the registry is empty and nothing errors.

> In v1.0 mokata *consumes* MCP servers (it discovers and routes to them); it does not
> itself expose an MCP server.

## 3. Cross-harness portability (the harness boundary)

mokata's engine runs through a thin **harness boundary** (`mokata harness`) so the pipeline
isn't tied to one host. The reference implementation targets Claude Code; the boundary
defines how commands, hooks, context injection, and subagents map to a harness. On a
harness that lacks a capability (e.g. a host without subagents), mokata **degrades with a
clear message** instead of failing — so the same workflow runs, just with fewer features.
This is what lets mokata extend to tools like Codex or OpenCode without rebuilding the
engine per host.

## 4. Shareable stack manifests (team adoption)

A mokata stack — which tools are wired, the profile, toggles, trust dials — is a committed,
reviewable manifest. Share it so a teammate adopts the same governed setup in one step:

```bash
mokata export team-stack.json        # publish your stack
mokata import team-stack.json --yes  # validate + apply on another repo (human-gated)
```

Imports are validated before they apply (an invalid manifest is rejected, nothing written),
and applying is human-gated — so sharing a stack never silently reconfigures someone's repo.
See [Share a stack](share-a-stack.md).

## What stays true everywhere

No matter the integration path, mokata's guarantees hold: **local-first** (nothing leaves
the machine unless you wire an external tool), **every durable write human-gated**, and a
**full audit trail** of every gate decision and tool call. Adopted external tools are
treated as untrusted input — gated and permission-scoped via the per-adapter
[trust dial](configure-a-profile.md).
