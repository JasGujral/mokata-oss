# Integrating mokata with other AI tools

mokata is **Claude-Code-first** — start with the [plugin](use-the-plugin.md), or wire the
same experience in without the marketplace via [`mokata setup claude`](use-without-plugin.md).
But the engine is harness-agnostic: the plugin is just a bundle of three portable artifacts
(prompt templates, the `mokata-mcp` server, and hook scripts), so you can wire those into
another harness or share a governed stack across a team. The paths below go from
highest-fidelity (a real harness integration) down to the lowest common denominator (raw
CLI), so reach for them in that order.

## 1. Wire mokata into your harness (the `setup` model)

The best integration mirrors what `mokata setup claude` does: point your harness at the
three artifacts — the prompt templates (`templates/commands/*.md`), the `mokata-mcp` MCP
server, and the hook scripts — using that harness's own command/MCP/hook conventions. For
Claude Code this is one command ([`mokata setup claude`](use-without-plugin.md)); for other
harnesses (Gemini CLI, Codex) the same three pieces map to their equivalents (worked
examples are on the roadmap). This gives the LLM the full structured workflow, not just
shell access.

## 2. The CLI works anywhere (lowest common denominator)

The [`mokata` CLI](../reference/cli.md) is a dependency-light Python tool (no required
runtime deps; `jsonschema` optional). It runs in any terminal, script, or CI, independent
of Claude Code. Any AI tool that can run shell commands can therefore call mokata — e.g.
have your assistant run `mokata query callers foo`, `mokata preview`, or `mokata doctor`
and read the output. Use this when a full harness integration isn't available — the CLI is
the engine's mechanics (it has no LLM of its own).

## 3. Orchestrate external MCP servers (H4)

mokata has an **MCP registry + discovery** layer: drop a `.mokata/mcp.json` listing the MCP
servers you use (`{name, provides, command}`), and `mokata mcp` enumerates them and maps
them to stack roles/capabilities. mokata then orchestrates those servers through its own
gates and audit trail. Discovery is pluggable and **degrades cleanly** — with no config
present, the registry is empty and nothing errors.

> In v1.0 mokata *consumes* MCP servers (it discovers and routes to them); it does not
> itself expose an MCP server.

## 4. Cross-harness portability (the harness boundary)

mokata's engine runs through a thin **harness boundary** (`mokata harness`) so the pipeline
isn't tied to one host. The reference implementation targets Claude Code; the boundary
defines how commands, hooks, context injection, and subagents map to a harness. On a
harness that lacks a capability (e.g. a host without subagents), mokata **degrades with a
clear message** instead of failing — so the same workflow runs, just with fewer features.
This is what lets mokata extend to tools like Codex or OpenCode without rebuilding the
engine per host.

## 5. Shareable stack manifests (team adoption)

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
